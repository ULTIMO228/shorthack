"""Детектор массовых сбоев и управление инцидентами (US6, FR-050–FR-053).

Детектор ищет всплеск однотипных обращений (service + category) за скользящее
окно DETECTION_WINDOW_MIN от разных авторов (учёт как user_id, так и session_id).
При достижении порога DETECTION_THRESHOLD создаётся запись incidents(status='active'),
дежурный оператор получает синхронное outbound-уведомление (SC-005 ≤ 1 мин).

Новые обращения, подпадающие под активный инцидент (FR-052), получают шаблонный
ответ без обращения к LLM и без вызова автодиагностики (экономия лимита FR-021),
подзадача и тикет закрываются статусом «решена», приоритет подзадачи поднимается
до минимум high (FR-011) с отметкой в route_reason (FR-012).

Повторный прогон пайплайна или повторный evaluate по тому же тикету идемпотентен
(guard по ticket.incident_id, счётчик request_count не дублируется).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as OrmSession

from app import tickets, triggers
from app.events import log_event
from app.models import (
    Incident,
    KbArticle,
    OutboundMessage,
    Request,
    Session as UserSession,
    Subtask,
    TgLink,
    Ticket,
    User,
)

DETECTION_WINDOW_MIN = 15   # скользящее окно детекции, минуты
DETECTION_THRESHOLD = 3     # ≥3 однотипных от разных авторов
STATUS_ACTIVE = "active"
STATUS_RESOLVED = "resolved"
TEMPLATE_MASS_ANSWER = "Шаблон: массовый ответ при инциденте"
FALLBACK_MASS_ANSWER = (
    "Уведомляем: по {service} зафиксирован массовый сбой, затронувший несколько "
    "пользователей. Инцидент зарегистрирован (№{incident}), дежурные уведомлены, "
    "ведутся работы. Спасибо за сообщения — они помогли быстро обнаружить проблему."
)

SERVICE_DISPLAY = {
    "site": "сайт misis.ru",
    "lms": "newlms.misis.ru (LMS)",
    "wifi_guest": "Wi-Fi MISIS-Guest",
    "wifi_edu": "Wi-Fi MISIS-EDU",
    "wifi_corp": "Wi-Fi MISIS-CORP",
    "account": "корпоративная учётная запись",
    "certs": "справки",
}


def service_display(service: str | None) -> str:
    """Человекочитаемое имя сервиса для текста уведомлений и ответов."""
    if not service:
        return "сервис"
    return SERVICE_DISPLAY.get(service, service)


def find_active_incident(
    db: OrmSession,
    *,
    service: str | None,
    category: str | None,
) -> Incident | None:
    """Один активный инцидент на пару (service, category) — основа идемпотентности."""
    if not service or not category:
        return None
    return db.scalars(
        select(Incident).where(
            Incident.service == service,
            Incident.category == category,
            Incident.status == STATUS_ACTIVE,
        )
    ).first()


def count_wave(
    db: OrmSession,
    *,
    service: str,
    category: str,
    since: datetime,
) -> tuple[int, int, datetime | None]:
    """Агрегирующий запрос детектора: distinct-авторы, число запросов, время первого.

    distinct-автор = COUNT(DISTINCT COALESCE(requests.user_id, requests.session_id)).
    """
    stmt = (
        select(
            func.count(func.distinct(func.coalesce(Request.user_id, Request.session_id))),
            func.count(Request.id),
            func.min(Request.created_at),
        )
        .select_from(Subtask)
        .join(Request, Subtask.request_id == Request.id)
        .where(
            Subtask.service == service,
            Subtask.category == category,
            Request.created_at >= since,
        )
    )
    row = db.execute(stmt).one()
    distinct_authors = int(row[0] or 0)
    matching_requests = int(row[1] or 0)
    window_start = row[2]
    if isinstance(window_start, str):
        window_start = datetime.fromisoformat(window_start)
    return distinct_authors, matching_requests, window_start


def notify_duty(db: OrmSession, *, incident: Incident, authors: int) -> OutboundMessage:
    """Уведомление дежурному оператору о создании инцидента (FR-051, SC-005 ≤ 1 мин).

    chat_id: его TgLink со state='idle', иначе сентинел -(operator.id), при
    отсутствии операторов — сентинел -1.
    """
    operator = db.scalars(
        select(User).where(User.role == "operator").order_by(User.id)
    ).first()

    if operator is not None:
        link = db.scalars(
            select(TgLink).where(TgLink.user_id == operator.id, TgLink.state == "idle")
        ).first()
        chat_id = link.chat_id if link and link.chat_id is not None else -operator.id
    else:
        chat_id = -1

    text = (
        f"🔴 Инцидент: {service_display(incident.service)}. "
        f"{authors} обращения за {DETECTION_WINDOW_MIN} мин."
    )
    outbound = OutboundMessage(chat_id=chat_id, text=text, status="pending")
    db.add(outbound)
    db.flush()

    log_event(
        db,
        ticket_id=None,
        actor="system",
        action="incident_notify",
        payload={
            "incident_id": incident.id,
            "chat_id": chat_id,
            "outbound_id": outbound.id,
        },
    )
    return outbound


def _render_mass_answer(db: OrmSession, *, service: str, incident_id: int) -> str:
    """Рендеринг шаблона массового ответа (из БЗ или fallback)."""
    article = db.scalars(
        select(KbArticle).where(
            KbArticle.kind == "template",
            KbArticle.title == TEMPLATE_MASS_ANSWER,
        )
    ).first()

    if article and article.body:
        return (
            article.body.replace("{{service}}", service)
            .replace("{{incident}}", str(incident_id))
        )
    return FALLBACK_MASS_ANSWER.format(service=service, incident=incident_id)


def create_incident(
    db: OrmSession,
    *,
    service: str,
    category: str,
    authors: int,
    request_count: int,
    window_start: datetime,
) -> Incident:
    """Создание нового инцидента и отправка уведомления дежурному."""
    now = datetime.now(timezone.utc)
    incident = Incident(
        service=service,
        category=category,
        status=STATUS_ACTIVE,
        request_count=request_count,
        window_start=window_start,
        notified_at=now,
    )
    db.add(incident)
    db.flush()

    notify_duty(db, incident=incident, authors=authors)
    db.commit()

    log_event(
        db,
        ticket_id=None,
        actor="system",
        action="incident_created",
        payload={
            "incident_id": incident.id,
            "service": service,
            "category": category,
            "authors": authors,
            "window_start": window_start.isoformat() if hasattr(window_start, "isoformat") else str(window_start),
            "request_count": request_count,
        },
    )
    return incident


def attach_to_incident(
    db: OrmSession,
    *,
    incident: Incident,
    subtask: Subtask,
    ticket: Ticket,
) -> dict[str, str]:
    """Привязка тикета и подзадачи к инциденту (FR-052).

    Приоритет подзадачи поднимается минимум до high (FR-011).
    Подзадача переводится в статус 'решена'.
    Возвращает словарь реакции outage_notice.
    """
    is_new = (ticket.incident_id != incident.id)
    if is_new:
        ticket.incident_id = incident.id
        incident.request_count = (incident.request_count or 0) + 1

    old_priority = subtask.priority
    new_priority = triggers.raise_priority(subtask.priority or "medium", "high")
    if new_priority != old_priority:
        subtask.priority = new_priority
        marker = f"(триггеры: активный инцидент №{incident.id})"
        subtask.route_reason = (
            f"{subtask.route_reason} {marker}".strip()
            if subtask.route_reason
            else marker
        )

    subtask.status = tickets.STATUS_RESOLVED
    db.commit()

    if is_new:
        log_event(
            db,
            ticket_id=ticket.id,
            actor="system",
            action="incident_attached",
            payload={
                "incident_id": incident.id,
                "request_count": incident.request_count,
            },
        )

    answer_text = _render_mass_answer(
        db,
        service=service_display(subtask.service),
        incident_id=incident.id,
    )
    return {
        "kind": "outage_notice",
        "text": answer_text,
    }


def link_wave_tickets(
    db: OrmSession,
    incident: Incident,
    *,
    service: str,
    category: str,
    since: datetime,
) -> None:
    """Привязка всех тикетов и подзадач, сформировавших волну, к созданному инциденту."""
    stmt = (
        select(Ticket, Subtask)
        .join(Subtask, Subtask.request_id == Ticket.request_id)
        .join(Request, Request.id == Ticket.request_id)
        .where(
            Subtask.service == service,
            Subtask.category == category,
            Request.created_at >= since,
            Ticket.incident_id.is_(None),
        )
    )
    rows = db.execute(stmt).all()
    for ticket, subtask in rows:
        ticket.incident_id = incident.id
        old_priority = subtask.priority
        new_priority = triggers.raise_priority(subtask.priority or "medium", "high")
        if new_priority != old_priority:
            subtask.priority = new_priority
            marker = f"(триггеры: активный инцидент №{incident.id})"
            subtask.route_reason = (
                f"{subtask.route_reason} {marker}".strip()
                if subtask.route_reason
                else marker
            )
        subtask.status = tickets.STATUS_RESOLVED
    db.commit()


def evaluate(
    db: OrmSession,
    *,
    subtask: Subtask,
    ticket: Ticket,
    window_minutes: int = DETECTION_WINDOW_MIN,
    threshold: int = DETECTION_THRESHOLD,
) -> dict[str, str] | None:
    """Точка входа детектора инцидентов.

    1. Если сервис или категория не заданы -> None.
    2. Если уже есть активный инцидент по паре (service, category) -> привязка без повторного уведомления.
    3. Подсчёт волны за последние window_minutes: если distinct-авторов < threshold -> None.
    4. Создание нового инцидента -> привязка всех тикетов волны -> возврат реакции.
    """
    if not subtask.service or not subtask.category:
        return None

    active = find_active_incident(db, service=subtask.service, category=subtask.category)
    if active is not None:
        return attach_to_incident(db, incident=active, subtask=subtask, ticket=ticket)

    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    authors, matching_requests, window_start = count_wave(
        db,
        service=subtask.service,
        category=subtask.category,
        since=since,
    )

    if authors < threshold:
        return None

    incident = create_incident(
        db,
        service=subtask.service,
        category=subtask.category,
        authors=authors,
        request_count=matching_requests,
        window_start=window_start or since,
    )
    link_wave_tickets(
        db,
        incident,
        service=subtask.service,
        category=subtask.category,
        since=since,
    )
    return attach_to_incident(db, incident=incident, subtask=subtask, ticket=ticket)


def broadcast(
    db: OrmSession,
    *,
    incident: Incident,
    text: str | None,
    actor: str = "operator",
) -> tuple[int, datetime]:
    """Массовая рассылка авторам обращений привязанных тикетов (FR-053).

    Рассылка возможна только по активному инциденту.
    Гости без user_id пропускаются (нет Telegram-идентичности).
    """
    if incident.status != STATUS_ACTIVE:
        raise ValueError("Массовый ответ возможен только по активному инциденту")

    body_text = (
        text
        if text is not None
        else _render_mass_answer(
            db,
            service=service_display(incident.service),
            incident_id=incident.id,
        )
    )

    stmt = (
        select(Request)
        .join(Ticket, Ticket.request_id == Request.id)
        .where(Ticket.incident_id == incident.id)
    )
    requests = db.scalars(stmt).all()

    seen_authors: set[Any] = set()
    unique_user_ids: list[int] = []

    for req in requests:
        key = req.user_id if req.user_id is not None else req.session_id
        if key in seen_authors:
            continue
        seen_authors.add(key)
        if req.user_id is not None:
            unique_user_ids.append(req.user_id)

    for uid in unique_user_ids:
        link = db.scalars(
            select(TgLink).where(TgLink.user_id == uid, TgLink.state == "idle")
        ).first()
        chat_id = link.chat_id if link and link.chat_id is not None else -uid
        db.add(OutboundMessage(chat_id=chat_id, text=body_text, status="pending"))

    sent = len(unique_user_ids)
    now = datetime.now(timezone.utc)
    incident.broadcast_at = now
    db.commit()

    log_event(
        db,
        ticket_id=None,
        actor=actor,
        action="incident_broadcast",
        payload={
            "incident_id": incident.id,
            "sent": sent,
            "custom_text": text is not None,
        },
    )
    return sent, now


def resolve(db: OrmSession, *, incident: Incident, actor: str = "operator") -> Incident:
    """Закрытие инцидента (FR-053). Повторный resolve по закрытому бросает ValueError."""
    if incident.status != STATUS_ACTIVE:
        raise ValueError("Инцидент уже закрыт")

    incident.status = STATUS_RESOLVED
    db.commit()

    log_event(
        db,
        ticket_id=None,
        actor=actor,
        action="incident_resolved",
        payload={"incident_id": incident.id, "actor": actor},
    )
    return incident


def simulate_wave(
    db: OrmSession,
    *,
    service: str,
    count: int = 3,
) -> dict[str, Any]:
    """Симуляция волны жалоб (FR-050): count однотипных обращений от разных
    тестовых пользователей -> прогон каждого через evaluate.

    service — ключ классификатора (site/lms/wifi_guest/wifi_edu/wifi_corp),
    category волны фиксирована 'availability'.
    Возвращает {"incident_id": id|None, "created_requests": [...]}.
    """
    valid_services = ("site", "lms", "wifi_guest", "wifi_edu", "wifi_corp")
    if service not in valid_services:
        raise ValueError(f"Неизвестный сервис волны: {service}")

    count = max(1, min(int(count), 50))
    created_request_ids: list[int] = []
    last_incident_id: int | None = None

    for i in range(count):
        email = f"wave-user-{i+1}@edu.misis.ru"
        user = db.scalars(select(User).where(User.email == email)).first()
        if not user:
            user = User(email=email, full_name=f"Студент {i+1}", role="student")
            db.add(user)
            db.flush()

        session_id = secrets.token_urlsafe(16)
        user_session = UserSession(id=session_id, user_id=user.id)
        db.add(user_session)
        db.flush()

        req = Request(
            session_id=session_id,
            user_id=user.id,
            channel="web",
            raw_text=f"[тестовая волна] Жалоба {i+1}: не работает {service}",
            masked_text=f"[тестовая волна] Жалоба {i+1}: не работает {service}",
            lang="ru",
        )
        db.add(req)
        db.flush()
        created_request_ids.append(req.id)

        subtask = tickets.attach_subtask(
            db,
            req,
            f"Недоступен {service} (симуляция {i+1})",
            service=service,
            category="availability",
            route="auto_check",
            priority="high",
            confidence=0.9,
            status=tickets.STATUS_IN_PROGRESS,
        )

        ticket = tickets.ensure_ticket(db, req.id)
        evaluate(db, subtask=subtask, ticket=ticket)
        if ticket.incident_id:
            last_incident_id = ticket.incident_id

    log_event(
        db,
        actor="system",
        action="simulate_wave",
        payload={
            "service": service,
            "count": count,
            "created_requests": created_request_ids,
            "incident_id": last_incident_id,
        },
    )

    return {
        "incident_id": last_incident_id,
        "created_requests": created_request_ids,
    }

