"""Юнит-тесты маршрутизации и диспетчеризации Telegram-бота (T006)."""

from unittest.mock import MagicMock
import pytest

from app.bot.core_api import CoreApiConflictError, CoreApiError
from app.bot.handlers import (
    dispatch_update,
    get_effective_state,
    is_valid_misis_email,
    reset_chat_states,
    set_chat_state,
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


@pytest.fixture(autouse=True)
def clean_chat_states():
    """Сброс локального состояния чатов перед каждым тестом."""
    reset_chat_states()
    yield
    reset_chat_states()


def test_ignore_group_messages():
    tg = MagicMock()
    core = MagicMock()

    update = {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": -100123, "type": "supergroup"},
            "text": "/start",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_not_called()
    core.get_tg_link.assert_not_called()


def test_non_text_message_warns_user():
    tg = MagicMock()
    core = MagicMock()

    update = {
        "update_id": 2,
        "message": {
            "message_id": 11,
            "chat": {"id": 12345, "type": "private"},
            "photo": [{"file_id": "xyz"}],
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, MSG_TEXT_ONLY)
    core.get_tg_link.assert_not_called()


def test_core_api_unavailable_handles_gracefully():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.side_effect = CoreApiError(503, "Connection failed")

    update = {
        "update_id": 3,
        "message": {
            "message_id": 12,
            "chat": {"id": 12345, "type": "private"},
            "text": "/start",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, T13_SERVICE_UNAVAILABLE)


def test_start_unlinked_user():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": False, "state": "awaiting_email"}

    update = {
        "update_id": 4,
        "message": {
            "message_id": 13,
            "chat": {"id": 12345, "type": "private"},
            "text": "/start",
        },
    }

    dispatch_update(update, tg, core)

    assert tg.send_message.call_count == 2
    calls = [call[0] for call in tg.send_message.call_args_list]
    assert calls[0] == (12345, T1_GREETING)
    assert calls[1] == (12345, T3_ASK_EMAIL)


def test_start_linked_user():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 99}

    update = {
        "update_id": 5,
        "message": {
            "message_id": 14,
            "chat": {"id": 12345, "type": "private"},
            "text": "/start@misis_bot",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, T1_GREETING)


def test_help_command():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle"}

    update = {
        "update_id": 6,
        "message": {
            "message_id": 15,
            "chat": {"id": 12345, "type": "private"},
            "text": "/help",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, T2_HELP)


def test_cancel_command():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle"}

    update = {
        "update_id": 7,
        "message": {
            "message_id": 16,
            "chat": {"id": 12345, "type": "private"},
            "text": "/cancel",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, MSG_CANCELLED)


def test_status_and_certs_unlinked():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": False, "state": "awaiting_email"}

    # /status
    update_status = {
        "update_id": 8,
        "message": {
            "message_id": 17,
            "chat": {"id": 12345, "type": "private"},
            "text": "/status",
        },
    }
    dispatch_update(update_status, tg, core)
    tg.send_message.assert_called_with(12345, MSG_NEED_LINK)

    # /certs
    tg.reset_mock()
    update_certs = {
        "update_id": 9,
        "message": {
            "message_id": 18,
            "chat": {"id": 12345, "type": "private"},
            "text": "/certs",
        },
    }
    dispatch_update(update_certs, tg, core)
    tg.send_message.assert_called_with(12345, MSG_NEED_LINK)


def test_callback_query_ack():
    tg = MagicMock()
    core = MagicMock()

    update = {
        "update_id": 10,
        "callback_query": {
            "id": "cb_query_123",
            "message": {
                "chat": {"id": 12345, "type": "private"},
                "message_id": 20,
            },
            "data": "noop",
        },
    }

    dispatch_update(update, tg, core)

    tg.answer_callback_query.assert_called_once_with("cb_query_123")


# --- US2: Привязка профиля по почте (T008) ---


def test_is_valid_misis_email():
    assert is_valid_misis_email("ivan@misis.ru") is True
    assert is_valid_misis_email("STUDENT@EDU.MISIS.RU") is True
    assert is_valid_misis_email("p.ivanov-26@misis.ru") is True

    assert is_valid_misis_email("ivan@gmail.com") is False
    assert is_valid_misis_email("ivan@misis.ru.com") is False
    assert is_valid_misis_email("not-an-email") is False
    assert is_valid_misis_email("@misis.ru") is False


def test_awaiting_email_invalid_input():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": False, "state": "awaiting_email"}

    update = {
        "update_id": 11,
        "message": {
            "message_id": 21,
            "chat": {"id": 12345, "type": "private"},
            "text": "мой email ivan@mail.ru",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, T5_EMAIL_INVALID)
    core.request_tg_link.assert_not_called()


def test_awaiting_email_valid_input():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": False, "state": "awaiting_email"}
    core.request_tg_link.return_value = {"ok": True, "confirm_code": "1234"}

    update = {
        "update_id": 12,
        "message": {
            "message_id": 22,
            "chat": {"id": 12345, "type": "private"},
            "text": "  student@edu.misis.ru  ",
        },
    }

    dispatch_update(update, tg, core)

    core.request_tg_link.assert_called_once_with(12345, "student@edu.misis.ru")
    tg.send_message.assert_called_once_with(
        12345,
        T4_EMAIL_SENT.format(email="student@edu.misis.ru"),
    )


def test_awaiting_confirm_success():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": False, "state": "awaiting_confirm"}
    core.confirm_tg_link.return_value = {
        "ok": True,
        "user": {"full_name": "Алексей Иванов", "email": "a.ivanov@misis.ru"},
    }

    update = {
        "update_id": 13,
        "message": {
            "message_id": 23,
            "chat": {"id": 12345, "type": "private"},
            "text": "123456",
        },
    }

    dispatch_update(update, tg, core)

    core.confirm_tg_link.assert_called_once_with(12345, "123456")
    core.login.assert_called_once_with(12345, "a.ivanov@misis.ru")
    tg.send_message.assert_called_once_with(
        12345,
        T6_LINK_SUCCESS.format(full_name="Алексей Иванов"),
    )


def test_awaiting_confirm_invalid_code():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": False, "state": "awaiting_confirm"}
    core.confirm_tg_link.return_value = {
        "ok": False,
        "reason": "invalid_code",
        "remaining_attempts": 2,
    }

    update = {
        "update_id": 14,
        "message": {
            "message_id": 24,
            "chat": {"id": 12345, "type": "private"},
            "text": "000000",
        },
    }

    dispatch_update(update, tg, core)

    core.confirm_tg_link.assert_called_once_with(12345, "000000")
    core.login.assert_not_called()
    tg.send_message.assert_called_once_with(12345, T7_CODE_INVALID.format(n=2))


def test_awaiting_confirm_attempts_exceeded():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": False, "state": "awaiting_confirm"}
    core.confirm_tg_link.return_value = {
        "ok": False,
        "reason": "attempts_exceeded",
        "remaining_attempts": 0,
    }

    update = {
        "update_id": 15,
        "message": {
            "message_id": 25,
            "chat": {"id": 12345, "type": "private"},
            "text": "999999",
        },
    }

    dispatch_update(update, tg, core)

    core.confirm_tg_link.assert_called_once_with(12345, "999999")
    core.login.assert_not_called()
    tg.send_message.assert_called_once_with(12345, T8_ATTEMPTS_EXCEEDED)


# --- US1: Обращение через бота (Фаза 3 / T007) ---


def test_idle_text_instant_t9_and_request_creation():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.create_request.return_value = {
        "request_id": 101,
        "duplicate": False,
        "reactions": [{"kind": "answer", "text": "Инструкция по подключению к Wi-Fi отправлена."}],
        "ticket": {"number": "SUP-2026-0001"},
    }

    update = {
        "update_id": 16,
        "message": {
            "message_id": 26,
            "chat": {"id": 12345, "type": "private"},
            "text": "Не работает Wi-Fi в корпусе Б",
        },
    }

    dispatch_update(update, tg, core)

    # 1. Мгновенный фидбек T9
    # 2. Сообщение ответа
    assert tg.send_message.call_count == 2
    calls = [call[0] for call in tg.send_message.call_args_list]
    assert calls[0] == (12345, T9_PROCESSING)
    assert calls[1] == (12345, "Инструкция по подключению к Wi-Fi отправлена.")

    core.create_request.assert_called_once_with(12345, "Не работает Wi-Fi в корпусе Б")


def test_idle_text_outage_notice_reaction():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.create_request.return_value = {
        "request_id": 102,
        "duplicate": False,
        "reactions": [{"kind": "outage_notice", "text": "Массовый сбой Wi-Fi MISIS-EDU"}],
        "ticket": {"number": "SUP-2026-0002"},
    }

    update = {
        "update_id": 17,
        "message": {
            "message_id": 27,
            "chat": {"id": 12345, "type": "private"},
            "text": "Wi-Fi упал",
        },
    }

    dispatch_update(update, tg, core)

    assert tg.send_message.call_count == 2
    calls = [call[0] for call in tg.send_message.call_args_list]
    assert calls[0] == (12345, T9_PROCESSING)
    assert calls[1] == (12345, "🔴 Массовый сбой Wi-Fi MISIS-EDU")

    # Состояние должно остаться idle
    assert get_effective_state(12345, core.get_tg_link.return_value) == "idle"


def test_idle_text_clarification_reaction_sets_dialog_state():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.create_request.return_value = {
        "request_id": 205,
        "duplicate": False,
        "reactions": [
            {
                "kind": "clarification",
                "text": "Уточните, пожалуйста: ошибка на одном устройстве или на всех?",
            }
        ],
        "ticket": {"number": "SUP-2026-0003"},
    }

    update = {
        "update_id": 18,
        "message": {
            "message_id": 28,
            "chat": {"id": 12345, "type": "private"},
            "text": "Не могу зайти в личный кабинет",
        },
    }

    dispatch_update(update, tg, core)

    calls = [call[0] for call in tg.send_message.call_args_list]
    assert calls[0] == (12345, T9_PROCESSING)
    assert calls[1] == (
        12345,
        "Уточните, пожалуйста: ошибка на одном устройстве или на всех?",
    )

    # Проверяем переход в dialog:205
    assert get_effective_state(12345, core.get_tg_link.return_value) == "dialog:205"


def test_idle_text_escalated_reaction():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.create_request.return_value = {
        "request_id": 301,
        "duplicate": False,
        "reactions": [{"kind": "escalated"}],
        "ticket": {"number": "SUP-2026-0099"},
    }

    update = {
        "update_id": 19,
        "message": {
            "message_id": 29,
            "chat": {"id": 12345, "type": "private"},
            "text": "Сложная нетиповая проблема",
        },
    }

    dispatch_update(update, tg, core)

    calls = [call[0] for call in tg.send_message.call_args_list]
    assert calls[1] == (12345, T11_ESCALATED.format(number="SUP-2026-0099"))


def test_idle_text_cert_ordered_reaction():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.create_request.return_value = {
        "request_id": 401,
        "duplicate": False,
        "reactions": [
            {
                "kind": "cert_ordered",
                "id": 15,
                "title": "Справка с места учёбы",
                "status": "не обработана",
            }
        ],
        "ticket": {"number": "SUP-2026-0150"},
    }

    update = {
        "update_id": 20,
        "message": {
            "message_id": 30,
            "chat": {"id": 12345, "type": "private"},
            "text": "Мне нужна справка об обучении",
        },
    }

    dispatch_update(update, tg, core)

    calls = [call[0] for call in tg.send_message.call_args_list]
    expected_msg = T14_CERT_ORDERED.format(
        id=15,
        title="Справка с места учёбы",
        status="не обработана",
    )
    assert calls[1] == (12345, expected_msg)


def test_idle_text_duplicate_prefix():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.create_request.return_value = {
        "request_id": 501,
        "duplicate": True,
        "reactions": [{"kind": "answer", "text": "Заявка уже рассматривается специалистом."}],
        "ticket": {"number": "SUP-2026-0077"},
    }

    update = {
        "update_id": 21,
        "message": {
            "message_id": 31,
            "chat": {"id": 12345, "type": "private"},
            "text": "Повторяю: интернет не работает",
        },
    }

    dispatch_update(update, tg, core)

    calls = [call[0] for call in tg.send_message.call_args_list]
    expected_prefix = MSG_DUPLICATE_PREFIX.format(number="SUP-2026-0077")
    assert calls[1] == (12345, f"{expected_prefix}Заявка уже рассматривается специалистом.")


def test_idle_text_multiple_subtasks_prefix():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.create_request.return_value = {
        "request_id": 601,
        "duplicate": False,
        "reactions": [
            {"kind": "answer", "text": "Wi-Fi в корпусе работает штатно."},
            {"kind": "answer", "text": "Пароль от LMS можно сбросить на портале."},
        ],
        "ticket": {"number": "SUP-2026-0080"},
    }

    update = {
        "update_id": 22,
        "message": {
            "message_id": 32,
            "chat": {"id": 12345, "type": "private"},
            "text": "Не работает Wi-Fi и забыл пароль от LMS",
        },
    }

    dispatch_update(update, tg, core)

    # 1 (T9) + 2 реакции = 3 сообщения
    assert tg.send_message.call_count == 3
    calls = [call[0] for call in tg.send_message.call_args_list]
    assert calls[0] == (12345, T9_PROCESSING)
    assert calls[1] == (12345, "1/2: Wi-Fi в корпусе работает штатно.")
    assert calls[2] == (12345, "2/2: Пароль от LMS можно сбросить на портале.")


def test_idle_text_duplicate_and_multiple_subtasks():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.create_request.return_value = {
        "request_id": 701,
        "duplicate": True,
        "reactions": [
            {"kind": "answer", "text": "Ответ 1"},
            {"kind": "answer", "text": "Ответ 2"},
        ],
        "ticket": {"number": "SUP-2026-0090"},
    }

    update = {
        "update_id": 23,
        "message": {
            "message_id": 33,
            "chat": {"id": 12345, "type": "private"},
            "text": "Две проблемы повторно",
        },
    }

    dispatch_update(update, tg, core)

    calls = [call[0] for call in tg.send_message.call_args_list]
    dup_prefix = MSG_DUPLICATE_PREFIX.format(number="SUP-2026-0090")
    assert calls[1] == (12345, f"{dup_prefix}1/2: Ответ 1")
    assert calls[2] == (12345, "2/2: Ответ 2")


def test_idle_text_core_error_sends_t13():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.create_request.side_effect = CoreApiError(503, "Core service unavailable")

    update = {
        "update_id": 24,
        "message": {
            "message_id": 34,
            "chat": {"id": 12345, "type": "private"},
            "text": "Любое обращение при упавшем ядре",
        },
    }

    dispatch_update(update, tg, core)

    assert tg.send_message.call_count == 2
    calls = [call[0] for call in tg.send_message.call_args_list]
    assert calls[0] == (12345, T9_PROCESSING)
    assert calls[1] == (12345, T13_SERVICE_UNAVAILABLE)


def test_cancel_resets_dialog_state_to_idle():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}

    # Имитируем переход в диалог
    set_chat_state(12345, "dialog:888")
    assert get_effective_state(12345, core.get_tg_link.return_value) == "dialog:888"

    # Пользователь шлёт /cancel
    update = {
        "update_id": 25,
        "message": {
            "message_id": 35,
            "chat": {"id": 12345, "type": "private"},
            "text": "/cancel",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, MSG_CANCELLED)
    # Состояние должно сброситься в idle
    assert get_effective_state(12345, core.get_tg_link.return_value) == "idle"


# --- US3: Уточняющий диалог (Фаза 5 / T009) ---


def test_dialog_reply_clarification_keeps_dialog_state():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    set_chat_state(12345, "dialog:105")

    core.reply_to_request.return_value = {
        "reactions": [
            {"kind": "clarification", "text": "Какая модель роутера или номер аудитории?"}
        ],
        "ticket": {"number": "SUP-2026-0005"},
    }

    update = {
        "update_id": 30,
        "message": {
            "message_id": 40,
            "chat": {"id": 12345, "type": "private"},
            "text": "Аудитория Б-201",
        },
    }

    dispatch_update(update, tg, core)

    core.reply_to_request.assert_called_once_with(12345, 105, "Аудитория Б-201")
    tg.send_message.assert_called_once_with(
        12345, "Какая модель роутера или номер аудитории?"
    )
    assert get_effective_state(12345, core.get_tg_link.return_value) == "dialog:105"


def test_dialog_reply_final_answer_resets_to_idle():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    set_chat_state(12345, "dialog:105")

    core.reply_to_request.return_value = {
        "reactions": [{"kind": "answer", "text": "Точка доступа в Б-201 перезагружена."}],
        "ticket": {"number": "SUP-2026-0005"},
    }

    update = {
        "update_id": 31,
        "message": {
            "message_id": 41,
            "chat": {"id": 12345, "type": "private"},
            "text": "Проблема решена?",
        },
    }

    dispatch_update(update, tg, core)

    core.reply_to_request.assert_called_once_with(12345, 105, "Проблема решена?")
    tg.send_message.assert_called_once_with(
        12345, "Точка доступа в Б-201 перезагружена."
    )
    # Так как clarification не было, автомат возвращается в idle
    assert get_effective_state(12345, core.get_tg_link.return_value) == "idle"


def test_dialog_reply_conflict_409_resets_to_idle():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    set_chat_state(12345, "dialog:105")

    core.reply_to_request.side_effect = CoreApiConflictError(409, "Диалог завершён")

    update = {
        "update_id": 32,
        "message": {
            "message_id": 42,
            "chat": {"id": 12345, "type": "private"},
            "text": "Спасибо",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, MSG_DIALOG_COMPLETED)
    assert get_effective_state(12345, core.get_tg_link.return_value) == "idle"


def test_dialog_reply_core_error_503():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    set_chat_state(12345, "dialog:105")

    core.reply_to_request.side_effect = CoreApiError(503, "Service unavailable")

    update = {
        "update_id": 33,
        "message": {
            "message_id": 43,
            "chat": {"id": 12345, "type": "private"},
            "text": "Ответ при сбое",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, T13_SERVICE_UNAVAILABLE)


def test_dialog_reply_invalid_state_format():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    set_chat_state(12345, "dialog:non_integer")

    update = {
        "update_id": 34,
        "message": {
            "message_id": 44,
            "chat": {"id": 12345, "type": "private"},
            "text": "Текст в битом состоянии",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, MSG_DIALOG_COMPLETED)
    assert get_effective_state(12345, core.get_tg_link.return_value) == "idle"


def test_dialog_reply_multiple_reactions_with_prefix():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    set_chat_state(12345, "dialog:105")

    core.reply_to_request.return_value = {
        "reactions": [
            {"kind": "answer", "text": "Шаг 1: проверьте провод."},
            {"kind": "answer", "text": "Шаг 2: введите логин."},
        ],
        "ticket": {"number": "SUP-2026-0005"},
    }

    update = {
        "update_id": 35,
        "message": {
            "message_id": 45,
            "chat": {"id": 12345, "type": "private"},
            "text": "Что делать дальше?",
        },
    }

    dispatch_update(update, tg, core)

    assert tg.send_message.call_count == 2
    calls = [call[0] for call in tg.send_message.call_args_list]
    assert calls[0] == (12345, "1/2: Шаг 1: проверьте провод.")
    assert calls[1] == (12345, "2/2: Шаг 2: введите логин.")


def test_dialog_reply_empty_reactions_resets_to_idle():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    set_chat_state(12345, "dialog:105")

    core.reply_to_request.return_value = {"reactions": []}

    update = {
        "update_id": 36,
        "message": {
            "message_id": 46,
            "chat": {"id": 12345, "type": "private"},
            "text": "Пустой ответ",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, MSG_DIALOG_COMPLETED)
    assert get_effective_state(12345, core.get_tg_link.return_value) == "idle"


def test_dialog_reply_updates_request_id_when_provided():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    set_chat_state(12345, "dialog:105")

    core.reply_to_request.return_value = {
        "request_id": 106,
        "reactions": [
            {"kind": "clarification", "text": "Уточните этаж в корпусе Б"}
        ],
    }

    update = {
        "update_id": 37,
        "message": {
            "message_id": 47,
            "chat": {"id": 12345, "type": "private"},
            "text": "Корпус Б",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, "Уточните этаж в корпусе Б")
    assert get_effective_state(12345, core.get_tg_link.return_value) == "dialog:106"


def test_dialog_reply_503_preserves_dialog_state_for_retry():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    set_chat_state(12345, "dialog:105")

    core.reply_to_request.side_effect = CoreApiError(503, "Service Unavailable")

    update = {
        "update_id": 38,
        "message": {
            "message_id": 48,
            "chat": {"id": 12345, "type": "private"},
            "text": "Повторный ответ при ошибке сети",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, T13_SERVICE_UNAVAILABLE)
    # Состояние диалога не должно быть сброшено в idle при временном сбое
    assert get_effective_state(12345, core.get_tg_link.return_value) == "dialog:105"


def test_dialog_full_multi_turn_e2e_scenario():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    reset_chat_states()

    # Шаг 1: Пользователь в idle пишет о проблеме -> ядро возвращает clarification
    core.create_request.return_value = {
        "request_id": 201,
        "ticket": {"number": "SUP-2026-0201"},
        "reactions": [{"kind": "clarification", "text": "Укажите имя сети Wi-Fi"}],
    }

    update_1 = {
        "update_id": 40,
        "message": {
            "message_id": 50,
            "chat": {"id": 12345, "type": "private"},
            "text": "Не подключается Wi-Fi",
        },
    }
    dispatch_update(update_1, tg, core)

    # Проверяем переход в dialog:201
    assert get_effective_state(12345, core.get_tg_link.return_value) == "dialog:201"
    core.create_request.assert_called_once_with(12345, "Не подключается Wi-Fi")

    # Шаг 2: Пользователь отвечает в диалоге -> ядро задаёт второй уточняющий вопрос
    core.reply_to_request.return_value = {
        "request_id": 201,
        "ticket": {"number": "SUP-2026-0201"},
        "reactions": [{"kind": "clarification", "text": "Какая ошибка при вводе пароля?"}],
    }

    update_2 = {
        "update_id": 41,
        "message": {
            "message_id": 51,
            "chat": {"id": 12345, "type": "private"},
            "text": "Сеть MISIS-GUEST",
        },
    }
    dispatch_update(update_2, tg, core)

    core.reply_to_request.assert_called_once_with(12345, 201, "Сеть MISIS-GUEST")
    assert get_effective_state(12345, core.get_tg_link.return_value) == "dialog:201"

    # Шаг 3: Пользователь отвечает на второй вопрос -> ядро даёт финальный ответ (answer)
    core.reply_to_request.reset_mock()
    core.reply_to_request.return_value = {
        "ticket": {"number": "SUP-2026-0201"},
        "reactions": [{"kind": "answer", "text": "Для MISIS-GUEST пароль не требуется, пройдите SMS-авторизацию."}],
    }

    update_3 = {
        "update_id": 42,
        "message": {
            "message_id": 52,
            "chat": {"id": 12345, "type": "private"},
            "text": "Пишет неверный пароль",
        },
    }
    dispatch_update(update_3, tg, core)

    core.reply_to_request.assert_called_once_with(12345, 201, "Пишет неверный пароль")
    # Диалог завершён, автомат вернулся в idle
    assert get_effective_state(12345, core.get_tg_link.return_value) == "idle"

    # Шаг 4: Следующее сообщение пользователя обрабатывается уже как НОВОЕ обращение в idle
    core.create_request.reset_mock()
    core.create_request.return_value = {
        "request_id": 202,
        "ticket": {"number": "SUP-2026-0202"},
        "reactions": [{"kind": "answer", "text": "Новое обращение зарегистрировано."}],
    }

    update_4 = {
        "update_id": 43,
        "message": {
            "message_id": 53,
            "chat": {"id": 12345, "type": "private"},
            "text": "А ещё столовая закрыта",
        },
    }
    dispatch_update(update_4, tg, core)

    core.create_request.assert_called_once_with(12345, "А ещё столовая закрыта")
    assert get_effective_state(12345, core.get_tg_link.return_value) == "idle"


# --- US4: Справки и статус через бота (Фаза 6 / T010) ---


def test_status_command_empty_requests():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.get_my_requests.return_value = []

    update = {
        "update_id": 36,
        "message": {
            "message_id": 46,
            "chat": {"id": 12345, "type": "private"},
            "text": "/status",
        },
    }

    dispatch_update(update, tg, core)

    core.get_my_requests.assert_called_once_with(12345)
    tg.send_message.assert_called_once_with(12345, T12_NO_REQUESTS)


def test_status_command_with_requests_and_formatting():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.get_my_requests.return_value = [
        {
            "id": 1,
            "ticket": {"number": "SUP-2026-0001", "status": "в работе"},
            "created_at": "2026-09-12T10:15:00Z",
            "subtasks": [{"summary": "Сбой Wi-Fi"}],
        },
        {
            "id": 2,
            "ticket": {"number": "SUP-2026-0002", "status": "закрыта"},
            "created_at": "2026-09-11T09:00:00Z",
            "subtasks": [{"summary": "Заказ справки"}],
        },
    ]

    update = {
        "update_id": 37,
        "message": {
            "message_id": 47,
            "chat": {"id": 12345, "type": "private"},
            "text": "/status",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once()
    sent_text = tg.send_message.call_args[0][1]
    assert "SUP-2026-0001" in sent_text
    assert "в работе" in sent_text
    assert "Сбой Wi-Fi" in sent_text
    assert "SUP-2026-0002" in sent_text
    assert "закрыта" in sent_text
    assert "Заказ справки" in sent_text
    assert MSG_INCIDENT_ROW not in sent_text


def test_status_command_with_incident_marker():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.get_my_requests.return_value = [
        {
            "id": 1,
            "ticket": {"number": "SUP-2026-0001", "status": "в работе"},
            "created_at": "2026-09-12T10:15:00Z",
            "subtasks": [{"summary": "Сбой Wi-Fi"}],
            "incident_active": True,
        }
    ]

    update = {
        "update_id": 38,
        "message": {
            "message_id": 48,
            "chat": {"id": 12345, "type": "private"},
            "text": "/status",
        },
    }

    dispatch_update(update, tg, core)

    sent_text = tg.send_message.call_args[0][1]
    assert MSG_INCIDENT_ROW in sent_text


def test_status_command_core_error():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.get_my_requests.side_effect = CoreApiError(503, "Service unavailable")

    update = {
        "update_id": 39,
        "message": {
            "message_id": 49,
            "chat": {"id": 12345, "type": "private"},
            "text": "/status",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, T13_SERVICE_UNAVAILABLE)


def test_certs_command_catalog_and_orders():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.get_certs_catalog.return_value = [
        {"type": "study", "title": "Справка с места учёбы"},
        {"type": "military", "title": "Справка в военкомат"},
    ]
    core.get_my_cert_orders.return_value = [
        {"id": 10, "title": "Справка с места учёбы", "status": "готова"}
    ]

    update = {
        "update_id": 40,
        "message": {
            "message_id": 50,
            "chat": {"id": 12345, "type": "private"},
            "text": "/certs",
        },
    }

    dispatch_update(update, tg, core)

    assert tg.send_message.call_count == 2
    calls = tg.send_message.call_args_list

    # Сообщение 1: каталог с K1
    assert calls[0][0] == (12345, MSG_CERTS_CATALOG_TITLE)
    markup = calls[0][1].get("reply_markup", {})
    assert "inline_keyboard" in markup
    assert len(markup["inline_keyboard"]) == 3  # 2 справки + "Мои заказы"

    # Сообщение 2: список заказов
    assert calls[1][0][0] == 12345
    assert MSG_CERTS_ORDERS_TITLE in calls[1][0][1]
    assert "№10 · Справка с места учёбы · готова" in calls[1][0][1]


def test_certs_command_no_orders():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.get_certs_catalog.return_value = [{"type": "study", "title": "Справка"}]
    core.get_my_cert_orders.return_value = []

    update = {
        "update_id": 41,
        "message": {
            "message_id": 51,
            "chat": {"id": 12345, "type": "private"},
            "text": "/certs",
        },
    }

    dispatch_update(update, tg, core)

    calls = tg.send_message.call_args_list
    assert MSG_NO_CERT_ORDERS in calls[1][0][1]


def test_certs_command_core_error():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}
    core.get_certs_catalog.side_effect = CoreApiError(503, "Service unavailable")

    update = {
        "update_id": 42,
        "message": {
            "message_id": 52,
            "chat": {"id": 12345, "type": "private"},
            "text": "/certs",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, T13_SERVICE_UNAVAILABLE)


def test_callback_cert_type_shows_confirm_k2():
    tg = MagicMock()
    core = MagicMock()

    update = {
        "update_id": 43,
        "callback_query": {
            "id": "cb_study_1",
            "message": {
                "message_id": 99,
                "chat": {"id": 12345, "type": "private"},
            },
            "data": "cert:study",
        },
    }

    dispatch_update(update, tg, core)

    tg.answer_callback_query.assert_called_once_with("cb_study_1")
    tg.edit_message_text.assert_called_once()
    args, kwargs = tg.edit_message_text.call_args
    assert args[0] == 12345
    assert args[1] == 99
    assert "study" in args[2]
    assert "inline_keyboard" in kwargs.get("reply_markup", {})


def test_callback_cert_order_creates_order():
    tg = MagicMock()
    core = MagicMock()
    core.create_cert_order.return_value = {
        "order": {
            "id": 55,
            "title": "Справка с места учёбы",
            "status": "не обработана",
        }
    }

    update = {
        "update_id": 44,
        "callback_query": {
            "id": "cb_order_1",
            "message": {
                "message_id": 99,
                "chat": {"id": 12345, "type": "private"},
            },
            "data": "cert_order:study",
        },
    }

    dispatch_update(update, tg, core)

    core.create_cert_order.assert_called_once_with(12345, "study")
    tg.answer_callback_query.assert_called_once_with("cb_order_1")
    tg.edit_message_text.assert_called_once_with(
        12345,
        99,
        T14_CERT_ORDERED.format(
            id=55,
            title="Справка с места учёбы",
            status="не обработана",
        ),
    )


def test_callback_cert_my_refreshes_orders():
    tg = MagicMock()
    core = MagicMock()
    core.get_my_cert_orders.return_value = [
        {"id": 55, "title": "Справка", "status": "выдана"}
    ]

    update = {
        "update_id": 45,
        "callback_query": {
            "id": "cb_my_1",
            "message": {
                "message_id": 100,
                "chat": {"id": 12345, "type": "private"},
            },
            "data": "cert:my",
        },
    }

    dispatch_update(update, tg, core)

    core.get_my_cert_orders.assert_called_once_with(12345)
    tg.answer_callback_query.assert_called_once_with("cb_my_1")
    tg.edit_message_text.assert_called_once()
    assert "№55 · Справка · выдана" in tg.edit_message_text.call_args[0][2]


def test_callback_noop():
    tg = MagicMock()
    core = MagicMock()

    update = {
        "update_id": 46,
        "callback_query": {
            "id": "cb_noop_1",
            "message": {
                "message_id": 101,
                "chat": {"id": 12345, "type": "private"},
            },
            "data": "noop",
        },
    }

    dispatch_update(update, tg, core)

    tg.answer_callback_query.assert_called_once_with("cb_noop_1")
    tg.edit_message_text.assert_not_called()
    tg.send_message.assert_not_called()


def test_callback_core_error():
    tg = MagicMock()
    core = MagicMock()
    core.create_cert_order.side_effect = CoreApiError(503, "Service unavailable")

    update = {
        "update_id": 47,
        "callback_query": {
            "id": "cb_err_1",
            "message": {
                "message_id": 102,
                "chat": {"id": 12345, "type": "private"},
            },
            "data": "cert_order:study",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_called_once_with(12345, T13_SERVICE_UNAVAILABLE)


# --- Границы и контракты (Фаза 8 / T013) ---


def test_callback_group_ignored():
    tg = MagicMock()
    core = MagicMock()

    update = {
        "update_id": 48,
        "callback_query": {
            "id": "cb_group_1",
            "message": {
                "message_id": 103,
                "chat": {"id": -100555, "type": "supergroup"},
            },
            "data": "cert:study",
        },
    }

    dispatch_update(update, tg, core)

    # В группах callback только ack-ается без действий
    tg.answer_callback_query.assert_called_once_with("cb_group_1")
    core.create_cert_order.assert_not_called()
    tg.edit_message_text.assert_not_called()


def test_unknown_command_ignored():
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = {"ok": True, "state": "idle", "user_id": 10}

    update = {
        "update_id": 49,
        "message": {
            "message_id": 104,
            "chat": {"id": 12345, "type": "private"},
            "text": "/foobar_random_command",
        },
    }

    dispatch_update(update, tg, core)

    tg.send_message.assert_not_called()



