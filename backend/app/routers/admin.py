"""Админка оператора (contracts/api.md): переключение эмулируемых сервисов (FR-024).

PATCH /api/admin/services/{id} — ручная смена состояния Wi-Fi сетей (эмуляция);
real-сервисы (misis.ru, newlms.misis.ru) получают состояние только от автопроверок
→ 409. Переключение логируется в журнал (actor=operator, action=service_state_change).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session as OrmSession

from app import certs, incidents, kb, metrics, tickets, tools, triggers
from app.agent import ESCALATION_FALLBACK_RECOMMENDATION
from app.auth import get_operator
from app.db import get_session
from app.events import log_event
from app.models import (
    CertOrder,
    Event,
    Incident,
    KbArticle,
    Request,
    Service,
    ServiceCheck,
    Ticket,
    User,
)
from app.routers.requests import _build_request_detail
from app.schemas import (
    AdminCertOrderOut,
    AdminCertOrderPatch,
    AdminCertOrderResponse,
    AdminCertUser,
    AdminQueueItem,
    CertOrderOut,
    EscalationCard,
    EscalationCheck,
    EscalationCloseRequest,
    EscalationCloseResponse,
    IncidentBroadcastRequest,
    IncidentBroadcastResponse,
    IncidentOut,
    IncidentResolveResponse,
    KbConfirmResponse,
    KbDraftOut,
    LastCheckOut,
    MetricsOut,
    QueueUser,
    ServicePatchRequest,
    ServicePatchResponse,
    StatusBoardItem,
    ToolInvokeRequest,
    ToolInvokeResponse,
    ToolOut,
    ToolParam,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _cert_order_out(order: CertOrder) -> CertOrderOut:
    return CertOrderOut(
        id=order.id,
        cert_type=order.cert_type,
        title=certs.title_of(order.cert_type),
        status=order.status,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.patch("/services/{service_id}", response_model=ServicePatchResponse)
def patch_service_state(
    service_id: int,
    body: ServicePatchRequest,
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> ServicePatchResponse:
    """Смена состояния эмулируемого сервиса (Wi-Fi); real → 409; событие в журнал."""
    service = db.get(Service, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Сервис не найден")
    if service.check_type != "emulated":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Состояние реального сервиса изменяется только автоматической проверкой",
        )
    old = service.state
    service.state = body.state
    service.updated_at = datetime.now(timezone.utc)
    db.commit()
    log_event(
        db,
        ticket_id=None,
        actor="operator",
        action="service_state_change",
        payload={
            "service_id": service.id,
            "name": service.name,
            "from": old,
            "to": body.state,
            "operator": operator.email,
        },
    )
    return ServicePatchResponse(service=service)


@router.get("/certs/orders", response_model=list[AdminCertOrderOut])
def list_admin_cert_orders(
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> list[AdminCertOrderOut]:
    """Список всех заказов справок для оператора (FR-041/042)."""
    stmt = (
        select(CertOrder, User)
        .join(User, CertOrder.user_id == User.id)
        .order_by(CertOrder.created_at.desc())
    )
    rows = db.execute(stmt).all()
    results = []
    for order, user in rows:
        results.append(
            AdminCertOrderOut(
                id=order.id,
                cert_type=order.cert_type,
                title=certs.title_of(order.cert_type),
                status=order.status,
                user=AdminCertUser(
                    full_name=user.full_name,
                    email=user.email,
                    group_name=user.group_name,
                ),
                created_at=order.created_at,
            )
        )
    return results


@router.patch("/certs/orders/{order_id}", response_model=AdminCertOrderResponse)
def patch_cert_order_status(
    order_id: int,
    body: AdminCertOrderPatch,
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> AdminCertOrderResponse:
    """Смена статуса заказа справки по цепочке (FR-042)."""
    order = db.get(CertOrder, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Заказ не найден")
    if not certs.can_transition(order.status, body.status):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Переход «{order.status}» → «{body.status}» невозможен",
        )
    certs.set_status(db, order, body.status, actor="operator")
    return AdminCertOrderResponse(order=_cert_order_out(order))


@router.get("/queue", response_model=list[AdminQueueItem])
def get_admin_queue(
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> list[AdminQueueItem]:
    """Очередь обращений для оператора (FR-016, contracts/api.md).

    Возвращает все обращения с собственными заявками (без дублей).
    Сортировка: priority (critical → high → medium → low), затем created_at по возрастанию.
    """
    stmt = (
        select(Request, Ticket)
        .join(Ticket, Ticket.request_id == Request.id)
    )
    rows = db.execute(stmt).all()

    items: list[AdminQueueItem] = []
    for req, ticket in rows:
        queue_user: QueueUser | None = None
        if req.user_id is not None:
            user_obj = db.get(User, req.user_id)
            if user_obj:
                queue_user = QueueUser(
                    full_name=user_obj.full_name or "",
                    email=user_obj.email or "",
                )

        first_subtask = req.subtasks[0] if req.subtasks else None
        summary = first_subtask.summary if first_subtask and first_subtask.summary else (req.masked_text or req.raw_text or "")
        service = first_subtask.service if first_subtask else None
        category = first_subtask.category if first_subtask and first_subtask.category else "other"
        route = first_subtask.route if first_subtask and first_subtask.route else "escalate"
        route_reason = first_subtask.route_reason if first_subtask and first_subtask.route_reason else ""

        priority = "low"
        if req.subtasks:
            for s in req.subtasks:
                if s.priority:
                    priority = triggers.raise_priority(priority, s.priority)
        else:
            priority = "medium"

        items.append(
            AdminQueueItem(
                request_id=req.id,
                number=tickets.ticket_number(ticket),
                created_at=req.created_at,
                channel=req.channel or "web",
                user=queue_user,
                summary=summary,
                service=service,
                category=category,
                route=route,
                route_reason=route_reason,
                priority=priority,
                status=ticket.status,
                escalated=bool(ticket.escalated),
                incident_id=ticket.incident_id,
            )
        )

    items.sort(
        key=lambda i: (-triggers.PRIORITY_ORDER.get(i.priority, 0), i.created_at)
    )
    return items


@router.get("/escalations/{request_id}", response_model=EscalationCard)
def get_escalation_card(
    request_id: int,
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> EscalationCard:
    """Карточка эскалации для оператора (FR-060)."""
    req = db.get(Request, request_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Обращение не найдено")

    ticket = req.tickets[0] if req.tickets else db.scalar(select(Ticket).where(Ticket.request_id == request_id))
    if ticket is None or not ticket.escalated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Обращение не эскалировано")

    req_detail = _build_request_detail(db, req)

    event = db.scalar(
        select(Event)
        .where(Event.ticket_id == ticket.id, Event.action == "escalation_package")
        .order_by(Event.id.desc())
    )
    if event and event.payload:
        try:
            pkg = json.loads(event.payload)
        except Exception:
            pkg = {}
    else:
        pkg = {}

    first_subtask = req.subtasks[0] if req.subtasks else None
    summary = pkg.get("summary") or (first_subtask.summary if first_subtask else req.masked_text or "")
    why_escalated = pkg.get("why_escalated") or (first_subtask.route_reason if first_subtask and first_subtask.route_reason else "Передано дежурному оператору")
    recommendation = pkg.get("recommendation") or ESCALATION_FALLBACK_RECOMMENDATION
    raw_checks = pkg.get("checks")
    if raw_checks is None:
        raw_checks = tools.collect_checks(db, req.id)

    checks: list[EscalationCheck] = []
    for c in raw_checks:
        if isinstance(c, dict):
            checks.append(EscalationCheck.model_validate(c))
        elif isinstance(c, EscalationCheck):
            checks.append(c)

    return EscalationCard(
        request=req_detail,
        summary=summary,
        checks=checks,
        recommendation=recommendation,
        why_escalated=why_escalated,
    )


@router.post("/escalations/{request_id}/close", response_model=EscalationCloseResponse)
def close_escalation(
    request_id: int,
    body: EscalationCloseRequest,
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> EscalationCloseResponse:
    """Закрытие эскалации оператором (T038, T043, FR-063).

    Закрывает тикет (через «решена» → «закрыта»). Если передан add_to_kb=true,
    вызывает инструмент draft_kb_article и создаёт неподтверждённый черновик
    KbArticle (source='operator', confirmed=false).
    Повторный вызов для уже закрытого тикета возвращает 409 Conflict.
    """
    if not body.resolution or not body.resolution.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Укажите резолюцию закрытия",
        )

    req = db.get(Request, request_id)
    if req is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Обращение не найдено")

    ticket = req.tickets[0] if req.tickets else db.scalar(select(Ticket).where(Ticket.request_id == request_id))
    if ticket is None or not ticket.escalated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Обращение не эскалировано")

    if ticket.status == tickets.STATUS_CLOSED:
        return EscalationCloseResponse(ticket_status=ticket.status, kb_draft=None)

    # Переход статуса по цепочке FR-015
    try:
        if ticket.status in tickets.OPEN_STATUSES:
            tickets.set_status(db, ticket, tickets.STATUS_RESOLVED, actor="operator")
            tickets.set_status(db, ticket, tickets.STATUS_CLOSED, actor="operator")
        elif ticket.status == tickets.STATUS_RESOLVED:
            tickets.set_status(db, ticket, tickets.STATUS_CLOSED, actor="operator")
        else:
            tickets.set_status(db, ticket, tickets.STATUS_CLOSED, actor="operator")
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))

    for subtask in req.subtasks:
        if subtask.status != tickets.STATUS_RESOLVED:
            subtask.status = tickets.STATUS_RESOLVED
    db.commit()

    log_event(
        db,
        ticket_id=ticket.id,
        actor="operator",
        action="escalation_closed",
        payload={"resolution": body.resolution.strip(), "add_to_kb": body.add_to_kb},
    )

    kb_draft = None
    if body.add_to_kb:
        call = tools.call_tool(
            db,
            "draft_kb_article",
            {"request_id": request_id, "resolution": body.resolution.strip()},
            ticket_id=ticket.id,
            tool_calls=0,
        )
        res = call.result
        title = res.get("title") or f"Решение: {body.resolution.strip()}"[:100]
        article_body = (
            res.get("body")
            or f"Проблема: {req.masked_text or req.raw_text or '—'}.\nРешение: {body.resolution.strip()}."
        )
        topic = res.get("topic") or "other"

        article = KbArticle(
            kind="document",
            title=title,
            body=article_body,
            topic=topic,
            source="operator",
            confirmed=False,
            embedding=None,
        )
        db.add(article)
        db.commit()
        kb_draft = KbDraftOut(
            id=article.id,
            title=article.title,
            body=article.body,
            confirmed=False,
        )

    return EscalationCloseResponse(
        ticket_status=ticket.status,
        kb_draft=kb_draft,
    )


@router.post("/kb/articles/{article_id}/confirm", response_model=KbConfirmResponse)
def confirm_kb_article(
    article_id: int,
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> KbConfirmResponse:
    """Подтверждение черновика статьи БЗ оператором (FR-063, T043).

    Переводит статью в confirmed=true и индексирует embedding (kb.index_article).
    Идемпотентно: если статья уже имеет embedding, повторно не пересчитывает.
    При недоступности LLM подтверждение всё равно успешно (деградация до keyword-поиска).
    """
    article = db.get(KbArticle, article_id)
    if article is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Статья не найдена")

    article.confirmed = True
    if article.embedding is None:
        kb.index_article(db, article)

    db.commit()

    log_event(
        db,
        actor="operator",
        action="kb_article_confirmed",
        payload={"article_id": article.id, "indexed": bool(article.embedding is not None)},
    )

    return KbConfirmResponse(id=article.id, confirmed=True)


@router.get("/incidents", response_model=list[IncidentOut])
def list_incidents(
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> list[Incident]:
    """Список инцидентов (FR-053): active первыми, далее по убыванию id."""
    rows = db.scalars(
        select(Incident).order_by(
            case((Incident.status == incidents.STATUS_ACTIVE, 0), else_=1),
            Incident.id.desc(),
        )
    ).all()
    return list(rows)


@router.post("/incidents/{incident_id}/broadcast", response_model=IncidentBroadcastResponse)
def broadcast_incident(
    incident_id: int,
    body: IncidentBroadcastRequest,
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> IncidentBroadcastResponse:
    """Массовая рассылка по инциденту (FR-053); 404 если нет, 409 если не active."""
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Инцидент не найден")
    if incident.status != incidents.STATUS_ACTIVE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Массовый ответ возможен только по активному инциденту",
        )
    sent, broadcast_at = incidents.broadcast(
        db,
        incident=incident,
        text=body.text,
        actor=operator.email,
    )
    return IncidentBroadcastResponse(sent=sent, broadcast_at=broadcast_at)


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentResolveResponse)
def resolve_incident(
    incident_id: int,
    operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> IncidentResolveResponse:
    """Закрытие инцидента (FR-053); 404 если нет, 409 если уже закрыт."""
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Инцидент не найден")
    try:
        incidents.resolve(db, incident=incident, actor=operator.email)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Инцидент уже закрыт") from exc
    return IncidentResolveResponse(status=incident.status)


@router.get("/metrics", response_model=MetricsOut)
def admin_metrics(
    _operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> MetricsOut:
    """Метрики качества работы помощника (FR-062)."""
    return metrics.collect_metrics(db)


@router.get("/status-board", response_model=list[StatusBoardItem])
def get_status_board(
    _operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> list[StatusBoardItem]:
    """Все сервисы + последний service_checks по каждому (порядок по services.id)."""
    services = db.scalars(select(Service).order_by(Service.id)).all()
    if not services:
        return []

    subq = (
        select(
            ServiceCheck.service_id,
            func.max(ServiceCheck.checked_at).label("max_checked_at"),
        )
        .group_by(ServiceCheck.service_id)
        .subquery()
    )
    latest_checks = db.scalars(
        select(ServiceCheck)
        .join(
            subq,
            (ServiceCheck.service_id == subq.c.service_id)
            & (ServiceCheck.checked_at == subq.c.max_checked_at),
        )
        .order_by(ServiceCheck.id.desc())
    ).all()

    last_checks_by_service_id = {c.service_id: c for c in latest_checks}

    items: list[StatusBoardItem] = []
    for svc in services:
        chk = last_checks_by_service_id.get(svc.id)
        last_chk_out = None
        if chk is not None:
            last_chk_out = LastCheckOut(
                ok=bool(chk.ok),
                http_code=chk.http_code,
                latency_ms=chk.latency_ms,
                checked_at=chk.checked_at,
            )
        items.append(
            StatusBoardItem(
                id=svc.id,
                name=svc.name,
                check_type=svc.check_type,
                state=svc.state,
                last_check=last_chk_out,
            )
        )
    return items


@router.get("/tools", response_model=list[ToolOut])
def list_admin_tools(
    _operator: Annotated[User, Depends(get_operator)],
) -> list[ToolOut]:
    """Реестр инструментов для панели оператора (только ADMIN_INVOKABLE)."""
    items: list[ToolOut] = []
    for name in tools.ADMIN_INVOKABLE:
        info = tools.TOOLS.get(name)
        if not info:
            continue
        params_out = [
            ToolParam(name=p["name"], type=p["type"], default=p.get("default"))
            for p in info.get("params", [])
        ]
        items.append(
            ToolOut(
                name=name,
                type=info["type"],
                description=info["description"],
                params=params_out,
            )
        )
    return items


def _validate_tool_params(name: str, raw_params: dict[str, Any]) -> dict[str, Any]:
    if name not in tools.ADMIN_INVOKABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Инструмент не найден")
    tool_info = tools.TOOLS.get(name)
    if not tool_info or "params" not in tool_info:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Инструмент не найден")

    param_specs = {p["name"]: p for p in tool_info["params"]}

    for key in raw_params:
        if key not in param_specs:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Неизвестный параметр «{key}»",
            )

    resolved: dict[str, Any] = {}
    for p_name, spec in param_specs.items():
        if p_name in raw_params:
            val = raw_params[p_name]
        elif spec.get("default") is not None:
            val = spec["default"]
        elif spec.get("required"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Параметр «{p_name}» обязателен",
            )
        else:
            val = None

        if val is not None:
            expected_type = spec.get("type")
            if expected_type == "string" and not isinstance(val, str):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Параметр «{p_name}» должен быть строкой",
                )
            elif expected_type == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Параметр «{p_name}» должен быть целым числом",
                )

            choices = spec.get("choices")
            if choices is not None and val not in choices:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Параметр «{p_name}» вне допустимого набора",
                )

            min_val = spec.get("min")
            if min_val is not None and val < min_val:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Параметр «{p_name}» меньше допустимого минимума ({min_val})",
                )

            max_val = spec.get("max")
            if max_val is not None and val > max_val:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Параметр «{p_name}» больше допустимого максимума ({max_val})",
                )

            if p_name == "url" and spec.get("default") and val != spec["default"]:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Недопустимый URL «{val}» (разрешён только «{spec['default']}»)",
                )

            resolved[p_name] = val

    return resolved


@router.post("/tools/{name}/invoke", response_model=ToolInvokeResponse)
def invoke_admin_tool(
    name: str,
    body: ToolInvokeRequest,
    _operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> ToolInvokeResponse:
    """Вызов инструмента из тест-панели оператора (FR-020, FR-024)."""
    resolved_params = _validate_tool_params(name, body.params or {})

    if name in ("check_site", "check_lms"):
        call = tools.call_tool(db, name, {})
        return ToolInvokeResponse(
            tool=name,
            ok=bool(call.result.get("ok")),
            result=call.result,
        )
    elif name == "check_wifi":
        call = tools.call_tool(db, "check_wifi", {"network": resolved_params["network"]})
        return ToolInvokeResponse(
            tool=name,
            ok=bool(call.result.get("ok")),
            result=call.result,
        )
    elif name == "simulate_wave":
        res = incidents.simulate_wave(
            db,
            service=resolved_params["service"],
            count=resolved_params.get("count", 3),
        )
        return ToolInvokeResponse(
            tool="simulate_wave",
            ok=True,
            result=res,
        )
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Инструмент не найден")




