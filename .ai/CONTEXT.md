# CONTEXT (Shorthack)

Дата: 2026-09-12
Workspace: /Users/seva/Projects/shorthack
Тип: хакатонный pet-проект — сервис коротких ссылок

## Обзор
Минималистичный URL shortener: FastAPI бэкенд + React фронтенд.
Цель MVP — сокращать ссылки и считать клики за минимум кода.

## Стек
Backend: Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite, Pydantic v2, uvicorn
Frontend: React 18, Vite 5
Tests: pytest, httpx TestClient

## Архитектура
`Browser` → `Vite dev :5173` → `proxy /api` → `FastAPI :8000` → `SQLite (shorthack.db)`

### Поток сокращения
1. POST `/api/shorten` `{url}` → валидация HttpUrl → генерация 6-символьного кода → INSERT
2. Ответ: `{code, short_url: "/{code}", url, clicks}`
3. GET `/{code}` → SELECT по code → clicks++ → 307 Redirect

## НЕ ДЕЛАТЬ
- ❌ Не тащить PostgreSQL/Redis/Docker до появления реальной нагрузки
- ❌ Не разбивать main.py на роутеры, пока эндпоинтов ≤ 3
- ❌ Не коммитить `shorthack.db`, `.venv`, `node_modules`
- ❌ Не писать доки/комментарии на английском

## Файлы для ОБЯЗАТЕЛЬНОГО чтения
- [.ai/LESSONS.md](.ai/LESSONS.md) — 4 актуальных ловушки проекта
- [.ai/PROJECT_MAP.md](.ai/PROJECT_MAP.md) — что работает и роадмап
- [backend/app/main.py](../backend/app/main.py) — вся текущая логика

## Уверенность
STACK: ✅ [FastAPI, SQLite, React, Vite]
EXISTING: ✅ [код написан, тесты green]
ANTI: ✅ [no over-engineering, no secrets in code]
