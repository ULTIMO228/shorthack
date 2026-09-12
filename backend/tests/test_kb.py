"""База знаний и RAG-агент (US3, T028): retrieval, порог схожести, keyword-деградация.

Косинусный top-3 поиск с замоканным embed (порог 0.5, граница >=), keyword-fallback
при embedding=null (без единого вызова embed), пустая выдача → маршрут escalate
(FR-032). Сценарий 4 quickstart.md: вопрос по регламенту Wi-Fi → answer с источником
из БЗ; вопрос вне базы → escalated. LLM мокается по маркерам промпта.

Порядок маркеров важен: «Переведи» → перевод, '"subtasks"' → сплиттер (эхо текста
обращения как summary единственной подзадачи), '"queries"' → подзапросы kb_agent,
«Переформулируй» → переформулировка, «Фрагменты базы знаний» → финальный ответ,
иначе — классификатор.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import kb, llm, tools
from app.models import Event, KbArticle
from app.seed import KB_ARTICLES

# Заголовок регламента Wi-Fi из сида — источник ответа в сценарии 4 quickstart.md
WIFI_DOC_TITLE = "Регламент Wi-Fi сетей"


@pytest.fixture()
def db_session(db_engine):
    """Сессия на той же временной БД, что и client (conftest.db_engine) —
    пайплайн-тесты и проверки журнала видят одни данные."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()


@pytest.fixture()
def seed_kb_docs(db_session):
    """5 документов-регламентов из сида (без эмбеддингов — keyword-деградация)."""
    docs = [
        KbArticle(kind=kind, title=title, topic=topic, body=body,
                  source="seed", confirmed=True)
        for kind, title, topic, body in KB_ARTICLES
        if kind == "document"
    ]
    db_session.add_all(docs)
    db_session.commit()
    return docs


def _add_article(db_session, title: str, embedding: list[float] | None,
                 body: str = "текст статьи") -> KbArticle:
    article = KbArticle(
        kind="document", title=title, topic="other", body=body,
        source="seed", confirmed=True,
        embedding=json.dumps(embedding) if embedding is not None else None,
    )
    db_session.add(article)
    db_session.commit()
    return article


def make_kb_fake_llm(monkeypatch, *, classify: dict | None = None,
                     reformulated: str | None = None,
                     subqueries: list[str] | None = None,
                     answer: str = "Для подключения к eduroam используйте логин <почта>@edu.misis.ru.",
                     fail_on_reformulate: bool = False) -> dict:
    """Фейк app.llm.chat для RAG-ветки: диспетчеризация по маркерам промпта.
    Переформулировка по умолчанию — эхо вопроса (есть явный reformulated — он);
    подзапросы строятся по последнему результату переформулировки."""
    calls = {"count": 0, "last_reformulated": reformulated}

    def fake_chat(messages, *, model=None, temperature=0.2, timeout=25.0):
        calls["count"] += 1
        text = messages if isinstance(messages, str) else "\n".join(m["content"] for m in messages)
        if "Переведи" in text:
            return "Не работает Wi-Fi."
        if '"subtasks"' in text:
            # одиночная проблема — summary = сам текст обращения (как живой сплиттер)
            marker = "Текст обращения:\n"
            summary = text.split(marker, 1)[1].strip() if marker in text else "Единственная проблема"
            return json.dumps({"subtasks": [{"summary": summary}]}, ensure_ascii=False)
        if '"queries"' in text:
            payload = {"queries": subqueries or [calls["last_reformulated"] or "запрос"]}
            return json.dumps(payload, ensure_ascii=False)
        if "Переформулируй" in text:
            if fail_on_reformulate:
                raise llm.LLMUnavailable("модель недоступна (тест)")
            if reformulated is None:
                calls["last_reformulated"] = text.split("Вопрос:", 1)[1].strip()
            return calls["last_reformulated"]
        if "Фрагменты базы знаний" in text:
            return answer
        payload = classify or {
            "route": "kb", "service": "wifi_edu", "category": "howto",
            "priority": "medium", "confidence": 0.9,
            "reason": "Вопрос по регламенту подключения",
        }
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr("app.llm.chat", fake_chat)
    return calls


# ---------------------------------------------------------------------------
# T028/T029: косинусный retrieval — top-3, порог 0.5, замоканный embed
# ---------------------------------------------------------------------------

def test_cosine_retrieval_top3_with_threshold(db_session, monkeypatch):
    """Топ-3 по косинусу; порядок — по убыванию схожести; антиподобные (<0.5)
    и нулевой вектор отброшены."""
    # query-вектор = ось x; документы с заранее посчитанными косинусами
    _add_article(db_session, "d1", [1, 0, 0, 0])                    # 1.000
    _add_article(db_session, "d2", [1, 1, 0, 0])                    # ≈0.707
    _add_article(db_session, "d3", [1, 1, 1, 0])                    # ≈0.577
    _add_article(db_session, "d4", [1, 1, 1, 1])                    # = 0.500 (граница)
    _add_article(db_session, "d5", [-1, 0, 0, 0])                   # −1.0
    _add_article(db_session, "d6", [0, 0, 0, 0])                    # нулевой → 0.0
    monkeypatch.setattr("app.llm.embed", lambda text, **kw: [1.0, 0.0, 0.0, 0.0])

    hits = kb.retrieve(db_session, "любой запрос")

    assert [h.article.title for h in hits] == ["d1", "d2", "d3"]
    assert [h.score for h in hits] == pytest.approx([1.0, 0.7071, 0.5774], abs=1e-3)
    assert all(h.score >= kb.SIMILARITY_THRESHOLD for h in hits)


def test_cosine_exactly_at_threshold_is_included(db_session, monkeypatch):
    """Граничный случай: косинус ровно 0.5 — статья проходит (порог >=)."""
    _add_article(db_session, "ровно 0.5", [1.0, 1.0, 1.0, 1.0])
    monkeypatch.setattr("app.llm.embed", lambda text, **kw: [1.0, 0.0, 0.0, 0.0])  # cos = 0.5
    hits = kb.retrieve(db_session, "запрос")
    assert [h.article.title for h in hits] == ["ровно 0.5"]
    assert hits[0].score == pytest.approx(0.5)


def test_cosine_below_threshold_excluded(db_session, monkeypatch):
    """Статьи с косинусом < 0.5 в выдачу не попадают, даже если других нет."""
    _add_article(db_session, "слабо", [1, 1, 1, 1, 1, 1, 1, 1])  # cos = 1/√8 ≈ 0.354
    monkeypatch.setattr("app.llm.embed", lambda text, **kw: [1.0] + [0.0] * 7)
    assert kb.retrieve(db_session, "запрос") == []


def test_retrieval_skips_unconfirmed_drafts(db_session, monkeypatch):
    """Черновики (confirmed=false, FR-063) RAG-агенту не отдаются."""
    _add_article(db_session, "подтверждённая", [1, 0])
    db_session.add(KbArticle(kind="document", title="черновик", topic="other",
                             body="текст", source="operator", confirmed=False,
                             embedding=json.dumps([1, 0])))
    db_session.commit()
    monkeypatch.setattr("app.llm.embed", lambda text, **kw: [1.0, 0.0])
    hits = kb.retrieve(db_session, "запрос")
    assert [h.article.title for h in hits] == ["подтверждённая"]


def test_unindexed_articles_wait_for_indexing(db_session, monkeypatch):
    """В векторном режиме статьи без эмбеддинга пропускаются (дождутся индексации)."""
    _add_article(db_session, "с вектором", [1, 0])
    _add_article(db_session, "без вектора", None)
    monkeypatch.setattr("app.llm.embed", lambda text, **kw: [1.0, 0.0])
    hits = kb.retrieve(db_session, "запрос")
    assert [h.article.title for h in hits] == ["с вектором"]


def test_keyword_fallback_without_embeddings(db_session, monkeypatch, seed_kb_docs):
    """Деградация (R3): embedding=null у всех — поиск по токенам title+body,
    ни одного вызова embed."""
    calls = []

    def counting_embed(text, **kw):
        calls.append(text)
        return [1.0]

    monkeypatch.setattr("app.llm.embed", counting_embed)
    hits = kb.retrieve(db_session, "Как подключиться к eduroam с телефона?")
    assert calls == []  # keyword-режим — embed не нужен
    assert hits, "регламент Wi-Fi обязан находиться по токенам eduroam/телефон"
    top = hits[0]
    assert top.article.title == WIFI_DOC_TITLE
    assert top.score >= 2  # совпали несколько токенов запроса


def test_keyword_fallback_empty_when_no_overlap(db_session, seed_kb_docs):
    """Пересечения токенов нет — выдача пустая (не выдумываем ответ, FR-032)."""
    assert kb.retrieve(db_session, "столовая меню обеды") == []


# ---------------------------------------------------------------------------
# T030: инструмент kb_agent — переформулировка, подзапросы, ответ по источникам
# ---------------------------------------------------------------------------

def test_kb_agent_registered_in_tools():
    """FR-020: kb_agent в реестре с типом llm, схемой и вызываемой функцией."""
    assert "kb_agent" in tools.TOOLS
    assert tools.TOOLS["kb_agent"]["type"] == "llm"
    assert callable(tools.TOOLS["kb_agent"]["fn"])
    assert tools.TOOLS["kb_agent"]["schema"]


def test_kb_agent_answer_with_sources(db_session, monkeypatch, seed_kb_docs):
    """FR-031: найденные статьи → ответ строго по источникам + список источников."""
    make_kb_fake_llm(monkeypatch)
    call = tools.call_tool(db_session, "kb_agent", {"question": "Как подключиться к eduroam?"})

    result = call.result
    assert result["ok"] is True
    assert result["found"] is True
    assert "edu.misis.ru" in result["answer"]  # текст финального ответа из мока
    assert result["sources"][0]["title"] == WIFI_DOC_TITLE
    assert result["sources"][0]["id"]
    assert call.tool_calls == 1  # счётчик инструментов инкрементирован (FR-021)


def test_kb_agent_composite_question_uses_subqueries(db_session, monkeypatch, seed_kb_docs):
    """Составной вопрос (FR-031): агент разделяет запрос и собирает ответ по обеим темам."""
    make_kb_fake_llm(
        monkeypatch,
        subqueries=["пароль учётная запись", "eduroam телефон"],
        answer="Пароль единый для почты и LMS; eduroam настраивается как MISIS-EDU.",
    )
    call = tools.call_tool(
        db_session, "kb_agent",
        {"question": "Какой пароль от учётки и как подключить eduroam?"},
    )
    result = call.result
    assert result["found"] is True
    titles = {s["title"] for s in result["sources"]}
    assert WIFI_DOC_TITLE in titles
    assert "Политика паролей" in titles
    assert len(result["queries"]) == 2


def test_kb_agent_empty_result_signals_escalation(db_session, monkeypatch, seed_kb_docs):
    """FR-032: пустая выдача — found=false, без ответа; агент эскалирует."""
    make_kb_fake_llm(monkeypatch, reformulated="столовая обеды меню")
    call = tools.call_tool(db_session, "kb_agent", {"question": "Где вкусно пообедать?"})
    result = call.result
    assert result["ok"] is True
    assert result["found"] is False
    assert result["answer"] is None
    assert result["sources"] == []


def test_kb_agent_llm_unavailable_is_error(db_session, monkeypatch, seed_kb_docs):
    """FR-013: модель недоступна — ok=false (безопасный маршрут эскалации)."""
    make_kb_fake_llm(monkeypatch, fail_on_reformulate=True)
    call = tools.call_tool(db_session, "kb_agent", {"question": "Какой пароль?"})
    assert call.result["ok"] is False
    assert "LLMUnavailable" in call.result["error"]


def test_kb_agent_journal_events(db_session, monkeypatch, seed_kb_docs):
    """FR-061: вызов инструмента пишется в журнал событий."""
    make_kb_fake_llm(monkeypatch)
    tools.call_tool(db_session, "kb_agent", {"question": "eduroam?"})
    actions = [e.action for e in db_session.query(Event).order_by(Event.id).all()]
    assert actions == ["tool_call", "tool_result"]


# ---------------------------------------------------------------------------
# T031 + сценарий 4 quickstart.md: ветка route=kb в пайплайне
# ---------------------------------------------------------------------------

def test_kb_route_answer_via_pipeline(client, monkeypatch, db_session, seed_kb_docs):
    """route=kb → инструмент kb_agent → реакция answer с источниками; заявка «решена»."""
    make_kb_fake_llm(monkeypatch)
    resp = client.post(
        "/api/requests",
        json={"text": "Как подключиться к eduroam с телефона?", "channel": "web"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    sub = data["subtasks"][0]
    assert sub["route"] == "kb"
    reaction = data["reactions"][0]
    assert reaction["kind"] == "answer"
    assert "edu.misis.ru" in reaction["text"]
    assert reaction["sources"][0]["title"] == WIFI_DOC_TITLE
    assert data["ticket"]["status"] == "решена"
    assert sub["status"] == "решена"
    # вызов инструмента зафиксирован в журнале
    actions = [e.action for e in db_session.query(Event).order_by(Event.id).all()]
    assert "tool_call" in actions and "tool_result" in actions


def test_kb_route_empty_result_escalates(client, monkeypatch, db_session, seed_kb_docs):
    """FR-032: вопрос вне базы → пустая выдача → эскалация оператору, не выдумка."""
    make_kb_fake_llm(monkeypatch, reformulated="столовая обеды меню")
    resp = client.post(
        "/api/requests",
        json={"text": "Где ближайшая столовая?", "channel": "web"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reactions"][0]["kind"] == "escalated"
    assert data["ticket"]["escalated"] is True


def test_kb_route_llm_unavailable_escalates(client, monkeypatch, db_session, seed_kb_docs):
    """FR-013: недоступность модели в kb_agent — безопасная эскалация."""
    make_kb_fake_llm(monkeypatch, fail_on_reformulate=True)
    resp = client.post(
        "/api/requests",
        json={"text": "Как подключиться к eduroam с телефона?", "channel": "web"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reactions"][0]["kind"] == "escalated"
    assert data["ticket"]["escalated"] is True


def test_quickstart_scenario_4_kb_and_out_of_scope(client, monkeypatch, db_session, seed_kb_docs):
    """Сценарий 4 quickstart.md: вопрос по регламенту Wi-Fi → answer по документу
    «Регламент Wi-Fi сетей»; вопрос вне базы → реакция escalated."""
    make_kb_fake_llm(monkeypatch)

    covered = client.post(
        "/api/requests",
        json={"text": "Как подключиться к eduroam с телефона?", "channel": "web"},
    )
    assert covered.status_code == 200, covered.text
    data = covered.json()
    assert data["subtasks"][0]["route"] == "kb"
    reaction = data["reactions"][0]
    assert reaction["kind"] == "answer"
    assert any(s["title"] == WIFI_DOC_TITLE for s in reaction["sources"])
    assert data["ticket"]["escalated"] is False

    outside = client.post(
        "/api/requests",
        json={"text": "Как записаться в секцию по плаванию?", "channel": "web"},
    )
    assert outside.status_code == 200, outside.text
    outside_data = outside.json()
    assert outside_data["reactions"][0]["kind"] == "escalated"
    assert outside_data["ticket"]["escalated"] is True


def test_retrieval_excludes_templates(db_session, monkeypatch):
    """Шаблоны (kind='template') исключены из выдачи, чтобы не утекали плейсхолдеры."""
    tmpl = KbArticle(
        kind="template", title="Шаблон Wi-Fi", topic="wifi",
        body="Для подключения к eduroam настройте {{device}}.",
        source="seed", confirmed=True,
    )
    db_session.add(tmpl)
    db_session.commit()
    hits = kb.retrieve(db_session, "Как подключиться к eduroam с телефона?")
    assert not any(h.article.kind == "template" for h in hits)


def test_kb_route_guest_access_allowed(client, monkeypatch, db_session, seed_kb_docs):
    """FR-016: база знаний доступна гостям без авторизации (не auth_required)."""
    make_kb_fake_llm(monkeypatch)
    resp = client.post(
        "/api/requests",
        json={"text": "Как подключиться к eduroam с телефона?", "channel": "web"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reactions"][0]["kind"] == "answer"

