# Phase 1 — Data Model: Telegram-бот (003)

Собственной БД у бота нет. Все данные — в таблицах ядра (детально: `specs/001-misis-support-assistant/data-model.md`), доступ через REST (contracts/api.md). Здесь — только то, что бот использует, плюс локальное состояние процесса.

## Данные ядра, используемые ботом

### tg_links — привязка Telegram-аккаунта (FR-003, FR-004)
| Поле | Смысл для бота |
|---|---|
| chat_id | ключ: все handler'ы начинаются с `GET /api/internal/tg/link?chat_id=` |
| user_id | null → пользователь не привязан (доступны только `/start`, `/help` и сценарий привязки) |
| state | `awaiting_email` → `awaiting_confirm` → `idle` → `dialog:<request_id>` (см. конечный автомат ниже) |
| confirm_code | код привязки (вводит пользователь) |

### outbound_messages — очередь исходящих (FR-008)
| Поле | Смысл для бота |
|---|---|
| chat_id | адресат: пользователь или дежурный (`TG_DUTY_CHAT_ID`) |
| text | готовый текст (ядро формирует саммари инцидента/эскалации само) |
| status | бот забирает только `pending`, затем ack → `sent`/`failed` |

### Остальное — по контракту ядра
- `requests`/`tickets` — создаются `POST /api/requests` (бот передаёт `channel: "telegram"`); статусы для `/status` — из `GET /api/requests`.
- `cert_orders` — `POST /api/certs/orders`, `GET /api/certs/orders`; каталог — `GET /api/certs/catalog`.

## Конечный автомат состояния чата (handlers.py)

```
                        ┌─────────────────────────────────────────┐
                        │  GET /api/internal/tg/link?chat_id      │
                        └─────────────────────────────────────────┘
   нет записи / user_id=null                user_id != null
          │                                        │
   ┌──────▼───────┐                          ┌─────▼──────┐
   │ awaiting_email│ ── email введён ──▶ ┌───┴─────────┐ │
   └──────────────┘                     │awaiting_confirm│ │
                                        └──────┬───────┘ │
                                           код верный      (неверный → повтор, 3 попытки → сброс в awaiting_email)
                                               ▼
                                        ┌─────────────┐   ответ на уточнение ядра    ┌────────────────────┐
                                        │    idle     │ ── POST /requests вернул ──▶ │ dialog:<request_id> │
                                        └──────┬──────┘   reaction=clarification     └─────────┬──────────┘
                                               ▲                                               │
                                               └──────── тикет не «ждёт ответа пользователя» ◀─┘
```

Правила:
- Любой текст в `idle` (не команда) → `POST /api/requests` (обращение). Ответ ядра с `reaction.kind=clarification` → state=`dialog:<request_id>`.
- Любой текст в `dialog:<request_id>` → `POST /api/requests/{id}/reply`; финальная реакция (не clarification) → state=`idle`.
- Команды `/start /help` работают в любом состоянии и НЕ сбрасывают `dialog` (подтверждение сброса — только `/cancel`: state=`idle`, текст «Текущее действие отменено»).

## Локальное состояние процесса бота (не в БД)

| Что | Где | Зачем |
|---|---|---|
| offset getUpdates | память + файл `backend/.bot_offset` | не получать старые апдейты после рестарта |
| cookie-сессии ядра | dict `{chat_id: httpx.Client}` в памяти | после рестарта бота — повторный login по tg_links (прозрачно) |
| backoff-счётчики | память | R2 research.md |

## Файл .env (backend/.env, не коммитим — LESSONS L004)

```
TG_BOT_TOKEN=123456:ABC...        # от @BotFather (quickstart.md)
TG_DUTY_CHAT_ID=123456789         # chat_id дежурного оператора
BOT_INTERNAL_TOKEN=<случайная>    # тот же, что в ядре
CORE_API_URL=http://localhost:8000
```
