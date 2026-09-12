"""Тесты US7 (Phase 9, T042-T043): самообучение базы знаний.

Закрытие эскалации с add_to_kb → LLM-черновик → подтверждение оператором
→ индексация и доступность статьи RAG-агенту.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import kb, llm, tickets, tools
from app.models import Event, KbArticle, Request, Session as DbSessionModel, Subtask, Ticket, User
from app.tools import KbDraftResult


@pytest.fixture()
def db_session(db_engine):
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()


def make_operator(db_engine, email="smirnov@misis.ru") -> User:
    """Создать оператора в БД."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name="Смирнов Олег", role="operator")
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    return user


def make_student(db_engine, email="ivanov@misis.ru") -> User:
    """Создать студента в БД."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name="Иванов Иван", role="student")
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    return user


def create_escalation(
    db_session,
    *,
    text: str = "Не работает Wi-Fi в общежитии",
    status: str = "в работе",
    resolution: str = "Заменили коммутатор",
) -> tuple[Request, Ticket]:
    """Создать обращение с тикетом и перепиской для проверки закрытия."""
    sess = DbSessionModel(id=secrets.token_urlsafe(16))
    db_session.add(sess)
    db_session.flush()

    req = Request(
        session_id=sess.id,
        channel="web",
        raw_text=text,
        masked_text=text,
        lang="ru",
    )
    db_session.add(req)
    db_session.flush()

    subtask = Subtask(
        request_id=req.id,
        position=1,
        summary=text,
        service="wifi_edu",
        category="availability",
        route="escalate",
        priority="high",
        confidence=0.9,
        status=status,
    )
    db_session.add(subtask)
    db_session.flush()

    ticket = Ticket(
        request_id=req.id,
        status=status,
        escalated=True,
    )
    db_session.add(ticket)
    db_session.flush()

    # Журнал диалога
    ev1 = Event(
        ticket_id=ticket.id,
        actor="user",
        action="dialog",
        payload=json.dumps({"role": "user", "text": "У меня не ловит Wi-Fi в корпусе 3"}, ensure_ascii=False),
    )
    ev2 = Event(
        ticket_id=ticket.id,
        actor="agent",
        action="reaction_sent",
        payload=json.dumps({"kind": "answer", "text": "Передал запрос дежурному"}, ensure_ascii=False),
    )
    db_session.add_all([ev1, ev2])
    db_session.commit()
    return req, ticket


# ---------------------------------------------------------------------------
# T042 / T043: Закрытие эскалации (POST /api/admin/escalations/{id}/close)
# ---------------------------------------------------------------------------

def test_close_without_add_to_kb_returns_null_draft(app, db_engine, db_session):
    """Happy path: close без add_to_kb → тикет закрыт, kb_draft=null."""
    make_operator(db_engine)
    req, ticket = create_escalation(db_session, status="в работе")

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resp = op_client.post(
        f"/api/admin/escalations/{req.id}/close",
        json={"resolution": "Перезагрузили оборудование", "add_to_kb": False},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ticket_status"] == "закрыта"
    assert data["kb_draft"] is None

    # В БД тикет закрыт
    db_session.refresh(ticket)
    assert ticket.status == "закрыта"

    # Событие в журнале
    events = db_session.scalars(
        select(Event).where(Event.ticket_id == ticket.id, Event.action == "escalation_closed")
    ).all()
    assert len(events) == 1
    payload = json.loads(events[0].payload)
    assert payload["resolution"] == "Перезагрузили оборудование"
    assert payload["add_to_kb"] is False


def test_close_with_add_to_kb_creates_draft(app, db_engine, db_session, monkeypatch):
    """Happy path: close с add_to_kb=True → черновик создан через LLM, confirmed=False."""
    make_operator(db_engine)
    req, ticket = create_escalation(db_session, status="в работе")

    def fake_chat_structured(prompt: str, schema_cls: type[Any], **kwargs):
        assert issubclass(schema_cls, KbDraftResult)
        return KbDraftResult(
            title="Настройка Wi-Fi в корпусе 3",
            body="Инструкция: подключитесь к MISIS-EDU с логином и паролем.",
            topic="wifi",
        )

    monkeypatch.setattr(llm, "chat_structured", fake_chat_structured)

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resp = op_client.post(
        f"/api/admin/escalations/{req.id}/close",
        json={"resolution": "Перенастроили точку доступа", "add_to_kb": True},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ticket_status"] == "закрыта"
    draft = data["kb_draft"]
    assert draft is not None
    assert draft["title"] == "Настройка Wi-Fi в корпусе 3"
    assert draft["body"] == "Инструкция: подключитесь к MISIS-EDU с логином и паролем."
    assert draft["confirmed"] is False

    # Проверка статьи в БД
    article = db_session.get(KbArticle, draft["id"])
    assert article is not None
    assert article.title == "Настройка Wi-Fi в корпусе 3"
    assert article.topic == "wifi"
    assert article.source == "operator"
    assert article.confirmed is False
    assert article.embedding is None
    assert article.kind == "document"

    # Проверка записи вызова инструмента в events
    tool_events = db_session.scalars(
        select(Event).where(Event.actor == "tool:draft_kb_article")
    ).all()
    assert len(tool_events) >= 2  # tool_call и tool_result


def test_close_uses_dialog_and_resolution_in_prompt(app, db_engine, db_session, monkeypatch):
    """Промпт для draft_kb_article содержит текст переписки и resolution."""
    make_operator(db_engine)
    req, ticket = create_escalation(db_session, status="в работе")

    captured_prompts: list[str] = []

    def fake_chat_structured(prompt: str, schema_cls: type[Any], **kwargs):
        captured_prompts.append(prompt)
        return KbDraftResult(title="Статья", body="Инструкция", topic="wifi")

    monkeypatch.setattr(llm, "chat_structured", fake_chat_structured)

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resolution_text = "Специальное решение для теста 12345"
    resp = op_client.post(
        f"/api/admin/escalations/{req.id}/close",
        json={"resolution": resolution_text, "add_to_kb": True},
    )
    assert resp.status_code == 200

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert resolution_text in prompt
    assert "У меня не ловит Wi-Fi в корпусе 3" in prompt


def test_close_add_to_kb_llm_unavailable_uses_template(app, db_engine, db_session, monkeypatch):
    """При недоступности LLM черновик генерируется по шаблону, тикет всё равно закрывается."""
    make_operator(db_engine)
    req, ticket = create_escalation(db_session, status="в работе")

    def fake_chat_structured(prompt: str, schema_cls: type[Any], **kwargs):
        raise llm.LLMUnavailable("Яндекс API временно недоступен")

    monkeypatch.setattr(llm, "chat_structured", fake_chat_structured)

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resolution = "Заменили патч-корд на коммутаторе"
    resp = op_client.post(
        f"/api/admin/escalations/{req.id}/close",
        json={"resolution": resolution, "add_to_kb": True},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ticket_status"] == "закрыта"
    draft = data["kb_draft"]
    assert draft is not None
    assert resolution in draft["title"]
    assert resolution in draft["body"]
    assert draft["confirmed"] is False

    # В БД статья сохранена
    article = db_session.get(KbArticle, draft["id"])
    assert article is not None
    assert article.confirmed is False


def test_close_sets_resolved_then_closed_from_open_status(app, db_engine, db_session):
    """Тикет в статусе 'в работе' проходит цепочку 'решена' → 'закрыта' (FR-015)."""
    make_operator(db_engine)
    req, ticket = create_escalation(db_session, status="в работе")

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resp = op_client.post(
        f"/api/admin/escalations/{req.id}/close",
        json={"resolution": "Готово", "add_to_kb": False},
    )
    assert resp.status_code == 200

    # Проверяем историю статусов в events
    status_events = db_session.scalars(
        select(Event)
        .where(Event.ticket_id == ticket.id, Event.action == "status_change")
        .order_by(Event.id)
    ).all()
    transitions = [json.loads(e.payload) for e in status_events]
    assert any(t["to"] == "решена" for t in transitions)
    assert any(t["to"] == "закрыта" for t in transitions)


def test_close_non_operator_forbidden(app, db_engine, db_session):
    """Студент получает 403, гость без входа — 401."""
    make_student(db_engine)
    req, ticket = create_escalation(db_session, status="в работе")

    # Студент
    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    resp_student = student_client.post(
        f"/api/admin/escalations/{req.id}/close",
        json={"resolution": "Готово", "add_to_kb": False},
    )
    assert resp_student.status_code == 403

    # Гость
    guest_client = TestClient(app)
    resp_guest = guest_client.post(
        f"/api/admin/escalations/{req.id}/close",
        json={"resolution": "Готово", "add_to_kb": False},
    )
    assert resp_guest.status_code == 401


def test_close_unknown_request_404(app, db_engine):
    """Несуществующее обращение → 404."""
    make_operator(db_engine)
    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resp = op_client.post(
        "/api/admin/escalations/999999/close",
        json={"resolution": "Готово", "add_to_kb": False},
    )
    assert resp.status_code == 404


def test_close_closed_ticket_idempotent(app, db_engine, db_session):
    """Повторный close для уже закрытого тикета идемпотентен: 200, без новых черновиков."""
    make_operator(db_engine)
    req, ticket = create_escalation(db_session, status="закрыта")

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resp = op_client.post(
        f"/api/admin/escalations/{req.id}/close",
        json={"resolution": "Повторное закрытие", "add_to_kb": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ticket_status"] == "закрыта"
    assert data["kb_draft"] is None


# ---------------------------------------------------------------------------
# T043: Подтверждение черновика (POST /api/admin/kb/articles/{id}/confirm)
# ---------------------------------------------------------------------------

def test_confirm_article_indexes_embedding(app, db_engine, db_session, monkeypatch):
    """Happy path: confirm статьи → confirmed=True, embedding проиндексирован."""
    make_operator(db_engine)
    article = KbArticle(
        kind="document",
        title="Инструкция по Wi-Fi",
        body="Для подключения используйте PEAP.",
        topic="wifi",
        source="operator",
        confirmed=False,
        embedding=None,
    )
    db_session.add(article)
    db_session.commit()

    fake_vector = [0.1] * 256
    monkeypatch.setattr(llm, "embed", lambda text, **kw: fake_vector)

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resp = op_client.post(f"/api/admin/kb/articles/{article.id}/confirm")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == article.id
    assert data["confirmed"] is True

    # Проверка в БД
    db_session.refresh(article)
    assert article.confirmed is True
    assert article.embedding is not None
    loaded_vector = json.loads(article.embedding)
    assert len(loaded_vector) == 256

    # Событие в журнале
    events = db_session.scalars(
        select(Event).where(Event.action == "kb_article_confirmed")
    ).all()
    assert len(events) >= 1
    p = json.loads(events[-1].payload)
    assert p["article_id"] == article.id
    assert p["indexed"] is True


def test_confirm_retrieval_finds_article(db_session, monkeypatch):
    """Сценарий 8: до confirm статья не находится RAG, после confirm — находится."""
    article = KbArticle(
        kind="document",
        title="Особый регламент доступа к лабораторным ПК",
        body="Для входа в терминал используйте студенческий билет и пароль от LMS.",
        topic="support",
        source="operator",
        confirmed=False,
        embedding=None,
    )
    db_session.add(article)
    db_session.commit()

    # Вектор для поиска
    target_vector = [1.0, 0.0]
    monkeypatch.setattr(llm, "embed", lambda text, **kw: target_vector)

    # 1. До подтверждения черновик не виден retrieval
    hits_before = kb.retrieve(db_session, "доступ к лабораторным ПК")
    assert not any(h.article.id == article.id for h in hits_before)

    # 2. Подтверждение и индексация
    article.confirmed = True
    assert kb.index_article(db_session, article) is True

    # 3. После подтверждения статья находится в top-N
    hits_after = kb.retrieve(db_session, "доступ к лабораторным ПК")
    assert any(h.article.id == article.id for h in hits_after)


def test_confirm_idempotent_no_second_embed(app, db_engine, db_session, monkeypatch):
    """Повторный confirm той же статьи не вызывает embed повторно."""
    make_operator(db_engine)
    article = KbArticle(
        kind="document",
        title="Статья без дублирования",
        body="Тело статьи.",
        topic="wifi",
        source="operator",
        confirmed=False,
        embedding=None,
    )
    db_session.add(article)
    db_session.commit()

    embed_calls = []

    def counting_embed(text, **kw):
        embed_calls.append(text)
        return [0.5] * 256

    monkeypatch.setattr(llm, "embed", counting_embed)

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    # Первый вызов
    r1 = op_client.post(f"/api/admin/kb/articles/{article.id}/confirm")
    assert r1.status_code == 200
    assert len(embed_calls) == 1

    # Второй вызов
    r2 = op_client.post(f"/api/admin/kb/articles/{article.id}/confirm")
    assert r2.status_code == 200
    assert len(embed_calls) == 1  # Повторного вызова нет


def test_confirm_llm_unavailable_still_confirms(app, db_engine, db_session, monkeypatch):
    """Если при confirm модель недоступна, статья подтверждается, embedding остаётся null."""
    make_operator(db_engine)
    article = KbArticle(
        kind="document",
        title="Статья при недоступном LLM",
        body="Тело статьи.",
        topic="other",
        source="operator",
        confirmed=False,
        embedding=None,
    )
    db_session.add(article)
    db_session.commit()

    def failing_embed(text, **kw):
        raise llm.LLMUnavailable("Yandex Embeddings down")

    monkeypatch.setattr(llm, "embed", failing_embed)

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resp = op_client.post(f"/api/admin/kb/articles/{article.id}/confirm")
    assert resp.status_code == 200
    assert resp.json()["confirmed"] is True

    db_session.refresh(article)
    assert article.confirmed is True
    assert article.embedding is None


def test_confirm_unknown_article_404(app, db_engine):
    """Несуществующая статья → 404."""
    make_operator(db_engine)
    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resp = op_client.post("/api/admin/kb/articles/999999/confirm")
    assert resp.status_code == 404


def test_confirm_non_operator_forbidden(app, db_engine, db_session):
    """Студент получает 403, гость без входа — 401."""
    make_student(db_engine)
    article = KbArticle(
        kind="document",
        title="Статья",
        body="Тело",
        topic="wifi",
        source="operator",
        confirmed=False,
    )
    db_session.add(article)
    db_session.commit()

    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    resp_student = student_client.post(f"/api/admin/kb/articles/{article.id}/confirm")
    assert resp_student.status_code == 403

    guest_client = TestClient(app)
    resp_guest = guest_client.post(f"/api/admin/kb/articles/{article.id}/confirm")
    assert resp_guest.status_code == 401


# ---------------------------------------------------------------------------
# Сценарий 8 Quickstart (интеграционный e2e-тест цикла самообучения)
# ---------------------------------------------------------------------------

def test_quickstart_scenario_8_kb_learning(app, db_engine, db_session, monkeypatch):
    """Сценарий 8 quickstart.md: вопрос вне базы → эскалация → close с add_to_kb
    → confirm → повторный аналогичный вопрос успешно отвечает RAG-агентом из новой статьи.
    """
    make_operator(db_engine)
    client = TestClient(app)

    # Вектор для embedding
    target_vector = [1.0, 0.0]
    monkeypatch.setattr(llm, "embed", lambda text, **kw: target_vector)

    # Настройка мока LLM
    def fake_chat(messages, *, model=None, temperature=0.2, timeout=25.0):
        text = messages if isinstance(messages, str) else "\n".join(m["content"] for m in messages)
        if "Переведи" in text:
            return "Как получить материальную поддержку?"
        if '"subtasks"' in text:
            return json.dumps({"subtasks": [{"summary": "Вопрос о материальной помощи"}]}, ensure_ascii=False)
        if '"queries"' in text:
            return json.dumps({"queries": ["материальная помощь"]}, ensure_ascii=False)
        if "Переформулируй" in text:
            return "материальная помощь студентам"
        if "Фрагменты базы знаний" in text:
            return "Для получения материальной помощи подайте заявление через профком."
        # classify
        return json.dumps({
            "route": "kb",
            "service": "support",
            "category": "howto",
            "priority": "medium",
            "confidence": 0.85,
            "reason": "Вопрос по правилам материальной помощи",
        }, ensure_ascii=False)

    def fake_chat_structured(prompt: str, schema_cls: type[Any], **kwargs):
        if issubclass(schema_cls, KbDraftResult):
            return KbDraftResult(
                title="Регламент материальной поддержки студентов",
                body="Для получения матпомощи подайте заявление через профком.",
                topic="support",
            )
        if schema_cls.__name__ == "SplitResult":
            from app.agent import SplitResult, SubtaskDraft
            return SplitResult(subtasks=[SubtaskDraft(summary="Вопрос о материальной помощи")])
        if schema_cls.__name__ == "ClassifierResult":
            from app.schemas import ClassifierResult
            return ClassifierResult(
                route="kb",
                service="other",
                category="howto",
                priority="medium",
                confidence=0.85,
                reason="Вопрос по правилам материальной помощи",
            )
        if schema_cls.__name__ == "Subqueries":
            from app.tools import Subqueries
            return Subqueries(queries=["материальная помощь"])
        from pydantic import TypeAdapter
        return TypeAdapter(schema_cls).validate_python({})

    monkeypatch.setattr(llm, "chat", fake_chat)
    monkeypatch.setattr(llm, "chat_structured", fake_chat_structured)

    # Шаг 1: Вопрос вне базы (в БЗ нет статей по матпомощи)
    resp1 = client.post(
        "/api/requests",
        json={"text": "Как получить материальную помощь студенту?", "channel": "web"},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["reactions"][0]["kind"] == "escalated"
    assert data1["ticket"]["escalated"] is True
    request_id = data1["request_id"]

    # Шаг 2: Оператор закрывает эскалацию с add_to_kb=true
    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    resp_close = op_client.post(
        f"/api/admin/escalations/{request_id}/close",
        json={
            "resolution": "Подать заявление на матпомощь через профком студентов",
            "add_to_kb": True,
        },
    )
    assert resp_close.status_code == 200
    close_data = resp_close.json()
    assert close_data["ticket_status"] == "закрыта"
    draft = close_data["kb_draft"]
    assert draft is not None
    assert draft["confirmed"] is False
    article_id = draft["id"]

    # Шаг 3: Оператор подтверждает черновик
    resp_confirm = op_client.post(f"/api/admin/kb/articles/{article_id}/confirm")
    assert resp_confirm.status_code == 200
    assert resp_confirm.json()["confirmed"] is True

    # Шаг 4: Повторный запрос пользователя с аналогичным вопросом
    resp2 = client.post(
        "/api/requests",
        json={"text": "Как получить материальную помощь студенту?", "channel": "web"},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    reaction = data2["reactions"][0]
    assert reaction["kind"] == "answer"
    assert "профком" in reaction["text"]
    assert any(s["title"] == "Регламент материальной поддержки студентов" for s in reaction["sources"])
    assert data2["ticket"]["status"] == "решена"

