"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  BriefcaseBusiness,
  ChevronDown,
  ChevronRight,
  CircleUserRound,
  Clock,
  CreditCard,
  FileText,
  GraduationCap,
  Headset,
  LogIn,
  LogOut,
  Mail,
  Medal,
  PackageOpen,
  ShieldAlert,
  ShieldCheck,
  TicketCheck,
  UserRound,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/components/auth-provider";
import { useToast } from "@/components/toast";
import { usePolling } from "@/lib/use-polling";
import { fmtDateTime, initials } from "@/lib/format";
import { PriorityBadge, RouteBadge } from "@/components/badges";
import {
  CERT_STATUS_CHAIN,
  type CertCatalogItem,
  type CertOrder,
  type RequestDetail,
  type RequestListItem,
  type User,
} from "@/lib/types";

// Ядро закрывает тикеты статусами «решена» → «закрыта» (backend/app/tickets.py)
const TICKET_DONE_STATUSES = ["решена", "закрыта"];

const ROLE_META: Record<string, { label: string; icon: typeof GraduationCap }> = {
  student: { label: "Студент", icon: GraduationCap },
  staff: { label: "Сотрудник", icon: BriefcaseBusiness },
  operator: { label: "Оператор", icon: ShieldCheck },
};

const CERT_META: Record<
  string,
  { icon: typeof FileText; tone: "blue" | "cyan" | "green" | "amber" | "red" | "violet" }
> = {
  payments: { icon: CreditCard, tone: "green" },
  callup: { icon: FileText, tone: "blue" },
  medical: { icon: ShieldAlert, tone: "red" },
  study: { icon: GraduationCap, tone: "cyan" },
  military: { icon: Medal, tone: "amber" },
};

const MONO_CHIP = { fontFamily: "'DM Mono', monospace", fontSize: 11 } as const;

function StatusChain({ status }: { status: string }) {
  const idx = CERT_STATUS_CHAIN.indexOf(status as (typeof CERT_STATUS_CHAIN)[number]);
  return (
    <div className="chain" aria-label={`Статус: ${status}`}>
      {CERT_STATUS_CHAIN.map((step, i) => (
        <span key={step} style={{ display: "contents" }}>
          {i > 0 && <span className={`chain-sep ${i <= idx ? "done" : ""}`} />}
          <span className={`chain-step ${i < idx ? "done" : i === idx ? (idx === CERT_STATUS_CHAIN.length - 1 ? "done" : "current") : ""}`}>
            <i />
            {step}
          </span>
        </span>
      ))}
    </div>
  );
}

function ProfileCard({ user }: { user: User }) {
  const { logout } = useAuth();
  const role = ROLE_META[user.role] ?? { label: user.role, icon: UserRound };
  const RoleIcon = role.icon;
  return (
    <section className="card card-pad anim-in" style={{ display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
      <span className="avatar avatar-lg">{initials(user.full_name)}</span>
      <div style={{ flex: "1 1 240px", minWidth: 220 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 800, letterSpacing: "-.02em" }}>{user.full_name}</h2>
          <span className="chip">
            <RoleIcon size={13} aria-hidden="true" />
            {role.label}
          </span>
        </div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", marginTop: 9 }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 7, color: "var(--muted)", fontSize: 13 }}>
            <Mail size={14} aria-hidden="true" />
            {user.email}
          </span>
          {user.group_name && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7, color: "var(--muted)", fontSize: 13 }}>
              <GraduationCap size={14} aria-hidden="true" />
              {user.group_name}
            </span>
          )}
        </div>
      </div>
      <button className="btn btn-ghost btn-sm" onClick={() => void logout()}>
        <LogOut aria-hidden="true" />
        Выйти
      </button>
    </section>
  );
}

function MyRequests() {
  const { data, error, loading } = usePolling<RequestListItem[]>(() => api("/api/requests"), 5000);
  const [openedId, setOpenedId] = useState<number | null>(null);
  const openedIdRef = useRef<number | null>(null);
  const [detail, setDetail] = useState<RequestDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  async function toggle(id: number) {
    if (openedIdRef.current === id) {
      openedIdRef.current = null;
      setOpenedId(null);
      setDetail(null);
      setDetailError(null);
      return;
    }
    openedIdRef.current = id;
    setOpenedId(id);
    setDetail(null);
    setDetailError(null);
    try {
      const result = await api<RequestDetail>(`/api/requests/${id}`);
      // Применяем ответ, только если карточка всё ещё открыта и не переключена
      if (openedIdRef.current !== id) return;
      setDetail(result);
    } catch (e) {
      if (openedIdRef.current !== id) return;
      setDetailError(e instanceof ApiError ? e.message : "Не удалось загрузить диалог");
    }
  }

  if (loading && !data)
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {[0, 1, 2].map((i) => (
          <div key={i} className="skeleton" style={{ height: 96 }} />
        ))}
      </div>
    );
  if (error && !data)
    return (
      <div className="api-error" role="alert">
        <AlertTriangle />
        {error.message}
      </div>
    );
  if (!data || data.length === 0)
    return (
      <div className="card empty anim-in">
        <img className="empty-mascot mascot-float" src="/maia-mascot.jpg" alt="Маскот MAIA" />
        <h3 className="empty-title">Обращений пока нет</h3>
        <p className="empty-text">Опишите проблему на главном экране — здесь появятся статусы и диалоги.</p>
        <Link className="btn btn-primary" href="/">
          <Headset aria-hidden="true" />
          Новое обращение
        </Link>
      </div>
    );

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {data.map((r, i) => {
        const opened = openedId === r.id;
        const done = TICKET_DONE_STATUSES.includes(r.ticket.status);
        const priorities = r.subtasks
          .map((s) => s.priority)
          .filter((p, idx, arr) => arr.indexOf(p) === idx);
        return (
          <article
            key={r.id}
            className="card card-pad anim-in"
            style={{ ["--d" as string]: `${Math.min(i, 8) * 50}ms`, cursor: "pointer" }}
            onClick={() => toggle(r.id)}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span className="chip" style={MONO_CHIP}>
                {r.ticket.number}
              </span>
              <span className={`status${done ? " done" : ""}`}>{r.ticket.status}</span>
              {priorities.map((p) => (
                <PriorityBadge key={p} priority={p} />
              ))}
              <span
                style={{
                  marginLeft: "auto",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  color: "var(--muted)",
                  fontSize: 12,
                }}
              >
                <Clock size={13} aria-hidden="true" />
                {fmtDateTime(r.created_at)}
                {opened ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
              </span>
            </div>
            <h3 style={{ margin: "10px 0 0", fontSize: 15, lineHeight: 1.45 }}>{r.masked_text}</h3>
            {!opened && (
              <p style={{ margin: "6px 0 0", color: "var(--muted)", fontSize: 12 }}>
                Нажмите, чтобы открыть диалог по обращению (только чтение)
              </p>
            )}
            {opened && (
              <div style={{ marginTop: 12 }}>
                {detailError && (
                  <div className="api-error" role="alert" style={{ margin: 0 }}>
                    <AlertTriangle />
                    {detailError}
                  </div>
                )}
                {!detail && !detailError && <div className="list-loading">Загрузка диалога…</div>}
                {detail && (
                  <>
                    {detail.subtasks.length > 0 && (
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        {detail.subtasks.map((s) => (
                          <span key={s.id} style={{ display: "contents" }}>
                            <RouteBadge route={s.route} />
                            <PriorityBadge priority={s.priority} />
                          </span>
                        ))}
                      </div>
                    )}
                    {detail.dialog.length > 0 ? (
                      <div className="conversation" style={{ flex: "none", padding: "14px 0 4px" }}>
                        {detail.dialog.map((m, idx) => (
                          <div className={`message ${m.role === "user" ? "user" : "bot"}`} key={`${idx}-${m.at}`}>
                            {m.role === "agent" && <i>AI</i>}
                            <p>{m.text}</p>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p style={{ margin: "12px 0 0", color: "var(--muted)", fontSize: 12 }}>
                        Диалог пуст — агент ещё не отвечал по этому обращению.
                      </p>
                    )}
                  </>
                )}
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}

function Certs() {
  const toast = useToast();
  const catalog = usePolling<CertCatalogItem[]>(() => api("/api/certs/catalog"), 60000);
  const orders = usePolling<CertOrder[]>(() => api("/api/certs/orders"), 5000);
  const [ordering, setOrdering] = useState<string | null>(null);

  async function order(certType: string) {
    if (ordering) return;
    setOrdering(certType);
    try {
      await api("/api/certs/orders", { method: "POST", body: { cert_type: certType } });
      toast.push("Заказ создан — статус появится в «Моих заказах»", "ok");
      orders.reload();
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Ошибка сети");
    } finally {
      setOrdering(null);
    }
  }

  return (
    <>
      {catalog.error && !catalog.data && (
        <div className="api-error" role="alert">
          <AlertTriangle />
          {catalog.error.message}
        </div>
      )}
      {catalog.loading && !catalog.data && !catalog.error && (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 14 }}>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 190 }} />
          ))}
        </div>
      )}
      {catalog.data && (
        <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 14 }}>
          {catalog.data.map((c, i) => {
            const meta = CERT_META[c.type] ?? { icon: FileText, tone: "blue" as const };
            const Icon = meta.icon;
            return (
              <article
                key={c.type}
                className="card card-pad anim-in"
                style={{ ["--d" as string]: `${i * 60}ms`, display: "flex", flexDirection: "column", gap: 10 }}
              >
                <span className="icon-tile" data-tone={meta.tone}>
                  <Icon aria-hidden="true" />
                </span>
                <h3 style={{ margin: 0, fontSize: 15, letterSpacing: "-.02em" }}>{c.title}</h3>
                <p style={{ margin: 0, flex: 1, color: "var(--muted)", fontSize: 12, lineHeight: 1.55 }}>{c.description}</p>
                <button
                  className="btn btn-primary btn-sm"
                  style={{ alignSelf: "flex-start" }}
                  onClick={() => order(c.type)}
                  disabled={ordering === c.type}
                >
                  <FileText aria-hidden="true" />
                  {ordering === c.type ? "Заказываем…" : "Заказать"}
                </button>
              </article>
            );
          })}
        </section>
      )}

      <section className="card card-pad anim-in" style={{ ["--d" as string]: "120ms", marginTop: 22 }}>
        <h2 className="card-title">
          <PackageOpen aria-hidden="true" />
          Мои заказы{orders.data ? ` · ${orders.data.length}` : ""}
        </h2>
        {orders.error && !orders.data && (
          <div className="api-error" role="alert" style={{ margin: "0 0 14px" }}>
            <AlertTriangle />
            {orders.error.message}
          </div>
        )}
        {orders.loading && !orders.data && !orders.error && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {[0, 1].map((i) => (
              <div key={i} className="skeleton" style={{ height: 64 }} />
            ))}
          </div>
        )}
        {orders.data && orders.data.length === 0 && (
          <div className="empty" style={{ padding: "28px 16px" }}>
            <img className="empty-mascot" src="/maia-mascot.jpg" alt="Маскот MAIA" style={{ width: 100, height: 100 }} />
            <h3 className="empty-title" style={{ fontSize: 15 }}>
              Заказов пока нет
            </h3>
            <p className="empty-text">Выберите справку из каталога выше — статус заказа появится здесь.</p>
          </div>
        )}
        {orders.data?.map((o) => {
          const pickedUp = o.status === "забрана";
          return (
            <div
              key={o.id}
              style={{ display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap", padding: "14px 0", borderTop: "1px solid var(--line)" }}
            >
              <span className="chip" style={MONO_CHIP}>
                №{o.id}
              </span>
              <div style={{ flex: "1 1 320px", minWidth: 240 }}>
                <h3 style={{ margin: 0, fontSize: 14 }}>{o.title}</h3>
                <p style={{ margin: "3px 0 0", color: "var(--muted)", fontSize: 11 }}>заказана {fmtDateTime(o.created_at)}</p>
                <StatusChain status={o.status} />
              </div>
              <span
                className="chip"
                style={pickedUp ? { borderColor: "#b7e9cf", background: "var(--ok-bg)", color: "var(--ok)" } : undefined}
              >
                <span className="chip-dot" style={pickedUp ? { background: "var(--ok)" } : undefined} />
                {o.status}
              </span>
            </div>
          );
        })}
      </section>
    </>
  );
}

export default function CabinetPage() {
  const { session, loading } = useAuth();
  const router = useRouter();
  const [tab, setTab] = useState<"tickets" | "certs">("tickets");
  const user = session?.user ?? null;

  if (loading) {
    return (
      <main className="page">
        <div className="skeleton" style={{ height: 104 }} />
        <div className="skeleton" style={{ height: 220 }} />
      </main>
    );
  }

  if (!user) {
    return (
      <main className="page">
        <div className="card empty anim-in" style={{ marginTop: 60 }}>
          <img className="empty-mascot mascot-float" src="/maia-mascot.jpg" alt="Маскот MAIA" />
          <h3 className="empty-title">Кабинет доступен после входа</h3>
          <p className="empty-text">
            Войдите по почте МИСИС, чтобы видеть свои обращения, заказывать справки и следить за статусами.
          </p>
          <button className="btn btn-primary" onClick={() => router.push("/login?from=/cabinet")}>
            <LogIn aria-hidden="true" />
            Войти
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="page">
      <header className="page-head" style={{ flexWrap: "wrap" }}>
        <span className="page-head-icon">
          <CircleUserRound aria-hidden="true" />
        </span>
        <div>
          <h1 className="page-head-title">Личный кабинет</h1>
          <p className="page-head-sub">Обращения, диалоги с MAIA и заказ справок — всё под рукой</p>
        </div>
        <Link className="btn btn-primary" style={{ marginLeft: "auto" }} href="/">
          <Headset aria-hidden="true" />
          Новое обращение
        </Link>
      </header>

      <ProfileCard user={user} />

      <div className="cabinet-tabs" style={{ marginTop: 0 }}>
        <button
          className={tab === "tickets" ? "selected" : ""}
          onClick={() => setTab("tickets")}
          style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
        >
          <TicketCheck size={15} aria-hidden="true" />
          Мои обращения
        </button>
        <button
          className={tab === "certs" ? "selected" : ""}
          onClick={() => setTab("certs")}
          style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
        >
          <FileText size={15} aria-hidden="true" />
          Справки
        </button>
      </div>

      {tab === "tickets" ? <MyRequests /> : <Certs />}
    </main>
  );
}
