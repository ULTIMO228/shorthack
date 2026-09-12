# ИИ-помощник техподдержки Университета МИСИС 🎓⚡

Интеллектуальная система автоматизации первой линии технической поддержки НИТУ МИСИС.
Автоматически классифицирует обращения, выполняет диагностику сервисов, отвечает по регламентам и базе знаний (RAG), оформляет справки, обнаруживает массовые сбои (аварии) и передаёт сложные случаи операторам с готовым саммари.

---

## Стек технологий

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0, SQLite
- **AI / Оркестрация:** LangGraph (StateGraph), Yandex AI Studio (YandexGPT + Yandex Embeddings, 256-dim)
- **Knowledge Graph (опционально):** Neo4j 5.26 + Graphiti Core
- **Frontend:** Next.js 16 (App Router), React 19, TypeScript, Lucide Icons, чистый CSS (дизайн-токены МИСИС)
- **Тестирование:** pytest (264 теста), httpx (TestClient)

---

## Архитектура ядра

```text
Пользователь (Web / Telegram)
              │
              ▼
    POST /api/requests
              │
              ▼
   LangGraph Pipeline (app/agent.py)
   ├─ 1. normalize (маскирование PII/мата, детект языка, перевод)
   ├─ 2. split (разделение составных обращений)
   ├─ 3. classify (сервис, категория, приоритет, уверенность)
   ├─ 4. triggers (форс-эскалация жалоб, порог confidence 0.6)
   ├─ 5. check_duplicate (дедупликация по сессии/пользователю)
   ├─ 6. incident_check (проверка массовой аварии без вызова LLM)
   └─ 7. execute_route:
        ├─ auto_check   → HTTP-проверка site/lms, эмуляция Wi-Fi (app/tools.py)
        ├─ kb           → RAG-поиск по регламентам МИСИС (app/kb.py)
        ├─ cert_order   → заказ студенческих справок (app/certs.py)
        └─ escalate     → сборка пакета эскалации с саммари оператору (FR-060)
```

---

## Быстрый старт

### 1. Бэкенд

```bash
cd backend

# Создание и активация виртуального окружения
python3 -m venv .venv
source .venv/bin/activate

# Установка зависимостей
pip install -r requirements.txt

# Настройка переменных окружения
cp .env.example .env
# Заполните YANDEX_API_KEY и YANDEX_FOLDER_ID (при отсутствии ключей система работает в режиме fallback)

# Запуск сервера
uvicorn app.main:app --reload --port 8000
```

> **Примечание**: при первом запуске автоматически создаются таблицы SQLite (`shorthack.db`) и выполняется идемпотентный сид: 5 пользователей (студенты, операторы, преподаватель), сервисы МИСИС и начальная база знаний.

### 2. Фронтенд (Веб-интерфейс)

```bash
cd frontend
npm install
npm run dev
# Доступен на http://localhost:3000 (проксирует /api → http://localhost:8000)
```

---

## Основные API-группы

| Префикс | Описание | Основные эндпоинты |
|---|---|---|
| `/api/auth` | Аутентификация и сессии | `POST /login`, `POST /logout`, `GET /me` |
| `/api/requests` | Обращения студентов | `POST /`, `POST /{id}/reply`, `GET /`, `GET /{id}` |
| `/api/certs` | Заказ справок студентами | `GET /catalog`, `POST /orders`, `GET /orders` |
| `/api/admin` | Панель оператора | `GET /queue`, `GET /escalations/{id}`, `POST /escalations/{id}/close`, `GET /incidents`, `POST /incidents/{id}/broadcast`, `GET /certs/orders`, `PATCH /certs/orders/{id}`, `GET /status-board`, `PATCH /services/{id}`, `GET /tools`, `POST /tools/{name}/invoke`, `GET /metrics` |
| `/api/internal` | Системная интеграция | `POST /tg-link`, `GET /outbound-messages` |
| `/health` | Проверка здоровья сервиса | `GET /health` |

---

## Сценарии проверки (Quickstart)

Полный набор сквозных сценариев с примерами `curl` находится в [`specs/001-misis-support-assistant/quickstart.md`](specs/001-misis-support-assistant/quickstart.md):

1. **Сценарий 1:** Прямой вопрос по LMS (автодиагностика `auto_check`).
2. **Сценарий 2:** Недоступность сервиса и аварийное оповещение (`outage_notice`).
3. **Сценарий 3:** Вопрос по регламенту (RAG-ответ из базы знаний).
4. **Сценарий 4:** Неясный вопрос или жалоба на сотрудника (эскалация оператору с саммари).
5. **Сценарий 5:** Заказ справки об обучении (авторизованный и гостевой сценарии).
6. **Сценарий 6:** Мультиязычный запрос на английском (автоперевод и ответ).
7. **Сценарий 7:** Массовый сбой (симуляция волны жалоб, детекция аварии, рассылка broadcast).
8. **Сценарий 8:** Самообучение базы знаний (закрытие эскалации с добавлением черновика статьи в БЗ).

---

## Тестирование

Запуск полного тестового набора ядра (264 теста):

```bash
cd backend
.venv/bin/python -m pytest tests/ -v
```

Все сетевые вызовы к LLM (YandexGPT) и внешним HTTP-ресурсам в тестах детерминированно изолированы фикстурами.
