# SESSION STATE (Shorthack) — актуальное состояние

> Обновлять В КОНЦЕ каждой сессии. Формат: что сделано / что горит / что дальше.

## Последнее обновление: 2026-09-12T10:16:00+03:00

## Готово ✅
- [x] Backend: FastAPI + SQLite, 3 эндпоинта (shorten / redirect / stats)
- [x] Frontend: React форма сокращения, тёмный UI
- [x] Бот (фича 003): Фаза 1 (Setup) T001–T005 (main, tg, core_api, messages, keyboards)
- [x] Бот (фича 003): Фаза 2 (Foundational) T006 (handlers.py, автомат состояний, диспетчер, команды)
- [x] Тесты: 28/28 green (17 bot phase 1 + 9 handlers + 2 api)
- [x] GitHub private repo, main запушен
- [x] .ai/ + .agents/ инфраструктура

## Горит 🔥 (блокеры/баги)
- L001: `/s/` prefix рассинхрон vite proxy ↔ backend short_url
- L002: SQLAlchemy сессии без close()

## В работе 🛠
- Фича 003: Telegram-бот поддержки МИСИС (переход к Phase 4 / US2 — привязка профиля)

## Следующие шаги (приоритет)
1. Фича 003: Фаза 4 (US2 T008 привязка профиля по почте @misis.ru/@edu.misis.ru и сессия login)
2. Фича 003: Фаза 3 (US1 T007 приём и обработка обращений, реакции ядра)
3. FIX L001+L002 — 30 мин

## Контекст для следующей сессии
- Запуск: `cd backend && .venv/bin/uvicorn app.main:app --reload` + `cd frontend && npm run dev`
- Тесты: `cd backend && .venv/bin/python -m pytest tests/ -v`
- .venv бэкенда уже создан, НЕ коммитить
- LESSONS.md читать ПЕРЕД правками — там 4 свежих ловушки
