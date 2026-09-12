"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, FileText, ScrollText, Wrench } from "lucide-react";

const TABS = [
  { href: "/admin", label: "Очередь", icon: ScrollText, exact: true },
  { href: "/admin/certs", label: "Справки", icon: FileText, exact: false },
  { href: "/admin/tools", label: "Инструменты", icon: Wrench, exact: false },
  { href: "/admin/metrics", label: "Метрики", icon: BarChart3, exact: false },
];

export default function AdminTabs() {
  const pathname = usePathname();
  return (
    <nav className="admin-tabs" aria-label="Разделы админки">
      {TABS.map((t) => {
        const active = t.exact ? pathname === t.href : pathname.startsWith(t.href);
        const Icon = t.icon;
        return (
          <Link key={t.href} href={t.href} className={active ? "active" : ""}>
            <Icon aria-hidden="true" />
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
