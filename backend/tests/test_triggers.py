"""Детерминированные триггеры (FR-011/FR-013) и жизненный цикл заявки (FR-015)."""

from __future__ import annotations

import pytest

from app import tickets, triggers
from app.models import Event, Request, Ticket


# ---------------------------------------------------------------------------
# Триггеры: форс-эскалация, повышение приоритета, запрет понижения
# ---------------------------------------------------------------------------

def test_complaint_trigger_forces_escalation():
    decision = triggers.apply(
        "Хочу пожаловаться на сотрудника деканата, он грубил мне",
        route="kb", priority="medium", confidence=0.9,
    )
    assert decision.route == "escalate"
    assert decision.triggered


def test_legal_trigger_forces_escalation():
    decision = triggers.apply(
        "Мне нужна консультация юриста по договору найма общежития",
        route="kb", priority="low", confidence=0.88,
    )
    assert decision.route == "escalate"


def test_priority_raise_by_rule():
    decision = triggers.apply(
        "Не работает Wi-Fi в четвёртом корпусе",
        route="auto_check", priority="low", confidence=0.9,
    )
    assert decision.priority == "high"


def test_rules_never_lower_priority():
    """Даже если срабатывает правило с более низким приоритетом — понижение запрещено (FR-011)."""
    decision = triggers.apply(
        "Критический сбой: не работает почта, срочно",
        route="escalate", priority="critical", confidence=0.97,
    )
    assert decision.priority == "critical"


def test_confidence_below_threshold_escalates():
    decision = triggers.apply("Что-то с чем-то", route="kb", priority="medium", confidence=0.59)
    assert decision.route == "escalate"
    assert decision.triggered


def test_confident_route_passes_through():
    decision = triggers.apply("Как подключиться к eduroam?", route="kb", priority="medium", confidence=0.93)
    assert decision.route == "kb"
    assert decision.priority == "medium"
    assert decision.triggered == []


def test_raise_priority_helper():
    assert triggers.raise_priority("low", "high") == "high"
    assert triggers.raise_priority("high", "low") == "high"
    assert triggers.raise_priority("critical", "high") == "critical"
    assert triggers.raise_priority("medium", "medium") == "medium"


# ---------------------------------------------------------------------------
# Жизненный цикл заявки (FR-015) и дедупликация (FR-014)
# ---------------------------------------------------------------------------

def _make_request(db_session, session_id: str, text: str = "текст") -> Request:
    req = Request(session_id=session_id, channel="web", raw_text=text, masked_text=text)
    db_session.add(req)
    db_session.commit()
    return req


@pytest.fixture()
def db_session(db_engine):
    from sqlalchemy.orm import sessionmaker

    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()


def test_ticket_lifecycle_happy_path(db_session):
    req = _make_request(db_session, "s-lifecycle")
    ticket = tickets.ensure_ticket(db_session, req.id)
    assert ticket.status == tickets.STATUS_NEW

    tickets.set_status(db_session, ticket, tickets.STATUS_IN_PROGRESS)
    tickets.set_status(db_session, ticket, tickets.STATUS_WAITING_USER)
    tickets.set_status(db_session, ticket, tickets.STATUS_IN_PROGRESS)
    tickets.set_status(db_session, ticket, tickets.STATUS_RESOLVED)
    tickets.set_status(db_session, ticket, tickets.STATUS_CLOSED)
    assert ticket.status == tickets.STATUS_CLOSED


def test_ticket_invalid_transition_raises(db_session):
    req = _make_request(db_session, "s-invalid")
    ticket = tickets.ensure_ticket(db_session, req.id)
    with pytest.raises(ValueError):
        tickets.set_status(db_session, ticket, tickets.STATUS_CLOSED)  # новая → закрыта напрямую
    with pytest.raises(ValueError):
        tickets.set_status(db_session, ticket, tickets.STATUS_WAITING_USER)  # новая → ждёт ответа в обход цепочки


def test_status_change_logged(db_session):
    req = _make_request(db_session, "s-logged")
    ticket = tickets.ensure_ticket(db_session, req.id)
    tickets.set_status(db_session, ticket, tickets.STATUS_IN_PROGRESS)
    events = db_session.query(Event).filter_by(ticket_id=ticket.id, action="status_change").all()
    assert len(events) == 1


def test_ticket_number_format(db_session):
    req = _make_request(db_session, "s-number")
    ticket = tickets.ensure_ticket(db_session, req.id)
    assert tickets.ticket_number(ticket) == f"SUP-2026-{ticket.id:04d}"


def test_dedup_open_ticket_by_session(db_session):
    """У гостя дедупликация идёт по session_id в пределах гостевой сессии (FR-016)."""
    req1 = _make_request(db_session, "s-dedup", "не работает вайфай")
    t1 = tickets.ensure_ticket(db_session, req1.id)
    sub1 = tickets.attach_subtask(db_session, req1, "не работает вайфай", service="wifi_guest",
                                  category="availability")
    found = tickets.find_open_duplicate(db_session, user_id=None, session_id="s-dedup",
                                        service="wifi_guest", category="availability",
                                        exclude_request_id=None)
    assert found is not None and found.id == t1.id

    # другая гостевая сессия — дубля нет
    other = tickets.find_open_duplicate(db_session, user_id=None, session_id="s-other",
                                        service="wifi_guest", category="availability",
                                        exclude_request_id=None)
    assert other is None

    # свежие подзадачи текущего обращения исключаем — иначе дедуп на самого себя
    self_hit = tickets.find_open_duplicate(db_session, user_id=None, session_id="s-dedup",
                                           service="wifi_guest", category="availability",
                                           exclude_request_id=req1.id)
    assert self_hit is None


def test_dedup_ignores_resolved(db_session):
    req1 = _make_request(db_session, "s-resolved", "не работает вайфай")
    t1 = tickets.ensure_ticket(db_session, req1.id)
    tickets.attach_subtask(db_session, req1, "не работает вайфай", service="wifi_edu",
                           category="availability")
    tickets.set_status(db_session, t1, tickets.STATUS_RESOLVED)
    found = tickets.find_open_duplicate(db_session, user_id=None, session_id="s-resolved",
                                        service="wifi_edu", category="availability",
                                        exclude_request_id=None)
    assert found is None
