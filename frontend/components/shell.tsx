"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { LogIn, LogOut, MessageSquarePlus, ShieldCheck, UserRound } from "lucide-react";
import { useAuth } from "@/components/auth-provider";
import { initials } from "@/lib/format";

type NavItem = { href: string; label: string; icon: typeof MessageSquarePlus };

const ROLE_LABEL: Record<string, string> = {
  student: "Студент",
  staff: "Сотрудник",
  operator: "Оператор",
};

export default function Shell({ children }: { children: React.ReactNode }) {
  const { session, loading, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const user = session?.user ?? null;

  const links: NavItem[] = [{ href: "/", label: "Новое обращение", icon: MessageSquarePlus }];
  if (user) links.push({ href: "/cabinet", label: "Кабинет", icon: UserRound });
  if (user?.role === "operator")
    links.push({ href: "/admin", label: "Админка", icon: ShieldCheck });

  const isActive = (href: string) =>
    href === "/" ? pathname === "/" : pathname.startsWith(href);

  async function signOut() {
    await logout();
    router.push("/");
    router.refresh();
  }

  return (
    <div className="site">
      <header className="topbar">
        <Link className="brand" href="/">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="brand-mascot" src="/maia-mascot.jpg" alt="MAIA — бобр-помощник МИСИС" />
          <span className="brand-word">
            MAIA
            <small>помощник МИСИС</small>
          </span>
        </Link>
        <nav>
          {links.map((l) => {
            const Icon = l.icon;
            return (
              <Link key={l.href} href={l.href} className={isActive(l.href) ? "active" : ""}>
                <Icon aria-hidden="true" />
                {l.label}
              </Link>
            );
          })}
        </nav>
        {!loading &&
          (user ? (
            <div className="account-box">
              <span className="avatar" aria-hidden="true">
                {initials(user.full_name)}
              </span>
              <span className="account-who">
                <strong>{user.full_name}</strong>
                <small>{ROLE_LABEL[user.role] ?? user.role}</small>
              </span>
              <button className="account-out" onClick={signOut}>
                <LogOut aria-hidden="true" />
                Выйти
              </button>
            </div>
          ) : (
            <div className="account-box">
              <Link className="btn btn-primary btn-sm" href="/login">
                <LogIn aria-hidden="true" />
                Войти
              </Link>
            </div>
          ))}
      </header>

      {children}

      <nav className="mobile-nav">
        {links.map((l) => {
          const Icon = l.icon;
          return (
            <Link key={l.href} href={l.href} className={isActive(l.href) ? "active" : ""}>
              <i>
                <Icon size={16} />
              </i>
              {l.label}
            </Link>
          );
        })}
        {user ? (
          <button className="mobile-out" onClick={signOut}>
            <i>
              <LogOut size={16} />
            </i>
            Выйти
          </button>
        ) : (
          <Link href="/login" className={isActive("/login") ? "active" : ""}>
            <i>
              <LogIn size={16} />
            </i>
            Войти
          </Link>
        )}
      </nav>
    </div>
  );
}
