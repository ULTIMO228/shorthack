"""Подключение к БД: engine SQLite, DeclarativeBase, фабрика сессий, get_session для Depends."""

from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Файл БД — рядом с пакетом app (backend/shorthack.db), независимо от cwd
DB_PATH = Path(__file__).resolve().parents[1] / "shorthack.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Базовый класс всех ORM-моделей проекта."""


def get_session() -> Iterator[Session]:
    """Генератор сессии БД для FastAPI Depends (yield + закрытие в finally)."""
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
