# PROJECT MAP (Shorthack) — карта проекта

## Статус: MVP scaffold (2026-09-12)

## Что работает ✅
- `POST /api/shorten` — сокращение ссылки (Pydantic HttpUrl валидация)
- `GET /{code}` — 307 редирект + инкремент кликов
- `GET /api/stats/{code}` — клики по коду
- pytest: 2/2 green (shorten+redirect+stats, 404)

## Ключевые файлы
| Файл | Что внутри |
|------|-----------|
| [backend/app/main.py](../backend/app/main.py) | Всё API в одном файле (~80 строк): модель Link, генерация кода, 3 эндпоинта |
| [backend/tests/test_api.py](../backend/tests/test_api.py) | TestClient: happy path + not found |
| [frontend/src/App.jsx](../frontend/src/App.jsx) | Форма → fetch `/api/shorten` → показ short_url |
| [frontend/vite.config.js](../frontend/vite.config.js) | proxy `/api` и `/s` → localhost:8000 |

## Архитектура (текущая)
Browser → Vite dev (5173) → proxy → FastAPI (8000) → SQLite
! Vite proxy настроен на `/s`, но бэкенд отдаёт редирект на `/{code}` — рассинхрон, см. LESSONS L001

## Дорожная карта
1. Custom alias (`POST /api/shorten {"url":..., "alias":"my-link"}`)
2. Expiring links (TTL)
3. Аналитика: referer, geo, по времени
4. QR-генерация
5. Аккаунты + дашборд ссылок
6. PostgreSQL + Redis

## Точки входа для агента
- Новый эндпоинт → `backend/app/main.py` (пока монолитный, не плодить файлы раньше времени)
- UI-фича → `frontend/src/App.jsx` + `App.css`
- Всегда: прогнать `cd backend && python -m pytest tests/ -v`
