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

