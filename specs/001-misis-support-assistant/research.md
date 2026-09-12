# Phase 0 — Research: ядро (001)

## R1. Подключение к Yandex AI Studio

- **Decision**: OpenAI-совместимый API `https://ai.api.cloud.yandex.net/v1` через `httpx` (уже в зависимостях). Аутентификация: заголовок `Authorization: Api-Key <YANDEX_API_KEY>` + `OpenAI-Project: <YANDEX_FOLDER_ID>`. Модели: `gpt://<folder>/yandexgpt-lite/latest` — классификатор (быстро/дёшево), `gpt://<folder>/yandexgpt/latest` — RAG-ответы и саммари. Эмбеддинги: `emb://<folder>/text-search-doc/latest` (индексация статей) и `emb://<folder>/text-search-query/latest` (запросы), вектор 256-dim.
- **Rationale**: ноль новых зависимостей (httpx уже есть), полный контроль таймаутов (25 c LLM / 5 c проверки), OpenAI-совместимость подтверждена [докой AI Studio](https://aistudio.yandex.ru/ru/docs/ai-studio/operations/embeddings/search-openai) и [Habr](https://habr.com/ru/articles/1049322/).
- **Alternatives considered**: `openai` python SDK (лишняя зависимость, выигрыша при 2 типах вызовов нет); официальный `yandex-ai-studio-sdk` (тяжелее, менее знаком команде); нативный `foundationModels/v1/completion` (не OpenAI-совместимый — больше кода).
- **Открытое на месте**: точный список доступных моделей и формат ключа сверить с примером организаторов (слайд 7 ТЗ) — переменные `YANDEX_API_KEY`, `YANDEX_FOLDER_ID`, `YANDEX_MODEL_CLASSIFY`, `YANDEX_MODEL_GENERATE` в `.env`, значения по умолчанию выше.
- **JSON-режим**: поддержка `response_format` не гарантирована → промпт «верни строго JSON по схеме» + `json.loads` + Pydantic-валидация + 1 retry с уточняющим промптом; при повторном сбое — безопасный маршрут (FR-013).

## R2. Оркестрация агента — LangGraph

- **Decision**: `langgraph >=1.1,<2` (стабильная линия 1.1.x, Python ≥3.10). `StateGraph` со state-.TypedDict: `{request_id, text, lang, subtasks[], route, service, category, priority, confidence, reason, tool_calls:int, tool_results[], dialog_rounds:int, reaction}`. Ноды = обычные функции пайплайна (normalize → split → classify → triggers → execute_route → respond). Условные рёбра по `route`. Лимит самовызова — счётчик `tool_calls` в state, превышение → нода эскалации (FR-021). Checkpointer — in-memory `MemorySaver` (состояние диалога между HTTP-запросами восстанавливаем из БД по `request_id`, персистентность чекпоинтов не нужна).
- **Rationale**: явное требование заказчика («агенты работают в langgraph»); граф даёт читаемую схему для слайдов; ноды тестируются как обычные функции с моком LLM.
- **Alternatives considered**: ручной цикл функций без фреймворка (проще, но нарушает требование; оставлено как аварийный план — ноды не зависят от LangGraph по сигнатурам); CrewAI/AutoGen (тяжелее, избыточно).

## R3. Хранение и поиск эмбеддингов (RAG)

- **Decision**: векторы — JSON-текстом в колонке `kb_articles.embedding`; косинусная близость на чистом Python (`math.sqrt`, без numpy); top-k=3, порог релевантности 0.5 (настраивается). Индексация — при сидировании и при подтверждении новой статьи оператором (FR-063).
- **Rationale**: ~10-50 статей × 256 float — полный скан мгновенный; без новых зависимостей и сервисов (CONTEXT.md).
- **Alternatives considered**: FAISS/pgvector/Qdrant (overkill); keyword-поиск без эмбеддингов (запасной fallback при недоступности API эмбеддингов — реализуется в `kb.py` как деградация).

## R4. Авторизация (эмуляция)

- **Decision**: `POST /api/auth/login {email}` → проверка домена (`@misis.ru`, `@edu.misis.ru`) → запись в `sessions` + HttpOnly cookie `session_id`. Без паролей и внешнего IdP. Пользователи — seed (5 штук: 3 студента, сотрудник, оператор).
- **Rationale**: требование «вход в начале, по мне была инфа» при нулевой стоимости; cookie — стандартно для fetch с `credentials:'include'` через Vite-прокси.
- **Alternatives considered**: JWT (избыточно для прототипа); заголовок `X-User-Email` без сессий (небезопасно выглядит даже для демо, и нет «входа» как действия).

## R5. Реестр инструментов и самовызов

- **Decision**: `tools.py` — словарь `{name: {type: exec|llm, fn, schema}}`. Exec: `check_site`, `check_lms`, `check_wifi`, `order_certificate`, `create_ticket`, `send_template`, `broadcast_incident`, `add_kb_article`. LLM: `classify`, `kb_agent`, `summarize`, `translate`, `draft_kb_article`. Агент вызывает через единый диспетчер с журналированием в `events` (FR-061) и счётчиком лимита (FR-021). Тест-панель админки дёргает тот же диспетчер (`/api/admin/tools/{name}/invoke`).
- **Rationale**: единая точка вызова = журнал, лимиты и тест-панель бесплатно.
- **Alternatives considered**: function-calling протокол Yandex (не подтверждён документально для их OpenAI-режима — отложено; при наличии времени и подтверждении — маппинг реестра на их tools-формат).

## R6. Детектор инцидентов и дедупликация

- **Decision**: на каждое новое обращение — SQL по `subtasks`: `service + category` совпадают, `created_at` в окне 15 мин, `COUNT(DISTINCT user_id) >= 3` и нет активного инцидента → создать инцидент + уведомить. Дедупликация: открытая заявка того же пользователя с теми же `service+category` (статус не «решена»/«закрыта») → привязка обращения к ней (FR-014).
- **Rationale**: правило однотипности из Clarifications Q1; SQL — дёшево и прозрачно для тестов.
- **Alternatives considered**: сходство эмбеддингов (отклонено заказчиком в Q1).

## R7. Telegram-бот (детально — фича 003)

- **Decision**: отдельный процесс `backend/app/bot/`, long polling по Bot API через `httpx` (`getUpdates` с offset), без фреймворков; вся логика — вызовы REST API ядра (тонкий канал).
- **Rationale**: ноль новых зависимостей, нет asyncio-конфликтов с uvicorn, контракт ядра переиспользуется; другому разработчику не нужно знать фреймворк — только HTTP.
- **Alternatives considered**: `aiogram 3` (стандарт, но новая зависимость + кривая asyncio); webhook (нужен публичный домен — нет).
