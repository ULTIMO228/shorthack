"""Маршрутизация и диспетчеризация сообщений Telegram-бота (фича 003, T006).

Реализует:
- Фильтрацию типов чатов (только private, группы игнорируются).
- Проверку типа контента (только текст, медиа/файлы -> MSG_TEXT_ONLY).
- Конечный автомат состояний (state machine по данным GET /api/internal/tg/link).
- Базовые команды: /start, /help, /cancel, /status, /certs.
- Обработку callback_query с обязательным answerCallbackQuery.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
    MSK_TZ = ZoneInfo("Europe/Moscow")
except Exception:
    MSK_TZ = timezone(timedelta(hours=3))

from app.bot.core_api import CoreApiClient, CoreApiConflictError, CoreApiError
from app.bot.keyboards import (
    make_cert_catalog_keyboard,
    make_cert_order_confirm_keyboard,
)
from app.bot.messages import (
    MSG_CANCELLED,
    MSG_CERTS_CATALOG_TITLE,
    MSG_CERTS_ORDERS_TITLE,
    MSG_DIALOG_COMPLETED,
    MSG_DUPLICATE_PREFIX,
    MSG_INCIDENT_ROW,
    MSG_NEED_LINK,
    MSG_NO_CERT_ORDERS,
    MSG_TEXT_ONLY,
    T1_GREETING,
    T2_HELP,
    T3_ASK_EMAIL,
    T4_EMAIL_SENT,
    T5_EMAIL_INVALID,
    T6_LINK_SUCCESS,
    T7_CODE_INVALID,
    T8_ATTEMPTS_EXCEEDED,
    T9_PROCESSING,
    T10_OUTAGE_NOTICE,
    T11_ESCALATED,
    T12_NO_REQUESTS,
    T13_SERVICE_UNAVAILABLE,
    T14_CERT_ORDERED,
)
from app.bot.tg import TelegramClient

logger = logging.getLogger("bot.handlers")

DEFAULT_STATES_FILE = Path(__file__).resolve().parent.parent.parent / ".bot_states"


def load_states(path: Path | None = None) -> dict[int, str]:
    """Чтение сохранённых локальных состояний чатов."""
    p = path or DEFAULT_STATES_FILE
    states: dict[int, str] = {}
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if str(k).lstrip("-").isdigit() and isinstance(v, str):
                        states[int(k)] = v
    except Exception as exc:
        logger.warning("Не удалось прочитать состояния из %s: %s", p, exc)
    return states


def save_states(states: dict[int, str], path: Path | None = None) -> None:
    """Сохранение активных состояний чатов (dialog:*) в файл."""
    p = path or DEFAULT_STATES_FILE
    try:
        data = {str(k): v for k, v in states.items() if v.startswith("dialog:")}
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Не удалось сохранить состояния в %s: %s", p, exc)


# Локальное отслеживание состояний чатов (оверлей над ядром для dialog:<id> и /cancel)
_chat_states: dict[int, str] = load_states()


def reset_chat_states(path: Path | None = None) -> None:
    """Сброс локальных состояний (для тестов)."""
    _chat_states.clear()
    p = path or DEFAULT_STATES_FILE
    try:
        if p.is_file():
            p.unlink(missing_ok=True)
    except Exception:
        pass


def set_chat_state(chat_id: int, state: str, path: Path | None = None) -> None:
    """Установить текущее состояние пользователя и сохранить активные диалоги."""
    _chat_states[chat_id] = state
    save_states(_chat_states, path)



def get_effective_state(chat_id: int, link_info: dict[str, Any]) -> str:
    """Определить текущее состояние пользователя с учётом локальных переходов."""
    if not is_user_linked(link_info):
        return link_info.get("state", "awaiting_email")
    if chat_id in _chat_states:
        return _chat_states[chat_id]
    return link_info.get("state", "idle")


def is_user_linked(link_info: dict[str, Any]) -> bool:
    """Проверка, привязан ли аккаунт пользователя."""
    if link_info.get("ok") is False:
        return False
    state = link_info.get("state", "awaiting_email")
    if state in ("awaiting_email", "awaiting_confirm"):
        return False
    return bool(link_info.get("user_id") or state not in ("awaiting_email", "awaiting_confirm"))


def handle_command(
    command: str,
    chat_id: int,
    link_info: dict[str, Any],
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Обработка команд бота (/start, /help, /cancel, /status, /certs)."""
    linked = is_user_linked(link_info)

    if command == "/start":
        tg.send_message(chat_id, T1_GREETING)
        if not linked:
            tg.send_message(chat_id, T3_ASK_EMAIL)

    elif command == "/help":
        tg.send_message(chat_id, T2_HELP)

    elif command == "/cancel":
        if linked:
            set_chat_state(chat_id, "idle")
        tg.send_message(chat_id, MSG_CANCELLED)

    elif command == "/status":
        if not linked:
            tg.send_message(chat_id, MSG_NEED_LINK)
        else:
            handle_status_command(chat_id, tg, core)

    elif command == "/certs":
        if not linked:
            tg.send_message(chat_id, MSG_NEED_LINK)
        else:
            handle_certs_command(chat_id, tg, core)

    else:
        logger.debug("Неизвестная команда: %s от chat_id=%s", command, chat_id)


def is_valid_misis_email(email_candidate: str) -> bool:
    """Проверка корпоративного домена МИСИС (@misis.ru или @edu.misis.ru)."""
    candidate = email_candidate.strip().lower()
    if not re.match(r"^[a-z0-9_.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+$", candidate):
        return False
    return candidate.endswith("@misis.ru") or candidate.endswith("@edu.misis.ru")


def handle_awaiting_email(
    text: str,
    chat_id: int,
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Сценарий S1: обработка ввода email в состоянии awaiting_email."""
    cleaned = text.strip().strip("'\"")
    if not is_valid_misis_email(cleaned):
        tg.send_message(chat_id, T5_EMAIL_INVALID)
        return

    email = cleaned.lower()
    try:
        core.request_tg_link(chat_id, email)
        tg.send_message(chat_id, T4_EMAIL_SENT.format(email=email))
    except CoreApiError as exc:
        if exc.status_code == 400:
            tg.send_message(chat_id, T5_EMAIL_INVALID)
        else:
            logger.warning("Ошибка связи с ядром при request_tg_link: %s", exc)
            tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)


def handle_awaiting_confirm(
    text: str,
    chat_id: int,
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Сценарий S1: обработка ввода кода подтверждения в состоянии awaiting_confirm."""
    code = text.strip()
    try:
        res = core.confirm_tg_link(chat_id, code)
        if res.get("ok"):
            set_chat_state(chat_id, "idle")
            user = res.get("user") or {}
            full_name = user.get("full_name") or user.get("name") or "Пользователь"
            user_email = user.get("email", "")
            if user_email:
                try:
                    core.login(chat_id, user_email)
                except Exception as login_exc:
                    logger.warning(
                        "Не удалось авторизовать сессию ядра для chat_id=%s (%s): %s",
                        chat_id,
                        user_email,
                        login_exc,
                    )
            tg.send_message(chat_id, T6_LINK_SUCCESS.format(full_name=full_name))
        else:
            reason = res.get("reason", "")
            remaining = res.get("remaining_attempts", 0)
            if reason == "attempts_exceeded" or remaining <= 0:
                set_chat_state(chat_id, "awaiting_email")
                tg.send_message(chat_id, T8_ATTEMPTS_EXCEEDED)
            else:
                tg.send_message(chat_id, T7_CODE_INVALID.format(n=remaining))
    except CoreApiError as exc:
        if exc.status_code == 400:
            if "attempts_exceeded" in exc.detail:
                set_chat_state(chat_id, "awaiting_email")
                tg.send_message(chat_id, T8_ATTEMPTS_EXCEEDED)
            else:
                tg.send_message(chat_id, T7_CODE_INVALID.format(n=1))
        else:
            logger.warning("Ошибка связи с ядром при confirm_tg_link: %s", exc)
            tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)


def format_reaction_message(
    rx: dict[str, Any],
    ticket: dict[str, Any],
    request_id: Any,
) -> str:
    """Сформировать текст сообщения для одной реакции ядра."""
    kind = rx.get("kind", "")
    text = rx.get("text", "")

    if kind == "outage_notice":
        cleaned = text.strip()
        if cleaned.startswith("🔴"):
            return cleaned
        return T10_OUTAGE_NOTICE.format(text=cleaned)

    elif kind == "escalated":
        num = rx.get("number") or ticket.get("number") or str(request_id or "")
        return T11_ESCALATED.format(number=num)

    elif kind == "cert_ordered":
        cid = rx.get("id") or rx.get("order_id") or ""
        title = rx.get("title") or ""
        status = rx.get("status") or ""
        if cid or title or status:
            return T14_CERT_ORDERED.format(id=cid, title=title, status=status)
        return text

    # answer, clarification, или любой другой текст
    return text


def handle_idle_text(
    text: str,
    chat_id: int,
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Сценарий S2 (US1): приём обращения в состоянии idle, мгновенный T9 и реакции ядра."""
    # Мгновенный фидбек пользователю
    tg.send_message(chat_id, T9_PROCESSING)

    try:
        resp = core.create_request(chat_id, text)
    except (CoreApiError, Exception) as exc:
        logger.warning("Ошибка создания обращения для chat_id=%s: %s", chat_id, exc)
        tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)
        return

    if not isinstance(resp, dict):
        logger.warning("Некорректный ответ ядра на create_request: %s", resp)
        tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)
        return

    request_id = resp.get("request_id")
    is_duplicate = bool(resp.get("duplicate"))
    ticket = resp.get("ticket") or {}
    ticket_number = ticket.get("number") or str(request_id or "")

    reactions = resp.get("reactions") or []
    if not reactions:
        fallback = f"Обращение зарегистрировано: {ticket_number}" if ticket_number else "Обращение зарегистрировано."
        tg.send_message(chat_id, fallback)
        return

    n = len(reactions)
    for i, rx in enumerate(reactions, 1):
        rx_msg = format_reaction_message(rx, ticket, request_id)

        # Если реакций несколько — префикс i/n
        if n > 1:
            rx_msg = f"{i}/{n}: {rx_msg}"

        # Если дубликат — к первому сообщению добавляем префикс дубликата
        if is_duplicate and i == 1:
            dup_prefix = MSG_DUPLICATE_PREFIX.format(number=ticket_number)
            rx_msg = f"{dup_prefix}{rx_msg}"

        tg.send_message(chat_id, rx_msg)

        # Переход автомата в dialog:<request_id> при уточнении
        if rx.get("kind") == "clarification" and request_id is not None:
            set_chat_state(chat_id, f"dialog:{request_id}")


def handle_dialog_text(
    text: str,
    chat_id: int,
    state: str,
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Сценарий S2b (US3): продолжение уточняющего диалога по обращению."""
    parts = state.split(":", 1)
    req_id_str = parts[1] if len(parts) > 1 else ""
    try:
        req_id = int(req_id_str)
    except ValueError:
        logger.warning("Некорректный request_id в состоянии %s для chat_id=%s", state, chat_id)
        set_chat_state(chat_id, "idle")
        tg.send_message(chat_id, MSG_DIALOG_COMPLETED)
        return

    try:
        resp = core.reply_to_request(chat_id, req_id, text)
    except CoreApiConflictError:
        set_chat_state(chat_id, "idle")
        tg.send_message(chat_id, MSG_DIALOG_COMPLETED)
        return
    except (CoreApiError, Exception) as exc:
        logger.warning("Ошибка ответа в диалоге для req_id=%s (chat_id=%s): %s", req_id, chat_id, exc)
        tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)
        return

    if not isinstance(resp, dict):
        set_chat_state(chat_id, "idle")
        tg.send_message(chat_id, MSG_DIALOG_COMPLETED)
        return

    reactions = resp.get("reactions") or []
    ticket = resp.get("ticket") or {}

    if not reactions:
        set_chat_state(chat_id, "idle")
        tg.send_message(chat_id, MSG_DIALOG_COMPLETED)
        return

    is_duplicate = bool(resp.get("duplicate"))
    ticket_number = ticket.get("number") or str(req_id)

    active_req_id = resp.get("request_id") or req_id
    has_clarification = False
    n = len(reactions)
    for i, rx in enumerate(reactions, 1):
        if rx.get("kind") == "clarification":
            has_clarification = True
            if rx.get("request_id"):
                active_req_id = rx.get("request_id")

        rx_msg = format_reaction_message(rx, ticket, req_id)
        if n > 1:
            rx_msg = f"{i}/{n}: {rx_msg}"

        if is_duplicate and i == 1:
            dup_prefix = MSG_DUPLICATE_PREFIX.format(number=ticket_number)
            rx_msg = f"{dup_prefix}{rx_msg}"

        tg.send_message(chat_id, rx_msg)

    if has_clarification:
        set_chat_state(chat_id, f"dialog:{active_req_id}")
    else:
        set_chat_state(chat_id, "idle")


def handle_status_command(
    chat_id: int,
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Сценарий S3 (US4): вывод списка обращений пользователя."""
    try:
        requests = core.get_my_requests(chat_id)
    except (CoreApiError, Exception) as exc:
        logger.warning("Ошибка получения списка обращений для chat_id=%s: %s", chat_id, exc)
        tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)
        return

    if not requests:
        tg.send_message(chat_id, T12_NO_REQUESTS)
        return

    lines = []
    has_incident = False

    for req in requests[:10]:
        ticket = req.get("ticket") or {}
        number = ticket.get("number") or req.get("number") or str(req.get("id") or "")
        status = ticket.get("status") or req.get("status") or ""

        created_at = req.get("created_at") or ""
        date_str = ""
        if created_at:
            try:
                dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if dt.tzinfo is not None:
                    dt = dt.astimezone(MSK_TZ)
                date_str = dt.strftime("%d.%m %H:%M")
            except Exception:
                date_str = str(created_at)[:16]

        subtasks = req.get("subtasks") or []
        summary = ""
        if subtasks and isinstance(subtasks, list):
            summary = subtasks[0].get("summary") or ""
        if not summary:
            summary = req.get("summary") or req.get("masked_text") or req.get("text") or ""

        parts = [p for p in (number, date_str, status, summary) if p]
        lines.append(" · ".join(parts))

        if (
            req.get("incident_active")
            or req.get("has_incident")
            or ticket.get("incident_id") is not None
            or req.get("incident_id") is not None
        ):
            has_incident = True

    if has_incident:
        lines.append(MSG_INCIDENT_ROW)

    msg_text = "\n".join(lines)
    tg.send_message(chat_id, msg_text)


def handle_certs_command(
    chat_id: int,
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Сценарий S4 (US4): вывод каталога справок и текущих заказов."""
    try:
        catalog = core.get_certs_catalog(chat_id)
        orders = core.get_my_cert_orders(chat_id)
    except (CoreApiError, Exception) as exc:
        logger.warning("Ошибка получения справок для chat_id=%s: %s", chat_id, exc)
        tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)
        return

    # Сообщение 1: Заказ справки + K1
    k1 = make_cert_catalog_keyboard(catalog)
    tg.send_message(chat_id, MSG_CERTS_CATALOG_TITLE, reply_markup=k1)

    # Сообщение 2: Ваши заказы
    if not orders:
        orders_text = f"{MSG_CERTS_ORDERS_TITLE}\n{MSG_NO_CERT_ORDERS}"
    else:
        order_lines = [MSG_CERTS_ORDERS_TITLE]
        for ord in orders:
            oid = ord.get("id", "")
            title = ord.get("title", "")
            status = ord.get("status", "")
            order_lines.append(f"№{oid} · {title} · {status}")
        orders_text = "\n".join(order_lines)

    tg.send_message(chat_id, orders_text)


def handle_text_message(
    text: str,
    chat_id: int,
    link_info: dict[str, Any],
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Обработка обычного текста по состоянию пользователя."""
    state = get_effective_state(chat_id, link_info)
    logger.debug("Обработка текста в состоянии %s для chat_id=%s", state, chat_id)

    if state == "awaiting_confirm":
        handle_awaiting_confirm(text, chat_id, tg, core)
    elif state == "awaiting_email" or not is_user_linked(link_info):
        handle_awaiting_email(text, chat_id, tg, core)
    elif state == "idle":
        handle_idle_text(text, chat_id, tg, core)
    elif state.startswith("dialog:"):
        handle_dialog_text(text, chat_id, state, tg, core)


def handle_message(
    message: dict[str, Any],
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Обработка входящего сообщения Telegram."""
    chat = message.get("chat", {})
    chat_type = chat.get("type")
    chat_id = chat.get("id")

    # Граница: группы игнорируются (только личка, chat.type == "private")
    if chat_type != "private" or chat_id is None:
        logger.debug("Пропуск сообщения из не-private чата: type=%s, id=%s", chat_type, chat_id)
        return

    # Граница: фото, голос, файлы, стикеры -> только текст
    text = message.get("text")
    if text is None:
        tg.send_message(chat_id, MSG_TEXT_ONLY)
        return

    text = text.strip()

    # Запрос состояния привязки в ядре
    try:
        link_info = core.get_tg_link(chat_id)
    except CoreApiError as exc:
        logger.warning("Ошибка связи с ядром при get_tg_link для %s: %s", chat_id, exc)
        tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)
        return

    if text.startswith("/"):
        # Извлекаем имя команды без параметров и юзернейма бота (например /start@bot -> /start)
        command = text.split()[0].split("@")[0].lower()
        handle_command(command, chat_id, link_info, tg, core)
    else:
        handle_text_message(text, chat_id, link_info, tg, core)


def handle_callback_query(
    callback_query: dict[str, Any],
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Обработка нажатий на inline-кнопки (callback_query)."""
    cb_id = callback_query.get("id")
    message = callback_query.get("message", {})
    chat = message.get("chat", {})
    chat_type = chat.get("type")
    chat_id = chat.get("id")
    msg_id = message.get("message_id")

    # Игнорируем вызовы из групп
    if chat_type != "private" or chat_id is None:
        if cb_id:
            tg.answer_callback_query(cb_id)
        return

    data = callback_query.get("data", "")
    logger.debug("Callback query получен: id=%s, data=%s", cb_id, data)

    # Обязательный ack для снятия индикатора ожидания у пользователя
    if cb_id:
        tg.answer_callback_query(cb_id)

    if data == "noop":
        return

    elif data.startswith("cert:"):
        sub = data[len("cert:"):]
        if sub == "my":
            try:
                orders = core.get_my_cert_orders(chat_id)
                if not orders:
                    text = f"{MSG_CERTS_ORDERS_TITLE}\n{MSG_NO_CERT_ORDERS}"
                else:
                    order_lines = [MSG_CERTS_ORDERS_TITLE]
                    for ord in orders:
                        oid = ord.get("id", "")
                        title = ord.get("title", "")
                        status = ord.get("status", "")
                        order_lines.append(f"№{oid} · {title} · {status}")
                    text = "\n".join(order_lines)
                if msg_id:
                    tg.edit_message_text(chat_id, msg_id, text)
                else:
                    tg.send_message(chat_id, text)
            except CoreApiError:
                tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)
        else:
            cert_type = sub
            k2 = make_cert_order_confirm_keyboard(cert_type)
            confirm_text = f"Заказать справку ({cert_type})?"
            if msg_id:
                tg.edit_message_text(chat_id, msg_id, confirm_text, reply_markup=k2)
            else:
                tg.send_message(chat_id, confirm_text, reply_markup=k2)

    elif data.startswith("cert_order:"):
        cert_type = data[len("cert_order:"):]
        try:
            res = core.create_cert_order(chat_id, cert_type)
            order = res.get("order") or res
            order_id = order.get("id", "")
            title = order.get("title", cert_type)
            status = order.get("status", "не обработана")
            success_text = T14_CERT_ORDERED.format(id=order_id, title=title, status=status)
            if msg_id:
                tg.edit_message_text(chat_id, msg_id, success_text)
            else:
                tg.send_message(chat_id, success_text)
        except CoreApiError:
            tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)


def dispatch_update(
    update: dict[str, Any],
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Главный диспетчер апдейтов Telegram."""
    if "message" in update:
        handle_message(update["message"], tg, core)
    elif "callback_query" in update:
        handle_callback_query(update["callback_query"], tg, core)
    else:
        logger.debug("Неподдерживаемый тип update: %s", list(update.keys()))
