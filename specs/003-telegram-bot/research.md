# Phase 0 — Research: Telegram-бот (003)

## R1. Способ работы с Telegram Bot API

- **Decision**: raw Bot API по HTTPS через `httpx`: long polling `getUpdates(offset=last+1, timeout=30)` в бесконечном цикле; отправка `sendMessage`; клавиатуры `reply_markup` (inline_keyboard); колбэки `answerCallbackQuery`. Никаких фреймворков.
- **Rationale**: ноль новых зависимостей (httpx уже в requirements); разработчику 003 не нужно осваивать фреймворк — только 4 HTTP-метода; полный контроль таймаутов и ретраев; нет asyncio-конфликтов с uvicorn (бот — синхронный цикл в отдельном процессе).
- **Alternatives considered**: `aiogram 3` — стандарт индустрии, но новая зависимость + asyncio-кривая + избыточен для 4 команд и 2 клавиатур; `python-telegram-bot` — то же по сути; webhook — нужен публичный HTTPS-домен, в демо нет.

## R2. Long polling: детали надёжности

- **Decision**:
  - `getUpdates` с `timeout=30` (long poll), `allowed_updates=["message","callback_query"]`.
  - offset хранится в памяти и дублируется в файле `backend/.bot_offset` (не коммитим); при рестарте бот не получает старые апдейты повторно (Telegram сам подтверждает offset после первого запроса с ним).
  - Ошибки сети/5xx Telegram → backoff 1→2→4→…→60 c, без падения процесса.
  - HTTP 429 → читаем `retry_after` из ответа и спим указанное время.
  - Сообщения >4096 символов (ответы ядра) → разбиение по границе абзаца (`tg.py`).
- **Rationale**: демо не должно падать на сцене от мерцания Wi-Fi.
- **Alternatives considered**: хранение offset в БД ядра (лишний эндпоинт ради рестартов, которые в демо редки).

## R3. Связь с ядром

- **Decision**: только REST по контракту `specs/001-misis-support-assistant/contracts/api.md`: пользовательские эндпоинты (`/api/auth/*`, `/api/requests*`, `/api/certs/*`) + внутренние (`/api/internal/tg/link*`, `/api/internal/outbound*`) с заголовком `X-Bot-Token: <BOT_INTERNAL_TOKEN>`. Сессия ядра: бот держит по одной cookie-сессии на chat_id после привязки (login служебным вызовом `POST /api/auth/login` с email пользователя из `tg_links`).
- **Rationale**: бот — тонкий канал; вся логика (маршруты, диалоги, статусы) едина для веба и бота; разработчик 003 не читает код ядра.
- **Alternatives considered**: прямые вызовы функций ядра (импорт кода 001 — связывание, запрещено Structure Decision); прямой доступ к БД (нарушает единство логики).

## R4. Доставка уведомлений дежурному

- **Decision**: параллельный цикл в том же процессе: каждые 10 c `GET /api/internal/outbound?status=pending` → `sendMessage` каждому → `POST /api/internal/outbound/{id}/ack {"status":"sent"}` (при ошибке отправки — `failed`, ядро/оператор видят в журнале). Дежурный chat_id — из `.env` (`TG_DUTY_CHAT_ID`).
- **Rationale**: выполняет SC-003 (≤ 1 мин) без websocket'ов и вебхуков; очередь в ядре переживает рестарт бота.
- **Alternatives considered**: прямой push из ядра в Telegram (ядро начало бы знать о Telegram — нарушение «тонкого канала»).

## R5. Идентификация пользователя (эмуляция)

- **Decision**: сценарий привязки по состояниям `tg_links.state`: `awaiting_email` → `awaiting_confirm` → `idle`. Код подтверждения генерирует ядро (`POST /api/internal/tg/link`), в демо «письмо» не отправляется — код возвращается в ответе API и дублируется в админку; бот честно пишет пользователю «код отправлен на почту» (для демо код показывает оператор/разработчик). Подтверждение: `POST /api/internal/tg/link/confirm`.
- **Rationale**: соответствует спеке (эмуляция, без реальной отправки писем — Assumptions 003).
- **Alternatives considered**: без кода, доверие введённому email (проще, но теряется демонстрация «привязки как в жизни»).
