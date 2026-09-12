"""Юнит-тесты маршрутизации и диспетчеризации Telegram-бота (T006)."""

from unittest.mock import MagicMock

from app.bot.core_api import CoreApiError
from app.bot.handlers import dispatch_update
from app.bot.messages import (
    MSG_CANCELLED,
    MSG_NEED_LINK,
    MSG_TEXT_ONLY,
    T1_GREETING,
    T2_HELP,
    T3_ASK_EMAIL,
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
