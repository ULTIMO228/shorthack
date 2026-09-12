# Phase 8 / US6 — Детектор массовых сбоев и массовый ответ: план реализации (T039–T041)

Фича: 001-misis-support-assistant. Зависимости: фазы 1–3 готовы (US1: пайплайн
`agent.py`, `tickets.py`, `triggers.py`), фаза 4 довела `tools.py` до целевого API
(`call_tool`/`ToolCall` — её тесты зелёные), `routers/admin.py` создан (сейчас
содержит `PATCH /api/admin/services/{id}`), `routers/internal.py` содержит рабочие
`GET /api/internal/outbound` + `POST /api/internal/outbound/{id}/ack` из фазы 2.
По `tasks.md` US6 идёт **параллельно** с US3–US5 → план НЕ опирается на код фаз
5–7 (`summarize`, нода `escalate`-пакета, kb/certs): `incidents.py` не импортирует
`agent.py`/`tools.py`, уведомление дежурному реализовано своим helper'ом по той же
конвенции, что в плане фазы 7 (оператор → его `TgLink(idle)` → сентинел `-(user.id)`).

Создаём: `backend/app/incidents.py`, `backend/tests/test_incidents.py`.
Меняем: `backend/app/agent.py` (новая нода + перекладка рёбер графа),
`backend/app/tools.py` (+инструмент `simulate_wave`), `backend/app/routers/admin.py`
(+3 эндпоинта). `models.py`, `schemas.py`, `db.py`, `auth.py`, `routers/internal.py`
— **без изменений** (DTO `IncidentOut`/`IncidentBroadcastRequest`/
`IncidentBroadcastResponse`/`IncidentResolveResponse` заготовлены в T006, модель
`Incident` — в T005, outbound-хуки реализованы в T012).

## 1. Цель и критерий готовности

**Что должно работать:**

1. **Детектор (FR-050, SC-005).** После классификации каждой подзадачи
   (service+category известны) система ищет всплеск: ≥3 однотипных обращений
   (совпадение `subtasks.service` + `subtasks.category`) от **разных авторов**
   за скользящее окно 15 минут (настраиваемо: константы модуля `incidents.py`).
   «Разный автор» = `COUNT(DISTINCT COALESCE(requests.user_id, requests.session_id))`
   — гостевые сессии считаются (user_id null не теряется).
2. **Создание инцидента + уведомление ≤ 1 мин (FR-051, SC-005).** Порог достигнут
   → строка `incidents` (status=`active`, `window_start`=MIN(created_at) всплеска,
   `request_count`=число обращений в окне), `notified_at` проставляется сразу
   (запись в `outbound_messages` пишется синхронно в ходе обработки обращения —
   требование «≤ 1 мин» выполняется тривиально), текст по образцу contracts/api.md:
   `🔴 Инцидент: Wi-Fi MISIS-EDU. 3 обращения за 15 мин.` (одна строка, статус
   `pending`, забирается ботом поллингом — фича 003).
3. **Идемпотентность уведомления.** Активный инцидент по паре (service, category)
   может быть только один: перед созданием ищется `status='active'`; если найден —
   ветка привязки, новое уведомление НЕ пишется. Повторный прогон пайплайна по
   тому же тикету (reply) не плодит инциденты и не инкрементит счётчик дважды
   (guard по `ticket.incident_id`).
4. **Привязка новых обращений (FR-052).** Обращение, попавшее под активный инцидент
   (та же пара service+category, маршрут не важен), получает шаблонное извещение
   **строго без LLM** (шаблон «Шаблон: массовый ответ при инциденте» из БЗ,
   запасная копия в коде для тестов без сида — как `agent.FALLBACK_TEMPLATES`),
   реакция `{"kind": "outage_notice", ...}`, тикет: `incident_id` проставлен,
   подзадача → «решена», тикет → «решена» (цепочкой «новая»→«в работе»→«решена»,
   два вызова `tickets.set_status` — одиночный «новая»→«решена» бросит ValueError).
   Автопроверка сервиса при этом **не вызывается** (экономия лимита FR-021 и
   мгновенный ответ).
5. **Приоритет-арбитраж (FR-011).** Привязка/создание инцидента поднимает приоритет
   подзадачи до минимум `high` через `triggers.raise_priority` — понижение
   невозможно, `critical` остаётся `critical`; в `route_reason` дописывается маркер
   «(триггеры: активный инцидент №N)» (FR-012).
6. **Админ-эндпоинты (FR-053, contracts/api.md)** — только `role=operator`
   (`auth.get_operator`: гость 401, студент/сотрудник 403):
   - `GET /api/admin/incidents` → 200 `list[IncidentOut]`, **active первыми**,
     далее по убыванию id;
   - `POST /api/admin/incidents/{id}/broadcast` body `{"text": null|"..."}` →
     200 `{sent, broadcast_at}`; `text=null` → шаблон из БЗ; рассылка = outbound
     `pending` всем **разным авторам** привязанных обращений (chat_id = привязанный
     `TgLink(idle)` или сентинел `-(user_id)`); гости без Telegram не получают
     строку (у них веб) — `sent` = число созданных строк; 404 нет инцидента,
     **409 если инцидент resolved**;
   - `POST /api/admin/incidents/{id}/resolve` → 200 `{status: "resolved"}`; 404;
     409 если уже resolved.
7. **Инструмент `simulate_wave` (тест-панель).** Запись в реестре `TOOLS`
   (type `exec`): `simulate_wave(db, *, service, count=3, subtask_id=None)`
   создаёт `count` однотипных обращений от `count` разных синтетических
   пользователей (`wave-user-{i}@edu.misis.ru`, role `student`, своя сессия на
   каждого) с уже «классифицированными» подзадачами (`route=auto_check`,
   `category="availability"`, `priority="high"`, `confidence=0.9`) и прогоняет
   каждую через детектор → результат `{"ok": True, "incident_id": N|null,
   "created_requests": [id,...]}` (контракт ответа `POST /api/admin/tools/{name}/
   invoke` из contracts/api.md). HTTP-обёртка invoke — фаза 10 (T045), здесь
   инструмент регистрируется и тестируется напрямую через `tools.call_tool`.
8. **Внутренние хуки.** `GET /api/internal/outbound?status=pending` /
   `POST /api/internal/outbound/{id}/ack` реализованы в T012 и меня не требуют —
   T041 покрывает их тестами (уведомление инцидента видно в pending, ack
   переводит в `sent`, без `X-Bot-Token` → 401).

**Критерий готовности (зелёное):**

- Новый `backend/tests/test_incidents.py` полностью зелёный (кейсы §4).
- Регрессия US1/US2: `test_agent.py`, `test_triggers.py`, `test_tools.py`,
  `test_quickstart_us2.py`, `test_core.py` зелёные — граф получает ноду
  `incident_check`, но при отсутствии всплесков поведение не меняется
  (проверяется существующими тестами).
- Ручная проверка quickstart **сценария 7** (через pytest-контекст + curl на
  админ-эндпоинты; HTTP-invoke `simulate_wave` — после фазы 10):
  волна ×3 → инцидент active + запись в `outbound_messages` (pending);
  4-я жалоба через `POST /api/requests` → реакция `outage_notice` без нового
  outbound; `broadcast` → `sent ≥ 3`; `resolve` → статус `resolved`.

## 2. Точки интеграции

| Файл:функция | Что меняется |
|---|---|
| `backend/app/incidents.py` (новый) | Детектор, создание/привязка инцидента, уведомление дежурному, broadcast, resolve, рендер шаблона. Список функций и сигнатуры — в §3 (T040) |
| `backend/app/agent.py` | + импорт `from app import incidents`; + `node_incident_check(state)`; перекладка рёбер: было `ensure_ticket →(conditional)→ execute_route|escalate`, станет `ensure_ticket → incident_check →(conditional)→ execute_route|escalate|next_subtask`; обновить шапку модуля (схема графа). `node_execute_route`, `node_escalate`, `AgentState` — **без изменений** |
| `backend/app/tools.py` | + функция `simulate_wave` и запись `"simulate_wave"` в `TOOLS` (только добавление; существующие `call_tool`/`ToolCall`/`CHECK_TOOL_BY_SERVICE` не трогаем). Импорт `from app import incidents` — цикла нет (incidents не импортирует tools/agent) |
| `backend/app/routers/admin.py` | + три эндпоинта (§3, T041) тем же стилем, что существующий `patch_service_state` (`Depends(get_operator)` на каждом эндпоинте) |
| `backend/app/routers/internal.py` | **Не меняется** — `outbound`/`outbound_ack` реализованы (T012); T041 даёт только тестовое покрытие |
| `backend/app/models.py` | **Не меняется** — `Incident`, `OutboundMessage`, `TgLink`, `Request`, `Ticket` из T005 |
| `backend/app/schemas.py` | **Не меняется** — `IncidentOut`, `IncidentBroadcastRequest`, `IncidentBroadcastResponse`, `IncidentResolveResponse` из T006 |
| `backend/app/triggers.py`, `tickets.py`, `events.py` | **Не меняется** — используем как есть: `raise_priority`, статусные константы/`set_status`/`ensure_ticket`/`attach_subtask`, `log_event` |
| `backend/app/seed.py` | **Не меняется** — шаблон «Шаблон: массовый ответ при инциденте» уже в сиде (FR-030) |
| `backend/app/main.py` | **Не меняется** — `admin_router` уже подключён |

**Оговорка про совместную работу над `agent.py`/`tools.py`.** Фазы 5–7 и 10 тоже
добавляют ноды в `agent.py` и инструменты в `tools.py`. Правило фазы: только
аддитивные диффы (новая нода + одна перекладка conditional-ребра; новая запись
реестра), никакого переименования существующего. Перед стартом — `git diff` на
предмет свежих правок этих файлов; если фаза 7 успела добавить helper уведомления
дежурному — конвенцию chat_id (TgLink → сентинел) использовать ту же, helper
всё равно остаётся локальным в `incidents.py` (несколько строк, чтобы не тянуть
зависимость от фазы 7).

## 3. Пошаговый план по задачам

Порядок: **T040** (ядро) → **T041** (эндпоинты) → **T039** (тесты, §4).
После каждого шага — точечный прогон `pytest`.

### T040 — `backend/app/incidents.py` (новый) + нода в `agent.py` + инструмент в `tools.py`

#### Модуль `incidents.py`

Константы (настраиваемые дефолты FR-050, spec «Assumptions»):

```python
DETECTION_WINDOW_MIN = 15   # скользящее окно детекции, минуты
DETECTION_THRESHOLD = 3     # ≥3 однотипных от разных авторов
STATUS_ACTIVE = "active"
STATUS_RESOLVED = "resolved"
TEMPLATE_MASS_ANSWER = "Шаблон: массовый ответ при инциденте"
FALLBACK_MASS_ANSWER = (
    "Уведомляем: по {service} зафиксирован массовый сбой, затронувший несколько "
    "пользователей. Инцидент зарегистрирован (№{incident}), дежурные уведомлены, "
    "ведутся работы. Спасибо за сообщения — они помогли быстро обнаружить проблему."
)
SERVICE_DISPLAY = {  # локальная копия agent.SERVICE_DISPLAY — нельзя импортировать agent (цикл)
    "site": "сайт misis.ru", "lms": "newlms.misis.ru (LMS)",
    "wifi_guest": "Wi-Fi MISIS-Guest", "wifi_edu": "Wi-Fi MISIS-EDU",
    "wifi_corp": "Wi-Fi MISIS-CORP", "account": "корпоративная учётная запись",
    "certs": "справки",
}
```

Функции (имена и сигнатуры финальные):

```python
def service_display(service: str | None) -> str
    # человекочитаемое имя; неизвестное — как есть или «сервис».

def find_active_incident(db: OrmSession, *, service: str | None,
                         category: str | None) -> Incident | None
    # SELECT * FROM incidents WHERE service=:s AND category=:c AND status='active'.
    # Один активный на пару — основа идемпотентности уведомления.

def count_wave(db: OrmSession, *, service: str, category: str,
               since: datetime) -> tuple[int, int, datetime | None]
    # Один агрегирующий запрос (критический SQL детектора):
    #   SELECT COUNT(DISTINCT COALESCE(requests.user_id, requests.session_id)),
    #          COUNT(requests.id), MIN(requests.created_at)
    #   FROM subtasks JOIN requests ON subtasks.request_id = requests.id
    #   WHERE subtasks.service = :service AND subtasks.category = :category
    #     AND requests.created_at >= :since
    # Возвращает (distinct_authors, matching_requests, window_start).
    # SQLAlchemy: func.count(func.distinct(func.coalesce(Request.user_id,
    # Request.session_id))), func.count(Request.id), func.min(Request.created_at).

def notify_duty(db: OrmSession, *, incident: Incident, authors: int) -> OutboundMessage
    # Дежурный: первый User с role='operator' (ORDER BY id).
    # chat_id: его TgLink в state='idle' → link.chat_id; иначе сентинел -(operator.id);
    # оператора нет вовсе → сентинел -1 (тесты без сида пользователей).
    # text: f"🔴 Инцидент: {service_display(incident.service)}. {authors} обращения
    #        за {DETECTION_WINDOW_MIN} мин."  (одна строка, статус pending).
    # Событие: log_event(actor="system", action="incident_notify",
    #                    payload={incident_id, chat_id, outbound_id}).

def _render_mass_answer(db: OrmSession, *, service: str, incident_id: int) -> str
    # Тело шаблона «Шаблон: массовый ответ при инциденте» из kb_articles
    # (kind='template') с подстановкой {{service}}/{{incident}}; статьи нет
    # (тесты без сида) — FALLBACK_MASS_ANSWER.format(...).

def create_incident(db: OrmSession, *, service: str, category: str, authors: int,
                    request_count: int, window_start: datetime) -> Incident
    # INSERT incidents(status='active', ...); notify_duty(...);
    # incident.notified_at = сейчас (UTC); commit;
    # log_event(actor="system", action="incident_created",
    #           payload={incident_id, service, category, authors,
    #                    window_start, request_count}).

def attach_to_incident(db: OrmSession, *, incident: Incident, subtask: Subtask,
                       ticket: Ticket) -> dict[str, str]
    # Если ticket.incident_id != incident.id: привязать (ticket.incident_id) и
    #   incident.request_count += 1; иначе только реакция (идемпотентность).
    # FR-011: subtask.priority = triggers.raise_priority(priority or "medium", "high");
    #   при изменении — в route_reason маркер «(триггеры: активный инцидент №N)».
    # subtask.status = tickets.STATUS_RESOLVED; commit.
    # Событие (только при реальной привязке): actor="system",
    #   action="incident_attached", payload={incident_id, request_count}.
    # Вернуть реакцию {"kind": "outage_notice",
    #   "text": _render_mass_answer(..., service=service_display(subtask.service),
    #                               incident_id=incident.id)}.

def evaluate(db: OrmSession, *, subtask: Subtask, ticket: Ticket,
             window_minutes: int = DETECTION_WINDOW_MIN,
             threshold: int = DETECTION_THRESHOLD) -> dict[str, str] | None
    # Единая точка входа детектора (нода графа и simulate_wave вызывают её):
    # 1) service/category пусты (не классифицировано) → None;
    # 2) find_active_incident → attach_to_incident (ветка FR-052, без уведомления);
    # 3) since = now(UTC) - timedelta(minutes=window_minutes);
    #    count_wave(...) → authors < threshold → None;
    # 4) create_incident(authors=authors, request_count=matching_requests,
    #    window_start=window_start or since) → attach_to_incident.
    # LLM не вызывается ни на одном шаге (FR-052 — шаблонное извещение).

def broadcast(db: OrmSession, *, incident: Incident, text: str | None,
              actor: str = "operator") -> tuple[int, datetime]
    # Только status='active' (проверка — в роутере, 409).
    # custom = text is not None; text = text or _render_mass_answer(...).
    # Получатели: SELECT requests JOIN tickets ON tickets.request_id = requests.id
    #   WHERE tickets.incident_id = :id; дедуп по ключу автора
    #   COALESCE(user_id, session_id) (в Python, dict.setdefault).
    # На каждого автора с user_id: chat_id = TgLink(user_id, state='idle').chat_id
    #   или сентинел -(user_id); INSERT outbound_messages(status='pending').
    # Гость (user_id NULL) — пропуск (нет Telegram-идентичности; sent честно
    #   отражает число созданных строк).
    # incident.broadcast_at = сейчас; commit; log_event(actor="operator",
    #   action="incident_broadcast", payload={incident_id, sent, custom_text}).
    # Вернуть (sent, broadcast_at).

def resolve(db: OrmSession, *, incident: Incident, actor: str = "operator") -> Incident
    # status != 'active' → ValueError("Инцидент уже закрыт") (роутер → 409).
    # status='resolved'; commit; log_event(actor="system",
    #   action="incident_resolved", payload={incident_id, actor}).
```

Импорты модуля: `datetime/timedelta/timezone`, `sqlalchemy.select/func`,
`sqlalchemy.orm.Session`, `from app import tickets, triggers`,
`from app.events import log_event`,
`from app.models import Incident, OutboundMessage, Request, Subtask, TgLink,
Ticket, User`. **Запрещено** импортировать `app.agent` и `app.tools` (циклы:
agent и tools импортируют incidents).

#### Дифф `backend/app/agent.py`

1. Шапка модуля: добавить ноду `incident_check` в схему графа.
2. Новая нода (после `node_ensure_ticket`):

```python
def node_incident_check(state: AgentState) -> dict[str, Any]:
    """Нода детектора инцидентов (US6, T040): evaluate(subtask, ticket).

    Ветка FR-052/FR-050: привязка/создание инцидента → шаблонная реакция
    outage_notice без LLM, тикет «решена», маршрутная обработка подзадачи
    пропускается (инструмент не тратит лимит FR-021). Иначе — обычный
    dispatch по route (conditional-ребро ниже).
    """
    db = state["db"]
    subtask = db.get(Subtask, state["subtask_ids"][state["subtask_index"]])
    ticket = db.get(Ticket, state["ticket_id"])
    reaction = incidents.evaluate(db, subtask=subtask, ticket=ticket)
    if reaction is None:
        return {"incident_bound": False}
    tickets.set_status(db, ticket, tickets.STATUS_IN_PROGRESS)
    tickets.set_status(db, ticket, tickets.STATUS_RESOLVED)
    log_event(db, ticket_id=ticket.id, actor="agent",
              action="reaction_sent", payload=reaction)
    return {
        "reactions": state.get("reactions", []) + [reaction],
        "subtask_index": state["subtask_index"] + 1,
        "incident_bound": True,
    }
```

3. Сборка графа (`build_graph`): добавить `graph.add_node("incident_check",
   node_incident_check)`; **убрать** `add_conditional_edges("ensure_ticket", ...)`
   и вместо него:

```python
graph.add_edge("ensure_ticket", "incident_check")
graph.add_conditional_edges(
    "incident_check",
    lambda state: (
        node_next_subtask(state)
        if state.get("incident_bound")
        else ("execute_route" if state.get("route") == "auto_check" else "escalate")
    ),
)
```

`AgentState` не расширяем: `incident_bound` — опциональный флаг TypedDict
(total=False), как `duplicate`.

#### Дифф `backend/app/tools.py`

```python
def simulate_wave(db: OrmSession, *, service: str, count: int = 3,
                  subtask_id: int | None = None) -> dict[str, Any]:
    """Симуляция волны жалоб (FR-050): count однотипных обращений от разных
    тестовых пользователей → прогон каждого через incidents.evaluate.

    service — ключ классификатора (site/lms/wifi_guest/wifi_edu/wifi_corp),
    category волны фиксирована 'availability'. Синтетические авторы
    wave-user-{i+1}@edu.misis.ru (role='student', отдельная Session на каждого) —
    гарантия разных COALESCE(user_id, session_id) без зависимости от сида.
    Подзадача создаётся уже классифицированной (route='auto_check',
    priority='high', confidence=0.9) — LLM и HTTP не нужны.
    Возвращает {"ok": True, "incident_id": id|None, "created_requests": [...]}.
    """
```

Тело: `service not in CHECK_TOOL_BY_SERVICE` → `ToolError("Неизвестный сервис
волны: {service}")`; `count = max(1, min(int(count), 50))`; цикл по `i`:
get-or-create `User(email=f"wave-user-{i+1}@edu.misis.ru", full_name=...,
role="student")` + `Session(id=secrets.token_urlsafe(16), user_id=user.id)`;
`Request(session_id=..., user_id=user.id, channel="web", raw_text=masked_text=
f"[тестовая волна] Жалоба {i+1}: не работает {service}", lang="ru")`;
`tickets.attach_subtask(db, request, f"Недоступен {service} (симуляция {i+1})",
service=service, category="availability", route="auto_check", priority="high",
confidence=0.9, status=tickets.STATUS_IN_PROGRESS)`;
`ticket = tickets.ensure_ticket(db, request.id)`;
`incidents.evaluate(db, subtask=subtask, ticket=ticket)`;
на последнем тикете — `log_event(actor="system", action="simulate_wave",
payload={"service": service, "count": count, "created_requests": ids,
"incident_id": ...})`. Импорты: `secrets`, `from app import incidents, tickets`,
`from app.models import Request, Session as UserSession, User`.
Вызов через `tools.call_tool` даст журнал `tool_call`/`tool_result` бесплатно.

Запись реестра (аддитивно, в полном формате для T045):

```python
"simulate_wave": {
    "type": "exec",
    "fn": simulate_wave,
    "schema": [{"name": "service", "type": "string"},
               {"name": "count", "type": "integer", "default": 3}],
    "description": "Симуляция волны: count однотипных обращений от разных "
                   "тестовых пользователей → детектор инцидентов (FR-050)",
},
```

### T041 — эндпоинты в `backend/app/routers/admin.py` + внутренние хуки

Стиль — как существующий `patch_service_state`: `operator: Annotated[User,
Depends(get_operator)]`, `db: Annotated[OrmSession, Depends(get_session)]`,
`HTTPException` с русским `detail`. Импорты: `case` (sqlalchemy),
`Incident`, схемы `IncidentOut`/`IncidentBroadcastRequest`/
`IncidentBroadcastResponse`/`IncidentResolveResponse`, `from app import incidents`.

**`GET /api/admin/incidents` → 200 `list[IncidentOut]`** (contracts/api.md):

```python
rows = db.scalars(
    select(Incident).order_by(
        case((Incident.status == incidents.STATUS_ACTIVE, 0), else_=1),
        Incident.id.desc(),
    )
).all()
```

**`POST /api/admin/incidents/{incident_id}/broadcast`** body
`IncidentBroadcastRequest` → 200 `IncidentBroadcastResponse`:

- `db.get(Incident, incident_id)` → None ⇒ **404** «Инцидент не найдено»;
- `incident.status != "active"` ⇒ **409** «Массовый ответ возможен только по
  активному инциденту»;
- `sent, broadcast_at = incidents.broadcast(db, incident=incident,
  text=body.text, actor=operator.email)` → `IncidentBroadcastResponse(sent=sent,
  broadcast_at=broadcast_at)`.

**`POST /api/admin/incidents/{incident_id}/resolve`** → 200
`IncidentResolveResponse`:

- 404 как выше; `incidents.resolve(...)` ловим `ValueError` → **409**
  «Инцидент уже закрыт» (паттерн сертификатов/статусных цепочек);
- иначе `IncidentResolveResponse(status=incident.status)` (`"resolved"`).

**`/api/internal/outbound*`** — код не меняется; в рамках T041 пишутся тесты
poll/ack/401 (§4) и вручную проверяется, что уведомление инцидента видно в
`GET /api/internal/outbound?status=pending` с заголовком `X-Bot-Token`.

### T039 — `backend/tests/test_incidents.py`

Новый файл, полный список кейсов и моков — в §4. Пишется после T040/T041,
но по сигнатурам §3 (фикстуры повторяют паттерны `test_tools.py`:
`db_session`, autouse-`seed_services` из `seed.SERVICES`, `_make_operator`
создаёт оператора напрямую в БД — логин автосоздаёт только `student`).

## 4. Тесты — `backend/tests/test_incidents.py`

**Моки:** LLM — `make_fake_llm` (из `test_agent.py`, импорт между тестовыми
модулями — прецедент есть в `test_quickstart_us2.py`) или счётчик
`make_counting_fake_llm` (из `test_tools.py`); HTTP **не мокаем** — ветка
инцидента автопроверку не вызывает. Оператор — напрямую в БД
(`User(role="operator")`), логин через `POST /api/auth/login`. Отдельные авторы —
отдельные экземпляры `TestClient(app)` (гость) или разные логины. БД — фикстуры
`db_engine`/`client`/`bot_token` из `conftest.py`. Сидирования БЗ нет →
проверяем запасные шаблоны (fallback-тексты), как это делает `agent`-код.

**Хелперы файла:**

```python
def _make_operator(db_engine, email="smirnov@misis.ru")  # как в test_tools.py
def _db_session(db_engine)  # sessionmaker(expire_on_commit=False)
def _complaint(db, *, service="wifi_edu", category="availability",
               minutes_ago=0, user_id=None, session_id="s-1", summary=None)
    # Request + subtask (attach_subtask) + ensure_ticket; created_at
    # при необходимости откатывается назад (окно детекции).
def _login(client, email) -> None  # POST /api/auth/login
```

**Кейсы (happy path / edge / error):**

| # | Тест | Что проверяет |
|---|---|---|
| 1 | `test_detector_creates_incident_on_third_distinct_author` | 3 жалобы от 3 авторов в окне → `evaluate` на 3-й: инцидент `active`, `notified_at` проставлен, `request_count`=3, outbound `pending` одна строка, текст содержит «Инцидент» и «MISIS-EDU», тикет привязан, реакция `outage_notice`, тикет «решена» |
| 2 | `test_detector_ignores_same_author_repeated` | 3 жалобы ОДНОГО автора → `None`, инцидентов 0, outbound 0 (FR-050 «разных пользователей») |
| 3 | `test_detector_respects_sliding_window` | 3 автора, но у 2-х `created_at` за пределами 15 мин → `None`; вернуть в окно → инцидент |
| 4 | `test_detector_threshold_and_window_configurable` | `evaluate(..., threshold=2)` → инцидент от 2 авторов; `window_minutes=60` захватывает старую жалобу |
| 5 | `test_detector_skips_unclassified_subtask` | `service=None` → `None`, запросов к incidents нет |
| 6 | `test_active_incident_attaches_without_second_notification` | Активный инцидент + новая жалоба → привязка, `request_count` 3→4, outbound всё ещё 1 (FR-052 — без повторного уведомления), реакция-шаблон содержит имя сервиса и № инцидента |
| 7 | `test_active_incident_raises_priority_floor_high` | Привязанная подзадача `medium` → `high`; маркер «активный инцидент» в `route_reason` (FR-011/FR-012) |
| 8 | `test_active_incident_never_lowers_priority` | `critical` остаётся `critical` (арбитраж: понижение запрещено) |
| 9 | `test_incident_branch_never_calls_llm` | Полный `POST /api/requests` при активном инциденте: ровно 2 вызова LLM (сплиттер+классификатор), событий `tool_call` нет (FR-052 без LLM и без траты лимита FR-021) |
| 10 | `test_pipeline_creates_incident_on_third_wave_user` | 2 жалобы засеяны напрямую, 3-я через `POST /api/requests` от другого гостя → в ответе реакция `outage_notice`, в БД инцидент + outbound; 4-я жалоба → шаблон, outbound не добавился (quickstart сценарий 7, шаги 1–2) |
| 11 | `test_repeated_evaluate_same_ticket_no_double_count` | Двойной `evaluate` для тикета → `request_count` инкрементился один раз (идемпотентность привязки) |
| 12 | `test_resolve_then_new_wave_creates_new_incident` | `resolve` → новая волна 3 авторов → НОВЫЙ инцидент с другим id (старый остался `resolved`) |
| 13 | `test_simulate_wave_creates_incident_and_outbound` | `tools.call_tool(db, "simulate_wave", {"service": "wifi_edu", "count": 3})` → `result["ok"]`, `incident_id` не None, `created_requests` длины 3, 3 distinct-автора, outbound pending есть; в журнале `tool_call`/`tool_result` (actor `tool:simulate_wave`) и `simulate_wave` |
| 14 | `test_simulate_wave_count_two_no_incident` | `count=2` → `incident_id is None`; дефолт count=3 |
| 15 | `test_simulate_wave_unknown_service_raises` | `service="unknown"` → `tools.ToolError` |
| 16 | `test_admin_incidents_list_active_first` | Оператор: активный + resolved → active первым; гость → **401**; студент → **403** |
| 17 | `test_admin_broadcast_template_and_custom_text` | `text=None` → `sent`=числу авторов (3), `broadcast_at` проставлен, outbound-строки содержат текст шаблона с именем сервиса; повтор с custom текстом → новые строки с этим текстом (quickstart сценарий 7, шаг 3) |
| 18 | `test_admin_broadcast_resolved_incident_409` | broadcast по resolved → **409** |
| 19 | `test_admin_resolve_happy_and_409` | resolve → 200 `{"status": "resolved"}` + событие `incident_resolved`; повторный resolve → **409** |
| 20 | `test_admin_incident_endpoints_404` | broadcast/resolve несуществующего id → **404** |
| 21 | `test_internal_outbound_poll_and_ack` | С `bot_token`: `GET /api/internal/outbound?status=pending` содержит уведомление инцидента; `POST .../{id}/ack {"status":"sent"}` → 200, запись ушла из pending; без заголовка → **401** (T041, покрытие готовых хуков) |

**Регрессионные прогоны обязательны** (нода в общем графе): `test_agent.py`,
`test_triggers.py`, `test_tools.py`, `test_quickstart_us2.py` — зелёные без правок.

## 5. Риски и подводные камни

1. **Статусная цепочка FR-015.** «Новая»→«решена» напрямую запрещена
   (`tickets.TRANSITIONS`) — в ноде и в тестах только пара
   `set_status(в работе)` + `set_status(решена)`, как в `node_execute_route`.
2. **409 на закрытых инцидентах.** `resolve`/`broadcast` по `resolved` → 409
   (единая конвенция проекта: ValueError из доменной функции → 409 в роутере).
   `incidents.resolve` бросает `ValueError`, роутер ловит.
3. **Приоритет-арбитраж (FR-011).** Только `triggers.raise_priority` — понижение
   невозможно; `critical` сохраняется. Маркер в `route_reason` добавлять только
   при реальном изменении приоритета, чтобы не засорять обоснование (FR-012).
4. **Идемпотентность уведомления.** Один активный инцидент на пару
   (service, category): `find_active_incident` до `count_wave`; guard
   `ticket.incident_id` в `attach_to_incident`. Гонки двух одновременных
   «третьих» жалоб в демо-режиме (single-process uvicorn) не страшны; уникальный
   индекс не добавляем (create_all миграций не делает).
5. **Гостевые сессии.** `COALESCE(user_id, session_id)` смешивает int и str —
   SQLite считает их разными значениями (нам и надо). Broadcast гостю не шлётся
   (нет chat_id) — `sent` < `request_count` законен; в тестах волны используются
   синтетические пользователи, чтобы `sent`=3.
6. **LLMUnavailable / лимит tool_calls.** Ветка инцидента LLM не вызывает и
   инструмент не тратит — при эскалации-из-инцидента (если появится в поздних
   фазах) счётчик не перегружен. `simulate_wave` вне пайплайна — лимит FR-021
   его не касается.
7. **Дубли FR-014 vs инциденты.** Дубль уходит в `respond` до `ensure_ticket` и
   не доходит до `incident_check` → не инкрементит `request_count` (осознанно:
   автор уже получил реакцию «объединили с заявкой»).
8. **Дубликат шаблонного кода.** `SERVICE_DISPLAY` и fallback-текст массового
   ответа продублированы из `agent.py`/`seed.py` — цена отсутствия циклического
   импорта (agent→incidents). Не выносить в agent.py.
9. **Временные метки в SQLite.** Все даты пишутся naive-UTC строками
   (единый `_utcnow`) — сравнения `created_at >= since` консистентны; в новом
   коде использовать `datetime.now(timezone.utc)`, как везде в проекте.
10. **Параллельная работа фаз 5–7/10** над `agent.py`, `tools.py`,
    `routers/admin.py`: только аддитивные диффы; перед стартом и перед
    финальным коммитом — `git diff` по этим файлам и полный прогон тестов.
11. **HTTP-доступ к `simulate_wave`.** Эндпоинт `POST /api/admin/tools/{name}/
    invoke` — T045 (фаза 10). Сценарий 7 quickstart целиком по HTTP станет
    доступен после неё; в этой фазе приёмка — через `tools.call_tool` + админ- и
    internal-эндпоинты по HTTP.

## 6. Валидация

```bash
cd backend

# Фазовые тесты (новый файл)
.venv/bin/python -m pytest tests/test_incidents.py -v

# Фокусная регрессия US1/US2 (нода в общем графе, tools-реестр)
.venv/bin/python -m pytest tests/test_agent.py tests/test_triggers.py \
    tests/test_tools.py tests/test_quickstart_us2.py -q

# Полный прогон
.venv/bin/python -m pytest tests/ -q
```

Ручной прогон quickstart, сценарий 7 (после поднятия uvicorn; волна — через
pytest-контекст, т.к. HTTP-invoke появится в фазе 10):

```bash
.venv/bin/uvicorn app.main:app --reload   # :8000
# волна: pytest-фикстура/скрипт с tools.call_tool("simulate_wave", {...})
curl -c jar -b jar -X POST http://localhost:8000/api/auth/login \
     -H 'Content-Type: application/json' -d '{"email":"smirnov@misis.ru"}'
curl -c jar -b jar http://localhost:8000/api/admin/incidents
# → active первым, notified_at не null
curl -c jar -b jar -X POST http://localhost:8000/api/admin/incidents/1/broadcast \
     -H 'Content-Type: application/json' -d '{"text": null}'   # → {"sent": 3, ...}
curl -c jar -b jar -X POST http://localhost:8000/api/admin/incidents/1/resolve
curl -H "X-Bot-Token: $BOT_INTERNAL_TOKEN" \
     "http://localhost:8000/api/internal/outbound?status=pending"
```
