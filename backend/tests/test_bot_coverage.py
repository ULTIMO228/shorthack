"""Тесты для достижения >90% покрытия кода всех модулей app.bot."""

import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import httpx
import pytest

from app.bot.core_api import CoreApiClient, CoreApiConflictError, CoreApiError
from app.bot.handlers import (
    dispatch_update,
    format_reaction_message,
    handle_awaiting_confirm,
    handle_awaiting_email,
    handle_callback_query,
    handle_dialog_text,
    handle_idle_text,
    handle_status_command,
    load_states,
    reset_chat_states,
    save_states,
    set_chat_state,
)
from app.bot.main import (
    BotRunner,
    load_offset,
    main,
    save_offset,
)
from app.bot.messages import (
    MSG_CERTS_ORDERS_TITLE,
    MSG_DIALOG_COMPLETED,
    MSG_INCIDENT_ROW,
    MSG_NO_CERT_ORDERS,
    T5_EMAIL_INVALID,
    T6_LINK_SUCCESS,
    T7_CODE_INVALID,
    T8_ATTEMPTS_EXCEEDED,
    T13_SERVICE_UNAVAILABLE,
    T14_CERT_ORDERED,
)
from app.bot.tg import TelegramClient


# =====================================================================
# 1. Тесты CoreApiClient (полное покрытие всех веток)
# =====================================================================


def test_core_api_close():
    api = CoreApiClient(base_url="http://test:8000")
    # Добавим клиент пользователя
    api._get_user_client(12345)
    assert len(api._sessions) == 1
    api.close()
    assert len(api._sessions) == 0


def test_core_api_handle_response_variations():
    api = CoreApiClient(base_url="http://test:8000")

    # 409 с не-JSON телом
    resp_409 = MagicMock()
    resp_409.status_code = 409
    resp_409.json.side_effect = ValueError("Invalid json")
    resp_409.text = "Plain conflict"
    with pytest.raises(CoreApiConflictError) as exc_info:
        api._handle_response(resp_409)
    assert "Plain conflict" in exc_info.value.detail

    # 400 с не-JSON телом
    resp_400 = MagicMock()
    resp_400.status_code = 400
    resp_400.json.side_effect = ValueError("Invalid json")
    resp_400.text = "Bad request text"
    with pytest.raises(CoreApiError) as exc_400:
        api._handle_response(resp_400)
    assert "Bad request text" in exc_400.value.detail

    # 204 No Content
    resp_204 = MagicMock()
    resp_204.status_code = 204
    assert api._handle_response(resp_204) is None


def test_core_api_endpoints_success_and_errors():
    api = CoreApiClient(base_url="http://test:8000")

    # get_tg_link 404
    resp_404 = MagicMock(status_code=404)
    with patch.object(api._sys_http, "get", return_value=resp_404):
        link = api.get_tg_link(111)
        assert link["state"] == "awaiting_email"

    # get_tg_link HTTPError
    with patch.object(api._sys_http, "get", side_effect=httpx.ConnectError("Connection refused")):
        with pytest.raises(CoreApiError):
            api.get_tg_link(111)

    # request_tg_link HTTPError
    with patch.object(api._sys_http, "post", side_effect=httpx.ConnectError("Fail")):
        with pytest.raises(CoreApiError):
            api.request_tg_link(111, "test@misis.ru")

    # confirm_tg_link success and HTTPError
    resp_confirm = MagicMock(status_code=200)
    resp_confirm.json.return_value = {"ok": True}
    with patch.object(api._sys_http, "post", return_value=resp_confirm):
        assert api.confirm_tg_link(111, "123456") == {"ok": True}

    with patch.object(api._sys_http, "post", side_effect=httpx.ConnectError("Fail")):
        with pytest.raises(CoreApiError):
            api.confirm_tg_link(111, "123456")

    # get_pending_outbound non-list and error
    resp_out_not_list = MagicMock(status_code=200)
    resp_out_not_list.json.return_value = {"not": "a list"}
    with patch.object(api._sys_http, "get", return_value=resp_out_not_list):
        assert api.get_pending_outbound() == []

    with patch.object(api._sys_http, "get", side_effect=httpx.ConnectError("Fail")):
        assert api.get_pending_outbound() == []

    # ack_outbound success and error
    resp_ack = MagicMock(status_code=200)
    resp_ack.json.return_value = {"ok": True}
    with patch.object(api._sys_http, "post", return_value=resp_ack):
        assert api.ack_outbound(99, "sent") is True

    with patch.object(api._sys_http, "post", side_effect=httpx.ConnectError("Fail")):
        assert api.ack_outbound(99, "sent") is False

    # login success and error
    u_client = api._get_user_client(111)
    resp_login = MagicMock(status_code=200)
    resp_login.json.return_value = {"user": {"id": 1}}
    with patch.object(u_client, "post", return_value=resp_login):
        assert api.login(111, "test@misis.ru") == {"user": {"id": 1}}

    with patch.object(u_client, "post", side_effect=httpx.ConnectError("Fail")):
        with pytest.raises(CoreApiError):
            api.login(111, "test@misis.ru")

    # create_request HTTPError
    with patch.object(u_client, "post", side_effect=httpx.ConnectError("Fail")):
        with pytest.raises(CoreApiError):
            api.create_request(111, "problem")

    # reply_to_request success and error
    resp_reply = MagicMock(status_code=200)
    resp_reply.json.return_value = {"ok": True}
    with patch.object(u_client, "post", return_value=resp_reply):
        assert api.reply_to_request(111, 42, "reply") == {"ok": True}

    with patch.object(u_client, "post", side_effect=httpx.ConnectError("Fail")):
        with pytest.raises(CoreApiError):
            api.reply_to_request(111, 42, "reply")

    # get_my_requests success, non-list, error
    resp_reqs = MagicMock(status_code=200)
    resp_reqs.json.return_value = [{"id": 1}]
    with patch.object(u_client, "get", return_value=resp_reqs):
        assert api.get_my_requests(111) == [{"id": 1}]

    resp_reqs.json.return_value = {}
    with patch.object(u_client, "get", return_value=resp_reqs):
        assert api.get_my_requests(111) == []

    with patch.object(u_client, "get", side_effect=httpx.ConnectError("Fail")):
        with pytest.raises(CoreApiError):
            api.get_my_requests(111)

    # get_certs_catalog success, non-list, error
    resp_cat = MagicMock(status_code=200)
    resp_cat.json.return_value = [{"type": "study"}]
    with patch.object(u_client, "get", return_value=resp_cat):
        assert api.get_certs_catalog(111) == [{"type": "study"}]

    resp_cat.json.return_value = None
    with patch.object(u_client, "get", return_value=resp_cat):
        assert api.get_certs_catalog(111) == []

    with patch.object(u_client, "get", side_effect=httpx.ConnectError("Fail")):
        with pytest.raises(CoreApiError):
            api.get_certs_catalog(111)

    # get_my_cert_orders success, non-list, error
    resp_ord = MagicMock(status_code=200)
    resp_ord.json.return_value = [{"id": 5}]
    with patch.object(u_client, "get", return_value=resp_ord):
        assert api.get_my_cert_orders(111) == [{"id": 5}]

    resp_ord.json.return_value = "invalid"
    with patch.object(u_client, "get", return_value=resp_ord):
        assert api.get_my_cert_orders(111) == []

    with patch.object(u_client, "get", side_effect=httpx.ConnectError("Fail")):
        with pytest.raises(CoreApiError):
            api.get_my_cert_orders(111)

    # create_cert_order success and error
    resp_create_ord = MagicMock(status_code=200)
    resp_create_ord.json.return_value = {"order": {"id": 10}}
    with patch.object(u_client, "post", return_value=resp_create_ord):
        assert api.create_cert_order(111, "study") == {"order": {"id": 10}}

    with patch.object(u_client, "post", side_effect=httpx.ConnectError("Fail")):
        with pytest.raises(CoreApiError):
            api.create_cert_order(111, "study")


# =====================================================================
# 2. Тесты Handlers (проверка всех оставшихся веток)
# =====================================================================


def test_handle_awaiting_email_core_errors():
    tg = MagicMock()
    core = MagicMock()

    # 400 CoreApiError
    core.request_tg_link.side_effect = CoreApiError(400, "Bad email")
    handle_awaiting_email("student@misis.ru", 12345, tg, core)
    tg.send_message.assert_called_once_with(12345, T5_EMAIL_INVALID)

    # 500 CoreApiError
    tg.reset_mock()
    core.request_tg_link.side_effect = CoreApiError(500, "Server error")
    handle_awaiting_email("student@misis.ru", 12345, tg, core)
    tg.send_message.assert_called_once_with(12345, T13_SERVICE_UNAVAILABLE)


def test_handle_awaiting_confirm_login_error_and_core_exceptions():
    tg = MagicMock()
    core = MagicMock()

    # Успех, но login бросает исключение -> должно логироваться, а T6 всё равно отправляться
    core.confirm_tg_link.return_value = {
        "ok": True,
        "user": {"email": "student@misis.ru", "full_name": "Иван"},
    }
    core.login.side_effect = RuntimeError("Redis session error")
    handle_awaiting_confirm("123456", 12345, tg, core)
    tg.send_message.assert_called_once_with(12345, T6_LINK_SUCCESS.format(full_name="Иван"))

    # CoreApiError 400 с attempts_exceeded
    tg.reset_mock()
    core.confirm_tg_link.side_effect = CoreApiError(400, "attempts_exceeded")
    handle_awaiting_confirm("123456", 12345, tg, core)
    tg.send_message.assert_called_once_with(12345, T8_ATTEMPTS_EXCEEDED)

    # CoreApiError 400 без attempts_exceeded
    tg.reset_mock()
    core.confirm_tg_link.side_effect = CoreApiError(400, "invalid code")
    handle_awaiting_confirm("123456", 12345, tg, core)
    tg.send_message.assert_called_once_with(12345, T7_CODE_INVALID.format(n=1))

    # CoreApiError 500
    tg.reset_mock()
    core.confirm_tg_link.side_effect = CoreApiError(500, "Crash")
    handle_awaiting_confirm("123456", 12345, tg, core)
    tg.send_message.assert_called_once_with(12345, T13_SERVICE_UNAVAILABLE)


def test_format_reaction_message_edge_cases():
    ticket = {"number": "SUP-1"}

    # outage_notice уже начинается с 🔴
    rx1 = {"kind": "outage_notice", "text": "🔴 Сервис лежит"}
    assert format_reaction_message(rx1, ticket, 10) == "🔴 Сервис лежит"

    # cert_ordered без id/title/status
    rx2 = {"kind": "cert_ordered", "text": "Текст справки"}
    assert format_reaction_message(rx2, ticket, 10) == "Текст справки"


def test_handle_idle_text_empty_or_malformed_response():
    tg = MagicMock()
    core = MagicMock()

    # Не dict
    core.create_request.return_value = "Not a dict"
    handle_idle_text("Проблема", 12345, tg, core)
    assert tg.send_message.call_count == 2
    assert tg.send_message.call_args[0][1] == T13_SERVICE_UNAVAILABLE

    # Пустые reactions
    tg.reset_mock()
    core.create_request.return_value = {"ticket": {"number": "SUP-99"}, "reactions": []}
    handle_idle_text("Проблема", 12345, tg, core)
    assert tg.send_message.call_count == 2
    assert "Обращение зарегистрировано: SUP-99" in tg.send_message.call_args[0][1]


def test_handle_dialog_text_empty_reactions_and_malformed():
    tg = MagicMock()
    core = MagicMock()

    # Не dict
    core.reply_to_request.return_value = 123
    handle_dialog_text("Ответ", 12345, "dialog:42", tg, core)
    tg.send_message.assert_called_once_with(12345, MSG_DIALOG_COMPLETED)

    # Пустые reactions
    tg.reset_mock()
    core.reply_to_request.return_value = {"reactions": []}
    handle_dialog_text("Ответ", 12345, "dialog:42", tg, core)
    tg.send_message.assert_called_once_with(12345, MSG_DIALOG_COMPLETED)

    # Мульти-подзадачи в диалоге с дубликатом
    tg.reset_mock()
    core.reply_to_request.return_value = {
        "duplicate": True,
        "reactions": [
            {"kind": "clarification", "text": "Вопрос 1", "request_id": 99},
            {"kind": "answer", "text": "Ответ 2"},
        ],
    }
    handle_dialog_text("Ответ", 12345, "dialog:42", tg, core)
    assert tg.send_message.call_count == 2


def test_handle_status_date_and_summary_fallbacks():
    tg = MagicMock()
    core = MagicMock()

    # Дата не ISO и summary из masked_text
    core.get_my_requests.return_value = [
        {
            "id": 1,
            "created_at": "not-a-valid-date-string",
            "masked_text": "Маскированный текст проблемы",
            "ticket": {"number": "SUP-10", "status": "открыта"},
        }
    ]
    handle_status_command(12345, tg, core)
    msg = tg.send_message.call_args[0][1]
    assert "SUP-10" in msg
    assert "not-a-valid-date" in msg
    assert "Маскированный текст проблемы" in msg


def test_callback_without_message_id():
    tg = MagicMock()
    core = MagicMock()

    # cert:my без message_id
    core.get_my_cert_orders.return_value = []
    cb_my = {
        "id": "cb1",
        "data": "cert:my",
        "message": {"chat": {"id": 12345, "type": "private"}},
    }
    handle_callback_query(cb_my, tg, core)
    tg.send_message.assert_called_once()
    assert MSG_NO_CERT_ORDERS in tg.send_message.call_args[0][1]

    # cert:study без message_id
    tg.reset_mock()
    cb_study = {
        "id": "cb2",
        "data": "cert:study",
        "message": {"chat": {"id": 12345, "type": "private"}},
    }
    handle_callback_query(cb_study, tg, core)
    tg.send_message.assert_called_once()

    # cert_order:study без message_id
    tg.reset_mock()
    core.create_cert_order.return_value = {"id": 1, "title": "Справка", "status": "ок"}
    cb_order = {
        "id": "cb3",
        "data": "cert_order:study",
        "message": {"chat": {"id": 12345, "type": "private"}},
    }
    handle_callback_query(cb_order, tg, core)
    tg.send_message.assert_called_once()
    assert "Справка" in tg.send_message.call_args[0][1]


# =====================================================================
# 3. Тесты TelegramClient (обработка ошибок и edge-cases)
# =====================================================================


def test_tg_client_close():
    client = TelegramClient(token="TEST_TOKEN")
    client.close()


def test_tg_client_rate_limit_invalid_json_body():
    client = TelegramClient(token="TEST_TOKEN")
    resp_429 = MagicMock(status_code=429)
    resp_429.json.side_effect = ValueError("Invalid JSON")
    resp_200 = MagicMock(status_code=200)
    resp_200.json.return_value = {"ok": True, "result": {}}

    with patch.object(client.http, "request", side_effect=[resp_429, resp_200]):
        with patch("time.sleep") as mock_sleep:
            client.get_me()
            mock_sleep.assert_called_with(5)


def test_tg_client_api_error_response():
    client = TelegramClient(token="TEST_TOKEN")
    resp_err = MagicMock(status_code=200)
    resp_err.json.return_value = {"ok": False, "description": "Unauthorized"}

    with patch.object(client.http, "request", return_value=resp_err):
        with pytest.raises(RuntimeError) as exc_info:
            client.get_me()
        assert "Unauthorized" in str(exc_info.value)


def test_tg_client_network_retries_and_failure():
    client = TelegramClient(token="TEST_TOKEN")
    with patch.object(client.http, "request", side_effect=httpx.ConnectError("Network fail")):
        with patch("time.sleep"):
            with pytest.raises(httpx.ConnectError):
                client.get_me()


def test_tg_client_edit_message_with_reply_markup():
    client = TelegramClient(token="TEST_TOKEN")
    resp_200 = MagicMock(status_code=200)
    resp_200.json.return_value = {"ok": True, "result": {"message_id": 99}}

    with patch.object(client.http, "request", return_value=resp_200) as mock_req:
        client.edit_message_text(12345, 99, "New text", reply_markup={"inline_keyboard": []})
        payload = mock_req.call_args[1]["json"]
        assert payload["reply_markup"] == {"inline_keyboard": []}


def test_tg_client_answer_callback_query_alert():
    client = TelegramClient(token="TEST_TOKEN")
    resp_200 = MagicMock(status_code=200)
    resp_200.json.return_value = {"ok": True, "result": True}

    with patch.object(client.http, "request", return_value=resp_200) as mock_req:
        res = client.answer_callback_query("cb_id", text="Alert msg", show_alert=True)
        assert res is True
        payload = mock_req.call_args[1]["json"]
        assert payload["show_alert"] is True
        assert payload["text"] == "Alert msg"


# =====================================================================
# 4. Тесты main.py (BotRunner, load/save offset, CLI)
# =====================================================================


def test_load_and_save_offset_edge_cases(tmp_path: Path):
    offset_file = tmp_path / "offset.txt"

    # Нечисловой offset
    offset_file.write_text("invalid_offset", encoding="utf-8")
    assert load_offset(offset_file) is None

    # Ошибка чтения
    with patch.object(Path, "is_file", side_effect=PermissionError("Denied")):
        assert load_offset(offset_file) is None

    # Ошибка записи
    with patch.object(Path, "write_text", side_effect=PermissionError("Denied")):
        save_offset(offset_file, 555)  # не должно падать


def test_bot_runner_process_update_without_dispatch():
    tg = MagicMock()
    runner = BotRunner(tg_client=tg)
    runner.stop()
    assert runner.running is False

    with patch("app.bot.main.dispatch_update", None):
        runner.process_update({"update_id": 1})


def test_bot_runner_run_loop():
    tg = MagicMock()
    tg.get_me.return_value = {"username": "test_bot"}
    runner = BotRunner(tg_client=tg)

    # Имитируем один шаг и остановку
    def stop_after_one(timeout=30):
        runner.stop()
        return 0

    runner.run_polling_step = stop_after_one
    runner.run()
    assert runner.running is False

    # Имитируем KeyboardInterrupt
    runner.running = True

    def raise_interrupt(timeout=30):
        raise KeyboardInterrupt()

    runner.run_polling_step = raise_interrupt
    runner.run()

    # get_me ошибка
    tg.get_me.side_effect = RuntimeError("TG down")
    runner.running = True
    runner.run_polling_step = stop_after_one
    runner.run()


def test_main_cli_missing_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_main_cli_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("TG_BOT_TOKEN", "12345:ABCDE")

    with patch("app.bot.main.TelegramClient") as mock_tg_cls, \
         patch("app.bot.main.CoreApiClient") as mock_core_cls, \
         patch("app.bot.main.BotRunner") as mock_runner_cls:

        mock_runner = MagicMock()
        mock_runner.offset = 42
        mock_runner.offset_file = tmp_path / ".bot_offset"
        mock_runner_cls.return_value = mock_runner

        # Запускаем main()
        with patch("signal.signal") as mock_signal:
            main()
            # Проверяем вызов обработчика сигнала
            sig_handler = mock_signal.call_args_list[0][0][1]
            sig_handler(signal.SIGINT, None)
            mock_runner.stop.assert_called_once()

        mock_runner.run.assert_called_once()
        mock_tg_cls.return_value.close.assert_called_once()
        mock_core_cls.return_value.close.assert_called_once()


def test_handlers_dispatch_unsupported_update():
    tg = MagicMock()
    core = MagicMock()
    dispatch_update({"unknown_update_type": {}}, tg, core)
    tg.send_message.assert_not_called()


def test_callback_cert_my_core_error():
    tg = MagicMock()
    core = MagicMock()
    core.get_my_cert_orders.side_effect = CoreApiError(500, "Error")

    cb = {
        "id": "cb_err",
        "data": "cert:my",
        "message": {"chat": {"id": 12345, "type": "private"}},
    }
    handle_callback_query(cb, tg, core)
    tg.send_message.assert_called_once_with(12345, T13_SERVICE_UNAVAILABLE)


def test_core_api_lru_eviction():
    api = CoreApiClient(base_url="http://test:8000")
    api._max_sessions = 2

    c1 = api._get_user_client(1)
    c2 = api._get_user_client(2)
    assert len(api._sessions) == 2

    # Обращение к c1 делает c2 самым старым
    api._get_user_client(1)

    with patch.object(c2, "close") as mock_close:
        c3 = api._get_user_client(3)
        mock_close.assert_called_once()
        assert len(api._sessions) == 2
        assert 2 not in api._sessions
        assert 1 in api._sessions
        assert 3 in api._sessions
    api.close()


def test_core_api_ensure_user_session_and_401_retry():
    api = CoreApiClient(base_url="http://test:8000")
    user_client = api._get_user_client(12345)

    resp_401 = MagicMock(status_code=401)
    resp_200 = MagicMock(status_code=200)
    resp_200.json.return_value = {"ok": True, "request_id": 77}

    with patch.object(user_client, "post", side_effect=[resp_401, resp_200]):
        with patch.object(api, "get_tg_link", return_value={"ok": True, "email": "student@misis.ru"}):
            with patch.object(api, "login", return_value={"ok": True}) as mock_login:
                res = api.create_request(12345, "Проблема с личным кабинетом")
                assert res["request_id"] == 77
                mock_login.assert_called_once_with(12345, "student@misis.ru")


def test_core_api_ensure_user_session_failures():
    api = CoreApiClient(base_url="http://test:8000")

    # Сбой при get_tg_link
    with patch.object(api, "get_tg_link", side_effect=RuntimeError("DB err")):
        assert api.ensure_user_session(111) is False

    # Нет email в tg_link
    with patch.object(api, "get_tg_link", return_value={"ok": False, "state": "awaiting_email"}):
        assert api.ensure_user_session(111) is False

    # Сбой при login
    with patch.object(api, "get_tg_link", return_value={"ok": True, "email": "test@misis.ru"}):
        with patch.object(api, "login", side_effect=CoreApiError(500, "Login fail")):
            assert api.ensure_user_session(111) is False


def test_tg_client_server_error_5xx_retry():
    client = TelegramClient(token="TEST_TOKEN")
    resp_502 = MagicMock(status_code=502)
    resp_200 = MagicMock(status_code=200)
    resp_200.json.return_value = {"ok": True, "result": {"username": "misis_bot"}}

    with patch.object(client.http, "request", side_effect=[resp_502, resp_200]):
        with patch("time.sleep") as mock_sleep:
            me = client.get_me()
            assert me["username"] == "misis_bot"
            mock_sleep.assert_called_once_with(1.0)


def test_bot_runner_outbox_thread_and_stop():
    tg = MagicMock()
    tg.get_me.return_value = {"username": "test_bot"}
    core = MagicMock()
    runner = BotRunner(tg_client=tg, core_client=core)

    # Имитируем быстрый polling и остановку
    def stop_runner(timeout=30):
        runner.stop()
        return 0

    runner.run_polling_step = stop_runner
    runner.run(start_outbox_thread=True)

    assert runner.running is False
    assert runner._outbox_thread is not None
    assert runner._outbox_thread.is_alive() is False


def test_handlers_state_persistence(tmp_path: Path):
    test_state_file = tmp_path / ".test_bot_states"
    reset_chat_states(path=test_state_file)

    # Сохранение dialog:100
    set_chat_state(12345, "dialog:100", path=test_state_file)
    loaded = load_states(path=test_state_file)
    assert loaded.get(12345) == "dialog:100"

    # Сброс в idle не сохраняется в файл
    set_chat_state(12345, "idle", path=test_state_file)
    loaded_after = load_states(path=test_state_file)
    assert 12345 not in loaded_after

    # Поврежденный файл
    test_state_file.write_text("not json content", encoding="utf-8")
    assert load_states(path=test_state_file) == {}

    reset_chat_states(path=test_state_file)
    assert not test_state_file.is_file()


def test_status_command_msk_timezone():
    tg = MagicMock()
    core = MagicMock()
    core.get_my_requests.return_value = [
        {
            "id": 1,
            "created_at": "2026-09-12T10:00:00Z",
            "ticket": {"number": "SUP-999", "status": "открыта"},
            "subtasks": [{"summary": "Вопрос по общежитию"}],
        }
    ]

    handle_status_command(12345, tg, core)
    tg.send_message.assert_called_once()
    sent_text = tg.send_message.call_args[0][1]
    # 10:00 UTC -> 13:00 MSK
    assert "12.09 13:00" in sent_text
    assert "SUP-999" in sent_text


def test_is_user_linked_resilience():
    from app.bot.handlers import is_user_linked, handle_message

    # Без ключа 'ok', но state='idle' и user_id задан
    link_info_no_ok = {"chat_id": 1380996180, "user_id": 1, "state": "idle"}
    assert is_user_linked(link_info_no_ok) is True

    # Состояние awaiting_email или awaiting_confirm -> False
    assert is_user_linked({"chat_id": 1, "state": "awaiting_email"}) is False
    assert is_user_linked({"chat_id": 1, "state": "awaiting_confirm"}) is False

    # Явный ok: False -> False
    assert is_user_linked({"ok": False, "state": "idle"}) is False

    # Проверка вызова handle_message: текст не должен трактоваться как email
    tg = MagicMock()
    core = MagicMock()
    core.get_tg_link.return_value = link_info_no_ok
    core.create_request.return_value = {
        "request_id": 10,
        "ticket": {"number": "SUP-10"},
        "reactions": [{"kind": "answer", "text": "Принято"}],
    }

    handle_message(
        {"chat": {"id": 1380996180, "type": "private"}, "text": "в корпусе Б не ловит вайфай"},
        tg,
        core,
    )
    core.create_request.assert_called_once_with(1380996180, "в корпусе Б не ловит вайфай")


