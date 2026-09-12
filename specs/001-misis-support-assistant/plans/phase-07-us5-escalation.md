# Phase 7 / US5 — Эскалация оператору с саммари: план реализации (T036–T038)

Фича: 001-misis-support-assistant. Зависимости: фазы 1–3 готовы, фаза 4 доводит `tools.py`
(реестр + диспетчер). Меняем: `backend/app/tools.py`, `backend/app/agent.py`,
`backend/app/main.py`, `backend/app/routers/requests.py` (мелкий рефакторинг),
создаём `backend/app/routers/admin.py`, `backend/tests/test_escalations.py`.

## 1. Цель и критерий готовности

**Что должно работать:**

1. Любой путь в эскалацию (FR-013) — `confidence < 0.6`, форс-триггер из
   `triggers.RULES`, `LLMUnavailable` в classify, непроверяемый сервис в
   `execute_route`, превышение лимита `tool_calls` (FR-021) — проходит через ноду
   `escalate` и формирует **пакет FR-060**: `summary`, `checks` (сводка
   автопроверок из `service_checks`), `recommendation`, `why_escalated`, плюс
   исходник + перевод (`request.raw_text`/`translation`) и журнал (`events`).
2. Пакет сохраняется (событие `escalation_package` в журнале FR-061) и читается
   оператором: `GET /api/admin/escalations/{request_id}` возвращает `EscalationCard`
   со всеми блоками (quickstart, сценарий 4b — «вопрос вне базы → реакция
   `escalated` → карточка содержит все блоки FR-060»).
3. При эскалации дежурный оператор получает запись в `outbound_messages`
   (статус `pending`, забирается ботом поллингом — фича 003) — один раз на заявку.
4. Очередь `GET /api/admin/queue`: все обращения с заявками, сортировка
   `priority` (critical→low) → `created_at`, автор-гость отображается с
   `user: null` (FR-016).
5. `POST /api/admin/escalations/{request_id}/close` с `resolution` переводит
   тикет в «закрыта» (через «решена», FR-015), пишет событие от `operator`;
   `add_to_kb` принимается, но игнорируется (`kb_draft: null`) — реализуется
   в фазе 9 (T043). Доступ ко всем трём эндпоинтам — только `role=operator`
   (`auth.get_operator`: гость 401, студент/сотрудник 403).
6. При `LLMUnavailable` на шаге саммари пакет собирается из шаблонов/полей БД
   (summary = `subtask.summary`, recommendation — константа, why_escalated =
   `subtask.route_reason`, где уже есть маркеры «триггеры: …», «confidence … <
   0.6», «Модель недоступна») — эскалация не ломается и не блокируется сбоем LLM.

**Критерий готовности (зелёное):**

- Новый `backend/tests/test_escalations.py` полностью зелёный (кейсы из §4).
- Регрессия: `tests/test_agent.py`, `tests/test_triggers.py`, `tests/test_core.py`
  зелёные (поведение US1 не меняется: реакция `escalated`, шаблон
  TEMPLATE_ESCALATION, `auth_required` для гостевого cert_order без outbound).
- Ручная проверка сценария 4b: `POST /api/requests` с вопросом вне базы →
  `reactions[0].kind == "escalated"` → логин оператором →
  `GET /api/admin/escalations/{id}` содержит `summary/checks/recommendation/
  why_escalated` + `request.raw_text`, `request.translation` (для en), `events`.

## 2. Точки интеграции

| Файл:функция | Что меняется |
|---|---|
| `backend/app/tools.py` (`TOOLS`, рядом с `check_wifi`) | + инструмент `summarize` (type `llm`) и Pydantic-схема его ответа. Реестр общий с фазой 4 — только добавление записи, сигнатуры `fn(db, **params)` не меняются |
| `backend/app/agent.py:node_escalate` | Основной дифф: формирование пакета FR-060, вызов `summarize`, fallback при `LLMUnavailable`, событие `escalation_package`, `notify_duty_operator`, защита от повторного уведомления, защита статусной цепочки при эскалации из «решена» |
| `backend/app/agent.py` (модульный уровень) | + константа fallback-рекомендации, + helper `notify_duty_operator(db, ticket, package)`, + helper `build_escalation_package(...)` (чтобы нода оставалась читаемой). Импорт `OutboundMessage`, `User` из `app.models`, `ServiceCheck` |
| `backend/app/main.py` | + `app.include_router(admin_router.router)` (после requests_router) |
| `backend/app/routers/requests.py:request_detail` | Тело деталей обращения выносится в helper `_build_request_detail(db, request) -> RequestDetail`; сам эндпоинт его вызывает. Нужно, чтобы `GET /api/admin/escalations/{id}` встроил `request: RequestDetail` без дублирования ~50 строк |
| `backend/app/schemas.py` | **Не меняется** — DTO уже есть: `AdminQueueItem`, `QueueUser`, `EscalationCheck`, `EscalationCard`, `EscalationCloseRequest`, `EscalationCloseResponse`, `KbDraftOut` (T006 заготовил под все фазы) |
| `backend/app/models.py`, `backend/app/db.py` | **Не меняется** — отдельной таблицы эскалаций нет и не нужно: эскалация — признак тикета (Q2), пакет хранится в `events.payload` (action=`escalation_package`, FR-061) |
| `backend/app/auth.py` | **Не меняется** — `get_operator` готов (401 гостю, 403 не-оператору) |
| `backend/app/routers/internal.py` | **Не меняется** — `outbound_messages` пишется ядром напрямую, бот забирает поллингом |

**Важная оговорка про API диспетчера (наследие фазы 4).** `backend/tests/test_tools.py`
уже написан против целевого API фазы 4: `tools.call_tool(db, name, params,
ticket_id=..., subtask_id=..., tool_calls=...) -> ToolCall(result, tool_calls)`,
`tools.ToolError`, константа `tools.CHECK_TIMEOUT_SEC`; текущий `tools.py` ещё
содержит промежуточный `dispatch(db, ticket_id, name, params) -> dict` без
инкремента счётчика. **План опирается на целевой API из тестов** (`call_tool` /
`ToolCall` / обёртка ошибок в `ok=False` с ключом `error`), т.к. фаза 4 обязана
к нему сойтись (её тесты красные до этого). Если фаза 4 оставит имя `dispatch` —
адаптация тривиальна (переименование одного вызова в `node_escalate`); сигнатура
`summarize(db, *, request_id, subtask_id)` от этого не зависит.

**Реестр для фазы 10.** Запись `summarize` в `TOOLS` сразу оформлять полной
схемой (`schema`, `description`) — `GET /api/admin/tools` (T045) читает реестр
как есть; тип `llm` должен попасть в выдачу без доработок.

## 3. Пошаговый план по задачам

### T036 — инструмент `summarize` (type `llm`) в `backend/app/tools.py`

Добавить Pydantic-схему и функцию (имена и сигнатуры — финальные):

```python
class SummarizePayload(BaseModel):
    """JSON-ответ LLM для пакета эскалации (FR-060: суть, рекомендация, причина)."""

    summary: str = Field(min_length=1)        # саммари сути обращения
    recommendation: str = Field(min_length=1)  # рекомендуемый следующий шаг оператору
    why_escalated: str = Field(min_length=1)   # почему передано человеку
```

```python
def summarize(db: OrmSession, *, request_id: int, subtask_id: int) -> dict[str, Any]:
    """Саммари пакета эскалации (FR-060): суть + сводка проверок + рекомендация.

    LLM генерирует только summary/recommendation/why_escalated по промпту
    (masked_text, translation, subtask.summary, subtask.route_reason, текстовая
    сводка проверок). Список checks собирается детерминированно из service_checks
    (через подзадачи обращения) — модель его не выдумывает. note проверки:
    HTTP — «HTTP {code}, {latency} мс»; Wi-Fi — «сеть в состоянии «сбой»/«норма».
    При LLMUnavailable исключение пролетает в диспетчер и оборачивается в
    {"ok": False, "error": ...} — нода escalate соберёт пакет из шаблонов.
    Возвращает {"ok": True, "summary", "checks", "recommendation", "why_escalated"}.
    """
```

Запись реестра (рядом с `check_wifi`, без изменения существующих):

```python
"summarize": {
    "type": "llm",
    "fn": summarize,
    "schema": {"request_id": "integer", "subtask_id": "integer"},
    "description": "LLM-саммари пакета эскалации: суть, сводка проверок, рекомендация (FR-060)",
},
```

Промпт — на русском, требует строго один JSON `{summary, recommendation,
why_escalated}`; контекст: `subtask.summary`, `subtask.route_reason` (там уже
обоснование маршрута FR-012 и маркеры триггеров), `request.masked_text`,
`request.translation` (если не None — явно сказать модели, что это перевод),
текстовая сводка проверок. Модель — `llm.generate_model_uri()` (генеративная,
как у ноды normalize), температура дефолтная.

### T037 — нода `escalate` в `backend/app/agent.py`

Текущая ветка `cert_order` без `user_id` → `auth_required` **не трогаем**
(FR-016/FR-043, регрессия `test_no_duplicate_after_ticket_resolved`). Дифф —
только ветка «эскалация оператору»:

1. **Защита статусной цепочки (баг текущего кода, чиним в этой же ноде).**
   Сейчас вызывается `tickets.set_status(db, ticket, STATUS_IN_PROGRESS)`. Если
   тикет уже «решена» (ветка сбоя FR-026, а пользователь потом ответил и ушёл в
   эскалацию по FR-025), переход «решена»→«в работе» запрещён `TRANSITIONS` →
   `ValueError` роняет пайплайн. Условие: `if ticket.status in tickets.OPEN_STATUSES:
   tickets.set_status(...IN_PROGRESS)`, иначе статус не трогаем (признак
   `escalated` фиксируется независимо от статуса, Q2).
2. **Счётчик FR-021.** `can_call = state.get("tool_calls", 0) < MAX_TOOL_CALLS`.
   Если `can_call` — вызов через диспетчер фазы 4:
   `call = tools.call_tool(db, "summarize", {"request_id": ..., "subtask_id": ...},
   ticket_id=ticket.id, subtask_id=subtask.id, tool_calls=state["tool_calls"])`
   (при целевом `dispatch`: `result = tools.dispatch(db, ticket.id, "summarize",
   {...})`). Возврат ноды добавляет `"tool_calls": call.tool_calls` (или +1 при
   `dispatch`). Если `not can_call` или `call.result.get("ok") is False` —
   fallback-пакет без LLM (см. п. 4).
3. **Пакет FR-060 → журнал.** `log_event(db, ticket_id=ticket.id, actor="agent",
   action="escalation_package", payload=package)`, где package — dict с ключами
   `summary`, `checks` (список dict по `EscalationCheck`), `recommendation`,
   `why_escalated`. Это единственное хранилище пакета; читается карточкой
   (T038) как последнее событие с `action="escalation_package"`.
4. **Fallback при недоступности LLM / лимите вызовов** (собирается в helper
   `build_escalation_package`, чтобы не дублировать):
   - `summary` = `subtask.summary` (непустой по схеме сплиттера);
   - `checks` = те же строки `service_checks` (детерминированный сбор и в
     fallback, и в инструменте — общий helper `_collect_checks(db, request_id)`),
     при отсутствии проверок — пустой список (не ошибка: эскалация по confidence
     может быть без auto_check);
   - `recommendation` = модульная константа
     `ESCALATION_FALLBACK_RECOMMENDATION = "Передать дежурному оператору: запросить у пользователя детали и диагностику по журналу проверок."`;
   - `why_escalated` = `subtask.route_reason` (уже содержит «триггеры: …»,
     «confidence 0.42 < 0.6», «Модель недоступна … — безопасный маршрут (FR-013)»).
5. **Уведомление дежурному** — helper `notify_duty_operator(db, ticket, package)
   -> OutboundMessage`, вызывается **только при первичной эскалации**
   (`already = ticket.escalated` до установки; `if not already:`) — идемпотентность
   при повторных прогонах пайплайна по тому же тикету (reply → эскалация):
   - дежурный: первый пользователь с `User.role == "operator"`;
   - `chat_id`: привязанный `TgLink` дежурного в состоянии `idle` → его
     `chat_id`; иначе стабильный сентинел `-(user.id)` (детерминированно,
     сообщение находится в тестах и видно в `GET /api/internal/outbound`);
   - `text`: одна строка-заголовок с номером тикета, сутью (`package["summary"]`)
     и причиной (`package["why_escalated"]`), статус `pending`.
   Событие в журнал не дублируем — `tool_call`/`tool_result` диспетчера и
   `escalation_package`/`escalated` достаточно (FR-061).
6. Реакция пользователю — без изменений: `render_template(db,
   TEMPLATE_ESCALATION, ticket=...)` → `{"kind": "escalated", ...}` (контракт
   реакций и существующие тесты US1).

`AgentState` не расширяем: пакет читается из БД, в state только `tool_calls`.

### T038 — `backend/app/routers/admin.py` (новый файл) + подключение

```python
router = APIRouter(prefix="/api/admin", tags=["admin"],
                   dependencies=[Depends(get_operator)])
```

Все три эндпоинта — под `get_operator` (гость 401, не-оператор 403).

**`GET /api/admin/queue` → 200 `list[AdminQueueItem]`** (contracts/api.md):
- SQL: `select(Request).join(Ticket, Ticket.request_id == Request.id)` +
  подзадачи через `request.subtasks`; дубли (у обращения нет своего тикета)
  не попадают — как в `my_requests`.
- Поля item: `request_id`, `number`/`status`/`escalated`/`incident_id` из тикета;
  `user` — `QueueUser(full_name, email)` или `None` для гостя (FR-016);
  `summary/service/category/route/route_reason` — из первой подзадачи
  (`position == 1`); `priority` — **максимум по всем подзадачам** обращения
  (fold через `triggers.raise_priority`, FR-011: арбитраж уже применён, здесь
  только агрегация для очереди).
- Сортировка: Python `sorted(rows, key=lambda i: (-triggers.PRIORITY_ORDER
  .get(i.priority, 0), i.created_at))` — критический запрос: priority
  critical→low, затем created_at по возрастанию; используем существующий
  `PRIORITY_ORDER`, не дублируем маппинг.

**`GET /api/admin/escalations/{request_id}` → 200 `EscalationCard`** (FR-060):
- 404, если обращение не найдено; 404 `{ "detail": "Обращение не эскалировано" }`,
  если тикет отсутствует или `escalated is not True` (контракт коды ошибок не
  фиксирует — берём 404, т.к. ресурса «эскалация» нет).
- `request` = `_build_request_detail(db, request)` — helper, вынесенный из
  `routers/requests.py:request_detail` (тот же формат: `raw_text`, `masked_text`,
  `lang`, `translation` — исходник+перевод FR-060, `subtasks`, `dialog`,
  `ticket`, `events` — журнал FR-061). Рефакторинг не меняет поведение
  пользовательского эндпоинта (регрессия `test_get_request_detail_access`).
- `summary/recommendation/why_escalated` — payload последнего события
  `action="escalation_package"` по тикету; если его нет (данные до фазы 7) —
  fallback: summary из первой подзадачи, why из `route_reason`, recommendation —
  константа из `agent` (импортируем `ESCALATION_FALLBACK_RECOMMENDATION`).
- `checks` = payload-события, валидируется `EscalationCheck` (`service`, `ok`,
  `checked_at`, `note` — формат «HTTP {code}, {ms} мс» / «сеть в состоянии
  «сбой»/«норма»»).

**`POST /api/admin/escalations/{request_id}/close` → 200 `EscalationCloseResponse`**:
- Тело `EscalationCloseRequest {resolution: str, add_to_kb: bool = False}`;
  пустая `resolution` (после strip) → 422.
- 404 — обращение/эскалация не найдены; 409 `{ "detail": "Эскалация уже закрыта" }`,
  если тикет уже «закрыта».
- Перевод: если статус в `OPEN_STATUSES` — `tickets.set_status(db, ticket,
  STATUS_RESOLVED, actor="operator")`, затем `set_status(..., STATUS_CLOSED,
  actor="operator")`; если уже «решена» — сразу «закрыта». Цепочка FR-015
  соблюдена, каждый переход пишет `status_change` в журнал.
- `log_event(db, ticket_id=..., actor="operator", action="escalation_closed",
  payload={"resolution": ..., "add_to_kb": ...})`.
- `add_to_kb=true` в фазе 7 **игнорируем**: отвечаем `kb_draft=None` (контракт
  допускает `null`); фаза 9 (T043) заменит на вызов `draft_kb_article`.
- Ответ: `EscalationCloseResponse(ticket_status=ticket.status, kb_draft=None)`.

**`backend/app/main.py`**: `from app.routers import admin as admin_router` +
`app.include_router(admin_router.router)` после `requests_router`.

## 4. Тесты — `backend/tests/test_escalations.py` (новый файл)

Моки (по паттерну `tests/test_agent.py::make_fake_llm`, переиспользуем импортом):
- `monkeypatch.setattr("app.llm.chat", ...)` — диспетчеризация по маркерам
  промпта: «Переведи» → перевод; `"subtasks"` → сплиттер; `"summary"` /
  маркер промпта саммари (напр. «саммари эскалации») → JSON
  `{summary, recommendation, why_escalated}`; иначе — ответ классификатора.
  Счётчик вызовов — обёрткой как в `test_quickstart_us2.make_counting_llm`
  (проверяем «саммари не зовётся» в ветке лимита и «ветка сбоя без LLM» не ломается).
- HTTP не мокаем (эскалационные кейсы не делают auto_check; кейс со сбоями
  Wi-Fi идёт через прямую запись `Service(state=...)` в `db_engine`, как в
  `test_quickstart_us2._set_service_state`).
- Оператор: пользователь `role="operator"` пишется напрямую в БД
  (`db_engine` + sessionmaker), логин через отдельный `TestClient(app)` —
  как в `test_get_request_detail_access` (автосоздание при логине даёт student,
  оператору роль проставляем руками).
- `fake_http` не нужен; гостевая сессия — дефолтный `client`.

| Кейс | Тест-функция |
|---|---|
| Happy: confidence 0.42 → эскалация; queue содержит item (escalated=true), сортировка priority→created_at; карточка содержит все блоки FR-060 (summary/checks/recommendation/why_escalated + raw_text + events); в `outbound_messages` одна pending-запись дежурному | `test_escalation_card_contains_fr060_package` |
| Сценарий 4b end-to-end: вопрос «вне базы» (route escalate от классификатора, confidence 0.95) → реакция `escalated`, карточка открывается оператором | `test_quickstart_scenario_4b_escalation_flow` |
| Английский текст: `lang=en`, перевод в карточке (`request.translation`), саммари собран по переводу | `test_escalation_card_includes_translation` |
| Путь триггера: «жалоба на сотрудника» → route escalate; `why_escalated` содержит имя правила; путь лимита: unit-вызов `agent.node_escalate` со `state["tool_calls"] = MAX_TOOL_CALLS` → пакет из шаблонов, `llm.chat` не вызывался | `test_escalation_trigger_force_and_tool_limit_paths` |
| LLMUnavailable: `unavailable=True` в моке → classify сам уходит в escalate; нода не падает, карточка собирается fallback-пакетом, why содержит «Модель недоступна» | `test_escalation_package_fallback_when_llm_down` |
| Эскалация после сбоя: тикет «решена» (outage) → reply → эскалация не падает (`ValueError` из цепочки), статус остаётся «решена», `escalated=true` | `test_escalation_from_resolved_ticket_keeps_status` |
| Закрытие: close → `ticket_status == "закрыта"`, в журнале `escalation_closed` от operator; повторный close → 409; `add_to_kb=true` → `kb_draft is None` | `test_close_escalation_and_double_close_409` |
| Доступ: гость → 401, студент → 403 на всех трёх эндпоинтах; чужая эскалация оператору отдаётся (оператор — доверенная роль) | `test_admin_endpoints_require_operator` |
| Идемпотентность уведомления: повторный прогон пайплайна по тому же тикету (reply → эскалация) → в `outbound_messages` по-прежнему одна запись | `test_duty_notification_sent_once` |
| Регрессия auth_required: гость + cert_order → реакция `auth_required`, тикет НЕ эскалирован, outbound пуст | `test_guest_cert_order_not_escalated` |
| Гость в очереди: `user is None` у гостевого обращения; приоритет item = max по подзадачам составного обращения | `test_queue_guest_user_null_and_priority_aggregation` |

## 5. Риски и подводные камни

- **Статусная цепочка FR-015 и 409.** Главный риск фазы — `set_status` в
  `node_escalate` при тикете «решена»/«закрыта» (`TRANSITIONS["решена"] =
  {"закрыта"}`) → `ValueError` роняет пайплайн. Решение — условие по
  `OPEN_STATUSES` (п. 1 T037) и тест `test_escalation_from_resolved_ticket_keeps_status`.
  Close строится строго «решена»→«закрыта»; повторный close → осмысленный 409,
  а не 500 из `ValueError`.
- **Лимит tool_calls=3 (FR-021).** `summarize` — вызов инструмента, считается
  в лимит. При `tool_calls >= MAX` нода НЕ зовёт LLM, пакет — из шаблонов
  (эскалация уже и есть безопасный исход превышения, «с журналом действий»
  обеспечивается событиями `tool_call`/`tool_result` + `escalation_package`).
  Иначе лимит превращался бы в вечный цикл «эскалация требует инструмента».
- **Приоритет-арбитраж (FR-011).** Понижать нельзя: в очереди только агрегация
  через `raise_priority`; fallback-пакет не переписывает `subtask.priority`.
- **Гостевые сессии (FR-016).** `requests.user_id is None` → `QueueUser=None`;
  уведомление дежурному не зависит от автора обращения; доступ в админку — по
  роли, гость 401.
- **LLMUnavailable → деградация без молчания.** Пакет из шаблонов обязан
  сохранять четыре блока FR-060 (пустой `checks` — допустим, список); нода
  никогда не пробрасывает исключение LLM наружу — иначе `POST /api/requests`
  отдаст 500 вместо реакции `escalated` (регрессия
  `test_llm_unavailable_safe_route`).
- **Идемпотентность.** Уведомление дежурному — один раз (флаг `already`);
  `escalation_package` пишется на каждый эскалационный прогон (история), карточка
  читает последнее — обновление саммари при повторной эскалации корректно.
  Повторный close → 409, а не двойной перевод статуса.
- **API диспетчера фазы 4 не зафиксирован** (`call_tool` в тестах против
  `dispatch` в коде) — см. оговорку в §2; перед стартом T036 убедиться, что
  фаза 4 закрыта и имя диспетчера известно.
- **Параллельные фазы 5/6/8–10 трогают те же файлы** (`agent.py` — ноды
  execute_route kb/cert_order, `routers/admin.py` — certs/incidents/metrics):
  изменения аддитивны, `routers/admin.py` создаём с общим префиксом/тегом/
  `dependencies=[Depends(get_operator)]`, чтобы фазы 8–10 только добавляли
  эндпоинты.

## 6. Валидация

```bash
cd backend
# Фазовые тесты (новый файл + регрессия US1/auth/events)
.venv/bin/python -m pytest tests/test_escalations.py tests/test_agent.py \
    tests/test_triggers.py tests/test_core.py -v
# Полный прогон
.venv/bin/python -m pytest tests/ -v
```

Ожидание: новый файл зелёный; `test_agent.py`/`test_triggers.py`/`test_core.py`
без изменений. Оговорка: `tests/test_tools.py` сейчас красный из-за
незавершённой фазы 4 (`call_tool`/`ToolCall`/`CHECK_TIMEOUT_SEC` отсутствуют в
`tools.py`) — это вне скоупа фазы 7 и должно быть зелёным к моменту старта T036
(фазы строго последовательны внутри `agent.py`/`tools.py`).

Ручная проверка сценария 4b (поднятое ядро, LLM-ключ задан):
`POST /api/requests {"text":"Когда откроется бассейн в кампусе?"}` (вне базы →
`escalated`) → `POST /api/auth/login smirnov@misis.ru` →
`GET /api/admin/queue` (эскалация вверху своего приоритета) →
`GET /api/admin/escalations/{request_id}` (все блоки FR-060) →
`POST /api/admin/escalations/{request_id}/close {"resolution":"…","add_to_kb":false}`
→ `"ticket_status": "закрыта"`.
