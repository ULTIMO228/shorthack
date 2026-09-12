import { ArrowDownCircle, ArrowUpCircle, Bot, BookOpen, FileText, Flame, Minus, User } from "lucide-react";
import type { Priority, RouteKind } from "@/lib/types";
import { PRIORITY_LABEL, ROUTE_LABEL } from "@/lib/types";

const PRIORITY_ICON: Record<Priority, typeof Flame> = {
  critical: Flame,
  high: ArrowUpCircle,
  medium: Minus,
  low: ArrowDownCircle,
};

export function PriorityBadge({ priority }: { priority: Priority }) {
  const Icon = PRIORITY_ICON[priority] ?? Minus;
  return (
    <span className={`badge p-${priority}`}>
      <Icon aria-hidden="true" />
      {PRIORITY_LABEL[priority] ?? priority}
    </span>
  );
}

const ROUTE_ICON: Record<RouteKind, typeof Bot> = {
  auto_check: Bot,
  kb: BookOpen,
  cert_order: FileText,
  escalate: User,
};

export function RouteBadge({ route }: { route: RouteKind }) {
  const Icon = ROUTE_ICON[route] ?? Bot;
  return (
    <span className={`badge r-${route}`}>
      <Icon aria-hidden="true" />
      {ROUTE_LABEL[route] ?? route}
    </span>
  );
}
