# Phase 1 — UI-контракт: веб-интерфейс (002)

Карта «экран → эндпоинты API ядра» (`specs/001-misis-support-assistant/contracts/api.md`). Новых серверных интерфейсов фича не вводит.

## Общее

- Все запросы через `api.js`: `fetch(url, { credentials: "include", ... })`.
- Гость — валидное состояние: `GET /api/auth/me` → `{user:null, guest:true}`. 401 на персональных эндпоинтах (кабинет, заказ справки) → плашка с предложением войти по почте МИСИС; гостевая сессия НЕ сбрасывается.
- Тексты ошибок — из `error.detail` ядра (уже по-русски), без своей локализации.

## Карта экранов

| Экран (view) | Эндпоинты | Ключевые действия |
|---|---|---|
| `login` | `POST /api/auth/login` | вход опциональный (кнопка в шапке всегда доступна); показ ошибки домена; после входа открываются кабинет и справки |
| `request` | `POST /api/requests`, `POST /api/requests/{id}/reply`, `GET /api/requests/{id}` | доступен гостю; примеры-кнопки; результат с badges; диалог; плашка сбоя; плашка `auth_required` с кнопкой входа |
| `cabinet` | `GET /api/requests`, `GET /api/certs/catalog`, `POST /api/certs/orders`, `GET /api/certs/orders` | только после входа; гостю — заглушка с предложением войти; заказ из каталога; цепочка статусов; polling 5 c |
| `admin.queue` | `GET /api/admin/queue`, `GET /api/admin/incidents` | сортировка ядра; баннер инцидента; клик → `admin.ticket`; polling 5 c |
| `admin.ticket` | `GET /api/admin/escalations/{id}`, `GET /api/requests/{id}`, `POST /api/admin/escalations/{id}/close`, `POST /api/admin/kb/articles/{id}/confirm`, `POST /api/admin/incidents/{id}/broadcast`, `POST /api/admin/incidents/{id}/resolve` | саммари-блоки; закрытие ± «в БЗ»; подтверждение черновика; массовый ответ |
| `admin.certs` | `GET /api/admin/certs/orders`, `PATCH /api/admin/certs/orders/{id}` | таблица заказов; «следующий статус»; 409 → тост |
| `admin.tools` | `GET /api/admin/tools`, `POST /api/admin/tools/{name}/invoke`, `GET /api/admin/status-board`, `PATCH /api/admin/services/{id}` | форма по params реестра; переключатели Wi-Fi; «Волна жалоб» |
| `admin.metrics` | `GET /api/admin/metrics` | 3 карточки + счётчики; polling 5 c |
| StatusBoard (компонент) | `GET /api/admin/status-board` | лампочки; встроен в `admin.queue` и `admin.tools` |

## Контракт состояний UI

- `ticket.status === "ждёт ответа пользователя"` → поле ответа активно; иначе диалог read-only (409 ядра дублируется блокировкой ввода).
- `reaction.kind === "outage_notice"` → красная плашка; `clarification` → сообщение агента + активный ввод; `answer` → сообщение агента; `cert_ordered` → карточка «Заказ №N создан» + ссылка в кабинет; `escalated` → плашка «Передано специалисту»; `auth_required` → плашка «Требуется вход по почте МИСИС» + кнопка входа (действие не выполнено).
- Эмулируемый сервис `state=down` → лампочка красная независимо от last_check; real-сервис — по `last_check.ok`.
- Админские view: при `user.role !== "operator"` пункты меню скрыты (сервер всё равно вернёт 403 — показываем `detail`).

## Демо-скрипт (порядок экранов на сцене, ≤ 2 мин по SC-001/SC-007 002)

1. `login` → вход студентом.
2. `request` → пример «Wi-Fi не работает» → уточняющий диалог (сервис в норме).
3. `admin.tools` (оператором) → переключить MISIS-EDU в «сбой».
4. `request` → повторный пример → красная плашка извещения.
5. `admin.tools` → «Волна жалоб» → `admin.queue` → баннер инцидента → `admin.ticket` → «Уведомить затронутых».
6. `cabinet` студентом → заказ справки; `admin.certs` → смена статуса; `cabinet` → статус обновился.
