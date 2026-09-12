"""Маршрутизация и диспетчеризация сообщений Telegram-бота (фича 003, T006).

Реализует:
- Фильтрацию типов чатов (только private, группы игнорируются).
- Проверку типа контента (только текст, медиа/файлы -> MSG_TEXT_ONLY).
- Конечный автомат состояний (state machine по данным GET /api/internal/tg/link).
- Базовые команды: /start, /help, /cancel, /status, /certs.
- Обработку callback_query с обязательным answerCallbackQuery.
"""

import logging
import re
from typing import Any

from app.bot.core_api import CoreApiClient, CoreApiError
from app.bot.messages import (
    MSG_CANCELLED,
    MSG_NEED_LINK,
    MSG_TEXT_ONLY,
    T1_GREETING,
    T2_HELP,
    T3_ASK_EMAIL,
    T4_EMAIL_SENT,
    T5_EMAIL_INVALID,
    T6_LINK_SUCCESS,
    T7_CODE_INVALID,
    T8_ATTEMPTS_EXCEEDED,
    T13_SERVICE_UNAVAILABLE,
)
from app.bot.tg import TelegramClient

logger = logging.getLogger("bot.handlers")


def is_user_linked(link_info: dict[str, Any]) -> bool:
    """Проверка, привязан ли аккаунт пользователя."""
    if not link_info.get("ok"):
        return False
    state = link_info.get("state", "awaiting_email")
    return state not in ("awaiting_email", "awaiting_confirm")


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
        tg.send_message(chat_id, MSG_CANCELLED)

    elif command == "/status":
        if not linked:
            tg.send_message(chat_id, MSG_NEED_LINK)
        else:
            # Сценарий S3 (US4) будет подключен в Phase 6
            pass

    elif command == "/certs":
        if not linked:
            tg.send_message(chat_id, MSG_NEED_LINK)
        else:
            # Сценарий S4 (US4) будет подключен в Phase 6
            pass

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
                tg.send_message(chat_id, T8_ATTEMPTS_EXCEEDED)
            else:
                tg.send_message(chat_id, T7_CODE_INVALID.format(n=remaining))
    except CoreApiError as exc:
        if exc.status_code == 400:
            if "attempts_exceeded" in exc.detail:
                tg.send_message(chat_id, T8_ATTEMPTS_EXCEEDED)
            else:
                tg.send_message(chat_id, T7_CODE_INVALID.format(n=1))
        else:
            logger.warning("Ошибка связи с ядром при confirm_tg_link: %s", exc)
            tg.send_message(chat_id, T13_SERVICE_UNAVAILABLE)


def handle_text_message(
    text: str,
    chat_id: int,
    link_info: dict[str, Any],
    tg: TelegramClient,
    core: CoreApiClient,
) -> None:
    """Обработка обычного текста по состоянию пользователя."""
    state = link_info.get("state", "awaiting_email")
    logger.debug("Обработка текста в состоянии %s для chat_id=%s", state, chat_id)

    if state == "awaiting_confirm":
        handle_awaiting_confirm(text, chat_id, tg, core)
    elif state == "awaiting_email" or not is_user_linked(link_info):
        handle_awaiting_email(text, chat_id, tg, core)
    elif state == "idle":
        # Состояние idle (новое обращение) реализуется в US1 (Phase 3)
        pass
    elif state.startswith("dialog:"):
        # Состояние dialog:<id> реализуется в US3 (Phase 5)
        pass


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

    # Игнорируем вызовы из групп
    if chat_type != "private":
        if cb_id:
            tg.answer_callback_query(cb_id)
        return

    data = callback_query.get("data", "")
    logger.debug("Callback query получен: id=%s, data=%s", cb_id, data)

    # Обязательный ack для снятия индикатора ожидания у пользователя
    if cb_id:
        tg.answer_callback_query(cb_id)

    # Логика кнопок cert:* и cert_order:* реализуется в US4 (Phase 6)


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
