"""Тесты каркаса приложения (T011) и зависимостей авторизации (T008): health,
db.get_session, get_current_user/get_operator, load_env_file, lifespan."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import auth, main as main_module
from app.db import Base
from app.models import Session as UserSession
from app.models import User


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

def test_health_reports_llm_down(client, monkeypatch):
    monkeypatch.setattr(main_module.llm, "llm_up", lambda: False)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "llm": "down"}


def test_health_reports_llm_up(client, monkeypatch):
    monkeypatch.setattr(main_module.llm, "llm_up", lambda: True)
    r = client.get("/api/health")
    assert r.json() == {"status": "ok", "llm": "up"}


# ---------------------------------------------------------------------------
# db.get_session (L002: yield + закрытие)
# ---------------------------------------------------------------------------

def test_get_session_yields_and_closes():
    from app.db import get_session

    gen = get_session()
    session = next(gen)
    assert session is not None
    assert session.bind is not None
    gen.close()  # закрытие через finally в генераторе


# ---------------------------------------------------------------------------
# Зависимости авторизации: get_current_user / get_operator
# ---------------------------------------------------------------------------

@pytest.fixture()
def auth_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/auth-test.db")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def test_get_current_user_guest_raises_401(auth_db):
    auth_db.add(UserSession(id="tok-guest", user_id=None))
    auth_db.commit()
    guest = auth_db.get(UserSession, "tok-guest")

    with pytest.raises(HTTPException) as exc_info:
        auth.get_current_user(guest)
    assert exc_info.value.status_code == 401


def test_get_current_user_returns_user(auth_db):
    user = User(email="a@misis.ru", full_name="A", role="student",
                password_hash=auth.hash_password("student123"))
    auth_db.add(user)
    auth_db.flush()
    auth_db.add(UserSession(id="tok-user", user_id=user.id))
    auth_db.commit()
    session = auth_db.get(UserSession, "tok-user")

    assert auth.get_current_user(session).email == "a@misis.ru"


def test_get_operator_rejects_student_and_guest(auth_db):
    student = User(email="s@misis.ru", full_name="S", role="student",
                   password_hash=auth.hash_password("student123"))
    auth_db.add(student)
    auth_db.flush()
    auth_db.add(UserSession(id="tok-student", user_id=student.id))
    auth_db.add(UserSession(id="tok-anon", user_id=None))
    auth_db.commit()

    with pytest.raises(HTTPException) as exc_info:
        auth.get_operator(auth_db.get(UserSession, "tok-student"))
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        auth.get_operator(auth_db.get(UserSession, "tok-anon"))
    assert exc_info.value.status_code == 401  # гость — сначала предложение войти


def test_get_operator_returns_operator(auth_db):
    operator = User(email="op@misis.ru", full_name="Op", role="operator",
                    password_hash=auth.hash_password("operator123"))
    auth_db.add(operator)
    auth_db.flush()
    auth_db.add(UserSession(id="tok-op", user_id=operator.id))
    auth_db.commit()

    assert auth.get_operator(auth_db.get(UserSession, "tok-op")).role == "operator"


def test_is_misis_email_variants():
    assert auth.is_misis_email("a@misis.ru")
    assert auth.is_misis_email("A@edu.misis.ru")
    assert not auth.is_misis_email("a@misis.ru.evil.com")  # поддомен-обманка
    assert not auth.is_misis_email("@misis.ru")
    assert not auth.is_misis_email("a@gmail.com")


def test_full_name_from_email_variants():
    assert auth.full_name_from_email("ivanov.ivan@misis.ru") == "Ivanov Ivan"
    assert auth.full_name_from_email("petrova_i@edu.misis.ru") == "Petrova I"
    assert auth.full_name_from_email("@misis.ru") == "Пользователь"


# ---------------------------------------------------------------------------
# load_env_file и lifespan
# ---------------------------------------------------------------------------

def test_load_env_file_parses_and_respects_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# комментарий\n"
        "COVERAGE_TEST_KEY=из файла\n"
        'COVERAGE_QUOTED="значение"\n'
        "\n"
        "без-равно\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("COVERAGE_TEST_KEY", "из окружения")

    main_module.load_env_file(env)

    assert os_environ("COVERAGE_TEST_KEY") == "из окружения"  # env важнее файла
    assert os_environ("COVERAGE_QUOTED") == "значение"


def os_environ(key: str) -> str:
    import os

    return os.environ[key]


def test_load_env_file_missing_file_ok(tmp_path):
    main_module.load_env_file(tmp_path / "нет-файла.env")  # не падает


def test_lifespan_creates_tables_and_seeds_temp_db(monkeypatch, tmp_path):
    """lifespan целиком на временной БД: create_all + seed, реальная БД не трогается."""
    engine = create_engine(
        f"sqlite:///{tmp_path}/lifespan.db", connect_args={"check_same_thread": False}
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main_module, "engine", engine)
    monkeypatch.setattr(main_module, "SessionLocal", Session)

    async def run():
        async with main_module.lifespan(main_module.app):
            with Session() as db:
                assert db.query(User).count() == 5
                assert db.query(UserSession).count() == 0

    asyncio.run(run())
    engine.dispose()
