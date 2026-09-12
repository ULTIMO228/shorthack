"use client";

import Link from "next/link";
import { AlertTriangle, Globe, Inbox, Send, ShieldCheck, Siren, UserX } from "lucide-react";
import { api } from "@/lib/api";
import AdminTabs from "@/components/admin-tabs";
import StatusBoard from "@/components/status-board";
import { PriorityBadge } from "@/components/badges";
import { useAuth } from "@/components/auth-provider";
import { usePolling } from "@/lib/use-polling";
import { fmtTime } from "@/lib/format";
import type { Incident, QueueItem } from "@/lib/types";

const MONO = '"DM Mono", monospace';
const QUEUE_GRID = "150px minmax(0,1fr) 110px 130px 62px";

export default function AdminQueuePage() {
  const { session } = useAuth();
  const user = session?.user ?? null;
  const queue = usePolling<QueueItem[]>(() => api("/api/admin/queue"), 5000);
  const incidents = usePolling<Incident[]>(() => api("/api/admin/incidents"), 5000);

  const activeIncident = (incidents.data ?? []).find((i) => i.status === "active") ?? null;
  const forbidden = queue.error && [401, 403].includes(queue.error.status);

  return (
    <main className="page">
      <header className="page-head">
        <div className="page-head-icon">
          <ShieldCheck aria-hidden="true" />
        </div>
        <div>
          <h1 className="page-head-title">Очередь поддержки</h1>
          <p className="page-head-sub">
            {user
              ? `${user.full_name} · ${user.group_name ?? "Служба техподдержки"}`
              : "Служба техподдержки"}
          </p>
        </div>
        <span className="chip" style={{ marginLeft: "auto" }}>
          <span className="chip-dot" aria-hidden="true" />
          {queue.updatedAt
            ? `Обновлено ${fmtTime(queue.updatedAt.toISOString())}`
            : "Обновление…"}
        </span>
      </header>

      <AdminTabs />

      {activeIncident && (
        <section
          className="card anim-in"
          role="alert"
          style={{
            ["--d" as string]: "60ms",
            display: "flex",
            alignItems: "center",
            gap: 14,
            padding: "16px 18px",
            borderColor: "#ffc8ca",
            background: "#fff4f4",
          }}
        >
          <div className="icon-tile" data-tone="red">
            <Siren aria-hidden="true" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <b style={{ fontSize: 13, color: "#c63343" }}>
              Активный инцидент · {activeIncident.service} · {activeIncident.category}
            </b>
            <p style={{ margin: "3px 0 0", color: "#9b6069", fontSize: 12 }}>
              с {fmtTime(activeIncident.window_start)} · затронуто обращений:{" "}
              {activeIncident.request_count}
            </p>
          </div>
          <Link className="btn btn-danger btn-sm" href="/admin/tools">
            Подробнее в инструментах
          </Link>
        </section>
      )}

      <StatusBoard />

      {forbidden && (
        <div className="api-error" role="alert">
          <AlertTriangle />
          {queue.error?.message}
        </div>
      )}
      {!forbidden && queue.error && !queue.data && (
        <div className="api-error" role="alert">
          <AlertTriangle />
          {queue.error.message}
        </div>
      )}

      <section className="card card-pad anim-in" style={{ ["--d" as string]: "120ms" }}>
        <header
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 12,
            marginBottom: 14,
          }}
        >
          <h2 className="card-title" style={{ margin: 0 }}>
            <Inbox aria-hidden="true" />
            Обращения · сортировка по приоритету
          </h2>
          <span className="chip">{queue.data?.length ?? 0} в очереди</span>
        </header>

        {queue.loading && !queue.data && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ height: 54 }} />
            ))}
          </div>
        )}

        {queue.data && queue.data.length === 0 && (
          <div className="empty">
            <img className="empty-mascot" src="/maia-mascot.jpg" alt="MAIA" />
            <h3 className="empty-title">Очередь пуста — MAIA справляется</h3>
            <p className="empty-text">Новые обращения появятся здесь автоматически.</p>
          </div>
        )}

        {queue.data && queue.data.length > 0 && (
          <>
            <div className="queue-head" style={{ gridTemplateColumns: QUEUE_GRID }}>
              <span>Приоритет</span>
              <span>Обращение</span>
              <span>Канал</span>
              <span>Статус</span>
              <span>Время</span>
            </div>
            {queue.data.map((q) => (
              <Link
                className="queue-line"
                key={q.request_id}
                href={`/admin/tickets/${q.request_id}`}
                style={{ gridTemplateColumns: QUEUE_GRID, cursor: "pointer" }}
              >
                <PriorityBadge priority={q.priority} />
                <p>
                  {q.summary}
                  <small>
                    <span style={{ fontFamily: MONO }}>{q.number}</span>
                    {q.user ? ` · ${q.user.full_name}` : ""} · {q.route_reason}
                    {q.incident_id ? ` · инцидент №${q.incident_id}` : ""}
                  </small>
                  {q.user === null && (
                    <span
                      className="chip"
                      style={{ marginLeft: 8, padding: "2px 8px", fontSize: 10 }}
                    >
                      <UserX size={11} aria-hidden="true" />
                      Гость
                    </span>
                  )}
                </p>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  {q.channel === "telegram" ? (
                    <Send size={12} aria-hidden="true" />
                  ) : (
                    <Globe size={12} aria-hidden="true" />
                  )}
                  {q.channel === "telegram" ? "telegram" : "web"}
                </span>
                <span className="chip">{q.status}</span>
                <time>{fmtTime(q.created_at)}</time>
              </Link>
            ))}
          </>
        )}
      </section>
    </main>
  );
}
