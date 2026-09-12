"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  BookOpen,
  Bot,
  CheckCircle2,
  CreditCard,
  FileText,
  Globe,
  GraduationCap,
  Languages,
  Lock,
  LogIn,
  MailQuestion,
  MessagesSquare,
  MonitorX,
  PanelLeft,
  Send,
  SquarePen,
  User,
  Users,
  Wifi,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import Spinner from "@/components/spinner";
import { PriorityBadge, RouteBadge } from "@/components/badges";
import { useAuth } from "@/components/auth-provider";
import { fmtTime, initials } from "@/lib/format";
import { usePolling } from "@/lib/use-polling";
import type { DialogMessage, Reaction, RequestCreated, RequestDetail, RequestListItem, RouteKind, Ticket } from "@/lib/types";

type IconT = typeof Send;
type Tone = "blue" | "cyan" | "green" | "amber" | "red" | "violet";

const EXAMPLES: { label: string; text: string; Icon: IconT; tone: Tone }[] = [
  { label: "Сайт недоступен", text: "Не открывается сайт misis.ru — крутится загрузка, потом ошибка. Проверял с двух устройств.", Icon: MonitorX, tone: "red" },
  { label: "LMS не грузится", text: "LMS не загружает страницу курса, выдаёт ошибку 504 уже второй час. У одногруппников то же самое.", Icon: GraduationCap, tone: "blue" },
  { label: "Wi-Fi отваливается", text: "Wi-Fi MISIS-EDU отваливается каждые пять минут в 4 корпусе, на всех устройствах.", Icon: Wifi, tone: "cyan" },
  { label: "Неполное письмо", text: "Здравствуйте! У меня проблема с", Icon: FileText, tone: "amber" },
  { label: "Несколько вопросов", text: "Не работает Wi-Fi MISIS-EDU, не могу войти в LMS, и подскажите, где взять справку с места учёбы?", Icon: BookOpen, tone: "violet" },
  { label: "Расшифровка звонка", text: "— Алло, у меня в общаге интернет не работает. — Какая сеть? — MISIS-Guest, по вечерам вообще не подключается, днём еле-еле.", Icon: Users, tone: "blue" },
  { label: "Заказ справки", text: "Здравствуйте, мне нужна справка с места учёбы для банка. Как заказать?", Icon: CreditCard, tone: "green" },
  { label: "Мат / английский", text: "WTF, your wifi is down AGAIN, nothing works, this is ridiculous!!", Icon: Languages, tone: "amber" },
];

const ROUTE_TILE: Record<RouteKind, { Icon: IconT; tone: Tone }> = {
  auto_check: { Icon: Bot, tone: "blue" },
  kb: { Icon: BookOpen, tone: "cyan" },
  cert_order: { Icon: FileText, tone: "green" },
  escalate: { Icon: User, tone: "violet" },
};

const WAITING = "ждёт ответа пользователя";
const CLOSED_STATUSES = ["решена", "закрыта"];

type Notice = { kind: Reaction["kind"]; text: string };

type KbSource = { id?: number; title?: string; score?: number };

function reactionSources(r: Reaction): KbSource[] {
  const s = (r as Reaction & { sources?: unknown }).sources;
  return Array.isArray(s) ? (s as KbSource[]) : [];
}

const agentAvatar = (
  <img
    src="/maia-mascot.jpg"
    alt="MAIA"
    width={32}
    height={32}
    style={{ width: 32, height: 32, borderRadius: "50%", objectFit: "cover", flex: "0 0 auto" }}
  />
);

export default function RequestPage() {
  const { session } = useAuth();
  const router = useRouter();

  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RequestCreated | null>(null);
  const [detail, setDetail] = useState<RequestDetail | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [detailError, setDetailError] = useState<number | null>(null);
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [locked, setLocked] = useState<string | null>(null);
  const [resultAt, setResultAt] = useState<Date | null>(null);
  const [sourcesByText, setSourcesByText] = useState<Record<string, KbSource[]>>({});
  const [activeId, setActiveId] = useState<number | null>(null);
  const [sideOpen, setSideOpen] = useState(false);

  const convoRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const my = usePolling<RequestListItem[]>(() => (session?.user ? api("/api/requests") : Promise.resolve([])), 10000);

  const pending = (my.data ?? []).filter((r) => r.ticket.status === WAITING);
  const all = (my.data ?? []).slice(0, 25);

  const loadDetail = useCallback(async (id: number) => {
    try {
      const d = await api<RequestDetail>(`/api/requests/${id}`);
      setDetail(d);
      setTicket(d.ticket);
      setDetailError(null);
    } catch {
      setDetailError(id);
    }
  }, []);

  async function openRequest(id: number) {
    setSideOpen(false);
    setActiveId(id);
    setResult(null);
    setNotices([]);
    setLocked(null);
    setError(null);
    setDetailError(null);
    setDetail(null);
    setTicket(null);
    await loadDetail(id);
  }

  function resetAll() {
    setActiveId(null);
    setResult(null);
    setDetail(null);
    setTicket(null);
    setNotices([]);
    setLocked(null);
    setError(null);
    setDetailError(null);
    setSourcesByText({});
    setText("");
    setResultAt(null);
  }

  function collectNotices(reactions: Reaction[]) {
    setNotices(
      reactions
        .filter((r) => ["outage_notice", "auth_required", "cert_ordered", "escalated"].includes(r.kind))
        .map((r) => ({ kind: r.kind, text: r.text })),
    );
  }

  function collectSources(reactions: Reaction[], reset = false) {
    const fresh: Record<string, KbSource[]> = {};
    for (const r of reactions) {
      if (r.kind === "answer") {
        const src = reactionSources(r);
        if (src.length) fresh[r.text] = src;
      }
    }
    setSourcesByText((prev) => (reset ? fresh : { ...prev, ...fresh }));
  }

  async function submit(payload: string) {
    if (busy || !payload.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setDetail(null);
    setTicket(null);
    setLocked(null);
    setNotices([]);
    setDetailError(null);
    try {
      const res = await api<RequestCreated>("/api/requests", {
        method: "POST",
        body: { text: payload, channel: "web" },
      });
      setResult(res);
      setResultAt(new Date());
      setTicket(res.ticket);
      setActiveId(res.request_id);
      collectNotices(res.reactions);
      collectSources(res.reactions, true);
      setText("");
      await loadDetail(res.request_id);
      my.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Ошибка сети, попробуйте ещё раз");
    } finally {
      setBusy(false);
    }
  }

  async function sendReply() {
    if (busy || !activeId || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ reactions: Reaction[]; ticket: Ticket }>(`/api/requests/${activeId}/reply`, {
        method: "POST",
        body: { text: text.trim() },
      });
      setTicket(res.ticket);
      collectNotices(res.reactions);
      collectSources(res.reactions);
      setText("");
      await loadDetail(activeId);
      my.reload();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setLocked(e.message);
      } else {
        setError(e instanceof ApiError ? e.message : "Ошибка сети, попробуйте ещё раз");
      }
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    if (activeId && ticket?.status === WAITING && !locked) await sendReply();
    else await submit(text);
  }

  function onComposerKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    } else if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      send();
    }
  }

  function runExample(example: (typeof EXAMPLES)[number]) {
    setText(example.text);
    submit(example.text);
  }

  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 132) + "px";
  }, [text]);

  const dialog: DialogMessage[] = detail?.dialog ?? [];
  const waiting = ticket?.status === WAITING && !locked;
  const composerLocked = locked || (!!ticket && CLOSED_STATUSES.includes(ticket.status));

  useEffect(() => {
    const el = convoRef.current;
    if (!el) return;
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }, [dialog.length, busy]);

  function statusColor(s?: string) {
    if (s === WAITING) return "var(--warn)";
    if (s === "решена" || s === "закрыта") return "var(--ok)";
    return "var(--blue)";
  }

  const user = session?.user;

  return (
    <main className="page chat-page">
      <h1 className="sr-only">Новое обращение</h1>

      <button className="btn btn-ghost btn-sm side-toggle" onClick={() => setSideOpen(true)}>
        <PanelLeft size={16} aria-hidden="true" />
        Обращения
      </button>

      {sideOpen && <div className="side-backdrop" onClick={() => setSideOpen(false)} aria-hidden="true" />}

      <div className="chat-layout">
        <aside className={`card chat-side ${sideOpen ? "open" : ""}`}>
          <button className="btn btn-primary side-new" onClick={resetAll}>
            <SquarePen size={16} aria-hidden="true" />
            Новое обращение
          </button>

          {user ? (
            <>
              <div className="side-folder">
                <MailQuestion aria-hidden="true" />
                Ждут ответа
                <span className="side-count">{pending.length}</span>
              </div>
              {pending.length === 0 && <p className="side-empty">Нет обращений, ждущих вашего ответа</p>}
              <div className="side-list">
                {pending.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    className={`side-item ${activeId === r.id ? "active" : ""}`}
                    onClick={() => openRequest(r.id)}
                  >
                    {r.channel === "telegram" ? <Send size={14} /> : <Globe size={14} />}
                    <span className="t">{r.masked_text || r.ticket.number}</span>
                    <span className="n">{r.ticket.number}</span>
                    <span className="side-dot" style={{ background: statusColor(r.ticket.status) }} />
                  </button>
                ))}
              </div>

              <div className="side-folder">
                <MessagesSquare aria-hidden="true" />
                Общие обращения
              </div>
              {all.length === 0 && <p className="side-empty">История пуста</p>}
              <div className="side-list">
                {all.map((r) => (
                  <button
                    key={r.id}
                    type="button"
                    className={`side-item ${activeId === r.id ? "active" : ""}`}
                    onClick={() => openRequest(r.id)}
                  >
                    {r.channel === "telegram" ? <Send size={14} /> : <Globe size={14} />}
                    <span className="t">{r.masked_text || r.ticket.number}</span>
                    <span className="n">{r.ticket.number}</span>
                    <span className="side-dot" style={{ background: statusColor(r.ticket.status) }} />
                  </button>
                ))}
              </div>
            </>
          ) : (
            <div className="side-guest">
              <MailQuestion size={18} aria-hidden="true" style={{ color: "var(--blue)" }} />
              Войдите по почте МИСИС, чтобы обращения сохранялись в истории и отображались здесь.
              <Link href="/login?from=/" className="btn btn-primary btn-sm">
                <LogIn size={14} aria-hidden="true" />
                Войти
              </Link>
            </div>
          )}

          {user ? (
            <Link href="/cabinet" className="side-profile">
              <span className="avatar">{initials(user.full_name)}</span>
              <div>
                <div className="nm">{user.full_name}</div>
                <div className="rl">{user.email}</div>
              </div>
            </Link>
          ) : (
            <div className="side-profile" style={{ pointerEvents: "none" }}>
              <span className="avatar">Г</span>
              <div>
                <div className="nm">Гость</div>
                <div className="rl">История не сохраняется</div>
              </div>
            </div>
          )}
        </aside>

        <section className="card chat-main">
          {ticket && (
            <div className="chat-topline">
              Тикет <b>{ticket.number}</b>
              <span className="chip">
                <span className="chip-dot" style={{ background: statusColor(ticket.status) }} />
                {ticket.status}
              </span>
              {result?.duplicate && <span>· привязано к открытой заявке</span>}
              {ticket.escalated && <span>· эскалировано оператору</span>}
            </div>
          )}

          <div className="chat-scroll" ref={convoRef} aria-live="polite">
            {dialog.length === 0 && !busy && !result && (
              <div className="empty" style={{ margin: "auto" }}>
                <img className="empty-mascot mascot-float" src="/maia-mascot.jpg" alt="Маскот MAIA" />
                <h2 className="empty-title">С чего начнём?</h2>
                <p className="empty-text">Опишите проблему — MAIA разберёт и поможет</p>
                <div className="ex-rows">
                  {EXAMPLES.map(({ label, Icon, tone, ...example }) => (
                    <button
                      key={label}
                      type="button"
                      onClick={() => runExample({ label, Icon, tone, ...example })}
                      disabled={busy}
                      className="ex-row"
                    >
                      <span className="icon-tile" data-tone={tone}>
                        <Icon aria-hidden="true" size={16} />
                      </span>
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {busy && dialog.length === 0 && !result && (
              <div className="card anim-in" style={{ margin: "auto 0" }}>
                <Spinner />
              </div>
            )}

            {result && (
              <div className="card card-pad anim-in">
                <header style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", paddingBottom: 12, borderBottom: "1px solid var(--line)" }}>
                  {result.subtasks[0] && <RouteBadge route={result.subtasks[0].route} />}
                  {result.subtasks[0] && <PriorityBadge priority={result.subtasks[0].priority} />}
                  {resultAt && (
                    <time style={{ marginLeft: "auto", color: "#8b9aae", font: '10px "DM Mono",monospace' }}>
                      {fmtTime(resultAt.toISOString())}
                    </time>
                  )}
                </header>

                {result.subtasks.length > 0 && (
                  <div className="result-block">
                    <span>Подзадачи</span>
                    <div className="subtask-list">
                      {result.subtasks.map((s) => {
                        const tile = ROUTE_TILE[s.route] ?? ROUTE_TILE.auto_check;
                        return (
                          <div className="subtask-item" key={s.id}>
                            <span className="icon-tile" data-tone={tile.tone}>
                              <tile.Icon aria-hidden="true" />
                            </span>
                            <div>
                              <h4>
                                {s.position}. {s.summary}
                              </h4>
                              <p>{s.route_reason}</p>
                              <div className="badges">
                                <RouteBadge route={s.route} />
                                <PriorityBadge priority={s.priority} />
                                <span className="badge p-low">{s.status}</span>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                <div className="result-notices">
                  {notices.map((n) => {
                    if (n.kind === "outage_notice")
                      return (
                        <div className="notice crit" key={`${n.kind}-${n.text}`} role="alert">
                          <AlertTriangle aria-hidden="true" />
                          <div>
                            <b>Сбой известен, работы ведутся</b>
                            {n.text}
                          </div>
                        </div>
                      );
                    if (n.kind === "auth_required")
                      return (
                        <div className="notice warn" key={`${n.kind}-${n.text}`}>
                          <LogIn aria-hidden="true" />
                          <div>
                            <b>Требуется вход по почте МИСИС</b>
                            {n.text}
                          </div>
                          <button className="notice-action" onClick={() => router.push("/login?from=/")}>
                            Войти
                          </button>
                        </div>
                      );
                    if (n.kind === "cert_ordered")
                      return (
                        <div className="notice ok" key={`${n.kind}-${n.text}`}>
                          <CheckCircle2 aria-hidden="true" />
                          <div>
                            <b>Заказ создан</b>
                            {n.text}
                          </div>
                          <Link className="notice-action" href="/cabinet">
                            В кабинет
                          </Link>
                        </div>
                      );
                    return (
                      <div className="notice info" key={`${n.kind}-${n.text}`}>
                        <AlertTriangle aria-hidden="true" />
                        <div>
                          <b>Передано специалисту</b>
                          {n.text}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {ticket && (
                  <div className="ticket-line">
                    Номер тикета <b>{ticket.number}</b>
                    {result.duplicate && <span>· привязано к открытой заявке</span>}
                    {ticket.escalated && <span>· эскалировано оператору</span>}
                  </div>
                )}
              </div>
            )}

            {dialog.map((m, i) => {
              if (m.role === "user")
                return (
                  <div className="message user" key={`${i}-${m.at}`}>
                    <p style={{ background: "var(--brand-grad)" }}>{m.text}</p>
                  </div>
                );
              const src = sourcesByText[m.text];
              return (
                <div className="message bot" key={`${i}-${m.at}`}>
                  {agentAvatar}
                  <div style={{ maxWidth: "84%", display: "flex", flexDirection: "column", gap: 6 }}>
                    <p style={{ maxWidth: "100%", background: "#fff", border: "1px solid var(--line)", boxShadow: "var(--shadow-card)" }}>
                      {m.text}
                    </p>
                    {src && (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }} aria-label="Источники из базы знаний">
                        {src.map((s, j) => (
                          <span className="chip" key={`${s.id ?? j}`}>
                            <BookOpen size={12} aria-hidden="true" />
                            {s.title ?? `Статья БЗ №${s.id ?? j + 1}`}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}

            {busy && (result || activeId) && (
              <div className="message bot typing">
                {agentAvatar}
                <p aria-label="Агент печатает">
                  <span className="spinner-dots" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                </p>
              </div>
            )}

            {detailError !== null && (
              <div className="api-error" role="alert" style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10 }}>
                <AlertTriangle aria-hidden="true" />
                <span style={{ flex: 1 }}>Не удалось загрузить ответ</span>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => loadDetail(detailError)}>
                  Повторить
                </button>
              </div>
            )}
          </div>

          <div className="chat-bottom">
            {error && (
              <div className="api-error" role="alert" style={{ marginBottom: 10 }}>
                <AlertTriangle aria-hidden="true" />
                {error}
              </div>
            )}

            {composerLocked ? (
              <div className="input-locked">
                <Lock aria-hidden="true" />
                {locked ?? `Диалог по обращению завершён — статус: ${ticket?.status}`}
                <button type="button" className="btn btn-ghost btn-sm" onClick={resetAll} style={{ marginLeft: "auto" }}>
                  Новое обращение
                </button>
              </div>
            ) : (
              <>
                <div className="composer">
                  <textarea
                    ref={taRef}
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    onKeyDown={onComposerKeyDown}
                    placeholder={
                      waiting ? "Ответьте на уточняющий вопрос…" : "Опишите проблему своими словами…"
                    }
                    aria-label={waiting ? "Ответ агенту на уточняющий вопрос" : "Текст обращения"}
                    disabled={busy}
                    rows={1}
                  />
                  <button className="composer-send" type="button" aria-label="Отправить" onClick={send} disabled={busy || !text.trim()}>
                    <Send aria-hidden="true" />
                  </button>
                </div>
                <p className="composer-hint">
                  {waiting ? "Enter — отправить · агент ждёт ваш ответ" : "Enter — отправить · Shift+Enter — новая строка"}
                </p>
              </>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
