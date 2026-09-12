"use client";

import { api } from "@/lib/api";
import { usePolling } from "@/lib/use-polling";
import { fmtTime } from "@/lib/format";
import type { ServiceStatus } from "@/lib/types";

function isUp(s: ServiceStatus): boolean {
  // Эмулируемый сервис: state=down → красная независимо от last_check (контракт состояний UI)
  if (s.check_type === "emulated") return s.state !== "down";
  return s.last_check ? s.last_check.ok : s.state !== "down";
}

function hint(s: ServiceStatus): string {
  if (!isUp(s)) return "Требуется внимание";
  if (s.check_type === "real" && s.last_check) {
    return `${fmtTime(s.last_check.checked_at)} · ${s.last_check.http_code ?? "—"} OK`;
  }
  return "Сервис работает";
}

export default function StatusBoard() {
  const { data, error, loading } = usePolling<ServiceStatus[]>(
    () => api<ServiceStatus[]>("/api/admin/status-board"),
    5000,
  );

  if (loading && !data) return <div className="list-loading">Загрузка статусов сервисов…</div>;
  if (error && !data) return <div className="api-error">{error.message}</div>;
  if (!data) return null;

  return (
    <section className="service-row" aria-label="Статус сервисов">
      {data.map((s) => (
        <article key={s.name} className={isUp(s) ? "" : "down"}>
          <i />
          <span>
            {s.name}
            <small>{hint(s)}</small>
          </span>
        </article>
      ))}
    </section>
  );
}
