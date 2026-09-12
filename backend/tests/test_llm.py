"""Юнит-тесты клиента Yandex AI Studio (T007): chat / chat_structured / embed.

HTTP полностью имитируется подменой httpx.Client — реальных сетевых вызовов нет.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app import llm
from app.schemas import ClassifierResult


@pytest.fixture()
def yandex_env(monkeypatch):
    """Ключи Yandex в env; значения моделей из env убраны (дефолты кода)."""
    monkeypatch.setenv("YANDEX_API_KEY", "test-key")
    monkeypatch.setenv("YANDEX_FOLDER_ID", "test-folder")
    monkeypatch.delenv("YANDEX_MODEL_CLASSIFY", raising=False)
    monkeypatch.delenv("YANDEX_MODEL_GENERATE", raising=False)
    monkeypatch.delenv("YANDEX_MODEL_EMBED_DOC", raising=False)
    monkeypatch.delenv("YANDEX_MODEL_EMBED_QUERY", raising=False)
    return monkeypatch


@pytest.fixture()
def fake_http(monkeypatch):
    """Подмена httpx.Client: ответы берутся из очереди queue.

    Элемент очереди: dict — успешный JSON-ответ; Exception — поднять при post()
    (httpx.ConnectError и т.п.); ("status", код) — raise_for_status() бросает
    httpx.HTTPStatusError (имитация 5xx от API).
    """
    state = SimpleNamespace(calls=[], queue=[])

    class _Response:
        def __init__(self, item):
            self._item = item

        def raise_for_status(self):
            if isinstance(self._item, tuple) and self._item[0] == "status":
                request = httpx.Request("POST", "http://fake")
                response = httpx.Response(self._item[1], request=request)
                raise httpx.HTTPStatusError("server error", request=request, response=response)

        def json(self):
            if isinstance(self._item, Exception):
                raise self._item
            return self._item

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, path, json=None, headers=None):
            state.calls.append({"path": path, "body": json, "headers": headers})
            item = state.queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return _Response(item)

    monkeypatch.setattr(llm.httpx, "Client", _Client)
    return state


def _chat_payload(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


# ---------------------------------------------------------------------------
# chat()
# ---------------------------------------------------------------------------

def test_chat_success_normalizes_messages_and_headers(yandex_env, fake_http):
    fake_http.queue.append(_chat_payload("привет"))
    result = llm.chat("как дела")

    assert result == "привет"
    call = fake_http.calls[0]
    assert call["path"] == "/chat/completions"
    assert call["body"]["model"] == "gpt://test-folder/yandexgpt/latest"
    assert call["body"]["messages"] == [
        {"role": "system", "content": "Отвечай только на русском языке."},
        {"role": "user", "content": "как дела"},
    ]
    assert call["headers"]["Authorization"] == "Api-Key test-key"
    assert call["headers"]["OpenAI-Project"] == "test-folder"


def test_chat_accepts_message_list_and_custom_model(yandex_env, fake_http):
    fake_http.queue.append(_chat_payload("ok"))
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    llm.chat(messages, model="gpt://custom/x", temperature=0.9)

    call = fake_http.calls[0]
    assert call["body"]["model"] == "gpt://custom/x"
    assert call["body"]["messages"] == messages
    assert call["body"]["temperature"] == 0.9


def test_chat_without_key_raises_unavailable(monkeypatch, fake_http):
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    with pytest.raises(llm.LLMUnavailable, match="YANDEX_API_KEY"):
        llm.chat("x")


def test_chat_without_folder_omits_project_header(monkeypatch, fake_http):
    monkeypatch.setenv("YANDEX_API_KEY", "k")
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)
    fake_http.queue.append(_chat_payload("ok"))
    llm.chat("x")
    assert "OpenAI-Project" not in fake_http.calls[0]["headers"]


def test_chat_connect_error_wrapped(yandex_env, fake_http):
    fake_http.queue.append(httpx.ConnectError("boom"))
    with pytest.raises(llm.LLMUnavailable):
        llm.chat("x")


def test_chat_http_status_error_wrapped(yandex_env, fake_http):
    fake_http.queue.append(("status", 500))
    with pytest.raises(llm.LLMUnavailable, match="server error"):
        llm.chat("x")


def test_chat_invalid_json_body_wrapped(yandex_env, fake_http):
    fake_http.queue.append(ValueError("no json"))
    with pytest.raises(llm.LLMUnavailable):
        llm.chat("x")


def test_chat_malformed_payload_raises(yandex_env, fake_http):
    fake_http.queue.append({"unexpected": True})
    with pytest.raises(llm.LLMUnavailable, match="формат ответа"):
        llm.chat("x")


# ---------------------------------------------------------------------------
# chat_structured() и _extract_json()
# ---------------------------------------------------------------------------

_VALID_CLASSIFY = json.dumps(
    {
        "route": "auto_check",
        "service": "site",
        "category": "availability",
        "priority": "high",
        "confidence": 0.91,
        "reason": "Жалоба на доступность",
    },
    ensure_ascii=False,
)


def test_chat_structured_success_with_fenced_json(yandex_env, fake_http):
    fake_http.queue.append(_chat_payload(f"```json\n{_VALID_CLASSIFY}\n```"))
    result = llm.chat_structured("текст", ClassifierResult)

    assert isinstance(result, ClassifierResult)
    assert result.route == "auto_check"
    assert result.confidence == 0.91
    call = fake_http.calls[0]
    assert call["body"]["model"] == "gpt://test-folder/yandexgpt/latest"  # chat_structured -> chat() default
    assert call["body"]["temperature"] == 0.1


def test_chat_structured_extracts_json_from_prose(yandex_env, fake_http):
    fake_http.queue.append(_chat_payload(f"Вот ответ: {_VALID_CLASSIFY} — всё."))
    result = llm.chat_structured("текст", ClassifierResult)
    assert result.service == "site"


def test_extract_json_without_object_raises():
    with pytest.raises(json.JSONDecodeError):
        llm._extract_json("тут вообще нет объекта")


def test_chat_structured_retries_once_on_garbage(yandex_env, fake_http):
    fake_http.queue.append(_chat_payload("не json, а строка"))
    fake_http.queue.append(_chat_payload(_VALID_CLASSIFY))

    result = llm.chat_structured("текст", ClassifierResult)
    assert result.route == "auto_check"
    assert len(fake_http.calls) == 2
    # retry-итог: подсказка о JSON добавлена в историю
    last_message = fake_http.calls[1]["body"]["messages"][-1]["content"]
    assert "валидный JSON" in last_message


def test_chat_structured_retries_on_validation_error(yandex_env, fake_http):
    fake_http.queue.append(_chat_payload(json.dumps({"route": "kb"})))  # не хватает полей
    fake_http.queue.append(_chat_payload(_VALID_CLASSIFY))

    result = llm.chat_structured("текст", ClassifierResult)
    assert result.priority == "high"
    assert len(fake_http.calls) == 2


def test_chat_structured_gives_up_after_retries(yandex_env, fake_http):
    fake_http.queue.append(_chat_payload("мусор 1"))
    fake_http.queue.append(_chat_payload("мусор 2"))
    with pytest.raises(llm.LLMUnavailable, match="валидный JSON не получен"):
        llm.chat_structured("текст", ClassifierResult)
    assert len(fake_http.calls) == 2  # 1 попытка + 1 retry (MAX_RETRIES=1)


# ---------------------------------------------------------------------------
# embed() / embed_many()
# ---------------------------------------------------------------------------

def test_embed_openai_format_sorted_by_index(yandex_env, fake_http):
    fake_http.queue.append(
        {"data": [{"embedding": [1.0, 2.0], "index": 1}, {"embedding": [3.0, 4.0], "index": 0}]}
    )
    vectors = llm.embed_many(["a", "b"])
    assert vectors == [[3.0, 4.0], [1.0, 2.0]]
    call = fake_http.calls[0]
    assert call["path"] == "/embeddings"
    assert call["body"]["input"] == ["a", "b"]
    assert call["body"]["model"] == "emb://test-folder/text-search-query/latest"


def test_embed_doc_kind_uses_doc_model(yandex_env, fake_http):
    fake_http.queue.append({"data": [{"embedding": [0.5], "index": 0}]})
    llm.embed("текст", kind="doc")
    assert "text-search-doc/latest" in fake_http.calls[0]["body"]["model"]


def test_embed_empty_input_skips_http(yandex_env, fake_http):
    assert llm.embed_many([]) == []
    assert fake_http.calls == []


def test_embed_native_yandex_format(yandex_env, fake_http):
    fake_http.queue.append({"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    assert llm.embed_many(["a", "b"], kind="doc") == [[0.1, 0.2], [0.3, 0.4]]


def test_embed_response_without_vectors_raises(yandex_env, fake_http):
    fake_http.queue.append({"something": "else"})
    with pytest.raises(llm.LLMUnavailable, match="ни data, ни embeddings"):
        llm.embed_many(["a"])


def test_embed_malformed_rows_raise(yandex_env, fake_http):
    fake_http.queue.append({"data": [{"index": 0}]})  # нет embedding
    with pytest.raises(llm.LLMUnavailable, match="формат ответа"):
        llm.embed_many(["a"])


# ---------------------------------------------------------------------------
# Конфигурация моделей и llm_up()
# ---------------------------------------------------------------------------

def test_model_uri_raw_value_passed_through(yandex_env, monkeypatch):
    monkeypatch.setenv("YANDEX_MODEL_CLASSIFY", "gpt://custom/yandexgpt-lite/latest")
    assert llm.classify_model_uri() == "gpt://custom/yandexgpt-lite/latest"


def test_model_uri_without_folder(yandex_env, monkeypatch):
    monkeypatch.delenv("YANDEX_FOLDER_ID", raising=False)
    assert llm.classify_model_uri() == "gpt:///yandexgpt-lite/latest"


def test_llm_up_states(yandex_env, monkeypatch):
    assert llm.llm_up() is True
    monkeypatch.setenv("YANDEX_API_KEY", "placeholder")
    assert llm.llm_up() is False
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    assert llm.llm_up() is False
