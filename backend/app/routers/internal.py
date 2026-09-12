"""Внутренние хуки для Telegram-бота (003): outbound-очередь и привязка чатов (contracts/api.md).

Авторизация — заголовок X-Bot-Token (env BOT_INTERNAL_TOKEN). Бот забирает
исходящие поллингом (GET /outbound?status=pending) и подтверждает отправку (ack).
"""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.auth import full_name_from_email, is_misis_email
from app.db import get_session
from app.models import OutboundMessage, TgLink, User
from app.schemas import (
    OutboundAckRequest,
    OutboundMessageOut,
    TgLinkConfirmRequest,
    TgLinkConfirmResponse,
    TgLinkStateResponse,
    TgLinkUpsertRequest,
    TgLinkUpsertResponse,
    UserOut,
)

router = APIRouter(prefix="/api/internal", tags=["internal"])

MAX_CONFIRM_ATTEMPTS = 3


def _check_bot_token(request: Request) -> None:
    """Сравнение X-Bot-Token с BOT_INTERNAL_TOKEN; без токена env хуки отключены (401)."""
    expected = os.environ.get("BOT_INTERNAL_TOKEN", "")
    provided = request.headers.get("X-Bot-Token", "")
    if not expected or provided != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Неверный внутренний токен бота")


BotAuth = Depends(_check_bot_token)


def _get_or_create_link(db: OrmSession, chat_id: int) -> TgLink:
    link = db.scalar(select(TgLink).where(TgLink.chat_id == chat_id))
    if link is None:
        link = TgLink(chat_id=chat_id, state="awaiting_email")
        db.add(link)
        db.commit()
    return link


@router.get("/outbound", response_model=list[OutboundMessageOut], dependencies=[BotAuth])
def outbound(
    db: Annotated[OrmSession, Depends(get_session)],
    status_filter: str = Query("pending", alias="status"),
) -> list[OutboundMessage]:
    """Сообщения для отправки ботом (по умолчанию pending), по возрастанию id."""
    if status_filter not in ("pending", "sent", "failed"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status должен быть pending, sent или failed",
        )
    return list(
        db.scalars(
            select(OutboundMessage)
            .where(OutboundMessage.status == status_filter)
            .order_by(OutboundMessage.id)
        ).all()
    )


@router.post("/outbound/{msg_id}/ack", dependencies=[BotAuth])
def outbound_ack(
    msg_id: int,
    body: OutboundAckRequest,
    db: Annotated[OrmSession, Depends(get_session)],
) -> dict:
    """Подтверждение отправки/ошибки доставки; сообщение переводится в sent|failed."""
    message = db.get(OutboundMessage, msg_id)
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Сообщение не найдено")
    message.status = body.status
    db.commit()
    return {"id": message.id, "status": message.status}


@router.get("/tg/link", response_model=TgLinkStateResponse, dependencies=[BotAuth])
def tg_link_state(
    db: Annotated[OrmSession, Depends(get_session)],
    chat_id: int = Query(...),
) -> TgLinkStateResponse:
    """Состояние привязки чата; неизвестному чату создаётся запись awaiting_email."""
    link = _get_or_create_link(db, chat_id)
    return TgLinkStateResponse(chat_id=link.chat_id, user_id=link.user_id, state=link.state)


@router.post("/tg/link", response_model=TgLinkUpsertResponse, dependencies=[BotAuth])
def tg_link_start(
    body: TgLinkUpsertRequest,
    db: Annotated[OrmSession, Depends(get_session)],
) -> TgLinkUpsertResponse:
    """Старт привязки: генерация 6-значного кода (эмуляция письма на почту).

    Сценарий S1 (003/quickstart): новый пользователь привязывается через бота
    БЕЗ предварительного веб-входа — неизвестная почта автоматически создаёт
    пользователя (как логин бота), 404 здесь не возвращается (FR-016)."""
    email = body.email.strip().lower()
    if not is_misis_email(email):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Нужна корпоративная почта МИСИС (@misis.ru или @edu.misis.ru)",
        )
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name=full_name_from_email(email), role="student")
        db.add(user)
        db.flush()
    link = _get_or_create_link(db, body.chat_id)
    link.user_id = user.id  # целевой пользователь; подтверждение — кодом (state=idle)
    link.state = "awaiting_confirm"
    link.confirm_code = f"{secrets.randbelow(1_000_000):06d}"
    link.confirm_attempts = 0
    db.commit()
    return TgLinkUpsertResponse(ok=True, confirm_code=link.confirm_code)


@router.post("/tg/link/confirm", response_model=TgLinkConfirmResponse, dependencies=[BotAuth])
def tg_link_confirm(
    body: TgLinkConfirmRequest,
    db: Annotated[OrmSession, Depends(get_session)],
) -> TgLinkConfirmResponse:
    """Подтверждение кода; 3 неверные попытки — сброс в awaiting_email, код нужно запросить заново."""
    link = db.scalar(select(TgLink).where(TgLink.chat_id == body.chat_id))
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Привязка не начата — сначала укажите почту")
    if link.state != "awaiting_confirm" or not link.confirm_code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Сначала запросите код подтверждения")
    if body.code.strip() != link.confirm_code:
        link.confirm_attempts = (link.confirm_attempts or 0) + 1
        if link.confirm_attempts >= MAX_CONFIRM_ATTEMPTS:
            link.state = "awaiting_email"
            link.user_id = None
            link.confirm_code = None
            link.confirm_attempts = 0
            db.commit()
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Превышено число попыток — запросите код заново",
            )
        db.commit()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Неверный код. Осталось попыток: {MAX_CONFIRM_ATTEMPTS - link.confirm_attempts}",
        )
    user = db.get(User, link.user_id)
    link.state = "idle"
    link.confirm_code = None
    link.confirm_attempts = 0
    db.commit()
    return TgLinkConfirmResponse(ok=True, user=UserOut.model_validate(user))
