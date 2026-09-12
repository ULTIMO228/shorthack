"""Тесты Фазы 1 (Setup) Telegram-бота поддержки МИСИС (без внешних сетевых вызовов)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.bot.core_api import CoreApiClient, CoreApiConflictError, CoreApiError
from app.bot.keyboards import make_cert_catalog_keyboard, make_cert_order_confirm_keyboard
from app.bot.main import BotRunner, load_offset, save_offset
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
    T9_PROCESSING,
    T10_OUTAGE_NOTICE,
    T11_ESCALATED,
    T12_NO_REQUESTS,
    T13_SERVICE_UNAVAILABLE,
    T14_CERT_ORDERED,
    T15_OUT_OF_SCOPE,
)
from app.bot.tg import TelegramClient, split_text


# --- 1. Тесты split_text (tg.py) ---


def test_split_text_short():
    text = "Короткое сообщение"
    chunks = split_text(text)
    assert chunks == [text]


def test_split_text_exact_limit():
    text = "a" * 4096
    chunks = split_text(text, max_len=4096)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_split_text_by_paragraphs():
    p1 = "Абзац 1: " + "x" * 2000
    p2 = "Абзац 2: " + "y" * 2500
    text = f"{p1}\n{p2}"
    chunks = split_text(text, max_len=4096)

    assert len(chunks) == 2
    assert chunks[0] == p1 + "\n"
    assert chunks[1].startswith(p2)
    assert chunks[1].endswith(" (продолжение)")
    for chunk in chunks:
        assert len(chunk) <= 4096


def test_split_text_long_unbroken_line():
    text = "x" * 10000
    chunks = split_text(text, max_len=4096)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert len(chunk) <= 4096


# --- 2. Тесты констант сообщений (messages.py) ---


def test_messages_content_and_formatting():
    assert "Здравствуйте!" in T1_GREETING
    assert "проверяю сервисы" in T2_HELP
    assert "@misis.ru" in T3_ASK_EMAIL

    t4 = T4_EMAIL_SENT.format(email="student@edu.misis.ru")
    assert "student@edu.misis.ru" in t4

    assert "Это не корпоративная почта" in T5_EMAIL_INVALID

    t6 = T6_LINK_SUCCESS.format(full_name="Иван Иванов")
    assert "Иван Иванов" in t6

    t7 = T7_CODE_INVALID.format(n=2)
    assert "2" in t7

    assert "Попытки кончились" in T8_ATTEMPTS_EXCEEDED
    assert "Принял, разбираю" in T9_PROCESSING

    t10 = T10_OUTAGE_NOTICE.format(text="Сбой Wi-Fi")
    assert t10 == "🔴 Сбой Wi-Fi"

    t11 = T11_ESCALATED.format(number="SUP-2026-0001")
    assert "SUP-2026-0001" in t11

    assert "У вас пока нет обращений." in T12_NO_REQUESTS
    assert "Сервис временно недоступен" in T13_SERVICE_UNAVAILABLE

    t14 = T14_CERT_ORDERED.format(id=42, title="Справка об обучении", status="новая")
    assert "№42" in t14
    assert "Справка об обучении" in t14

    assert T15_OUT_OF_SCOPE.format(text="ответ") == "ответ"
    assert "Сначала привяжите аккаунт" in MSG_NEED_LINK
    assert "отменено" in MSG_CANCELLED
    assert "только с текстом" in MSG_TEXT_ONLY


# --- 3. Тесты клавиатур (keyboards.py) ---


def test_cert_catalog_keyboard():
    catalog = [
        {"type": "study", "title": "Справка с места учёбы"},
        {"type": "academic", "title": "Академическая справка"},
    ]
    kb = make_cert_catalog_keyboard(catalog)
    rows = kb.get("inline_keyboard", [])
    assert len(rows) == 3
    assert rows[0][0]["text"] == "Справка с места учёбы"
    assert rows[0][0]["callback_data"] == "cert:study"
    assert rows[1][0]["text"] == "Академическая справка"
    assert rows[1][0]["callback_data"] == "cert:academic"
    assert rows[2][0]["text"] == "Мои заказы"
    assert rows[2][0]["callback_data"] == "cert:my"


def test_cert_order_confirm_keyboard():
    kb = make_cert_order_confirm_keyboard("study")
    rows = kb.get("inline_keyboard", [])
    assert len(rows) == 1
    buttons = rows[0]
    assert len(buttons) == 2
    assert buttons[0]["text"] == "✅ Заказать"
    assert buttons[0]["callback_data"] == "cert_order:study"
    assert buttons[1]["text"] == "Отмена"
    assert buttons[1]["callback_data"] == "noop"


# --- 4. Тесты TelegramClient (tg.py) ---


def test_tg_client_get_me():
    client = TelegramClient(token="TEST_TOKEN")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"id": 123, "username": "test_misis_bot"}}

    with patch.object(client.http, "request", return_value=mock_resp) as mock_req:
        me = client.get_me()
        assert me["username"] == "test_misis_bot"
        assert mock_req.call_args[0] == ("GET", "https://api.telegram.org/botTEST_TOKEN/getMe")


def test_tg_client_get_updates():
    client = TelegramClient(token="TEST_TOKEN")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": [{"update_id": 101, "message": {"text": "/start"}}],
    }

    with patch.object(client.http, "request", return_value=mock_resp) as mock_req:
        updates = client.get_updates(offset=100, timeout=25)
        assert len(updates) == 1
        assert updates[0]["update_id"] == 101
        json_data = mock_req.call_args[1]["json"]
        assert json_data["offset"] == 100
        assert json_data["timeout"] == 25
        assert json_data["allowed_updates"] == ["message", "callback_query"]


def test_tg_client_send_message_and_markup():
    client = TelegramClient(token="TEST_TOKEN")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 1}}

    with patch.object(client.http, "request", return_value=mock_resp) as mock_req:
        markup = {"inline_keyboard": []}
        res = client.send_message(chat_id=555, text="Hello", reply_markup=markup)
        assert len(res) == 1
        payload = mock_req.call_args[1]["json"]
        assert payload["chat_id"] == 555
        assert payload["text"] == "Hello"
        assert payload["reply_markup"] == markup


def test_tg_client_edit_message_and_callback():
    client = TelegramClient(token="TEST_TOKEN")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": True}

    with patch.object(client.http, "request", return_value=mock_resp):
        res_edit = client.edit_message_text(chat_id=555, message_id=42, text="Updated")
        assert res_edit is True or isinstance(res_edit, dict)

        ack = client.answer_callback_query("cb_1", text="Ok")
        assert ack is True


def test_tg_client_rate_limit_retry():
    client = TelegramClient(token="TEST_TOKEN")
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.json.return_value = {"parameters": {"retry_after": 0.01}}

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"ok": True, "result": {"id": 1, "username": "bot"}}

    with patch.object(client.http, "request", side_effect=[resp_429, resp_200]):
        with patch("time.sleep") as mock_sleep:
            me = client.get_me()
            assert me["username"] == "bot"
            mock_sleep.assert_called_with(0.01)


# --- 5. Тесты CoreApiClient (core_api.py) ---


def test_core_api_tg_link_and_auth():
    api = CoreApiClient(base_url="http://core:8000", internal_token="secret_internal")

    # get_tg_link 200
    resp_link = MagicMock()
    resp_link.status_code = 200
    resp_link.json.return_value = {"ok": True, "state": "idle", "user_id": 10}

    with patch.object(api._sys_http, "get", return_value=resp_link) as mock_get:
        link = api.get_tg_link(12345)
        assert link["state"] == "idle"
        mock_get.assert_called_with("/api/internal/tg/link", params={"chat_id": 12345})

    # request_tg_link
    resp_req = MagicMock()
    resp_req.status_code = 200
    resp_req.json.return_value = {"ok": True}
    with patch.object(api._sys_http, "post", return_value=resp_req):
        res = api.request_tg_link(12345, "ivan@misis.ru")
        assert res["ok"] is True


def test_core_api_create_request_and_conflict():
    api = CoreApiClient()
    user_client = api._get_user_client(999)

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.json.return_value = {"request_id": 5, "reactions": [{"kind": "answer", "text": "Ok"}]}

    with patch.object(user_client, "post", return_value=resp_ok):
        res = api.create_request(999, "Не работает Wi-Fi")
        assert res["request_id"] == 5

    # 409 conflict
    resp_409 = MagicMock()
    resp_409.status_code = 409
    resp_409.json.return_value = {"detail": "Диалог завершен"}
    with patch.object(user_client, "post", return_value=resp_409):
        with pytest.raises(CoreApiConflictError) as exc_info:
            api.reply_to_request(999, 5, "Уже починили?")
        assert exc_info.value.status_code == 409


# --- 6. Тесты main.py (offset и BotRunner) ---


def test_offset_save_and_load(tmp_path: Path):
    offset_file = tmp_path / ".bot_offset"
    assert load_offset(offset_file) is None

    save_offset(offset_file, 456789)
    assert load_offset(offset_file) == 456789

    # Corrupted content
    offset_file.write_text("invalid", encoding="utf-8")
    assert load_offset(offset_file) is None


def test_bot_runner_polling_step(tmp_path: Path):
    offset_file = tmp_path / ".bot_offset"
    tg_mock = MagicMock()
    core_mock = MagicMock()

    tg_mock.get_updates.return_value = [
        {"update_id": 10, "message": {"text": "test 1"}},
        {"update_id": 11, "message": {"text": "test 2"}},
    ]

    runner = BotRunner(tg_client=tg_mock, core_client=core_mock, offset_file=offset_file)
    count = runner.run_polling_step(timeout=5)

    assert count == 2
    assert runner.offset == 12
    assert load_offset(offset_file) == 12
    assert runner.backoff == 1.0


def test_bot_runner_polling_step_error_backoff(tmp_path: Path):
    offset_file = tmp_path / ".bot_offset"
    tg_mock = MagicMock()
    tg_mock.get_updates.side_effect = httpx.ConnectError("Connection refused")

    runner = BotRunner(tg_client=tg_mock, offset_file=offset_file)
    with patch("time.sleep"):
        count = runner.run_polling_step(timeout=5)
        assert count == 0
        assert runner.backoff == 2.0  # 1.0 * 2.0
