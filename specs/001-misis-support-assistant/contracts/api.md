# Phase 1 — REST-контракт ядра (001)

Единый контракт для веб-фронта (002) и Telegram-бота (003). Формат: JSON, UTF-8. Аутентификация: cookie `session_id` (HttpOnly). **Гостевой режим (FR-016)**: вход не обязателен — при первом обращении без логина создаётся гостевая сессия (cookie); эндпоинты уровня «почта» помечены «только авторизованный» — гостю отвечают 401 с предложением войти. Веб ходит через Vite-прокси `/api` → `:8000`; бот — напрямую на `:8000`. Ошибки: `{ "detail": "текст по-русски" }` со статусами 400/401/403/404/409/422/500.

## Аутентификация

### POST /api/auth/login
Вход (эмуляция, без пароля).
```json
→ { "email": "ivanov@misis.ru" }
← 200 { "user": { "id": 1, "email": "ivanov@misis.ru", "full_name": "Иванов Иван", "group_name": "ИТ-24", "role": "student" } }
   + Set-Cookie: session_id=<token>; HttpOnly; SameSite=Lax
← 400 { "detail": "Нужна корпоративная почта МИСИС (@misis.ru или @edu.misis.ru)" }
```
Новый email с правильным доменом → пользователь создаётся автоматически (ФИО из локальной части email).

### POST /api/auth/logout → 204. Удаляет сессию.

### GET /api/auth/me
```json
← 200 { "user": {…}, "guest": false }        // авторизован
← 200 { "user": null, "guest": true }        // гость (сессия есть или будет создана)
```

## Обращения (пользователь)

### POST /api/requests
Подача обращения. **Доступно гостю** (при отсутствии сессии создаётся гостевая cookie-сессия). Запускает конвейер агента синхронно и возвращает результат первой реакции.
```json
→ { "text": "Не работает Wi-Fi MISIS-EDU в 4 корпусе", "channel": "web" }
   channel: "web" | "telegram" (бот ставит "telegram")
← 200 {
  "request_id": 12,
  "duplicate": false,                  // true — привязано к открытой заявке (FR-014)
  "subtasks": [
    { "id": 31, "position": 1, "summary": "Недоступен Wi-Fi MISIS-EDU",
      "service": "wifi_edu", "category": "availability",
      "route": "auto_check", "route_reason": "Жалоба на доступность сети",
      "priority": "high", "confidence": 0.91, "status": "в работе" }
  ],
  "reactions": [
    { "kind": "clarification",         // "answer" | "clarification" | "outage_notice" | "cert_ordered" | "escalated" | "auth_required"
      "text": "Уточните, пожалуйста: ошибка видна на всех устройствах или только на одном?" }
  ],
  "ticket": { "id": 7, "number": "SUP-2026-0007", "status": "ждёт ответа пользователя", "escalated": false }
}
← 422 (пустой текст)
```

`auth_required` — маршрут требует авторизации (напр. cert_order от гостя, FR-016): `{ "kind": "auth_required", "text": "Для заказа справки войдите по корпоративной почте МИСИС" }`; действие НЕ выполняется, заказ не создаётся; после входа пользователь повторяет запрос.

### POST /api/requests/{id}/reply
Ответ пользователя на уточняющий вопрос (диалог, ≤ 2 раундов — FR-025). Доступно автору обращения: авторизованному или гостю той же сессии.
```json
→ { "text": "На всех устройствах" }
← 200 { "reactions": [ … ], "ticket": { … } }   // тот же формат реакций
← 409 { "detail": "Диалог по обращению завершён" }
```

### GET /api/requests — мои обращения. **Только авторизованный** (история — персональная возможность, FR-016); гостю: `← 401 { "detail": "История обращений доступна после входа по почте МИСИС" }`.
```json
← 200 [ { "id": 12, "created_at": "…", "channel": "web", "masked_text": "…",
          "ticket": { "number": "SUP-2026-0007", "status": "…" },
          "subtasks": [ { "service": "…", "category": "…", "priority": "…" } ] } ]
```

### GET /api/requests/{id} — детально: обращение + подзадачи + диалог + журнал. Доступно автору (пользователь или гостевая сессия) и оператору.
```json
← 200 { "id": 12, "raw_text": "…", "masked_text": "…", "lang": "ru", "translation": null,
        "subtasks": [ … ], "dialog": [ { "role": "user|agent", "text": "…", "at": "…" } ],
        "ticket": { … }, "events": [ { "actor": "…", "action": "…", "payload": {…}, "created_at": "…" } ] }
← 403 (чужое обращение и не оператор) | 404
```

## Справки (пользователь)

### GET /api/certs/catalog — публичный (внутри сервиса).
```json
← 200 [
  { "type": "payments", "title": "Справка о выплатах", "description": "Начисленная стипендия и другие выплаты. Документ с подписью и печатью на бланке вуза." },
  { "type": "callup",   "title": "Справка-вызов", "description": "Учебный отпуск на период аттестации и защиты ВКР (предоставляется работодателю)." },
  { "type": "medical",  "title": "Справка для переболевших / медотвода / вакцинации", "description": "…" },
  { "type": "study",    "title": "Справка с места учёбы", "description": "Подтверждает факт обучения; электронный PDF с ЭЦП в ЛК." },
  { "type": "military", "title": "Справка в военкомат", "description": "Отсрочка от призыва на период обучения (по приложению №4)." }
]
```

### POST /api/certs/orders — **только авторизованный** (FR-043).
```json
→ { "cert_type": "study" }
← 200 { "order": { "id": 5, "cert_type": "study", "title": "Справка с места учёбы",
                   "status": "не обработана", "created_at": "…" } }
← 401 { "detail": "Для заказа справки войдите по корпоративной почте МИСИС" }   // гость
← 422 (неизвестный тип)
```

### GET /api/certs/orders — мои заказы.
```json
← 200 [ { "id": 5, "cert_type": "study", "title": "…", "status": "обрабатывается", "created_at": "…", "updated_at": "…" } ]
```

## Админка (оператор; `role=operator`)

### GET /api/admin/queue — очередь обращений.
```json
← 200 [ { "request_id": 12, "number": "SUP-2026-0007", "created_at": "…", "channel": "web",
          "user": { "full_name": "…", "email": "…" },
          "summary": "Недоступен Wi-Fi MISIS-EDU", "service": "wifi_edu", "category": "availability",
          "route": "auto_check", "route_reason": "…", "priority": "high",
          "status": "ждёт ответа пользователя", "escalated": false, "incident_id": null } ]
```
Сортировка: priority (critical→low), затем created_at.

### GET /api/admin/escalations/{request_id} — карточка эскалации (FR-060).
```json
← 200 { "request": { …как GET /api/requests/{id}… },
        "summary": "Пользователь сообщает о недоступности Wi-Fi MISIS-EDU…",
        "checks": [ { "service": "wifi_edu", "ok": false, "checked_at": "…", "note": "сеть в состоянии «сбой»" } ],
        "recommendation": "Передать сетевой группе; пользователю отправлено извещение о сбое.",
        "why_escalated": "Подтверждён сбой сервиса / уверенность 0.42 < 0.6 / триггер …" }
```

### POST /api/admin/escalations/{request_id}/close
```json
→ { "resolution": "Перезапущен контроллер точки доступа", "add_to_kb": true }
← 200 { "ticket_status": "закрыта",
        "kb_draft": { "id": 44, "title": "…", "body": "…", "confirmed": false } | null }
```

### POST /api/admin/kb/articles/{id}/confirm — подтвердить черновик → `confirmed=true` + индексация (embedding). ← 200 `{ "id": 44, "confirmed": true }`

### GET /api/admin/certs/orders — все заказы справок.
```json
← 200 [ { "id": 5, "cert_type": "study", "title": "…", "status": "не обработана",
          "user": { "full_name": "…", "email": "…", "group_name": "…" }, "created_at": "…" } ]
```

### PATCH /api/admin/certs/orders/{id}
```json
→ { "status": "обрабатывается" }   // только следующий по цепочке FR-042
← 200 { "order": { … } }
← 409 { "detail": "Переход «забрана» → «обрабатывается» невозможен" }
```

### GET /api/admin/status-board — состояние сервисов.
```json
← 200 [ { "name": "misis.ru", "check_type": "real", "state": "up", "last_check": { "ok": true, "http_code": 200, "latency_ms": 183, "checked_at": "…" } },
        { "name": "MISIS-EDU", "check_type": "emulated", "state": "down", "last_check": null } ]
```

### PATCH /api/admin/services/{id} — переключение эмулируемого сервиса.
```json
→ { "state": "down" }   // только для check_type=emulated; ← 409 для real
← 200 { "service": { … } }
```

### Тест-панель (инструменты)

### GET /api/admin/tools — реестр инструментов.
```json
← 200 [ { "name": "check_site", "type": "exec", "description": "HTTP-проверка misis.ru",
          "params": [ { "name": "url", "type": "string", "default": "https://misis.ru" } ] }, … ]
```

### POST /api/admin/tools/{name}/invoke
```json
→ { "params": {} }        // check_site | check_lms | check_wifi {network} | simulate_wave {service, count}
← 200 { "tool": "check_site", "ok": true, "result": { "http_code": 200, "latency_ms": 183 } }
   simulate_wave: создаёт count (дефолт 3) однотипных обращений от разных тестовых пользователей →
   { "ok": true, "result": { "incident_id": 2 | null, "created_requests": [13,14,15] } }
← 404 (нет такого инструмента) | 422 (параметры)
```

### Инциденты

### GET /api/admin/incidents — список (active первыми).
```json
← 200 [ { "id": 2, "service": "wifi_edu", "category": "availability", "request_count": 3,
          "window_start": "…", "status": "active", "notified_at": "…", "broadcast_at": null } ]
```

### POST /api/admin/incidents/{id}/broadcast — массовый ответ затронутым (FR-053).
```json
→ { "text": null }        // null → шаблон из БЗ «массовый ответ при инциденте»
← 200 { "sent": 3, "broadcast_at": "…" }
```

### POST /api/admin/incidents/{id}/resolve ← 200 `{ "status": "resolved" }`

### Метрики

### GET /api/admin/metrics
```json
← 200 { "auto_closed_pct": 62.5,          // % заявок без человека (не escalated и решены)
        "avg_first_reaction_sec": 4.8,     // по журналу: created_at → первая реакция
        "incidents_total": 2, "incidents_active": 1,
        "requests_total": 16, "escalations_open": 3 }
```

## Служебное

### GET /api/health — публичный. ← 200 `{ "status": "ok", "llm": "up|down" }`

## Внутренние хуки для бота (003)

Отдельного API не требуется: бот использует только перечисленное (`/api/auth/login` служебной почтой пользователя после привязки, `/api/requests`, `/api/requests/{id}/reply`, `/api/certs/*`, `/api/requests`). Для уведомлений дежурному ядро пишет в `outbound_messages` — бот забирает поллингом:

### GET /api/internal/outbound?status=pending — (токен `BOT_INTERNAL_TOKEN` в заголовке `X-Bot-Token`)
```json
← 200 [ { "id": 9, "chat_id": 123456, "text": "🔴 Инцидент: MISIS-EDU недоступна. 3 обращения за 15 мин." } ]
```
### POST /api/internal/outbound/{id}/ack — `{ "status": "sent" | "failed" }`

### GET /api/internal/tg/link — `?chat_id=` → состояние привязки (`tg_links`).
### POST /api/internal/tg/link — `{ "chat_id", "email" }` → создаёт/обновляет привязку, возвращает `{ "ok": true, "confirm_code": "123456" }` (эмуляция: код показывается пользователю в ответе бота… нет — код приходит «на почту» = в демо показывается в админке и в ответе API; бот запрашивает его у пользователя).
### POST /api/internal/tg/link/confirm — `{ "chat_id", "code" }` → `{ "ok": true, "user": {…} }`.
