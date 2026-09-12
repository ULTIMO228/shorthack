"""Общие фикстуры тестов: временная SQLite-БД, переопределённый get_session, TestClient.

Приложение импортируется из app.main (роутеры подключены там, T011); get_session
переопределён на временную БД — реальная backend/shorthack.db тестами не трогается.
Lifespan основного приложения (create_all + seed основной БД) в тестах не запускаем:
таблицы создаёт фикстура db_engine, сидирование тестам не нужно.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# backend/ на sys.path (пакет app), чтобы работали и `python -m pytest`, и `pytest`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, get_session  # noqa: E402
from app.main import app as main_app  # noqa: E402


@pytest.fixture()
def db_engine(tmp_path):
    """Временная SQLite-БД (файл в tmp_path), все таблицы create_all."""
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def app(db_engine):
    """Готовое приложение app.main с переопределённым get_session на тестовую БД."""
    testing_session = sessionmaker(bind=db_engine, expire_on_commit=False)

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    main_app.dependency_overrides[get_session] = override_get_session
    yield main_app
    main_app.dependency_overrides.clear()


@pytest.fixture()
def client(app):
    """TestClient без входа в lifespan (без контекстного менеджера) — реальная
    БД не сидируется; изоляция обеспечена override get_session."""
    return TestClient(app)


@pytest.fixture()
def bot_token(monkeypatch):
    """Тестовый BOT_INTERNAL_TOKEN в env (реальный секрет не трогаем)."""
    token = "test-bot-token"
    monkeypatch.setenv("BOT_INTERNAL_TOKEN", token)
    return token


@pytest.fixture()
def operator_client(app, db_engine):
    """TestClient с сессией дежурного оператора (smirnov@misis.ru)."""
    from app.models import User
    from sqlalchemy import select

    testing_session = sessionmaker(bind=db_engine, expire_on_commit=False)
    with testing_session() as db:
        op = db.scalars(select(User).where(User.email == "smirnov@misis.ru")).first()
        if not op:
            op = User(email="smirnov@misis.ru", full_name="Смирнов Олег", role="operator")
            db.add(op)
            db.commit()
    c = TestClient(app)
    resp = c.post("/api/auth/login", json={"email": "smirnov@misis.ru"})
    assert resp.status_code == 200, resp.text
    return c


class FakeHttpResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeHttpClient:
    """Замена httpx.Client: get() возвращает подставленный ответ или бросает исключение."""

    instances: list[dict] = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        FakeHttpClient.instances.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url: str):
        item = fake_routes.get(url, FakeHttpResponse(200))
        if isinstance(item, Exception):
            raise item
        return item


fake_routes: dict = {}


@pytest.fixture()
def fake_http(monkeypatch):
    """Подмена app.tools.httpx.Client; вернуть словарь URL → ответ/исключение."""
    global fake_routes
    fake_routes = {}
    FakeHttpClient.instances = []
    monkeypatch.setattr("app.tools.httpx.Client", FakeHttpClient)
    return fake_routes

