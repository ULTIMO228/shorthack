# STACK (Shorthack) — infra + tech details

## MVP Стек
Backend: FastAPI + SQLite (SQLAlchemy 2.0) | Python 3.12
Frontend: React 18 + Vite 5 | чистый CSS (без Tailwind пока)
Tests: pytest + httpx (TestClient) | 2 теста green
Runtime: uvicorn (dev: `--reload`, port 8000) | frontend dev: port 5173, proxy `/api` → 8000

## Структура
```
shorthack/
├── backend/
│   ├── app/main.py        # FastAPI: /api/shorten, /api/stats/{code}, /{code} redirect
│   ├── tests/test_api.py  # pytest: shorten+redirect+stats, 404
│   └── requirements.txt
└── frontend/
    ├── src/App.jsx        # форма сокращения + результат
    └── vite.config.js     # proxy на backend
```

## БД
SQLite (файл `shorthack.db`, НЕ коммитится) | таблица `links`: id, code(unique), url, clicks, created_at
! Миграций нет — `Base.metadata.create_all()` при старте
! План: переход на PostgreSQL при >100K ссылок

## Генерация кодов
6 символов [a-zA-Z0-9] | random.choices | 5 попыток на коллизию
! План: custom alias + expiring links

## Хостинг (план)
Stage1 (MVP): один VPS, uvicorn за nginx, SQLite на диске
Stage2: PostgreSQL + Redis (кэш редиректов) + горизонтальное масштабирование

## Монетизация (идея, не реализовано)
Free: безлимит базовых ссылок | Pro: custom alias, аналитика, QR
