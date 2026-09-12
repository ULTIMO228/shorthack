# SESSION STATE (Shorthack) — актуальное состояние

> Обновлять В КОНЦЕ каждой сессии. Формат: что сделано / что горит / что дальше.

## Последнее обновление: 2026-09-12T15:39:00+03:00

## Готово ✅
- [x] Фича 001 Ph1+Ph2: каркас ядра (db/models/schemas/llm/auth/events/seed/routers auth+internal, T001-T012)
- [x] Фича 001 Ph3 (US1, T013-T021): агент разбора обращения на LangGraph
  - agent.py: ноды normalize/split/classify/triggers/check_duplicate/ensure_ticket/execute_route/escalate/respond + StateGraph (conditional edges по route, лимит tool_calls=3 FR-021)
  - triggers.py: правила форс-эскалации (жалобы/юр.), повышение приоритета без понижения (FR-011), порог confidence 0.6 (FR-013)
  - tickets.py: статус-цикл FR-015, дедуп FR-014 (service+category, гость по session_id), номер SUP-2026-<id:04d>
  - routers/requests.py: POST /api/requests, /reply (≤2 раундов→409), GET /api/requests (401 гостю), GET /api/requests/{id} (403 чужое)
  - маскирование мата ru+en, детект языка, перевод не-ru через llm
- [x] Тесты 001: 87 passed (test_agent.py 13, test_triggers.py 12 + старые)
- [x] E2E-smoke uvicorn: сценарии 1, 2(safe-route), 6 quickstart'а зелёные
- [x] **Фича 002 — веб-интерфейс на Next.js 16 + React 19 + TS (пивот со Vite по указанию заказчика)**: T001-T019
  - Каркас: app/layout.tsx + components/shell.tsx (шапка по роли, mobile-nav), lib/api.ts (cookie+{detail}), прокси /api→:8000 в next.config.ts
  - Дизайн: порт референса Figma Make (лого МИСИС public/misis-logo.png, Manrope/DM Mono, #0047FF, glass) → app/globals.css + токены статусов
  - Экраны: / (форма+результат+диалог, 8 примеров FR-014), /login, /cabinet (обращения+справки+цепочка статусов), /admin (очередь+баннер+StatusBoard), /admin/tickets/[id] (эскалация FR-031+действия), /admin/certs, /admin/tools (реестр+Wi-Fi toggle+волна+терминал), /admin/metrics
  - Polling 5 c (lib/use-polling.ts), тосты, модалка закрытия, KB-драфт, плашки outage/auth_required/cert_ordered/escalated
  - `npm run build` зелёный; живой smoke против ядра: страницы 200, прокси, login, POST /api/requests, reply — ок
- [x] **002-T021 — проход улучшений фронта (2026-09-12)**
  - UX: автоскролл диалога к последнему сообщению (+prefers-reduced-motion), пузырь «агент печатает» при reply, Ctrl/Cmd+Enter в композере, скролл-зона .conversation (max-height+overflow)
  - Корректность: фикс Link>button (плашка cert_ordered, баннер инцидента), PRIORITY_LABEL в очереди, «Загрузка диалога…»/ошибка в кабинете, Esc+role=dialog в модалке, время результата из ответа ядра, дедуп route_reason, fnRef-синк use-polling в useEffect
  - Проверено: `next build` зелёный; смоук через прокси :3000 (login → POST /api/requests → SUP-2026-0002 → reply 200 → список/деталь); смоук-данные из dev-БД вычищены
- [x] Дополнение контракта 001: status-board теперь с числовым `id` сервиса (нужен для PATCH /api/admin/services/{id})

- [x] **Фича 001 Ph5 (US3, T028-T031): RAG-агент по БЗ (документы и регламенты)**:
  - kb.py: `RetrievalHit`, `retrieve()` top-3 по косинусу (порог 0.5), keyword-деградация (min_score=2 для многословных запросов отсекает ложные срабатывания, stem_len=5), фильтр `kind="document"` (шаблоны не утекают в выдачу), `index_article()`
  - tools.py: `kb_agent` (type="llm", гейт на исходный вопрос, объединение источников по подзапросам, сборка ответа строго по источникам, fallback при недоступности LLM)
  - agent.py: `_execute_kb` в маршруте `kb`, фиксация счётчика `tool_calls` и причины в `route_reason` при эскалации (FR-021/FR-032)
  - Тесты: 19 тестов в `test_kb.py` зелёные (включая hardening: исключение шаблонов, гостевой доступ FR-016). Весь набор: **173 passed, 0 failed**

- [x] **Фича 001 Ph6 (US4, T032–T035): Заказ справок**:
  - certs.py: каталог 5 справок (FR-041), цепочка 4 статусов (FR-042), `can_transition`, `set_status` (с записью `cert_status_change` в журнал), `create_order`, `resolve_cert_type`, `get_regulation`, инструмент `order_certificate` (type="exec")
  - tools.py: регистрация `order_certificate` в реестре `TOOLS`
  - routers/certs.py: `GET /api/certs/catalog` (публичный), `POST /api/certs/orders` (авторизованный, 401 гостю), `GET /api/certs/orders` (мои заказы desc)
  - routers/admin.py: `GET /api/admin/certs/orders` (список с профилями), `PATCH /api/admin/certs/orders/{order_id}` (смена статуса по цепочке, 409 при нарушении)
  - agent.py: нода `node_cert_order` (гость → `auth_required`, детерминированный подбор типа, неясный тип → `clarification`, вызов инструмента `order_certificate` → реакция `cert_ordered` с регламентом, тикет «решена»), ребро в графе
  - main.py: подключён `certs_router`
- [x] **Фича 001 Ph8 (US6, T039–T041): Детектор массовых сбоев и массовый ответ**:
  - incidents.py: скользящее окно 15 мин (DETECTION_WINDOW_MIN=15, threshold=3 по distinct-авторам), создание активного инцидента + синхронное уведомление дежурному в outbound_messages (FR-051), привязка тикетов волны link_wave_tickets (FR-050), привязка новых обращений без повторного уведомления с шаблоном без LLM (FR-052), поднятие приоритета до high (FR-011) с маркером в route_reason (FR-012), broadcast по авторам (FR-053), resolve (409 при закрытом)
  - agent.py: нода incident_check после ensure_ticket → привязан/создан → outage_notice, тикет «решена», без вызова автодиагностики/LLM (экономия лимита FR-021)
  - tools.py: инструмент simulate_wave (type="exec"), регистрация в TOOLS
  - routers/admin.py: GET /api/admin/incidents (active первыми), POST /api/admin/incidents/{id}/broadcast (шаблон из БЗ или custom text), POST /api/admin/incidents/{id}/resolve (409)
  - Тесты: 21 тест в tests/test_incidents.py зелёный (включая сценарий 7 quickstart.md, distinct authors, sliding window, broadcast, resolve, outbound poll/ack, 401/403/404/409)
  - Регрессия: 126 passed без единой ошибки

- [x] **Фича 001 Ph9 (US7, T042–T043): Самообучение базы знаний**:
  - `tools.py`: инструмент `draft_kb_article` (type="llm", регистрация в реестре `TOOLS`), сборка контекста из тикета (диалог, реакции, masked_text, summary), генерация структурированного черновика `KbDraftResult` (title, body, topic), graceful fallback при `LLMUnavailable` (шаблонный черновик без сбоя эскалации).
  - `kb.py`: функция `index_article(db, article)` (идемпотентная индексация отдельной статьи по эмбеддингу, fallback при недоступности API).
  - `routers/admin.py`: `POST /api/admin/escalations/{request_id}/close` (закрытие по цепочке «решена» → «закрыта», генерация черновика при `add_to_kb=true`, идемпотентное закрытие 200), `POST /api/admin/kb/articles/{article_id}/confirm` (подтверждение, индексация embedding, логирование в `events`).
  - Тесты: 15 тестов в `tests/test_kb_learning.py` (включая сценарий 8 quickstart.md, 401/403/404, идемпотентность, моки LLM/fallback) — 100% зелёные. Общий прогон backend: **234 passed, 0 failed**.

- [x] **Фича 001 Ph10 (Polish: метрики, админка, доки, T044–T047)**:
  - `metrics.py`: сбор 6 метрик качества FR-062 (`auto_closed_pct`, `avg_first_reaction_sec` через min(reaction_sent) лаг, `incidents_total`, `incidents_active`, `requests_total`, `escalations_open`) + фасад `collect_metrics(db)`.
  - `routers/admin.py`: `GET /api/admin/metrics`, `GET /api/admin/status-board` (сервисы + last_check), `PATCH /api/admin/services/{id}` (переключение emulated, 409 для real), `GET /api/admin/tools` (реестр инструментов с params), `POST /api/admin/tools/{name}/invoke` (строгая валидация params, 422, запуск проверок и симуляции волны).
  - `tools.py`: константа `ADMIN_INVOKABLE`, схема параметров `params` для админ-панели, устранение дублирования `subtask_id` в `call_tool`.
  - `incidents.py`: генератор волн `simulate_wave` (создание тестовых пользователей и тикетов, запуск детектора аварий).
  - Документация: `README.md`, `.ai/SESSION_STATE.md`, `.ai/PROJECT_MAP.md`, `.ai/STACK.md`, `.ai/SESSIONS.md`.
  - Тесты: 8 тестов в `test_metrics.py`, 11 тестов в `test_admin_panel.py`, фикс 11 тестов в `test_escalations.py`. Полный регрессионный прогон: **264 passed, 0 failed**.

## Горит 🔥 (блокеры/баги)
- (пусто)

## В работе 🛠
- (пусто)

## Следующие шаги (приоритет)
1. Полный демо-прогон 002-T020 (8 сценариев quickstart через фронтенд и curl)
2. Подготовка фичи 003 (Telegram-бот для студентов и операторов)

## Контекст для следующей сессии
- Запуск: `cd backend && .venv/bin/uvicorn app.main:app --reload` (:8000) + `cd frontend && npm run dev` (:3000)
- Тесты: `cd backend && .venv/bin/python -m pytest tests/ -v` (все 264 теста зелёные)
- Фронт — Next.js: сборка `npm run build`, иконки lucide-react; ТОЛЬКО client components ('use client') во views
- .venv бэкенда уже создан, НЕ коммитить
- dev-БД backend/shorthack.db засидирована; smoke-данные вычищены
- LESSONS.md читать ПЕРЕД правками — там 4 свежих ловушки
