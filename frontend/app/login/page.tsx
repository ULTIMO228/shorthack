"use client";

import { FormEvent, Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  BookOpen,
  LockKeyhole,
  LogIn,
  Mail,
  ShieldCheck,
  UserRound,
  Zap,
} from "lucide-react";
import { useAuth } from "@/components/auth-provider";
import { ApiError } from "@/lib/api";

const FEATURES = [
  {
    icon: Zap,
    tone: "blue",
    title: "Мгновенная первая реакция",
    text: "Авто-проверка сервисов и ответ за секунды, без очереди",
  },
  {
    icon: ShieldCheck,
    tone: "green",
    title: "Ничего не потеряется",
    text: "Сложное эскалируется оператору с полной историей обращения",
  },
  {
    icon: BookOpen,
    tone: "violet",
    title: "Ответы по базе знаний",
    text: "Строго по статьям НИТУ МИСИС, со ссылками на источники",
  },
] as const;

const DEMO_ACCOUNTS = [
  { label: "Студент", email: "ivanov@misis.ru", password: "student123", color: "var(--blue)" },
  { label: "Сотрудник", email: "kozlova@misis.ru", password: "staff123", color: "#0d8fa1" },
  { label: "Оператор", email: "smirnov@misis.ru", password: "operator123", color: "var(--ai)" },
] as const;

function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(null);
    setBusy(true);
    try {
      await login(email, password);
      const from = params.get("from");
      router.push(from && from.startsWith("/") && !from.startsWith("//") ? from : "/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Ошибка сети, попробуйте ещё раз");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="card card-pad anim-in"
      style={{ ["--d" as string]: "90ms", display: "flex", flexDirection: "column", gap: 15 }}
      onSubmit={submit}
    >
      <h2 className="card-title" style={{ marginBottom: 0 }}>
        <UserRound />
        Вход по почте МИСИС
      </h2>
      <p className="field-hint" style={{ margin: 0 }}>
        Доступны домены @misis.ru и @edu.misis.ru
      </p>
      <label className="field">
        <span className="field-label">Почта МИСИС</span>
        <span style={{ position: "relative", display: "block" }}>
          <Mail
            size={16}
            style={{
              position: "absolute",
              left: 13,
              top: "50%",
              transform: "translateY(-50%)",
              color: "var(--muted)",
              pointerEvents: "none",
            }}
          />
          <input
            className="input"
            style={{ paddingLeft: 38 }}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="ivanov@misis.ru"
            required
            autoFocus
          />
        </span>
      </label>
      <label className="field">
        <span className="field-label">Пароль</span>
        <span style={{ position: "relative", display: "block" }}>
          <LockKeyhole
            size={16}
            style={{
              position: "absolute",
              left: 13,
              top: "50%",
              transform: "translateY(-50%)",
              color: "var(--muted)",
              pointerEvents: "none",
            }}
          />
          <input
            className="input"
            style={{ paddingLeft: 38 }}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Пароль от учётной записи"
            required
          />
        </span>
      </label>
      {error && (
        <p
          className="field-err"
          role="alert"
          style={{ display: "flex", alignItems: "center", gap: 6, margin: 0 }}
        >
          <AlertTriangle size={14} style={{ flex: "0 0 auto" }} />
          {error}
        </p>
      )}
      <button className="btn btn-primary" type="submit" disabled={busy}>
        <LogIn />
        {busy ? "Входим…" : "Войти"}
      </button>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span className="field-hint">Демо-доступ — нажмите, чтобы подставить логин и пароль:</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {DEMO_ACCOUNTS.map((d) => (
            <button
              key={d.email}
              type="button"
              className="chip"
              title={`Подставить ${d.email} / ${d.password}`}
              onClick={() => {
                setEmail(d.email);
                setPassword(d.password);
                setError(null);
              }}
            >
              <span className="chip-dot" style={{ background: d.color }} />
              {d.label} · {d.email} / {d.password}
            </button>
          ))}
        </div>
      </div>
      <p className="field-hint" style={{ margin: 0 }}>
        Можно продолжить без входа —{" "}
        <Link href="/" style={{ color: "var(--blue)", fontWeight: 700, textDecoration: "none" }}>
          гостевой режим
        </Link>
      </p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <main className="auth-page">
      <section className="auth-copy anim-in">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/maia-mascot.jpg"
          alt="Маскот MAIA"
          className="mascot-float"
          style={{
            width: "clamp(140px, 30vw, 220px)",
            height: "clamp(140px, 30vw, 220px)",
            borderRadius: 40,
            objectFit: "cover",
            filter: "drop-shadow(0 22px 42px rgba(16,32,61,.2))",
          }}
        />
        <h1 style={{ margin: "26px 0 0" }}>
          M<em>AI</em>A
        </h1>
        <p className="lead">ИИ-помощник поддержки НИТУ МИСИС</p>
        <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 38 }}>
          {FEATURES.map((f) => (
            <div key={f.title} style={{ display: "flex", alignItems: "center", gap: 13 }}>
              <span className="icon-tile" data-tone={f.tone}>
                <f.icon />
              </span>
              <div>
                <b style={{ display: "block", fontSize: 14, letterSpacing: "-.01em" }}>{f.title}</b>
                <span
                  style={{
                    display: "block",
                    marginTop: 2,
                    color: "var(--muted)",
                    fontSize: 12,
                    lineHeight: 1.45,
                  }}
                >
                  {f.text}
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </main>
  );
}
