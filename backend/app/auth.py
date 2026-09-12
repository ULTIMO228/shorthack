"""Авторизация (эмуляция): вход по корпоративной почте МИСИС, cookie-сессии, гостевой режим.

research.md R4: без паролей и внешнего IdP. Гостевая сессия (FR-016) создаётся
автоматически при первом обращении без логина и привязывается к пользователю
при входе — диалог гостя сохраняется.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session as OrmSession

from app.db import get_session
from app.models import Session as UserSession
from app.models import User

COOKIE_NAME = "session_id"
MISIS_DOMAINS = ("@misis.ru", "@edu.misis.ru")


def is_misis_email(email: str) -> bool:
    """Только корпоративные домены МИСИС (FR-001, research.md R4).

    Строго: ровно один '@', локальная часть не пуста и без пробелов, домен —
    точное совпадение (не поддомен-обманка вида misis.ru.evil.com).
    """
    normalized = email.strip().lower()
    if normalized.count("@") != 1:
        return False
    local, domain = normalized.split("@")
    if not local or any(ch.isspace() for ch in local):
        return False
    return "@" + domain in MISIS_DOMAINS


def full_name_from_email(email: str) -> str:
    """ФИО из локальной части email: 'ivanov.ivan' -> 'Ivanov Ivan' (эмуляция)."""
    local = email.split("@", 1)[0]
    parts = [part for part in local.replace("_", ".").split(".") if part]
    return " ".join(part.capitalize() for part in parts) or "Пользователь"


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", path="/")


def get_current_session(
    request: Request,
    response: Response,
    db: Annotated[OrmSession, Depends(get_session)],
) -> UserSession:
    """Текущая сессия по cookie; при отсутствии/невалидности — авто-создание гостевой (FR-016)."""
    token = request.cookies.get(COOKIE_NAME)
    session = db.get(UserSession, token) if token else None
    if session is not None:
        return session
    session = UserSession(id=secrets.token_urlsafe(32))
    db.add(session)
    db.commit()
    _set_session_cookie(response, session.id)
    return session


def get_current_user(session: Annotated[UserSession, Depends(get_current_session)]) -> User:
    """Только авторизованный пользователь; гостю — 401 с предложением войти."""
    user = session.user if session.user_id is not None else None
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Действие доступно после входа по корпоративной почте МИСИС",
        )
    return user


def get_operator(session: Annotated[UserSession, Depends(get_current_session)]) -> User:
    """Только оператор (админка); не оператору — 403."""
    user = get_current_user(session)
    if user.role != "operator":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Доступно только оператору")
    return user
