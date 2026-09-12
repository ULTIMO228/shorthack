---
description: "Задачи реализации фичи 002 — веб-интерфейс помощника поддержки МИСИС"
---

# Tasks: Веб-интерфейс помощника поддержки МИСИС

**Input**: Design documents from `/specs/002-web-interface/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ui-map.md ✓ (+ contracts/api.md фичи 001)

**Tests**: ручной прогон quickstart + `npm run build` как чек (тестовая инфраструктура фронта в проекте отсутствует — не добавляем).

**Organization**: по user story из spec.md (US1-US7). Все задачи можно начинать по контракту API 001 против моков `api.js`, интеграция — по готовности эндпоинтов ядра (см. Dependencies).

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Заменить frontend/src/App.jsx (форма сокращателя) каркасом: state `session`/`view`/`selectedRequestId`, `GET /api/auth/me` при старте, переключение представлений, меню по роли
- [ ] T002 [P] Создать frontend/src/api.js: обёртка fetch (`credentials:"include"`, JSON, 401 → колбэк на login, ошибки → `error.detail`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Блокирует все user story**

- [ ] T003 [P] Создать frontend/src/components/Spinner.jsx: «Агент разбирает обращение…»
- [ ] T004 [P] Создать frontend/src/components/PriorityBadge.jsx и RouteBadge.jsx: значки приоритета (critical/high/medium/low) и маршрута (⚙/📖/📄/👤)
- [ ] T005 [P] Создать frontend/src/components/StatusBoard.jsx: лампочки сервисов из `GET /api/admin/status-board` (emulated down → красная независимо от last_check), polling 5 c, cleanup
- [ ] T006 Базовые стили в frontend/src/App.css: badges, плашки (в т.ч. красная outage), таблицы, лампочки, карточки метрик — в тёмной теме scaffold

**Checkpoint**: каркас рендерится, компоненты доступны всем представлениям.

---

## Phase 3: User Story 1 - Вход в начале работы (Priority: P1) 🎯 MVP

**Goal**: экран входа до всего остального; email с доменом МИСИС → профиль; сессия переживает F5; чужой домен → ошибка.

**Independent Test**: quickstart 002 сценарий 1.

### Implementation for User Story 1

- [ ] T007 [US1] Создать frontend/src/views/LoginView.jsx: форма email → `POST /api/auth/login`; ошибка 400 (домен) под полем; успех → session в App, переход на `request` (оператор → `admin.queue`); logout-кнопка в шапке App

**Checkpoint**: вход/выход/F5 работают против живого ядра.

---

## Phase 4: User Story 2 - Подача обращения и диалог (Priority: P1)

**Goal**: форма с кнопками примеров, результат с badges, диалог уточнений в том же окне, красная плашка при сбое.

**Independent Test**: quickstart 002 сценарии 2-3.

### Implementation for User Story 2

- [ ] T008 [US2] Создать frontend/src/views/RequestView.jsx: textarea + `POST /api/requests`; 8 кнопок примеров (FR-014, захардкожены); Spinner на время запроса
- [ ] T009 [US2] В RequestView.jsx — блок результата: подзадачи с PriorityBadge/RouteBadge + `route_reason`, номер тикета SUP-2026-*, статус
- [ ] T010 [US2] В RequestView.jsx — диалог: лента сообщений, ввод при `ticket.status==="ждёт ответа пользователя"` → `POST /api/requests/{id}/reply`; `reaction.kind==="outage_notice"` → красная плашка; `cert_ordered` → карточка-ссылка в кабинет; 409 → блокировка ввода с текстом (зависит T008)

**Checkpoint**: полный цикл обращения в браузере.

---

## Phase 5: User Story 3 - Личный кабинет (Priority: P2)

**Goal**: мои обращения со статусами; каталог 5 справок с заказом; мои заказы с цепочкой статусов; обновление без F5.

**Independent Test**: quickstart 002 сценарий 4.

### Implementation for User Story 3

- [ ] T011 [P] [US3] Создать frontend/src/views/CabinetView.jsx: раздел «Мои обращения» (`GET /api/requests`, polling 5 c): номер, дата, статус, summary; клик → детали (диалог read-only)
- [ ] T012 [US3] В CabinetView.jsx — раздел «Справки»: каталог (`GET /api/certs/catalog`) с кнопкой заказа (`POST /api/certs/orders`); «Мои заказы» (`GET /api/certs/orders`) с цепочкой-прогрессом 4 статусов (зависит T011)

**Checkpoint**: заказ из каталога появляется в списке; смена статуса оператором доезжает поллингом.

---

## Phase 6: User Story 4 - Админка оператора (Priority: P2)

**Goal**: очередь с баннером инцидента; карточка эскалации по блокам FR-031; действия: закрытие ± в БЗ, подтверждение черновика, массовый ответ.

**Independent Test**: quickstart 002 сценарии 5-6.

### Implementation for User Story 4

- [ ] T013 [US4] Создать frontend/src/views/AdminQueueView.jsx: таблица очереди (`GET /api/admin/queue`, polling 5 c) с badges; баннер при активном инциденте (`GET /api/admin/incidents`); встроенный StatusBoard; клик → `admin.ticket`
- [ ] T014 [US4] Создать frontend/src/views/AdminTicketView.jsx: блоки саммари → проверки → рекомендация → почему эскалировано → исходник+перевод → журнал (свёрнут); читается ≤30 c
- [ ] T015 [US4] В AdminTicketView.jsx — действия: форма закрытия (`resolution`, чекбокс «в базу знаний») → `POST .../close`; показ `kb_draft` с кнопкой «Подтвердить» (`POST /api/admin/kb/articles/{id}/confirm`); при активном инциденте — «Уведомить затронутых» (`POST .../broadcast`) и «Инцидент решён» (`POST .../resolve`) (зависит T014)

**Checkpoint**: оператор ведёт эскалацию от очереди до закрытия без Postman.

---

## Phase 7: User Story 5 - Админка: заказы справок (Priority: P2)

**Goal**: все заказы, смена статуса по цепочке, пользователь видит обновление.

**Independent Test**: quickstart 002 сценарий 4 (вторая половина).

### Implementation for User Story 5

- [ ] T016 [US5] Создать frontend/src/views/AdminCertsView.jsx: таблица (`GET /api/admin/certs/orders`): тип, заявитель, группа, время, статус; кнопка «Следующий статус» → `PATCH /api/admin/certs/orders/{id}`; 409 → тост с `detail`; polling 5 c

**Checkpoint**: цепочка «не обработана → … → забрана» проходится кнопками.

---

## Phase 8: User Story 6 - Тест-панель инструментов (Priority: P3)

**Goal**: запуск exec-инструментов с выводом, переключатели Wi-Fi, «волна жалоб» — демо-пульт.

**Independent Test**: quickstart 002 сценарии 3, 6 (части с панелью).

### Implementation for User Story 6

- [ ] T017 [US6] Создать frontend/src/views/AdminToolsView.jsx: `GET /api/admin/tools` → карточки инструментов с формой по `params[]` → `POST /api/admin/tools/{name}/invoke`, вывод JSON-результата; блок переключателей Wi-Fi (`PATCH /api/admin/services/{id}`) со StatusBoard; кнопка «Волна жалоб» = invoke `simulate_wave {service, count:3}`

**Checkpoint**: все демо-сценарии запускаются с панели в один клик.

---

## Phase 9: User Story 7 - Метрики (Priority: P3)

**Goal**: виджет метрик для питча.

**Independent Test**: quickstart 002 сценарий 6 (метрики обновились после серии обращений).

### Implementation for User Story 7

- [ ] T018 [US7] Создать frontend/src/views/AdminMetricsView.jsx: `GET /api/admin/metrics` (polling 5 c): карточки % автоматизации, среднее время реакции, инциденты (active/total), счётчики обращений/эскалаций

**Checkpoint**: цифры сходятся с журналом.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [ ] T019 Проверка прав: пункты admin:* скрыты при `role!=="operator"`; прямой переход → 403 от ядра с русским текстом; состояния загрузки/пустоты всех списков
- [ ] T020 `npm run build` без ошибок + полный прогон демо-скрипта из contracts/ui-map.md (6 шагов) + quickstart.md (7 сценариев)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Ph1 → Ph2** → блокирует всё; **US1 (Ph3)** первой (нужна session-логика App)
- **US2 (Ph4)** после US1 (view `request` — основной экран)
- **US3-US7**: после Ph2; между собой параллельны (все — разные файлы views/*.jsx)
- **Polish** — в конце

### Зависимости от ядра (001)

- Можно начинать СРАЗУ по contracts/api.md против моков `api.js` (intercept-заглушки) — UI готов параллельно с 001
- Интеграция с живым ядром: US1 ждёт 001-T008; US2 — 001-T021; US3 — 001-T021+T034; US4 — 001-T037/T038/T041/T043; US5 — 001-T034; US6 — 001-T045; US7 — 001-T044

### Parallel Opportunities

- Ph2: T003/T004/T005 параллельно (разные компоненты)
- US3 || US5 || US6 || US7 — разные файлы, после Ph2 идут одновременно

### Parallel Example

```bash
Task: "Spinner.jsx" (T003) || Task: "PriorityBadge/RouteBadge.jsx" (T004) || Task: "StatusBoard.jsx" (T005)
Task: "AdminCertsView.jsx" (T016) || Task: "AdminToolsView.jsx" (T017) || Task: "AdminMetricsView.jsx" (T018)
```

---

## Implementation Strategy

### MVP First

Ph1+Ph2 → US1 (вход) → US2 (форма+диалог) → **STOP**: основной демо-экран жив. Затем US4 (админка) — вторая половина демо.

### Incremental Delivery

US1 → US2 → US4 (ядро демо) → US3+US5 (справки end-to-end) → US6 (демо-пульт) → US7 (метрики) → Polish. Каждый шаг проверяется сценарием quickstart.
