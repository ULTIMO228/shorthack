"""Тесты метрик качества работы ассистента (FR-062, T044).

Проверяет:
- auto_closed_pct (% решённых без человека),
- avg_first_reaction_sec (вычитание min(reaction_sent) - created_at в Python),
- incidents_total / incidents_active,
- requests_total / escalations_open,
- доступ только для роли operator (401 гостю, 403 студенту).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import Session as OrmSession, sessionmaker

from app import tickets
from app.models import Event, Incident, Request, Ticket, User


@pytest.fixture()
def db_session(db_engine):
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()


def _make_ticket(
    db: OrmSession,
    *,
    status: str = tickets.STATUS_NEW,
    escalated: bool = False,
    lag_sec: float | None = None,
) -> Ticket:
    """Создание тестового Request -> Ticket с опциональным событием reaction_sent."""
    req = Request(
        session_id="test-session",
        user_id=None,
        channel="web",
        raw_text="Тестовый вопрос",
        masked_text="Тестовый вопрос",
        lang="ru",
    )
    db.add(req)
    db.flush()

    ticket = tickets.ensure_ticket(db, req.id)
    ticket.status = status
    ticket.escalated = escalated
    db.commit()

    if lag_sec is not None:
        first_reaction = ticket.created_at + timedelta(seconds=lag_sec)
        ev = Event(
            ticket_id=ticket.id,
            actor="agent",
            action="reaction_sent",
            payload='{"kind": "answer", "text": "ответ"}',
            created_at=first_reaction,
        )
        db.add(ev)
        db.commit()

    return ticket


def test_metrics_empty_db_all_zeros(operator_client):
    """Пустая БД -> auto_closed_pct=0.0, avg=0.0, все count=0 (нет деления на ноль)."""
    resp = operator_client.get("/api/admin/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["auto_closed_pct"] == 0.0
    assert data["avg_first_reaction_sec"] == 0.0
    assert data["incidents_total"] == 0
    assert data["incidents_active"] == 0
    assert data["requests_total"] == 0
    assert data["escalations_open"] == 0


def test_auto_closed_pct_counts_resolved_non_escalated_only(db_session, operator_client):
    """4 тикета: 2 resolved не-escalated, 1 escalated open, 1 escalated resolved -> 50.0%."""
    _make_ticket(db_session, status=tickets.STATUS_RESOLVED, escalated=False)
    _make_ticket(db_session, status=tickets.STATUS_RESOLVED, escalated=False)
    _make_ticket(db_session, status=tickets.STATUS_IN_PROGRESS, escalated=True)
    _make_ticket(db_session, status=tickets.STATUS_RESOLVED, escalated=True)

    resp = operator_client.get("/api/admin/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["auto_closed_pct"] == 50.0


def test_auto_closed_pct_includes_closed_status(db_session, operator_client):
    """Тикет «закрыта» не-escalated входит в числитель auto_closed_pct."""
    _make_ticket(db_session, status=tickets.STATUS_CLOSED, escalated=False)
    _make_ticket(db_session, status=tickets.STATUS_IN_PROGRESS, escalated=True)

    resp = operator_client.get("/api/admin/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["auto_closed_pct"] == 50.0


def test_avg_first_reaction_uses_first_reaction_event(db_session, operator_client):
    """Лаг считается до ПЕРВОГО reaction_sent (min). 2 тикета с лагами 2.0 и 4.0 -> avg 3.0."""
    t1 = _make_ticket(db_session, status=tickets.STATUS_RESOLVED, escalated=False, lag_sec=2.0)
    # Добавим для t1 второе событие реакции с большим лагом (10 сек)
    later_ev = Event(
        ticket_id=t1.id,
        actor="agent",
        action="reaction_sent",
        payload='{"kind": "clarification", "text": "повторная реакция"}',
        created_at=t1.created_at + timedelta(seconds=10.0),
    )
    db_session.add(later_ev)
    db_session.commit()

    _make_ticket(db_session, status=tickets.STATUS_RESOLVED, escalated=False, lag_sec=4.0)

    resp = operator_client.get("/api/admin/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["avg_first_reaction_sec"] == 3.0


def test_avg_first_reaction_skips_tickets_without_reaction(db_session, operator_client):
    """Тикет без reaction_sent не участвует в среднем и не ломает расчёт."""
    _make_ticket(db_session, status=tickets.STATUS_IN_PROGRESS, escalated=False, lag_sec=None)
    _make_ticket(db_session, status=tickets.STATUS_RESOLVED, escalated=False, lag_sec=5.0)

    resp = operator_client.get("/api/admin/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["avg_first_reaction_sec"] == 5.0


def test_escalations_open_excludes_final_statuses(db_session, operator_client):
    """escalated со статусами «решена» и «закрыта» не считаются open."""
    _make_ticket(db_session, status=tickets.STATUS_IN_PROGRESS, escalated=True)
    _make_ticket(db_session, status=tickets.STATUS_WAITING_USER, escalated=True)
    _make_ticket(db_session, status=tickets.STATUS_RESOLVED, escalated=True)
    _make_ticket(db_session, status=tickets.STATUS_CLOSED, escalated=True)

    resp = operator_client.get("/api/admin/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["escalations_open"] == 2


def test_incidents_total_and_active(db_session, operator_client):
    """2 инцидента (active + resolved) -> total=2, active=1."""
    now = datetime.now(timezone.utc)
    inc1 = Incident(
        service="wifi_edu",
        category="availability",
        status="active",
        request_count=3,
        window_start=now,
        notified_at=now,
    )
    inc2 = Incident(
        service="lms",
        category="availability",
        status="resolved",
        request_count=4,
        window_start=now,
        notified_at=now,
    )
    db_session.add_all([inc1, inc2])
    db_session.commit()

    resp = operator_client.get("/api/admin/metrics")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["incidents_total"] == 2
    assert data["incidents_active"] == 1


def test_metrics_requires_operator(client, db_session):
    """Гость -> 401, студент -> 403."""
    # 1. Гость
    assert client.get("/api/admin/metrics").status_code == 401

    # 2. Студент
    student = User(email="petrova@edu.misis.ru", full_name="Петрова Анна", role="student")
    db_session.add(student)
    db_session.commit()

    login_resp = client.post("/api/auth/login", json={"email": "petrova@edu.misis.ru"})
    assert login_resp.status_code == 200, login_resp.text

    assert client.get("/api/admin/metrics").status_code == 403
