---
description: "Задачи реализации фичи 001 — ядро ИИ-помощника поддержки МИСИС"
---

# Tasks: ИИ-помощник технической поддержки МИСИС — ядро

**Input**: Design documents from `/specs/001-misis-support-assistant/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/api.md ✓

**Tests**: включены (проект на pytest; LLM везде мокается — реальных вызовов в тестах нет).

**Organization**: по user story из spec.md (US1-US7).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: параллельно с другими [P] (разные файлы, без незакрытых зависимостей)
- **[Story]**: US1..US7 из spec.md

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Добавить `langgraph>=1.1,<2` в backend/requirements.txt и установить в backend/.venv
- [X] T002 [P] Создать backend/.env.example: `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`, `YANDEX_MODEL_CLASSIFY`, `YANDEX_MODEL_GENERATE`, `BOT_INTERNAL_TOKEN`, `CORE_API_URL` (значения-плейсхолдеры, без секретов)
- [X] T003 Удалить код сокращателя ссылок из backend/app/main.py (эндпоинты `/api/shorten`, `/{code}`, модель Link) и старые тесты из backend/tests/test_api.py — оставить каркас приложения

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Блокирует все user story**

- [X] T004 [P] Создать backend/app/db.py: engine SQLite, Base, `get_session()` через Depends+yield (LESSONS L002)
- [X] T005 Создать backend/app/models.py: все таблицы по data-model.md (users, sessions, requests, subtasks, tickets, services, service_checks, kb_articles, cert_orders, incidents, events, tg_links, outbound_messages) (зависит T004)
- [X] T006 [P] Создать backend/app/schemas.py: DTO всех эндпоинтов из contracts/api.md + Pydantic-схема JSON-ответа классификатора (route/service/category/priority/confidence/reason)
- [X] T007 Создать backend/app/llm.py: клиент Yandex AI Studio — `chat()` и `embed()` через httpx (`Authorization: Api-Key`, `OpenAI-Project`, таймаут 25 c), JSON-парсинг ответа + Pydantic-валидация + 1 retry, исключение `LLMUnavailable` (зависит T006)
- [X] T008 [P] Создать backend/app/auth.py + backend/app/routers/auth.py: `POST /api/auth/login` (домен @misis.ru/@edu.misis.ru, автосоздание пользователя, cookie `session_id` HttpOnly), `POST /api/auth/logout`, `GET /api/auth/me` (200 `{user}` или `{user:null, guest:true}`); гостевые сессии: авто-создание анонимной сессии при первом обращении без логина, привязка гостевой сессии к user_id при логине (диалог сохраняется); dependency `current_session`/`current_user` (зависит T005)
- [X] T009 [P] Создать backend/app/events.py: helper `log_event(ticket_id, actor, action, payload)` — журнал FR-061 (зависит T005)
- [X] T010 Создать backend/app/seed.py: 5 пользователей (3 студента, сотрудник, оператор), 5 сервисов (misis.ru/newlms real, 3 Wi-Fi emulated), документы и шаблоны БЗ по FR-030 + индексация embeddings при старте (идемпотентно; при недоступности API — keyword-деградация) (зависит T005, T007)
- [X] T011 Собрать backend/app/main.py: FastAPI, include_router всех роутеров, startup → `create_all` + `seed()` (зависит T004-T010)
- [X] T012 [P] Создать backend/app/routers/internal.py: `GET/POST /api/internal/tg/link`, `POST /api/internal/tg/link/confirm` (код, 3 попытки), заготовка `GET /api/internal/outbound`, `POST /api/internal/outbound/{id}/ack` — заголовок `X-Bot-Token` (зависит T005, T008)

**Checkpoint**: `uvicorn app.main:app` стартует, сидирование отработало, `/api/health` и логин отвечают.

---

## Phase 3: User Story 1 - Разбор и маршрутизация обращения (Priority: P1) 🎯 MVP

**Goal**: обращение → нормализация (мат/язык) → сплиттер → классификатор (триггеры, confidence, приоритет-арбитраж) → маршрут + обоснование → реакция; дедупликация; статус-цикл заявки.

**Independent Test**: `POST /api/requests` тремя контрольными текстами (простое/составное/мат+англ.) → корректные подзадачи, маршруты, приоритеты, обоснования; повтор → `duplicate=true` (quickstart 001, сценарии 1-2, 6).

### Tests for User Story 1

- [X] T013 [P] [US1] Создать backend/tests/test_agent.py: пайплайн с моком `llm.chat` — маршруты, сплиттер на 3 вопроса, confidence < 0.6 → escalate, safe-route при LLMUnavailable
- [X] T014 [P] [US1] Создать backend/tests/test_triggers.py: форс-эскалация по триггеру, повышение приоритета правилами, запрет понижения

### Implementation for User Story 1

- [X] T015 [US1] Нода normalize в backend/app/agent.py: маскирование мата (словарь + `*`), определение языка, перевод через llm для не-ru
- [X] T016 [US1] Нода split в backend/app/agent.py: разбиение составного обращения на подзадачи (LLM → JSON-список)
- [X] T017 [US1] Нода classify в backend/app/agent.py: промпт классификатора (маршруты auto_check/kb/cert_order/escalate + service/category/priority/confidence/reason), валидация схемой из T006
- [X] T018 [P] [US1] Создать backend/app/triggers.py: настраиваемые правила (условие → форс-маршрут/эскалация; повышение приоритета без понижения, FR-011/FR-013)
- [X] T019 [P] [US1] Создать backend/app/tickets.py: жизненный цикл FR-015, дедупликация FR-014 (совпадение service+category по открытой заявке пользователя), номер `SUP-2026-<id>`
- [X] T020 [US1] Сборка StateGraph в backend/app/agent.py: ноды normalize→split→classify→triggers→execute_route→respond, conditional edges по route, счётчик tool_calls с лимитом 3 (FR-021) (зависит T015-T019)
- [X] T021 [US1] Создать backend/app/routers/requests.py: `POST /api/requests` (доступно гостю — авто-создание гостевой сессии; синхронный прогон графа, ответ по contracts/api.md), `POST /api/requests/{id}/reply` (автор или гостевая сессия-автор; диалог, ≤2 раундов FR-025, 409), `GET /api/requests` (только авторизованный, гостю 401 с предложением войти), `GET /api/requests/{id}` (автор/гостевая сессия/оператор, 403 чужое) (зависит T020)

**Checkpoint**: сценарии 1-2, 6 quickstart'а зелёные; тесты T013/T014 green.

---

## Phase 4: User Story 2 - Автодиагностика сервисов (Priority: P1)

**Goal**: агент сам вызывает проверки (misis.ru, newlms.misis.ru реально; Wi-Fi — эмуляция), ветки «норма → уточняющий вопрос» / «сбой → мгновенное извещение без LLM», запись service_checks, приоритет critical при сбое.

**Independent Test**: жалоба на сайт при живом misis.ru → clarification; MISIS-EDU в «сбой» → outage_notice ≤ 2 c (quickstart 001, сценарии 2-3).

### Tests for User Story 2

- [X] T022 [P] [US2] Создать backend/tests/test_tools.py: мок httpx — 200/301/500/таймаут для check_site (критерий 2xx/3xx ≤5 c), check_wifi по состоянию services, ветка сбоя без вызова LLM

### Implementation for User Story 2

- [X] T023 [US2] Создать backend/app/tools.py: реестр инструментов `{name: {type: exec|llm, fn, schema}}` + диспетчер вызова (журнал в events, инкремент tool_calls)
- [X] T024 [P] [US2] Реализовать `check_site` и `check_lms` в backend/app/tools.py: httpx GET, критерий доступности FR-022/023 (зависит T023)
- [X] T025 [P] [US2] Реализовать `check_wifi` в backend/app/tools.py: чтение эмулируемого состояния services (зависит T023)
- [X] T026 [US2] Нода execute_route для auto_check в backend/app/agent.py: вызов проверки → запись service_checks → шаблонный уточняющий вопрос (норма) или шаблонное извещение outage_notice без LLM (сбой) (зависит T020, T023-T025)
- [X] T027 [US2] Правило «подтверждённый сбой → priority critical» в backend/app/triggers.py (зависит T018, T026)

**Checkpoint**: сценарий 3 quickstart'а зелёный; T022 green.

---

## Phase 5: User Story 3 - RAG-агент по документам и шаблонам (Priority: P2)

**Goal**: переформулировка запроса, подзапросы, retrieval top-3 по эмбеддингам (fallback keyword), ответ строго по источникам, пустая выдача → эскалация.

**Independent Test**: вопрос по регламенту Wi-Fi → answer из документа; вопрос вне базы → escalated (quickstart 001, сценарий 4).

### Tests for User Story 3

- [X] T028 [P] [US3] Создать backend/tests/test_kb.py: косинус-retrieval с моком embed, порог 0.5, keyword-fallback, пустая выдача → маршрут escalate

### Implementation for User Story 3

- [X] T029 [US3] Создать backend/app/kb.py: индексация (embed при сиде/подтверждении), retrieval top-3 косинус (чистый Python), порог, keyword-деградация (зависит T007)
- [X] T030 [US3] Инструмент `kb_agent` (type llm) в backend/app/tools.py: переформулировка запроса, разделение на подзапросы, сборка финального ответа по источникам, отказ при пустоте (зависит T023, T029)
- [X] T031 [US3] Нода execute_route для kb в backend/app/agent.py (зависит T020, T030)

**Checkpoint**: сценарий 4 quickstart'а зелёный; T028 green.

---

## Phase 6: User Story 4 - Заказ справок (Priority: P2)

**Goal**: каталог 5 справок, заказ как действие агента с профилем из сессии, статусная цепочка из 4 статусов, admin-смена статусов.

**Independent Test**: заказ справки с места учёбы → «не обработана»; PATCH оператором → новый статус виден пользователю (quickstart 001, сценарий 5).

### Tests for User Story 4

- [X] T032 [P] [US4] Создать backend/tests/test_certs.py: создание заказа, цепочка статусов, 409 на обратный/скачкообразный переход, заказ без авторизации → 401

### Implementation for User Story 4

- [X] T033 [US4] Создать backend/app/certs.py: каталог FR-041 (5 типов с описаниями), инструмент `order_certificate` (exec), создание заказа из профиля сессии (зависит T023)
- [X] T034 [US4] Роутеры backend/app/routers/certs.py (`GET /api/certs/catalog`, `POST /api/certs/orders`, `GET /api/certs/orders`) и admin-часть в backend/app/routers/admin.py (`GET /api/admin/certs/orders`, `PATCH /api/admin/certs/orders/{id}` с проверкой цепочки) (зависит T033)
- [X] T035 [US4] Нода execute_route для cert_order в backend/app/agent.py: регламент из БЗ → заказ → реакция cert_ordered; гость без user_id → реакция `auth_required` («войдите по корпоративной почте МИСИС»), заказ НЕ создаётся (FR-016/FR-043) (зависит T020, T033)

**Checkpoint**: сценарий 5 quickstart'а зелёный; T032 green.

---

## Phase 7: User Story 5 - Эскалация оператору с саммари (Priority: P2)

**Goal**: пакет эскалации (саммари, проверки, рекомендация, why_escalated, исходник+перевод, журнал), очередь админки, закрытие; outbound-уведомление дежурному о новой эскалации.

**Independent Test**: эскалация вопросом вне базы → `GET /api/admin/escalations/{id}` содержит все блоки FR-060 (quickstart 001, сценарий 4b).

### Implementation for User Story 5

- [X] T036 [US5] Инструмент `summarize` (type llm) в backend/app/tools.py: саммари сути, сводка проверок, рекомендация, причина эскалации (зависит T023)
- [X] T037 [US5] Нода escalate в backend/app/agent.py: формирование пакета FR-060 + запись в outbound_messages дежурному (зависит T020, T036)
- [X] T038 [US5] В backend/app/routers/admin.py: `GET /api/admin/queue` (сортировка priority→created_at), `GET /api/admin/escalations/{request_id}`, `POST /api/admin/escalations/{request_id}/close` (resolution, без KB-черновика) (зависит T037)

**Checkpoint**: эскалация видна в очереди и открывается пакетом; закрытие меняет статус на «закрыта».

---

## Phase 8: User Story 6 - Детектор массовых сбоев и массовый ответ (Priority: P3)

**Goal**: ≥3 однотипных (service+category) от разных пользователей за 15 мин → инцидент + уведомление дежурных ≤ 1 мин; привязка новых обращений к активному инциденту; массовый ответ кнопкой.

**Independent Test**: simulate_wave ×3 → инцидент + outbound-запись; 4-я жалоба → шаблон без нового уведомления; broadcast → sent ≥ 3 (quickstart 001, сценарий 7).

### Tests for User Story 6

- [X] T039 [P] [US6] Создать backend/tests/test_incidents.py: детектор (окно, distinct users, порог), идемпотентность уведомления, привязка к активному инциденту, broadcast

### Implementation for User Story 6

- [X] T040 [US6] Создать backend/app/incidents.py: SQL-детектор (окно 15 мин настраиваемо), создание инцидента, outbound-уведомление, привязка новых обращений + шаблонное извещение (FR-052) (зависит T005, T020)
- [X] T041 [US6] В backend/app/routers/admin.py: `GET /api/admin/incidents`, `POST /api/admin/incidents/{id}/broadcast` (шаблон из БЗ или custom text), `POST /api/admin/incidents/{id}/resolve`; достроить `/api/internal/outbound*` в routers/internal.py (зависит T040)

**Checkpoint**: сценарий 7 quickstart'а зелёный; T039 green.

---

## Phase 9: User Story 7 - Самообучение базы знаний (Priority: P3)

**Goal**: закрытие эскалации с add_to_kb → LLM-черновик статьи → подтверждение оператором → индексация и доступность RAG-агенту.

**Independent Test**: закрыть эскалацию с add_to_kb → confirm → аналогичный вопрос отвечается из БЗ (quickstart 001, сценарий 8).

### Implementation for User Story 7

- [X] T042 [US7] Инструмент `draft_kb_article` (type llm) в backend/app/tools.py: черновик статьи (title/body/topic) по переписке и resolution (зависит T023)
- [X] T043 [US7] В backend/app/routers/admin.py + backend/app/kb.py: close с `add_to_kb=true` → kb_draft (confirmed=false); `POST /api/admin/kb/articles/{id}/confirm` → confirmed + индексация embedding (зависит T038, T042, T029)

**Checkpoint**: сценарий 8 quickstart'а зелёный.

---

## Phase 10: Polish & Cross-Cutting Concerns
 
- [X] T044 [P] Создать backend/app/metrics.py + `GET /api/admin/metrics` в routers/admin.py: % без человека, среднее время первой реакции, инциденты (из events, FR-062)
- [X] T045 [P] В backend/app/routers/admin.py: `GET /api/admin/status-board`, `PATCH /api/admin/services/{id}` (409 для real), `GET /api/admin/tools`, `POST /api/admin/tools/{name}/invoke` (check_site/check_lms/check_wifi/simulate_wave)
- [X] T046 [P] Обновить README.md (запуск нового стека), .ai/SESSION_STATE.md, .ai/PROJECT_MAP.md, .ai/STACK.md под новую архитектуру
- [X] T047 Прогон quickstart.md целиком (8 сценариев) + `pytest -v` green

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Ph1)** → **Foundational (Ph2)** → блокирует все story-фазы
- **US1 (Ph3)** → **US2 (Ph4)**: US2 встраивается в execute_route из US1 (T020) — строго последовательно
- **US3/US4/US5 (Ph5-7)**: после US1; между собой — параллельно (разные файлы: kb.py/certs.py/tools.py-инструменты; общий файл agent.py — ноды добавлять последовательно внутри команды)
- **US6 (Ph8)**: после US1; параллельно с US3-US5 (отдельный incidents.py)
- **US7 (Ph9)**: после US5 (close из T038)
- **Polish (Ph10)**: после нужных story

### Кросс-фичевая параллельность

- **Фича 002 (веб)** и **фича 003 (бот)**: стартуют немедленно по `contracts/api.md` (контракт заморожен) — верстать/писать против моков или поднятого каркаса ядра; интеграционные точки: 002 ждёт T008 (login), T021 (requests), T034 (certs); 003 ждёт T012 (internal link), T021, T034, T041 (outbound)
- Два человека: A — 001 (Ph1→Ph4), затем US3/US4; B — 003 полностью + 002 при наличии времени

### Parallel Opportunities

- Ph2: T004+T006, T008+T009+T012 — парами параллельно
- Тесты T013/T014/T022/T028/T032/T039 — все параллельно (разные файлы)
- US3-US5 между собой после US1

### Parallel Example: Phase 2

```bash
Task: "Создать backend/app/db.py" (T004) || Task: "Создать backend/app/schemas.py" (T006)
Task: "auth.py + routers/auth.py" (T008) || Task: "events.py" (T009) || Task: "routers/internal.py" (T012)
```

---

## Implementation Strategy

### MVP First

1. Ph1 + Ph2 → каркас стартует и сидируется
2. **US1 + US2 (обе P1)** → обязательный минимум хакатона (приём, LLM, действие) — STOP и проверка по quickstart сценариям 1-3
3. Демо-готовность уже здесь: ядро отвечает, проверки работают

### Incremental Delivery

US3 (RAG) → US4 (справки) → US5 (эскалации) → US6 (инциденты) → US7 (самообучение) → Polish; каждая — независимо проверяемый инкремент по quickstart'у.
