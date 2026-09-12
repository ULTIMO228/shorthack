"use client";

import { AlertTriangle, Gauge, Inbox, Percent, Siren, Timer } from "lucide-react";
import { api } from "@/lib/api";
import AdminTabs from "@/components/admin-tabs";
import { usePolling } from "@/lib/use-polling";
import { fmtTime } from "@/lib/format";
import type { Metrics } from "@/lib/types";

type MetricCard = {
  key: string;
  label: string;
  hint: string;
  tone: "green" | "cyan" | "red" | "blue" | "amber";
  icon: typeof Percent;
  value: (m: Metrics) => string;
};

const CARDS: MetricCard[] = [
  {
    key: "auto_closed",
    label: "Авто-закрытие",
    hint: "Обращений закрыто MAIA без оператора",
    tone: "green",
    icon: Percent,
    value: (m) => `${Math.round(m.auto_closed_pct)}%`,
  },
  {
    key: "first_reaction",
    label: "Первое реагирование",
    hint: "От создания обращения до первого ответа агента",
    tone: "cyan",
    icon: Timer,
    value: (m) => `${m.avg_first_reaction_sec.toFixed(1)} с`,
  },
  {
    key: "incidents",
    label: "Инциденты",
    hint: "Активные / всего за период",
    tone: "red",
    icon: Siren,
    value: (m) => `${m.incidents_active} / ${m.incidents_total}`,
  },
  {
    key: "requests",
    label: "Всего обращений",
    hint: "Принято системой за период демо",
    tone: "blue",
    icon: Inbox,
    value: (m) => String(m.requests_total),
  },
  {
    key: "escalations",
    label: "Открытые эскалации",
    hint: "Ожидают решения оператора в очереди",
    tone: "amber",
    icon: AlertTriangle,
    value: (m) => String(m.escalations_open),
  },
];

export default function AdminMetricsPage() {
  const { data, error, loading, updatedAt } = usePolling<Metrics>(() => api("/api/admin/metrics"), 5000);

  return (
    <main className="page">
      <header className="page-head">
        <span className="page-head-icon">
          <Gauge aria-hidden="true" />
        </span>
        <div>
          <h1 className="page-head-title">Метрики</h1>
          <p className="page-head-sub">Эффективность MAIA в реальном времени</p>
        </div>
        <span className="chip" style={{ marginLeft: "auto" }}>
          <span className="chip-dot" />
          {updatedAt ? `Обновлено ${fmtTime(updatedAt.toISOString())}` : "Обновление…"}
        </span>
      </header>

      <AdminTabs />

      {error && !data && (
        <div className="api-error" role="alert">
          <AlertTriangle />
          {error.message}
        </div>
      )}
      {loading && !data && !error && (
        <div className="stat-grid">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="skeleton" style={{ height: 86 }} />
          ))}
        </div>
      )}

      {data && (
        <div className="stat-grid">
          {CARDS.map((c, i) => {
            const Icon = c.icon;
            return (
              <div key={c.key} className="stat-card anim-in" style={{ ["--d" as string]: `${i * 60}ms` }}>
                <span className="icon-tile" data-tone={c.tone}>
                  <Icon aria-hidden="true" />
                </span>
                <div>
                  <div className="stat-value">{c.value(data)}</div>
                  <div className="stat-label">{c.label}</div>
                  <div className="stat-label" style={{ marginTop: 2, fontSize: 11 }}>
                    {c.hint}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </main>
  );
}
