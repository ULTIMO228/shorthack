"""Роутер /api/auth/*: login / logout / me (contracts/api.md, research.md R4)."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session as OrmSession

from app import auth
from app.auth import COOKIE_NAME, full_name_from_email, get_current_session, is_misis_email
from app.db import get_session
from app.models import Session as UserSession
from app.models import User
from app.models import Request as SupportRequest
from app.schemas import LoginRequest, LoginResponse, MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[OrmSession, Depends(get_session)],
) -> LoginResponse:
    """Вход по email без пароля: новый корректный домен — автосоздание пользователя."""
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
    # гостевая сессия привязывается к пользователю — гостевой диалог сохраняется (FR-016)
    token = request.cookies.get(COOKIE_NAME)
    session = db.get(UserSession, token) if token else None
    if session is None:
        session = UserSession(id=secrets.token_urlsafe(32))
        db.add(session)
    session.user_id = user.id
    # FR-002/FR-020/SC-005: гостевые обращения сессии приписываем профилю —
    # история гостя не теряется после входа
    db.execute(
        update(SupportRequest)
        .where(SupportRequest.session_id == session.id, SupportRequest.user_id.is_(None))
        .values(user_id=user.id)
    )
    db.commit()
    response.set_cookie(COOKIE_NAME, session.id, httponly=True, samesite="lax", path="/")
    return LoginResponse(user=user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Annotated[OrmSession, Depends(get_session)],
) -> None:
    """Выход: деавторизация сессии (user_id → null) и удаление cookie.

    Сессию не удаляем: на неё ссылаются обращения (requests.session_id NOT NULL),
    удаление давало IntegrityError → 500. История обращений сохраняется (FR-016).
    """
    token = request.cookies.get(COOKIE_NAME)
    if token:
        session = db.get(UserSession, token)
        if session is not None:
            session.user_id = None
            db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")


@router.get("/me", response_model=MeResponse)
def me(
    session: Annotated[UserSession, Depends(get_current_session)],
) -> MeResponse:
    """Кто я: авторизованный пользователь или гость (сессия создаётся автоматически)."""
    user = session.user if session.user_id is not None else None
    return MeResponse(user=user, guest=user is None)
