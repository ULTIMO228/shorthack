# План фазы 5 / US3 — RAG-агент по документам и шаблонам (T028–T031)

Спека: `specs/001-misis-support-assistant/` (spec.md, contracts/api.md, data-model.md, quickstart.md, research.md).
Фаза строится на завершённой фазе 4: `tools.call_tool`/`ToolCall`/`ToolError`/`CHECK_TIMEOUT_SEC`
в `backend/app/tools.py` подтверждены на диске, набор фазы 4 зелёный (109 passed).

**Важно: тесты T028 уже написаны.** `backend/tests/test_kb.py` (17 тест-функций) существует
в рабочем дереве и сейчас падает на коллекции (`from app import kb` — модуль не создан).
Этот файл — зафиксированный контракт фазы: реализация T029–T031 подгоняется под него,
а не наоборот. Ниже по тексту «контракт тестов» = точные ожидания `test_kb.py`.

---

## 1. Цель и критерий готовности

**Цель**: подзадача с маршрутом `kb` обрабатывается RAG-агентом: запрос переформулируется
и при необходимости делится на подзапросы (FR-031), выполняется retrieval top-3 по
эмбеддингам с косинусной близостью (порог 0.5, R3) и keyword-деградацией; итоговый ответ
собирается LLM строго по найденным документам с цитированием источников. Пустая выдача
или недоступность модели → эскалация оператору, а не выдуманный ответ (FR-032, SC-006).

**Критерий готовности (независимая проверка US3)**:
- `cd backend && .venv/bin/python -m pytest tests/test_kb.py -v` — зелёный
  (сейчас: ошибка коллекции, модуль `app.kb` отсутствует).
- `cd backend && .venv/bin/python -m pytest tests/ -q` — весь набор зелёный,
  базовая линия на момент плана: **109 passed, 0 failed** (фаза 4 завершена).
- quickstart.md сценарий 4: `POST /api/requests {"text":"Как подключиться к eduroam с телефона?"}`
  → `route=kb`, реакция `answer` с источником «Регламент Wi-Fi сетей»; вопрос вне базы
  → реакция `escalated`, `ticket.escalated=true` (покрыто `test_quickstart_scenario_4_kb_and_out_of_scope`).
- Реакция ≤ 60 c (SC-001): kb_agent делает ≤3 LLM-вызова (переформулировка, сплит
  подзапросов, синтез) с таймаутом 25 c + 1 вызов `embed` — укладывается.

**Границы фазы**: новых эндпоинтов и миграций БД нет. Шаблоны (`kind="template"`) в
retrieval не участвуют — иначе в ответы утекут незаполненные `{{placeholders}}`.

---

## 2. Точки интеграции

| Файл:функция | Что меняется |
|---|---|
| `backend/app/kb.py` (новый) | Косинус, константы, `retrieve()`, keyword-деградация, `index_article()` (для фазы 9) |
| `backend/app/tools.py:TOOLS` | Запись `kb_agent` (`type="llm"`, FR-020) + функция `kb_agent()` + Pydantic-схема сплита подзапросов |
| `backend/app/agent.py:build_graph` | Ребро из `ensure_ticket` (сейчас agent.py:528-531): маршрут `kb` тоже идёт в `execute_route` |
| `backend/app/agent.py:node_execute_route` | Ветка `kb` после общей проверки лимита (agent.py:388-389) и постановки подзадачи «в работе» (agent.py:393-394), до `tools.resolve_check_tool` (agent.py:395) |
| `backend/app/agent.py` (новая `node_execute_kb`) | Вызов `kb_agent`, реакция `answer`/`escalated`, статусы, `tool_calls` |
| `backend/app/schemas.py:ReactionOut` | **Добавить поле `sources`** (иначе FastAPI/Pydantic v2 отрежет его при валидации ответа — тесты читают `reaction["sources"]` из JSON ответа) |
| `backend/app/agent.py` (docstring модуля и `node_execute_route`) | Убрать «ветки kb достраиваются в US3», описать ветку kb |

**Не трогаем**: `models.py` (KbArticle готов), `seed.py` (`index_pending_articles` индексирует
при старте; тесты используют `KB_ARTICLES` как данные), `routers/requests.py`, `triggers.py`,
`tickets.py`, `llm.py`.

**Подтверждённый контракт диспетчера (диск, фаза 4)**: `tools.call_tool(db, name, params,
*, ticket_id=None, subtask_id=None, tool_calls=0) -> ToolCall` (поля `result: dict`,
`tool_calls: int`), `tools.ToolError`, `tools.CHECK_TIMEOUT_SEC=5.0`. Функции вызываются
как `fn(db, **params)`; журнал `tool_call`/`tool_result` пишет диспетчер (FR-061) —
`test_kb_agent_journal_events` требует ровно `["tool_call", "tool_result"]` от вызова
`call_tool`, нода дублировать журналирование вызова не должна.

Побочный эффект: `kb_agent` в общем реестре автоматически попадёт в `GET /api/admin/tools`
(фаза 10) — отдельной работы не требуется.

---

## 3. Пошаговый план по задачам

### T029 — `backend/app/kb.py` (новый модуль)

Докstring модуля — на русском, стиль соседних модулей (R3: JSON-векторы в
`kb_articles.embedding`, косинус без numpy, top-k=3, порог 0.5).

Константы и тип (имена зафиксированы тестами):

```python
SIMILARITY_THRESHOLD: float = 0.5  # R3, порог релевантности, сравнение >=
TOP_K: int = 3                     # R3

@dataclass(frozen=True)
class RetrievalHit:
    """Один найденный источник; score — косинус (векторный режим) или число совпавших
    токенов (keyword-режим)."""
    article: KbArticle  # тесты читают hit.article.title
    score: float
    degraded: bool = False  # True — найден keyword-режимом
```

Функции:

```python
def cosine(a: list[float], b: list[float]) -> float:
    """Косинус на чистом Python (math.sqrt). Нулевой вектор → 0.0 (без ZeroDivisionError)."""

def _tokenize(text: str) -> list[str]:
    """Нижний регистр, буквы ru+en, отброс стоп-слов (как, к, с, по, не, и, в, на,
    что, какой, от, где, вкусно…) и токенов < 3 символов. Для keyword-режима токены
    дополнительно усекаются до 5 символов (лёгкий стемминг): «телефона»/«телефоне» →
    «телеф», «подключиться»/«Подключение» → «подкл». Без этого тест
    test_keyword_fallback_without_embeddings не найдёт регламент Wi-Fi (score ≥ 2)."""

def retrieve(db: OrmSession, query: str) -> list[RetrievalHit]:
    """Топ-3 подтверждённых документа, сортировка по убыванию score, граница >= 0.5.

    Режимы (контракт тестов):
    1) В БД есть ≥1 подтверждённый документ (confirmed=true, kind="document") с
       непустым embedding → ВЕКТОРНЫЙ: один llm.embed(query, kind="query"), косинус
       по каждому проиндексированному документу; документы без embedding пропускаются
       (test_unindexed_articles_wait_for_indexing); битый JSON/чужая размерность → skip.
       LLMUnavailable от embed → keyword-режим (деградация R3).
    2) Индексированных документов НЕТ → KEYWORD без единого вызова embed
       (test_keyword_fallback_without_embeddings считает вызовы: assert calls == []):
       скоринг = число различных токенов запроса, найденных в title+body; только
       score ≥ 1; top-3; degraded=True.
    Черновики (confirmed=false) в обоих режимах исключены (test_retrieval_skips_unconfirmed_drafts).
    Шаблоны (kind!="document") не участвуют — защита от {{placeholders}} в ответах."""

def index_article(db: OrmSession, article: KbArticle) -> bool:
    """Одиночная индексация: llm.embed(f"{title}\n{body}", kind="doc") → JSON в колонку.
    LLMUnavailable → False (без raise). В фазе 5 используется тестами и пригодится
    фазе 9 (подтверждение статьи оператором, T043); seed.py не переписываем."""
```

### T030 — инструмент `kb_agent` в `backend/app/tools.py`

Дифф к существующему файлу. Импорт `from app import kb` — в верхний блок.

**Дисциплина маркеров промптов (критично!)**: тестовый фейк-LLM
(`test_kb.py:make_kb_fake_llm`) диспетчеризует по подстрокам промпта в порядке:
`"Переведи"` → перевод; `'"subtasks"'` → сплиттер; `'"queries"'` → подзапросы;
`"Переформулируй"` → переформулировка; `"Фрагменты базы знаний"` → финальный ответ;
иначе — классификатор. Промпты kb_agent обязаны:
- переформулировка: содержать «Переформулируй», НЕ содержать `"queries"`, `"subtasks"`,
  «Переведи», «Фрагменты базы знаний»; ответ — простой текст (через `llm.chat`);
- сплит подзапросов: содержать подстроку `"queries"` (например, пример JSON
  `{"queries": ["..."]}`), НЕ содержать `"subtasks"` и «Переведи»; ответ валидируется
  Pydantic-схемой через `llm.chat_structured`;
- синтез: содержать «Фрагменты базы знаний».

Шаги `kb_agent(db: OrmSession, question: str, context: str | None = None) -> dict`:

1. **Переформулировка**: `llm.chat(REWRITE_PROMPT)` → `reformulated: str`.
   `LLMUnavailable` → `return {"ok": False, "error": f"LLMUnavailable: {exc}"}`
   (контракт `test_kb_agent_llm_unavailable_is_error`: ok=False, подстрока «LLMUnavailable»).
2. **Сплит подзапросов**: `llm.chat_structured(SPLIT_PROMPT, QuerySplit)`, где
   `class QuerySplit(BaseModel): queries: list[str] = Field(min_length=1)`.
   При сбое → деградация на `[reformulated]` (R3).
3. **Retrieval с гейтом (ключевое решение, продиктованное тестами)**: сначала
   `hits_q = kb.retrieve(db, question)` по ИСХОДНОМУ вопросу. Если `hits_q` пуст —
   сразу `{"ok": True, "found": False, "answer": None, "sources": [], "queries": queries}`:
   переформулировка не имеет права «привнести» релевантность, которой нет у исходного
   вопроса (иначе `test_quickstart_scenario_4_kb_and_out_of_scope` сломан: фейк возвращает
   одинаковую переформулировку «eduroam телефон подключение» для вопроса про секцию
   плавания, и поиск по ней нашёл бы регламент Wi-Fi). Если `hits_q` непуст — объединяем
   с `kb.retrieve(db, q)` для каждого подзапроса, дедуп по `article.id` (max score),
   сортировка desc, top-3. Это же сохраняет верность FR-031: составной вопрос собирается
   по подзапросам (`test_kb_agent_composite_question_uses_subqueries` ждёт источники
   обоих тем и `len(result["queries"]) == 2`).
4. **Синтез**: `llm.chat(ANSWER_PROMPT)` — «ответь строго по фрагментам, цитируй документы
   по названию, если ответа нет — скажи прямо». `LLMUnavailable` → `{"ok": False, "error": ...}`.
5. Успех: `{"ok": True, "found": True, "answer": answer,
   "sources": [{"id": a.id, "title": a.title, "score": s} ...], "queries": queries}`.
   Весь dict JSON-сериализуем (уходит в payload событий FR-061).

Весь инструмент — **один** вызов по счётчику FR-021, сколько бы LLM-вызовов внутри ни было
(`test_kb_agent_answer_with_sources`: `call.tool_calls == 1`).

Регистрация (после `check_wifi`):

```python
TOOLS["kb_agent"] = {
    "type": "llm",
    "fn": kb_agent,
    "schema": {"question": "string", "context": "string | null"},
    "description": "RAG-ответ по документам БЗ: переформулировка, подзапросы, top-3 с порогом 0.5",
}
```

### T031 — нода маршрута `kb` в `backend/app/agent.py`

1. `build_graph()` (agent.py:528-531): маршрут `kb` тоже направляется в `execute_route`:

```python
graph.add_conditional_edges(
    "ensure_ticket",
    lambda state: (
        "execute_route"
        if state.get("route") in ("auto_check", "kb")
        else "escalate"
    ),
)
```

2. `node_execute_route()`: после общей проверки лимита (agent.py:388-389) и постановки
   подзадачи «в работе» (agent.py:393-394), до `tools.resolve_check_tool` (agent.py:395):

```python
if subtask.route == "kb":
    return node_execute_kb(state)
```

Проверка лимита единая на входе: `tool_calls >= MAX_TOOL_CALLS` → эскалация до ветвления,
инструмент не вызывается (FR-021).

3. Новая функция `node_execute_kb(state)` (рядом с `node_execute_route`):

```python
def node_execute_kb(state: AgentState) -> dict[str, Any]:
    """Маршрут kb (US3): RAG-ответ из БЗ либо безопасная эскалация (FR-032/FR-013)."""
    db = state["db"]
    subtask = db.get(Subtask, state["subtask_ids"][state["subtask_index"]])
    ticket = db.get(Ticket, state["ticket_id"])
    call = tools.call_tool(
        db, "kb_agent",
        {"question": subtask.summary, "context": state["work_text"]},
        ticket_id=ticket.id, subtask_id=subtask.id,
        tool_calls=state.get("tool_calls", 0),
    )
    result = call.result
    if result.get("ok") and result.get("found") and result.get("answer"):
        reaction = {"kind": "answer", "text": result["answer"],
                    "sources": result.get("sources", [])}
        subtask.status = tickets.STATUS_RESOLVED
        db.commit()
        tickets.set_status(db, ticket, tickets.STATUS_RESOLVED)  # FR-015: новая/в работе → решена
        log_event(db, ticket_id=ticket.id, actor="agent", action="reaction_sent",
                  payload={"kind": "answer", "sources": result.get("sources", [])})
        return {"reactions": state.get("reactions", []) + [reaction],
                "subtask_index": state["subtask_index"] + 1,
                "tool_calls": call.tool_calls}
    # found=False — пустая выдача (FR-032); ok=False — LLM недоступен (FR-013)
    reason = result.get("error") or "пустая выдача"
    subtask.route_reason = f"{subtask.route_reason} (RAG: {reason})"  # FR-012
    db.commit()
    updates = node_escalate(state)      # escalated=True, реакция из TEMPLATE_ESCALATION
    updates["tool_calls"] = call.tool_calls  # вызов инструмента состоялся — счётчик растёт
    return updates
```

Пояснения:
- `question=subtask.summary` (суть подзадачи от сплиттера — в пайплайн-тестах это эхо
  текста обращения), `context=state["work_text"]` — оба уже есть в `AgentState`.
- Статус подзадачи «в работе» уже выставлен входной нодой; успех → «решена», тикет
  «решена» (переход NEW→RESOLVED разрешён `tickets.TRANSITIONS`).
- Реакция `answer` несёт `sources` — поэтому меняем `schemas.py` (см. п. 4).
- Приоритет не трогаем: правил повышения для kb нет, арбитраж FR-011 отработал в triggers;
  понижение/повышение здесь запрещены.
- Гостевые сессии: проверок авторизации нет — БЗ доступна гостю (FR-016); ветка
  `auth_required` в `node_escalate` срабатывает только для `route == "cert_order"`.
- Идемпотентность: kb_agent read-only, повторный прогон в диалоге (reply) безопасен.

4. `backend/app/schemas.py` — расширить `ReactionOut`:

```python
class ReactionSource(BaseModel):
    id: int
    title: str
    score: float | None = None

class ReactionOut(BaseModel):
    kind: str
    text: str
    sources: list[ReactionSource] | None = None  # RAG-ответы (US3)
```

Обязательно: Pydantic v2 по умолчанию игнорирует лишние ключи — без поля `sources` в
схеме FastAPI отрежет его из JSON-ответа и `reaction["sources"]` в тестах будет KeyError.

5. Docstring модуля agent.py: убрать «Ветки kb (RAG) … достраиваются в фазах US3–US4»,
в описании `node_execute_route` упомянуть ветку kb.

### T028 — `backend/tests/test_kb.py` (уже написан — не переписывать)

Файл существует и полон. Реализационный порядок фазы: **T029 → T030 → T031**, после
каждого шага прогон `pytest tests/test_kb.py -q` (красный → зелёный). Опциональные
дополнения к T028 (по согласованию, в рамках той же задачи): тест гостевого доступа
к route=kb (FR-016), тест лимита `tool_calls=3` для ветки kb, тест исключения шаблонов
из retrieval. Без них критерии готовности уже достигаются.

---

## 4. Тесты (T028) — что именно фиксирует контракт

Моки: `app.llm.chat` подменяется `make_kb_fake_llm` (диспетчеризация по маркерам
промпта, см. раздел 3, T030); `app.llm.embed` — лямбдами с фиксированными векторами;
`llm.LLMUnavailable` имитируется фейком с `fail_on_reformulate=True`. Реальных сетевых
вызовов нет; БД — временная SQLite из `conftest.db_engine`, статьи создаются фикстурами
(`seed_kb_docs` берёт 5 документов из `app.seed.KB_ARTICLES` без эмбеддингов,
`_add_article` — с заданными векторами).

Покрытие (17 тестов, все должны быть зелёными):
- **Косинус/pорог**: top-3 с сортировкой и точными значениями (1.0/0.7071/0.5774);
  граница «ровно 0.5» включается (`>=`); 0.354 и нулевой/отрицательный векторы отброшены.
- **Фильтры выдачи**: черновики `confirmed=false` исключены; непроиндексированные
  пропускаются в векторном режиме.
- **Keyword-деградация**: режим без единого вызова embed; регламент Wi-Fi находится
  по «как подключиться к eduroam с телефона?» с score ≥ 2; пересечений нет → `[]`.
- **Реестр**: `kb_agent` в `tools.TOOLS`, тип `llm`, callable, схема непустая.
- **kb_agent**: ответ с источниками (`sources[0].title == "Регламент Wi-Fi сетей"`,
  есть `id`, `tool_calls == 1`); составной вопрос → 2 подзапроса и источники обеих тем;
  пустая выдача → `ok=True, found=False, answer=None, sources=[]` (эскалацию решает нода);
  LLMUnavailable → `ok=False` + «LLMUnavailable» в `error`; журнал `tool_call/tool_result`.
- **Пайплайн** (`POST /api/requests`): route=kb → реакция `answer` с `sources` в JSON,
  подзадача и тикет «решена»; вопрос вне базы → `escalated`, `ticket.escalated=true`;
  LLM недоступен → `escalated`; сценарий 4 quickstart целиком.

Опциональные hardening-тесты (см. конец раздела 3): гость, лимит tool_calls, шаблоны.

---

## 5. Риски и подводные камни

- **Pydantic-готча**: без расширения `ReactionOut` поле `sources` молча отсекается при
  валидации ответа (`extra="ignore"` по умолчанию) — тесты падают с KeyError, хотя нода
  его отдаёт. Раздел 3, п. 4 — обязательный пункт.
- **Маркеры промптов**: любая подстрока-пересечение с `"subtasks"`, `"queries"`,
  «Переведи», «Переформулируй», «Фрагменты базы знаний» в «чужом» промпте сломает
  диспетчеризацию фейка (и живой сплиттер/классификатор в пайплайне используют те же
  маркеры). Формулировки промптов — только по дисциплине из раздела 3 (T030).
- **Гейт retrieval на исходный вопрос**: не убирать. Поиск только по переформулировке
  ломает `test_quickstart_scenario_4_kb_and_out_of_scope` (фейк отдаёт одинаковую
  переформулировку для вопроса вне базы — по ней регламент Wi-Fi находится).
- **Keyword-режим не зовёт embed**: тест считает вызовы (`calls == []`). Решение о режиме
  принимается по наличию проиндексированных документов ДО вызова embed. Эмбеддинги и
  генерация — разные модели/эндпоинты: допустимы сценарии «embed жив, генерация нет» и
  наоборот; embed упал при живом индексе → keyword-деградация (R3).
- **Лёгкий стемминг обязателен**: без усечения токенов до 5 символов «телефона» ≠
  «телефоне» и требование score ≥ 2 не выполняется. Стоп-словы: «где», «как», «к»,
  «с»… иначе мусорные совпадения.
- **Лимит tool_calls=3 (FR-021)**: kb_agent — один вызов счётчика независимо от числа
  внутренних LLM-обращений; инкремент берём из `ToolCall.tool_calls`, а не `state+1`.
  Пустая выдача тоже съедает вызов (инструмент дёргался). Составное обращение с 4+
  kb-подзадачами: начиная с 4-й — эскалация через общую проверку на входе ноды.
- **Приоритет-арбитраж (FR-011)**: нода kb не меняет `subtask.priority` — к моменту
  execute_route триггеры уже применились; понижение/повышение здесь запрещены.
- **Статусная цепочка**: только `tickets.set_status` (валидирует FR-015, пишет журнал);
  прямые присваивания `ticket.status` запрещены. Здесь: NEW/IN_PROGRESS → «решена»
  (ответ) и NEW → «в работе» (эскалация через `node_escalate`) — оба разрешены.
- **Шаблоны в retrieval**: фильтр `kind="document"` защищает от `{{placeholders}}` в
  ответах (пinned-теста нет — опциональный hardening-тест из раздела 3).
- **Косинус**: нулевой вектор → 0.0, отрицательная близость естественно отсекается
  порогом; битый JSON в `embedding` → статья пропускается, retrieval не падает.
- **Кэш графа**: `get_graph()` с `@lru_cache` — тесты в свежем процессе получат новую
  сборку; при ручных экспериментах с uvicorn --reload перезапуск не нужен.
- **Индексация сидом**: в dev-окружении без ключа `index_pending_articles` оставляет
  статьи без embedding → runtime сразу работает в keyword-режиме (R3), после ввода
  ключей проиндексируется при перезапуске. Тесты этим не затронуты (свои статьи).
- **Скорость (SC-001)**: 3 LLM-вызова подряд добавляют до ~75 c теоретического
  максимума при таймаутах; на практике yandexgpt-lite отвечает за секунды — 60-секундный
  бюджет держится. При жёстких демо-сетях допустимо ужать сплит подзапросов
  (объединить с переформулировкой), но тогда ломается маркерная дисциплина тестов —
  только по согласованию.

---

## 6. Валидация

```bash
# точечно — тесты фазы (сейчас: ошибка коллекции app.kb; цель — зелёный)
cd backend && .venv/bin/python -m pytest tests/test_kb.py -v

# регрессия — весь набор (базовая линия на момент плана: 109 passed, 0 failed;
# фаза 5 не добавляет красных)
cd backend && .venv/bin/python -m pytest tests/ -q

# ручная приёмка сценария 4 (нужен живой YANDEX_API_KEY в backend/.env)
cd backend && .venv/bin/uvicorn app.main:app &
curl -c jar -b jar -X POST http://localhost:8000/api/requests \
  -H 'Content-Type: application/json' \
  -d '{"text":"Как подключиться к eduroam с телефона?","channel":"web"}'
# → subtasks[0].route == "kb", reactions[0].kind == "answer",
#   reactions[0].sources[0].title == "Регламент Wi-Fi сетей", ticket.status == "решена"
curl -c jar -b jar -X POST http://localhost:8000/api/requests \
  -H 'Content-Type: application/json' \
  -d '{"text":"Как записаться в секцию по плаванию?","channel":"web"}'
# → reactions[0].kind == "escalated", ticket.escalated == true
```

Чекпоинт фазы (tasks.md): сценарий 4 quickstart'а зелёный; T028 green.
