"""Тесты сидирования (T010): наполнение БД, идемпотентность, индексация эмбеддингов."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import llm, seed as seed_module
from app.db import Base
from app.models import KbArticle, Service, User


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/seed-test.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def test_seed_creates_users_services_and_kb(db_session):
    seed_module.seed(db_session)

    users = db_session.scalars(select(User)).all()
    assert len(users) == 5
    assert sum(1 for u in users if u.role == "student") == 3
    assert any(u.role == "staff" for u in users)
    assert any(u.role == "operator" for u in users)
    assert any(u.email == "ivanov@misis.ru" for u in users)

    services = db_session.scalars(select(Service)).all()
    assert len(services) == 5
    real = {s.name for s in services if s.check_type == "real"}
    emulated = {s.name for s in services if s.check_type == "emulated"}
    assert real == {"misis.ru", "newlms.misis.ru"}
    assert emulated == {"MISIS-Guest", "MISIS-EDU", "MISIS-CORP"}
    assert all(s.state == "up" for s in services)

    articles = db_session.scalars(select(KbArticle)).all()
    assert len(articles) == 10
    assert sum(1 for a in articles if a.kind == "document") == 5
    assert sum(1 for a in articles if a.kind == "template") == 5
    assert all(a.source == "seed" for a in articles)
    assert all(a.confirmed for a in articles)
    topics = {a.topic for a in articles}
    assert {"wifi", "password", "lms", "certs", "support"} <= topics


def test_seed_is_idempotent(db_session):
    seed_module.seed(db_session)
    seed_module.seed(db_session)
    seed_module.seed(db_session)

    assert db_session.query(User).count() == 5
    assert db_session.query(Service).count() == 5
    assert db_session.query(KbArticle).count() == 10


def test_seed_degrades_when_llm_unavailable(db_session, monkeypatch):
    """API эмбеддингов недоступен — статьи остаются без индекса, старт не падает (R3)."""
    monkeypatch.setattr(
        seed_module.llm, "embed", lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable("down"))
    )
    seed_module.seed(db_session)

    articles = db_session.scalars(select(KbArticle)).all()
    assert len(articles) == 10
    assert all(a.embedding is None for a in articles)


def test_index_pending_articles_indexes_only_missing(monkeypatch, db_session):
    monkeypatch.setattr(
        seed_module.llm, "embed", lambda *a, **kw: (_ for _ in ()).throw(llm.LLMUnavailable("down"))
    )
    seed_module.seed(db_session)  # все без эмбеддингов

    def fake_embed(text, **kw):
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(seed_module.llm, "embed", fake_embed)
    indexed = seed_module.index_pending_articles(db_session)
    assert indexed == 10

    articles = db_session.scalars(select(KbArticle)).all()
    for article in articles:
        assert json.loads(article.embedding) == [0.1, 0.2, 0.3]

    # повторный вызов — нечего индексировать
    assert seed_module.index_pending_articles(db_session) == 0


def test_index_pending_articles_skips_unconfirmed_drafts(monkeypatch, db_session):
    db_session.add(
        KbArticle(kind="document", title="Черновик", body="текст", topic="other",
                  source="operator", confirmed=False)
    )
    db_session.commit()
    monkeypatch.setattr(seed_module.llm, "embed", lambda *a, **kw: [1.0])

    assert seed_module.index_pending_articles(db_session) == 0
    draft = db_session.scalar(select(KbArticle).where(KbArticle.title == "Черновик"))
    assert draft.embedding is None


def test_index_pending_articles_stops_on_first_failure(monkeypatch, db_session):
    """Первая ошибка API — прерываем цикл: остальные статьи ждут следующего старта."""
    db_session.add_all(
        KbArticle(kind="document", title=f"Статья {i}", body="текст", topic="other",
                  source="seed", confirmed=True)
        for i in range(3)
    )
    db_session.commit()

    calls = []

    def failing_embed(text, **kw):
        calls.append(text)
        raise llm.LLMUnavailable("down")

    monkeypatch.setattr(seed_module.llm, "embed", failing_embed)
    assert seed_module.index_pending_articles(db_session) == 0
    assert len(calls) == 1  # break после первого сбоя, а не 3 вызова
