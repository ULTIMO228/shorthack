"use client";

import { useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  Banknote,
  CheckCircle2,
  FileCheck,
  FileText,
  GraduationCap,
  PhoneCall,
  Shield,
  Stethoscope,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import AdminTabs from "@/components/admin-tabs";
import { useToast } from "@/components/toast";
import { usePolling } from "@/lib/use-polling";
import { fmtDateTime, fmtTime } from "@/lib/format";
import { CERT_STATUS_CHAIN, type AdminCertOrder } from "@/lib/types";

const CERT_ICON: Record<string, typeof FileText> = {
  payments: Banknote,
  callup: PhoneCall,
  medical: Stethoscope,
  study: GraduationCap,
  military: Shield,
};

const STATUS_COLOR: Record<string, string> = {
  "не обработана": "var(--muted)",
  "обрабатывается": "var(--blue)",
  "готова и ждёт выдачи": "var(--warn)",
  "забрана": "var(--ok)",
};

function nextStatus(status: string): string | null {
  const idx = CERT_STATUS_CHAIN.indexOf(status as (typeof CERT_STATUS_CHAIN)[number]);
  return idx >= 0 && idx < CERT_STATUS_CHAIN.length - 1 ? CERT_STATUS_CHAIN[idx + 1] : null;
}

function StatusCell({ status }: { status: string }) {
  const idx = CERT_STATUS_CHAIN.indexOf(status as (typeof CERT_STATUS_CHAIN)[number]);
  const color = STATUS_COLOR[status] ?? "var(--blue)";
  return (
    <div>
      <span className="chip">
        <span className="chip-dot" style={{ background: color }} />
        {status}
      </span>
      <div
        style={{ display: "flex", gap: 4, marginTop: 7, paddingLeft: 2 }}
        title={CERT_STATUS_CHAIN.join(" → ")}
        aria-label={`Шаг ${idx + 1} из ${CERT_STATUS_CHAIN.length}`}
      >
        {CERT_STATUS_CHAIN.map((s, i) => (
          <span
            key={s}
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: i <= idx ? color : "var(--line)",
            }}
          />
        ))}
      </div>
    </div>
  );
}

export default function AdminCertsPage() {
  const toast = useToast();
  const orders = usePolling<AdminCertOrder[]>(() => api("/api/admin/certs/orders"), 5000);
  const [movingId, setMovingId] = useState<number | null>(null);
  const [flashId, setFlashId] = useState<number | null>(null);

  async function advance(order: AdminCertOrder) {
    const next = nextStatus(order.status);
    if (!next || movingId) return;
    setMovingId(order.id);
    try {
      await api(`/api/admin/certs/orders/${order.id}`, { method: "PATCH", body: { status: next } });
      setFlashId(order.id);
      setTimeout(() => setFlashId(null), 1300);
      await orders.reload();
    } catch (e) {
      // 409 ядра — тост с detail
      toast.push(e instanceof ApiError ? e.message : "Ошибка сети");
    } finally {
      setMovingId(null);
    }
  }

  const forbidden = orders.error && [401, 403].includes(orders.error.status);

  return (
    <main className="page">
      <header className="page-head">
        <span className="page-head-icon">
          <FileCheck />
        </span>
        <div>
          <h1 className="page-head-title">Заказы справок</h1>
          <p className="page-head-sub">Обработка заявок студентов и сотрудников</p>
        </div>
        <span className="chip" style={{ marginLeft: "auto" }}>
          <span className="chip-dot" />
          {orders.updatedAt ? `Обновлено ${fmtTime(orders.updatedAt.toISOString())}` : "Обновление…"}
        </span>
      </header>

      <AdminTabs />

      {orders.error && !orders.data && (
        <div className="api-error" role="alert">
          <AlertTriangle />
          {orders.error.message}
        </div>
      )}

      {!forbidden && (
        <section className="card anim-in" style={{ ["--d" as string]: "60ms" }}>
          <div className="card-pad" style={{ paddingBottom: 0 }}>
            <h2 className="card-title">
              <FileCheck />
              Все заказы
              <span className="chip" style={{ marginLeft: "auto" }}>
                {orders.data?.length ?? 0}
              </span>
            </h2>
          </div>

          {orders.loading && !orders.data && (
            <div className="card-pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {[0, 1, 2].map((i) => (
                <div key={i} className="skeleton" style={{ height: 44 }} />
              ))}
            </div>
          )}

          {orders.data && orders.data.length === 0 && (
            <div className="empty">
              <img className="empty-mascot" src="/maia-mascot.jpg" alt="Маскот MAIA" />
              <h3 className="empty-title">Заказов пока нет</h3>
              <p className="empty-text">
                Новые заявки появятся здесь автоматически — после оформления из кабинета
                или через агента.
              </p>
            </div>
          )}

          {orders.data && orders.data.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table className="table">
                <thead>
                  <tr>
                    <th>№</th>
                    <th>Заявитель</th>
                    <th>Тип справки</th>
                    <th>Статус</th>
                    <th>Создана</th>
                    <th>Действие</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.data.map((o) => {
                    const next = nextStatus(o.status);
                    const CertIcon = CERT_ICON[o.cert_type] ?? FileText;
                    return (
                      <tr key={o.id} className={flashId === o.id ? "flash" : ""}>
                        <td className="mono" style={{ color: "var(--muted)" }}>
                          №{o.id}
                        </td>
                        <td>
                          {o.user ? (
                            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                              <span style={{ fontWeight: 700 }}>{o.user.full_name}</span>
                              <span style={{ color: "var(--muted)", fontSize: 12 }}>
                                {o.user.email}
                              </span>
                              {o.user.group_name && (
                                <span className="chip" style={{ alignSelf: "flex-start" }}>
                                  {o.user.group_name}
                                </span>
                              )}
                            </div>
                          ) : (
                            <span style={{ color: "var(--muted)" }}>гость</span>
                          )}
                        </td>
                        <td>
                          <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                            <CertIcon
                              style={{ width: 16, height: 16, color: "var(--blue)", flex: "0 0 auto" }}
                            />
                            {o.title}
                          </span>
                        </td>
                        <td>
                          <StatusCell status={o.status} />
                        </td>
                        <td className="mono" style={{ whiteSpace: "nowrap" }}>
                          {fmtDateTime(o.created_at)}
                        </td>
                        <td>
                          {next ? (
                            <button
                              className="btn btn-ghost btn-sm"
                              onClick={() => advance(o)}
                              disabled={movingId !== null}
                              title={`Перевести в статус «${next}»`}
                            >
                              <ArrowRight />
                              {movingId === o.id ? "Сохраняем…" : next}
                            </button>
                          ) : (
                            <span className="chip">
                              <CheckCircle2 style={{ width: 14, height: 14, color: "var(--ok)" }} />
                              Цепочка завершена
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
