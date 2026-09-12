"""Метрики качества работы помощника (FR-062): чтение из журнала events и агрегатов.

auto_closed_pct — доля заявок, решённых без человека (escalated=false и статус
«решена»/«закрыта») среди ВСЕХ заявок. avg_first_reaction_sec — средний лаг
tickets.created_at → первая запись events.action='reaction_sent' (минимум по
тикету; тикеты без реакции в среднее не входят). Пустые выборки → 0.0, не ошибка.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app import tickets
from app.models import Event, Incident, Request, Ticket
from app.schemas import MetricsOut

FINAL_STATUSES = (tickets.STATUS_RESOLVED, tickets.STATUS_CLOSED)


def _parse_dt(val: Any) -> datetime | None:
    """Нормализация даты: строка или datetime -> datetime с timezone.utc."""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            val = datetime.fromisoformat(val)
        except (ValueError, TypeError):
            return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val
    return None


def auto_closed_pct(db: OrmSession) -> float:
    """Доля заявок, решённых без человека (% среди всех тикетов)."""
    total = db.scalar(select(func.count(Ticket.id))) or 0
    if total == 0:
        return 0.0
    closed = (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.escalated.is_(False),
                Ticket.status.in_(FINAL_STATUSES),
            )
        )
        or 0
    )
    return round(100.0 * closed / total, 1)


def avg_first_reaction_sec(db: OrmSession) -> float:
    """Средний лаг tickets.created_at -> первая реакция events.action='reaction_sent'."""
    rows = db.execute(
        select(Ticket.id, Ticket.created_at, func.min(Event.created_at))
        .join(Event, Event.ticket_id == Ticket.id)
        .where(Event.action == "reaction_sent")
        .group_by(Ticket.id)
    ).all()

    lags: list[float] = []
    for _id, created, first in rows:
        c_dt = _parse_dt(created)
        f_dt = _parse_dt(first)
        if c_dt is not None and f_dt is not None:
            lags.append(max(0.0, (f_dt - c_dt).total_seconds()))

    return round(statistics.mean(lags), 1) if lags else 0.0


def incidents_total(db: OrmSession) -> int:
    """Общее число зарегистрированных инцидентов."""
    return db.scalar(select(func.count(Incident.id))) or 0


def incidents_active(db: OrmSession) -> int:
    """Число активных инцидентов."""
    return (
        db.scalar(
            select(func.count(Incident.id)).where(Incident.status == "active")
        )
        or 0
    )


def requests_total(db: OrmSession) -> int:
    """Общее число обращений пользователей."""
    return db.scalar(select(func.count(Request.id))) or 0


def escalations_open(db: OrmSession) -> int:
    """Число открытых эскалаций (escalated=true и статус не финальный)."""
    return (
        db.scalar(
            select(func.count(Ticket.id)).where(
                Ticket.escalated.is_(True),
                Ticket.status.not_in(FINAL_STATUSES),
            )
        )
        or 0
    )


def collect_metrics(db: OrmSession) -> MetricsOut:
    """Сбор всех 6 метрик качества (FR-062)."""
    return MetricsOut(
        auto_closed_pct=auto_closed_pct(db),
        avg_first_reaction_sec=avg_first_reaction_sec(db),
        incidents_total=incidents_total(db),
        incidents_active=incidents_active(db),
        requests_total=requests_total(db),
        escalations_open=escalations_open(db),
    )
