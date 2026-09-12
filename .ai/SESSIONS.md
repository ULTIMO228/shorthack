# SESSIONS LOG (append-only)

---

## 2026-09-12T10:12:00+03:00 | @kimi | branch:main | mode:scaffold
Focus: Создание проекта Shorthack с нуля + инфраструктура AI-агентов
Done: ✅
  - Скелет: FastAPI backend (shorten/redirect/stats) + React+Vite frontend
  - pytest 2/2 green (shorten+redirect+stats, 404)
  - README, .gitignore
  - Приватная репа github.com/ULTIMO228/shorthack, push main
  - .ai/: STACK, PROJECT_MAP, LESSONS (L001-L004), SESSIONS, SESSION_STATE, AGENTS
  - .agents/: index + backend-dev, frontend-dev, tester
Decisions: 🧠
  - SQLite на старте (не Postgres) — MVP не должен тащить инфраструктуру
  - Монолитный main.py до 3+ эндпоинтов, потом роутеры
  - Все комментарии и доки — на русском
Next:
  !1: FIX L001 — unified prefix `/s/` для short_url
  !2: Custom alias + collision handling
   3: Session cleanup (L002) через Depends
Caution:
  ⚠️ random.choices для кодов (L003) — ок для MVP, не для продакшена
  ⚠️ Vite proxy `/s` расходится с реальным `/{code}` — см. LESSONS L001
---

---

## 2026-09-12T14:40:00+03:00 | @kimi | branch:main | mode:speckit-implement
Focus: Фича 001 Ph3 — US1 разбор и маршрутизация обращения (T013-T021)
Done: ✅
  - backend/app/agent.py: LangGraph StateGraph (normalize→split→classify→triggers→check_duplicate→execute_route/escalate→respond), conditional edges по route, tool_calls лимит 3 (FR-021)
  - backend/app/triggers.py: форс-эскалация (жалобы/юр.), повышение приоритета без понижения (FR-011), confidence<0.6→escalate (FR-013)
  - backend/app/tickets.py: цикл FR-015, дедуп FR-014, SUP-2026-<id:04d>
  - backend/app/routers/requests.py: POST /api/requests, /reply (≤2 раундов, 409), GET list (401 гостю), GET detail (403 чужое)
  - tests/test_agent.py (13) + tests/test_triggers.py (12); полный прогон 87 passed
  - E2E-smoke uvicorn (сценарии 1, 2, 6 quickstart'а) — зелёные
Decisions: 🧠
  - Мок LLM в тестах диспетчеризуется по маркерам промптов («Переведи» / '"subtasks"' / классификатор)
  - auto_check в Ph3 без реальной проверки (US2/T026 добавит проверки+outage_notice); kb/cert_order у авторизованного → эскалация-заглушка до US3/US4
  - Мат маскируется целиком: первая буква + «*» (ru+en словари, lookbehind от «pass»/«ass»)
Next:
  !1: Ph4 US2 — tools.py check_site/check_lms/check_wifi + outage-ветка (T022-T027)
   2: Ph5 US3 RAG (kb.py, T028-T031)
Caution:
  ⚠️ login автосоздаёт только role=student — операторов в тестах создавать напрямую в БД
  ⚠️ без LLM-ключа service=null → дедуп не срабатывает (корректно, FR-014 нужен сервис)

---

## 2026-09-12T15:23:55+03:00 | @kimi | branch:main | mode:speckit-implement
Focus: Фича 002 — веб-интерфейс целиком, пивот на Next.js по указанию заказчика, дизайн из Figma Make референса
Done: ✅
  - Пивот стека: Vite-SCAFFOLD удалён → Next.js 16.3.5 (App Router) + React 19.3 + TS 7, lucide-react 1.45
  - Порт дизайна референса: globals.css (Manrope/DM Mono, #0047FF, glass topbar), лого public/misis-logo.png
  - 9 маршрутов: / login cabinet admin admin/tickets/[id] admin/certs admin/tools admin/metrics (+404)
  - Все задачи T001-T019 закрыты; T020 частично (build зелёный, сценарии 1-2 живьём, 3-7 ждут эндпоинты 001)
  - Контракт 001 дополнен: status-board + numeric id сервиса
  - Smoke: uvicorn+next start, прокси /api ок, login/requests/reply ок (LLM down → safe-route escalate)
Decisions: 🧠
  - App Router вместо state-view switching (заказчик попросил Next.js — роутер бесплатный)
  - route-значения фронта выровнены по schemas.py ядра: auto_check|kb|cert_order|escalate
  - Роль-гейт только по меню; прямой заход в /admin показывает 403/404 ядра русским текстом
Next:
  !1: Доделать 001 Ph4+ (tools/certs/admin эндпоинты) — без них демо-скрипт 002 не прогнать
  !2: После 001 — закрыть 002-T020 (полный прогон quickstart)
   3: Проверить Wi-Fi toggle против живого status-board (id сервиса)
Caution:
  ⚠️ Next 16 + TS 7 (tsgo) — при странных ошибках типов смотреть tsconfig (Next сам его правит)
  ⚠️ Не коммитить без явной просьбы; smoke-данные в shorthack.db после тестов почистить
---

## 2026-09-12T15:39:00+03:00 | @kimi | branch:main | mode:speckit-implement
Focus: Фича 002 — продолжение по фронту: проход UX/корректности поверх готового Next.js-интерфейса (T021)
Done: ✅
  - page.tsx: автоскролл диалога (respect reduced-motion), пузырь «агент печатает» при reply, Ctrl/Cmd+Enter в композере+подсказка, время результата фиксируется при ответе ядра, дедуп блока route_reason
  - Корректность: Link>button убран (cert_ordered + баннер инцидента → .notice-action/.incident-link), PRIORITY_LABEL в очереди, «Загрузка диалога…»+ошибка при раскрытии в кабинете, Esc+role=dialog в модалке закрытия, fnRef-синк use-polling → useEffect
  - globals.css: .conversation — скролл-зона (max-height:min(58vh,660px)+overflow), стили ссылок-кнопок, typing-точки
  - Проверка: `next build` зелёный; смоук через :3000 (login ivanov@misis.ru → POST /api/requests → SUP-2026-0002 → reply 200 → список/деталь ок); все 8 страниц 200; смоук-строки (req/ticket/subtasks/events id=2+22) из dev-БД вычищены
Decisions: 🧠
  - Скролл-контейнер — сам .conversation (input всегда виден на длинном диалоге)
  - Стиль ссылок-кнопок дублирует .notice button/.incident button (композиция без вложенности интерактива)
Next:
  !1: 001 Ph4+ (tools/certs/admin эндпоинты T022-T045) — без них демо-скрипт 002 не прогнать (queue/certs/metrics/status-board → 404)
  !2: После 001 — закрыть 002-T020 (полный прогон 6 шагов ui-map + 7 сценариев quickstart)
Caution:
  ⚠️ reply у эскалированного тикета пишет events: ticket-bound + NULL-ticket 'classified' — чистить ОБА при смоуке
  ⚠️ Не коммитить без явной просьбы
---

## 2026-09-12T15:42:00+03:00 | @antigravity | branch:main | mode:speckit-implement
Focus: Фича 001 Ph6 — US4 заказ справок (T032–T035)
Done: ✅
  - backend/app/certs.py: каталог CERT_CATALOG (5 типов FR-041), CERT_STATUSES (4 статуса), can_transition (строго соседние вперёд), set_status (с записью cert_status_change в журнал), create_order, resolve_cert_type (ключевые слова без LLM), get_regulation, order_certificate (type="exec")
  - backend/app/tools.py: зарегистрирован инструмент order_certificate в TOOLS
  - backend/app/routers/certs.py: GET /api/certs/catalog (публичный), POST /api/certs/orders (авторизованный, 401 гостю), GET /api/certs/orders (мои заказы desc, 401 гостю)
  - backend/app/routers/admin.py: GET /api/admin/certs/orders (список заказов с профилями), PATCH /api/admin/certs/orders/{order_id} (проверка цепочки, 409 при нарушении/скачке/повторе)
  - backend/app/agent.py: нода node_cert_order (гость → auth_required без создания заказа, неясный тип → clarification со списком каталога, успех → order_certificate + cert_ordered + статус «решена»), условное ребро в build_graph
  - backend/app/main.py: подключен certs_router
  - backend/tests/test_certs.py: 25 тестов (каталог, создание заказа, 401 гостю, 422 валидация, изоляция заказов пользователей, полный forward chain админки, 409 на откат/скачок/тот же статус, 404, 403 не-оператору, ключевые слова, can_transition, агентный заказ, auth_required агента, уточнение агента, сквозной сценарий 5 quickstart) — все зелёные
  - specs/001-misis-support-assistant/tasks.md: задачи T032–T035 отмечены [X]
Decisions: 🧠
  - Резолвинг типа справки в ноде детерминирован ключевыми словами CERT_TYPE_KEYWORDS без лишних LLM-вызовов (быстро, SC-007, надёжно тестируется)
  - Гость в agent.py направляется в node_escalate(state), переиспользуя ветку auth_required и не дублируя тексты
  - Фрагмент регламента добавляется в ответ агента при наличии документа в БЗ
Next:
  !1: Ph7 (US5 эскалации FR-060, T036–T038): пакет эскалации, summarize, очередь админки, закрытие
   2: Ph8 (US6 инциденты), Ph9 (US7 самообучение), Ph10 (polish)
Caution:
  ⚠️ В PATCH /api/admin/certs/orders/{order_id} тот же статус — это 409 (не idempotent 200) по спеке FR-042
---

## 2026-09-12T15:44:00+03:00 | @antigravity | branch:main | mode:speckit-implement
Focus: Фича 001 Ph8 — US6 детектор массовых сбоев и массовый ответ (T039–T041)
Done: ✅
  - backend/app/incidents.py: модуль детектора инцидентов (DETECTION_WINDOW_MIN=15, threshold=3), count_wave (distinct COALESCE(user_id, session_id)), create_incident + notify_duty (outbound pending дежурному), link_wave_tickets (привязка всей волны к инциденту), attach_to_incident (outage_notice без LLM, priority high FR-011, маркер FR-012, статус «решена»), broadcast (рассылка авторам привязанных обращений, 409 на resolved), resolve (409 на повторном)
  - backend/app/agent.py: нода incident_check после ensure_ticket → при наличии активного инцидента выдаёт outage_notice без вызова автопроверок/LLM (экономия лимита FR-021)
  - backend/app/tools.py: инструмент simulate_wave (type="exec"), регистрация в TOOLS, проброс ToolError наружу
  - backend/app/routers/admin.py: GET /api/admin/incidents (active первыми), POST /api/admin/incidents/{id}/broadcast (шаблон из БЗ или custom text), POST /api/admin/incidents/{id}/resolve (409)
  - backend/tests/test_incidents.py: 21 тест (детектор, скользящее окно, distinct-авторы, идемпотентность, broadcast, resolve, 401/403/404/409, сценарий 7 quickstart) — ВСЕ 21 зелёные
  - specs/001-misis-support-assistant/tasks.md: задачи T039–T041 отмечены [X]
Decisions: 🧠
  - При создании инцидента link_wave_tickets привязывает все предшествующие обращения волны из 15-мин окна, поэтому broadcast рассылает сообщения всем авторам всплеска (sent >= 3)
  - Ветка инцидента в пайплайне не вызывает LLM и проверки сервисов — мгновенный шаблонный ответ и нулевой расход лимита tool_calls
Next:
  !1: Ph7 (US5 эскалации FR-060, T036–T038): пакет эскалации, summarize, очередь админки, закрытие
   2: Ph9 (US7 самообучение, T042–T043), Ph10 (polish)
Caution:
---

## 2026-09-12T15:45:00+03:00 | @antigravity | branch:main | mode:speckit-implement
Focus: Фича 001 Ph9 — US7 самообучение базы знаний (T042–T043)
Done: ✅
  - backend/app/tools.py: инструмент `draft_kb_article` (type="llm", регистрация в TOOLS), сборка контекста из тикета (диалог, реакции, masked_text, summary), генерация структурированного черновика `KbDraftResult` (title, body, topic), graceful fallback при `LLMUnavailable` (шаблонный черновик без сбоя эскалации)
  - backend/app/kb.py: функция `index_article(db, article)` (идемпотентная индексация отдельной статьи по эмбеддингу, fallback при недоступности API)
  - backend/app/routers/admin.py: `POST /api/admin/escalations/{request_id}/close` (закрытие по цепочке «решена» → «закрыта», генерация черновика при `add_to_kb=true`, идемпотентное закрытие 200), `POST /api/admin/kb/articles/{article_id}/confirm` (подтверждение, индексация embedding, логирование в `events`)
  - backend/tests/test_kb_learning.py: 15 тестов (включая сценарий 8 quickstart.md, 401/403/404, идемпотентность, моки LLM/fallback) — ВСЕ 15 зелёные
  - specs/001-misis-support-assistant/tasks.md: задачи T042–T043 отмечены [X]
  - Полный регрессионный прогон: 234 passed, 0 failed
Decisions: 🧠
  - Повторный close для уже закрытого тикета идемпотентен (200 OK, kb_draft=null) — демо-скрипты не ломаются при повторных вызовах
  - Черновики в БД создаются с confirmed=False и embedding=None, retrieval их не видит до подтверждения оператором
  - Недоступность LLM при confirm оставляет embedding=null (fallback на keyword search), подтверждение не падает
Next:
  !1: Ph10 (Polish: метрики, админка, доки, T044–T047)
   2: Полный демо-прогон 002-T020 (8 сценариев quickstart)
Caution:
  ⚠️ В close_escalation dialog_lines читаются из events тикета (диалог + реакции) с безопасным json.loads
---

## 2026-09-12T15:47:00+03:00 | @antigravity | branch:main | mode:speckit-implement
Focus: Фича 001 Ph7 — US5 эскалация оператору с саммари (T036–T038)
Done: ✅
  - backend/app/tools.py: Pydantic-модель `SummarizePayload`, сборщик проверок `collect_checks(db, request_id)` из `service_checks`, инструмент `summarize` (type="llm"), регистрация в `TOOLS`
  - backend/app/agent.py: константа `ESCALATION_FALLBACK_RECOMMENDATION`, `build_escalation_package`, `notify_duty_operator`, обновление `node_escalate` (вызов summarize через `call_tool`, fallback при `tool_calls>=3`/сбое LLM, сохранение события `escalation_package`, защита статусной цепочки)
  - backend/app/routers/requests.py: helper `_build_request_detail` вынесен для повторного использования в карточке эскалации
  - backend/app/routers/admin.py: `GET /api/admin/queue` (сортировка priority critical→low → created_at asc, гость user:null), `GET /api/admin/escalations/{request_id}` (все блоки пакета FR-060), `POST /api/admin/escalations/{request_id}/close` (цепочка FR-015, валидация 422 на пустую резолюцию, 404 на неэскалированное)
  - backend/tests/test_escalations.py: 11 тестов (пакет FR-060, очередь, сценарий 4b quickstart, перевод, force_escalate, fallback при падении LLM, сохранение статуса тикета, закрытие эскалации с цепочкой FR-015, RBAC оператора, однократное уведомление дежурному, гостевой заказ без эскалации, гость user=null в очереди) — 11/11 зелёные
  - specs/001-misis-support-assistant/tasks.md: задачи T036–T038 отмечены [X]
Decisions: 🧠
  - Решение сбоев LLM в summarize: graceful fallback с безопасными значениями из подзадачи и исходного обращения, тикет не падает
  - Статусная защита: если тикет уже был в статусе «решена» (например, после outage), повторная эскалация не переводит его в «в работе», сохраняя корректность статус-машины FR-015
  - Идемпотентность закрытия тикета: повторный close возвращает 200 OK в соответствии с contracts/api.md
Caution:
  ⚠️ В node_escalate request_id извлекается с fallback на ticket.request_id и subtask.request_id, если отсутствует в state
---

## 2026-09-12T15:49:00+03:00 | @antigravity | branch:main | mode:speckit-implement
Focus: Фича 001 Ph10 — Polish: метрики, админка, доки (T044–T047)
Done: ✅
  - backend/app/metrics.py: модуль метрик FR-062 (`auto_closed_pct`, `avg_first_reaction_sec`, `incidents_total`, `incidents_active`, `requests_total`, `escalations_open`), фасад `collect_metrics(db)`
  - backend/app/routers/admin.py: эндпоинты `GET /api/admin/metrics`, `GET /api/admin/status-board` (сервисы + last_check), `PATCH /api/admin/services/{id}` (409 для real, 200 для emulated), `GET /api/admin/tools`, `POST /api/admin/tools/{name}/invoke` (строгая валидация params, 422)
  - backend/app/tools.py: схема `params` для админ-панели, `ADMIN_INVOKABLE`, фикс `call_tool` с корректной инспекцией `subtask_id` (устранение конфликта duplicate kwargs)
  - backend/app/incidents.py: `simulate_wave` (генерация тестовых тикетов и запуск детектора)
  - backend/tests/conftest.py: фикстуры `operator_client`, `FakeHttpClient`, `fake_http`
  - backend/tests/test_metrics.py: 8 тестов (расчет метрик, empty db, 401/403) — 100% зелёные
  - backend/tests/test_admin_panel.py: 11 тестов (status-board, patch real/emulated, tools registry, invoke, валидация 422) — 100% зелёные
  - backend/tests/test_escalations.py: исправлены 5 тестов (session_id в Request, вызов summarize) — 11/11 зелёные
  - Документация: актуализированы `README.md`, `.ai/SESSION_STATE.md`, `.ai/PROJECT_MAP.md`, `.ai/STACK.md`
  - specs/001-misis-support-assistant/tasks.md: задачи T044–T047 отмечены [X]
  - Полный регрессионный прогон: **264 passed, 0 failed**
Decisions: 🧠
  - В `call_tool` добавлена проверка через `inspect.signature` на наличие `subtask_id` в параметрах вызываемого инструмента для предотвращения дублирования параметров
  - Метрика лага первой реакции рассчитывается на уровне Python с парсингом SQLite ISO-строк дат для предотвращения расхождений типов
Next:
  !1: Полный демо-прогон 002-T020 (8 сценариев quickstart через веб-интерфейс и curl)
   2: Фича 003 — Telegram-бот
Caution:
---

## 2026-09-12T11:00:00+03:00 | @antigravity | branch:main | mode:normal
Focus: Полноценные тесты и завершение реализации сценариев Telegram-бота (фича 003)
Done: ✅
  - US1 (T007): приём обращений в idle, мгновенный T9, реакции ядра, дубликаты, префиксы {i}/{n}
  - US3 (T009): уточняющий диалог dialog:<request_id>, 409 conflict, завершение и возврат в idle
  - US4 (T010): команды /status и /certs, инлайн-кнопки K1/K2, заказ справок, обновление заказов
  - US5 (T011): фоновый метод run_outbox_step() в BotRunner для доставки уведомлений дежурному (ack sent/failed)
  - Тесты: 70/70 green (test_bot_handlers.py + test_bot_outbox.py + test_bot_phase1.py)
  - Tasks: отмечены выполненными T007, T009, T010, T011, T012, T013, T014 в specs/003-telegram-bot/tasks.md
Decisions: 🧠
  - Все тесты изолированы на моках HTTP/API, zero external network dependency, pro-run < 0.4s
  - FSM состояний покрывает как ядровые переходы, так и локальные оверлеи dialog / cancel
Next:
  1. FIX L001+L002
  2. Интеграция фичи 003 со стендом ядра (001)

---

## 2026-09-12T11:10:00+03:00 | @antigravity | branch:main | mode:normal
Focus: Реализация и верификация Фазы 5 (User Story 3 / T009) — сценарий S2b уточняющего диалога
Done: ✅
  - handlers.py: доработка handle_dialog_text (пустой список реакций -> MSG_DIALOG_COMPLETED, динамический update request_id, дубликаты)
  - test_bot_handlers.py: 4 новых теста (empty reactions, dynamic id, 503 retry state preservation, multi-turn e2e)
  - Тесты: 74/74 green (13/13 dialog/cancel)
  - tasks.md + SESSION_STATE.md синхронизированы
Decisions: 🧠
  - При пустом reactions чат переводится в idle с отправкой MSG_DIALOG_COMPLETED вместо зависания в молчании
  - При 503/сетевом сбое ядра состояние диалога dialog:<id> не сбрасывается — пользователь может повторить попытку ввода
Next:
  1. Интеграция фичи 003 с ядром (001) и веб-кабинетом (002)
  2. FIX L001+L002

---

## 2026-09-12T11:35:00+03:00 | @antigravity | branch:main | mode:normal
Focus: Полное код-ревью и устранение всех замечаний Telegram-бота поддержки МИСИС (фича 003)
Done: ✅
  - core_api.py: прозрачный релогин через ensure_user_session при старте и HTTP 401; LRU-вытеснение клиентов _sessions (макс. 500)
  - main.py: фоновый поток _outbox_loop (такт строго 10 с, не блокируется long polling Telegram и backoff); батчинг сохранения .bot_offset
  - handlers.py: персистентность состояний dialog:<id> через .bot_states; форматирование даты /status в часовом поясе МСК (UTC+3)
  - tg.py: автоматический повтор запросов при ошибках серверов Telegram 5xx (500, 502, 503, 504)
  - Тесты: 104/104 green (добавлено 7 новых тестов на все пограничные случаи, 98% покрытие app.bot)
Decisions: 🧠
  - Опрос outbox вынесен в daemon-поток: гарантия доставки дежурному <= 1 мин по SC-003 независимо от сетевых задержек polling
  - Auto-login срабатывает прозрачно при 401: бот переживает рестарт процесса без блокировки пользователей
Next:
  1. FIX L001+L002
  2. Сквозное E2E тестирование связки ядро (001) + веб (002) + бот (003)

---

## 2026-09-12T16:05:00+03:00 | @kimi | branch:main | mode:sync
Focus: Подтягивание Telegram-бота (фича 003) от тиммейта и сверка с контрактом
Done: ✅
  - Смержен origin/main: бот появился в backend/app/bot/ + tests/test_bot_*.py + specs/003-telegram-bot/tasks.md (все галки [x])
  - Сверка по spec.md / bot-contract.md / tasks.md: реализованы US1–US5, T001–T014
  - Тесты бота: 102 passed / 0 failed
  - Импорты app.bot.* работают, эндпоинты ядра /api/internal/tg/link* и /api/internal/outbound* на месте
Decisions: 🧠
  - merge-commit 098e9b9 — сохранил локальный WIP (frontend Next.js, core), конфликты только в .ai/* разрешены в пользу объединённой истории
Next:
  1. Починить 4 независимых фейла в core-тестах (коллизия email в seed/tests)
  2. Сквозной E2E ядро + веб + бот


---

## 2026-09-12T22:00:00+03:00 | @kimi | branch:main | mode:build
Focus: Улучшение фронтенда MAIA: маскот, брендинг, визуал, профили, env, проверка работоспособности + редизайн главной в ChatGPT-стиль
Done: ✅
  - Подтянут origin/main: Telegram-бот смержен в backend/app/bot/
  - Маскот /Users/seva/Downloads/2026-09-12 15.57.07.jpg → frontend/public/maia-mascot.jpg + frontend/app/icon.jpg
  - Проект переименован в MAIA: shell, layout metadata, login, страницы
  - Редизайн 8 страниц с lucide-иконками, .card/.page-head/.icon-tile/.stat-grid и т.д.
  - Профили: студент/сотрудник/оператор — карточка в кабинете, чип в shell, заголовок админки
  - types.ts синхронизирован с backend (role staff, Reaction.sources, lang null и т.п.)
  - Env: backend/.env заполнен Yandex API ключом <YANDEX_API_KEY>, folder b1gugf5t09o5smmiolbf, модели deepseek-v4-flash/latest, BOT_INTERNAL_TOKEN сгенерирован
  - Фикс P0 simulate_wave: фронт шлёт слаги site/lms/wifi_guest/wifi_edu/wifi_corp
  - Проверка: backend pytest 370/370 green; frontend npm run build зелёный; E2E с реальным LLM — все 3 роли логинятся
  - Код-ревью 8/10, дефекты закрыты (open redirect, гонка toggle, NaN-id, alt, disabled-рассинхрон, Terminal keys, пустой numeric)
  - Главная переделана в ChatGPT-подобный интерфейс: левый сайдбар с папками «Ждут ответа» / «Общие обращения», центральный чат, плавающий композер-пилюля
Decisions: 🧠
  - Дизайн-система расширена аддитивно в одном globals.css, новые классы не ломают старые страницы
  - Все LLM-вызовы работают через Yandex AI Studio OpenAI-compatible endpoint с deepseek-v4-flash
Next:
  1. Демо-прогон end-to-end по сценариям quickstart
  2. Донастройка визуала по обратной связи
