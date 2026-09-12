"""Реестр инструментов (FR-020) и диспетчер вызова с журналированием (FR-061, FR-021).

Инструменты типа `exec` выполняются без LLM:
- check_site — HTTP-проверка https://misis.ru (FR-022);
- check_lms — HTTP-проверка https://newlms.misis.ru (FR-023);
- check_wifi — чтение эмулируемого состояния Wi-Fi сетей MISIS-Guest/EDU/CORP
  из таблицы services (FR-024, без реального сетевого вызова).

Критерий доступности (FR-022/023): HTTP 2xx/3xx в пределах 5 секунд.
Каждый вызов пишется в журнал events (actor «tool:<name>», действия tool_call/
tool_result) и инкрементирует счётчик вызовов инструментов обращения (FR-021,
лимит 3 — контролирует нода execute_route в agent.py). Результат проверки
фиксируется в service_checks (с привязкой к подзадаче, если она передана).

Инструменты типа `llm` (с участием модели):
- kb_agent — RAG-агент по базе знаний (FR-031/FR-032, US3): переформулировка
  запроса, разделение на подзапросы, retrieval (app.kb) и ответ строго по
  найденным источникам; пустая выдача — found=false (эскалация, не выдумка).

Инструменты `summarize` и `draft_kb_article` добавляются в последующих фазах;
диспетчер типо-независим.
"""

from __future__ import annotations

import inspect
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from app import incidents, kb, llm, tickets
from app.certs import order_certificate
from app.events import log_event
from app.models import (
    Event,
    Request,
    Service,
    ServiceCheck,
    Session as UserSession,
    Subtask,
    Ticket,
    User,
)

# Таймаут HTTP-проверок: «доступен» = 2xx/3xx в пределах 5 секунд (FR-022/023)
CHECK_TIMEOUT_SEC = 5.0
HTTP_TIMEOUT_SEC = CHECK_TIMEOUT_SEC  # алиас обратной совместимости

# URL реальных проверок и соответствующие строки таблицы services
SITE_URL = "https://misis.ru"
LMS_URL = "https://newlms.misis.ru"

# Сервис подзадачи (классификатор) → инструмент и параметры проверки
CHECK_TOOL_BY_SERVICE: dict[str, tuple[str, dict[str, Any]]] = {
    "site": ("check_site", {}),
    "lms": ("check_lms", {}),
    "wifi_guest": ("check_wifi", {"network": "wifi_guest"}),
    "wifi_edu": ("check_wifi", {"network": "wifi_edu"}),
    "wifi_corp": ("check_wifi", {"network": "wifi_corp"}),
}

# Имя сервиса подзадачи → каноническое имя строки services (FR-024)
WIFI_SERVICE_NAMES = {
    "wifi_guest": "MISIS-Guest",
    "wifi_edu": "MISIS-EDU",
    "wifi_corp": "MISIS-CORP",
    "MISIS-Guest": "MISIS-Guest",
    "MISIS-EDU": "MISIS-EDU",
    "MISIS-CORP": "MISIS-CORP",
}


class ToolError(RuntimeError):
    """Инструмент не найден в реестре или вызван с некорректными параметрами."""


@dataclass(frozen=True)
class ToolCall:
    """Итог диспетчерского вызова: результат (JSON-словарь) и новый счётчик вызовов."""

    result: dict[str, Any]
    tool_calls: int


def resolve_check_tool(service: str | None) -> tuple[str, dict[str, Any]] | None:
    """Инструмент проверки для сервиса подзадачи; None — сервис не проверяется."""
    if not service:
        return None
    return CHECK_TOOL_BY_SERVICE.get(service)


def _get_service_by_id(db: OrmSession, service_id: int) -> Service:
    """Строка сервиса по id; отсутствующий id — ошибка параметров вызова."""
    service = db.get(Service, service_id)
    if service is None:
        raise ToolError(f"Сервис id={service_id} не найден")
    return service


def _ensure_service(db: OrmSession, name: str, check_type: str) -> Service:
    """Строка сервиса по имени; без сидирования создаётся (Wi-Fi — в состоянии up)."""
    service = db.scalar(select(Service).where(Service.name == name))
    if service is None:
        service = Service(name=name, check_type=check_type, state="up")
        db.add(service)
        db.commit()
    return service


def _record_check(
    db: OrmSession,
    service: Service,
    *,
    subtask_id: int | None,
    ok: bool,
    http_code: int | None,
    latency_ms: int | None,
) -> ServiceCheck:
    """Записать результат проверки в service_checks (FR-022/023/024)."""
    check = ServiceCheck(
        service_id=service.id,
        subtask_id=subtask_id,
        ok=ok,
        http_code=http_code,
        latency_ms=latency_ms,
    )
    db.add(check)
    db.commit()
    return check


def _http_check(
    db: OrmSession,
    *,
    service_name: str,
    url: str,
    service_id: int | None = None,
    subtask_id: int | None = None,
) -> dict[str, Any]:
    """Общая реализация check_site/check_lms: GET с таймаутом 5 c, критерий 2xx/3xx."""
    service = _get_service_by_id(db, service_id) if service_id is not None \
        else _ensure_service(db, service_name, "real")
    started = time.monotonic()
    http_code: int | None = None
    ok = False
    try:
        with httpx.Client(timeout=CHECK_TIMEOUT_SEC, follow_redirects=True) as client:
            http_code = client.get(url).status_code
        ok = 200 <= http_code < 400
    except httpx.HTTPError:
        ok = False  # таймаут/обрыв/сбой соединения — сервис недоступен
    latency_ms = int((time.monotonic() - started) * 1000)
    service.state = "up" if ok else "down"  # состояние реального сервиса — по проверкам
    _record_check(db, service, subtask_id=subtask_id, ok=ok,
                  http_code=http_code, latency_ms=latency_ms)
    return {
        "ok": ok,
        "service": service_name,
        "http_code": http_code,
        "latency_ms": latency_ms,
    }


def check_site(db: OrmSession, *, service_id: int | None = None, url: str = SITE_URL,
               subtask_id: int | None = None) -> dict[str, Any]:
    """Проверка доступности https://misis.ru (FR-022)."""
    return _http_check(db, service_name="misis.ru", url=url,
                       service_id=service_id, subtask_id=subtask_id)


def check_lms(db: OrmSession, *, service_id: int | None = None, url: str = LMS_URL,
              subtask_id: int | None = None) -> dict[str, Any]:
    """Проверка доступности https://newlms.misis.ru (FR-023)."""
    return _http_check(db, service_name="newlms.misis.ru", url=url,
                       service_id=service_id, subtask_id=subtask_id)


def check_wifi(db: OrmSession, *, service_id: int | None = None, network: str = "wifi_edu",
               subtask_id: int | None = None) -> dict[str, Any]:
    """Эмулируемая проверка Wi-Fi сети: состояние из services, без сетевого вызова (FR-024)."""
    if service_id is not None:
        service = _get_service_by_id(db, service_id)
        name = service.name or network
    else:
        name = WIFI_SERVICE_NAMES.get(network, network)
        service = _ensure_service(db, name, "emulated")
    ok = service.state != "down"
    _record_check(db, service, subtask_id=subtask_id, ok=ok, http_code=None, latency_ms=None)
    return {
        "ok": ok,
        "service": name,
        "state": service.state,
        "http_code": None,
        "latency_ms": None,
    }


# ---------------------------------------------------------------------------
# T030 — kb_agent: RAG-агент по базе знаний (type="llm", FR-031/FR-032)
# ---------------------------------------------------------------------------

class Subqueries(BaseModel):
    """JSON-ответ разделения на подзапросы: список независимых поисковых запросов."""

    queries: list[str] = Field(min_length=1)


REFORMULATE_PROMPT = (
    "Ты — поисковый ассистент базы знаний техподдержки университета МИСИС. "
    "Переформулируй вопрос пользователя как точный поисковый запрос по базе знаний "
    "(регламенты, инструкции, политики). Сохрани смысл и ключевые термины. "
    "Верни только переформулированный запрос без пояснений и кавычек.\n\nВопрос: {question}"
)

SUBQUERIES_PROMPT = (
    "Ты — поисковый ассистент базы знаний техподдержки университета МИСИС. "
    "Раздели поисковый запрос на независимые подзапросы, если он составной "
    "(разные сервисы или темы). Если запрос единый — верни список из одного элемента. "
    'Верни строго один JSON-объект вида {{"queries": ["подзапрос 1", "подзапрос 2"]}} '
    "без пояснений и markdown.\n\nЗапрос: {query}"
)

ANSWER_PROMPT = (
    "Ты — ИИ-помощник технической поддержки университета МИСИС. "
    "Ответь на вопрос пользователя СТРОГО на основе приведённых фрагментов базы знаний. "
    "Не выдумывай факты, которых нет во фрагментах; если информации не хватает — "
    "скажи об этом прямо. Ответ — по-русски, кратко и по делу, со ссылками на регламенты. "
    "В конце перечисли использованные источники (заголовки статей).\n\n"
    "Вопрос: {question}\n\nФрагменты базы знаний:\n{chunks}"
)


def kb_agent(
    db: OrmSession,
    *,
    question: str,
    subtask_id: int | None = None,
    context: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """RAG-агент по базе знаний (FR-031, type="llm").

    1. Переформулировка запроса через LLM.
    2. Разделение на подзапросы при необходимости (FR-031).
    3. Retrieval с гейтом: проверка исходного вопроса kb.retrieve(db, question).
       Если исходный вопрос пуст — сразу отказ (FR-032, защита от галлюцинаций переформулировки).
       Если не пуст — объединение с результатами подзапросов, дедупликация по article.id (max score),
       сортировка desc, top-3.
    4. Синтез ответа строго по найденным статьям.
    """
    try:
        reformulated = llm.chat(REFORMULATE_PROMPT.format(question=question), timeout=15.0)
    except llm.LLMUnavailable as exc:
        return {"ok": False, "error": f"LLMUnavailable: {exc}"}

    try:
        split = llm.chat_structured(
            SUBQUERIES_PROMPT.format(query=reformulated), Subqueries, timeout=15.0
        )
        queries = [q.strip() for q in split.queries if q.strip()] or [reformulated]
    except (llm.LLMUnavailable, Exception):
        queries = [reformulated]

    # Гейт на исходный вопрос: если исходный вопрос не имеет совпадений в БЗ — эскалация (FR-032)
    hits_q = kb.retrieve(db, question)
    if not hits_q:
        return {
            "ok": True,
            "found": False,
            "answer": None,
            "sources": [],
            "queries": queries,
        }

    # Объединение с результатами подзапросов
    all_hits: dict[int, Any] = {h.article.id: h for h in hits_q}
    for q in queries:
        for h in kb.retrieve(db, q):
            if h.article.id not in all_hits or h.score > all_hits[h.article.id].score:
                all_hits[h.article.id] = h

    sorted_hits = sorted(all_hits.values(), key=lambda h: (-h.score, h.article.id))[:kb.TOP_K]

    chunks = "\n\n---\n\n".join(
        f"«{hit.article.title}»\n{hit.article.body}" for hit in sorted_hits
    )
    try:
        answer = llm.chat(ANSWER_PROMPT.format(question=question, chunks=chunks), timeout=20.0)
    except llm.LLMUnavailable as exc:
        return {"ok": False, "error": f"LLMUnavailable: {exc}"}

    sources = [{"id": hit.article.id, "title": hit.article.title, "score": hit.score} for hit in sorted_hits]
    return {
        "ok": True,
        "found": True,
        "answer": answer.strip(),
        "sources": sources,
        "queries": queries,
    }


# ---------------------------------------------------------------------------
# T042 — draft_kb_article: подготовка черновика статьи БЗ (type="llm", FR-063)
# ---------------------------------------------------------------------------

class KbDraftResult(BaseModel):
    """JSON-ответ LLM для черновика статьи базы знаний."""

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    topic: str = Field(default="other")


DRAFT_KB_PROMPT = (
    "Ты — ассистент технической поддержки университета МИСИС. "
    "По переписке обращения и итоговому решению оператора подготовь черновик статьи базы знаний.\n"
    "Требования:\n"
    "- title: краткий понятный заголовок статьи одной строкой (до 100 символов)\n"
    "- body: структурированная инструкция для пользователей\n"
    "- topic: строго одна тема из списка: wifi, password, lms, certs, support, other\n\n"
    "Текст обращения: {problem}\n"
    "История переписки:\n{dialog}\n"
    "Решение оператора: {resolution}"
)


def draft_kb_article(
    db: OrmSession,
    *,
    request_id: int,
    resolution: str,
    subtask_id: int | None = None,
) -> dict[str, Any]:
    """Черновик статьи БЗ по переписке и resolution (FR-063, type='llm').

    Собирает переписку из events тикета (диалог и реакции), текст обращения
    (маскированный, FR-002) и решение оператора. При LLMUnavailable
    деградирует до шаблонного черновика, не роняя закрытие эскалации.
    """
    request = db.get(Request, request_id)
    ticket = None
    if request is not None and request.tickets:
        ticket = request.tickets[0]
    elif request is not None:
        ticket = db.scalar(select(Ticket).where(Ticket.request_id == request_id))

    dialog_parts: list[str] = []
    if ticket is not None:
        events = list(
            db.scalars(
                select(Event)
                .where(
                    Event.ticket_id == ticket.id,
                    Event.action.in_(("dialog", "reaction_sent")),
                )
                .order_by(Event.id)
            ).all()
        )
        for ev in events:
            if not ev.payload:
                continue
            try:
                payload = json.loads(ev.payload)
                if isinstance(payload, dict):
                    text = payload.get("text")
                    role = payload.get("role") or payload.get("kind") or ev.actor or "сообщение"
                    if text:
                        dialog_parts.append(f"{role}: {text}")
            except Exception:
                continue

    dialog_text = "\n".join(dialog_parts) if dialog_parts else "—"

    summary = ""
    service_name = None
    if request is not None:
        if request.subtasks and request.subtasks[0].summary:
            summary = request.subtasks[0].summary
            service_name = request.subtasks[0].service
        elif request.masked_text:
            summary = request.masked_text
        elif request.raw_text:
            summary = request.raw_text

    topic_map = {
        "wifi_guest": "wifi",
        "wifi_edu": "wifi",
        "wifi_corp": "wifi",
        "lms": "lms",
        "account": "password",
        "certs": "certs",
        "site": "other",
    }
    fallback_topic = topic_map.get(service_name or "", "other")

    try:
        prompt = DRAFT_KB_PROMPT.format(
            problem=summary or "—",
            dialog=dialog_text,
            resolution=resolution,
        )
        draft = llm.chat_structured(prompt, KbDraftResult)
        topic = (draft.topic or "other").strip().lower()
        if topic not in {"wifi", "password", "lms", "certs", "support", "other"}:
            topic = fallback_topic
        return {
            "ok": True,
            "title": draft.title.strip()[:100],
            "body": draft.body.strip(),
            "topic": topic,
            "draft_fallback": False,
        }
    except Exception as exc:
        logger.warning("draft_kb_article: сбой генерации через LLM, используется шаблон: %s", exc)
        title = f"Решение: {resolution}"[:100]
        prob_text = summary or (request.masked_text if request else "") or "обращение"
        body = f"Проблема: {prob_text}.\nРешение: {resolution}."
        return {
            "ok": True,
            "title": title,
            "body": body,
            "topic": fallback_topic,
            "draft_fallback": True,
        }


def simulate_wave(
    db: OrmSession,
    *,
    service: str,
    count: int = 3,
    subtask_id: int | None = None,
) -> dict[str, Any]:
    """Симуляция волны жалоб (FR-050): count однотипных обращений от разных
    тестовых пользователей → прогон каждого через incidents.evaluate.

    service — ключ классификатора (site/lms/wifi_guest/wifi_edu/wifi_corp),
    category волны фиксирована 'availability'. Синтетические авторы
    wave-user-{i+1}@edu.misis.ru (role='student', отдельная Session на каждого) —
    гарантия разных COALESCE(user_id, session_id) без зависимости от сида.
    Подзадача создаётся уже классифицированной (route='auto_check',
    priority='high', confidence=0.9) — LLM и HTTP не нужны.
    Возвращает {"ok": True, "incident_id": id|None, "created_requests": [...]}.
    """
    if service not in CHECK_TOOL_BY_SERVICE:
        raise ToolError(f"Неизвестный сервис волны: {service}")

    count = max(1, min(int(count), 50))
    created_request_ids: list[int] = []
    last_incident_id: int | None = None

    for i in range(count):
        user_email = f"wave-user-{i+1}@edu.misis.ru"
        user = db.scalar(select(User).where(User.email == user_email))
        if user is None:
            user = User(
                email=user_email,
                full_name=f"Студент Волна {i+1}",
                role="student",
            )
            db.add(user)
            db.flush()

        session_token = secrets.token_urlsafe(16)
        user_session = UserSession(id=session_token, user_id=user.id)
        db.add(user_session)
        db.flush()

        request_obj = Request(
            session_id=user_session.id,
            user_id=user.id,
            channel="web",
            raw_text=f"[тестовая волна] Жалоба {i+1}: не работает {service}",
            masked_text=f"[тестовая волна] Жалоба {i+1}: не работает {service}",
            lang="ru",
        )
        db.add(request_obj)
        db.flush()
        created_request_ids.append(request_obj.id)

        subtask = tickets.attach_subtask(
            db,
            request_obj,
            f"Недоступен {service} (симуляция {i+1})",
            service=service,
            category="availability",
            route="auto_check",
            priority="high",
            confidence=0.9,
            status=tickets.STATUS_IN_PROGRESS,
        )
        ticket = tickets.ensure_ticket(db, request_obj.id)
        incidents.evaluate(db, subtask=subtask, ticket=ticket)
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
        "ok": True,
        "incident_id": last_incident_id,
        "created_requests": created_request_ids,
    }


# ---------------------------------------------------------------------------
# T036 — summarize: LLM-саммари пакета эскалации (type="llm", FR-060)
# ---------------------------------------------------------------------------

class SummarizePayload(BaseModel):
    """JSON-ответ LLM для пакета эскалации (FR-060: суть, рекомендация, причина)."""

    summary: str = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    why_escalated: str = Field(min_length=1)


def collect_checks(db: OrmSession, request_id: int) -> list[dict[str, Any]]:
    """Сбор сводки проверок сервисов для обращения (FR-060)."""
    subtask_ids = db.scalars(
        select(Subtask.id).where(Subtask.request_id == request_id)
    ).all()
    if not subtask_ids:
        return []
    checks = db.scalars(
        select(ServiceCheck)
        .where(ServiceCheck.subtask_id.in_(subtask_ids))
        .order_by(ServiceCheck.id)
    ).all()
    results: list[dict[str, Any]] = []
    for check in checks:
        service = db.get(Service, check.service_id)
        service_name = service.name if service and service.name else "сервис"
        if check.http_code is not None:
            note = f"HTTP {check.http_code}, {check.latency_ms or 0} мс"
        else:
            state_str = "норма" if check.ok else "сбой"
            note = f"сеть в состоянии «{state_str}»"
        results.append({
            "service": service_name,
            "ok": bool(check.ok),
            "checked_at": (
                check.checked_at.isoformat()
                if check.checked_at
                else datetime.now(timezone.utc).isoformat()
            ),
            "note": note,
        })
    return results


SUMMARIZE_PROMPT = (
    "Ты — ассистент дежурного оператора техподдержки университета МИСИС. "
    "Сформируй пакет эскалации обращения для оператора (FR-060).\n\n"
    "Контекст обращения:\n"
    "- Текст обращения: {masked_text}\n"
    "{translation_info}"
    "- Проблема: {subtask_summary}\n"
    "- Причина эскалации: {route_reason}\n"
    "- Результаты автопроверок:\n{checks_summary}\n\n"
    "Сформируй:\n"
    "1. summary — краткая суть проблемы (1-2 предложения);\n"
    "2. recommendation — рекомендуемый следующий шаг дежурному оператору;\n"
    "3. why_escalated — почему обращение передано человеку.\n\n"
    'Верни строго один JSON-объект вида {{"summary": "...", "recommendation": "...", "why_escalated": "..."}} '
    "без пояснений и markdown."
)


def summarize(
    db: OrmSession,
    *,
    request_id: int,
    subtask_id: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Саммари пакета эскалации (FR-060, type='llm'): суть + проверки + рекомендация."""
    request = db.get(Request, request_id)
    if request is None:
        return {"ok": False, "error": f"Request id={request_id} не найден"}

    subtask = db.get(Subtask, subtask_id) if subtask_id is not None else None
    if subtask is None and request.subtasks:
        subtask = request.subtasks[0]

    checks = collect_checks(db, request_id)
    if checks:
        checks_lines = [
            f"- {c['service']}: {'ОК' if c['ok'] else 'СБОЙ'} ({c['note']})"
            for c in checks
        ]
        checks_summary = "\n".join(checks_lines)
    else:
        checks_summary = "Автопроверки не проводились"

    translation_info = ""
    if request.translation:
        translation_info = f"- Перевод на русский (язык {request.lang or 'en'}): {request.translation}\n"

    subtask_summary = subtask.summary if subtask and subtask.summary else (request.masked_text or "Обращение")
    route_reason = subtask.route_reason if subtask and subtask.route_reason else "Эскалация по правилам маршрутизации"

    prompt = SUMMARIZE_PROMPT.format(
        masked_text=request.masked_text or request.raw_text or "",
        translation_info=translation_info,
        subtask_summary=subtask_summary,
        route_reason=route_reason,
        checks_summary=checks_summary,
    )

    try:
        payload = llm.chat_structured(
            prompt,
            SummarizePayload,
            model=llm.generate_model_uri(),
        )
    except (llm.LLMUnavailable, Exception) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "summary": payload.summary.strip(),
        "checks": checks,
        "recommendation": payload.recommendation.strip(),
        "why_escalated": payload.why_escalated.strip(),
    }


ADMIN_INVOKABLE: tuple[str, ...] = ("check_site", "check_lms", "check_wifi", "simulate_wave")

# Реестр инструментов (FR-020): name → {type, fn, schema, description}
TOOLS: dict[str, dict[str, Any]] = {
    "simulate_wave": {
        "type": "simulation",
        "fn": simulate_wave,
        "schema": [
            {"name": "service", "type": "string"},
            {"name": "count", "type": "integer", "default": 3},
        ],
        "params": [
            {
                "name": "service",
                "type": "string",
                "required": True,
                "choices": ["site", "lms", "wifi_guest", "wifi_edu", "wifi_corp"],
                "description": "Сервис волны (slug)",
            },
            {
                "name": "count",
                "type": "integer",
                "required": False,
                "default": 3,
                "min": 1,
                "max": 10,
                "choices": None,
                "description": "Число обращений (1..10)",
            },
        ],
        "description": (
            "Симуляция волны: count однотипных обращений от разных "
            "тестовых пользователей → детектор инцидентов (FR-050)"
        ),
    },
    "check_site": {
        "type": "exec",
        "description": "HTTP-проверка misis.ru",
        "fn": check_site,
        "schema": [{"name": "service_id", "type": "integer | null"},
                   {"name": "subtask_id", "type": "integer | null"}],
        "params": [
            {
                "name": "url",
                "type": "string",
                "required": False,
                "default": "https://misis.ru",
                "choices": None,
                "description": "проверяемый URL (только штатный)",
            }
        ],
    },
    "check_lms": {
        "type": "exec",
        "description": "HTTP-проверка newlms.misis.ru",
        "fn": check_lms,
        "schema": [{"name": "service_id", "type": "integer | null"},
                   {"name": "subtask_id", "type": "integer | null"}],
        "params": [
            {
                "name": "url",
                "type": "string",
                "required": False,
                "default": "https://newlms.misis.ru",
                "choices": None,
                "description": "проверяемый URL (только штатный)",
            }
        ],
    },
    "check_wifi": {
        "type": "exec",
        "description": "Эмулируемая проверка Wi-Fi сети (состояние из services)",
        "fn": check_wifi,
        "schema": [{"name": "service_id", "type": "integer | null"},
                   {"name": "network", "type": "string", "default": "wifi_edu"}],
        "params": [
            {
                "name": "network",
                "type": "string",
                "required": True,
                "default": None,
                "choices": ["wifi_guest", "wifi_edu", "wifi_corp"],
                "description": "Wi-Fi сеть (slug)",
            }
        ],
    },
    "kb_agent": {
        "type": "llm",
        "description": "RAG-агент базы знаний: переформулировка, подзапросы, ответ строго по источникам",
        "fn": kb_agent,
        "schema": [
            {"name": "question", "type": "string"},
            {"name": "context", "type": "string | null"},
        ],
    },
    "summarize": {
        "type": "llm",
        "description": "LLM-саммари пакета эскалации: суть, сводка проверок, рекомендация (FR-060)",
        "fn": summarize,
        "schema": [
            {"name": "request_id", "type": "integer"},
            {"name": "subtask_id", "type": "integer"},
        ],
    },
    "order_certificate": {
        "type": "exec",
        "description": "Оформление заказа справки из каталога (FR-040/041)",
        "fn": order_certificate,
        "schema": [
            {"name": "user_id", "type": "integer"},
            {"name": "cert_type", "type": "string"},
        ],
    },
    "draft_kb_article": {
        "type": "llm",
        "description": "Черновик статьи БЗ по переписке и resolution (FR-063)",
        "fn": draft_kb_article,
        "schema": [
            {"name": "request_id", "type": "integer"},
            {"name": "resolution", "type": "string"},
        ],
    },
}


def dispatch(
    db: OrmSession,
    ticket_id: int | None,
    name: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Вызвать инструмент по имени с журналированием (FR-020, FR-061).

    Пишет tool_call (payload=params) перед вызовом и tool_result (payload=результат)
    после; возвращает результат инструмента. Неизвестный инструмент → KeyError
    (нода агента превращает в эскалацию).
    """
    tool = TOOLS.get(name)
    if tool is None:
        raise KeyError(f"Инструмент «{name}» не найден в реестре")
    actor = f"tool:{name}"
    log_event(db, ticket_id=ticket_id, actor=actor, action="tool_call", payload=params or {})
    fn: Callable[..., dict[str, Any]] = tool["fn"]
    result = fn(db, **(params or {}))
    log_event(db, ticket_id=ticket_id, actor=actor, action="tool_result", payload=result)
    return result


def call_tool(
    db: OrmSession,
    name: str,
    params: dict[str, Any] | None = None,
    *,
    ticket_id: int | None = None,
    subtask_id: int | None = None,
    tool_calls: int = 0,
) -> ToolCall:
    """Диспетчер вызова инструмента: журнал events (FR-061) + инкремент счётчика (FR-021).

    Сбой исполнения (например, обрыв соединения) фиксируется как результат
    {"ok": False, "error": ...} — возможный сбой сервиса, а не падение пайплайна.
    """
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"Инструмент «{name}» не найден в реестре")
    params = dict(params or {})
    log_event(
        db, ticket_id=ticket_id, actor=f"tool:{name}", action="tool_call",
        payload={"type": tool["type"], "params": params},
    )
    fn: Callable[..., dict[str, Any]] = tool["fn"]
    try:
        call_kwargs = dict(params)
        sig = inspect.signature(fn)
        has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if "subtask_id" in sig.parameters or has_var_keyword:
            if "subtask_id" not in call_kwargs and subtask_id is not None:
                call_kwargs["subtask_id"] = subtask_id
        result = fn(db, **call_kwargs)
    except ToolError:
        raise
    except Exception as exc:  # инструмент вернул ошибку — возможный сбой сервиса
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    log_event(
        db, ticket_id=ticket_id, actor=f"tool:{name}", action="tool_result",
        payload=result,
    )
    return ToolCall(result=result, tool_calls=tool_calls + 1)
