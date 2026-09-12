# Shorthack ⚡

Сервис коротких ссылок в стиле хакатонного проекта: минимум кода, максимум пользы.

## Стек

- **Backend:** FastAPI + SQLite (SQLAlchemy)
- **Frontend:** React + Vite
- **Тесты:** pytest

## Запуск

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload   # http://localhost:8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

## API

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/api/shorten` | Сократить ссылку `{"url": "https://..."}` |
| GET | `/api/stats/{code}` | Статистика (клики) |
| GET | `/{code}` | Редирект на оригинальный URL |

## Тесты

```bash
cd backend
python -m pytest tests/ -v
```
