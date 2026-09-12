"""Клиент Telegram Bot API через httpx (long polling, sendMessage, editMessageText, answerCallbackQuery)."""

import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("bot.tg")


def split_text(
    text: str,
    max_len: int = 4096,
    suffix: str = " (продолжение)",
) -> list[str]:
    """Разбиение текста длинее 4096 символов по границам абзацев с добавлением суффикса."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    lines = text.splitlines(keepends=True)
    current_chunk = ""

    for line in lines:
        is_first_chunk = len(chunks) == 0
        current_limit = max_len if is_first_chunk else max_len - len(suffix)

        if len(current_chunk) + len(line) <= current_limit:
            current_chunk += line
        else:
            if current_chunk:
                chunks.append(current_chunk if is_first_chunk else current_chunk + suffix)
                current_chunk = ""

            # Если одна строка превышает лимит
            rem_limit = max_len - len(suffix)
            while len(line) > rem_limit:
                chunk_part = line[:rem_limit]
                chunks.append(chunk_part + suffix if chunks else chunk_part)
                line = line[rem_limit:]

            current_chunk = line

    if current_chunk:
        is_first_chunk = len(chunks) == 0
        chunks.append(current_chunk if is_first_chunk else current_chunk + suffix)

    return chunks


class TelegramClient:
    """HTTP-клиент для вызовов Telegram Bot API."""

    def __init__(self, token: str, base_url: str = "https://api.telegram.org"):
        self.token = token
        self.api_url = f"{base_url.rstrip('/')}/bot{token}"
        self.http = httpx.Client(timeout=40.0)

    def close(self) -> None:
        self.http.close()

    def _request(self, method: str, endpoint: str, **kwargs) -> dict[str, Any]:
        url = f"{self.api_url}/{endpoint}"
        max_retries = 3

        for attempt in range(max_retries):
            try:
                resp = self.http.request(method, url, **kwargs)
                if resp.status_code == 429:
                    retry_after = 5
                    try:
                        data = resp.json()
                        retry_after = data.get("parameters", {}).get("retry_after", 5)
                    except Exception:
                        pass
                    logger.warning("Telegram 429: Rate limit hit. Sleeping %d s...", retry_after)
                    time.sleep(retry_after)
                    continue

                if resp.status_code >= 500:
                    logger.warning(
                        "Telegram server error %s on %s (attempt %d/%d).",
                        resp.status_code,
                        endpoint,
                        attempt + 1,
                        max_retries,
                    )
                    if attempt < max_retries - 1:
                        time.sleep(1.0 * (attempt + 1))
                        continue

                resp.raise_for_status()
                payload = resp.json()
                if not payload.get("ok"):
                    logger.error("Telegram API error: %s", payload)
                    raise RuntimeError(f"Telegram API error: {payload.get('description')}")
                return payload

            except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                if attempt == max_retries - 1:
                    logger.error("Network or HTTP error talking to Telegram: %s", exc)
                    raise
                time.sleep(1.0 * (attempt + 1))


        raise RuntimeError("Failed to execute request to Telegram Bot API after retries")

    def get_me(self) -> dict[str, Any]:
        """Получить информацию о боте."""
        res = self._request("GET", "getMe")
        return res.get("result", {})

    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 30,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Long polling метод getUpdates."""
        params: dict[str, Any] = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        if allowed_updates is None:
            allowed_updates = ["message", "callback_query"]
        params["allowed_updates"] = allowed_updates

        res = self._request("POST", "getUpdates", json=params)
        return res.get("result", [])

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Отправка сообщения с автоматическим разбиением > 4096 символов."""
        chunks = split_text(text)
        results = []

        for i, chunk in enumerate(chunks):
            # Разметку (клавиатуру) прикрепляем только к последнему сообщению
            markup = reply_markup if i == len(chunks) - 1 else None
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
            }
            if markup:
                payload["reply_markup"] = markup

            res = self._request("POST", "sendMessage", json=payload)
            results.append(res.get("result", {}))

        return results

    def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Редактирование текста сообщения."""
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        res = self._request("POST", "editMessageText", json=payload)
        return res.get("result", {})

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> bool:
        """Подтверждение обработки inline callback query."""
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = show_alert

        res = self._request("POST", "answerCallbackQuery", json=payload)
        return bool(res.get("result", False))
