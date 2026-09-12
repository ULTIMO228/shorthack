# Phase 1 — Data Model: веб-интерфейс (002)

Собственного хранилища нет. View-модели — прямое отображение DTO из `specs/001-misis-support-assistant/contracts/api.md` и сущностей `specs/001-misis-support-assistant/data-model.md`.

## Состояние App.jsx (client state)

| Поле | Тип | Источник |
|---|---|---|
| session | `{ user: {id, email, full_name, group_name, role} } \| null` | `GET /api/auth/me` при старте; `POST /api/auth/login` |
| view | `login \| request \| cabinet \| admin.queue \| admin.ticket \| admin.certs \| admin.tools \| admin.metrics` | локальное |
| selectedRequestId | number \| null | клик в очереди/кабинете |

## View-модели представлений

- **LoginView**: форма `{email}` → POST login; ошибка 400 → текст под полем.
- **RequestView**:
  - `examples: [{label, text}]` — захардкожено (FR-014): сайт / LMS / Wi-Fi / неполное письмо / несколько вопросов / расшифровка звонка / заказ справки / мат+английский.
  - `result` ← ответ `POST /api/requests`: `subtasks[]` (badges по `priority`, `route`, `route_reason`), `reactions[]`, `ticket`.
  - `dialog: [{role, text, at}]` ← `GET /api/requests/{id}`; ввод активен при `ticket.status === "ждёт ответа пользователя"`.
- **CabinetView**: `requests[]` ← `GET /api/requests`; `orders[]` ← `GET /api/certs/orders`; `catalog[]` ← `GET /api/certs/catalog` (заказ кнопкой → `POST /api/certs/orders`). Статусы заказов — 4 значения FR-042 ядра, отображаются цепочкой-прогрессом.
- **AdminQueueView**: `queue[]` ← `GET /api/admin/queue` (polling 5 c); сортировка ядра (priority → created_at); баннер при `incidents.active > 0` (`GET /api/admin/incidents`).
- **AdminTicketView**: ← `GET /api/admin/escalations/{request_id}` (для эскалаций) и/или `GET /api/requests/{id}`: саммари, `checks[]`, `recommendation`, `why_escalated`, `raw_text` + `translation`, `events[]` (журнал). Действия: `close {resolution, add_to_kb}` → при `kb_draft` показать черновик с кнопкой «Подтвердить» (`POST /api/admin/kb/articles/{id}/confirm`); при активном инциденте — «Уведомить затронутых» (`POST /api/admin/incidents/{id}/broadcast`).
- **AdminCertsView**: `orders[]` ← `GET /api/admin/certs/orders`; кнопка «Следующий статус» → `PATCH /api/admin/certs/orders/{id}` (409 → тост с текстом ошибки).
- **AdminToolsView**: `tools[]` ← `GET /api/admin/tools`; вызов → `POST /api/admin/tools/{name}/invoke` с формой по `params[]`; блок переключателей Wi-Fi (`PATCH /api/admin/services/{id}`); кнопка «Волна жалоб» = invoke `simulate_wave {service, count:3}`; вывод результата JSON-ом.
- **AdminMetricsView**: ← `GET /api/admin/metrics` (polling 5 c): три карточки (% автоматизации, среднее время реакции, инциденты).
- **StatusBoard** (компонент): ← `GET /api/admin/status-board`; лампочка по `state`/`last_check.ok`.

## Правила отображения (из спеки 002)

- FR-011: маршрут, приоритет, обоснование и подзадачи видны в результате разбора всегда.
- FR-013: `reaction.kind === "outage_notice"` → красная плашка «Сбой известен, работы ведутся», отдельно от обычных ответов.
- FR-031: карточка эскалации читается ≤ 30 c: порядок блоков — саммари → проверки → рекомендация → почему эскалировано → исходник/перевод → журнал (свёрнут по умолчанию).
- Сессия переживает F5: при старте `GET /api/auth/me`; 401 → `login`.
