# Phase 1 — Quickstart: ядро (001)

## Запуск

```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
# .env (не коммитим, LESSONS L004):
#   YANDEX_API_KEY=...        # ключ от организаторов
#   YANDEX_FOLDER_ID=...      # каталог Yandex Cloud
#   YANDEX_MODEL_CLASSIFY=gpt://<folder>/yandexgpt-lite/latest
#   YANDEX_MODEL_GENERATE=gpt://<folder>/yandexgpt/latest
#   BOT_INTERNAL_TOKEN=<случайная строка>   # для /api/internal/* (фича 003)
.venv/bin/uvicorn app.main:app --reload   # :8000
```

При первом старте: создаются таблицы (data-model.md), сидируются 5 пользователей, 5 сервисов, документы и шаблоны БЗ (`app/seed.py`, идемпотентно). Сидирование статей включает вызов embeddings API — при недоступности API стартует в деградации keyword-поиска (R3).

## Проверка end-to-end (сценарии → ожидание)

Все вызовы: `curl -c jar -b jar http://localhost:8000/api/...` (cookie-сессия).

1. **Вход**: `POST /api/auth/login {"email":"ivanov@misis.ru"}` → 200 + cookie; `petrov@gmail.com` → 400.
2. **Простое обращение (Wi-Fi, сеть в норме)**: `POST /api/requests {"text":"Не работает вайфай MISIS-Guest","channel":"web"}` → `route=auto_check`, реакция `clarification`, тикет `ждёт ответа пользователя`. `POST /api/requests/{id}/reply` → продолжение разбора.
3. **Ветка сбоя**: `PATCH /api/admin/services/{id MISIS-EDU} {"state":"down"}` (вход оператором) → повторить обращение про MISIS-EDU → реакция `outage_notice` ≤ 2 c, приоритет `critical` (FR-011).
4. **RAG**: `POST /api/requests {"text":"Как подключиться к eduroam с телефона?"}` → `route=kb`, реакция `answer` по документу «Регламент Wi-Fi». Вопрос вне базы → реакция `escalated`.
5. **Справки**: `POST /api/certs/orders {"cert_type":"study"}` → заказ `не обработана`; оператором `PATCH /api/admin/certs/orders/{id}` → `обрабатывается`; у пользователя в `GET /api/certs/orders` статус обновлён.
6. **Дедупликация**: повторное обращение того же пользователя про ту же сеть → `duplicate=true`.
7. **Инцидент**: `POST /api/admin/tools/simulate_wave/invoke {"params":{"service":"wifi_edu","count":3}}` → создан инцидент, в `outbound_messages` появилось уведомление дежурному; `GET /api/admin/incidents` — active; `POST /api/admin/incidents/{id}/broadcast` → `sent ≥ 3`.
8. **Самообучение**: `POST /api/admin/escalations/{id}/close {"resolution":"…","add_to_kb":true}` → `kb_draft`; `POST /api/admin/kb/articles/{id}/confirm` → повторный вопрос по теме теперь отвечается из БЗ.

## Тесты

```bash
cd backend && .venv/bin/python -m pytest tests/ -v
```

LLM в тестах замокан (`app.llm.chat`/`embed` через pytest-фикстуру): классификатор отдаёт записанные JSON-ответы; реальные вызовы API в CI/тестах не выполняются.

## Границы (чего здесь нет)

Веб-интерфейс — фича 002 (`frontend/`), Telegram-бот — фича 003 (`backend/app/bot/`); этот quickstart проверяет только ядро через curl/pytest.
