"""Клиент Yandex AI Studio через OpenAI-совместимый API (httpx).

Диалоговые модели — `chat()` / `chat_structured()` (классификатор и генерация),
эмбеддинги — `embed()` / `embed_many()`. Аутентификация: `Authorization: Api-Key`
+ `OpenAI-Project` (research.md R1). Реальные вызовы — только при наличии ключей
в окружении; в тестах функции мокаются (monkeypatch `app.llm.chat` / `app.llm.embed`).
"""

from __future__ import annotations

import json
import os
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

API_BASE = "https://ai.api.cloud.yandex.net/v1"
LLM_TIMEOUT_SEC = 25.0  # таймаут вызова LLM (plan.md, FR-022/023 противопоставлены 5 c)
EMBED_TIMEOUT_SEC = 10.0  # короче: сидирование не должно надолго блокировать старт
MAX_RETRIES = 1  # research R1: 1 retry с уточняющим промптом

EMBED_DOC_MODEL = "text-search-doc/latest"
EMBED_QUERY_MODEL = "text-search-query/latest"


class LLMUnavailable(RuntimeError):
    """LLM недоступен или ответ не проходит валидацию — безопасный маршрут (FR-013)."""


SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _api_key() -> str | None:
    key = os.environ.get("YANDEX_API_KEY", "").strip()
    return key or None


def _folder_id() -> str | None:
    return os.environ.get("YANDEX_FOLDER_ID", "").strip() or None


def _model_uri(env_name: str, fallback: str, scheme: str) -> str:
    """URI модели: значение из env как есть, если содержит '://', иначе scheme://<folder>/<имя>."""
    raw = os.environ.get(env_name, "").strip()
    if "://" in raw:
        return raw
    folder = _folder_id() or ""
    return f"{scheme}://{folder}/{raw or fallback}"


def classify_model_uri() -> str:
    return _model_uri("YANDEX_MODEL_CLASSIFY", "yandexgpt-lite/latest", "gpt")


def generate_model_uri() -> str:
    return _model_uri("YANDEX_MODEL_GENERATE", "yandexgpt/latest", "gpt")


def embedding_model_uri(kind: str) -> str:
    fallback = EMBED_DOC_MODEL if kind == "doc" else EMBED_QUERY_MODEL
    return _model_uri("", fallback, "emb")


def llm_up() -> bool:
    """Ключ задан и не является плейсхолдером (для /api/health)."""
    key = _api_key()
    return bool(key) and key.lower() != "placeholder"


def _headers() -> dict[str, str]:
    key = _api_key()
    if not key:
        raise LLMUnavailable("YANDEX_API_KEY не задан — LLM-вызов невозможен")
    headers = {"Authorization": f"Api-Key {key}"}
    folder = _folder_id()
    if folder:
        headers["OpenAI-Project"] = folder
    return headers


def _post(path: str, body: dict, timeout: float) -> dict:
    try:
        with httpx.Client(base_url=API_BASE, timeout=timeout) as client:
            resp = client.post(path, json=body, headers=_headers())
            resp.raise_for_status()
            return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LLMUnavailable(f"POST {path}: {exc}") from exc


def chat(
    messages: str | list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    timeout: float = LLM_TIMEOUT_SEC,
) -> str:
    """Один вызов chat-completions; messages — строка или список {role, content}."""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    body = {"model": model or generate_model_uri(), "messages": messages, "temperature": temperature}
    data = _post("/chat/completions", body, timeout)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable(f"chat: неожиданный формат ответа: {exc}") from exc


def _extract_json(content: str) -> str:
    """Выделяет первый JSON-объект из ответа модели (с защитой от ```json-огородок)."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text.strip("`")
        text = text.rstrip("`").strip()
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("JSON-объект не найден", content, 0)
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return json.dumps(obj, ensure_ascii=False)


def chat_structured(
    messages: str | list[dict[str, str]],
    schema: type[SchemaT],
    *,
    model: str | None = None,
    temperature: float = 0.1,
    timeout: float = 25.0,
) -> SchemaT:
    """chat() + извлечение JSON + Pydantic-валидация; при сбое — 1 retry с подсказкой."""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            content = chat(messages, model=model, temperature=temperature, timeout=timeout)
            return schema.model_validate_json(_extract_json(content))
        except (LLMUnavailable, ValidationError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        if attempt == 0:
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Ответ не прошёл валидацию по схеме. "
                        f"Ошибка: {last_error}. Верни строго один валидный JSON-объект без пояснений."
                    ),
                },
            ]
    raise LLMUnavailable(f"chat_structured: валидный JSON не получен: {last_error}")


def embed_many(texts: list[str], *, kind: str = "query", timeout: float = EMBED_TIMEOUT_SEC) -> list[list[float]]:
    """Эмбеддинги пачки текстов (256-dim); kind: 'doc' (индексация) или 'query' (поиск)."""
    if not texts:
        return []
    body = {"model": embedding_model_uri(kind), "input": texts}
    data = _post("/embeddings", body, timeout)
    rows = data.get("data")
    if rows is not None:  # OpenAI-совместимый формат
        rows = sorted(rows, key=lambda r: r.get("index", 0))
        try:
            return [[float(x) for x in row["embedding"]] for row in rows]
        except (KeyError, TypeError) as exc:
            raise LLMUnavailable(f"embed: неожиданный формат ответа: {exc}") from exc
    vectors = data.get("embeddings")  # нативный формат Yandex
    if vectors:
        return [[float(x) for x in vec] for vec in vectors]
    raise LLMUnavailable("embed: в ответе нет ни data, ни embeddings")


def embed(text: str, *, kind: str = "query", timeout: float = EMBED_TIMEOUT_SEC) -> list[float]:
    """Эмбеддинг одного текста."""
    return embed_many([text], kind=kind, timeout=timeout)[0]
