# Phase 9 / US7 — Самообучение базы знаний (T042–T043)

Фича: 001-misis-support-assistant. Зависимости: Phase 5 (kb.py, T029 — индексация/retrieval),
Phase 7 (routers/admin.py, T038 — close без KB), Phase 4 (tools.py — реестр и диспетчер).
План опирается на актуальные имена из кодовой базы (проверено по backend/app).

---

## 1. Цель и критерий готовности

Замкнутый цикл обучения базы знаний (FR-063, US7): оператор закрывает эскалацию с
`add_to_kb=true` → LLM готовит черновик статьи (title/body/topic) по переписке и
resolution → оператор подтверждает (`POST /api/admin/kb/articles/{id}/confirm`) →
статья индексируется embedding и находится RAG-агентом (kb.py из фазы 5).

Готово, когда:
- `POST /api/admin/escalations/{request_id}/close` с `add_to_kb=true` возвращает
  `kb_draft` с `confirmed=false` (статья в БД: `source="operator"`, `kind="document"`,
  `embedding=null`); тикет закрыт независимо от доступности LLM.
- `POST /api/admin/kb/articles/{id}/confirm` возвращает `{id, confirmed: true}`, статья
  получает embedding; повторный confirm — идемпотентен.
- При `LLMUnavailable` черновик собирается из шаблона (title/body/topic детерминированы),
  закрытие не падает.
- Новые тесты `backend/tests/test_kb_learning.py` зелёные; регрессионный прогон
  `pytest tests/ -q` не хуже базового состояния ветки.
- Ручная приёмка — quickstart сценарий 8: close → confirm → аналогичный вопрос
  отвечается RAG-агентом из новой статьи (маршрут `kb`, реакция `answer`).

## 2. Точки интеграции

| Файл:функция | Что меняется |
|---|---|
| `backend/app/tools.py` — реестр `TOOLS`, диспетчер (`dispatch`, целевая сигнатура по `tests/test_tools.py` — `call_tool(db, name, params, ticket_id=…, tool_calls=…)` → `ToolCall` с `.result`/`.tool_calls`) | Добавить инструмент `draft_kb_article` (type `llm`) + функцию `draft_kb_article()`. Подключение в реестр — пара строк, паттерн как у `check_site`/`check_wifi`. |
| `backend/app/routers/admin.py` — хендлер `close_escalation` (создан в фазе 7, T038, сейчас `kb_draft=null`) | Доработать: при `add_to_kb=true` собрать контекст, вызвать инструмент, создать `KbArticle`, вернуть `kb_draft`. Добавить хендлер `POST /api/admin/kb/articles/{id}/confirm`. |
| `backend/app/kb.py` (создаётся в фазе 5, T029) | Использовать его функцию индексации (плановая сигнатура `index_article(db, article)` — см. §3, T043). Если фаза 5 назвала её иначе — вызвать существующую, не дублировать код индексации. Запасной вариант: `seed.index_pending_articles(db)` уже умеет индексировать непроиндексированные подтверждённые статьи (`seed.py:162`). |
| `backend/app/schemas.py` | НЕ меняется: схемы уже есть — `EscalationCloseRequest` (resolution, add_to_kb=False, `schemas.py:222`), `KbDraftOut` (id/title/body/confirmed, `:227`), `EscalationCloseResponse` (ticket_status, kb_draft, `:234`), `KbConfirmResponse` (id, confirmed, `:239`). |
| `backend/app/models.py` (`KbArticle`, `models.py:154`) | НЕ меняется: все нужные поля есть (`kind`, `title`, `body`, `topic`, `embedding`, `source`, `confirmed`). |
| `backend/app/main.py` | НЕ меняется: роутер admin подключён в фазе 7; kb.py подключается в фазе 5. |
| `backend/tests/test_kb_learning.py` | Новый файл (см. §4). |

Не трогаем: `agent.py` (эскалации и RAG-нода не меняются — новая статья подхватывается
retrieval автоматически, т.к. индексация пишет `kb_articles.embedding`), `triggers.py`,
`tickets.py` (только вызываем `tickets.set_status`).

## 3. Пошаговый план по задачам

### T042 — Инструмент `draft_kb_article` (type llm) в backend/app/tools.py

**Реестр** (`TOOLS`, паттерн как у check_site, `tools.py:123`):

```python
"draft_kb_article": {
    "type": "llm",
    "fn": draft_kb_article,
    "schema": {"request_id": "integer", "resolution": "string"},
    "description": "Черновик статьи БЗ по переписке и resolution (FR-063)",
},
```

`type="llm"` — важно: `GET /api/admin/tools` (фаза 10) отдаёт тип из реестра,
для демо «где LLM, а где логика» инструмент должен быть помечен честно.

**Новая функция** (в tools.py; сигнатура под диспетчер — позиционный `db` + `**params`):

```python
def draft_kb_article(db: OrmSession, request_id: int, resolution: str) -> dict[str, Any]:
```

Поведение:
1. Собрать контекст: `request = db.get(Request, request_id)`; переписка — из журнала
   событий тикета заявки: события `action="dialog"` (payload `{role, text}`) и
   `action="reaction_sent"` (payload `{kind, text}`) через `select(Event)` по
   `ticket_id` (ticket — `request.tickets[0]` или `ensure_ticket`), упорядочить по
   `Event.id`. Плюс `request.masked_text` (для черновика используем маскированный
   текст, FR-002) и `subtasks.summary` — тема.
2. LLM-черновик — `llm.chat_structured(prompt, KbDraftResult)` с моделью
   `llm.generate_model_uri()` (дефолт `chat`/`chat_structured`), промпт: «по переписке
   обращения и решению оператора подготовь черновик статьи базы знаний: title одной
   строкой, body — инструкция для пользователей, topic из списка
   wifi/password/lms/certs/support/other». Pydantic-схема (локально в tools.py,
   паттерн `SplitResult` в `agent.py:151`):
   `KbDraftResult {title: str (min_length=1), body: str (min_length=1), topic: str}`.
3. Деградация: `except llm.LLMUnavailable` → `llm.LLMUnavailable` НЕ пролетает наружу
   (закрытие тикета не должно падать), возвращаем шаблонный черновик:
   - `title = f"Решение: {resolution}"` (обрезать до ~100 символов);
   - `body = f"Проблема: {summary первой подзадачи or masked_text}.\nРешение: {resolution}."`;
   - `topic` — первый подзадачи нет маппинга → дефолт `"other"` (или маппинг
     service→topic: wifi_*→wifi, lms→lms, account→password, certs→certs, иначе other);
   - добавить флаг `"draft_fallback": True` в результат (для тестов и журнала).
4. Результат диспетчера (паттерн `_record_check`): `{"ok": True, "title": …, "body": …,
   "topic": …, "draft_fallback": bool}`. Запись в `kb_articles` НЕ здесь — инструмент
   возвращает черновик, созданием статьи владеет роутер (разделение: инструмент
   чистый, создание сущности — в хендлере, как `cert_order` в фазе 6).

Журналирование `tool_call`/`tool_result` (actor `tool:draft_kb_article`) получаем
бесплатно через диспетчер (FR-061).

### T043 — close с add_to_kb=true + confirm в routers/admin.py и kb.py

**Эндпоинт 1 (расширение существующего, T038):** `POST /api/admin/escalations/{request_id}/close`

Тело — `EscalationCloseRequest` (`{resolution, add_to_kb}`). Коды: 200 / 401 (не
вошёл) / 403 (не оператор, `get_operator` из `app.auth`) / 404 (нет обращения или нет
тикета) / 409 (недопустимый переход статуса, `ValueError` из `tickets.set_status` →
`HTTPException(409)` — паттерн как в certs).

Алгоритм хендлера:
1. `request = db.get(Request, request_id)` → 404, если нет; `ticket` — тикет
   обращения (`request.tickets[0]` или `ensure_ticket`) → 404, если нет.
2. **Закрытие — в любом случае** (FR-063 не блокируется LLM): привести тикет к
   «решена», затем `tickets.set_status(db, ticket, tickets.STATUS_CLOSED, actor="operator")`.
   Открытые статусы — `tickets.OPEN_STATUSES` (`tickets.py:32`): если тикет в
   `новая/в работе/ждёт ответа пользователя`, сначала `set_status(…, STATUS_RESOLVED)`
   (переход «любой незакрытый → решена» разрешён `TRANSITIONS`), потом
   `set_status(…, STATUS_CLOSED)`. Если уже `закрыта` — идемпотентно: не плодить
   ошибку, вернуть текущий статус (опционально 409 — выбрать идемпотентный 200,
   см. §5). Если `решена` → просто `закрыта`.
3. `log_event(ticket_id, actor="operator", action="escalation_closed",
   payload={"resolution": …, "add_to_kb": …})`.
4. Если `add_to_kb=true`: вызов инструмента через диспетчер
   (`tools.call_tool(db, "draft_kb_article", {"request_id": …, "resolution": …},
   ticket_id=ticket.id, tool_calls=0)` — независимый счётчик, лимит FR-021 к
   вызовам агента в пайплайне отношения не имеет). Создать `KbArticle(kind="document",
   title, body, topic, source="operator", confirmed=False, embedding=None)`,
   `db.add` + `db.commit()`. Вернуть `kb_draft=KbDraftOut(id, title, body, confirmed=False)`.
5. Иначе `kb_draft=None`. Ответ — `EscalationCloseResponse(ticket_status=ticket.status,
   kb_draft=…)` (формат `contracts/api.md:123-128`).

**Эндпоинт 2 (новый):** `POST /api/admin/kb/articles/{id}/confirm`

Коды: 200 `{id, confirmed: true}` (`KbConfirmResponse`) / 401 / 403 / 404 (нет статьи).
Алгоритм: `article = db.get(KbArticle, id)` → 404; `article.confirmed = True`;
индексация — **через функцию фазы 5 из `app.kb`** (плановая сигнатура, T029):

```python
# kb.py (фаза 5) — используем готовую, если имена совпадают:
def index_article(db: OrmSession, article: KbArticle) -> bool: ...
#   embed(f"{title}\n{body}", kind="doc") → article.embedding = json.dumps(vector)
#   → True; LLMUnavailable → False (деградация, дозаполнение при следующем старте)
```

Импорт: `from app import kb` — если фаза 5 завершена, использовать `kb.index_article`;
фактическое имя уточнить по kb.py при реализации и не дублировать логику embed. Если
индексация вернула False (LLM down) — confirm всё равно успешен (статья подтверждена,
дозаполнится `seed.index_pending_articles` на следующем старте; retrieval в фазе 5
обязан уметь keyword-fallback для статей без embedding). Записать
`log_event(actor="operator", action="kb_article_confirmed", payload={"article_id": id,
"indexed": bool})` — `ticket_id=None` допустим (`log_event` это поддерживает).
Идемпотентность: повторный confirm той же статьи → 200 без повторного embed
(если `article.embedding is not None` — пропустить индексацию).

**SQL (критичные запросы):** сложных нет — прямые `db.get` по PK. Чтение переписки:
`select(Event).where(Event.ticket_id == ticket.id, Event.action.in_(("dialog",
"reaction_sent"))).order_by(Event.id)`. В retrieval-стороне (фаза 5) убедиться, что
выборка для поиска фильтрует `KbArticle.confirmed == True` (черновик до подтверждения
RAG-агенту не виден — иначе сценарий 8 подтверждения теряет смысл; если в фазе 5
фильтра нет — это правка-однострочник в kb.py, включаем её в T043).

## 4. Тесты

Новый файл `backend/tests/test_kb_learning.py`. Моки по паттерну conftest/test_tools.py:
`monkeypatch.setattr("app.llm.chat_structured", …)` и `"app.llm.embed"`;
сессии БД — фикстура-образец `db_session` из `tests/test_tools.py:62` (sessionmaker
поверх `db_engine`). Для HTTP-тестов — фикстура `client` + логин оператором через
`POST /api/auth/login` (оператор создаётся вручную: `User(role="operator")`, сид в
тестах не запускается). Для close нужен закрываемый тикет: `Request` + `Ticket` +
`Subtask` руками, либо через `agent.run_pipeline` с замоканным `llm.chat_structured`.

Кейсы:

- `test_close_without_add_to_kb_returns_null_draft` — happy path: close `{resolution,
  add_to_kb=false}` → 200, `kb_draft is None`, тикет «закрыта».
- `test_close_with_add_to_kb_creates_draft` — happy path: LLM-черновик (мок
  `chat_structured` → `KbDraftResult`-подобный ответ) → 200, `kb_draft.confirmed is
  False`; в БД статья `source="operator"`, `confirmed=False`, `embedding is None`,
  `kind="document"`; события `tool:draft_kb_article` (tool_call/tool_result) и
  `escalation_closed` в журнале.
- `test_close_uses_dialog_and_resolution_in_prompt` — мок читает prompt: содержит
  resolution и текст из событий `dialog`/`reaction_sent`.
- `test_close_add_to_kb_llm_unavailable_uses_template` — мок `chat_structured` бросает
  `llm.LLMUnavailable` → 200, черновик из шаблона (title содержит resolution, флаг
  fallback), тикет всё равно «закрыта».
- `test_close_sets_resolved_then_closed_from_open_status` — тикет «в работе» → после
  close «закрыта» (цепочка через «решена», FR-015).
- `test_close_non_operator_forbidden` — студент → 403; гость → 401.
- `test_close_unknown_request_404`.
- `test_close_closed_ticket_idempotent` — повторный close → 200 (или 409 — зафиксировать
  выбранное поведение в тесте, см. §5).
- `test_confirm_article_indexes_embedding` — happy path: мок `llm.embed` → вектор
  (например `[0.1]*256`); после confirm → 200 `{confirmed: true}`, статья
  `confirmed=True`, `embedding` — JSON с вектором; событие `kb_article_confirmed`.
- `test_confirm_retrieval_finds_article` — приёмка сценария 8: после confirm с моком
  `embed` retrieval из `app.kb` (фаза 5) возвращает новую статью в top-N по косинусу;
  до confirm — не возвращает (фильтр confirmed).
- `test_confirm_idempotent_no_second_embed` — повторный confirm → 200, `llm.embed`
  вызван один раз.
- `test_confirm_llm_unavailable_still_confirms` — мок `embed` бросает
  `LLMUnavailable` → 200, `confirmed=True`, `embedding is None` (дозаполнение при
  старте через `seed.index_pending_articles`).
- `test_confirm_unknown_article_404`; `test_confirm_non_operator_forbidden`.

## 5. Риски и подводные камни

- **Статусные цепочки и 409 (FR-015).** `tickets.set_status` валидирует переходы и
  бросает `ValueError` → хендлер ловит и отдаёт 409 с русским `detail`. Тикет
  эскалации после ноды `escalate` обычно «в работе»; закрытие идёт через «решена» →
  «закрыта». Не закрывать обходом `ticket.status = …` в обход `set_status` — иначе
  потеряем записи в журнале и проверку цепочки.
- **Идемпотентность close.** Контракт не фиксирует повторное закрытие: выбираем
  мягкий вариант (200 с текущим статусом, без повторного черновика) — демо-повторы
  curl не должны плодить дубли статей. Зафиксировать выбранное поведение тестом.
- **LLMUnavailable ≠ сбой закрытия.** FR-063: черновик обязан появиться, но тикет
  закрывается в любом случае. Все LLM-вызовы (`chat_structured`, `embed`) ловим,
  превращаем в деградацию (шаблон / confirm без индексации). Никаких необработанных
  исключений из хендлера — 500 недопустим.
- **Черновики не видны RAG до confirm.** Retrieval kb.py (фаза 5) должен фильтровать
  `confirmed == True`. Без этого сценарий 8 бессмысленен, а операторский драфт
  попадёт пользователям сырым. Проверить при реализации, добавить фильтр при
  необходимости.
- **Лимит tool_calls=3 (FR-021)** относится к вызовам агента в пайплайне; close —
  действие оператора вне графа, счётчик в state агента не трогаем. Вызов через
  диспетчер с `tool_calls=0` (или без счётчика — по фактической сигнатуре фазы 4),
  чтобы не спутать метрики.
- **Приоритет-арбитраж (FR-011)** не участвует: инструмент не меняет приоритеты
  подзадач. Если позже добавится правило «статья из эскалации → повысить приоритет
  темы», только raise через `triggers.raise_priority` (понижение запрещено).
- **Гостевые сессии (user_id null).** На close это не влияет (автор черновика —
  оператор), но в контекст черновика может попасть гостевое обращение — ок, мат уже
  замаскирован (`masked_text`), исходник в БД остаётся для оператора (FR-002).
- **Сигнатура диспетчера в переходном состоянии.** `tools.py` сейчас дорабатывается
  в фазе 4: код имеет `dispatch(db, ticket_id, name, params)`, тесты фазы 4 ожидают
  `call_tool(db, name, params, ticket_id=…, tool_calls=…)` с `ToolCall.result` /
  `.tool_calls` и `ToolError`. Перед стартом T042 сверить с фактическим кодом:
  регистрация инструмента одинакова в обоих вариантах, отличается только вызов из
  роутера — писать под актуальную сигнатуру, не рефакторить диспетчер в этой фазе.
- **Порядок фаз 5/7 перед 9.** kb.py и admin.py (close без kb) должны существовать.
  Если фазы ещё не сданы, T042 (инструмент) можно делать параллельно — он не зависит
  от них; T043 — только после.

## 6. Валидация

```bash
cd backend
# Новая фаза целиком:
.venv/bin/python -m pytest tests/test_kb_learning.py -v
# Регрессия по смежному (инструменты, агент, kb фазы 5, админка фазы 7):
.venv/bin/python -m pytest tests/test_tools.py tests/test_agent.py tests/test_kb.py tests/test_triggers.py -q
# Полный прогон:
.venv/bin/python -m pytest tests/ -q
```

Ручная приёмка (quickstart, сценарий 8): поднять `uvicorn app.main:app`, создать
эскалацию (вопрос вне базы) → `POST /api/admin/escalations/{id}/close {"resolution":"…",
"add_to_kb":true}` → `kb_draft` → `POST /api/admin/kb/articles/{id}/confirm` →
повторный `POST /api/requests` с аналогичным вопросом → реакция `answer` из новой
статьи.
