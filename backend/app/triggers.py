"""Детерминированные триггеры поверх классификатора (FR-011, FR-013).

Правила настраиваемые (список RULES): срабатывание по ключевым словам форсирует
маршрут (обычно escalate) и/или повышает приоритет. Понижение приоритета правилами
запрещено — только raise (FR-011). Порог уверенности классификатора (дефолт 0.6)
также переводит в эскалацию независимо от ответа модели (FR-013).

Отдельное пост-правило apply_outage (T027): подтверждённый инструментом сбой
сервиса после execute_route поднимает приоритет до critical (FR-011).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Порог уверенности: ниже — эскалация оператору (FR-013, настраивается)
CONFIDENCE_THRESHOLD = 0.6

# Порядок приоритетов для арбитража (FR-011: повышать можно, понижать нельзя)
PRIORITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


@dataclass(frozen=True)
class TriggerRule:
    """Одно правило: ключевые слова → форс-маршрут и/или повышение приоритета.

    keywords — подстроки/основы слов (любое вхождение: «преподавателем»,
    «неустойка»); exact — целые слова с границами \\w, чтобы короткая лексема
    «суд» не срабатывала на «судороги»/«судьба» (FR-013, подтверждённый баг).
    """

    name: str
    keywords: tuple[str, ...] = ()
    exact: tuple[str, ...] = ()
    force_route: str | None = None
    priority: str | None = None

    def __post_init__(self) -> None:
        patterns = tuple(re.compile(re.escape(kw)) for kw in self.keywords)
        patterns += tuple(
            re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)") for kw in self.exact
        )
        object.__setattr__(self, "patterns", patterns)

    def matches(self, lowered_text: str) -> bool:
        return any(p.search(lowered_text) for p in self.patterns)


@dataclass(frozen=True)
class TriggerDecision:
    """Итог арбитража: итоговый маршрут/приоритет и список сработавших правил."""

    route: str
    priority: str
    triggered: list[str] = field(default_factory=list)


# Настраиваемый список правил (FR-013: жалобы на персонал, юридические темы)
RULES: tuple[TriggerRule, ...] = (
    TriggerRule(
        name="жалоба на сотрудника",
        keywords=("жалоба", "сотрудник", "преподавател", "груб", "хам", "неадекват", "хамил"),
        force_route="escalate",
    ),
    TriggerRule(
        name="юридические темы",
        keywords=("юрист", "адвокат", "договор", "неустойк", "претензи"),
        exact=("суд", "иск"),  # целые слова: «судороги»/«судьба»/«поиск» — не эскалация
        force_route="escalate",
    ),
    TriggerRule(
        name="недоступность сервиса",
        keywords=("не работает", "не работают", "недоступен", "недоступна", "не открывается",
                  "не открывается", "не грузится", "сбой", "упал", "лежит"),
        priority="high",
    ),
    TriggerRule(
        name="массовость",
        keywords=("у всех", "всех пользователей", "все жалуются", "массов"),
        priority="high",
    ),
)


def raise_priority(current: str, candidate: str) -> str:
    """Возвращает более высокий из двух приоритетов (FR-011 — понижение запрещено)."""
    if PRIORITY_ORDER.get(candidate, 0) > PRIORITY_ORDER.get(current, 0):
        return candidate
    return current


# Имя детерминированного правила «подтверждённый сбой → critical» (FR-011, US2)
OUTAGE_PRIORITY_RULE = "подтверждённый сбой"


def enforce_outage(priority: str) -> str:
    """Подтверждённый сбой сервиса (проверка вернула ok=False) → critical (FR-011).

    Понижение по-прежнему запрещено: поднимаем до critical через raise_priority,
    текущий critical остаётся critical.
    """
    return raise_priority(priority, "critical")


def apply(text: str, *, route: str, priority: str, confidence: float) -> TriggerDecision:
    """Применить правила к результату классификатора.

    1) сработавшее правило с force_route переводит в эскалацию;
    2) priority правил поднимает приоритет, но никогда не понижает;
    3) confidence ниже порога — эскалация независимо от модели (FR-013).
    """
    triggered: list[str] = []
    new_route = route
    new_priority = priority
    lowered = text.lower()
    for rule in RULES:
        if rule.matches(lowered):
            triggered.append(rule.name)
            if rule.force_route is not None:
                new_route = rule.force_route
            if rule.priority is not None:
                new_priority = raise_priority(new_priority, rule.priority)
    if confidence < CONFIDENCE_THRESHOLD and new_route != "escalate":
        triggered.append(f"confidence {confidence:.2f} < {CONFIDENCE_THRESHOLD}")
        new_route = "escalate"
    return TriggerDecision(route=new_route, priority=new_priority, triggered=triggered)


def apply_outage(
    route: str,
    priority: str,
    *,
    service_down: bool,
    service: str | None = None,
) -> TriggerDecision:
    """Пост-правило FR-011 (T027): подтверждённый проверкой сбой сервиса → critical.

    Применяется нодой execute_route после инструмента проверки, когда результат
    зафиксирован в service_checks. Понижение приоритета по-прежнему запрещено —
    только raise поверх предложенного моделью и поднятого текстовыми правилами.
    """
    if not service_down:
        return TriggerDecision(route=route, priority=priority, triggered=[])
    rule = "подтверждённый сбой сервиса"
    if service:
        rule = f"{rule} ({service})"
    return TriggerDecision(
        route=route,
        priority=raise_priority(priority, "critical"),
        triggered=[rule],
    )
