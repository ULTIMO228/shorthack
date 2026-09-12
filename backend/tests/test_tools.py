"""Инструменты автопроверки (US2, T022): реестр, диспетчер, check_site/check_lms/check_wifi.

Сеть замокана на уровне httpx.Client (app.tools.httpx.Client): 200/301/500/таймаут.
Критерий доступности (FR-022/023): HTTP 2xx/3xx в пределах 5 секунд. Ветка сбоя
в ноде execute_route не обращается к LLM (SC-004: извещение ≤ 2 c без модели).
Пайплайн-уровень сценариев 2-3 quickstart.md — в test_quickstart_us2.py.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import agent, llm, tickets, tools, triggers
from app.auth import hash_password
from app.models import Event, Request, Service, ServiceCheck, User
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Мок httpx: подмена клиента и подстановка ответов/исключений по URL
# ---------------------------------------------------------------------------

class FakeHttpResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeHttpClient:
    """Замена httpx.Client: get() возвращает подставленный ответ или бросает подставленное исключение."""

    instances: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        FakeHttpClient.instances.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url: str):
        item = fake_routes.get(url, FakeHttpResponse(200))
        if isinstance(item, Exception):
            raise item
        return item


fake_routes: dict = {}


@pytest.fixture()
def fake_http(monkeypatch):
    """Подмена app.tools.httpx.Client; вернуть словарь URL → ответ/исключение."""
    global fake_routes
    fake_routes = {}
    FakeHttpClient.instances = []
    monkeypatch.setattr("app.tools.httpx.Client", FakeHttpClient)
    return fake_routes


@pytest.fixture()
def db_session(db_engine):
    from sqlalchemy.orm import sessionmaker

    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()


def _make_subtask(db_session, service: str, route: str = "auto_check", priority: str = "high"):
    req = Request(session_id="s-tools", channel="web", raw_text="текст", masked_text="текст")
    db_session.add(req)
    db_session.commit()
    subtask = tickets.attach_subtask(
        db_session, req, "не работает сервис",
        service=service, category="availability", route=route, priority=priority,
    )
    ticket = tickets.ensure_ticket(db_session, req.id)
    return req, subtask, ticket


def make_counting_fake_llm(monkeypatch, *, classify: dict) -> dict:
    """Фейк app.llm.chat со счётчиком вызовов (проверка веток без LLM, SC-004).

    Диспетчеризация по маркерам промпта, как в test_agent.make_fake_llm:
    «Переведи» → перевод, '"subtasks"' → сплиттер, иначе — классификатор.
    """
    calls = {"count": 0}

    def fake_chat(messages, *, model=None, temperature=0.2, timeout=25.0):
        calls["count"] += 1
        text = messages if isinstance(messages, str) else "\n".join(m["content"] for m in messages)
        if "Переведи" in text:
            return "Не работает Wi-Fi."
        if '"subtasks"' in text:
            return json.dumps({"subtasks": [{"summary": "Единственная проблема"}]}, ensure_ascii=False)
        return json.dumps(classify, ensure_ascii=False)

    monkeypatch.setattr("app.llm.chat", fake_chat)
    return calls


# ---------------------------------------------------------------------------
# check_site / check_lms: критерий 2xx/3xx ≤ 5 c, запись service_checks
# ---------------------------------------------------------------------------

def test_check_site_200_ok(db_session, fake_http):
    fake_http["https://misis.ru"] = FakeHttpResponse(200)
    call = tools.call_tool(db_session, "check_site", {}, ticket_id=None, subtask_id=None, tool_calls=0)
    assert call.result["ok"] is True
    assert call.result["http_code"] == 200
    assert call.tool_calls == 1  # инкремент счётчика вызовов (FR-021)

    check = db_session.query(ServiceCheck).one()
    assert check.ok is True and check.http_code == 200

    service = db_session.query(Service).filter_by(name="misis.ru").one()
    assert service.check_type == "real" and service.state == "up"

    # журнал: tool_call + tool_result (FR-061)
    actions = [e.action for e in db_session.query(Event).order_by(Event.id).all()]
    assert actions == ["tool_call", "tool_result"]
    assert db_session.query(Event).first().actor == "tool:check_site"


def test_check_site_301_redirect_counts_ok(db_session, fake_http):
    """3xx — доступен по критерию FR-022 (редирект считается штатным)."""
    fake_http["https://misis.ru"] = FakeHttpResponse(301)
    call = tools.call_tool(db_session, "check_site", {})
    assert call.result["ok"] is True
    assert call.result["http_code"] == 301
    assert db_session.query(ServiceCheck).one().ok is True


def test_check_site_500_failure(db_session, fake_http):
    fake_http["https://misis.ru"] = FakeHttpResponse(500)
    call = tools.call_tool(db_session, "check_site", {})
    assert call.result["ok"] is False
    assert call.result["http_code"] == 500
    assert db_session.query(ServiceCheck).one().ok is False
    service = db_session.query(Service).filter_by(name="misis.ru").one()
    assert service.state == "down"


def test_check_site_timeout_failure(db_session, fake_http):
    """Таймаут проверки = сбой; код ответа неизвестен; таймаут инструмента 5 c (FR-022)."""
    req = httpx.Request("GET", "https://misis.ru")
    fake_http["https://misis.ru"] = httpx.TimeoutException("превышено время ожидания", request=req)
    call = tools.call_tool(db_session, "check_site", {})
    assert call.result["ok"] is False
    assert call.result["http_code"] is None
    assert db_session.query(ServiceCheck).one().ok is False
    # инструмент держит таймаут 5 секунд
    assert FakeHttpClient.instances[0]["timeout"] == tools.CHECK_TIMEOUT_SEC


def test_check_lms_targets_newlms(db_session, fake_http):
    fake_http["https://newlms.misis.ru"] = FakeHttpResponse(200)
    call = tools.call_tool(db_session, "check_lms", {})
    assert call.result["ok"] is True
    service = db_session.query(Service).filter_by(name="newlms.misis.ru").one()
    assert service.check_type == "real"
    assert db_session.query(ServiceCheck).count() == 1


def test_check_result_linked_to_subtask(db_session, fake_http):
    _, subtask, ticket = _make_subtask(db_session, service="site")
    fake_http["https://misis.ru"] = FakeHttpResponse(200)
    tools.call_tool(db_session, "check_site", {}, ticket_id=ticket.id, subtask_id=subtask.id)
    check = db_session.query(ServiceCheck).one()
    assert check.subtask_id == subtask.id
    event = db_session.query(Event).filter_by(action="tool_result").one()
    assert event.ticket_id == ticket.id


# ---------------------------------------------------------------------------
# check_wifi: эмулируемое состояние services, без сетевых вызовов
# ---------------------------------------------------------------------------

def test_check_wifi_state_down(db_session, fake_http):
    """Сеть в состоянии down → проверка неуспешна, HTTP не вызывается (FR-024)."""
    db_session.add(Service(name="MISIS-EDU", check_type="emulated", state="down"))
    db_session.commit()
    call = tools.call_tool(db_session, "check_wifi", {"network": "wifi_edu"})
    assert call.result["ok"] is False
    assert call.result["state"] == "down"
    assert call.result["http_code"] is None and call.result["latency_ms"] is None
    check = db_session.query(ServiceCheck).one()
    assert check.ok is False
    assert FakeHttpClient.instances == []  # эмуляция — без реального сетевого вызова


def test_check_wifi_state_up(db_session, fake_http):
    db_session.add(Service(name="MISIS-Guest", check_type="emulated", state="up"))
    db_session.commit()
    call = tools.call_tool(db_session, "check_wifi", {"network": "MISIS-Guest"})
    assert call.result["ok"] is True
    assert db_session.query(ServiceCheck).one().ok is True


def test_check_wifi_auto_creates_missing_network_as_up(db_session, fake_http):
    """Без сидирования эмулируемая сеть создаётся в состоянии up (дефолт доступности)."""
    call = tools.call_tool(db_session, "check_wifi", {"network": "wifi_corp"})
    assert call.result["ok"] is True
    service = db_session.query(Service).filter_by(name="MISIS-CORP").one()
    assert service.check_type == "emulated" and service.state == "up"


def test_check_wifi_does_not_change_emulated_state(db_session, fake_http):
    """Эмулируемое состояние меняется только вручную — проверка его не перезаписывает (FR-024)."""
    db_session.add(Service(name="MISIS-EDU", check_type="emulated", state="down"))
    db_session.commit()
    tools.call_tool(db_session, "check_wifi", {"network": "wifi_edu"})
    assert db_session.query(Service).filter_by(name="MISIS-EDU").one().state == "down"


# ---------------------------------------------------------------------------
# Диспетчер: реестр, неизвестные инструменты, обёртка ошибок исполнения
# ---------------------------------------------------------------------------

def test_registry_contains_exec_checks():
    for name in ("check_site", "check_lms", "check_wifi"):
        assert name in tools.TOOLS
        assert tools.TOOLS[name]["type"] == "exec"
        assert callable(tools.TOOLS[name]["fn"])
        assert tools.TOOLS[name]["schema"]


def test_unknown_tool_raises(db_session):
    with pytest.raises(tools.ToolError):
        tools.call_tool(db_session, "нет_такого_инструмента", {})


def test_dispatcher_wraps_tool_exception_as_failure(db_session, fake_http):
    """Инструмент упал (не сетевая ошибка) — фиксируется как возможный сбой (ок=False),
    исключение не пролетает наружу и не роняет пайплайн."""
    fake_http["https://misis.ru"] = RuntimeError("внутренняя ошибка инструмента")
    call = tools.call_tool(db_session, "check_site", {})
    assert call.result["ok"] is False
    assert "error" in call.result


# ---------------------------------------------------------------------------
# Ветка сбоя ноды execute_route — без обращения к LLM (SC-004)
# ---------------------------------------------------------------------------

def test_outage_branch_reacts_without_llm(db_session, fake_http, monkeypatch):
    """Подтверждённый сбой → outage_notice без единого вызова llm.chat (FR-026, SC-004)."""
    llm_calls: list = []

    def forbidden_chat(*args, **kwargs):
        llm_calls.append(args)
        raise llm.LLMUnavailable("LLM не должен вызываться в ветке сбоя")

    monkeypatch.setattr("app.llm.chat", forbidden_chat)
    monkeypatch.setattr("app.llm.chat_structured", forbidden_chat)

    db_session.add(Service(name="MISIS-EDU", check_type="emulated", state="down"))
    db_session.commit()
    _, subtask, ticket = _make_subtask(db_session, service="wifi_edu", priority="high")

    state = {
        "db": db_session,
        "subtask_ids": [subtask.id],
        "subtask_index": 0,
        "ticket_id": ticket.id,
        "reactions": [],
        "tool_calls": 0,
    }
    updates = agent.node_execute_route(state)

    assert llm_calls == []  # модель не дёргалась
    reaction = updates["reactions"][0]
    assert reaction["kind"] == "outage_notice"
    assert "MISIS-EDU" in reaction["text"]
    assert updates["tool_calls"] == 1
    # приоритет поднят до critical правилом FR-011 (T027), понижение запрещено
    db_session.refresh(subtask)
    assert subtask.priority == "critical"
    assert "подтверждённый сбой" in (subtask.route_reason or "")
    assert subtask.status == tickets.STATUS_RESOLVED
    assert db_session.get(type(ticket), ticket.id).status == tickets.STATUS_RESOLVED
    # результат проверки зафиксирован
    check = db_session.query(ServiceCheck).one()
    assert check.ok is False and check.subtask_id == subtask.id


def test_ok_branch_asks_clarification(db_session, fake_http, monkeypatch):
    """Норма → шаблонный уточняющий вопрос, тикет ждёт ответа пользователя (FR-025)."""
    llm_calls: list = []

    def forbidden_chat(*args, **kwargs):
        llm_calls.append(args)
        raise llm.LLMUnavailable("LLM не должен вызываться в ветке нормы")

    monkeypatch.setattr("app.llm.chat", forbidden_chat)
    monkeypatch.setattr("app.llm.chat_structured", forbidden_chat)

    db_session.add(Service(name="MISIS-Guest", check_type="emulated", state="up"))
    db_session.commit()
    _, subtask, ticket = _make_subtask(db_session, service="wifi_guest", priority="high")

    state = {
        "db": db_session,
        "subtask_ids": [subtask.id],
        "subtask_index": 0,
        "ticket_id": ticket.id,
        "reactions": [],
        "tool_calls": 0,
    }
    updates = agent.node_execute_route(state)

    assert llm_calls == []
    assert updates["reactions"][0]["kind"] == "clarification"
    db_session.refresh(subtask)
    assert subtask.priority == "high"  # без сбоя приоритет не трогаем
    assert db_session.get(type(ticket), ticket.id).status == tickets.STATUS_WAITING_USER


def test_execute_route_escalates_unknown_service(db_session, monkeypatch):
    """auto_check с непроверяемым сервисом — безопасная эскалация (FR-013)."""
    _, subtask, ticket = _make_subtask(db_session, service="account")
    state = {
        "db": db_session,
        "subtask_ids": [subtask.id],
        "subtask_index": 0,
        "ticket_id": ticket.id,
        "reactions": [],
        "tool_calls": 0,
    }
    updates = agent.node_execute_route(state)
    assert updates["reactions"][0]["kind"] == "escalated"
    assert db_session.get(type(ticket), ticket.id).escalated is True


def test_execute_route_respects_tool_calls_limit(db_session, monkeypatch):
    """FR-021: при достижении лимита 3 вызова — эскалация, инструмент не зовётся."""
    _, subtask, ticket = _make_subtask(db_session, service="wifi_guest")
    state = {
        "db": db_session,
        "subtask_ids": [subtask.id],
        "subtask_index": 0,
        "ticket_id": ticket.id,
        "reactions": [],
        "tool_calls": agent.MAX_TOOL_CALLS,
    }
    updates = agent.node_execute_route(state)
    assert updates["reactions"][0]["kind"] == "escalated"
    assert db_session.query(Event).filter_by(action="tool_call").count() == 0


# ---------------------------------------------------------------------------
# T027: правило «подтверждённый сбой → critical» (unit-триггера)
# ---------------------------------------------------------------------------

def test_outage_rule_raises_to_critical():
    decision = triggers.apply_outage("auto_check", "high", service_down=True, service="MISIS-EDU")
    assert decision.priority == "critical"
    assert decision.triggered


def test_outage_rule_never_lowers():
    decision = triggers.apply_outage("auto_check", "critical", service_down=True, service="MISIS-EDU")
    assert decision.priority == "critical"


def test_outage_rule_inactive_when_service_up():
    decision = triggers.apply_outage("auto_check", "medium", service_down=False, service="MISIS-EDU")
    assert decision.priority == "medium"
    assert decision.triggered == []


# ---------------------------------------------------------------------------
# PATCH /api/admin/services/{id} (FR-024) — переключатель сценария 3 quickstart.md
# ---------------------------------------------------------------------------

def _make_operator(db_engine, email: str = "smirnov@misis.ru") -> None:
    """Оператор создаётся напрямую в БД (логин автосоздаёт только student)."""
    from sqlalchemy.orm import sessionmaker

    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    if db.scalar(select(User).where(User.email == email)) is None:  # smirnov уже засидирован
        db.add(User(email=email, full_name="Смирнов Олег", role="operator",
                    password_hash=hash_password("operator123")))
        db.commit()
    db.close()


def _fresh(db_engine):
    """Новая сессия поверх тестового engine — без протухшего кэша ORM."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=db_engine, expire_on_commit=False)()


def _service_id(db_engine, name: str, check_type: str, state: str = "up") -> int:
    """Строка сервиса для админ-тестов; без сидирования создаётся явно."""
    db = _fresh(db_engine)
    service = db.query(Service).filter_by(name=name).one_or_none()
    if service is None:
        service = Service(name=name, check_type=check_type, state=state)
        db.add(service)
        db.commit()
    db.close()
    return service.id


def test_admin_patch_emulated_then_complaint_outage(client, monkeypatch, db_engine):
    """Сценарий 3: оператор переводит MISIS-EDU в down → жалоба → outage_notice."""
    _make_operator(db_engine)
    login = client.post("/api/auth/login", json={"email": "smirnov@misis.ru", "password": "operator123"})
    assert login.status_code == 200

    edu_id = _service_id(db_engine, "MISIS-EDU", "emulated")
    patch = client.patch(f"/api/admin/services/{edu_id}", json={"state": "down"})
    assert patch.status_code == 200, patch.text
    body = patch.json()
    assert body["service"]["name"] == "MISIS-EDU"
    assert body["service"]["state"] == "down"

    db = _fresh(db_engine)
    assert db.query(Service).filter_by(name="MISIS-EDU").one().state == "down"
    # переключение записано в журнал оператором
    event = db.query(Event).filter_by(action="service_state_change").one()
    assert event.actor == "operator"
    db.close()

    calls = make_counting_fake_llm(
        monkeypatch,
        classify={
            "route": "auto_check", "service": "wifi_edu", "category": "availability",
            "priority": "high", "confidence": 0.88, "reason": "Жалоба на доступность сети",
        },
    )
    resp = client.post(
        "/api/requests", json={"text": "Не работает Wi-Fi MISIS-EDU", "channel": "web"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reactions"][0]["kind"] == "outage_notice"
    assert "Wi-Fi MISIS-EDU" in data["reactions"][0]["text"]
    assert data["subtasks"][0]["priority"] == "critical"
    assert data["ticket"]["status"] == "решена"
    assert calls["count"] == 2  # split + classify; ветка сбоя LLM не звала


def test_admin_patch_real_service_conflict(client, db_engine):
    """FR-024: real-сервисы руками не переключаются → 409."""
    _make_operator(db_engine)
    client.post("/api/auth/login", json={"email": "smirnov@misis.ru", "password": "operator123"})
    site_id = _service_id(db_engine, "misis.ru", "real")
    resp = client.patch(f"/api/admin/services/{site_id}", json={"state": "down"})
    assert resp.status_code == 409
    db = _fresh(db_engine)
    assert db.query(Service).filter_by(name="misis.ru").one().state == "up"  # не тронуто
    db.close()


def test_admin_patch_forbidden_for_non_operator(client, db_engine):
    """Анониму — 401, студенту (не оператору) — 403; состояние не меняется."""
    edu_id = _service_id(db_engine, "MISIS-EDU", "emulated")

    anon = client.patch(f"/api/admin/services/{edu_id}", json={"state": "down"})
    assert anon.status_code == 401

    client.post("/api/auth/login", json={"email": "ivanov@misis.ru", "password": "student123"})
    forbidden = client.patch(f"/api/admin/services/{edu_id}", json={"state": "down"})
    assert forbidden.status_code == 403

    db = _fresh(db_engine)
    assert db.query(Service).filter_by(name="MISIS-EDU").one().state == "up"
    db.close()


def test_admin_patch_unknown_service_404(client, db_engine):
    _make_operator(db_engine)
    client.post("/api/auth/login", json={"email": "smirnov@misis.ru", "password": "operator123"})
    resp = client.patch("/api/admin/services/9999", json={"state": "down"})
    assert resp.status_code == 404
