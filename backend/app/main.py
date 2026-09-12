"""Ядро ИИ-помощника техподдержки МИСИС: FastAPI-приложение, старт БД, сидирование."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app import llm
from app.db import Base, SessionLocal, engine
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import certs as certs_router
from app.routers import internal as internal_router
from app.routers import requests as requests_router
from app.schemas import HealthOut
from app.seed import seed

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path: Path = ENV_PATH) -> None:
    """Мини-загрузчик backend/.env (KEY=VALUE); python-dotenv в проекте нет.

    setdefault — реальные переменные окружения приоритетнее файла.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env_file()
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    yield
    engine.dispose()


app = FastAPI(
    title="Shorthack — ИИ-помощник техподдержки МИСИС",
    version="0.2.0",
    lifespan=lifespan,
)
app.include_router(auth_router.router)
app.include_router(internal_router.router)
app.include_router(requests_router.router)
app.include_router(admin_router.router)
app.include_router(certs_router.router)


@app.get("/api/health", response_model=HealthOut, tags=["service"])
def health() -> HealthOut:
    """Публичная проверка живости: статус приложения и доступность LLM."""
    return HealthOut(status="ok", llm="up" if llm.llm_up() else "down")
