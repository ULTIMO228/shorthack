"""Агент разбора обращения: LangGraph StateGraph поверх нод пайплайна (US1–US3, US6).

Граф: normalize → split → classify → triggers → check_duplicate → (дубль → respond)
→ ensure_ticket → incident_check ──(incident_bound)──→ next_subtask ─────────────┐
                                 ──(auto_check)──────→ execute_route ────────────┤
                                 ──(kb)──────────────→ execute_route ────────────┤
                                 ──(прочее)──────────→ escalate ─────────────────┤
                                  next_subtask → classify | respond

US2 (T026): нода execute_route для auto_check сама вызывает инструмент проверки
через диспетчер tools.call_tool (misis.ru / newlms.misis.ru — реальные HTTP-проверки,
Wi-Fi — эмуляция состояния services): норма → шаблонный уточняющий вопрос (FR-025),
сбой → шаблонное извещение outage_notice без вызова LLM (FR-026, SC-004 ≤ 2 c),
приоритет → critical по правилу FR-011 (T027).

US3 (T031): ветка route=kb в execute_route вызывает RAG-инструмент tools.kb_agent
(переформулировка → подзапросы → retrieval → ответ строго по источникам, FR-031):
найдено → реакция answer со списком статей-источников, заявка «решена»; пустая
выдача или недоступность модели — эскалация оператору (FR-032, FR-013).

FR-021: счётчик tool_calls в state с лимитом 3 — при превышении нода эскалации.
FR-013: при LLMUnavailable классификация уходит в безопасный маршрут (escalate).
Ветка cert_order (заказ, US4, T035) оформляет справку через order_certificate; гость → auth_required.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app import certs, incidents, llm, tickets, tools, triggers
from app.events import log_event
from app.models import (
    KbArticle,
    OutboundMessage,
    Request,
    Subtask,
    TgLink,
    Ticket,
    User,
)
from app.schemas import ClassifierResult, SplitResult, SubtaskDraft

# ---------------------------------------------------------------------------
# Состояние графа (research.md R2)
# ---------------------------------------------------------------------------

MAX_TOOL_CALLS = 3  # FR-021: не более 3 вызовов инструментов на обращение


class AgentState(TypedDict, total=False):
    db: OrmSession
    session_id: str
    user_id: int | None  # null = гость (FR-016)
    channel: str
    text: str  # исходный текст обращения или ответа в диалоге
    request_id: int
    ticket_id: int
    incident_bound: bool  # признак привязки подзадачи к инциденту (US6)
    duplicate: bool  # true — дублем оказались ВСЕ подзадачи (FR-014)
    dup_count: int  # число подзадач-дублей текущего обращения
    dup_next: str  # маршрут после ноды дедупликации: respond | classify | ensure_ticket
    subtask_ids: list[int]
    subtask_index: int
    route: str  # маршрут текущей подзадачи
    reactions: list[dict[str, str]]
    tool_calls: int
    lang: str
    masked_text: str
    work_text: str  # текст для классификатора: перевод или маскированный оригинал
    result: dict[str, Any]


# ---------------------------------------------------------------------------
# T015 — нода normalize: мат, язык, перевод
# ---------------------------------------------------------------------------

# Корни нецензурной лексики; маскирование = первая буква + «*» (FR-002)
MAT_ROOTS = (
    "хуй", "хуя", "хуе", "хуё", "пизд", "бляд", "блят", "бля",
    "сука", "суки", "сукой", "суч", "пидор", "пидар", "пидерас",
    "ебан", "ебат", "ебал", "заеб", "наеб", "выеб", "ёбан", "ебло",
    "мудак", "мудил", "мудозв", "гандон", "шлюх", "тварь", "урод",
    # английская лексика (контрольный текст «мат+англ.» из quickstart)
    "fuck", "shit", "bitch", "dick", "cunt", "asshole", "bastard",
)
# Границы по буквам обеих раскладок: не съедаем «pass» корнем «ass»
MAT_RE = re.compile(
    "|".join(
        rf"(?<![a-zа-яё])(?:{re.escape(root)})[a-zа-яё]*" for root in MAT_ROOTS
    ),
    re.IGNORECASE,
)


def mask_profanity(text: str) -> str:
    """Замазать мат: первая буква слова + звёздочки (FR-002)."""

    def repl(match: re.Match[str]) -> str:
        word = match.group(0)
        return word[0] + "*" * (len(word) - 1)

    return MAT_RE.sub(repl, text)


def detect_lang(text: str) -> str:
    """Детект языка по доле кириллицы: ≥30% букв — ru, иначе en (демо-эвристика)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "ru"
    cyrillic = sum(1 for c in letters if "\u0430" <= c.lower() <= "\u044f" or c.lower() == "ё")
    return "ru" if cyrillic / len(letters) >= 0.3 else "en"


def translate_to_ru(text: str) -> str | None:
    """Перевод не-ru текста через LLM (FR-003); при сбое API — None, работаем с оригиналом."""
    try:
        return llm.chat(
            "Переведи текст обращения в службу технической поддержки на русский язык. "
            "Верни только перевод без пояснений и кавычек.\n\n" + text
        )
    except llm.LLMUnavailable:
        return None


def node_normalize(state: AgentState) -> dict[str, Any]:
    """Нода normalize (T015): маскирование мата, определение языка, перевод на ru."""
    db = state["db"]
    text = state["text"]
    masked = mask_profanity(text)
    lang = detect_lang(text)
    translation = translate_to_ru(text) if lang != "ru" else None
    request_id = state.get("request_id")
    if not request_id:
        request = Request(
            session_id=state["session_id"],
            user_id=state.get("user_id"),
            channel=state["channel"],
            raw_text=text,
            masked_text=masked,
            lang=lang,
            translation=translation,
        )
        db.add(request)
        db.commit()
        request_id = request.id
    return {
        "request_id": request_id,
        "masked_text": masked,
        "lang": lang,
        "work_text": translation or masked,
    }


# ---------------------------------------------------------------------------
# T016 — нода split: разбиение составного обращения на подзадачи
# ---------------------------------------------------------------------------

SPLIT_PROMPT = (
    "Ты — сплиттер обращений в службу технической поддержки университета МИСИС. "
    "Раздели текст обращения на отдельные самостоятельные проблемы, если их несколько "
    "(например: разные сервисы, вопрос + жалоба, вопрос + заказ документа). "
    "Если проблема одна — верни список из одного элемента. "
    'Верни строго один JSON-объект вида {{"subtasks": [{{"summary": "суть проблемы одной строкой"}}]}} '
    "без пояснений и markdown.\n\nТекст обращения:\n{text}"
)


def node_split(state: AgentState) -> dict[str, Any]:
    """Нода split (T016): LLM разбивает обращение на подзадачи; при сбое — одна подзадача."""
    db = state["db"]
    request = db.get(Request, state["request_id"])
    try:
        result = llm.chat_structured(SPLIT_PROMPT.format(text=state["work_text"]), SplitResult)
        drafts = [d.summary.strip() for d in result.subtasks if d.summary.strip()]
    except llm.LLMUnavailable:
        drafts = []
    if not drafts:
        drafts = [state["work_text"]]
    subtask_ids = state.get("subtask_ids", [])
    for summary in drafts:
        subtask = tickets.attach_subtask(db, request, summary, status=tickets.STATUS_NEW)
        subtask_ids.append(subtask.id)
    return {"subtask_ids": subtask_ids, "subtask_index": 0}


# ---------------------------------------------------------------------------
# T017 — нода classify: маршрут + атрибуты подзадачи
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = (
    "Ты — классификатор обращений в техподдержку университета МИСИС. Классифицируй одну проблему.\n\n"
    "Маршруты:\n"
    "- auto_check — жалоба на недоступность/сбой сервиса: сайт misis.ru, newlms.misis.ru, "
    "Wi-Fi сети MISIS-Guest / MISIS-EDU / MISIS-CORP;\n"
    "- kb — вопрос «как сделать»: подключение, пароль, настройки, регламенты;\n"
    "- cert_order — запрос справки: с места учёбы, о выплатах, справка-вызов, в военкомат и т.п.;\n"
    "- escalate — всё остальное: неоднозначные случаи, персональные проблемы доступа, "
    "жалобы, темы вне компетенции техподдержки.\n\n"
    "Сервисы: site / lms / wifi_guest / wifi_edu / wifi_corp / account / certs / other.\n"
    "Категории: availability / access / howto / cert_order / incident_report / other.\n"
    "Приоритеты: critical — массовый подтверждённый сбой; high — недоступность сервиса для одного "
    "пользователя; medium — стандартный вопрос; low — консультация.\n"
    "confidence — уверенность в правильности маршрута, число 0..1.\n"
    "reason — обоснование выбранного маршрута одним предложением.\n\n"
    'Верни строго один JSON-объект с полями route, service, category, priority, confidence, reason.\n\n'
    "Проблема: {summary}\nКонтекст обращения: {context}"
)


def node_classify(state: AgentState) -> dict[str, Any]:
    """Нода classify (T017): промпт классификатора + валидация схемой ClassifierResult.

    При недоступности модели — безопасный маршрут escalate (FR-013).
    """
    db = state["db"]
    subtask = db.get(Subtask, state["subtask_ids"][state["subtask_index"]])
    prompt = CLASSIFY_PROMPT.format(summary=subtask.summary, context=state["work_text"])
    try:
        result: ClassifierResult = llm.chat_structured(
            prompt, ClassifierResult, model=llm.classify_model_uri()
        )
        route, service, category = result.route, result.service, result.category
        priority, confidence, reason = result.priority, result.confidence, result.reason
    except llm.LLMUnavailable as exc:
        route, service, category = "escalate", None, "other"
        priority, confidence = "high", 0.0
        reason = f"Модель недоступна ({exc}) — безопасный маршрут (FR-013)"
    subtask.service = service
    subtask.category = category
    subtask.priority = priority
    subtask.confidence = confidence
    subtask.route = route
    subtask.route_reason = reason
    db.commit()
    log_event(
        db, actor="agent", action="classified",
        payload={
            "subtask_id": subtask.id, "route": route, "service": service,
            "category": category, "priority": priority, "confidence": confidence,
            "reason": reason,
        },
    )
    return {"route": route}


# ---------------------------------------------------------------------------
# Ноды триггеров, дедупликации, маршрутов
# ---------------------------------------------------------------------------

def node_triggers(state: AgentState) -> dict[str, Any]:
    """Нода triggers (T018): детерминированные правила поверх ответа классификатора."""
    db = state["db"]
    subtask = db.get(Subtask, state["subtask_ids"][state["subtask_index"]])
    decision = triggers.apply(
        f"{state['work_text']}\n{subtask.summary}",
        route=subtask.route, priority=subtask.priority, confidence=subtask.confidence or 0.0,
    )
    if decision.triggered:
        subtask.route = decision.route
        subtask.priority = decision.priority
        subtask.route_reason = (
            f"{subtask.route_reason} (триггеры: {', '.join(decision.triggered)})"
        )
        db.commit()
        log_event(
            db, actor="system", action="trigger",
            payload={"subtask_id": subtask.id, "rules": decision.triggered,
                     "route": decision.route, "priority": decision.priority},
        )
    return {"route": decision.route}


def node_check_duplicate(state: AgentState) -> dict[str, Any]:
    """Нода дедупликации (FR-014) на уровне подзадачи.

    Совпадение service+category с открытой заявкой автора: текущая подзадача
    привязывается к ней (request.duplicate_of_ticket_id), помечается решённой,
    добавляется реакция со ссылкой на существующий тикет — и разбор ПРОДОЛЖАЕТСЯ
    со следующей подзадачи. Флаг duplicate=True выставляется только когда дублем
    оказались ВСЕ подзадачи (тогда пайплайн сразу идёт в respond). Без этого
    составное обращение с дублем первой подзадачи уходило в respond с непроклас-
    сифицированными остальными и падало 500 (SubtaskOut без route).
    """
    db = state["db"]
    index = state["subtask_index"]
    subtask = db.get(Subtask, state["subtask_ids"][index])
    ticket = tickets.find_open_duplicate(
        db, user_id=state.get("user_id"), session_id=state["session_id"],
        service=subtask.service, category=subtask.category,
        exclude_request_id=state["request_id"],
    )
    if ticket is None:
        return {"duplicate": False, "dup_next": "ensure_ticket"}
    request = db.get(Request, state["request_id"])
    if request.duplicate_of_ticket_id is None:
        request.duplicate_of_ticket_id = ticket.id
    subtask.status = tickets.STATUS_RESOLVED
    db.commit()
    reaction = {
        "kind": "answer",
        "text": (
            f"Это обращение по той же теме, что и ваша заявка "
            f"{tickets.ticket_number(ticket)} — она уже в работе, объединили с ней. "
            "Отдельной заявки не создавали."
        ),
    }
    log_event(
        db, ticket_id=ticket.id, actor="agent", action="duplicate_attached",
        payload={"request_id": state["request_id"], "subtask_id": subtask.id},
    )
    dup_count = state.get("dup_count", 0) + 1
    all_duplicates = dup_count == len(state["subtask_ids"])
    return {
        "duplicate": all_duplicates,
        "dup_count": dup_count,
        "subtask_index": index + 1,
        "reactions": state.get("reactions", []) + [reaction],
        "dup_next": "respond" if all_duplicates else "classify",
    }


def node_ensure_ticket(state: AgentState) -> dict[str, Any]:
    ticket = tickets.ensure_ticket(state["db"], state["request_id"])
    return {"ticket_id": ticket.id}


def node_incident_check(state: AgentState) -> dict[str, Any]:
    """Нода детектора инцидентов (US6, T040): evaluate(subtask, ticket).

    Ветка FR-052/FR-050: привязка/создание инцидента → шаблонная реакция
    outage_notice без LLM, тикет «решена», маршрутная обработка подзадачи
    пропускается (инструмент не тратит лимит FR-021). Иначе — обычный
    dispatch по route (conditional-ребро ниже).
    """
    db = state["db"]
    subtask = db.get(Subtask, state["subtask_ids"][state["subtask_index"]])
    ticket = db.get(Ticket, state["ticket_id"])
    reaction = incidents.evaluate(db, subtask=subtask, ticket=ticket)
    if reaction is None:
        return {"incident_bound": False}
    tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS)
    tickets.set_status(db, ticket, tickets.STATUS_RESOLVED)
    log_event(db, ticket_id=ticket.id, actor="agent",
              action="reaction_sent", payload=reaction)
    return {
        "reactions": state.get("reactions", []) + [reaction],
        "subtask_index": state["subtask_index"] + 1,
        "incident_bound": True,
    }


# Шаблоны реакций из базы знаний (FR-030) с локальными запасными копиями для тестов
TEMPLATE_CLARIFY = "Шаблон: уточняющий вопрос по доступности"
TEMPLATE_OUTAGE = "Шаблон: извещение о подтверждённом сбое"
TEMPLATE_ESCALATION = "Шаблон: эскалация оператору"
FALLBACK_TEMPLATES = {
    TEMPLATE_CLARIFY: (
        "Автоматическая проверка показывает, что {service} доступен в штатном режиме. "
        "Уточните, пожалуйста: ошибка видна на всех устройствах или только на одном? "
        "Какой текст ошибки отображается?"
    ),
    TEMPLATE_OUTAGE: (
        "Мы уже знаем о проблеме: {service} сейчас недоступен "
        "(подтверждено автоматической проверкой). Специалисты работают над "
        "восстановлением. Приносим извинения за неудобства — отдельное обращение "
        "оставлять не нужно. Уведомим о восстановлении."
    ),
    TEMPLATE_ESCALATION: (
        "Передал обращение дежурному оператору. Саммари, результаты автоматических "
        "проверок и история диалога уже прикреплены — ответ в течение рабочего времени. "
        "Номер обращения: {ticket}."
    ),
}

SERVICE_DISPLAY = {
    "site": "сайт misis.ru",
    "lms": "newlms.misis.ru (LMS)",
    "wifi_guest": "Wi-Fi MISIS-Guest",
    "wifi_edu": "Wi-Fi MISIS-EDU",
    "wifi_corp": "Wi-Fi MISIS-CORP",
    "account": "корпоративная учётная запись",
    "certs": "справки",
}


def render_template(db: OrmSession, title: str, **variables: Any) -> str:
    """Текст шаблона из kb_articles; в тестах без сида — запасная копия."""
    body = db.scalar(
        select(KbArticle.body).where(KbArticle.kind == "template", KbArticle.title == title)
    )
    if body is None:
        body = FALLBACK_TEMPLATES[title].format(**variables) if variables else FALLBACK_TEMPLATES[title]
        return body
    for key, value in variables.items():
        body = body.replace("{{" + key + "}}", str(value))
    return body


def node_execute_route(state: AgentState) -> dict[str, Any]:
    """Нода execute_route: auto_check (US2, T026) или kb — RAG-агент (US3, T031).

    auto_check: инструмент выбирается по сервису подзадачи (site/lms → check_site/
    check_lms, wifi_* → check_wifi) через tools.resolve_check_tool и вызывается через
    диспетчер tools.call_tool — с журналом tool_call/tool_result и инкрементом
    счётчика вызовов (FR-021; лимит 3 — эскалация выше). Результат пишется
    в service_checks внутри инструмента.
    Норма → шаблонный уточняющий вопрос, тикет «ждёт ответа пользователя» (FR-025).
    Сбой → шаблонное извещение outage_notice строго без LLM (FR-026, SC-004 ≤ 2 c),
    приоритет → critical по правилу FR-011 (T027), тикет «решена», заявка
    зафиксирована с результатом проверки.

    kb: инструмент kb_agent (tools.py) — переформулировка, подзапросы, retrieval
    и ответ строго по источникам (FR-031); пустая выдача → эскалация (FR-032).
    """
    if state.get("tool_calls", 0) >= MAX_TOOL_CALLS:
        return node_escalate(state)  # FR-021: превышение лимита — эскалация
    if state.get("route") == "kb":
        return _execute_kb(state)
    if state.get("route") == "cert_order":
        return node_cert_order(state)
    db = state["db"]
    subtask = db.get(Subtask, state["subtask_ids"][state["subtask_index"]])
    if subtask and subtask.route == "cert_order":
        return node_cert_order(state)
    ticket = db.get(Ticket, state["ticket_id"])
    subtask.status = tickets.STATUS_IN_PROGRESS
    db.commit()
    resolved = tools.resolve_check_tool(subtask.service)
    if resolved is None:
        # сервис не поддаётся автопроверке — безопасный маршрут (FR-013)
        return node_escalate(state)
    tool_name, params = resolved
    display = SERVICE_DISPLAY.get(subtask.service or "", subtask.service or "сервис")
    call = tools.call_tool(
        db, tool_name, params,
        ticket_id=ticket.id, subtask_id=subtask.id,
        tool_calls=state.get("tool_calls", 0),
    )
    check_ok = bool(call.result.get("ok"))
    if check_ok:
        text = render_template(db, TEMPLATE_CLARIFY, service=display)
        reaction = {"kind": "clarification", "text": text}
        tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS)
        tickets.set_status(db, ticket, tickets.STATUS_WAITING_USER)
    else:
        # подтверждённый сбой: шаблон без LLM, приоритет critical (FR-011), «решена»
        service_name = call.result.get("service") or display
        decision = triggers.apply_outage(
            subtask.route or "auto_check", subtask.priority or "medium",
            service_down=True, service=service_name,
        )
        subtask.priority = decision.priority
        subtask.route_reason = (
            f"{subtask.route_reason} (триггеры: {', '.join(decision.triggered)})"
        )
        subtask.status = tickets.STATUS_RESOLVED
        db.commit()
        log_event(
            db, actor="system", action="trigger",
            payload={"subtask_id": subtask.id, "rules": decision.triggered,
                     "priority": decision.priority},
        )
        text = render_template(db, TEMPLATE_OUTAGE, service=display)
        reaction = {"kind": "outage_notice", "text": text}
        tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS)
        tickets.set_status(db, ticket, tickets.STATUS_RESOLVED)
    log_event(db, ticket_id=ticket.id, actor="agent", action="reaction_sent", payload=reaction)
    return {
        "reactions": state.get("reactions", []) + [reaction],
        "subtask_index": state["subtask_index"] + 1,
        "tool_calls": call.tool_calls,
    }


def _execute_kb(state: AgentState) -> dict[str, Any]:
    """Ветка маршрута kb (US3, T031): RAG-агент по базе знаний (FR-031/FR-032).

    Вызов инструмента kb_agent через диспетчер tools.call_tool (журнал FR-061,
    инкремент счётчика FR-021). Найденные статьи → реакция answer с текстом
    ответа и списком источников, подзадача и заявка «решена». Пустая выдача
    или недоступность модели (ok=false) — эскалация оператору (FR-032/FR-013):
    ответ без источника не выдумываем (SC-006).
    """
    db = state["db"]
    subtask = db.get(Subtask, state["subtask_ids"][state["subtask_index"]])
    ticket = db.get(Ticket, state["ticket_id"])
    subtask.status = tickets.STATUS_IN_PROGRESS
    db.commit()
    call = tools.call_tool(
        db, "kb_agent",
        {"question": subtask.summary or state["work_text"], "context": state.get("work_text")},
        ticket_id=ticket.id, subtask_id=subtask.id,
        tool_calls=state.get("tool_calls", 0),
    )
    result = call.result
    if not result.get("ok") or not result.get("found"):
        # FR-032: пустая выдача по базе; FR-013: модель недоступна — к оператору
        reason = result.get("error") or "пустая выдача"
        prefix = subtask.route_reason or "kb"
        subtask.route_reason = f"{prefix} (RAG: {reason})"
        db.commit()
        updates = node_escalate(state)
        updates["tool_calls"] = call.tool_calls
        return updates
    reaction = {
        "kind": "answer",
        "text": result["answer"],
        "sources": result.get("sources", []),
    }
    subtask.status = tickets.STATUS_RESOLVED
    db.commit()
    tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS)
    tickets.set_status(db, ticket, tickets.STATUS_RESOLVED)
    log_event(db, ticket_id=ticket.id, actor="agent", action="reaction_sent", payload=reaction)
    return {
        "reactions": state.get("reactions", []) + [reaction],
        "subtask_index": state["subtask_index"] + 1,
        "tool_calls": call.tool_calls,
    }


def node_cert_order(state: AgentState) -> dict[str, Any]:
    """Маршрут cert_order (US4, T035): регламент из БЗ → заказ → cert_ordered.

    Гость (user_id=None) → node_escalate: реакция auth_required, заказ не создаётся
    (FR-016/FR-043). Тип справки — детерминированно через certs.resolve_cert_type
    (без LLM); не распознан → уточняющий вопрос с вариантами каталога, заказ не создаётся.
    Заказ выполняется инструментом order_certificate через диспетчер tools (FR-040),
    вызов журналируется (FR-061), счётчик tool_calls растёт на 1 (FR-021).
    Успех → реакция cert_ordered, подзадача и заявка «решена».
    """
    if state.get("user_id") is None:
        return node_escalate(state)

    db = state["db"]
    subtask = db.get(Subtask, state["subtask_ids"][state["subtask_index"]])
    ticket = db.get(Ticket, state["ticket_id"])
    subtask.status = tickets.STATUS_IN_PROGRESS
    db.commit()

    text_to_check = f"{state.get('work_text', '')}\n{subtask.summary or ''}"
    cert_type = certs.resolve_cert_type(text_to_check)

    if cert_type is None:
        titles = [item["title"] for item in certs.catalog_items()]
        text = "Уточните, какая справка нужна: " + ", ".join(titles) + "."
        reaction = {"kind": "clarification", "text": text}
        tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS)
        tickets.set_status(db, ticket, tickets.STATUS_WAITING_USER)
        log_event(db, ticket_id=ticket.id, actor="agent", action="reaction_sent", payload=reaction)
        return {
            "reactions": state.get("reactions", []) + [reaction],
            "subtask_index": state["subtask_index"] + 1,
            "tool_calls": state.get("tool_calls", 0),
        }

    call = tools.call_tool(
        db,
        "order_certificate",
        {"user_id": state["user_id"], "cert_type": cert_type},
        ticket_id=ticket.id,
        subtask_id=subtask.id,
        tool_calls=state.get("tool_calls", 0),
    )
    if not call.result.get("ok"):
        subtask.route_reason = f"{subtask.route_reason} (cert tool error: {call.result.get('error')})"
        db.commit()
        updates = node_escalate(state)
        updates["tool_calls"] = call.tool_calls
        return updates

    order_id = call.result["order_id"]
    title = certs.title_of(cert_type)
    text = f"Заказ оформлен: {title}. Номер заказа: #{order_id}. Статус: «не обработана»."
    reg = certs.get_regulation(db)
    if reg and reg.body:
        sentences = [s.strip() for s in reg.body.split(".") if s.strip()]
        reg_fragment = ". ".join(sentences[:2]) + "."
        text += f"\n\n{reg_fragment}"

    reaction = {"kind": "cert_ordered", "text": text}
    subtask.status = tickets.STATUS_RESOLVED
    db.commit()
    tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS)
    tickets.set_status(db, ticket, tickets.STATUS_RESOLVED)
    log_event(db, ticket_id=ticket.id, actor="agent", action="reaction_sent", payload=reaction)
    return {
        "reactions": state.get("reactions", []) + [reaction],
        "subtask_index": state["subtask_index"] + 1,
        "tool_calls": call.tool_calls,
    }


ESCALATION_FALLBACK_RECOMMENDATION = (
    "Передать дежурному оператору: запросить у пользователя детали и диагностику по журналу проверок."
)


def build_escalation_package(
    db: OrmSession,
    request: Request | None,
    subtask: Subtask | None,
    summarize_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Сборка пакета эскалации FR-060 (саммари, проверки, рекомендация, причина)."""
    masked_text = (request.masked_text if request else None) or (request.raw_text if request else "") or ""
    if summarize_result and summarize_result.get("ok"):
        return {
            "summary": (
                summarize_result.get("summary")
                or (subtask.summary if subtask else None)
                or masked_text
            ),
            "checks": summarize_result.get("checks", []),
            "recommendation": (
                summarize_result.get("recommendation")
                or ESCALATION_FALLBACK_RECOMMENDATION
            ),
            "why_escalated": (
                summarize_result.get("why_escalated")
                or (subtask.route_reason if subtask else None)
                or "Передано дежурному оператору"
            ),
        }
    req_id = request.id if request else (subtask.request_id if subtask else None)
    checks = tools.collect_checks(db, req_id) if req_id is not None else []
    return {
        "summary": (subtask.summary if subtask else None) or masked_text,
        "checks": checks,
        "recommendation": ESCALATION_FALLBACK_RECOMMENDATION,
        "why_escalated": (
            (subtask.route_reason if subtask else None)
            or "Передано дежурному оператору (FR-013)"
        ),
    }


def notify_duty_operator(
    db: OrmSession,
    ticket: Ticket,
    package: dict[str, Any],
) -> OutboundMessage | None:
    """Уведомление дежурному оператору в outbound_messages (FR-060)."""
    operator = db.scalar(
        select(User).where(User.role == "operator").order_by(User.id)
    )
    if operator is None:
        chat_id = -1
    else:
        tg_link = db.scalar(
            select(TgLink).where(TgLink.user_id == operator.id, TgLink.state == "idle")
        )
        if tg_link and tg_link.chat_id is not None:
            chat_id = tg_link.chat_id
        else:
            chat_id = -operator.id

    t_num = tickets.ticket_number(ticket)
    summary_text = package.get("summary", "")
    why_text = package.get("why_escalated", "")
    text = f"Эскалация {t_num}: {summary_text} ({why_text})"
    msg = OutboundMessage(
        chat_id=chat_id,
        text=text,
        status="pending",
    )
    db.add(msg)
    db.commit()
    return msg


def node_escalate(state: AgentState) -> dict[str, Any]:
    """Эскалация оператору / auth_required (FR-016) для маршрутов вне auto_check."""
    db = state["db"]
    subtask = db.get(Subtask, state["subtask_ids"][state["subtask_index"]])
    ticket = db.get(Ticket, state["ticket_id"])
    req_id = (
        state.get("request_id")
        or (ticket.request_id if ticket else None)
        or (subtask.request_id if subtask else None)
    )
    request = db.get(Request, req_id) if req_id is not None else None
    if subtask.route == "cert_order" and state.get("user_id") is None:
        # FR-016/FR-043: заказ требует авторизации — действие не выполняется
        reaction = {
            "kind": "auth_required",
            "text": "Для заказа справки войдите по корпоративной почте МИСИС",
        }
        subtask.status = tickets.STATUS_RESOLVED
        db.commit()
        tickets.set_status(db, ticket, tickets.STATUS_RESOLVED)
        log_event(db, ticket_id=ticket.id, actor="agent", action="reaction_sent", payload=reaction)
        return {
            "reactions": state.get("reactions", []) + [reaction],
            "subtask_index": state["subtask_index"] + 1,
        }

    # Эскалация оператору (FR-060):
    # 1. Защита статусной цепочки: если тикет открыт — переводим в STATUS_IN_PROGRESS
    if ticket.status in tickets.OPEN_STATUSES:
        tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS)
    subtask.status = tickets.STATUS_IN_PROGRESS
    db.commit()

    # 2. Вызов summarize или fallback
    tool_calls = state.get("tool_calls", 0)
    package: dict[str, Any]
    if tool_calls < MAX_TOOL_CALLS and request is not None and subtask is not None:
        call = tools.call_tool(
            db,
            "summarize",
            {"request_id": request.id, "subtask_id": subtask.id},
            ticket_id=ticket.id,
            subtask_id=subtask.id,
            tool_calls=tool_calls,
        )
        tool_calls = call.tool_calls
        if call.result.get("ok"):
            package = build_escalation_package(db, request, subtask, summarize_result=call.result)
        else:
            package = build_escalation_package(db, request, subtask, summarize_result=None)
    else:
        package = build_escalation_package(db, request, subtask, summarize_result=None)

    # 3. Сохранение пакета в журнал (FR-061)
    log_event(
        db,
        ticket_id=ticket.id,
        actor="agent",
        action="escalation_package",
        payload=package,
    )

    # 4. Уведомление дежурному оператору (только при первичной эскалации)
    if not ticket.escalated:
        notify_duty_operator(db, ticket, package)

    ticket.escalated = True
    db.commit()

    log_event(
        db,
        ticket_id=ticket.id,
        actor="agent",
        action="escalated",
        payload={"subtask_id": subtask.id, "route": subtask.route},
    )

    text = render_template(db, TEMPLATE_ESCALATION, ticket=tickets.ticket_number(ticket))
    reaction = {"kind": "escalated", "text": text}
    log_event(db, ticket_id=ticket.id, actor="agent", action="reaction_sent", payload=reaction)

    return {
        "reactions": state.get("reactions", []) + [reaction],
        "subtask_index": state["subtask_index"] + 1,
        "tool_calls": tool_calls,
    }


def node_next_subtask(state: AgentState) -> str:
    """Условное ребро: следующая подзадача или финальный ответ."""
    if state["subtask_index"] < len(state["subtask_ids"]):
        return "classify"
    return "respond"


def node_respond(state: AgentState) -> dict[str, Any]:
    """Сборка результата пайплайна для роутера (формат contracts/api.md)."""
    db = state["db"]
    request = db.get(Request, state["request_id"])
    subtasks = db.scalars(
        select(Subtask).where(Subtask.request_id == request.id).order_by(Subtask.position)
    ).all()
    if state.get("duplicate"):
        ticket = db.get(Ticket, request.duplicate_of_ticket_id)
    else:
        ticket = db.get(Ticket, state["ticket_id"])
    result = {
        "request_id": request.id,
        "duplicate": bool(state.get("duplicate")),
        "subtasks": subtasks,
        "reactions": state.get("reactions", []),
        "ticket": ticket,
    }
    return {"result": result}


# ---------------------------------------------------------------------------
# Сборка графа (T020)
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """StateGraph: normalize → split → classify → triggers → check_duplicate → маршруты."""
    graph = StateGraph(AgentState)
    graph.add_node("normalize", node_normalize)
    graph.add_node("split", node_split)
    graph.add_node("classify", node_classify)
    graph.add_node("triggers", node_triggers)
    graph.add_node("check_duplicate", node_check_duplicate)
    graph.add_node("ensure_ticket", node_ensure_ticket)
    graph.add_node("incident_check", node_incident_check)
    graph.add_node("execute_route", node_execute_route)
    graph.add_node("escalate", node_escalate)
    graph.add_node("respond", node_respond)

    graph.add_edge(START, "normalize")
    graph.add_edge("normalize", "split")
    graph.add_edge("split", "classify")
    graph.add_edge("classify", "triggers")
    graph.add_edge("triggers", "check_duplicate")
    graph.add_conditional_edges(
        "check_duplicate",
        lambda state: state.get("dup_next", "ensure_ticket"),
    )
    graph.add_edge("ensure_ticket", "incident_check")
    graph.add_conditional_edges(
        "incident_check",
        lambda state: (
            node_next_subtask(state)
            if state.get("incident_bound")
            else (
                "execute_route"
                if state.get("route") in ("auto_check", "kb", "cert_order")
                else "escalate"
            )
        ),
    )
    graph.add_conditional_edges("execute_route", lambda state: node_next_subtask(state))
    graph.add_conditional_edges("escalate", lambda state: node_next_subtask(state))
    graph.add_edge("respond", END)
    return graph.compile()


@lru_cache(maxsize=1)
def get_graph():
    """Скомпилированный граф (ноды чистые, кэширование безопасно)."""
    return build_graph()


def run_pipeline(
    db: OrmSession,
    user_session,
    channel: str,
    text: str,
    request: Request | None = None,
) -> dict[str, Any]:
    """Прогнать обращение (или ответ в диалоге) через граф; вернуть result из respond-ноды."""
    initial: AgentState = {
        "db": db,
        "session_id": user_session.id,
        "user_id": user_session.user_id,
        "channel": channel,
        "text": text,
        "request_id": request.id if request is not None else 0,
        "duplicate": False,
        "dup_count": 0,
        "subtask_ids": [],
        "subtask_index": 0,
        "reactions": [],
        "tool_calls": 0,
    }
    final = get_graph().invoke(initial)
    return final["result"]
