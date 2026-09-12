---
description: "Задачи реализации фичи 003 — Telegram-бот поддержки МИСИС"
---

# Tasks: Telegram-бот поддержки МИСИС

**Input**: Design documents from `/specs/003-telegram-bot/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/bot-contract.md ✓ (+ contracts/api.md фичи 001)

**Tests**: включены (юнит-тесты handlers без сети, pytest; проектная конвенция).

**Organization**: по user story из spec.md (US1-US5). Пакет самодостаточен для отдельного разработчика: контракт сообщений — `contracts/bot-contract.md`, вызовы ядра — `specs/001-misis-support-assistant/contracts/api.md`. Код ядра НЕ импортируем, только HTTP.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 Создать backend/app/bot/main.py: точка входа `python -m app.bot.main`; цикл long polling (offset в памяти + файл backend/.bot_offset), backoff 1→60 c при сетевых ошибках, лог старта (`polling as @<username>`)
- [x] T002 [P] Создать backend/app/bot/tg.py: клиент Bot API — `getUpdates(timeout=30, allowed_updates=["message","callback_query"])`, `sendMessage`, `editMessageText`, `answerCallbackQuery`; обработка 429 (`retry_after`); разбиение текста >4096 по абзацам с суффиксом «(продолжение)»
- [x] T003 [P] Создать backend/app/bot/core_api.py: клиент REST ядра — cookie-сессии per chat_id (`POST /api/auth/login` после привязки), вызовы `/api/requests*`, `/api/certs/*`, `/api/internal/*` с заголовком `X-Bot-Token`; `CORE_API_URL` из env
- [x] T004 [P] Создать backend/app/bot/messages.py: ВСЕ тексты T1-T15 verbatim из contracts/bot-contract.md §2 (константы; правки текстов — только здесь)
- [x] T005 [P] Создать backend/app/bot/keyboards.py: inline-клавиатуры K1 (каталог справок, `cert:<type>` + `cert:my`) и K2 (`cert_order:<type>` / `noop`) из contracts/bot-contract.md §3

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Блокирует все user story**

- [x] T006 State machine в backend/app/bot/handlers.py + main.py: `GET /api/internal/tg/link?chat_id=` на каждый апдейт → маршрутизация по `state` (awaiting_email/awaiting_confirm/idle/dialog:<request_id>); диспетчер: команды (`/start /help /status /certs /cancel`) vs текст vs callback_query; фильтр `chat.type=="private"` (зависит T001-T005)

**Checkpoint**: бот стартует, `/start` и `/help` отвечают (T1/T2) без ядровой логики обращений.

---

## Phase 3: User Story 1 - Обращение через бота (Priority: P1) 🎯 MVP

**Goal**: текст → T9 → конвейер ядра → реакции в чат (outage_notice 🔴 / clarification / answer / cert_ordered / escalated), префикс дубля, подзадачи i/n.

**Independent Test**: чек-лист bot-contract.md §6 п.2; обращение видно в админке с каналом telegram.

### Implementation for User Story 1

- [x] T007 [US1] В backend/app/bot/handlers.py — сценарий S2: текст в `idle` → T9 → `POST /api/requests {"text", "channel":"telegram"}` → маппинг `reactions[]` по kind (T10/T11/T14/текст как есть), `duplicate=true` → префикс «похоже на вашу заявку {number}», несколько подзадач → сообщения с префиксом `{i}/{n}:`; `clarification` → state=`dialog:<request_id>` (зависит T006)

**Checkpoint**: полное обращение проходит в чате.

---

## Phase 4: User Story 2 - Привязка профиля по почте (Priority: P1)

**Goal**: сценарий S1: email → код → привязка; сессия ядра per chat; 3 попытки кода; команды до привязки ограничены.

**Independent Test**: чек-лист §6 п.1 (полный цикл T3→T6).

### Implementation for User Story 2

- [x] T008 [US2] В backend/app/bot/handlers.py — сценарий S1: state `awaiting_email` (валидация домена @misis.ru/@edu.misis.ru → `POST /api/internal/tg/link` → T4; иначе T5) → state `awaiting_confirm` (`POST /api/internal/tg/link/confirm`: ok → T6 + `core_api.login(email)`; неверный → T7 с счётчиком; attempts_exceeded → T8 + state=`awaiting_email`); `/status`/`/certs` до привязки → просьба привязаться (зависит T006)

**Checkpoint**: новый пользователь привязывается, обращения идут от его профиля.

---

## Phase 5: User Story 3 - Уточняющий диалог (Priority: P2)

**Goal**: контекст диалога до завершения: ответы → `/reply`, финальная реакция → `idle`; `/cancel`; 409 → сброс.

**Independent Test**: чек-лист §6 п.3 (2 раунда без повтора проблемы).

### Implementation for User Story 3

- [x] T009 [US3] В backend/app/bot/handlers.py — сценарий S2b: state `dialog:<request_id>` → текст → `POST /api/requests/{id}/reply` → реакции как в S2; не-clarification → state=`idle`; `/cancel` → state=`idle` + «Текущее действие отменено»; 409 → state=`idle` + сообщение о завершении диалога (зависит T007)

**Checkpoint**: диалог из 2 раундов проходит без повторного описания.

---

## Phase 6: User Story 4 - Справки и статус через бота (Priority: P2)

**Goal**: `/certs` — каталог кнопками K1, подтверждение K2, заказ, мои заказы со статусами; `/status` — мои обращения с номерами и пометкой инцидента.

**Independent Test**: чек-лист §6 п.4-5; заказ виден в админке 002 и кабинете.

### Implementation for User Story 4

- [x] T010 [US4] В backend/app/bot/handlers.py — сценарий S4: `/certs` → каталог (`GET /api/certs/catalog`) сообщением с K1 + «Ваши заказы» (`GET /api/certs/orders`); callback `cert:<type>` → K2; `cert_order:<type>` → `POST /api/certs/orders` → T14 через `editMessageText`; `cert:my` → обновить список заказов; `/status` (S3): `GET /api/requests` → T12 или до 10 строк `{number} · {дата} · {status} · {summary}` + строка 🔴 при инциденте; `answerCallbackQuery` на все callback (зависит T006)

**Checkpoint**: заказ справки кнопками, паритет каналов (бот/сайт/админка).

---

## Phase 7: User Story 5 - Уведомления дежурному (Priority: P3)

**Goal**: цикл 10 c: pending → sendMessage → ack; инцидент ≤ 1 мин.

**Independent Test**: чек-лист §6 п.6 (simulate_wave → сообщение дежурному).

### Implementation for User Story 5

- [x] T011 [US5] В backend/app/bot/main.py — параллельный цикл outbox: каждые 10 c `GET /api/internal/outbound?status=pending` → `sendMessage` по `chat_id` записи (дежурный = `TG_DUTY_CHAT_ID`) → `POST /api/internal/outbound/{id}/ack {"status":"sent"}`; ошибка отправки → ack `failed` + лог; тексты не изменять (зависит T001, T002, T003)

**Checkpoint**: инцидент доезжает дежурному ≤ 1 мин; ack-статусы корректны.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [x] T012 [P] Создать backend/tests/test_bot_handlers.py: юнит-тесты без сети (моки tg.py/core_api.py) — разбор команд, переходы автомата (привязка, диалог, /cancel), форматирование /status и /certs, разбиение >4096, префиксы i/n и дубля
- [x] T013 Границы по контракту §5: фото/голос/файлы → «только текст»; группы → молчание; ядро недоступно → T13 без ретрая; рестарт бота → offset + перелогин прозрачно
- [x] T014 Полный прогон приёмочного чек-листа contracts/bot-contract.md §6 (7 пунктов) + quickstart.md (7 сценариев)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Ph1 → Ph2** → блокирует всё
- **US1 (Ph3)** и **US2 (Ph4)**: обе P1; US2 логически первая (без привязки обращения анонимны) — рекомендуемый порядок T008 → T007, но файлово совместимы (handlers.py правится последовательно, задачи одному человеку)
- **US3 (Ph5)**: после US1 (состояние dialog появляется из реакции clarification)
- **US4 (Ph6)**: после Ph2+US2 (нужна привязка); независима от US3
- **US5 (Ph7)**: после Ph1 (main.py); от ядра ждёт только `/api/internal/outbound*` (001-T041)
- **Polish (Ph8)**: в конце

### Зависимости от ядра (001)

- Разработку вести против контракта api.md сразу; интеграция: US2 ждёт 001-T012 (tg/link*); US1 — 001-T021 (requests); US4 — 001-T034 (certs); US5 — 001-T041 (outbound+ack)
- Если ядро не готово к интеграции: core_api.py допускает заглушки-записанные ответы (только для разработки handlers, не коммитить)

### Parallel Opportunities

- Ph1: T002/T003/T004/T005 — все параллельно (разные файлы)
- T012 (тесты) — параллельно с US4/US5
- Вся фича 003 идёт ПАРАЛЛЕЛЬНО фичам 001/002 (другой разработчик, связность только через contracts/api.md)

### Parallel Example

```bash
Task: "tg.py" (T002) || Task: "core_api.py" (T003) || Task: "messages.py" (T004) || Task: "keyboards.py" (T005)
```

---

## Implementation Strategy

### MVP First

Ph1+Ph2 → US2 (привязка) → US1 (обращение) → **STOP**: бот принимает и обрабатывает обращения — основной демо-поток с телефона.

### Incremental Delivery

US2 → US1 → US3 (диалог) → US4 (справки/статус) → US5 (дежурный) → Polish. Каждый шаг — пункт приёмочного чек-листа bot-contract.md §6.
