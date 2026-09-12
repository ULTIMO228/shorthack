# Phase 10 — Polish & Cross-Cutting Concerns: план реализации (T044–T047)

Фича: 001-misis-support-assistant (ядро ИИ-помощника техподдержки МИСИС).
Вход: phases 1–9 завершены (в т.ч. `routers/admin.py` от T038, `incidents.py` от T040/T041,
финальный реестр `tools.py` от T023 с интерфейсом `call_tool`, который уже закреплён
в `backend/tests/test_tools.py`). Код фазы 10 опирается на **целевой** интерфейс
`tools.call_tool`/`ToolError`/`CHECK_TIMEOUT_SEC` из тестов, а не на промежуточный
`tools.dispatch` (см. риск R1).

---

## 1. Цель и критерий готовности

**Что должно работать:**

1. **FR-062 (метрики)** — `GET /api/admin/metrics` отдаёт `MetricsOut`:
   `auto_closed_pct` (% заявок без человека: не escalated и решены/закрыты),
   `avg_first_reaction_sec` (по журналу events: `tickets.created_at` → первая `reaction_sent`),
   `incidents_total`, `incidents_active`, `requests_total`, `escalations_open`.
2. **Тест-панель оператора (FR-020, FR-024)** — в `routers/admin.py`:
   - `GET /api/admin/status-board` — все сервисы + последний `service_checks` по каждому (contracts/api.md);
   - `PATCH /api/admin/services/{id}` — переключение только `emulated`, для `real` → **409**;
   - `GET /api/admin/tools` — реестр из `tools.TOOLS` с params-схемами;
   - `POST /api/admin/tools/{name}/invoke` — только `check_site | check_lms | check_wifi | simulate_wave`, иначе **404**, невалидные params → **422**.
3. **T046** — README.md и `.ai/` (SESSION_STATE.md, PROJECT_MAP.md, STACK.md, + append SESSIONS.md по протоколу AGENTS.md) описывают архитектуру ядра, а не сокращатель ссылок.
4. **T047** — все 8 сценариев quickstart.md проходят вручную curl'ом, `pytest -v` зелёный.

**Критерий готовности (чекпоинт фазы):**

- `cd backend && .venv/bin/python -m pytest tests/ -v` — **all green** (включая новые
  `tests/test_metrics.py`, `tests/test_admin_panel.py`);
- чек-лист из §7 (8 сценариев quickstart + 4 новых admin-вызова) пройден вручную против
  поднятого `uvicorn app.main:app` с засидированной БД;
- новых зависимостей в `requirements.txt` нет; LLM/httpx в тестах замоканы.

**Зависимости:** T044 зависит только от Ph2 (models/events) — реально параллелен;
T045 требует T023 (реестр инструментов) и T040/T041 (`incidents.simulate_wave`);
T046/T047 — последними.

---

## 2. Точки интеграции

| Файл | Что меняется |
|---|---|
| `backend/app/metrics.py` | **Новый модуль**: 6 функций метрик + фасад `collect_metrics()` (§3.1) |
| `backend/app/routers/admin.py` | **Дифф**: +4 эндпоинта (`metrics`, `status-board`, `services/{id}`, `tools`, `tools/{name}/invoke`). Файл создан в T038 — добавлять, не переписывать; зависимость `get_operator` (auth.py:72) уже используется соседними эндпоинтами |
| `backend/app/main.py:50-52` | Проверить `include_router(admin_router)` (добавлен в T038). Если фаза 7 ещё не влита — добавить `from app.routers import admin as admin_router` + `app.include_router(admin_router.router)` |
| `backend/app/tools.py` | **Дифф**: в записи реестра `TOOLS` 4 админ-инструментов добавить ключ `"params"` (публичные params для GET /tools и валидации invoke); добавить константу `ADMIN_INVOKABLE: tuple[str, ...]`; invoke идёт через существующий `call_tool` (tools.py, целевая сигнатура по test_tools.py: `call_tool(db, name, params, *, ticket_id=None, subtask_id=None, tool_calls=0) -> ToolCall` с полями `.result`, `.tool_calls`) |
| `backend/app/incidents.py` | Только вызов: `simulate_wave(db, service: str, count: int = 3) -> dict` (создаётся в T040/T041, возвращает `{"incident_id": int \| None, "created_requests": list[int]}`). Сигнатуру сверить при старте T045; расхождение — эскалировать, не адаптировать молча |
| `backend/app/auth.py` | Без изменений: `get_operator` (401 гостю/неавторизованному через `get_current_user`, 403 не-оператору) |
| `backend/app/schemas.py` | **Без изменений**: схемы готовы с T006 — `MetricsOut` (schemas.py:349), `StatusBoardItem`+`LastCheckOut` (275/268), `ServicePatchRequest/Response`+`ServiceOut` (292–297), `ToolParam/ToolOut/ToolInvokeRequest/ToolInvokeResponse` (300–320) |
| `backend/app/models.py`, `events.py`, `tickets.py`, `triggers.py`, `agent.py` | Без изменений |
| `backend/tests/conftest.py` | Добавить общие фикстуры: `operator_client` (логин через API предсозданного `User(role="operator")`) и перенос `fake_http`/`FakeHttpClient` из test_tools.py (§4) |
| `backend/tests/test_metrics.py`, `backend/tests/test_admin_panel.py` | **Новые** тестовые файлы |
| `README.md`, `.ai/SESSION_STATE.md`, `.ai/PROJECT_MAP.md`, `.ai/STACK.md`, `.ai/SESSIONS.md` | T046, §3.4 |

---

## 3. Пошаговый план по задачам

### T044 — metrics.py + GET /api/admin/metrics (FR-062) [P]

**3.1. Новый модуль `backend/app/metrics.py`** — чистое чтение, ни одной записи в БД.
Константы статусов брать из `tickets.py` (`STATUS_RESOLVED`, `STATUS_CLOSED`), не дублировать строки.

```python
"""Метрики качества работы помощника (FR-062): чтение из журнала events и агрегатов.

auto_closed_pct — доля заявок, решённых без человека (escalated=false и статус
«решена»/«закрыта») среди ВСЕХ заявок. avg_first_reaction_sec — средний лаг
tickets.created_at → первая запись events.action='reaction_sent' (минимум по
тикету; тикеты без реакции в среднее не входят). Пустые выборки → 0, не ошибка.
"""

FINAL_STATUSES = (tickets.STATUS_RESOLVED, tickets.STATUS_CLOSED)

def auto_closed_pct(db: OrmSession) -> float
def avg_first_reaction_sec(db: OrmSession) -> float
def incidents_total(db: OrmSession) -> int        # select(func.count(Incident.id))
def incidents_active(db: OrmSession) -> int       # ... .where(Incident.status == "active")
def requests_total(db: OrmSession) -> int         # select(func.count(Request.id))
def escalations_open(db: OrmSession) -> int       # Ticket.escalated.is_(True),
                                                  # Ticket.status.not_in(FINAL_STATUSES)
def collect_metrics(db: OrmSession) -> MetricsOut # фасад, собирает все шесть
```

Критические запросы:

- `auto_closed_pct`: `SELECT count(*) FROM tickets` (знаменатель; ноль → вернуть `0.0`);
  числитель — `count(*).where(Ticket.escalated.is_(False), Ticket.status.in_(FINAL_STATUSES))`;
  результат `round(100 * num / den, 1)`.
- `avg_first_reaction_sec` — **вычитание в Python**, не в SQL (SQLite не вычитает datetime):
  ```python
  rows = db.execute(
      select(Ticket.id, Ticket.created_at, func.min(Event.created_at))
      .join(Event, Event.ticket_id == Ticket.id)
      .where(Event.action == "reaction_sent")
      .group_by(Ticket.id)
  ).all()
  lags = [(first - created).total_seconds() for _id, created, first in rows
          if first is not None and created is not None]
  return round(mean(lags), 1) if lags else 0.0
  ```
  Даты timezone-aware (`models._utcnow`), вычитание безопасно. Порядок: не важен (среднее),
  но `group_by(Ticket.id)` обязателен.
- `escalations_open` — «открытые» = не в финале: `status NOT IN ("решена", "закрыта")`
  (по `tickets.OPEN_STATUSES` нельзя — там нет «решена», а закрытая эскалация точно не open).

**3.2. Эндпоинт в `routers/admin.py`** (в конец файла, после эндпоинтов T038–T043):

```python
@router.get("/api/admin/metrics", response_model=MetricsOut)
def admin_metrics(
    _operator: Annotated[User, Depends(get_operator)],
    db: Annotated[OrmSession, Depends(get_session)],
) -> MetricsOut:
    return metrics.collect_metrics(db)
```

Коды: 200 — тело по contracts/api.md §«Метрики»; 401 — нет входа (`get_current_user`);
403 — не оператор. Пример тела: `{"auto_closed_pct": 62.5, "avg_first_reaction_sec": 4.8,
"incidents_total": 2, "incidents_active": 1, "requests_total": 16, "escalations_open": 3}`.

Импорт: `from app import metrics`; имя функции не пересекается с модулем.

### T045 — status-board, services patch, tools registry/invoke [P]

**3.3. Расширение реестра `backend/app/tools.py`** (подготовка к эндпоинтам):

1. В записи `TOOLS["check_site"]`, `TOOLS["check_lms"]`, `TOOLS["check_wifi"]` и
   `TOOLS["simulate_wave"]` (последний добавлен в фазе 8) добавить ключ `"params"` —
   список словарей `{name, type, required, default, choices, description}`:
   - `check_site`: `[{name:"url", type:"string", required:false, default:"https://misis.ru", choices:null, description:"проверяемый URL (только штатный)"}]`
   - `check_lms`: аналогично, default `"https://newlms.misis.ru"`;
   - `check_wifi`: `[{name:"network", type:"string", required:true, default:null, choices:["wifi_guest","wifi_edu","wifi_corp"], description:"Wi-Fi сеть (slug)"}]`;
   - `simulate_wave`: `[{name:"service", type:"string", required:true, choices:[все service-slugs из SERVICE_TOOLS], ...}, {name:"count", type:"integer", required:false, default:3, min:1, max:10, ...}]`.
2. Константа `ADMIN_INVOKABLE: tuple[str, ...] = ("check_site", "check_lms", "check_wifi", "simulate_wave")`.
3. Внутренние ключи `"schema"`/`"fn"` не трогать — они для агента; `"params"` — только для панели.

**3.4. Эндпоинты в `routers/admin.py`** (все четыре — под `Depends(get_operator)`):

**`GET /api/admin/status-board`** → `list[StatusBoardItem]`, 200.
Порядок — `services.id` (детерминирован: real-сервисы первыми, как в сиде).
Один запрос последних проверок, без N+1:
```python
latest = db.execute(
    select(ServiceCheck.service_id, func.max(ServiceCheck.checked_at))
    .group_by(ServiceCheck.service_id)
).all()
# затем подтянуть ServiceCheck по паре (service_id, max_checked_at) и сложить в dict
```
Сервис без проверок → `last_check=None` (по контракту, напр. `MISIS-EDU` с `last_check: null`).

**`PATCH /api/admin/services/{service_id}`** → `ServicePatchResponse`, тело `ServicePatchRequest{state: "up"|"down"}`.
- `db.get(Service, service_id)` → нет → **404** «Сервис не найден»;
- `service.check_type != "emulated"` → **409** «Сервис с реальной проверкой переключать нельзя (только эмулируемые)» — проверка ДО изменения;
- иначе: `old = service.state; service.state = body.state; service.updated_at = datetime.now(timezone.utc)` (явно, не полагаться на onupdate), commit;
- журнал: `log_event(db, ticket_id=None, actor="operator", action="service_state_change", payload={"service_id":..., "from": old, "to": body.state})` (FR-061);
- 200 `{"service": ServiceOut(...)}`.

**`GET /api/admin/tools`** → `list[ToolOut]` (200): для каждого `name in tools.ADMIN_INVOKABLE`
(порядок — как в кортеже, детерминирован): `name`, `type` из реестра (`"exec"`;
`simulate_wave` → `"simulation"` — отличать в панели, зафиксировать в тесте), `description`,
`params` → `list[ToolParam(name, type, default)]` из `TOOLS[name]["params"]`
(поля `required/choices/min/max` в ToolParam не входят — нужны только валидации invoke).

**`POST /api/admin/tools/{name}/invoke`** → `ToolInvokeResponse`, тело `ToolInvokeRequest{params: dict = {}}`.
- `name not in tools.ADMIN_INVOKABLE` → **404** «Инструмент не найден» (в т.ч. для llm-инструментов реестра — `kb_agent`, `summarize` и т.п.: с точки зрения панели их нет);
- валидация params против `TOOLS[name]["params"]` → неверный ключ/тип/обязательность/choices/diапазон → **422** с русским `detail` («Неизвестный параметр …», «Параметр … обязателен», «Параметр … вне допустимого набора»); проверка валидаторами Pydantic тут не работает (params — свободный dict), поэтому ручная;
- выполнение:
  - `check_site`/`check_lms`: `{"url"}` либо отсутствует, либо равен дефолту — иначе уже отсечено на валидации; `call = tools.call_tool(db, name, {})`; тело `{"tool": name, "ok": call.result["ok"], "result": call.result}` (200 — даже при `ok=false`: недоступность сайта это результат проверки, не ошибка HTTP; код ответа от `FakeHttpClient`/httpx внутри `call.result.http_code`). Запись в `service_checks` — штатный побочный эффект, real-сервисы питают борд.
  - `check_wifi`: `call_tool(db, "check_wifi", {"network": params["network"]})` — то же тело.
  - `simulate_wave`: `result = incidents.simulate_wave(db, service=params["service"], count=params.get("count", 3))`; тело `{"tool": "simulate_wave", "ok": True, "result": result}` (формат — contracts/api.md: `{incident_id, created_requests}`).
- вызов идёт **без** `ticket_id`/`subtask_id` — проверки панели не привязаны к обращению; счётчик FR-021 (tool_calls) на обращение не трогаем (state графа живёт внутри одного `run_pipeline`).

### T046 — документация [P]

**3.5. `README.md`** — полная замена (сейчас описывает сокращатель ссылок):
- заголовок/описание: ИИ-помощник техподдержки МИСИС (ядро, фича 001);
- стек: FastAPI + SQLite (SQLAlchemy 2.0) + LangGraph + Yandex AI Studio (httpx), pytest;
- запуск: `cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt`,
  `.env` по шаблону `backend/.env.example` (YANDEX_API_KEY/YANDEX_FOLDER_ID/модели/BOT_INTERNAL_TOKEN),
  `.venv/bin/uvicorn app.main:app --reload` (:8000); при старте — create_all + идемпотентный seed
  (5 пользователей, 5 сервисов, БЗ; при недоступности embeddings API — keyword-деградация);
- раздел «Проверка»: ссылка на `specs/001-misis-support-assistant/quickstart.md`, 8 сценариев curl'ом;
- таблица ключевых API-групп (auth/requests/certs/admin/internal/health);
- тесты: `cd backend && .venv/bin/python -m pytest tests/ -v` (LLM мокается);
- границы: веб — фича 002 (`frontend/`), бот — фича 003; Graphiti/Neo4j опционален
  (`docker compose up -d`, без env приложение работает в SQLite-режиме).

**3.6. `.ai/`** — новые строки по densecode (English, `key:value`, ≤160 символов; старые русские строки не трогать):

- `SESSION_STATE.md` — перезаписать снапшот: фича 001 Ph1–Ph10 done, список модулей
  (agent/tools/triggers/tickets/kb/certs/incidents/metrics + routers auth/requests/certs/admin/internal),
  «Горит: пусто», следующие шаги — фичи 002/003.
- `PROJECT_MAP.md` — новая карта ядра: файлы backend/app/*.py с однострочниками, поток
  `POST /api/requests → agent.run_pipeline → StateGraph`, admin-эндпоинты, что из quickstart работает.
- `STACK.md` — стек ядра: FastAPI/SQLAlchemy 2.0/SQLite, LangGraph 1.x, Yandex AI Studio
  (chat/embed, 256-dim), httpx-проверки 5 c, без новых зависимостей; убрать разделы
  сокращателя (генерация кодов, монетизация ссылок) — заменить дорожной картой ядра.
- `SESSIONS.md` — append-запись о сессии фазы 10 (протокол AGENTS.md).

### T047 — полный прогон quickstart + pytest

**3.7.** Выполнить чек-лист §7 и команды §6. Любое расхождение с ожиданиями — чинить
в своей фазе (docs/тесты), при баге в чужой фазе — фиксировать в `.ai/SESSION_STATE.md`
(«Горит») и не латать чужой код без отметки.

---

## 4. Тесты

Общие правила: LLM мокается `monkeypatch.setattr("app.llm.chat", ...)` /
`"app.llm.chat_structured"` (паттерн `make_fake_llm` из tests/test_agent.py:21);
сеть — подмена `app.tools.httpx.Client` (паттерн `FakeHttpClient` из tests/test_tools.py:26);
БД — фикстуры `db_engine`/`app`/`client` из conftest.py (реальная `shorthack.db` не трогается).
Seed в тестах не запускается — сущности создаются руками.

**4.1. `backend/tests/conftest.py` — добавить:**

```python
@pytest.fixture()
def operator_client(client, db_engine): ...
```
Создаёт `User(email="smirnov@misis.ru", full_name="Смирнов Олег", role="operator")` напрямую
в тестовой БД (login привяжет сессию к существующему пользователю — role сохранится;
автосоздание через login дало бы `role="student"`, routers/auth.py:38), затем
`client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})` → возвращает тот же
TestClient с cookie оператора.

Перенести `FakeHttpResponse`/`FakeHttpClient`/`fake_http` из test_tools.py в conftest.py
(фикстура с тем же именем — test_tools.py продолжит работать без правок).

**4.2. `backend/tests/test_metrics.py`** (T044):

| Кейс | Что проверяет |
|---|---|
| `test_metrics_empty_db_all_zeros` | пустая БД → `auto_closed_pct=0.0`, `avg=0.0`, все count=0 (нет деления на ноль) |
| `test_auto_closed_pct_counts_resolved_non_escalated_only` | 4 тикета: 2 resolved не-escalated, 1 escalated open, 1 escalated resolved → **50.0**; round 1 знак |
| `test_auto_closed_pct_includes_closed_status` | тикет «закрыта» не-escalated входит в числитель |
| `test_avg_first_reaction_uses_first_reaction_event` | тикет с двумя `reaction_sent` (второй позже) — лаг считается до ПЕРВОГО (min); 2 тикета с лагами 2 c и 4 c → avg 3.0 |
| `test_avg_first_reaction_skips_tickets_without_reaction` | тикет без `reaction_sent` не ломает среднее |
| `test_escalations_open_excludes_final_statuses` | escalated «решена»/«закрыта» не считаются open |
| `test_incidents_total_and_active` | 2 инцидента (active+resolved) → total=2, active=1 |
| `test_metrics_requires_operator` | гость → 401, студент (логин petrova@edu.misis.ru, роль из предсозданного User) → 403 |

Хелпер файла: `_make_ticket(db, *, status, escalated, lag_sec=None)` — создаёт
Request→Ticket (через `tickets.ensure_ticket`), нужные Event (`reaction_sent` с
`created_at = ticket.created_at + lag`), Incident.

**4.3. `backend/tests/test_admin_panel.py`** (T045):

| Кейс | Что проверяет |
|---|---|
| `test_status_board_returns_services_with_last_check` | 2 сервиса: у одного 2 проверки (разное `checked_at`) → `last_check` = самая поздняя; у второго проверок нет → `last_check=None`; порядок = `services.id`; гость 401 |
| `test_status_board_operator_only` | студент → 403 |
| `test_patch_service_emulated_ok` | PATCH emulated-сервиса `down` → 200, `service.state=="down"`, `updated_at` свежий, в events есть `service_state_change` (actor `operator`) |
| `test_patch_service_real_conflict_409` | PATCH `misis.ru` (check_type=real) → **409**, state в БД не изменился |
| `test_patch_service_not_found_404` | id=999 → 404 |
| `test_list_tools_registry_shape` | 200, ровно 4 инструмента в порядке `ADMIN_INVOKABLE`; у `check_wifi` есть param `network` (required), у `simulate_wave` — `service`+`count` (default 3); тип `simulate_wave` == `"simulation"` |
| `test_invoke_check_wifi_up_and_down` | эмулированные сети up/down → `ok` true/false, `result.state` |
| `test_invoke_check_site_with_fake_http` | `fake_http["https://misis.ru"]=FakeHttpResponse(200)` → `ok=true`, `result.http_code=200`; 500 → `ok=false` (200 HTTP!) |
| `test_invoke_unknown_tool_404` | несуществующее имя и llm-инструмент реестра (`kb_agent`) → 404 |
| `test_invoke_params_validation_422` | `check_wifi` без `network`; `check_wifi.network="internet"`; `simulate_wave.count=0` и `=99`; лишний ключ; `check_site.url="https://evil.example"` → все 422 |
| `test_invoke_simulate_wave_delegates_to_incidents` | `monkeypatch.setattr("app.incidents.simulate_wave", spy)` → spy вызван с `(service="wifi_edu", count=3)`, ответ `{"tool":"simulate_wave","ok":true,"result":{...spy-результат}}`; гость 401, студент 403 |

`simulate_wave` end-to-end здесь НЕ дублируется — он покрыт в tests/test_incidents.py (T039).

---

## 5. Риски и подводные камни

- **R1. Интерфейс tools.py в переходе.** На диске сейчас `dispatch()`, тесты требуют
  `call_tool()/ToolError/CHECK_TIMEOUT_SEC` (проверено: `pytest -x` падает на
  test_agent.py::test_simple_request_auto_check — рабочее дерево mid-flight). План и тесты
  фазы 10 верстаются против **целевого** интерфейса из test_tools.py; если к старту T045
  миграция T023 не завершена — фаза 10 блокируется, латать tools.py в рамках T045 нельзя.
- **R2. Знаменатель auto_closed_pct.** Контракт «% заявок без человека» не уточняет
  базу. Принято: все тикеты; пустая БД → 0.0. Семантика зафиксирована тестом
  `test_auto_closed_pct_counts_resolved_non_escalated_only` — не менять без правки контракта.
- **R3. 409 для real-сервисов.** Проверка `check_type` строго до записи; PATCH не должен
  перезаписывать `services.state` real-сервисов (иначе борд и `check_site` начнут врать).
  Операторский quickstart (сценарий 3) опирается на PATCH emulated — он должен остаться
  рабочим.
- **R4. Лимит tool_calls=3 (FR-021).** Счётчик живёт в `AgentState` одного прогона
  графа. Invoke из панели идёт вне графа и НЕ должен инкрементить чужие счётчики —
  вызывать `call_tool` без графового state; `simulate_wave` гонит count отдельных
  прогонов, у каждого свой счётчик.
- **R5. Реальные HTTP-вызовы наружу.** `invoke check_site/check_lms` в ручном прогоне
  бьёт в интернет (таймаут 5 c, FR-022/023) — для демо нужен выход в сеть; в тестах —
  только `fake_http`, реальных вызовов быть не должно.
- **R6. simulate_wave гоняет полный пайплайн с LLM.** Ручной прогон требует
  `YANDEX_API_KEY`; латентность ≈ count × (LLM-вызовы классификатора) — ограничить
  `count` валидатором (1..10), дефолт 3. Без ключа деградация по FR-013 даст эскалации —
  инцидент не создастся; это ожидаемо, зафиксировать в чек-листе §7 п.7 (нужен ключ).
- **R7. Админ-права.** Все 4 эндпоинта — строго `get_operator`: гость 401 (через
  `get_current_user`), студент/сотрудник 403. В тестах оператор создаётся напрямую в БД —
  автосоздание через login всегда `role="student"`.
- **R8. Приоритет-арбитраж (FR-011).** Фаза 10 не трогает `triggers.py`. PATCH services
  сам по себе приоритет не меняет — `critical` поднимется только при следующем обращении
  через execute_route (правило уже есть, T027). В тестах не дублировать арбитраж.
- **R9. Гостевые сессии (user_id null).** Метрики и борд автор-агностичны — ничего не
  фильтруем по user_id; денормализация не требуется.
- **R10. LLMUnavailable.** Метрики/борд/patch/tools-list не зависят от LLM; invoke
  check_* — тоже (exec-инструменты, FR-020). Только simulate_wave зависит — см. R6.
- **R11. Идемпотентность.** PATCH services — последняя запись побеждает (допустимо,
  это ручной рубильник). invoke check_* каждый раз пишет новую строку `service_checks` —
  это история, а не дубль. simulate_wave принципиально не идемпотентен (создаёт
  обращения) — документировать в README-разделе тест-панели.
- **R12. SQLite и даты.** Вычитание datetime только в Python (SQLAlchemy не вычтет
  datetime в SQLite); даты timezone-aware из `models._utcnow` — `total_seconds()` безопасен.
  `avg_first_reaction_sec` округление `round(x, 1)` — формат контракта.
- **R13. N+1 на борде.** 5 сервисов — коррелированные запросы простительны, но план
  требует один `group_by` + мапу; не писать запрос в цикле (LESSONS L002 — беречь
  соединения, всё через `Depends(get_session)`).

---

## 6. Валидация

```bash
# весь набор — обязательный гейт фазы
cd backend && .venv/bin/python -m pytest tests/ -v

# новые тесты фазы — при итерации
.venv/bin/python -m pytest tests/test_metrics.py tests/test_admin_panel.py -v

# регрессия смежного (панель трогает реестр и conftest)
.venv/bin/python -m pytest tests/test_tools.py tests/test_incidents.py tests/test_admin_panel.py -v

# ручной прогон — чек-лист §7 против живого сервера
.venv/bin/uvicorn app.main:app --reload   # :8000
```

Критерий: весь `tests/` зелёный, 8 сценариев §7 проходят, расхождений тела/кодов с
contracts/api.md нет. Если стандартный путь (pytest) красный — не «чинить» ослаблением
ассертов: разбирать причину (skill testing); непройденные проверки в отчёте называть прямо.

---

## 7. Чек-лист ручной проверки T047 (8 сценариев quickstart + панель)

Предусловия: `uvicorn` поднят, БД засидирована, задан `YANDEX_API_KEY` (иначе R6),
`BOT_INTERNAL_TOKEN` в env. Все вызовы `curl -c jar -b jar http://localhost:8000/api/...`.

**0. Разогрев**
```bash
curl -b jar http://localhost:8000/api/health            # → {"status":"ok","llm":"up|down"}
curl -c jar -b jar -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"smirnov@misis.ru"}'   # оператор → 200
```

**1. Вход** (quickstart п.1)
```bash
curl -c jar1 -X POST .../api/auth/login -d '{"email":"ivanov@misis.ru"}'  # → 200 + cookie
curl -X POST .../api/auth/login -d '{"email":"petrov@gmail.com"}'         # → 400
```

**2. Простое обращение, сеть в норме** (п.2)
```bash
curl -b jar1 -X POST .../api/requests -d '{"text":"Не работает вайфай MISIS-Guest","channel":"web"}'
# → subtasks[0].route=auto_check, reactions[0].kind=clarification, ticket.status=«ждёт ответа пользователя»
curl -b jar1 -X POST .../api/requests/<id>/reply -d '{"text":"На всех устройствах"}'  # → 200, диалог продолжается
```

**3. Ветка сбоя** (п.3, FR-011/FR-026, SC-004)
```bash
curl -b jar -X PATCH .../api/admin/services/<id MISIS-EDU> -d '{"state":"down"}'   # → 200 (emulated)
time curl -b jar1 -X POST .../api/requests -d '{"text":"Не работает Wi-Fi MISIS-EDU в 4 корпусе","channel":"web"}'
# → reactions[0].kind=outage_notice, ≤ 2 c, priority=critical, ticket.status=«решена»
curl -b jar -X PATCH .../api/admin/services/<id misis.ru> -d '{"state":"down"}'    # → 409 (real)
curl -b jar -X PATCH .../api/admin/services/<id MISIS-EDU> -d '{"state":"up"}'     # вернуть норму
```

**4. RAG** (п.4, FR-031/032)
```bash
curl -b jar1 -X POST .../api/requests -d '{"text":"Как подключиться к eduroam с телефона?","channel":"web"}'
# → route=kb, reactions[0].kind=answer (по «Регламенту Wi-Fi»)
curl -b jar1 -X POST .../api/requests -d '{"text":"Почему луна иногда зелёная?","channel":"web"}'
# → escalated (вне базы, без выдумки)
```

**5. Справки** (п.5, FR-040..043)
```bash
curl -b jar1 -X POST .../api/certs/orders -d '{"cert_type":"study"}'   # → status=«не обработана»
curl -b jar -X PATCH .../api/admin/certs/orders/<id> -d '{"status":"обрабатывается"}'  # → 200
curl -b jar1 .../api/certs/orders                                       # → статус обновился
```

**6. Дедупликация** (п.6, FR-014) — повторить обращение из п.2 тем же пользователем
→ `duplicate=true`, тикет тот же.

**7. Инцидент** (п.7, FR-050..053, SC-005)
```bash
curl -b jar -X POST .../api/admin/tools/simulate_wave/invoke \
  -d '{"params":{"service":"wifi_edu","count":3}}'     # → incident_id != null, created_requests=[..,..,..]
curl -b jar .../api/admin/incidents                     # → active первым
curl -H "X-Bot-Token: $BOT_INTERNAL_TOKEN" .../api/internal/outbound?status=pending
# → уведомление дежурному «🔴 Инцидент…»
curl -b jar -X POST .../api/admin/incidents/<id>/broadcast -d '{"text":null}'  # → sent ≥ 3
```

**8. Самообучение** (п.8, FR-063) — закрыть эскалацию из п.4:
```bash
curl -b jar -X POST .../api/admin/escalations/<request_id>/close \
  -d '{"resolution":"Перезапущен контроллер точки доступа","add_to_kb":true}'  # → kb_draft
curl -b jar -X POST .../api/admin/kb/articles/<id>/confirm                     # → confirmed=true
# повторный вопрос по теме → answer из новой статьи
```

**9. Новое в фазе 10 (FR-020/024/062)**
```bash
curl -b jar .../api/admin/metrics        # 200, шесть полей, типы чисел
curl -b jar .../api/admin/status-board   # 5 сервисов; у проверявшихся last_check заполнен
curl -b jar .../api/admin/tools          # 4 инструмента с params
curl -b jar -X POST .../api/admin/tools/check_wifi/invoke -d '{"params":{"network":"wifi_edu"}}'  # 200
curl -b jar -X POST .../api/admin/tools/unknown/invoke -d '{}'                                      # 404
curl -b jar -X POST .../api/admin/tools/check_wifi/invoke -d '{"params":{}}'                        # 422
curl -b jar1 .../api/admin/metrics       # студент → 403
```

---

## Резюме

- T044: новый `metrics.py` (6 функций + `collect_metrics`), знаменатель auto_closed_pct = все тикеты, реакция = min(events `reaction_sent`) − `tickets.created_at` (вычитание в Python).
- T045: 4 эндпоинта в `routers/admin.py` под `get_operator`; реестр `tools.TOOLS` обрастает публичными `"params"` + `ADMIN_INVOKABLE`; whitelist-валидация params → 404/422; simulate_wave делегирует в `incidents.simulate_wave`.
- Схемы Pydantic из T006 готовы — изменений нет; новых зависимостей нет.
- Тесты: conftest + `test_metrics.py` + `test_admin_panel.py`, LLM/httpx моки по существующим паттернам.
- T046: README + 4 файла `.ai/` (densecode, новые строки English); T047: чек-лист §7 + `pytest -v` green.
- Главные риски: R1 миграция tools.py (`dispatch`→`call_tool`) должна быть завершена к старту; R2 семантика auto_closed_pct (фиксируется тестом); R6 ручной прогон simulate_wave требует LLM-ключ.
