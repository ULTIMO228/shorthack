"""Журнал действий (FR-061): единый helper записи событий по заявке."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session as OrmSession

from app.models import Event


def log_event(
    db: OrmSession,
    *,
    ticket_id: int | None = None,
    actor: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> Event:
    """Записать событие в журнал. actor: user|agent|tool:<name>|operator|system; payload — dict (сериализуется в JSON)."""
    event = Event(
        ticket_id=ticket_id,
        actor=actor,
        action=action,
        payload=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
    )
    db.add(event)
    db.commit()
    return event
