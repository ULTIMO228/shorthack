"""Юнит-тесты маршрутизации и диспетчеризации Telegram-бота (T006)."""

from unittest.mock import MagicMock

from app.bot.core_api import CoreApiError
from app.bot.handlers import dispatch_update, is_valid_misis_email
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

