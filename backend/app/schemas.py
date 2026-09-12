"""Pydantic v2 DTO для эндпоинтов (contracts/api.md) + схема ответа классификатора."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Сплиттер (LLM): разделение обращения на подзадачи
# ---------------------------------------------------------------------------

class SubtaskDraft(BaseModel):
    summary: str = Field(min_length=1)


class SplitResult(BaseModel):
    """JSON-ответ сплиттера: список самостоятельных проблем (≥1)."""

    subtasks: list[SubtaskDraft] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Классификатор (LLM): JSON-ответ валидируется этой схемой
# ---------------------------------------------------------------------------

class ClassifierResult(BaseModel):
    """JSON-ответ классификатора: маршрут + атрибуты подзадачи + обоснование."""

    route: Literal["auto_check", "kb", "cert_order", "escalate"]
    service: Literal[
        "site", "lms", "wifi_guest", "wifi_edu", "wifi_corp", "account", "certs", "other"
    ] | None = None
    category: Literal[
        "availability", "access", "howto", "cert_order", "incident_report", "other"
    ]
    priority: Literal["critical", "high", "medium", "low"]
    confidence: float  # 0..1 (FR-013)
    reason: str



# ---------------------------------------------------------------------------
# Аутентификация
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    email: str  # домен @misis.ru / @edu.misis.ru проверяется в роутере


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    group_name: str | None = None
    role: Literal["student", "staff", "operator"]


class LoginResponse(BaseModel):
    user: UserOut


class MeResponse(BaseModel):
    user: UserOut | None = None
    guest: bool


# ---------------------------------------------------------------------------
# Обращения (пользователь)
# ---------------------------------------------------------------------------

class RequestCreate(BaseModel):
    text: str
    channel: Literal["web", "telegram"] = "web"


class SubtaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    position: int
    summary: str
    service: str | None = None
    category: str
    route: str
    route_reason: str
    priority: str
    confidence: float
    status: str


class ReactionOut(BaseModel):
    # answer | clarification | outage_notice | cert_ordered | escalated | auth_required
    kind: str
    text: str
    # answer из базы знаний: статьи-источники ответа (FR-031 — ответ строго по ним)
    sources: list[dict[str, Any]] | None = None


class TicketOut(BaseModel):
    id: int
    number: str  # SUP-2026-<id:04d> — генерируется из id, не хранится
    status: str
    escalated: bool


class RequestCreatedResponse(BaseModel):
    request_id: int
    duplicate: bool  # true — привязано к открытой заявке (FR-014)
    subtasks: list[SubtaskOut]
    reactions: list[ReactionOut]
    ticket: TicketOut


class ReplyRequest(BaseModel):
    text: str


class ReplyResponse(BaseModel):
    reactions: list[ReactionOut]
    ticket: TicketOut


class MyTicketBrief(BaseModel):
    number: str
    status: str


class MySubtaskBrief(BaseModel):
    service: str | None = None
    category: str
    priority: str


class MyRequestItem(BaseModel):
    id: int
    created_at: datetime
    channel: str
    masked_text: str
    ticket: MyTicketBrief
    subtasks: list[MySubtaskBrief]


class DialogMessage(BaseModel):
    role: Literal["user", "agent"]
    text: str
    at: datetime


class EventOut(BaseModel):
    actor: str
    action: str
    payload: dict[str, Any] | None = None
    created_at: datetime


class RequestDetail(BaseModel):
    id: int
    raw_text: str
    masked_text: str
    lang: str | None = None
    translation: str | None = None
    subtasks: list[SubtaskOut]
    dialog: list[DialogMessage]
    ticket: TicketOut
    events: list[EventOut]


# ---------------------------------------------------------------------------
# Справки (пользователь)
# ---------------------------------------------------------------------------

class CertCatalogItem(BaseModel):
    type: str  # payments / callup / medical / study / military
    title: str
    description: str


class CertOrderCreate(BaseModel):
    cert_type: Literal["payments", "callup", "medical", "study", "military"]


class CertOrderOut(BaseModel):
    id: int
    cert_type: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime | None = None


class CertOrderCreatedResponse(BaseModel):
    order: CertOrderOut


# ---------------------------------------------------------------------------
# Админка (оператор)
# ---------------------------------------------------------------------------

class QueueUser(BaseModel):
    full_name: str
    email: str


class AdminQueueItem(BaseModel):
    request_id: int
    number: str
    created_at: datetime
    channel: str
    user: QueueUser | None = None  # null для гостевых обращений
    summary: str
    service: str | None = None
    category: str
    route: str
    route_reason: str
    priority: str
    status: str
    escalated: bool
    incident_id: int | None = None


class EscalationCheck(BaseModel):
    service: str
    ok: bool
    checked_at: datetime
    note: str


class EscalationCard(BaseModel):
    request: RequestDetail
    summary: str
    checks: list[EscalationCheck]
    recommendation: str
    why_escalated: str


class EscalationCloseRequest(BaseModel):
    resolution: str
    add_to_kb: bool = False


class KbDraftOut(BaseModel):
    id: int
    title: str
    body: str
    confirmed: bool


class EscalationCloseResponse(BaseModel):
    ticket_status: str
    kb_draft: KbDraftOut | None = None


class KbConfirmResponse(BaseModel):
    id: int
    confirmed: bool


class AdminCertUser(BaseModel):
    full_name: str
    email: str
    group_name: str | None = None


class AdminCertOrderOut(BaseModel):
    id: int
    cert_type: str
    title: str
    status: str
    user: AdminCertUser
    created_at: datetime


class AdminCertOrderPatch(BaseModel):
    # цепочка: не обработана → обрабатывается → готова и ждёт выдачи → забрана
    status: Literal["не обработана", "обрабатывается", "готова и ждёт выдачи", "забрана"]


class AdminCertOrderResponse(BaseModel):
    order: CertOrderOut


class LastCheckOut(BaseModel):
    ok: bool
    http_code: int | None = None
    latency_ms: int | None = None
    checked_at: datetime


class StatusBoardItem(BaseModel):
    id: int | None = None
    name: str
    check_type: Literal["real", "emulated"]
    state: Literal["up", "down"]
    last_check: LastCheckOut | None = None


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    check_type: str
    state: str
    updated_at: datetime | None = None


class ServicePatchRequest(BaseModel):
    state: Literal["up", "down"]  # только для check_type=emulated, иначе 409


class ServicePatchResponse(BaseModel):
    service: ServiceOut


class ToolParam(BaseModel):
    name: str
    type: str
    default: Any | None = None


class ToolOut(BaseModel):
    name: str  # check_site | check_lms | check_wifi | simulate_wave
    type: str  # exec / simulation
    description: str
    params: list[ToolParam]


class ToolInvokeRequest(BaseModel):
    params: dict[str, Any] = {}


class ToolInvokeResponse(BaseModel):
    tool: str
    ok: bool
    result: dict[str, Any]


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service: str
    category: str
    request_count: int
    window_start: datetime
    status: Literal["active", "resolved"]
    notified_at: datetime | None = None
    broadcast_at: datetime | None = None


class IncidentBroadcastRequest(BaseModel):
    text: str | None = None  # null → шаблон из БЗ «массовый ответ при инциденте»


class IncidentBroadcastResponse(BaseModel):
    sent: int
    broadcast_at: datetime


class IncidentResolveResponse(BaseModel):
    status: str


class MetricsOut(BaseModel):
    auto_closed_pct: float
    avg_first_reaction_sec: float
    incidents_total: int
    incidents_active: int
    requests_total: int
    escalations_open: int


# ---------------------------------------------------------------------------
# Служебное и внутренние хуки для бота (003)
# ---------------------------------------------------------------------------

class HealthOut(BaseModel):
    status: str
    llm: Literal["up", "down"]


class OutboundMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    chat_id: int
    text: str


class OutboundAckRequest(BaseModel):
    status: Literal["sent", "failed"]


class TgLinkUpsertRequest(BaseModel):
    chat_id: int
    email: str


class TgLinkUpsertResponse(BaseModel):
    ok: bool
    confirm_code: str


class TgLinkConfirmRequest(BaseModel):
    chat_id: int
    code: str


class TgLinkConfirmResponse(BaseModel):
    ok: bool
    user: UserOut


class TgLinkStateResponse(BaseModel):
    chat_id: int
    user_id: int | None = None
    state: str


class ErrorDetail(BaseModel):
    """Стандартное тело ошибки: { "detail": "текст по-русски" }."""

    detail: str


# ---------------------------------------------------------------------------
# Служебное
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    """GET /api/health: { "status": "ok", "llm": "up|down" }."""

    status: Literal["ok"]
    llm: Literal["up", "down"]
