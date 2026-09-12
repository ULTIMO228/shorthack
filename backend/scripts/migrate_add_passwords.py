"""Разовая миграция dev-БД: добавление users.password_hash и заполнение хэшами.

Для существующей backend/shorthack.db (созданной до появления паролей):
1) ALTER TABLE users ADD COLUMN password_hash (NOT NULL DEFAULT '');
2) для пользователей с пустым хэшем ставится демо-пароль по роли из seed
   (student123 / staff123 / operator123), хэш считается через app.auth.hash_password.

Для новых БД скрипт не нужен: таблицы создаются create_all уже с колонкой,
а seed заполняет password_hash при сидировании. Скрипт идемпотентен.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.auth import hash_password  # noqa: E402
from app.db import DB_PATH  # noqa: E402
from app.models import User  # noqa: E402
from app.seed import PASSWORD_BY_ROLE  # noqa: E402


def add_column_if_missing(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "password_hash" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash VARCHAR NOT NULL DEFAULT ''")
            conn.commit()
            print("колонка users.password_hash добавлена")
        else:
            print("колонка users.password_hash уже есть")
    finally:
        conn.close()


def fill_hashes(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    with Session(bind=engine) as db:
        users = db.scalars(select(User).where(User.password_hash == "")).all()
        for user in users:
            user.password_hash = hash_password(PASSWORD_BY_ROLE[user.role])
            db.execute(
                update(User).where(User.id == user.id).values(password_hash=user.password_hash)
            )
        db.commit()
        print(f"хэши обновлены для {len(users)} пользователей")
    engine.dispose()


if __name__ == "__main__":
    add_column_if_missing(DB_PATH)
    fill_hashes(DB_PATH)
