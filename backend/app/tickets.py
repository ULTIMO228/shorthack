"""Заявка: жизненный цикл по FR-015, дедупликация по FR-014, номер SUP-2026-<id>.

Статусы (data-model.md): новая → в работе → ждёт ответа пользователя → решена → закрыта.
Эскалация — признак (tickets.escalated), а не статус (Q2). Переходы валидируются:
недопустимый переход → ValueError (роутер превращает в 409).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.events import log_event
from app.models import Request, Subtask, Ticket

STATUS_NEW = "новая"
STATUS_IN_PROGRESS = "в работе"
STATUS_WAITING_USER = "ждёт ответа пользователя"
STATUS_RESOLVED = "решена"
STATUS_CLOSED = "закрыта"

# Допустимые переходы (FR-015): любой незакрытый статус → «решена»; «закрыта» — только из «решена»
TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_NEW: frozenset({STATUS_IN_PROGRESS, STATUS_RESOLVED}),
    STATUS_IN_PROGRESS: frozenset({STATUS_WAITING_USER, STATUS_RESOLVED}),
    STATUS_WAITING_USER: frozenset({STATUS_IN_PROGRESS, STATUS_RESOLVED}),
    STATUS_RESOLVED: frozenset({STATUS_CLOSED}),
    STATUS_CLOSED: frozenset(),
}

# Дедупликация и детектор инцидентов считают заявку открытой, пока она не решена/не закрыта
OPEN_STATUSES = (STATUS_NEW, STATUS_IN_PROGRESS, STATUS_WAITING_USER)


def ticket_number(ticket: Ticket) -> str:
    """Номер для показа: SUP-2026-<id:04d> (генерируется из id, не хранится)."""
    return f"SUP-2026-{ticket.id:04d}"


def ensure_ticket(db: OrmSession, request_id: int) -> Ticket:
    """Заявка по обращению: существующая или новая со статусом «новая»."""
    ticket = db.scalar(select(Ticket).where(Ticket.request_id == request_id))
    if ticket is not None:
        return ticket
    ticket = Ticket(request_id=request_id, status=STATUS_NEW, dialog_rounds=0)
    db.add(ticket)
    db.commit()
    return ticket


def set_status(db: OrmSession, ticket: Ticket, new_status: str, *, actor: str = "agent") -> None:
    """Перевод заявки в новый статус с проверкой цепочки FR-015; переход пишется в журнал."""
    if new_status == ticket.status:
        return
    allowed = TRANSITIONS.get(ticket.status, frozenset())
    if new_status not in allowed:
        raise ValueError(
            f"Переход «{ticket.status}» → «{new_status}» невозможен"
        )
    old_status = ticket.status
    ticket.status = new_status
    db.commit()
    log_event(
        db, ticket_id=ticket.id, actor=actor, action="status_change",
        payload={"from": old_status, "to": new_status},
    )


def attach_subtask(
    db: OrmSession, request: Request, summary: str, **fields,
) -> Subtask:
    """Создать подзадачу обращения с автоматической позицией (helper для пайплайна и тестов)."""
    position = db.scalar(
        select(Subtask.position)
        .where(Subtask.request_id == request.id)
        .order_by(Subtask.position.desc())
        .limit(1)
    )
    subtask = Subtask(
        request_id=request.id,
        position=(position or 0) + 1,
        summary=summary,
        **fields,
    )
    db.add(subtask)
    db.commit()
    return subtask


def find_open_duplicate(
    db: OrmSession,
    *,
    user_id: int | None,
    session_id: str,
    service: str | None,
    category: str | None,
    exclude_request_id: int | None,
) -> Ticket | None:
    """Открытая заявка с тем же (service, category) у этого автора (FR-014).

    Авторизованный — по user_id; гость — по session_id в пределах гостевой сессии
    (FR-016). exclude_request_id отсекает подзадачи текущего обращения, иначе
    дедупликация находила бы саму себя.
    """
    if not service or not category:
        return None
    stmt = (
        select(Ticket)
        .join(Request, Ticket.request_id == Request.id)
        .join(Subtask, Subtask.request_id == Request.id)
        .where(
            Subtask.service == service,
            Subtask.category == category,
            Ticket.status.in_(OPEN_STATUSES),
        )
    )
    if user_id is not None:
        stmt = stmt.where(Request.user_id == user_id)
    else:
        stmt = stmt.where(Request.user_id.is_(None), Request.session_id == session_id)
    if exclude_request_id is not None:
        stmt = stmt.where(Request.id != exclude_request_id)
    return db.scalars(stmt.limit(1)).first()
