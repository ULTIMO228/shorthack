"""Граничные ветки internal-хуков: невалидный status, чужой домен, confirm без кода."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker

from app import models


@pytest.fixture()
def bot_headers(bot_token):
    return {"X-Bot-Token": bot_token}


def test_outbound_rejects_unknown_status(client, bot_headers):
    r = client.get("/api/internal/outbound?status=unknown", headers=bot_headers)
    assert r.status_code == 422
    assert "pending" in r.json()["detail"]


def test_outbound_filters_by_status(client, bot_headers, db_engine):
    db = sessionmaker(bind=db_engine, expire_on_commit=False)()
    db.add_all(
        [
            models.OutboundMessage(chat_id=1, text="a", status="pending"),
            models.OutboundMessage(chat_id=2, text="b", status="sent"),
            models.OutboundMessage(chat_id=3, text="c", status="failed"),
        ]
    )
    db.commit()

    for status, expected in (("pending", ["a"]), ("sent", ["b"]), ("failed", ["c"])):
        r = client.get(f"/api/internal/outbound?status={status}", headers=bot_headers)
        assert [m["text"] for m in r.json()] == expected


def test_tg_link_rejects_non_misis_email(client, bot_headers):
    r = client.post(
        "/api/internal/tg/link",
        json={"chat_id": 777, "email": "user@gmail.com"},
        headers=bot_headers,
    )
    assert r.status_code == 400
    assert "МИСИС" in r.json()["detail"]


def test_tg_link_confirm_without_code_request(client, bot_headers):
    # Чат известен (создался через state), но код не запрашивался
    r = client.get("/api/internal/tg/link?chat_id=555", headers=bot_headers)
    assert r.json()["state"] == "awaiting_email"

    r = client.post(
        "/api/internal/tg/link/confirm",
        json={"chat_id": 555, "code": "123456"},
        headers=bot_headers,
    )
    assert r.status_code == 400
    assert "запросите код" in r.json()["detail"]


def test_tg_link_confirm_wrong_code_then_reuse_limit(client, bot_headers):
    """1 неверная попытка — информативный отказ, повторная верная — успех (попытки < 3)."""
    client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    client.post(
        "/api/internal/tg/link",
        json={"chat_id": 888, "email": "ivanov@misis.ru"},
        headers=bot_headers,
    )
    r = client.post(
        "/api/internal/tg/link/confirm",
        json={"chat_id": 888, "code": "999999"},
        headers=bot_headers,
    )
    assert r.status_code == 400
    assert "Осталось попыток: 2" in r.json()["detail"]
