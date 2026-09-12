# Implementation Plan: ИИ-помощник технической поддержки МИСИС — ядро

**Branch**: `[001-misis-support-assistant]` | **Date**: 2026-09-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-misis-support-assistant/spec.md`

## Summary

Ядро ИИ-помощника техподдержки МИСИС: приём обращений от авторизованного пользователя, нормализация (мат, язык), сплиттер, LLM-классификатор с триггерами и confidence-порогом, маршрутизация (автопроверка / база знаний / заказ справки / эскалация), реестр инструментов exec/llm с самовызовом (лимит 3), agentic RAG по документам и шаблонам, заказ справок (5 типов, 4 статуса), дедупликация, детектор массовых сбоев с экстренными уведомлениями и массовым ответом, эскалации с саммари, самообучение базы знаний, метрики. Оркестрация агента — LangGraph 1.1 (StateGraph поверх функций-нод); LLM и эмбеддинги — Yandex AI Studio по OpenAI-совместимому API через httpx (новых тяжёлых зависимостей нет).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI ≥0.110, SQLAlchemy ≥2.0, Pydantic ≥2.6, httpx ≥0.27 (уже в `backend/requirements.txt`); добавляется только `langgraph >=1.1,<2` (StateGraph-оркестрация агента). OpenAI-SDK и векторные БД не используются — вызовы Yandex API через httpx, косинусная близость на чистом Python.

**Storage**: SQLite (`backend/shorthack.db` → новые таблицы; миграций нет, `Base.metadata.create_all()` при старте). Эмбеддинги — JSON-текстом в таблице `kb_articles` (256-dim, ~50 документов — поиск мгновенный).

**Testing**: pytest + httpx TestClient (`backend/tests/`); LLM в тестах мокается (контрактные тесты на JSON-схему классификатора, юнит-тесты триггеров/детектора/статусов).

**Target Platform**: локальный запуск (macOS/Linux), uvicorn :8000; демо на ноутбуке.

**Project Type**: web-service (REST API ядра; веб-фронт — фича 002, Telegram-бот — фича 003, оба ходят в это API).

**Performance Goals**: первая реакция ≤ 60 c (SC-001), извещение о сбое ≤ 2 c без LLM (SC-004); таймаут вызова LLM — 25 c, таймаут проверок сервисов — 5 c (FR-022/023).

**Constraints**: 6 часов разработки; без Docker/PostgreSQL/Redis (CONTEXT.md); секреты только в `.env` (LESSONS L004); сессии SQLAlchemy через `Depends`+yield (LESSONS L002); комментарии и доки — на русском.

**Scale/Scope**: демо-нагрузка (десятки обращений); ~25 REST-эндпоинтов; база знаний ~10-50 статей; 3 Wi-Fi-сети эмуляции; 5 тестовых пользователей.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Конституция проекта (`.specify/memory/constitution.md`) — незаполненный шаблон, нормативных принципов не содержит → нарушений нет. Применяются проектные правила из `AGENTS.md`/`.ai/`: минимум зависимостей, без over-engineering, русские доки, секреты в `.env` — план им соответствует. Пост-дизайн перепроверка: соответствие сохранено (единственная новая зависимость — `langgraph`, требование заказчика).

## Project Structure

### Documentation (this feature)

```text
specs/001-misis-support-assistant/
├── plan.md              # Этот файл
├── research.md          # Phase 0: решения по технологиям
├── data-model.md        # Phase 1: сущности и переходы состояний
├── quickstart.md        # Phase 1: запуск и сценарии проверки
├── contracts/
│   └── api.md           # Phase 1: REST-контракт ядра (общий для 002/003)
└── tasks.md             # Phase 2 (/speckit-tasks — отдельной командой)
```

### Source Code (repository root)

```text
backend/
├── app/
│   ├── main.py          # FastAPI app, подключение роутеров (старое API сокращателя удаляется)
│   ├── db.py            # engine, Base, get_session (Depends+yield, L002)
│   ├── models.py        # SQLAlchemy: users, sessions, requests, subtasks, tickets,
│   │                    #   services, service_checks, kb_articles, cert_orders,
│   │                    #   incidents, events, tg_links, outbound_messages
│   ├── schemas.py       # Pydantic-схемы запросов/ответов + схема JSON классификатора
│   ├── llm.py           # Yandex AI Studio клиент: chat(), embed(), JSON-валидация + 1 retry
│   ├── triggers.py      # детерминированные правила (до/после классификатора)
│   ├── tools.py         # реестр инструментов exec/llm + диспетчер вызова
│   ├── agent.py         # LangGraph StateGraph: ноды пайплайна, conditional edges, лимит 3
│   ├── kb.py            # RAG: индексация (embed), retrieval (косинус), ответ по источникам
│   ├── tickets.py       # жизненный цикл заявки (FR-015), дедупликация
│   ├── incidents.py     # детектор массовых сбоев, уведомления, массовый ответ
│   ├── certs.py         # каталог справок, заказы, смена статусов
│   ├── auth.py          # эмуляция входа по email, сессии (cookie)
│   ├── metrics.py       # агрегация метрик из журнала
│   ├── seed.py          # тестовые пользователи, сервисы, документы и шаблоны БЗ
│   └── routers/
│       ├── auth.py      # /api/auth/*
│       ├── requests.py  # /api/requests* (подача, диалог, мои обращения)
│       ├── certs.py     # /api/certs/*
│       └── admin.py     # /api/admin/* (очередь, эскалации, заказы, статус-борд,
│                        #   инструменты, метрики, инциденты)
└── tests/
    ├── test_api.py      # контрактные тесты эндпоинтов (переписать под новое API)
    ├── test_agent.py    # пайплайн с моком LLM: маршруты, лимиты, safe-route
    ├── test_triggers.py # триггеры и приоритет-арбитраж
    └── test_incidents.py# детектор, дедупликация, статусы справок
```

**Structure Decision**: Web application (backend + frontend в репозитории). Backend разбивается на модули — правило CONTEXT.md «не разбивать main.py пока эндпоинтов ≤ 3» перестало применяться (~25 эндпоинтов). Старый код сокращателя ссылок (`/api/shorten`, `/{code}`) удаляется вместе с таблицей `links`; scaffold (стек, прокси, `.ai/`) сохраняется.

## Complexity Tracking

> Нарушений конституции нет — секция пуста. Осознанные решения: `langgraph` — единственная новая зависимость (явное требование заказчика «агенты в langgraph»); векторный поиск без внешней БД — осознанное упрощение (объём ~50 статей).
