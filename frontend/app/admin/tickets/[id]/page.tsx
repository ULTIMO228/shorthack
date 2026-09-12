"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Check,
  CheckCheck,
  CheckCircle2,
  History,
  Languages,
  Lightbulb,
  Megaphone,
  MessagesSquare,
  MinusCircle,
  Sparkles,
  TicketCheck,
  Wrench,
  XCircle,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import AdminTabs from "@/components/admin-tabs";
import { PriorityBadge, RouteBadge } from "@/components/badges";
import { useToast } from "@/components/toast";
import { fmtDateTime, fmtTime } from "@/lib/format";
import type { CloseResult, EscalationCard, Incident, KbDraft, RequestDetail } from "@/lib/types";

const MONO = '"DM Mono", monospace';

export default function AdminTicketPage() {
  const params = useParams();
  const id = Number(params.id);
  const toast = useToast();

  const [card, setCard] = useState<EscalationCard | null>(null);
  const [plain, setPlain] = useState<RequestDetail | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [closeOpen, setCloseOpen] = useState(false);
  const [resolution, setResolution] = useState("");
  const [addToKb, setAddToKb] = useState(false);
  const [kbDraft, setKbDraft] = useState<KbDraft | null>(null);
  const [ticketStatus, setTicketStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!Number.isFinite(id)) {
      setLoading(false);
      return;
    }
    try {
      setCard(await api<EscalationCard>(`/api/admin/escalations/${id}`));
      setError(null);
    } catch (e) {
      if (e instanceof ApiError && [401, 403].includes(e.status)) {
        setError(e.message);
        setLoading(false);
        return;
      }
      // не эскалация — читаем обращение как оператор
      try {
        setPlain(await api<RequestDetail>(`/api/requests/${id}`));
        setError(null);
      } catch (e2) {
        setError(e2 instanceof ApiError ? e2.message : "Ошибка загрузки обращения");
      }
    } finally {
      setLoading(false);
    }
    try {
      setIncidents(await api<Incident[]>("/api/admin/incidents"));
    } catch {
      // баннеры инцидентов опциональны
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  // Esc закрывает модалку закрытия эскалации
  useEffect(() => {
    if (!closeOpen) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") setCloseOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeOpen]);

  const request = card?.request ?? plain;
  const service = request?.subtasks[0]?.service ?? null;
  const activeIncident =
    incidents.find((i) => i.status === "active" && i.service === service) ??
    incidents.find((i) => i.status === "active") ??
    null;

  async function submitClose(e: FormEvent) {
    e.preventDefault();
    if (busy || !resolution.trim()) return;
    setBusy(true);
    try {
      const res = await api<CloseResult>(`/api/admin/escalations/${id}/close`, {
        method: "POST",
        body: { resolution: resolution.trim(), add_to_kb: addToKb },
      });
      setTicketStatus(res.ticket_status);
      setKbDraft(res.kb_draft);
      setCloseOpen(false);
      toast.push(`Тикет закрыт: ${res.ticket_status}`, "ok");
      load();
    } catch (err) {
      toast.push(err instanceof ApiError ? err.message : "Ошибка сети");
    } finally {
      setBusy(false);
    }
  }

  async function confirmKb() {
    if (!kbDraft || busy) return;
    setBusy(true);
    try {
      await api(`/api/admin/kb/articles/${kbDraft.id}/confirm`, { method: "POST" });
      setKbDraft({ ...kbDraft, confirmed: true });
      toast.push("Статья подтверждена и ушла в базу знаний", "ok");
    } catch (err) {
      toast.push(err instanceof ApiError ? err.message : "Ошибка сети");
    } finally {
      setBusy(false);
    }
  }

  async function broadcast() {
    if (!activeIncident || busy) return;
    setBusy(true);
    try {
      const res = await api<{ sent: number }>(`/api/admin/incidents/${activeIncident.id}/broadcast`, {
        method: "POST",
        body: { text: null },
      });
      toast.push(`Уведомление отправлено затронутым: ${res.sent}`, "ok");
      load();
    } catch (err) {
      toast.push(err instanceof ApiError ? err.message : "Ошибка сети");
    } finally {
      setBusy(false);
    }
  }

  async function resolveIncident() {
    if (!activeIncident || busy) return;
    setBusy(true);
    try {
      await api(`/api/admin/incidents/${activeIncident.id}/resolve`, { method: "POST" });
      toast.push("Инцидент отмечен решённым", "ok");
      load();
    } catch (err) {
      toast.push(err instanceof ApiError ? err.message : "Ошибка сети");
    } finally {
      setBusy(false);
    }
  }

  if (!Number.isFinite(id)) {
    return (
      <main className="page">
        <AdminTabs />
        <section className="card empty anim-in" role="alert">
          <img src="/maia-mascot.jpg" alt="МАИА" className="empty-mascot mascot-float" />
          <h1 className="empty-title">Некорректный номер обращения</h1>
          <p className="empty-text">Проверьте ссылку — номер обращения должен быть числом.</p>
          <Link href="/admin" className="btn btn-primary" style={{ marginTop: 10 }}>
            <ArrowLeft aria-hidden="true" /> К очереди
          </Link>
        </section>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="page">
        <header className="page-head">
          <div className="skeleton" style={{ width: 48, height: 48, borderRadius: 14 }} />
          <div style={{ flex: 1 }}>
            <div className="skeleton" style={{ height: 26, width: 220 }} />
            <div className="skeleton" style={{ height: 14, width: 320, marginTop: 10 }} />
          </div>
        </header>
        <section className="card card-pad">
          <div className="skeleton" style={{ height: 16, width: "42%" }} />
          <div className="skeleton" style={{ height: 12, width: "86%", marginTop: 14 }} />
          <div className="skeleton" style={{ height: 12, width: "68%", marginTop: 8 }} />
        </section>
        <section className="card card-pad">
          <div className="skeleton" style={{ height: 16, width: "30%" }} />
          <div className="skeleton" style={{ height: 12, width: "74%", marginTop: 14 }} />
        </section>
      </main>
    );
  }

  if (error || !request) {
    return (
      <main className="page">
        <AdminTabs />
        <section className="card empty anim-in" role="alert">
          <img src="/maia-mascot.jpg" alt="МАИА" className="empty-mascot mascot-float" />
          <h1 className="empty-title">Карточка недоступна</h1>
          <p className="empty-text">{error ?? "Обращение не найдено"}</p>
          <Link href="/admin" className="btn btn-primary" style={{ marginTop: 10 }}>
            <ArrowLeft aria-hidden="true" /> К очереди
          </Link>
        </section>
      </main>
    );
  }

  const sub = request.subtasks[0];
  const closed = ticketStatus ?? request.ticket.status;
  const lastEvent = request.events.length - 1;

  return (
    <main className="page">
      <header className="page-head" style={{ flexWrap: "wrap" }}>
        <div className="page-head-icon">
          <TicketCheck aria-hidden="true" />
        </div>
        <div style={{ flex: 1, minWidth: 240 }}>
          <h1 className="page-head-title">Обращение {request.ticket.number}</h1>
          <div
            className="page-head-sub"
            style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}
          >
            {sub && <PriorityBadge priority={sub.priority} />}
            {sub && <RouteBadge route={sub.route} />}
            <span className="chip">язык: {request.lang ?? "—"}</span>
            <span className="chip">
              <span
                className="chip-dot"
                aria-hidden="true"
                style={{ background: closed.includes("закрыт") ? "var(--ok)" : "var(--blue)" }}
              />
              {closed}
            </span>
          </div>
        </div>
        <Link href="/admin" className="btn btn-ghost btn-sm">
          <ArrowLeft aria-hidden="true" /> К очереди
        </Link>
      </header>

      <AdminTabs />

      <div className="esc-layout">
        {/* Основная колонка: текст, диалог, журнал */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          <section className="card card-pad anim-in" style={{ ["--d" as string]: "0ms" }}>
            <h2 className="card-title">
              <Languages aria-hidden="true" /> Исходный текст и перевод
            </h2>
            <div className="raw-cols" style={{ marginTop: 0 }}>
              <div>
                <h5>Оригинал · {request.lang ?? "—"}</h5>
                <p>{request.raw_text}</p>
              </div>
              <div>
                <h5>Перевод на русский</h5>
                <p>{request.translation ?? "Перевод не требуется"}</p>
              </div>
            </div>
          </section>

          {request.dialog.length > 0 && (
            <section className="card card-pad anim-in" style={{ ["--d" as string]: "60ms" }}>
              <h2 className="card-title">
                <MessagesSquare aria-hidden="true" /> Диалог · {request.dialog.length}
              </h2>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {request.dialog.map((m, i) => (
                  <div
                    key={`${m.role}-${m.at}-${i}`}
                    style={{
                      display: "flex",
                      justifyContent: m.role === "user" ? "flex-end" : "flex-start",
                    }}
                  >
                    <div
                      style={{
                        maxWidth: "85%",
                        padding: "10px 13px",
                        borderRadius:
                          m.role === "user" ? "12px 3px 12px 12px" : "3px 12px 12px 12px",
                        background: m.role === "user" ? "var(--blue)" : "#eaf1fb",
                        color: m.role === "user" ? "#fff" : "#233653",
                        fontSize: 13,
                        lineHeight: 1.55,
                      }}
                    >
                      <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{m.text}</p>
                      <small
                        style={{
                          display: "block",
                          marginTop: 4,
                          fontSize: 10,
                          opacity: 0.72,
                          fontFamily: MONO,
                        }}
                      >
                        {m.role === "user" ? "пользователь" : "МАИА"} · {fmtTime(m.at)}
                      </small>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="card card-pad anim-in" style={{ ["--d" as string]: "120ms" }}>
            <h2 className="card-title">
              <History aria-hidden="true" /> Журнал событий · {request.events.length}
            </h2>
            {request.events.length === 0 ? (
              <p style={{ margin: 0, color: "var(--muted)", fontSize: 13 }}>
                Событий пока нет — они появятся по мере работы с обращением.
              </p>
            ) : (
              <div style={{ position: "relative" }}>
                <div
                  aria-hidden="true"
                  style={{
                    position: "absolute",
                    left: 4,
                    top: 20,
                    bottom: 20,
                    width: 2,
                    borderRadius: 2,
                    background: "var(--line)",
                  }}
                />
                {request.events.map((ev, i) => (
                  <div
                    key={`${ev.created_at}-${ev.actor}-${i}`}
                    style={{ position: "relative", padding: "10px 0 10px 26px" }}
                  >
                    <span
                      aria-hidden="true"
                      style={{
                        position: "absolute",
                        left: 0,
                        top: 15,
                        width: 10,
                        height: 10,
                        borderRadius: "50%",
                        background: i === lastEvent ? "var(--blue)" : "#c4d0e2",
                        boxShadow: "0 0 0 3px #fff",
                      }}
                    />
                    <div
                      style={{
                        display: "flex",
                        gap: 10,
                        alignItems: "baseline",
                        flexWrap: "wrap",
                      }}
                    >
                      <b style={{ fontSize: 13, color: "var(--ink)" }}>{ev.actor}</b>
                      <time
                        style={{ fontFamily: MONO, fontSize: 11, color: "#8b9aae" }}
                        title={fmtDateTime(ev.created_at)}
                      >
                        {fmtTime(ev.created_at)}
                      </time>
                    </div>
                    <p style={{ margin: "3px 0 0", fontSize: 13, lineHeight: 1.55, color: "#41526e" }}>
                      {ev.action}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* Сайдбар: саммари, проверки, рекомендация, действия */}
        <aside style={{ display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
          <section className="card card-pad anim-in" style={{ ["--d" as string]: "60ms" }}>
            <h2 className="card-title">
              <Sparkles aria-hidden="true" /> Саммари
            </h2>
            <p style={{ margin: 0, fontSize: 14, lineHeight: 1.65, color: "#233653" }}>
              {card?.summary ?? sub?.summary ?? request.masked_text}
            </p>
          </section>

          {card && card.checks.length > 0 && (
            <section className="card card-pad anim-in" style={{ ["--d" as string]: "120ms" }}>
              <h2 className="card-title">
                <Wrench aria-hidden="true" /> Автопроверки
              </h2>
              <div>
                {card.checks.map((c, i) => {
                  const state = !c.checked_at ? "none" : c.ok ? "ok" : "fail";
                  return (
                    <div
                      key={`${c.service}-${c.checked_at}`}
                      style={{
                        display: "flex",
                        gap: 12,
                        alignItems: "center",
                        padding: "9px 0",
                        borderTop: i > 0 ? "1px solid var(--line)" : undefined,
                      }}
                    >
                      <span
                        className="icon-tile"
                        data-tone={state === "ok" ? "green" : state === "fail" ? "red" : undefined}
                        style={
                          state === "none"
                            ? { background: "var(--low-bg)", color: "var(--low)" }
                            : undefined
                        }
                      >
                        {state === "ok" ? (
                          <CheckCircle2 aria-hidden="true" />
                        ) : state === "fail" ? (
                          <XCircle aria-hidden="true" />
                        ) : (
                          <MinusCircle aria-hidden="true" />
                        )}
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <b style={{ fontSize: 13, color: "var(--ink)" }}>{c.service}</b>
                        {c.note && (
                          <p style={{ margin: "2px 0 0", fontSize: 12, color: "var(--muted)" }}>
                            {c.note}
                          </p>
                        )}
                      </div>
                      <span
                        style={{
                          fontSize: 11,
                          fontWeight: 800,
                          color:
                            state === "ok"
                              ? "var(--ok)"
                              : state === "fail"
                                ? "var(--crit)"
                                : "var(--muted)",
                        }}
                      >
                        {state === "ok" ? "OK" : state === "fail" ? "FAIL" : "—"}
                      </span>
                      <time style={{ fontFamily: MONO, fontSize: 11, color: "#8b9aae" }}>
                        {fmtTime(c.checked_at)}
                      </time>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {card?.recommendation && (
            <section
              className="card card-pad anim-in"
              style={{
                ["--d" as string]: "180ms",
                background: "var(--info-bg)",
                borderColor: "#bcdcf0",
              }}
            >
              <h2 className="card-title" style={{ color: "var(--info)" }}>
                <Lightbulb aria-hidden="true" style={{ color: "var(--info)" }} /> Рекомендуемый шаг
              </h2>
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "#285e7d" }}>
                {card.recommendation}
              </p>
            </section>
          )}

          {card?.why_escalated && (
            <section
              className="card card-pad anim-in"
              style={{
                ["--d" as string]: "240ms",
                background: "var(--warn-bg)",
                borderColor: "#f0dfae",
              }}
            >
              <h2 className="card-title" style={{ color: "var(--warn)" }}>
                <AlertTriangle aria-hidden="true" style={{ color: "var(--warn)" }} /> Почему
                эскалировано
              </h2>
              <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6, color: "#7a5b12" }}>
                {card.why_escalated}
              </p>
            </section>
          )}

          <section className="card card-pad anim-in" style={{ ["--d" as string]: "300ms" }}>
            <h2 className="card-title">
              <TicketCheck aria-hidden="true" /> Действия оператора
            </h2>
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              <button className="btn btn-primary" onClick={() => setCloseOpen(true)} disabled={busy}>
                <Check aria-hidden="true" /> Закрыть эскалацию
              </button>
              {activeIncident && (
                <>
                  <button className="btn btn-ghost" onClick={broadcast} disabled={busy}>
                    <Megaphone aria-hidden="true" /> Уведомить затронутых (
                    {activeIncident.request_count})
                  </button>
                  <button className="btn btn-ghost" onClick={resolveIncident} disabled={busy}>
                    <CheckCheck aria-hidden="true" /> Инцидент решён
                  </button>
                </>
              )}
            </div>
            {activeIncident && (
              <p style={{ margin: "12px 0 0", fontSize: 11, lineHeight: 1.5, color: "var(--muted)" }}>
                Активный инцидент: {activeIncident.service} · {activeIncident.category}
              </p>
            )}
          </section>

          {kbDraft && (
            <section
              className="card card-pad anim-in"
              style={{
                ["--d" as string]: "360ms",
                background: "var(--ai-bg)",
                borderColor: "#ddd2f5",
              }}
            >
              <h2 className="card-title" style={{ color: "var(--ai)" }}>
                <BookOpen aria-hidden="true" style={{ color: "var(--ai)" }} /> Черновик базы знаний
              </h2>
              <span
                className="chip"
                style={{ borderColor: "#ddd2f5", color: "var(--ai)", marginBottom: 10 }}
              >
                <Sparkles size={12} aria-hidden="true" /> подготовлен ИИ
              </span>
              <h3 style={{ margin: "0 0 6px", fontSize: 14, color: "#3d2d75" }}>{kbDraft.title}</h3>
              <p
                style={{
                  margin: 0,
                  fontSize: 12,
                  lineHeight: 1.6,
                  color: "#54418f",
                  whiteSpace: "pre-wrap",
                }}
              >
                {kbDraft.body}
              </p>
              {!kbDraft.confirmed ? (
                <button
                  className="btn btn-primary btn-sm"
                  style={{ marginTop: 14 }}
                  onClick={confirmKb}
                  disabled={busy}
                >
                  <Check aria-hidden="true" /> Подтвердить статью
                </button>
              ) : (
                <p
                  style={{
                    margin: "14px 0 0",
                    fontWeight: 800,
                    fontSize: 12,
                    color: "var(--ok)",
                    display: "flex",
                    gap: 6,
                    alignItems: "center",
                  }}
                >
                  <CheckCircle2 size={14} aria-hidden="true" /> Статья подтверждена
                </p>
              )}
            </section>
          )}
        </aside>
      </div>

      {/* Модалка закрытия */}
      {closeOpen && (
        <div className="modal-backdrop" onClick={() => setCloseOpen(false)}>
          <form
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="Закрыть эскалацию"
            onClick={(e) => e.stopPropagation()}
            onSubmit={submitClose}
          >
            <h3 style={{ margin: "0 0 4px", fontSize: 18, letterSpacing: "-.02em" }}>
              Закрыть эскалацию
            </h3>
            <p style={{ margin: "0 0 16px", color: "var(--muted)", fontSize: 13 }}>
              Опишите, как решена проблема — резолюция уйдёт пользователю и в журнал.
            </p>
            <div className="field">
              <label className="field-label" htmlFor="close-resolution">
                Резолюция
              </label>
              <textarea
                id="close-resolution"
                className="textarea"
                value={resolution}
                onChange={(e) => setResolution(e.target.value)}
                placeholder="Например: перезапущен контроллер точки доступа, сеть восстановлена"
                required
                autoFocus
              />
            </div>
            <label
              style={{
                display: "flex",
                gap: 9,
                alignItems: "center",
                marginTop: 14,
                fontSize: 13,
                color: "#41526e",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              <input
                type="checkbox"
                checked={addToKb}
                onChange={(e) => setAddToKb(e.target.checked)}
                style={{ width: 16, height: 16, accentColor: "var(--blue)" }}
              />
              Добавить в базу знаний (ИИ подготовит черновик статьи)
            </label>
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 20 }}>
              <button type="button" className="btn btn-ghost" onClick={() => setCloseOpen(false)}>
                Отмена
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={busy || !resolution.trim()}
              >
                {busy ? "Закрываем…" : "Закрыть эскалацию"} <Check aria-hidden="true" />
              </button>
            </div>
          </form>
        </div>
      )}
    </main>
  );
}
