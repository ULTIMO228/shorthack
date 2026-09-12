"""Тесты детектора массовых сбоев и управления инцидентами (US6, T039–T041).

Кейсы:
1. Создание инцидента на 3-го distinct-автора в окне (FR-050, SC-005).
2. Игнорирование повторных жалоб одного автора.
3. Учёт скользящего окна 15 минут.
4. Настраиваемые порог и окно.
5. Пропуск неклассифицированных подзадач.
6. Привязка новых обращений без повторного уведомления (FR-052).
7. Поднятие приоритета до high (FR-011) + маркер (FR-012).
8. Запрет понижения приоритета (critical остаётся critical).
9. Ветка инцидента в пайплайне не вызывает LLM и auto_check.
10. Сценарий 7 quickstart: волна обращений через POST /api/requests.
11. Идемпотентность evaluate для одного тикета.
12. Новый инцидент после resolve предыдущего.
13. Инструмент simulate_wave (exec, T040): count=3 -> инцидент + outbound.
14. simulate_wave с count=2 -> без инцидента.
15. simulate_wave с неизвестным сервисом -> ToolError.
16. GET /api/admin/incidents: active первыми, 401 гостю, 403 студенту.
17. POST /api/admin/incidents/{id}/broadcast: шаблон и кастомный текст.
18. POST /api/admin/incidents/{id}/broadcast по resolved -> 409.
19. POST /api/admin/incidents/{id}/resolve: 200 и повторный 409.
20. 404 на несуществующих инцидентах.
21. Внутренние хуки /api/internal/outbound (poll, ack, 401 без токена).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession, sessionmaker

from app import incidents, tickets, tools
from app.models import Event, Incident, OutboundMessage, Request, Subtask, Ticket, User


@pytest.fixture()
def db_session(db_engine):
    """Сессия на тестовой БД."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def seed_services(db_engine):
    """5 сервисов из seed.SERVICES."""
    from app.models import Service
    from app.seed import SERVICES

    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    if db.scalar(select(Service).limit(1)) is None:
        db.add_all(Service(name=n, check_type=t, state=s) for n, t, s in SERVICES)
        db.commit()
    db.close()


def _make_operator(db_session: OrmSession, email: str = "smirnov@misis.ru") -> User:
    op = db_session.scalar(select(User).where(User.email == email))
    if op is None:
        op = User(email=email, full_name="Алексей Смирнов", role="operator")
        db_session.add(op)
        db_session.commit()
    return op


def _login(client, email: str):
    return client.post("/api/auth/login", json={"email": email})


def _complaint(
    db: OrmSession,
    *,
    service: str = "wifi_edu",
    category: str = "availability",
    minutes_ago: int = 0,
    user_id: int | None = None,
    session_id: str = "s-1",
    summary: str | None = None,
    priority: str = "medium",
) -> tuple[Request, Subtask, Ticket]:
    created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    req = Request(
        session_id=session_id,
        user_id=user_id,
        channel="web",
        raw_text=summary or f"Жалоба на {service}",
        masked_text=summary or f"Жалоба на {service}",
        lang="ru",
        created_at=created_at,
    )
    db.add(req)
    db.flush()
    subtask = tickets.attach_subtask(
        db,
        req,
        summary or f"Недоступен {service}",
        service=service,
        category=category,
        route="auto_check",
        priority=priority,
        confidence=0.9,
    )
    ticket = tickets.ensure_ticket(db, req.id)
    req.created_at = created_at
    db.commit()
    return req, subtask, ticket


def make_counting_fake_llm(monkeypatch, *, classify: dict) -> dict:
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
# 1. Создание инцидента на 3-го distinct-автора
# ---------------------------------------------------------------------------

def test_detector_creates_incident_on_third_distinct_author(db_session):
    _make_operator(db_session)
    _, sub1, t1 = _complaint(db_session, session_id="author-1")
    r1 = incidents.evaluate(db_session, subtask=sub1, ticket=t1)
    assert r1 is None

    _, sub2, t2 = _complaint(db_session, session_id="author-2")
    r2 = incidents.evaluate(db_session, subtask=sub2, ticket=t2)
    assert r2 is None

    _, sub3, t3 = _complaint(db_session, session_id="author-3")

    r3 = incidents.evaluate(db_session, subtask=sub3, ticket=t3)
    assert r3 is not None
    assert r3["kind"] == "outage_notice"
    assert t3.incident_id is not None

    inc = db_session.get(Incident, t3.incident_id)
    assert inc is not None
    assert inc.status == "active"
    assert inc.notified_at is not None
    assert inc.request_count == 3

    outbounds = db_session.scalars(select(OutboundMessage)).all()
    assert len(outbounds) == 1
    assert outbounds[0].status == "pending"
    assert "Инцидент" in outbounds[0].text and "MISIS-EDU" in outbounds[0].text
    assert sub3.status == tickets.STATUS_RESOLVED


# ---------------------------------------------------------------------------
# 2. Игнорирование повторных жалоб одного автора
# ---------------------------------------------------------------------------

def test_detector_ignores_same_author_repeated(db_session):
    _, sub1, t1 = _complaint(db_session, session_id="same-author")
    _, sub2, t2 = _complaint(db_session, session_id="same-author")
    _, sub3, t3 = _complaint(db_session, session_id="same-author")

    assert incidents.evaluate(db_session, subtask=sub1, ticket=t1) is None
    assert incidents.evaluate(db_session, subtask=sub2, ticket=t2) is None
    assert incidents.evaluate(db_session, subtask=sub3, ticket=t3) is None

    assert db_session.scalar(select(func.count(Incident.id))) == 0
    assert db_session.scalar(select(func.count(OutboundMessage.id))) == 0


# ---------------------------------------------------------------------------
# 3. Скользящее окно 15 минут
# ---------------------------------------------------------------------------

def test_detector_respects_sliding_window(db_session):
    req1, sub1, t1 = _complaint(db_session, session_id="author-1", minutes_ago=20)
    req2, sub2, t2 = _complaint(db_session, session_id="author-2", minutes_ago=16)
    _, sub3, t3 = _complaint(db_session, session_id="author-3", minutes_ago=1)

    assert incidents.evaluate(db_session, subtask=sub3, ticket=t3) is None
    assert db_session.scalar(select(func.count(Incident.id))) == 0

    # Сдвигаем первые две жалобы внутрь 15-минутного окна
    req1.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    req2.created_at = datetime.now(timezone.utc) - timedelta(minutes=3)
    db_session.commit()

    r3 = incidents.evaluate(db_session, subtask=sub3, ticket=t3)
    assert r3 is not None
    assert r3["kind"] == "outage_notice"
    assert db_session.scalar(select(func.count(Incident.id))) == 1


# ---------------------------------------------------------------------------
# 4. Настраиваемые threshold и window
# ---------------------------------------------------------------------------

def test_detector_threshold_and_window_configurable(db_session):
    _, sub1, t1 = _complaint(db_session, session_id="author-1", minutes_ago=30)
    _, sub2, t2 = _complaint(db_session, session_id="author-2", minutes_ago=1)

    # threshold=2 с окном 60 мин захватывает обе жалобы
    r2 = incidents.evaluate(db_session, subtask=sub2, ticket=t2, window_minutes=60, threshold=2)
    assert r2 is not None
    assert db_session.scalar(select(func.count(Incident.id))) == 1


# ---------------------------------------------------------------------------
# 5. Пропуск неклассифицированных подзадач
# ---------------------------------------------------------------------------

def test_detector_skips_unclassified_subtask(db_session):
    _, sub, t = _complaint(db_session, session_id="author-1")
    sub.service = None
    db_session.commit()
    assert incidents.evaluate(db_session, subtask=sub, ticket=t) is None

    sub.service = "wifi_edu"
    sub.category = ""
    db_session.commit()
    assert incidents.evaluate(db_session, subtask=sub, ticket=t) is None


# ---------------------------------------------------------------------------
# 6. Привязка без повторного уведомления
# ---------------------------------------------------------------------------

def test_active_incident_attaches_without_second_notification(db_session):
    tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})
    outbound_count = db_session.scalar(select(func.count(OutboundMessage.id)))
    assert outbound_count == 1
    inc = db_session.scalar(select(Incident).where(Incident.status == "active"))
    assert inc.request_count == 3

    _, sub4, t4 = _complaint(db_session, session_id="author-4")
    r4 = incidents.evaluate(db_session, subtask=sub4, ticket=t4)
    assert r4 is not None
    assert r4["kind"] == "outage_notice"
    assert t4.incident_id == inc.id
    assert inc.request_count == 4
    # Outbound сообщение не добавлялось
    assert db_session.scalar(select(func.count(OutboundMessage.id))) == outbound_count
    assert "Wi-Fi MISIS-EDU" in r4["text"]
    assert str(inc.id) in r4["text"]


# ---------------------------------------------------------------------------
# 7. Поднятие приоритета до high
# ---------------------------------------------------------------------------

def test_active_incident_raises_priority_floor_high(db_session):
    tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})
    inc = db_session.scalar(select(Incident).where(Incident.status == "active"))

    _, sub, t = _complaint(db_session, session_id="author-4", priority="medium")
    incidents.evaluate(db_session, subtask=sub, ticket=t)
    assert sub.priority == "high"
    assert f"(триггеры: активный инцидент №{inc.id})" in (sub.route_reason or "")


# ---------------------------------------------------------------------------
# 8. Запрет понижения приоритета (critical сохраняется)
# ---------------------------------------------------------------------------

def test_active_incident_never_lowers_priority(db_session):
    tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})

    _, sub, t = _complaint(db_session, session_id="author-4", priority="critical")
    incidents.evaluate(db_session, subtask=sub, ticket=t)
    assert sub.priority == "critical"


# ---------------------------------------------------------------------------
# 9. Ветка инцидента не вызывает LLM и auto_check
# ---------------------------------------------------------------------------

def test_incident_branch_never_calls_llm(client, monkeypatch, db_session):
    tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})

    calls = make_counting_fake_llm(
        monkeypatch,
        classify={
            "route": "auto_check",
            "service": "wifi_edu",
            "category": "availability",
            "priority": "medium",
            "confidence": 0.95,
            "reason": "Wi-Fi лежит",
        },
    )

    resp = client.post(
        "/api/requests",
        json={"text": "Не работает Wi-Fi MISIS-EDU на 4 этаже", "channel": "web"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reactions"][0]["kind"] == "outage_notice"
    # Ровно 2 вызова LLM (split + classify)
    assert calls["count"] == 2
    # Никаких вызовов инструментов auto_check в событиях по этой заявке
    req_id = data["request_id"]
    tool_events = db_session.scalars(
        select(Event).where(Event.ticket_id == data["ticket"]["id"], Event.action == "tool_call")
    ).all()
    assert len(tool_events) == 0


# ---------------------------------------------------------------------------
# 10. Quickstart сценарий 7: волна обращений через POST /api/requests
# ---------------------------------------------------------------------------

def test_pipeline_creates_incident_on_third_wave_user(client, monkeypatch, db_session):
    _make_operator(db_session)
    make_counting_fake_llm(
        monkeypatch,
        classify={
            "route": "auto_check",
            "service": "wifi_edu",
            "category": "availability",
            "priority": "medium",
            "confidence": 0.9,
            "reason": "Жалоба",
        },
    )

    # 2 жалобы напрямую
    _complaint(db_session, session_id="guest-1")
    _complaint(db_session, session_id="guest-2")

    # 3-я через API
    resp3 = client.post("/api/requests", json={"text": "Wi-Fi упал", "channel": "web"})
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert data3["reactions"][0]["kind"] == "outage_notice"

    inc = db_session.scalar(select(Incident).where(Incident.status == "active"))
    assert inc is not None
    assert inc.request_count == 3
    outbound_count = db_session.scalar(select(func.count(OutboundMessage.id)))
    assert outbound_count == 1

    # 4-я жалоба от другого клиента
    resp4 = client.post("/api/requests", json={"text": "Тоже нет сети", "channel": "web"})
    assert resp4.status_code == 200
    assert resp4.json()["reactions"][0]["kind"] == "outage_notice"
    # Уведомление не дублируется
    assert db_session.scalar(select(func.count(OutboundMessage.id))) == outbound_count


# ---------------------------------------------------------------------------
# 11. Идемпотентность повторного evaluate для одного тикета
# ---------------------------------------------------------------------------

def test_repeated_evaluate_same_ticket_no_double_count(db_session):
    tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})
    inc = db_session.scalar(select(Incident).where(Incident.status == "active"))

    _, sub, t = _complaint(db_session, session_id="author-4")
    incidents.evaluate(db_session, subtask=sub, ticket=t)
    assert inc.request_count == 4

    # Повторный вызов
    incidents.evaluate(db_session, subtask=sub, ticket=t)
    assert inc.request_count == 4


# ---------------------------------------------------------------------------
# 12. Resolve -> новая волна создаёт новый инцидент
# ---------------------------------------------------------------------------

def test_resolve_then_new_wave_creates_new_incident(db_session):
    call1 = tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})
    inc1_id = call1.result["incident_id"]
    inc1 = db_session.get(Incident, inc1_id)
    incidents.resolve(db_session, incident=inc1)
    assert inc1.status == "resolved"

    # Создаем новую волну
    call2 = tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})
    inc2_id = call2.result["incident_id"]
    assert inc2_id != inc1_id
    inc2 = db_session.get(Incident, inc2_id)
    assert inc2.status == "active"
    assert inc1.status == "resolved"


# ---------------------------------------------------------------------------
# 13. simulate_wave создаёт инцидент и outbound
# ---------------------------------------------------------------------------

def test_simulate_wave_creates_incident_and_outbound(db_session):
    _make_operator(db_session)
    call = tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})
    res = call.result
    assert res["ok"] is True
    assert res["incident_id"] is not None
    assert len(res["created_requests"]) == 3

    outbounds = db_session.scalars(select(OutboundMessage)).all()
    assert len(outbounds) >= 1

    events = db_session.scalars(select(Event).where(Event.actor == "system", Event.action == "simulate_wave")).all()
    assert len(events) == 1


# ---------------------------------------------------------------------------
# 14. simulate_wave с count=2 без инцидента
# ---------------------------------------------------------------------------

def test_simulate_wave_count_two_no_incident(db_session):
    call = tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 2})
    assert call.result["ok"] is True
    assert call.result["incident_id"] is None
    assert len(call.result["created_requests"]) == 2
    assert db_session.scalar(select(func.count(Incident.id))) == 0


# ---------------------------------------------------------------------------
# 15. simulate_wave с неизвестным сервисом бросает ToolError
# ---------------------------------------------------------------------------

def test_simulate_wave_unknown_service_raises(db_session):
    with pytest.raises(tools.ToolError):
        tools.call_tool(db_session, "simulate_wave", {"service": "unknown"})


# ---------------------------------------------------------------------------
# 16. GET /api/admin/incidents: active первыми, 401 гостю, 403 студенту
# ---------------------------------------------------------------------------

def test_admin_incidents_list_active_first(client, db_session):
    _make_operator(db_session)
    # Гость
    assert client.get("/api/admin/incidents").status_code == 401

    # Студент
    _login(client, "student1@edu.misis.ru")
    assert client.get("/api/admin/incidents").status_code == 403

    # Создаём два инцидента: resolved (id=1) и active (id=2)
    inc1 = Incident(service="site", category="availability", status="resolved", request_count=3, window_start=datetime.now(timezone.utc))
    inc2 = Incident(service="wifi_edu", category="availability", status="active", request_count=3, window_start=datetime.now(timezone.utc))
    db_session.add_all([inc1, inc2])
    db_session.commit()

    # Оператор
    _login(client, "smirnov@misis.ru")
    resp = client.get("/api/admin/incidents")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    assert items[0]["id"] == inc2.id
    assert items[0]["status"] == "active"
    assert items[1]["id"] == inc1.id
    assert items[1]["status"] == "resolved"


# ---------------------------------------------------------------------------
# 17. POST /api/admin/incidents/{id}/broadcast: шаблон и кастомный текст
# ---------------------------------------------------------------------------

def test_admin_broadcast_template_and_custom_text(client, db_session):
    _make_operator(db_session)
    call = tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})
    inc_id = call.result["incident_id"]

    _login(client, "smirnov@misis.ru")
    # Шаблонный текст (text=None)
    resp = client.post(f"/api/admin/incidents/{inc_id}/broadcast", json={"text": None})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] == 3
    assert data["broadcast_at"] is not None

    messages = db_session.scalars(select(OutboundMessage)).all()
    # 1 уведомление дежурному + 3 сообщения авторам
    assert len(messages) == 4
    assert any("Wi-Fi MISIS-EDU" in m.text for m in messages[1:])

    # Кастомный текст
    resp2 = client.post(f"/api/admin/incidents/{inc_id}/broadcast", json={"text": "Работы завершаются"})
    assert resp2.status_code == 200
    assert resp2.json()["sent"] == 3
    messages_after = db_session.scalars(select(OutboundMessage)).all()
    assert len(messages_after) == 7
    assert any("Работы завершаются" in m.text for m in messages_after[4:])


# ---------------------------------------------------------------------------
# 18. POST /api/admin/incidents/{id}/broadcast по resolved -> 409
# ---------------------------------------------------------------------------

def test_admin_broadcast_resolved_incident_409(client, db_session):
    _make_operator(db_session)
    call = tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})
    inc_id = call.result["incident_id"]
    inc = db_session.get(Incident, inc_id)
    incidents.resolve(db_session, incident=inc)

    _login(client, "smirnov@misis.ru")
    resp = client.post(f"/api/admin/incidents/{inc_id}/broadcast", json={"text": "Привет"})
    assert resp.status_code == 409
    assert "активному инциденту" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 19. POST /api/admin/incidents/{id}/resolve
# ---------------------------------------------------------------------------

def test_admin_resolve_happy_and_409(client, db_session):
    _make_operator(db_session)
    call = tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})
    inc_id = call.result["incident_id"]

    _login(client, "smirnov@misis.ru")
    resp = client.post(f"/api/admin/incidents/{inc_id}/resolve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"

    inc = db_session.get(Incident, inc_id)
    assert inc.status == "resolved"

    # Повторный resolve -> 409
    resp2 = client.post(f"/api/admin/incidents/{inc_id}/resolve")
    assert resp2.status_code == 409
    assert "уже закрыт" in resp2.json()["detail"]


# ---------------------------------------------------------------------------
# 20. 404 на несуществующих инцидентах
# ---------------------------------------------------------------------------

def test_admin_incident_endpoints_404(client, db_session):
    _make_operator(db_session)
    _login(client, "smirnov@misis.ru")

    assert client.post("/api/admin/incidents/99999/broadcast", json={}).status_code == 404
    assert client.post("/api/admin/incidents/99999/resolve").status_code == 404


# ---------------------------------------------------------------------------
# 21. Внутренние хуки /api/internal/outbound (poll, ack, 401)
# ---------------------------------------------------------------------------

def test_internal_outbound_poll_and_ack(client, db_session, bot_token):
    _make_operator(db_session)
    tools.call_tool(db_session, "simulate_wave", {"service": "wifi_edu", "count": 3})

    # Без заголовка бота -> 401
    assert client.get("/api/internal/outbound").status_code == 401

    # С заголовком -> 200
    resp = client.get("/api/internal/outbound?status=pending", headers={"X-Bot-Token": bot_token})
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    msg_id = items[0]["id"]
    assert "🔴 Инцидент" in items[0]["text"]

    # Ack
    ack_resp = client.post(
        f"/api/internal/outbound/{msg_id}/ack",
        json={"status": "sent"},
        headers={"X-Bot-Token": bot_token},
    )
    assert ack_resp.status_code == 200

    # Проверяем, что сообщение ушло из pending
    resp_after = client.get("/api/internal/outbound?status=pending", headers={"X-Bot-Token": bot_token})
    pending_ids = [m["id"] for m in resp_after.json()]
    assert msg_id not in pending_ids
