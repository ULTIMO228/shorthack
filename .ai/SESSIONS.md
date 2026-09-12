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

