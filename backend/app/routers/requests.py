"""Эндпоинты обращений (contracts/api.md): подача, диалог, история, детали."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app import agent, tickets
from app.auth import get_current_session
from app.db import get_session
from app.events import log_event
from app.models import Event, Request, Session as UserSession
from app.models import Ticket
from app.schemas import (
    DialogMessage,
    EventOut,
    MyRequestItem,
    MySubtaskBrief,
    MyTicketBrief,
    ReplyRequest,
    ReplyResponse,
    RequestCreate,
    RequestCreatedResponse,
    RequestDetail,
    SubtaskOut,
    TicketOut,
)

router = APIRouter(tags=["requests"])

DIALOG_ROUNDS_LIMIT = 2  # FR-025: не более 2 раундов уточняющего диалога


def _ticket_out(ticket: Ticket | None) -> TicketOut:
    return TicketOut(
        id=ticket.id,
        number=tickets.ticket_number(ticket),
        status=ticket.status,
        escalated=bool(ticket.escalated),
    )


def _build_created_response(result: dict) -> RequestCreatedResponse:
    return RequestCreatedResponse(
        request_id=result["request_id"],
        duplicate=result["duplicate"],
        subtasks=[SubtaskOut.model_validate(s) for s in result["subtasks"]],
        reactions=result["reactions"],
        ticket=_ticket_out(result["ticket"]),
    )


def _is_author(request: Request, session: UserSession) -> bool:
    if request.user_id is not None and request.user_id == session.user_id:
        return True
    return request.session_id == session.id


@router.post("/api/requests", response_model=RequestCreatedResponse)
def create_request(
    body: RequestCreate,
    session: Annotated[UserSession, Depends(get_current_session)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> RequestCreatedResponse:
    """Подача обращения; доступно гостю (гостевая сессия создаётся автоматически, FR-016)."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Текст обращения пуст")
    result = agent.run_pipeline(db, session, body.channel, text)
    return _build_created_response(result)


@router.post("/api/requests/{request_id}/reply", response_model=ReplyResponse)
def reply_to_request(
    request_id: int,
    body: ReplyRequest,
    session: Annotated[UserSession, Depends(get_current_session)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> ReplyResponse:
    """Ответ на уточняющий вопрос; доступно автору (пользователю или гостевой сессии)."""
    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Текст ответа пуст")
    request = db.get(Request, request_id)
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Обращение не найдено")
    if not _is_author(request, session):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Это чужое обращение")
    ticket = tickets.ensure_ticket(db, request_id)
    if ticket.dialog_rounds >= DIALOG_ROUNDS_LIMIT or ticket.status not in tickets.OPEN_STATUSES:
        # FR-025/api.md: лимит раундов исчерпан или заявка закрыта — диалог завершён.
        # Guard до set_status: «решена» достижима при dialog_rounds < 2 (auth_required),
        # и переход set_status тогда бросал бы ValueError → 500 вместо 409.
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Диалог по обращению завершён")
    log_event(db, ticket_id=ticket.id, actor="user", action="dialog",
              payload={"role": "user", "text": text})
    ticket.dialog_rounds += 1
    db.commit()
    if ticket.dialog_rounds >= DIALOG_ROUNDS_LIMIT:
        # FR-025: раунды уточнений кончились — эскалация оператору с историей
        # переписки (она собирается в events по ticket), а не третий вопрос.
        ticket.escalated = True
        db.commit()
        text_out = agent.render_template(
            db, agent.TEMPLATE_ESCALATION, ticket=tickets.ticket_number(ticket)
        )
        reaction = {"kind": "escalated", "text": text_out}
        tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS, actor="user")
        log_event(db, ticket_id=ticket.id, actor="agent", action="escalated",
                  payload={"reason": "dialog_rounds_limit"})
        log_event(db, ticket_id=ticket.id, actor="agent",
                  action="reaction_sent", payload=reaction)
        return ReplyResponse(reactions=[reaction], ticket=_ticket_out(ticket))
    tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS, actor="user")
    result = agent.run_pipeline(db, session, request.channel, text, request=request)
    return ReplyResponse(
        reactions=result["reactions"],
        ticket=_ticket_out(result["ticket"]),
    )


@router.get("/api/requests", response_model=list[MyRequestItem])
def my_requests(
    session: Annotated[UserSession, Depends(get_current_session)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> list[MyRequestItem]:
    """Мои обращения; только авторизованный (история — персональная возможность, FR-016)."""
    if session.user_id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="История обращений доступна после входа по почте МИСИС",
        )
    rows = db.scalars(
        select(Request)
        .where(Request.user_id == session.user_id)
        .order_by(Request.created_at.desc())
    ).all()
    items = []
    for request in rows:
        ticket = request.tickets[0] if request.tickets else None
        if ticket is None and request.duplicate_of_ticket_id is not None:
            ticket = db.get(Ticket, request.duplicate_of_ticket_id)
        if ticket is None:
            continue
        items.append(
            MyRequestItem(
                id=request.id,
                created_at=request.created_at,
                channel=request.channel,
                masked_text=request.masked_text,
                ticket=MyTicketBrief(number=tickets.ticket_number(ticket), status=ticket.status),
                subtasks=[
                    MySubtaskBrief(service=s.service, category=s.category or "", priority=s.priority or "")
                    for s in request.subtasks
                ],
            )
        )
    return items


def _build_request_detail(db: OrmSession, request: Request) -> RequestDetail:
    """Сборка детального представления обращения (FR-060, FR-061)."""
    own_ticket = request.tickets[0] if request.tickets else None
    linked_ticket = (
        db.get(Ticket, request.duplicate_of_ticket_id)
        if request.duplicate_of_ticket_id is not None else None
    )
    ticket_ids = [t.id for t in (own_ticket, linked_ticket) if t is not None]
    events = db.scalars(
        select(Event).where(Event.ticket_id.in_(ticket_ids)).order_by(Event.id)
    ).all() if ticket_ids else []

    dialog: list[DialogMessage] = []
    for event in events:
        payload = json.loads(event.payload or "{}")
        if event.action == "dialog" and payload.get("role") in ("user", "agent"):
            dialog.append(
                DialogMessage(role=payload["role"], text=payload["text"], at=event.created_at)
            )
        elif event.action == "reaction_sent" and payload.get("text"):
            # реакции агента — его реплики в диалоге
            dialog.append(DialogMessage(role="agent", text=payload["text"], at=event.created_at))
    return RequestDetail(
        id=request.id,
        raw_text=request.raw_text,
        masked_text=request.masked_text,
        lang=request.lang,
        translation=request.translation,
        subtasks=[SubtaskOut.model_validate(s) for s in request.subtasks],
        dialog=dialog,
        ticket=_ticket_out(own_ticket or linked_ticket),
        events=[
            EventOut(
                actor=e.actor or "",
                action=e.action or "",
                payload=json.loads(e.payload) if e.payload else None,
                created_at=e.created_at,
            )
            for e in events
        ],
    )


@router.get("/api/requests/{request_id}", response_model=RequestDetail)
def request_detail(
    request_id: int,
    session: Annotated[UserSession, Depends(get_current_session)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> RequestDetail:
    """Детали обращения: автор (пользователь/гостевая сессия) или оператор."""
    request = db.get(Request, request_id)
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Обращение не найдено")
    user = session.user
    is_operator = user is not None and user.role == "operator"
    if not is_operator and not _is_author(request, session):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Это чужое обращение")
    return _build_request_detail(db, request)
