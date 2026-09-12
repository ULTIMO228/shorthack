"""База знаний (US3, FR-030..033): индексация эмбеддингов и retrieval (RAG).

Индексация (index_pending_articles) — единая точка для сидирования (seed.py)
и будущего подтверждения статей оператором (US7, FR-063): эмбеддинг считается
для подтверждённых статей без вектора; при недоступности API прерываемся —
недоиндексированные статьи дозаполнятся на следующем вызове (research.md R3).

Retrieval (retrieve): top-K по косинусной близости (чистый Python), порог
схожести 0.5. Если ни одна статья не проиндексирована (embedding=null) —
keyword-деградация: поиск по токенам в title+body без единого LLM-вызова (R3).
Черновики (confirmed=false) выдаче не участвуют (FR-063).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app import llm
from app.models import KbArticle

TOP_K = 3  # сколько статей возвращаем в выдачу (FR-031, research.md)
SIMILARITY_THRESHOLD = 0.5  # порог косинусной близости (spec US3)

# keyword-деградация: токены короче и стоп-слова в поиск не идут;
# сравнение по префиксам-стемам — «телефона» совпадает с «телефоне».
# Длина 6: короче стем «запис» давал ложные совпадения («записаться» vs «запись»
# в любом регламенте) — вопросы вне базы не должны находить документы (FR-032).
TOKEN_MIN_LEN = 3
STEM_LEN = 5
STOPWORDS = {
    "как", "что", "где", "когда", "куда", "какой", "какая", "какие",
    "можно", "надо", "нужно", "почему", "зачем", "сколько", "есть",
}

TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class RetrievalHit:
    """Один найденный источник; score — косинус (векторный режим) или число совпавших токенов (keyword-режим)."""

    article: KbArticle
    score: float
    degraded: bool = False


Hit = RetrievalHit


def index_article(db: OrmSession, article: KbArticle) -> bool:
    """Индексация отдельной статьи (FR-063, US7).

    Считает embedding через llm.embed и записывает в article.embedding.
    Идемпотентно: если embedding уже заполнен — сразу возвращает True.
    При LLMUnavailable возвращает False (деградация).
    """
    if article.embedding is not None:
        return True
    try:
        vector = llm.embed(f"{article.title}\n{article.body}", kind="doc")
        article.embedding = json.dumps(vector)
        db.commit()
        return True
    except llm.LLMUnavailable:
        return False


def index_pending_articles(db: OrmSession) -> int:
    """Индексация эмбеддингов для статей без embedding (подтверждённых; FR-063).

    При недоступности API прерывается — работает keyword-деградация (R3),
    недоиндексированные статьи дозаполнятся на следующем вызове.
    Возвращает число проиндексированных статей.
    """
    pending = db.scalars(select(KbArticle).where(KbArticle.embedding.is_(None))).all()
    indexed = 0
    for article in pending:
        if article.confirmed is False:  # черновики индексируются после подтверждения (US7)
            continue
        if not index_article(db, article):
            break
        indexed += 1
    return indexed


def cosine(a: list[float], b: list[float]) -> float:
    """Косинусная близость двух векторов; нулевой вектор даёт 0.0 (не попадает в выдачу)."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _load_vector(article: KbArticle) -> list[float] | None:
    """Эмбеддинг статьи из JSON-текста; битый JSON трактуем как «не проиндексирована»."""
    if not article.embedding:
        return None
    try:
        vector = json.loads(article.embedding)
    except (ValueError, TypeError):
        return None
    return [float(x) for x in vector] if isinstance(vector, list) else None


def _tokenize(text: str) -> list[str]:
    """Токены для keyword-поиска: слова в нижнем регистре без стоп-слов и коротких."""
    normalized = text.lower().replace("wi-fi", "wifi").replace("вай-фай", "вайфай")
    return [
        token for token in TOKEN_RE.findall(normalized)
        if len(token) >= TOKEN_MIN_LEN and token not in STOPWORDS
    ]


def _keyword_search(articles: list[KbArticle], query: str, top_k: int) -> list[RetrievalHit]:
    """Деградация без эмбеддингов (R3): счёт = число стемов токенов запроса,
    вошедших в title+body; пустое пересечение → пустая выдача (FR-032)."""
    tokens = _tokenize(query)
    if not tokens:
        return []
    query_stems = [t[:STEM_LEN] for t in tokens]
    # Для одиночных токенов достаточно 1 совпадения; для многословных — не менее 2 совпадений,
    # чтобы случайное совпадение одного стэма (например, "запис" в вопросе про спорт/секцию)
    # не возвращало нерелевантные статьи (FR-032).
    min_score = 2.0 if len(query_stems) >= 2 else 1.0
    hits: list[RetrievalHit] = []
    for article in articles:
        stems = {
            word[:STEM_LEN]
            for word in TOKEN_RE.findall(f"{article.title or ''}\n{article.body or ''}".lower())
        }
        score = float(sum(1 for stem in query_stems if stem in stems))
        if score >= min_score:
            hits.append(RetrievalHit(article=article, score=score, degraded=True))
    hits.sort(key=lambda h: (-h.score, h.article.id))
    return hits[:top_k]


def _vector_search(
    articles: list[KbArticle],
    query: str,
    *,
    top_k: int,
    threshold: float,
) -> list[RetrievalHit] | None:
    """Векторный поиск: эмбеддинг запроса + косинус с порогом.

    Возвращает None, если в базе нет ни одного вектора (вызывающий код
    переключается на keyword-деградацию)."""
    vectors: list[tuple[KbArticle, list[float]]] = []
    for article in articles:
        vector = _load_vector(article)
        if vector is not None:
            vectors.append((article, vector))
    if not vectors:
        return None
    try:
        query_vector = llm.embed(query, kind="query")
    except llm.LLMUnavailable:
        return None  # LLMUnavailable от embed → keyword-режим (деградация R3)
    hits = [
        RetrievalHit(article=article, score=cosine(query_vector, vector), degraded=False)
        for article, vector in vectors
        if len(vector) == len(query_vector)
    ]
    hits = [h for h in hits if h.score >= threshold]
    hits.sort(key=lambda h: (-h.score, h.article.id))
    return hits[:top_k]


def retrieve(
    db: OrmSession,
    query: str,
    *,
    top_k: int = TOP_K,
    threshold: float = SIMILARITY_THRESHOLD,
) -> list[RetrievalHit]:
    """Поиск по базе знаний: top-K статей для запроса (FR-031).

    Подтверждённые документы (шаблоны kind!="document" не участвуют);
    векторный режим при наличии хотя бы одного эмбеддинга,
    иначе keyword-деградация без LLM-вызовов (R3).
    """
    if not query or not query.strip():
        return []
    articles = db.scalars(
        select(KbArticle).where(
            KbArticle.confirmed.is_(True),
            KbArticle.kind == "document",
        )
    ).all()
    vector_hits = _vector_search(articles, query, top_k=top_k, threshold=threshold)
    if vector_hits is not None:
        return vector_hits
    return _keyword_search(articles, query, top_k)


