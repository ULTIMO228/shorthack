"""Клиент REST ядра поддержки МИСИС (сессии per chat_id, auth, requests, certs, internal API)."""

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("bot.core_api")


class CoreApiError(Exception):
    """Базовое исключение вызова REST API ядра."""

    def __init__(self, status_code: int, detail: str = ""):
        super().__init__(f"Core API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class CoreApiConflictError(CoreApiError):
    """Исключение 409 (диалог по обращению завершён)."""


class CoreApiClient:
    """REST-клиент ядра поддержки МИСИС."""

    def __init__(
        self,
        base_url: str | None = None,
        internal_token: str | None = None,
    ):
        self.base_url = (base_url or os.getenv("CORE_API_URL", "http://localhost:8000")).rstrip("/")
        self.internal_token = internal_token or os.getenv("BOT_INTERNAL_TOKEN", "")
        # Сессии httpx с куками per chat_id (LRU до 500 активных клиентов)
        self._sessions: dict[int, httpx.Client] = {}
        self._max_sessions = 500
        # Системный клиент для internal вызовов
        self._sys_http = httpx.Client(
            base_url=self.base_url,
            headers={"X-Bot-Token": self.internal_token},
            timeout=30.0,
        )

    def close(self) -> None:
        self._sys_http.close()
        for client in self._sessions.values():
            client.close()
        self._sessions.clear()

    def _get_user_client(self, chat_id: int) -> httpx.Client:
        if chat_id in self._sessions:
            client = self._sessions.pop(chat_id)
            self._sessions[chat_id] = client
            return client

        if len(self._sessions) >= self._max_sessions:
            oldest_id, oldest_client = next(iter(self._sessions.items()))
            try:
                oldest_client.close()
            except Exception:
                pass
            del self._sessions[oldest_id]

        client = httpx.Client(
            base_url=self.base_url,
            timeout=30.0,
        )
        self._sessions[chat_id] = client
        return client

    def ensure_user_session(self, chat_id: int, email: str | None = None) -> bool:
        """Обеспечивает авторизованную сессию пользователя (login по данным tg_link)."""
        target_email = email
        if not target_email:
            try:
                link_info = self.get_tg_link(chat_id)
                target_email = (
                    link_info.get("email")
                    or (link_info.get("user") or {}).get("email")
                )
            except Exception as exc:
                logger.warning("Не удалось получить tg_link для auto-login chat_id=%s: %s", chat_id, exc)
                return False

        if not target_email:
            logger.debug("Email для chat_id=%s не найден в tg_link (пользователь не привязан)", chat_id)
            return False

        try:
            self.login(chat_id, target_email)
            return True
        except Exception as exc:
            logger.warning("Auto-login failed for chat_id=%s (%s): %s", chat_id, target_email, exc)
            return False

    def _user_call(self, chat_id: int, call_fn: Any) -> Any:
        """Выполняет вызов пользовательского эндпоинта с автоматическим релогином при 401."""
        client = self._get_user_client(chat_id)
        try:
            resp = call_fn(client)
            if getattr(resp, "status_code", None) == 401:
                logger.info("Сессия ядра для chat_id=%s не авторизована (401). Запуск auto-login...", chat_id)
                if self.ensure_user_session(chat_id):
                    resp = call_fn(client)
            return self._handle_response(resp)
        except (CoreApiError, CoreApiConflictError):
            raise
        except httpx.HTTPError as exc:
            raise CoreApiError(503, str(exc))


    def _handle_response(self, resp: httpx.Response) -> Any:
        if resp.status_code == 409:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text
            raise CoreApiConflictError(409, detail)

        if resp.status_code >= 400:
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                detail = resp.text
            raise CoreApiError(resp.status_code, detail)

        if resp.status_code == 204:
            return None
        return resp.json()

    # --- Внутренние эндпоинты ядра (/api/internal/*) ---

    def get_tg_link(self, chat_id: int) -> dict[str, Any]:
        """Получить состояние привязки по chat_id."""
        try:
            resp = self._sys_http.get("/api/internal/tg/link", params={"chat_id": chat_id})
            if resp.status_code == 404:
                return {"ok": False, "state": "awaiting_email", "user_id": None}
            res = self._handle_response(resp)
            if isinstance(res, dict) and "ok" not in res:
                res["ok"] = True
            return res
        except httpx.HTTPError as exc:
            logger.warning("Error fetching tg link for %s: %s", chat_id, exc)
            raise CoreApiError(503, str(exc))

    def request_tg_link(self, chat_id: int, email: str) -> dict[str, Any]:
        """Запрос кода подтверждения привязки к корпоративной почте."""
        try:
            resp = self._sys_http.post(
                "/api/internal/tg/link",
                json={"chat_id": chat_id, "email": email},
            )
            return self._handle_response(resp)
        except httpx.HTTPError as exc:
            logger.warning("Error requesting tg link for %s (%s): %s", chat_id, email, exc)
            raise CoreApiError(503, str(exc))

    def confirm_tg_link(self, chat_id: int, code: str) -> dict[str, Any]:
        """Подтверждение привязки кодом."""
        try:
            resp = self._sys_http.post(
                "/api/internal/tg/link/confirm",
                json={"chat_id": chat_id, "code": code},
            )
            return self._handle_response(resp)
        except httpx.HTTPError as exc:
            logger.warning("Error confirming tg link for %s: %s", chat_id, exc)
            raise CoreApiError(503, str(exc))

    def get_pending_outbound(self) -> list[dict[str, Any]]:
        """Получить сообщения из очереди исходящих для дежурного или пользователей."""
        try:
            resp = self._sys_http.get(
                "/api/internal/outbound",
                params={"status": "pending"},
            )
            res = self._handle_response(resp)
            return res if isinstance(res, list) else []
        except httpx.HTTPError as exc:
            logger.warning("Error getting pending outbound: %s", exc)
            return []

    def ack_outbound(self, message_id: int, status: str = "sent") -> bool:
        """Подтверждение отправки исходящего сообщения (status: 'sent' | 'failed')."""
        try:
            resp = self._sys_http.post(
                f"/api/internal/outbound/{message_id}/ack",
                json={"status": status},
            )
            self._handle_response(resp)
            return True
        except httpx.HTTPError as exc:
            logger.warning("Error acking outbound message %s: %s", message_id, exc)
            return False

    # --- Пользовательские эндпоинты ядра ---

    def login(self, chat_id: int, email: str) -> dict[str, Any]:
        """Авторизовать сессию пользователя по email."""
        client = self._get_user_client(chat_id)
        try:
            resp = client.post("/api/auth/login", json={"email": email})
            return self._handle_response(resp)
        except httpx.HTTPError as exc:
            logger.warning("Login failed for %s (%s): %s", chat_id, email, exc)
            raise CoreApiError(503, str(exc))

    def create_request(self, chat_id: int, text: str) -> dict[str, Any]:
        """Подать новое обращение (channel='telegram')."""
        return self._user_call(
            chat_id,
            lambda c: c.post("/api/requests", json={"text": text, "channel": "telegram"}),
        )

    def reply_to_request(self, chat_id: int, request_id: int, text: str) -> dict[str, Any]:
        """Отправить ответ на уточняющий вопрос в диалоге."""
        return self._user_call(
            chat_id,
            lambda c: c.post(f"/api/requests/{request_id}/reply", json={"text": text}),
        )

    def get_my_requests(self, chat_id: int) -> list[dict[str, Any]]:
        """Получить список моих обращений."""
        res = self._user_call(chat_id, lambda c: c.get("/api/requests"))
        return res if isinstance(res, list) else []

    def get_certs_catalog(self, chat_id: int) -> list[dict[str, Any]]:
        """Получить каталог доступных типов справок."""
        res = self._user_call(chat_id, lambda c: c.get("/api/certs/catalog"))
        return res if isinstance(res, list) else []

    def get_my_cert_orders(self, chat_id: int) -> list[dict[str, Any]]:
        """Получить список моих заказов справок."""
        res = self._user_call(chat_id, lambda c: c.get("/api/certs/orders"))
        return res if isinstance(res, list) else []

    def create_cert_order(self, chat_id: int, cert_type: str) -> dict[str, Any]:
        """Заказать справку выбранного типа."""
        return self._user_call(
            chat_id,
            lambda c: c.post("/api/certs/orders", json={"cert_type": cert_type}),
        )


