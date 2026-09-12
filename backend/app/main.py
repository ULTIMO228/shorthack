"""Shorthack — сервис коротких ссылок (hackathon-style)."""

import string
import random
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

app = FastAPI(title="Shorthack", version="0.1.0")

engine = create_engine("sqlite:///./shorthack.db", connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)
Base = declarative_base()

ALPHABET = string.ascii_letters + string.digits


class Link(Base):
    __tablename__ = "links"

    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, index=True)
    url = Column(String, nullable=False)
    clicks = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)


class ShortenRequest(BaseModel):
    url: HttpUrl


class LinkResponse(BaseModel):
    code: str
    short_url: str
    url: str
    clicks: int


def generate_code(length: int = 6) -> str:
    return "".join(random.choices(ALPHABET, k=length))


@app.post("/api/shorten", response_model=LinkResponse)
def shorten(req: ShortenRequest):
    session = Session()
    for _ in range(5):
        code = generate_code()
        if not session.query(Link).filter_by(code=code).first():
            break
    else:
        raise HTTPException(500, "Не удалось сгенерировать код")
    link = Link(code=code, url=str(req.url))
    session.add(link)
    session.commit()
    return LinkResponse(code=code, short_url=f"/{code}", url=link.url, clicks=0)


@app.get("/api/stats/{code}", response_model=LinkResponse)
def stats(code: str):
    session = Session()
    link = session.query(Link).filter_by(code=code).first()
    if not link:
        raise HTTPException(404, "Ссылка не найдена")
    return LinkResponse(code=code, short_url=f"/{code}", url=link.url, clicks=link.clicks)


@app.get("/{code}")
def redirect(code: str):
    session = Session()
    link = session.query(Link).filter_by(code=code).first()
    if not link:
        raise HTTPException(404, "Ссылка не найдена")
    link.clicks += 1
    session.commit()
    return RedirectResponse(link.url)
