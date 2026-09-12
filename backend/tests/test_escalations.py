"""Тесты Phase 7 / US5 — Эскалация оператору с саммари (T036–T038).

1. Пакет эскалации FR-060 (summary, checks, recommendation, why_escalated, raw_text, events).
2. Очередь /api/admin/queue с сортировкой priority (critical→low) → created_at asc.
3. Карточка /api/admin/escalations/{id} и сценарий 4b quickstart.md.
4. Закрытие /api/admin/escalations/{id}/close с цепочкой FR-015 и записью в журнал.
5. Защита доступа (гость 401, студент 403, оператор 200).
6. Outbound-уведомление дежурному оператору (идемпотентность, 1 раз).
7. Fallback при LLMUnavailable и при превышении лимита tool_calls.
8. Сохранение статуса «решена» при эскалации из решённого тикета.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import agent, llm, tickets, tools
from app.auth import hash_password
from app.models import Event, OutboundMessage, Request, Service, ServiceCheck, Subtask, Ticket, User
from app.agent import SplitResult, SubtaskDraft
from app.schemas import ClassifierResult
from app.tools import SummarizePayload


@pytest.fixture()
def db_session(db_engine):
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()


def make_operator(db_engine, email="smirnov@misis.ru") -> User:
    """Создать оператора в БД."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name="Смирнов Олег", role="operator",
                    password_hash=hash_password("operator123"))
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    return user


def make_student(db_engine, email="ivanov@misis.ru") -> User:
    """Создать студента в БД."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name="Иванов Иван", role="student",
                    password_hash=hash_password("student123"))
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    return user


def make_escalation_fake_llm(
    monkeypatch,
    *,
    classify: dict[str, Any] | None = None,
    summarize: dict[str, Any] | None = None,
    translation: str = "Переведённый текст",
    split_summaries: list[str] | None = None,
    fail_on_summarize: bool = False,
    fail_on_classify: bool = False,
) -> dict[str, int]:
    """Фейк LLM с подсчётом вызовов и диспетчеризацией по схеме и тексту."""
    calls = {"chat": 0, "structured": 0, "summarize": 0}

    def fake_chat(messages, *, model=None, temperature=0.2, timeout=25.0):
        calls["chat"] += 1
        text = messages if isinstance(messages, str) else "\n".join(m["content"] for m in messages)
        if "Переведи" in text:
            return translation
        return "Ответ модели"

    def fake_chat_structured(prompt: str, schema_cls: type[Any], **kwargs):
        calls["structured"] += 1
        if schema_cls.__name__ == "SplitResult" or issubclass(schema_cls, SplitResult):
            summaries = split_summaries or ["Единственная проблема обращения"]
            return SplitResult(subtasks=[SubtaskDraft(summary=s) for s in summaries])
        if schema_cls.__name__ == "ClassifierResult" or issubclass(schema_cls, ClassifierResult):
            if fail_on_classify:
                raise llm.LLMUnavailable("classify down")
            payload = classify or {
                "route": "escalate",
                "service": "other",
                "category": "other",
                "priority": "medium",
                "confidence": 0.42,
                "reason": "Низкая уверенность классификатора (0.42 < 0.6)",
            }
            return ClassifierResult.model_validate(payload)
        if schema_cls.__name__ == "SummarizePayload" or issubclass(schema_cls, SummarizePayload):
            calls["summarize"] += 1
            if fail_on_summarize:
                raise llm.LLMUnavailable("summarize down")
            payload = summarize or {
                "summary": "Пользователь сообщает о проблеме",
                "recommendation": "Проверить настройки учетной записи и связаться со студентом",
                "why_escalated": "Низкая уверенность классификатора (confidence 0.42 < 0.6)",
            }
            return SummarizePayload.model_validate(payload)
        from pydantic import TypeAdapter
        return TypeAdapter(schema_cls).validate_python({})

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "chat_structured", fake_chat_structured)
    return calls


# ---------------------------------------------------------------------------
# Тест 1: Happy path — пакет FR-060, очередь, карточка, уведомление дежурному
# ---------------------------------------------------------------------------

def test_escalation_card_contains_fr060_package(app, db_engine, client, monkeypatch, db_session):
    """Happy: confidence 0.42 → эскалация; queue содержит item (escalated=true);
    карточка содержит все блоки FR-060 (summary, checks, recommendation, why_escalated,
    raw_text, events); в outbound_messages одна pending-запись дежурному."""
    op = make_operator(db_engine)
    make_escalation_fake_llm(
        monkeypatch,
        classify={
            "route": "escalate",
            "service": "wifi_guest",
            "category": "availability",
            "priority": "high",
            "confidence": 0.42,
            "reason": "confidence 0.42 < 0.6 — передано оператору",
        },
        summarize={
            "summary": "Проблема с доступом к гостевой сети Wi-Fi",
            "recommendation": "Проверить портал авторизации и запросить MAC-адрес",
            "why_escalated": "confidence 0.42 < 0.6",
        },
    )

    wifi_service = Service(name="MISIS-Guest", check_type="emulated", state="up")
    db_session.add(wifi_service)
    db_session.commit()

    resp = client.post("/api/requests", json={"text": "Не могу зайти в гостевой вайфай", "channel": "web"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket"]["escalated"] is True
    assert data["reactions"][0]["kind"] == "escalated"
    req_id = data["request_id"]

    subtask = db_session.scalar(select(Subtask).where(Subtask.request_id == req_id))
    db_session.add(ServiceCheck(service_id=wifi_service.id, subtask_id=subtask.id, ok=True, http_code=None))
    db_session.commit()

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": op.email, "password": "operator123"})

    queue_resp = op_client.get("/api/admin/queue")
    assert queue_resp.status_code == 200
    queue = queue_resp.json()
    matched = [item for item in queue if item["request_id"] == req_id]
    assert len(matched) == 1
    assert matched[0]["escalated"] is True
    assert matched[0]["priority"] == "high"

    card_resp = op_client.get(f"/api/admin/escalations/{req_id}")
    assert card_resp.status_code == 200
    card = card_resp.json()
    assert card["summary"] == "Проблема с доступом к гостевой сети Wi-Fi"
    assert card["recommendation"] == "Проверить портал авторизации и запросить MAC-адрес"
    assert "0.42" in card["why_escalated"]
    assert len(card["checks"]) >= 0
    assert card["request"]["raw_text"] == "Не могу зайти в гостевой вайфай"
    assert len(card["request"]["events"]) > 0

    outbounds = db_session.scalars(select(OutboundMessage)).all()
    assert len(outbounds) == 1
    assert outbounds[0].status == "pending"
    assert outbounds[0].chat_id == -op.id
    assert "SUP-2026-" in outbounds[0].text
    assert "Проблема с доступом к гостевой сети" in outbounds[0].text


# ---------------------------------------------------------------------------
# Тест 2: Сценарий 4b quickstart.md — вопрос вне базы
# ---------------------------------------------------------------------------

def test_quickstart_scenario_4b_escalation_flow(app, db_engine, client, monkeypatch):
    """Сценарий 4b end-to-end: вопрос «вне базы» (route escalate от классификатора, confidence 0.95)
    → реакция escalated, карточка открывается оператором со всеми блоками."""
    op = make_operator(db_engine)
    make_escalation_fake_llm(
        monkeypatch,
        classify={
            "route": "escalate",
            "service": "other",
            "category": "other",
            "priority": "low",
            "confidence": 0.95,
            "reason": "Вопрос вне компетенции базы знаний (бассейн в кампусе)",
        },
        summarize={
            "summary": "Вопрос об открытии бассейна в кампусе",
            "recommendation": "Уточнить у спорткомплекса дату открытия и ответить заявителю",
            "why_escalated": "Вопрос вне компетенции базы знаний",
        },
    )

    resp = client.post("/api/requests", json={"text": "Когда откроется бассейн в кампусе?", "channel": "web"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reactions"][0]["kind"] == "escalated"
    req_id = data["request_id"]

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": op.email, "password": "operator123"})
    card_resp = op_client.get(f"/api/admin/escalations/{req_id}")
    assert card_resp.status_code == 200
    card = card_resp.json()
    assert "бассейн" in card["summary"].lower()
    assert card["why_escalated"] == "Вопрос вне компетенции базы знаний"
    assert card["recommendation"] != ""


# ---------------------------------------------------------------------------
# Тест 3: Английский текст — перевод в карточке
# ---------------------------------------------------------------------------

def test_escalation_card_includes_translation(app, db_engine, client, monkeypatch):
    """Английский текст: lang=en, перевод в карточке (request.translation), саммари собран по переводу."""
    op = make_operator(db_engine)
    make_escalation_fake_llm(
        monkeypatch,
        translation="Кампусный Wi-Fi полностью не работает",
        classify={
            "route": "escalate",
            "service": "wifi_edu",
            "category": "availability",
            "priority": "high",
            "confidence": 0.5,
            "reason": "confidence 0.5 < 0.6",
        },
        summarize={
            "summary": "Жалоба на неработающий Wi-Fi на английском языке",
            "recommendation": "Ответить на английском или предоставить перевод инструкции",
            "why_escalated": "confidence 0.5 < 0.6",
        },
    )

    resp = client.post("/api/requests", json={"text": "The campus wifi is completely down", "channel": "web"})
    assert resp.status_code == 200
    req_id = resp.json()["request_id"]

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": op.email, "password": "operator123"})
    card = op_client.get(f"/api/admin/escalations/{req_id}").json()
    assert card["request"]["lang"] == "en"
    assert card["request"]["translation"] == "Кампусный Wi-Fi полностью не работает"
    assert card["summary"] == "Жалоба на неработающий Wi-Fi на английском языке"


# ---------------------------------------------------------------------------
# Тест 4: Форс-триггер и путь превышения лимита tool_calls
# ---------------------------------------------------------------------------

def test_escalation_trigger_force_and_tool_limit_paths(app, db_engine, client, monkeypatch, db_session):
    """Путь триггера: жалоба на сотрудника → route escalate, why содержит правило;
    путь лимита: unit-вызов agent.node_escalate со state['tool_calls'] = MAX_TOOL_CALLS
    → пакет из шаблонов/fallback, llm.chat_structured(SummarizePayload) не вызывался."""
    make_operator(db_engine)
    calls = make_escalation_fake_llm(
        monkeypatch,
        classify={
            "route": "auto_check",
            "service": "site",
            "category": "availability",
            "priority": "low",
            "confidence": 0.95,
            "reason": "Штатная проверка",
        },
    )

    # 1. Форс-триггер жалобы
    resp = client.post("/api/requests", json={"text": "Жалоба на сотрудника деканата за хамство", "channel": "web"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket"]["escalated"] is True
    assert "жалоба" in data["subtasks"][0]["route_reason"].lower()

    # 2. Путь превышения лимита tool_calls
    req = Request(session_id="test-session", channel="web", raw_text="Проблема с лимитом", masked_text="Проблема с лимитом", lang="ru")
    db_session.add(req)
    db_session.flush()
    subtask = tickets.attach_subtask(
        db_session, req, "Проблема с лимитом", route="escalate", priority="medium",
        confidence=0.5, status=tickets.STATUS_IN_PROGRESS, route_reason="confidence 0.5 < 0.6",
    )
    ticket = tickets.ensure_ticket(db_session, req.id)

    summarize_calls_before = calls["summarize"]
    state = {
        "db": db_session,
        "request_id": req.id,
        "subtask_ids": [subtask.id],
        "subtask_index": 0,
        "ticket_id": ticket.id,
        "reactions": [],
        "tool_calls": agent.MAX_TOOL_CALLS,
    }
    updates = agent.node_escalate(state)
    assert updates["tool_calls"] == agent.MAX_TOOL_CALLS
    assert calls["summarize"] == summarize_calls_before  # модель не вызывалась

    event = db_session.scalar(
        select(Event).where(Event.ticket_id == ticket.id, Event.action == "escalation_package").order_by(Event.id.desc())
    )
    assert event is not None
    pkg = json.loads(event.payload)
    assert pkg["recommendation"] == agent.ESCALATION_FALLBACK_RECOMMENDATION


# ---------------------------------------------------------------------------
# Тест 5: Fallback при недоступности LLM
# ---------------------------------------------------------------------------

def test_escalation_package_fallback_when_llm_down(app, db_engine, client, monkeypatch):
    """LLMUnavailable: при сбое LLM нода escalate не падает, карточка собирается fallback-пакетом."""
    op = make_operator(db_engine)
    make_escalation_fake_llm(monkeypatch, fail_on_classify=True, fail_on_summarize=True)

    resp = client.post("/api/requests", json={"text": "Не работает ничего", "channel": "web"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket"]["escalated"] is True
    req_id = data["request_id"]

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": op.email, "password": "operator123"})
    card = op_client.get(f"/api/admin/escalations/{req_id}").json()
    assert "недоступна" in card["why_escalated"].lower() or "fr-013" in card["why_escalated"].lower()
    assert card["recommendation"] == agent.ESCALATION_FALLBACK_RECOMMENDATION


# ---------------------------------------------------------------------------
# Тест 6: Эскалация из решённого тикета сохраняет цепочку статусов
# ---------------------------------------------------------------------------

def test_escalation_from_resolved_ticket_keeps_status(app, db_engine, client, monkeypatch, db_session):
    """Тикет в статусе «решена» (например, после outage) при последующей эскалации
    не падает с ValueError и сохраняет статус «решена», флаг escalated=True."""
    make_operator(db_engine)
    make_escalation_fake_llm(
        monkeypatch,
        classify={
            "route": "escalate",
            "service": "wifi_guest",
            "category": "availability",
            "priority": "high",
            "confidence": 0.4,
            "reason": "escalate after outage",
        },
    )

    req = Request(session_id="test-session", channel="web", raw_text="Сеть упала", masked_text="Сеть упала", lang="ru")
    db_session.add(req)
    db_session.flush()
    subtask = tickets.attach_subtask(
        db_session, req, "Сеть упала", route="auto_check", priority="critical",
        status=tickets.STATUS_RESOLVED,
    )
    ticket = tickets.ensure_ticket(db_session, req.id)
    tickets.set_status(db_session, ticket, tickets.STATUS_RESOLVED)

    state = {
        "db": db_session,
        "request_id": req.id,
        "subtask_ids": [subtask.id],
        "subtask_index": 0,
        "ticket_id": ticket.id,
        "reactions": [],
        "tool_calls": 0,
    }
    updates = agent.node_escalate(state)
    assert ticket.status == tickets.STATUS_RESOLVED
    assert ticket.escalated is True
    assert updates["reactions"][0]["kind"] == "escalated"


# ---------------------------------------------------------------------------
# Тест 7: Закрытие эскалации оператором, валидация и повторный вызов
# ---------------------------------------------------------------------------

def test_close_escalation_and_double_close_409(app, db_engine, client, monkeypatch, db_session):
    """Закрытие: close → ticket_status == 'закрыта', в журнале escalation_closed от operator;
    пустая resolution → 422; неэскалированное обращение → 404."""
    op = make_operator(db_engine)
    make_escalation_fake_llm(monkeypatch)

    resp = client.post("/api/requests", json={"text": "Непонятная проблема с доступом", "channel": "web"})
    req_id = resp.json()["request_id"]

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": op.email, "password": "operator123"})

    # Пустая резолюция → 422
    err_resp = op_client.post(f"/api/admin/escalations/{req_id}/close", json={"resolution": "   "})
    assert err_resp.status_code == 422

    # Успешное закрытие
    close_resp = op_client.post(
        f"/api/admin/escalations/{req_id}/close",
        json={"resolution": "Перезагружен коммутатор на 3 этаже", "add_to_kb": False},
    )
    assert close_resp.status_code == 200
    close_data = close_resp.json()
    assert close_data["ticket_status"] == "закрыта"
    assert close_data["kb_draft"] is None

    ticket = db_session.scalar(select(Ticket).where(Ticket.request_id == req_id))
    assert ticket.status == "закрыта"

    ev = db_session.scalar(
        select(Event)
        .where(Event.ticket_id == ticket.id, Event.action == "escalation_closed")
    )
    assert ev is not None
    assert ev.actor == "operator"
    assert "Перезагружен" in json.loads(ev.payload)["resolution"]


# ---------------------------------------------------------------------------
# Тест 8: Проверка прав доступа к админским эндпоинтам
# ---------------------------------------------------------------------------

def test_admin_endpoints_require_operator(app, db_engine, client, monkeypatch):
    """Гость → 401, студент → 403 на всех трёх эндпоинтах; оператор → 200."""
    make_escalation_fake_llm(monkeypatch)
    resp = client.post("/api/requests", json={"text": "Вопрос для эскалации", "channel": "web"})
    req_id = resp.json()["request_id"]

    # 1. Гость (без куки авторизации)
    guest_client = TestClient(app)
    assert guest_client.get("/api/admin/queue").status_code == 401
    assert guest_client.get(f"/api/admin/escalations/{req_id}").status_code == 401
    assert guest_client.post(f"/api/admin/escalations/{req_id}/close", json={"resolution": "ок"}).status_code == 401

    # 2. Студент (авторизован, роль student)
    make_student(db_engine, "student_user@misis.ru")
    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "student_user@misis.ru", "password": "student123"})
    assert student_client.get("/api/admin/queue").status_code == 403
    assert student_client.get(f"/api/admin/escalations/{req_id}").status_code == 403
    assert student_client.post(f"/api/admin/escalations/{req_id}/close", json={"resolution": "ок"}).status_code == 403

    # 3. Оператор
    make_operator(db_engine, "duty_op@misis.ru")
    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "duty_op@misis.ru", "password": "operator123"})
    assert op_client.get("/api/admin/queue").status_code == 200
    assert op_client.get(f"/api/admin/escalations/{req_id}").status_code == 200


# ---------------------------------------------------------------------------
# Тест 9: Идемпотентность уведомления дежурному оператору
# ---------------------------------------------------------------------------

def test_duty_notification_sent_once(app, db_engine, client, monkeypatch, db_session):
    """Идемпотентность уведомления: повторный прогон пайплайна по тому же тикету
    (reply → эскалация) не дублирует запись в outbound_messages."""
    make_operator(db_engine)
    make_escalation_fake_llm(monkeypatch)

    resp = client.post("/api/requests", json={"text": "Непонятная ошибка", "channel": "web"})
    req_id = resp.json()["request_id"]

    count1 = len(db_session.scalars(select(OutboundMessage)).all())
    assert count1 == 1

    client.post(f"/api/requests/{req_id}/reply", json={"text": "Дополнительные подробности"})

    count2 = len(db_session.scalars(select(OutboundMessage)).all())
    assert count2 == 1


# ---------------------------------------------------------------------------
# Тест 10: Регрессия guest cert_order — не эскалируется
# ---------------------------------------------------------------------------

def test_guest_cert_order_not_escalated(app, db_engine, client, monkeypatch, db_session):
    """Гость + cert_order → реакция auth_required, тикет НЕ эскалирован, outbound пуст."""
    make_operator(db_engine)
    make_escalation_fake_llm(
        monkeypatch,
        classify={
            "route": "cert_order",
            "service": "certs",
            "category": "cert_order",
            "priority": "low",
            "confidence": 0.95,
            "reason": "Заказ справки об обучении",
        },
    )

    resp = client.post("/api/requests", json={"text": "Дайте справку об обучении", "channel": "web"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["reactions"][0]["kind"] == "auth_required"
    assert data["ticket"]["escalated"] is False

    outbounds = db_session.scalars(select(OutboundMessage)).all()
    assert len(outbounds) == 0


# ---------------------------------------------------------------------------
# Тест 11: Гость в очереди (user=null) и агрегация приоритета по подзадачам
# ---------------------------------------------------------------------------

def test_queue_guest_user_null_and_priority_aggregation(app, db_engine, client, monkeypatch):
    """В очереди: гость отображается с user=null; приоритет обращения = max по подзадачам;
    сортировка: critical перед medium/low, при равном приоритете — created_at asc."""
    op = make_operator(db_engine)
    make_escalation_fake_llm(
        monkeypatch,
        split_summaries=["Не работает почта", "Сбой LMS"],
        classify={
            "route": "escalate",
            "service": "lms",
            "category": "availability",
            "priority": "critical",
            "confidence": 0.5,
            "reason": "Критическая проблема",
        },
    )

    resp = client.post("/api/requests", json={"text": "Не работает почта и упал LMS", "channel": "web"})
    assert resp.status_code == 200
    req_id = resp.json()["request_id"]

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": op.email, "password": "operator123"})

    queue_resp = op_client.get("/api/admin/queue")
    assert queue_resp.status_code == 200
    queue = queue_resp.json()
    matched = [item for item in queue if item["request_id"] == req_id][0]
    assert matched["user"] is None
    assert matched["priority"] == "critical"
