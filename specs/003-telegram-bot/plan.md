# Implementation Plan: Telegram-бот поддержки МИСИС

**Branch**: `[003-telegram-bot]` | **Date**: 2026-09-12 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-telegram-bot/spec.md`

## Summary

Telegram-бот как тонкий канал поверх ядра (001): приём обращений текстом, привязка аккаунта к профилю по email МИСИС (код подтверждения, эмуляция), уточняющий диалог с сохранением контекста, заказ справок и просмотр статусов (`/certs`), мои обращения (`/status`), уведомления дежурному оператору об инцидентах и эскалациях. Бот — отдельный процесс на long polling по Telegram Bot API через `httpx`; вся продуктовая логика выполняется ядром, бот только переводит сообщения ↔ REST-вызовы по контракту `specs/001-misis-support-assistant/contracts/api.md`. План рассчитан на отдельного разработчика без контекста остальных фич: достаточно этого пакета + указанного контракта.

## Technical Context

**Language/Version**: Python 3.12 (тот же `backend/.venv`, что и ядро)

**Primary Dependencies**: `httpx >=0.27` (уже установлен). Новых зависимостей НЕТ: ни aiogram, ни других фреймворков (обоснование — research.md R1).

**Storage**: нет собственной; состояние привязок и диалогов — в таблицах ядра (`tg_links`, `outbound_messages`, см. data-model 001) через REST-эндпоинты `/api/internal/*`.

**Testing**: `pytest` для разборщика команд и форматтеров сообщений (юнит, без сети); ручной прогон в тестовом чате по quickstart.md.

**Target Platform**: локальный процесс `python -m app.bot.main` на той же машине, что и ядро; исходящий HTTPS до `api.telegram.org`.

**Project Type**: cli/демон (long polling воркер).

**Performance Goals**: реакция на сообщение ≤ 60 c (ограничено ядром, SC-001 спеки 003); уведомление дежурному ≤ 1 мин после создания инцидента (SC-003) — обеспечивается циклом опроса outbox каждые 10 c.

**Constraints**: токен бота — `TG_BOT_TOKEN` в `backend/.env` (LESSONS L004, не коммитим); внутренний токен `BOT_INTERNAL_TOKEN` для `/api/internal/*`; русские тексты сообщений; без webhook (long polling, нет публичного домена).

**Scale/Scope**: 1 бот, 1 дежурный чат; команды `/start /help /status /certs`; 4 состояния привязки/диалога; десятки пользователей в демо.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Конституция — незаполненный шаблон, принципов нет → нарушений нет. Проектные правила соблюдены: ноль новых зависимостей, секреты в `.env`, русские тексты. Пост-дизайн перепроверка: соответствует.

## Project Structure

### Documentation (this feature)

```text
specs/003-telegram-bot/
├── plan.md              # Этот файл
├── research.md          # Phase 0: long polling vs фреймворки, Bot API
├── data-model.md        # Phase 1: состояния и данные (через ядро)
├── quickstart.md        # Phase 1: завести бота у @BotFather и прогнать сценарии
├── contracts/
│   └── bot-contract.md  # Phase 1: ПОЛНЫЙ контракт бота — команды, тексты,
│                        #   клавиатуры, сценарии, маппинг на REST ядра
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
backend/app/bot/
├── main.py          # точка входа: цикл long polling (getUpdates, offset в памяти/файле),
│                    #   диспетчер update → handler; цикл опроса outbox ядра (10 c)
├── tg.py            # клиент Telegram Bot API: getUpdates, sendMessage, editMessageText,
│                    #   answerCallbackQuery; ретраи при 429/5xx; разбиение >4096 символов
├── core_api.py      # клиент REST ядра: /api/auth/*, /api/requests*, /api/certs/*,
│                    #   /api/internal/tg/link*, /api/internal/outbound* (X-Bot-Token)
├── handlers.py      # маршрутизация: команды, текст, callback_query; state machine tg_links
├── messages.py      # ВСЕ тексты сообщений бота (константы, рус.) — правятся только здесь
└── keyboards.py     # inline-клавиатуры: каталог справок, подтверждение, «статус заказа»
```

Тесты: `backend/tests/test_bot_handlers.py` — разбор команд и форматирование без сети (мок `tg.py` и `core_api.py`).

**Structure Decision**: бот живёт внутри `backend/` (тот же venv и `.env`), но запускается отдельным процессом от uvicorn — asyncio-конфликтов нет, деплой не нужен (демо локально). Никакого кода ядра бот не импортирует — только HTTP: связность исключительно через `core_api.py`, что позволяет разработчику 003 работать, не читая код ядра.

## Complexity Tracking

> Нарушений нет. Ключевое осознанное решение: отказ от aiogram в пользу raw Bot API — аргументы и отклонённые альтернативы в research.md R1.
