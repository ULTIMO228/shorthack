# Implementation Plan: Веб-интерфейс помощника поддержки МИСИС

**Branch**: `[002-web-interface]` | **Date**: 2026-09-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-web-interface/spec.md`

## Summary

Веб-интерфейс поверх ядра (001): экран входа по email МИСИС в начале работы, форма подачи обращения с диалогом уточнений и кнопками примеров, личный кабинет (мои обращения, каталог справок, мои заказы со статусами), админка оператора (очередь, карточка эскалации с саммари, статус-борд, раздел заказов справок со сменой статусов, тест-панель инструментов, виджет метрик, действия «в базу знаний»/«уведомить затронутых», баннер инцидента). Тонкий клиент: вся логика и данные — через REST-контракт `specs/001-misis-support-assistant/contracts/api.md`.

## Technical Context

**Language/Version**: JavaScript (ES2022), React 18.3, Vite 5

**Primary Dependencies**: react, react-dom (уже в `frontend/package.json`); роутер и state-библиотеки НЕ добавляются — переключение экранов состоянием App, запросы через `fetch` + обёртка `api.js`.

**Storage**: нет (всё в ядре); сессия — HttpOnly cookie, `fetch(..., { credentials: "include" })` через Vite-прокси `/api` → :8000 (`vite.config.js` уже настроен).

**Testing**: ручной прогон сценариев quickstart; сборка `npm run build` как чек.

**Target Platform**: браузер (демо с ноутбука), dev :5173.

**Project Type**: web-frontend (SPA одной страницы).

**Performance Goals**: реакции UI мгновенные; ожидание ядра — спиннер «Агент разбирает обращение…» (до 60 c, SC-001 ядра).

**Constraints**: тёмная тема существующего scaffold (`App.css`); без дизайн-системы и CSS-фреймворков; русский интерфейс; минимум зависимостей (CONTEXT.md).

**Scale/Scope**: 3 роли экранов (гость/пользователь/оператор), ~9 представлений; демо-нагрузка.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Конституция — незаполненный шаблон, принципов нет → нарушений нет. Проектные правила соблюдены: без новых зависимостей, без роутера (одностраничное переключение), русские тексты. Пост-дизайн перепроверка: соответствует.

## Project Structure

### Documentation (this feature)

```text
specs/002-web-interface/
├── plan.md              # Этот файл
├── research.md          # Phase 0
├── data-model.md        # Phase 1: view-модели (ссылки на data-model 001)
├── quickstart.md        # Phase 1
├── contracts/
│   └── ui-map.md        # Phase 1: экраны ↔ эндпоинты API 001
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
frontend/
├── src/
│   ├── main.jsx            # без изменений
│   ├── App.jsx             # корневой: сессия, переключение представлений
│   ├── api.js              # fetch-обёртка (credentials, ошибки → русские сообщения)
│   ├── App.css             # тёмная тема scaffold + стили новых блоков
│   ├── components/
│   │   ├── StatusBoard.jsx    # лампочки сервисов (переиспользуется в админке и форме)
│   │   ├── PriorityBadge.jsx  # значок приоритета critical/high/medium/low
│   │   ├── RouteBadge.jsx     # значок маршрута: авто/БЗ/справка/оператор
│   │   └── Spinner.jsx        # «Агент разбирает обращение…»
│   └── views/
│       ├── LoginView.jsx      # вход по email (FR-001..003)
│       ├── RequestView.jsx    # форма + диалог + примеры (FR-010..014)
│       ├── CabinetView.jsx    # мои обращения + мои заказы справок (FR-020..022)
│       ├── AdminQueueView.jsx # очередь + баннер инцидента (FR-030, FR-037)
│       ├── AdminTicketView.jsx# карточка эскалации/обращения (FR-031, FR-036)
│       ├── AdminCertsView.jsx # заказы справок, смена статусов (FR-033)
│       ├── AdminToolsView.jsx # тест-панель (FR-034)
│       └── AdminMetricsView.jsx # виджет метрик (FR-035)
└── vite.config.js        # без изменений (прокси /api)
```

**Structure Decision**: Web application; фронт — существующий `frontend/` scaffold, старый `App.jsx` (форма сокращателя) заменяется. Роутера нет: `App.jsx` держит `view` в state (`login` / `request` / `cabinet` / `admin:*`); роль из `GET /api/auth/me` решает доступ к `admin:*`.

## Complexity Tracking

> Нарушений нет. Осознанное упрощение: отказ от react-router и state-менеджера — одна страница, 9 представлений, демо.
