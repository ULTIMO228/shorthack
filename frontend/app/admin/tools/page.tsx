"use client";

import { FormEvent, useRef, useState } from "react";
import {
  AlertTriangle,
  FlaskConical,
  Globe,
  MonitorSmartphone,
  Play,
  RadioTower,
  Terminal as TerminalIcon,
  Waves,
  Wifi,
  WifiOff,
  Wrench,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import AdminTabs from "@/components/admin-tabs";
import StatusBoard from "@/components/status-board";
import { useToast } from "@/components/toast";
import { usePolling } from "@/lib/use-polling";
import { fmtTime } from "@/lib/format";
import type { ServiceStatus, Tool, ToolResult } from "@/lib/types";

type TermEntry = { id: number; at: string; title: string; ok: boolean; body: string };
type NewTermEntry = Omit<TermEntry, "id">;
type Option = { value: string; label: string };

// Слаги строго как в choices бэкенда (backend/app/tools.py) — дисплейные имена дают 422
const SERVICE_OPTIONS: Option[] = [
  { value: "site", label: "Сайт" },
  { value: "lms", label: "ЛМС" },
  { value: "wifi_guest", label: "Wi-Fi Guest" },
  { value: "wifi_edu", label: "Wi-Fi EDU" },
  { value: "wifi_corp", label: "Wi-Fi CORP" },
];

const WIFI_OPTIONS: Option[] = SERVICE_OPTIONS.filter((o) => o.value.startsWith("wifi_"));

// GET /api/admin/tools не отдаёт choices — select-списки задаём на фронте по схеме tools.py
const PARAM_OPTIONS: Record<string, Record<string, Option[]>> = {
  simulate_wave: { service: SERVICE_OPTIONS },
  check_wifi: { network: WIFI_OPTIONS },
};

const PARAM_LABEL: Record<string, string> = {
  service: "Сервис",
  count: "Количество",
  network: "Сеть",
  url: "URL",
};

const TOOL_META: Record<string, { icon: typeof Globe; tone: string }> = {
  check_site: { icon: Globe, tone: "blue" },
  check_lms: { icon: MonitorSmartphone, tone: "cyan" },
  check_wifi: { icon: Wifi, tone: "green" },
  simulate_wave: { icon: Waves, tone: "violet" },
};

function Terminal({ entries }: { entries: TermEntry[] }) {
  return (
    <div
      className="terminal anim-in"
      style={{ background: "var(--ink)", ["--d" as string]: "120ms" }}
      aria-live="polite"
    >
      <div className="t-head">
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <TerminalIcon size={11} aria-hidden="true" />
          вывод инструментов
        </span>
        <span>shorthack-core :8000</span>
      </div>
      {entries.length === 0 ? (
        <pre className="t-dim">// запустите инструмент — результат появится здесь</pre>
      ) : (
        entries.map((e) => (
          <pre key={e.id} className={e.ok ? "t-ok" : "t-err"}>
            <span className="t-dim">[{e.at}] {e.title}</span>
            {"\n"}
            {e.body}
          </pre>
        ))
      )}
    </div>
  );
}

function ToolCard({
  tool,
  index,
  onResult,
}: {
  tool: Tool;
  index: number;
  onResult: (e: NewTermEntry) => void;
}) {
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      tool.params.map((p) => [
        p.name,
        p.default != null
          ? String(p.default)
          : (PARAM_OPTIONS[tool.name]?.[p.name]?.[0]?.value ?? ""),
      ]),
    ),
  );
  const [busy, setBusy] = useState(false);

  async function invoke(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      const params = Object.fromEntries(
        tool.params.flatMap((p): [string, string | number][] => {
          const raw = values[p.name];
          if (p.type === "number" || p.type === "int" || p.type === "integer") {
            // Пустое числовое поле не отправляем — бэкенд подставит default
            return raw.trim() === "" ? [] : [[p.name, Number(raw)]];
          }
          return [[p.name, raw]];
        }),
      );
      const res = await api<ToolResult>(`/api/admin/tools/${tool.name}/invoke`, {
        method: "POST",
        body: { params },
      });
      onResult({
        at: fmtTime(new Date().toISOString()),
        title: `${tool.name} → ok=${res.ok}`,
        ok: res.ok,
        body: JSON.stringify(res.result, null, 2),
      });
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Ошибка сети";
      onResult({ at: fmtTime(new Date().toISOString()), title: `${tool.name} → ошибка`, ok: false, body: msg });
      toast.push(msg);
    } finally {
      setBusy(false);
    }
  }

  const meta = TOOL_META[tool.name] ?? { icon: Wrench, tone: "blue" };
  const Icon = meta.icon;
  const missingChoice = tool.params.some(
    (p) => PARAM_OPTIONS[tool.name]?.[p.name] && !values[p.name],
  );

  return (
    <form
      className="card card-pad anim-in"
      style={{ ["--d" as string]: `${index * 60}ms` }}
      onSubmit={invoke}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <span className="icon-tile" data-tone={meta.tone}>
          <Icon aria-hidden="true" />
        </span>
        <div style={{ minWidth: 0 }}>
          <h3 style={{ margin: 0, fontSize: 14, letterSpacing: "-.02em", fontFamily: '"DM Mono", monospace' }}>
            {tool.name}
          </h3>
          <p className="field-hint" style={{ margin: "3px 0 0" }}>
            {tool.description}
          </p>
        </div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
        {tool.params.map((p) => {
          const opts = PARAM_OPTIONS[tool.name]?.[p.name];
          return (
            <label key={p.name} className="field" style={{ flex: "1 1 180px" }}>
              <span className="field-label">
                {PARAM_LABEL[p.name] ?? p.name} · {p.type}
              </span>
              {opts ? (
                <>
                  <select
                    className="input"
                    value={values[p.name] ?? ""}
                    onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}
                  >
                    {opts.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                  <span className="field-hint">slug: {values[p.name]}</span>
                </>
              ) : (
                <input
                  className="input"
                  value={values[p.name] ?? ""}
                  onChange={(e) => setValues({ ...values, [p.name]: e.target.value })}
                  placeholder={p.default != null ? String(p.default) : p.name}
                />
              )}
            </label>
          );
        })}
        <button type="submit" className="btn btn-primary btn-sm" disabled={busy || missingChoice}>
          <Play aria-hidden="true" />
          {busy ? "Выполняем…" : "Запустить"}
        </button>
      </div>
    </form>
  );
}

function WifiSwitches({ onResult }: { onResult: (e: NewTermEntry) => void }) {
  const toast = useToast();
  const board = usePolling<ServiceStatus[]>(() => api("/api/admin/status-board"), 5000);
  const [busyName, setBusyName] = useState<string | null>(null);

  async function flip(svc: ServiceStatus) {
    const id = svc.id;
    if (id == null || svc.check_type !== "emulated" || busyName) return;
    const next = svc.state === "down" ? "up" : "down";
    setBusyName(svc.name);
    try {
      await api(`/api/admin/services/${id}`, { method: "PATCH", body: { state: next } });
      onResult({
        at: fmtTime(new Date().toISOString()),
        title: `services/${id} → ${svc.name} = ${next}`,
        ok: true,
        body: `эмулируемый сервис переведён в состояние «${next === "down" ? "сбой" : "норма"}»`,
      });
      board.reload();
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Ошибка сети");
    } finally {
      setBusyName(null);
    }
  }

  const emulated = (board.data ?? []).filter((s) => s.check_type === "emulated");

  return (
    <section className="card card-pad anim-in">
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 6 }}>
        <span className="icon-tile" data-tone="green">
          <RadioTower aria-hidden="true" />
        </span>
        <div>
          <h3 style={{ margin: 0, fontSize: 14, letterSpacing: "-.02em" }}>Wi-Fi сети (эмуляция)</h3>
          <p className="field-hint" style={{ margin: "3px 0 0" }}>
            Переключение «норма/сбой» — сразу влияет на статус-борд и реакции агента.
          </p>
        </div>
      </div>
      {board.loading && !board.data && <div className="list-loading">Загрузка сервисов…</div>}
      {board.error && !board.data && <div className="api-error">{board.error.message}</div>}
      {board.data && emulated.length === 0 && (
        <p className="field-hint">Эмулируемых сервисов нет — ядро вернуло только реальные проверки.</p>
      )}
      {emulated.map((svc) => {
        const down = svc.state === "down";
        return (
          <div className="esc-check" key={svc.name}>
            {down ? (
              <WifiOff className="fail" aria-hidden="true" />
            ) : (
              <Wifi className="ok" aria-hidden="true" />
            )}
            {svc.name}
            <small>{down ? "сбой" : "норма"}</small>
            <button
              className={`toggle ${down ? "off" : "on"}`}
              onClick={() => flip(svc)}
              disabled={busyName !== null || svc.id == null}
              aria-label={`${svc.name}: ${down ? "сбой" : "норма"}`}
              title={svc.id == null ? "ядро не вернуло id сервиса" : undefined}
            />
          </div>
        );
      })}
    </section>
  );
}

export default function AdminToolsPage() {
  const toast = useToast();
  const tools = usePolling<Tool[]>(() => api("/api/admin/tools"), 60000);
  const [entries, setEntries] = useState<TermEntry[]>([]);
  const nextEntryId = useRef(1);
  const [waveService, setWaveService] = useState("wifi_edu");
  const [waveBusy, setWaveBusy] = useState(false);

  function push(entry: NewTermEntry) {
    const withId: TermEntry = { ...entry, id: nextEntryId.current++ };
    setEntries((list) => [withId, ...list].slice(0, 20));
  }

  async function wave() {
    if (waveBusy) return;
    setWaveBusy(true);
    try {
      const res = await api<ToolResult>("/api/admin/tools/simulate_wave/invoke", {
        method: "POST",
        body: { params: { service: waveService, count: 3 } },
      });
      push({
        at: fmtTime(new Date().toISOString()),
        title: `simulate_wave → ok=${res.ok}`,
        ok: res.ok,
        body: JSON.stringify(res.result, null, 2),
      });
      toast.push("Волна из 3 жалоб запущена — смотрите очередь", "ok");
    } catch (e) {
      toast.push(e instanceof ApiError ? e.message : "Ошибка сети");
    } finally {
      setWaveBusy(false);
    }
  }

  return (
    <main className="page">
      <header className="page-head">
        <span className="page-head-icon">
          <Wrench aria-hidden="true" />
        </span>
        <div>
          <h1 className="page-head-title">Демо-пульт</h1>
          <p className="page-head-sub">Диагностика и симуляция сервисов</p>
        </div>
      </header>

      <AdminTabs />

      <StatusBoard />

      <div className="tools-grid">
        <WifiSwitches onResult={push} />

        <section className="card card-pad anim-in" style={{ ["--d" as string]: "60ms" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
            <span className="icon-tile" data-tone="violet">
              <Waves aria-hidden="true" />
            </span>
            <div>
              <h3 style={{ margin: 0, fontSize: 14, letterSpacing: "-.02em" }}>Волна жалоб</h3>
              <p className="field-hint" style={{ margin: "3px 0 0" }}>
                Три однотипных обращения от тестовых пользователей — так рождается инцидент в очереди.
              </p>
            </div>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}>
            <label className="field" style={{ flex: "1 1 180px" }}>
              <span className="field-label">Сервис</span>
              <select
                className="input"
                value={waveService}
                onChange={(e) => setWaveService(e.target.value)}
              >
                {SERVICE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <span className="field-hint">slug: {waveService}</span>
            </label>
            <button onClick={wave} disabled={waveBusy} type="button" className="btn btn-primary btn-sm">
              <Play aria-hidden="true" />
              {waveBusy ? "Запускаем…" : "Симулировать волну (×3)"}
            </button>
          </div>
        </section>
      </div>

      <h2 className="card-title" style={{ margin: "4px 0 -8px" }}>
        <FlaskConical aria-hidden="true" />
        Реестр проверок
      </h2>
      {tools.error && !tools.data && (
        <div className="api-error" role="alert">
          <AlertTriangle />
          {tools.error.message}
        </div>
      )}
      {tools.loading && !tools.data && (
        <div className="tools-grid">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="skeleton" style={{ height: 150 }} />
          ))}
        </div>
      )}
      {tools.data && tools.data.length === 0 && (
        <div className="card empty">
          <img className="empty-mascot" src="/maia-mascot.jpg" alt="Маскот MAIA" />
          <h3 className="empty-title">Инструменты не найдены</h3>
          <p className="empty-text">Ядро не вернуло ни одной проверки — попробуйте обновить страницу.</p>
        </div>
      )}
      <div className="tools-grid">
        {(tools.data ?? []).map((t, i) => (
          <ToolCard key={t.name} tool={t} index={i} onResult={push} />
        ))}
      </div>

      <h2 className="card-title" style={{ margin: "4px 0 -8px" }}>
        <TerminalIcon aria-hidden="true" />
        Вывод
      </h2>
      <Terminal entries={entries} />
    </main>
  );
}
