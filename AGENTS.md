# AGENTS (Shorthack) — compressed agent reference

> Пока AI-агентов в продукте нет. Это референс для coding-агентов
> (см. `.agents/`) + план AI-фич, если проект вырастет.

## Coding Agents (активны)
| Агент | Папка | Зона |
|-------|-------|------|
| @backend-dev | `backend-developer-fastapi-sqlalchemy/` | FastAPI эндпоинты, модели, SQLAlchemy |
| @frontend-dev | `frontend-developer-react-vite/` | React-компоненты, CSS, Vite |
| @tester | `test-engineer-pytest/` | pytest, покрытие API |

## Протокол агента
READ при старте: AGENTS.md + lessons.md + memory.md папки агента
WRITE при завершении: memory.md (Session Log) + lessons.md (если была ошибка)
+ обновить `.ai/SESSION_STATE.md` и дописать `.ai/SESSIONS.md`

## AI-фичи (роадмап, если растём)
- A1 LinkIntel: аномалии кликов, бот-детект по паттернам
- A2 SmartAlias: LLM-подбор запоминающихся alias из URL
- A3 Digest: еженедельная сводка по ссылкам пользователя
! Только после аккаунтов и аналитики — не раньше

## Cost-awareness
Любой LLM-вызов — только за продуктовый Pro-план, не за счёт MVP-юзеров.

## Graphiti (knowledge graph)
- Библиотека `graphiti-core` (Zep) — темпоральный граф знаний на Neo4j; бэкенд-клиент: `backend/app/graphiti_client.py`.
- Хранилище: Neo4j 5.26 через `docker-compose.yml` в корне (`docker compose up -d`), консоль на http://localhost:7474.
- Конфиг через env: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `OPENAI_API_KEY` (шаблон — `backend/.env.example`).
- Клиент ленивый: без env-переменных приложение работает без графа (SQLite-режим), `get_graphiti()` вернёт None.
- Правило: любой код, добавляющий события/сущности в граф (клики, ссылки, сигналы), идёт ТОЛЬКО через `graphiti_client.add_episode()` / `get_graphiti()`, не напрямую через драйвер.
- LLM-вызовы Graphiti (экстракция сущностей/эмбеддинги) — по правилу Cost-awareness выше.
