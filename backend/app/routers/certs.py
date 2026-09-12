"""Эндпоинты заказа справок (contracts/api.md): каталог, оформление, список моих заказов."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app import certs
from app.auth import get_current_session
from app.db import get_session
from app.models import CertOrder, Session as UserSession
from app.schemas import (
    CertCatalogItem,
    CertOrderCreate,
    CertOrderCreatedResponse,
    CertOrderOut,
)

router = APIRouter(prefix="/api/certs", tags=["certs"])

AUTH_REQUIRED_DETAIL = "Для заказа справки войдите по корпоративной почте МИСИС"


def _order_out(order: CertOrder) -> CertOrderOut:
    return CertOrderOut(
        id=order.id,
        cert_type=order.cert_type,
        title=certs.title_of(order.cert_type),
        status=order.status,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.get("/catalog", response_model=list[CertCatalogItem])
def get_catalog() -> list[CertCatalogItem]:
    """Публичный каталог справок (5 типов, FR-041)."""
    return [CertCatalogItem(**item) for item in certs.catalog_items()]


@router.post("/orders", response_model=CertOrderCreatedResponse)
def create_order(
    body: CertOrderCreate,
    current_session: Annotated[UserSession, Depends(get_current_session)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> CertOrderCreatedResponse:
    """Заказ справки: только для авторизованных пользователей (FR-040, FR-043)."""
    if current_session.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AUTH_REQUIRED_DETAIL,
        )
    order = certs.create_order(db, user_id=current_session.user_id, cert_type=body.cert_type)
    return CertOrderCreatedResponse(order=_order_out(order))


@router.get("/orders", response_model=list[CertOrderOut])
def get_my_orders(
    current_session: Annotated[UserSession, Depends(get_current_session)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> list[CertOrderOut]:
    """Мои заказы справок (FR-040, FR-043): только для авторизованных пользователей."""
    if current_session.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AUTH_REQUIRED_DETAIL,
        )
    stmt = (
        select(CertOrder)
        .where(CertOrder.user_id == current_session.user_id)
        .order_by(CertOrder.created_at.desc())
    )
    orders = db.scalars(stmt).all()
    return [_order_out(order) for order in orders]
