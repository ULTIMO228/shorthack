"""Тесты фонового цикла доставки уведомлений дежурному (US5 / T011)."""

from unittest.mock import MagicMock
import pytest

from app.bot.main import BotRunner


def test_outbox_step_delivers_pending_messages_and_acks_sent():
    tg = MagicMock()
    core = MagicMock()
    core.get_pending_outbound.return_value = [
        {"id": 101, "chat_id": 12345, "text": "🔴 Инцидент: сбой сети Wi-Fi"},
        {"id": 102, "chat_id": 67890, "text": "👤 Эскалация заявки SUP-2026-0001"},
    ]

    runner = BotRunner(tg_client=tg, core_client=core)
    delivered = runner.run_outbox_step()

    assert delivered == 2
    assert tg.send_message.call_count == 2
    tg.send_message.assert_any_call(12345, "🔴 Инцидент: сбой сети Wi-Fi")
    tg.send_message.assert_any_call(67890, "👤 Эскалация заявки SUP-2026-0001")

    assert core.ack_outbound.call_count == 2
    core.ack_outbound.assert_any_call(101, "sent")
    core.ack_outbound.assert_any_call(102, "sent")


def test_outbox_step_uses_duty_chat_env_if_chat_id_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TG_DUTY_CHAT_ID", "-100998877")
    tg = MagicMock()
    core = MagicMock()
    core.get_pending_outbound.return_value = [
        {"id": 103, "chat_id": None, "text": "🔴 Инцидент дежурному"}
    ]

    runner = BotRunner(tg_client=tg, core_client=core)
    delivered = runner.run_outbox_step()

    assert delivered == 1
    tg.send_message.assert_called_once_with(-100998877, "🔴 Инцидент дежурному")
    core.ack_outbound.assert_called_once_with(103, "sent")


def test_outbox_step_delivery_error_acks_failed():
    tg = MagicMock()
    tg.send_message.side_effect = RuntimeError("Telegram API timeout")
    core = MagicMock()
    core.get_pending_outbound.return_value = [
        {"id": 104, "chat_id": 12345, "text": "Сообщение, которое упадет"}
    ]

    runner = BotRunner(tg_client=tg, core_client=core)
    delivered = runner.run_outbox_step()

    assert delivered == 0
    tg.send_message.assert_called_once_with(12345, "Сообщение, которое упадет")
    core.ack_outbound.assert_called_once_with(104, "failed")


def test_outbox_step_missing_destination_acks_failed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TG_DUTY_CHAT_ID", raising=False)
    tg = MagicMock()
    core = MagicMock()
    core.get_pending_outbound.return_value = [
        {"id": 105, "chat_id": None, "text": "Нет адресата"}
    ]

    runner = BotRunner(tg_client=tg, core_client=core)
    delivered = runner.run_outbox_step()

    assert delivered == 0
    tg.send_message.assert_not_called()
    core.ack_outbound.assert_called_once_with(105, "failed")


def test_outbox_step_empty_queue():
    tg = MagicMock()
    core = MagicMock()
    core.get_pending_outbound.return_value = []

    runner = BotRunner(tg_client=tg, core_client=core)
    delivered = runner.run_outbox_step()

    assert delivered == 0
    tg.send_message.assert_not_called()
    core.ack_outbound.assert_not_called()


def test_outbox_step_core_error_handled():
    tg = MagicMock()
    core = MagicMock()
    core.get_pending_outbound.side_effect = RuntimeError("Core database connection lost")

    runner = BotRunner(tg_client=tg, core_client=core)
    delivered = runner.run_outbox_step()

    assert delivered == 0
    tg.send_message.assert_not_called()
    core.ack_outbound.assert_not_called()
