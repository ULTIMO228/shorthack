# Phase 6 / US4 — Заказ справок: план реализации (T032–T035)

Фича: 001-misis-support-assistant (ядро ИИ-помощника техподдержки МИСИС).
Основание: spec.md (US4, FR-040..FR-043, FR-016), contracts/api.md (раздел «Справки», админка),
data-model.md (таблица cert_orders), quickstart.md (сценарий 5), tasks.md (Phase 6).

---

## 1. Цель и критерий готовности

**Цель.** Заказ справки работает как действие агента end-to-end: авторизованный пользователь
просит справку текстом → агент определяет тип справки → оформляет заказ инструментом
`order_certificate` с автоподстановкой профиля (FR-040) → отвечает реакцией `cert_ordered`;
гость получает реакцию `auth_required` и заказ НЕ создаётся (FR-016/FR-043). Каталог из 5
справок отдаётся публично (FR-041). Заказы видны пользователю и оператору; оператор двигает
статус строго вперёд по цепочке «не обработана → обрабатывается → готова и ждёт выдачи →
забрана» (FR-042), нарушение — 409.

**Критерий готовности (всё зелёное):**
- `backend/.venv/bin/python -m pytest tests/test_certs.py -v` — все тесты T032 проходят;
- quickstart.md сценарий 5 проходит руками и в тесте: `POST /api/certs/orders {"cert_type":"study"}`
  → статус «не обработана» → `PATCH /api/admin/certs/orders/{id}` → «обрабатывается» →
  у пользователя `GET /api/certs/orders` показывает новый статус;
- существующие тесты не сломаны: `pytest tests/ -q` — зелёное (допускаем уже красные тесты
  фазы 4, T022/T026 — они чинятся в своей фазе, см. раздел «Риски»);
- SC-007: запрос → регламент → заказ со статусом ≤ 60 c (пайплайн не делает лишних LLM-вызовов:
  только split + classify, дальше детерминированно).

**Что НЕ входит в фазу** (явно): RAG-ответ по регламенту через `kb_agent` (фаза 5),
админ-очередь эскалаций (фаза 7), инциденты/метрики (фазы 8–10), веб-UI (фича 002).

---

## 2. Точки интеграции

| Файл | Функция/объект | Что меняется |
|---|---|---|
| `backend/app/certs.py` | — | **НОВЫЙ МОДУЛЬ** (T033): каталог, цепочка статусов, `create_order`, `resolve_cert_type`, `order_certificate` |
| `backend/app/tools.py` | `TOOLS` (реестр, tools.py:123), `dispatch`/`call_tool` | Регистрация инструмента `order_certificate` (type `exec`) — импорт fn из `app.certs` (T033) |
| `backend/app/routers/certs.py` | — | **НОВЫЙ ФАЙЛ** (T034): `GET /api/certs/catalog`, `POST /api/certs/orders`, `GET /api/certs/orders` |
| `backend/app/routers/admin.py` | — | **НОВЫЙ ФАЙЛ** (T034): `GET /api/admin/certs/orders`, `PATCH /api/admin/certs/orders/{id}`; в фазах 7–10 файл расширяется queue/escalations/incidents/metrics — только добавление |
| `backend/app/main.py` | `app.include_router(...)` (main.py:50-52) | +`include_router(certs_router.router)`, +`include_router(admin_router.router)` |
| `backend/app/agent.py` | `node_execute_route` (agent.py:356), `build_graph` (agent.py:482), условное ребро из `ensure_ticket` (agent.py:504-507) | Ветка `cert_order`: новая нода `node_cert_order`; ребро `ensure_ticket` направляет `cert_order` в `execute_route` (T035). `node_escalate` НЕ меняется — его ветка «cert_order + гость → auth_required» (agent.py:421-429) переиспользуется |
| `backend/app/models.py` | `CertOrder` (models.py:169-183) | Не меняется — таблица уже есть |
| `backend/app/schemas.py` | `CertCatalogItem`, `CertOrderCreate`, `CertOrderOut`, `CertOrderCreatedResponse`, `AdminCertUser`, `AdminCertOrderOut`, `AdminCertOrderPatch`, `AdminCertOrderResponse` (schemas.py:158-265) | Не меняются — схемы уже есть |
| `backend/tests/test_certs.py` | — | **НОВЫЙ ФАЙЛ** (T032) |
| `backend/app/seed.py` | — | Не меняется: документ «Регламент заказа справок» (topic `certs`) уже засеян (seed.py:39-52) |

**Зависимости:** T033 зависит от T023 (реестр инструментов); T034 — от T033; T035 — от T020
(граф) и T033. Фаза 6 НЕ зависит от фазы 5: регламент читается прямым запросом из
`kb_articles`, без `kb.py` (см. риски).

---

## 3. Пошаговый план по задачам

### T033 — `backend/app/certs.py`: каталог, цепочка статусов, инструмент `order_certificate`

Новый модуль. Комментарии — на русском, стиль — как в `tickets.py`. Функции и сигнатуры:

```python
# Каталог FR-041 — типы, названия и описания дословно из contracts/api.md.
# Ключи = допустимые значения cert_type (data-model.md: cert_orders.cert_type).
CERT_CATALOG: dict[str, dict[str, str]] = {
    "payments": {
        "title": "Справка о выплатах",
        "description": "Начисленная стипендия и другие выплаты. Документ с подписью "
                       "и печатью на бланке вуза.",
    },
    "callup": {
        "title": "Справка-вызов",
        "description": "Учебный отпуск на период аттестации и защиты ВКР "
                       "(предоставляется работодателю).",
    },
    "medical": {
        "title": "Справка для переболевших / медотвода / вакцинации",
        "description": "Справка для переболевших, для получения медотвода "
                       "или по факту вакцинации.",
    },
    "study": {
        "title": "Справка с места учёбы",
        "description": "Подтверждает факт обучения; электронный PDF с ЭЦП в ЛК.",
    },
    "military": {
        "title": "Справка в военкомат",
        "description": "Отсрочка от призыва на период обучения (по приложению №4).",
    },
}

# Цепочка FR-042 — индексы задают допустимые переходы (только +1, только вперёд).
CERT_STATUSES: tuple[str, ...] = ("не обработана", "обрабатывается",
                                  "готова и ждёт выдачи", "забрана")

# Детерминированное распознавание типа справки из текста (для ноды cert_order).
# Порядок ключей важен: сначала более специфичные формулировки.
CERT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "study": ("место учёбы", "места учёбы", "обучени"),
    "payments": ("выплат", "стипенди"),
    "callup": ("вызов", "учебный отпуск", "аттестац", "защит"),
    "medical": ("переболев", "медотвод", "вакцин", "медицин"),
    "military": ("военкомат", "призыв", "отсрочк"),
}
```

Сигнатуры:

```python
def catalog_items() -> list[dict[str, str]]:
    """Каталог для GET /api/certs/catalog: [{'type','title','description'}, ...]
    в порядке CERT_CATALOG (5 позиций, FR-041)."""

def title_of(cert_type: str) -> str:
    """Название справки для ответов; неизвестный тип → KeyError."""

def is_known_type(cert_type: str) -> bool:
    """cert_type входит в каталог FR-041."""

def can_transition(current: str, new: str) -> bool:
    """FR-042: переход возможен только к следующему статусу по цепочке
    (index(new) == index(current) + 1). Тот же статус — False; скачки и назад — False."""

def set_status(db: OrmSession, order: CertOrder, new_status: str,
               *, actor: str = "operator") -> None:
    """Проверка цепочки + перевод статуса + журнал status_change (FR-061).
    Нарушение → ValueError('Переход «{current}» → «{new}» невозможен') —
    роутер превращает в 409 (паттерн tickets.set_status, tickets.py:51-67)."""

def create_order(db: OrmSession, *, user_id: int, cert_type: str) -> CertOrder:
    """Создание заказа (FR-040): только авторизованный (user_id не None — иначе ValueError),
    только известный тип (иначе ValueError). Статус «не обработана». Коммит."""

def resolve_cert_type(text: str) -> str | None:
    """Тип справки из текста обращения/подзадачи по CERT_TYPE_KEYWORDS (нижний регистр).
    Совпадений нет или несколько разных типов → None (нода задаст уточняющий вопрос)."""

def get_regulation(db: OrmSession) -> KbArticle | None:
    """Документ «Регламент заказа справок» из kb_articles (topic='certs', kind='document').
    Прямой select — без kb.py (фаза 5). Нет документа (тесты без сида) → None."""

def order_certificate(db: OrmSession, *, user_id: int, cert_type: str) -> dict[str, Any]:
    """Инструмент заказа (FR-040, type exec): обёртка create_order для реестра tools.
    Возвращает {'ok': True, 'order_id', 'cert_type', 'title', 'status'}.
    ValueError от create_order пролетает наружу — диспетчер оборачивает (ToolError/ok=False)."""
```

**Регистрация в реестре (tools.py):** добавить импорт `from app.certs import order_certificate`
(циклического импорта нет: certs.py не импортирует tools) и в словарь `TOOLS` запись:

```python
"order_certificate": {
    "type": "exec",
    "fn": order_certificate,
    "schema": {"user_id": "integer", "cert_type": "string"},
    "description": "Оформление заказа справки из каталога (FR-040/041)",
},
```

**Контракт вызова инструмента.** Целевой контракт диспетчера фазы 4 (зафиксирован в
`tests/test_tools.py` и docstring agent.py:9): `tools.call_tool(db, name, params,
ticket_id=None, subtask_id=None, tool_calls=0) -> ToolCall` с полями `.result`, `.tool_calls`,
ошибки — `tools.ToolError`. Нода агента вызывает:
`tools.call_tool(db, "order_certificate", {"user_id": uid, "cert_type": t}, ticket_id=ticket.id, tool_calls=state.get("tool_calls", 0))`.
⚠️ В дереве на момент планирования `tools.py` ещё содержит промежуточный `dispatch(db,
ticket_id, name, params)` — см. раздел «Риски»: при расхождении вызов адаптируется одной
строкой, регистрация в `TOOLS` идентична.

---

### T034 — роутеры `routers/certs.py` + `routers/admin.py`

**`backend/app/routers/certs.py` (НОВЫЙ).** Паттерны — из `routers/requests.py`
(Annotated Depends, HTTPException с русским detail, response_model). Хелпер `_order_out`:
`CertOrderOut(id=..., cert_type=..., title=certs.title_of(...), status=..., created_at=..., updated_at=...)`.

1. `GET /api/certs/catalog` — **публичный** (без Depends на сессию; зависит только от get_session,
   в ответе не отражается). `response_model=list[CertCatalogItem]` → 200, 5 элементов.
2. `POST /api/certs/orders` — **только авторизованный** (FR-043). Depends `get_current_session`;
   если `session.user_id is None` → 401 с detail **дословно**
   `"Для заказа справки войдите по корпоративной почте МИСИС"` (contracts/api.md).
   Тело `CertOrderCreate`: неизвестный `cert_type` режется Literal Pydantic → 422 (код писать не нужно).
   `certs.create_order(db, user_id=session.user_id, cert_type=body.cert_type)` → 200
   `CertOrderCreatedResponse(order=...)`. Заказ связан с пользователем автоматически
   (профиль из сессии — FR-040).
3. `GET /api/certs/orders` — мои заказы; та же проверка авторизации (гость → 401 той же строкой).
   SQL: `select(CertOrder).where(CertOrder.user_id == session.user_id).order_by(CertOrder.created_at.desc())`
   → `list[CertOrderOut]`.

**`backend/app/routers/admin.py` (НОВЫЙ).** Все эндпоинты — `Depends(get_operator)`
(auth.py:72-77: неавторизован → 401, не оператор → 403). Файл в фазах 7–10 дополняется
(queue, escalations, incidents, metrics) — только добавление эндпоинтов.

1. `GET /api/admin/certs/orders` → `list[AdminCertOrderOut]`. SQL:
   ```python
   stmt = (select(CertOrder, User)
           .join(User, CertOrder.user_id == User.id)
           .order_by(CertOrder.created_at.desc()))
   ```
   Сборка: `AdminCertOrderOut(id, cert_type, title=certs.title_of(cert_type), status,
   user=AdminCertUser(full_name, email, group_name), created_at)`.
2. `PATCH /api/admin/certs/orders/{order_id}` (тело `AdminCertOrderPatch`).
   - `db.get(CertOrder, order_id)` → None: 404 `"Заказ не найден"`.
   - `certs.can_transition(order.status, body.status)` → False: 409 detail дословно
     `f"Переход «{order.status}» → «{body.status}» невозможен"` (формат из contracts/api.md).
   - Иначе `certs.set_status(db, order, body.status, actor="operator")` → 200
     `AdminCertOrderResponse(order=_order_out(order))`.

**`backend/app/main.py`**: импорты `from app.routers import admin as admin_router, certs as certs_router`
и два `app.include_router(...)` рядом с существующими (main.py:50-52).

---

### T035 — нода `cert_order` в `backend/app/agent.py` (диффы, не переписывание)

1. **Новая нода** `node_cert_order(state: AgentState) -> dict[str, Any]` (рядом с
   `node_execute_route`):

   ```python
   def node_cert_order(state: AgentState) -> dict[str, Any]:
       """Маршрут cert_order (US4, T035): регламент из БЗ → заказ → cert_ordered.

       Гость (user_id=None) → node_escalate: реакция auth_required, заказ не создаётся
       (FR-016/FR-043). Тип справки — детерминированно через certs.resolve_cert_type
       (без LLM); не распознан → уточняющий вопрос с вариантами каталога, заказ не создаётся.
       Заказ выполняется инструментом order_certificate через диспетчер tools (FR-040),
       вызов журналируется (FR-061), счётчик tool_calls растёт на 1 (FR-021).
       Успех → реакция cert_ordered, подзадача и заявка «решена».
       """
   ```

   Логика:
   - `if state.get("user_id") is None: return node_escalate(state)` — переиспользует
     существующую ветку auth_required (agent.py:421-429): реакция, подзадача «решена»,
     тикет «решена», журнал.
   - `cert_type = certs.resolve_cert_type(f"{state['work_text']}\n{subtask.summary}")`.
   - `cert_type is None` → реакция `{"kind": "clarification", "text": "Уточните, какая
     справка нужна: " + ", ".join(заголовки каталога) + "."}`; тикет → STATUS_WAITING_USER
     (как ветка нормы auto_check, agent.py:389-392); подзадача остаётся «в работе».
   - Иначе вызов инструмента через диспетчер (call_tool по целевому контракту фазы 4,
     см. T033; `ticket_id=ticket.id`). При `ok=False`/ToolError → `return node_escalate(state)`
     (FR-013: безопасный маршрут).
   - Успех → текст реакции `cert_ordered` (без LLM): «Заказ оформлен: {title} … Статус:
     «не обработана». …» + короткий фрагмент регламента из `certs.get_regulation(db)`
     (первые ~2 предложения body; документа нет — без фрагмента). Журнал
     `log_event(..., actor="agent", action="reaction_sent", payload=reaction)`;
     подзадача → STATUS_RESOLVED, тикет через `tickets.set_status(db, ticket, tickets.STATUS_RESOLVED)`.
   - Возврат, как у соседних нод:
     `{"reactions": state.get("reactions", []) + [reaction], "subtask_index": state["subtask_index"] + 1, "tool_calls": state.get("tool_calls", 0) + 1}`.
   - Импорт `from app import certs` в agent.py (рядом с `from app import llm, tickets, tools, triggers`, agent.py:30).

2. **Ветвление в `node_execute_route`** (agent.py:356-374): сразу после чтения subtask/ticket
   и перевода подзадачи в «в работе» добавить:
   ```python
   if subtask.route == "cert_order":
       return node_cert_order(state)
   ```
   (проверка лимита `tool_calls` сверху функции сохраняется — она применяется и к заказу, FR-021.)

3. **Ребро в `build_graph`** (agent.py:504-507) — расширить условие:
   ```python
   lambda state: "execute_route" if state.get("route") in ("auto_check", "cert_order") else "escalate"
   ```
   `node_escalate` и его cert-ветка не меняются — для гостя они остаются единственным
   путём (через `node_cert_order` → `node_escalate`) и защитным fallback, если классификатор
   отдал `cert_order` после триггеров.

4. **Docstring модуля** (agent.py:1-17): обновить фразу «Ветки kb (RAG) и cert_order (заказ)
   достраиваются в фазах US3–US4» → cert_order реализован (кратко, по-русски).

**Почему без LLM в ноде:** классификатор уже определил маршрут; тип справки извлекается
ключевыми словами — это детерминированно тестируется, укладывается в SC-007 и не тратит
вызовы модели. RAG-переформулировка (kb_agent) при необходимости добавится фазой 5 позже
— точка расширения: заменить `resolve_cert_type` на LLM-извлечение, контракт ноды не меняется.

---

## 4. Тесты (T032) — `backend/tests/test_certs.py`

**Что мокаем:**
- LLM — готовый хелпер `make_fake_llm` из `tests/test_agent.py` (паттерн импорта уже есть в
  `test_quickstart_us2.py:20`): для агент-тестов классификатор отдаёт
  `{"route": "cert_order", "service": "certs", "category": "cert_order", "priority": "low",
  "confidence": 0.95, "reason": "Запрос справки"}` (см. test_agent.py:159-161).
- Сеть не трогается (HTTP-инструменты фазы 4 в этой фазе не участвуют).
- Оператора создаём напрямую в БД до логина (паттерн test_agent.py:202-211: логин
  автосоздаёт только `student`, существующего пользователя не портит — routers/auth.py:36-39):
  ```python
  def make_operator(db_engine, email="smirnov@misis.ru"):
      maker = sessionmaker(bind=db_engine, expire_on_commit=False)
      db = maker(); db.add(User(email=email, full_name="Смирнов Олег", role="operator"))
      db.commit(); db.close()
  ```
- Фикстуры: `client`, `db_engine` из conftest.py; для unit-тестов certs — локальный
  `db_session` (копия фикстуры из test_tools.py:61-68).

**Кейсы и имена:**

*Каталог (публичность, FR-041):*
- `test_catalog_public_returns_five_types` — без логина 200; 5 элементов; множество типов
  == {payments, callup, medical, study, military}; у study title == «Справка с места учёбы»,
  description непустой; тип элемента — `CertCatalogItem`.

*Пользовательские эндпоинты (FR-043):*
- `test_create_order_authorized` — логин ivanov@misis.ru → POST study → 200, `order.status ==
  "не обработана"`, title из каталога; в БД строка с user_id залогиненного.
- `test_create_order_guest_401` — без логина → 401, detail == «Для заказа справки войдите по
  корпоративной почте МИСИС»; в БД `cert_orders` пусто (заказ НЕ создан, FR-043).
- `test_create_order_unknown_type_422` — логин + `{"cert_type": "diploma"}` → 422 (Pydantic Literal).
- `test_my_orders_returns_own_desc` — два пользователя, у каждого заказ → каждый видит
  только свой; порядок по created_at desc; поле updated_at присутствует.
- `test_my_orders_guest_401` — GET без логина → 401.

*Цепочка статусов (FR-042), admin:*
- `test_admin_patch_full_forward_chain` — оператор: «не обработана»→«обрабатывается»→
  «готова и ждёт выдачи»→«забрана», каждый раз 200 и новый статус в ответе; после каждого
  шага пользователь видит статус через GET /api/certs/orders (сценарий 5 quickstart).
- `test_admin_patch_backward_409` — из «обрабатывается» назад в «не обработана» → 409,
  detail == «Переход «обрабатывается» → «не обработана» невозможен».
- `test_admin_patch_skip_409` — «не обработана» → «забрана» (скачок) → 409.
- `test_admin_patch_same_status_409` — «не обработана» → «не обработана» → 409.
- `test_admin_patch_not_found_404`.
- `test_admin_list_orders` — в ответе блок `user` (full_name/email/group_name) заявителя.
- `test_admin_requires_operator` — гость → 401, студент → 403 (на обоих admin-эндпоинтах).

*Unit certs.py:*
- `test_resolve_cert_type_keywords` — параметризовано: «справка о выплатах»→payments,
  «стипендия»→payments, «справка-вызов»→callup, «в военкомат»→military, «медотвод»→medical,
  «справка с места учёбы»→study, «какой-то текст»→None.
- `test_cert_status_chain` — `can_transition`: только соседние вперёд; «забрана» → любой False.

*Агент (T035), end-to-end через POST /api/requests:*
- `test_agent_cert_order_authorized_creates_order` — залогинен, текст «Нужна справка с
  места учёбы» → 200; реакция `kind == "cert_ordered"`, в тексте «Справка с места учёбы» и
  «не обработана»; в БД заказ с user_id; тикет «решена», escalated false; в журнале события
  `tool:order_certificate` (tool_call + tool_result); subtask route_reason содержит обоснование.
- `test_agent_cert_order_guest_auth_required` — без логина, тот же classify-мок → реакция
  `auth_required` с текстом про корпоративную почту; заказов в БД нет; тикет «решена».
- `test_agent_cert_order_ambiguous_type_asks_clarification` — текст «нужна какая-то
  справка» (без ключевых слов) → реакция `clarification` со списком каталога; заказов нет;
  тикет «ждёт ответа пользователя».
- `test_quickstart_scenario_5_order_status_visible` — сквозной: POST /api/certs/orders
  (study) → «не обработана»; PATCH оператором → «обрабатывается»; GET /api/certs/orders у
  пользователя показывает «обрабатывается» (дословно сценарий 5 quickstart).

---

## 5. Риски и подводные камни

1. **⚠️ Контракт диспетчера инструментов (главный риск).** В дереве на момент планирования
   `tools.py` содержит промежуточный `dispatch(db, ticket_id, name, params)`, а целевой
   контракт фазы 4 — `call_tool(db, name, params, ticket_id=..., subtask_id=...,
   tool_calls=...) -> ToolCall` с `ToolError` (зафиксирован в tests/test_tools.py:89-134 и
   docstring agent.py:9; текущий прогон тестов фазы 4 красный — фаза в работе). План
   опирается на целевой контракт `call_tool`. При расхождении: вызов в `node_cert_order`
   адаптируется одной строкой (`tools.dispatch(db, ticket.id, "order_certificate",
   {"user_id": ..., "cert_type": ...})` и инкремент счётчика вручную); регистрация в `TOOLS`
   и сигнатура `order_certificate` не меняются. Перед стартом T035 убедиться, что фаза 4
   закрыта и контракт диспетчера стабилен.
2. **Статусная цепочка и 409.** Строки статусов — с «ё»: «готова и ждёт **выдачи**». Тот же
   статус в PATCH — это 409 (не idempotent-200): цепочка допускает только index+1.
   Pydantic-Literal в `AdminCertOrderPatch` отсекает невалидные строки до нашей логики → 422,
   это корректно. Формат сообщения 409 — дословно как в contracts/api.md:
   «Переход «{from}» → «{to}» невозможен».
3. **Гостевые сессии (user_id=None).** `get_current_session` авто-создаёт гостевую сессию —
   в POST/GET /api/certs/orders гость получит cookie и всё равно 401 (без создания заказа).
   В ноде проверка `state["user_id"] is None` стоит ДО `resolve_cert_type` и вызова
   инструмента — заказ физически не создаётся (FR-043). Ветка auth_required уже существует
   в `node_escalate` — не дублировать её текст, вызывать `node_escalate(state)`.
4. **Лимит tool_calls=3 (FR-021).** `order_certificate` — инструмент: проверка
   `MAX_TOOL_CALLS` в начале `node_execute_route` (agent.py:369-370) сохраняется и для
   cert_order; инкремент счётчика — в возврате ноды (как в auto_check, agent.py:412).
5. **Приоритет-арбитраж (FR-011).** Модель для cert_order обычно ставит low/medium —
   триггеры могут только повысить. Нода не трогает `subtask.priority` (в отличие от ветки
   сбоя auto_check) — понижение и повышение правилами не нарушаются.
6. **Дедупликация (FR-014).** Повторный cert-запрос того же авторизованного пользователя при
   открытой заявке склеится как duplicate ДО ноды — второго заказа не будет. Нормально:
   типичный флоу завершает тикет «решена», повторный запрос проходит заново.
7. **LLMUnavailable → деградация.** При недоступности модели classify уходит в escalate
   (agent.py:223-226) — до cert-ноды дело не доходит: безопасный маршрут, заказ не создаётся,
   пользователь получает escalated. Тесты фазы 3 это покрывают; отдельный сертификат-тест
   не нужен.
8. **Зависимость от фазы 5 отсутствует.** Регламент читается прямым `select` из kb_articles
   (`certs.get_regulation`); в тестах без сида документа нет — нода работает без фрагмента
   регламента. Не импортировать `kb.py`.
9. **Параллельность фаз 5–7.** `agent.py` — общий файл: ветка cert_order добавляется после
   того, как фаза 5 вольёт свою ветку kb (или до — конфликт минимален: одна строка в
   условии ребра + новая функция). `routers/admin.py` объявлен «новым файлом» и в этом
   плане, и в плане фазы 7 (`phase-07-us5-escalation.md`, T038) — правило координации:
   кто реализуется раньше, тот и создаёт файл (общий `APIRouter` с `tags=["admin"]`,
   каждый эндпоинт через `Depends(get_operator)`), второй только добавляет свои
   эндпоинты. Подключение в `main.py` — одна строка, конфликт тривиальный.
10. **Идемпотентность.** POST /api/certs/orders не идемпотентен по контракту (каждый вызов —
    новый заказ) — дублей-заказов в спеке нет; seed.py не трогаем, каталог — константа.
11. **Схемы уже существуют** (schemas.py) — не создавать дубликаты; `CertOrderOut.title`
    наполняется из `certs.CERT_CATALOG`, а не из БД (в cert_orders title нет — data-model.md).

---

## 6. Валидация

```bash
# фаза целиком (T032)
cd backend && .venv/bin/python -m pytest tests/test_certs.py -v

# регрессия соседних фаз (US1/US2-инструменты/триггеры)
cd backend && .venv/bin/python -m pytest tests/test_agent.py tests/test_triggers.py \
    tests/test_auth_internal_smoke.py tests/test_internal_edges.py -q

# полный прогон перед сдачей фазы
cd backend && .venv/bin/python -m pytest tests/ -q
```

Ручная приёмка (сценарий 5 quickstart.md, сервер `cd backend && .venv/bin/uvicorn app.main:app`):

```bash
curl -c jar -b jar -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"ivanov@misis.ru"}'
curl -c jar -b jar -X POST localhost:8000/api/certs/orders \
  -H 'Content-Type: application/json' -d '{"cert_type":"study"}'
# → 200 {"order": {"status": "не обработана", ...}}
curl -c jar_op -b jar_op -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"smirnov@misis.ru"}'
# (smirnov засеян оператором)
curl -c jar_op -b jar_op -X PATCH localhost:8000/api/admin/certs/orders/1 \
  -H 'Content-Type: application/json' -d '{"status":"обрабатывается"}'
curl -c jar -b jar localhost:8000/api/certs/orders
# → у пользователя статус «обрабатывается»
# агентный путь: POST /api/requests {"text":"Нужна справка с места учёбы"} → реакция cert_ordered
```
