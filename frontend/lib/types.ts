// DTO-типы по контракту specs/001-misis-support-assistant/contracts/api.md

export type Role = "student" | "staff" | "operator";

export type User = {
  id: number;
  email: string;
  full_name: string;
  group_name: string | null;
  role: Role;
};

export type Session = { user: User | null; guest: boolean };

export type Priority = "critical" | "high" | "medium" | "low";
// значения маршрута — как в schemas.py ядра
export type RouteKind = "auto_check" | "kb" | "cert_order" | "escalate";

export type Subtask = {
  id: number;
  position: number;
  summary: string;
  service: string | null;
  category: string;
  route: RouteKind;
  route_reason: string;
  priority: Priority;
  confidence: number;
  status: string;
};

export type ReactionKind =
  | "answer"
  | "clarification"
  | "outage_notice"
  | "cert_ordered"
  | "escalated"
  | "auth_required";

// источники answer-реакции из базы знаний (FR-031): tools.kb_agent → {id, title, score}
export type ReactionSource = { id: number; title: string; score: number };

export type Reaction = { kind: ReactionKind; text: string; sources?: ReactionSource[] | null };

export type Ticket = {
  id: number;
  number: string;
  status: string;
  escalated: boolean;
};

export type RequestCreated = {
  request_id: number;
  duplicate: boolean;
  subtasks: Subtask[];
  reactions: Reaction[];
  ticket: Ticket;
};

export type DialogMessage = { role: "user" | "agent"; text: string; at: string };

export type RequestEvent = {
  actor: string;
  action: string;
  payload: Record<string, unknown> | null;
  created_at: string;
};

export type RequestDetail = {
  id: number;
  raw_text: string;
  masked_text: string;
  lang: string | null;
  translation: string | null;
  subtasks: Subtask[];
  dialog: DialogMessage[];
  ticket: Ticket;
  events: RequestEvent[];
};

export type RequestListItem = {
  id: number;
  created_at: string;
  channel: string;
  masked_text: string;
  ticket: { number: string; status: string };
  subtasks: { service: string | null; category: string; priority: Priority }[];
};

export type CertCatalogItem = {
  type: string;
  title: string;
  description: string;
};

export type CertOrder = {
  id: number;
  cert_type: string;
  title: string;
  status: string;
  created_at: string;
  updated_at?: string | null;
};

export type QueueItem = {
  request_id: number;
  number: string;
  created_at: string;
  channel: string;
  user: { full_name: string; email: string } | null;
  summary: string;
  service: string | null;
  category: string;
  route: RouteKind;
  route_reason: string;
  priority: Priority;
  status: string;
  escalated: boolean;
  incident_id: number | null;
};

export type EscalationCard = {
  request: RequestDetail;
  summary: string;
  checks: { service: string; ok: boolean; checked_at: string; note: string }[];
  recommendation: string;
  why_escalated: string;
};

export type KbDraft = { id: number; title: string; body: string; confirmed: boolean };

export type CloseResult = { ticket_status: string; kb_draft: KbDraft | null };

export type AdminCertOrder = CertOrder & {
  user: { full_name: string; email: string; group_name: string | null };
};

export type ServiceStatus = {
  id?: number | null; // нужен для PATCH /api/admin/services/{id}; дополнение к контракту 001
  name: string;
  check_type: "real" | "emulated";
  state: "up" | "down";
  last_check: {
    ok: boolean;
    http_code: number | null;
    latency_ms: number | null;
    checked_at: string;
  } | null;
};

export type Tool = {
  name: string;
  type: string;
  description: string;
  params: { name: string; type: string; default: string | number | null }[];
};

export type ToolResult = {
  tool: string;
  ok: boolean;
  result: Record<string, unknown>;
};

export type Incident = {
  id: number;
  service: string;
  category: string;
  request_count: number;
  window_start: string;
  status: "active" | "resolved";
  notified_at: string | null;
  broadcast_at: string | null;
};

export type Metrics = {
  auto_closed_pct: number;
  avg_first_reaction_sec: number;
  incidents_total: number;
  incidents_active: number;
  requests_total: number;
  escalations_open: number;
};

// Цепочка статусов заказа справки (FR-042)
export const CERT_STATUS_CHAIN = [
  "не обработана",
  "обрабатывается",
  "готова и ждёт выдачи",
  "забрана",
] as const;

export const PRIORITY_LABEL: Record<Priority, string> = {
  critical: "Критично",
  high: "Высокий",
  medium: "Средний",
  low: "Низкий",
};

export const ROUTE_LABEL: Record<RouteKind, string> = {
  auto_check: "Авто-проверка",
  kb: "База знаний",
  cert_order: "Заказ справки",
  escalate: "Оператор",
};
