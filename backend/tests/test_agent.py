"""Пайплайн агента (US1) с моком llm.chat: маршруты, сплиттер, safe-route, дедуп, диалог.

Мок диспетчеризуется по маркерам в промпте: «Переведи» → перевод, '"subtasks"' → сплиттер,
иначе — ответ классификатора (chat_structured сам валидирует JSON по схеме).
"""

from __future__ import annotations

import json

import pytest

from app import llm


def make_fake_llm(monkeypatch, *, classify: dict | None = None, split: dict | None = None,
                  translate: str | None = None, unavailable: bool = False) -> None:
    """Подмена app.llm.chat: отдаёт заготовленный JSON классификатора/сплиттера."""

    def fake_chat(messages, *, model=None, temperature=0.2, timeout=25.0):
        if unavailable:
            raise llm.LLMUnavailable("модель недоступна (тест)")
        text = messages if isinstance(messages, str) else "\n".join(m["content"] for m in messages)
        if "Переведи" in text:
            return translate or "Не работает Wi-Fi."
        if '"subtasks"' in text:
            payload = split or {"subtasks": [{"summary": "Единственная проблема"}]}
            return json.dumps(payload, ensure_ascii=False)
        payload = classify or {
            "route": "auto_check",
            "service": "wifi_guest",
            "category": "availability",
            "priority": "high",
            "confidence": 0.91,
            "reason": "Жалоба на доступность сети",
        }
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr("app.llm.chat", fake_chat)


@pytest.fixture(autouse=True)
def seed_services(db_engine):
    """5 сервисов из seed.SERVICES — conftest не сидирует, а ветка auto_check (US2)
    после T023–T026 выполняет реальные проверки и требует строк сервисов в БД."""
    from sqlalchemy import select
    from sqlalchemy.orm import sessionmaker

    from app.models import Service
    from app.seed import SERVICES

    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    if db.scalar(select(Service).limit(1)) is None:
        db.add_all(Service(name=n, check_type=t, state=s) for n, t, s in SERVICES)
        db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Подача обращения: маршрутизация и реакции
# ---------------------------------------------------------------------------

def test_simple_request_auto_check(client, monkeypatch):
    make_fake_llm(monkeypatch)
    resp = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest", "channel": "web"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["duplicate"] is False
    assert len(data["subtasks"]) == 1
    sub = data["subtasks"][0]
    assert sub["route"] == "auto_check"
    assert sub["service"] == "wifi_guest"
    assert sub["category"] == "availability"
    assert sub["priority"] == "high"
    assert sub["confidence"] == pytest.approx(0.91)
    assert sub["route_reason"].startswith("Жалоба на доступность сети")
    assert "триггеры" in sub["route_reason"]  # «не работает» подняло приоритет-подтверждение
    assert data["reactions"][0]["kind"] == "clarification"
    ticket = data["ticket"]
    assert ticket["number"].startswith("SUP-2026-")
    assert ticket["status"] == "ждёт ответа пользователя"
    assert ticket["escalated"] is False


def test_composite_request_split_into_three(client, monkeypatch):
    make_fake_llm(
        monkeypatch,
        split={
            "subtasks": [
                {"summary": "Недоступен сайт misis.ru"},
                {"summary": "Не открывается newlms.misis.ru"},
                {"summary": "Как сбросить пароль от почты"},
            ]
        },
    )
    resp = client.post(
        "/api/requests",
        json={"text": "Не работает сайт, не открывается лмс и ещё как сбросить пароль"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data["subtasks"]) == 3
    assert [s["position"] for s in data["subtasks"]] == [1, 2, 3]
    # все три подзадачи получили маршрут от классификатора
    assert all(s["route"] == "auto_check" for s in data["subtasks"])


def test_low_confidence_escalates(client, monkeypatch):
    make_fake_llm(
        monkeypatch,
        classify={
            "route": "kb",
            "service": "other",
            "category": "howto",
            "priority": "medium",
            "confidence": 0.42,
            "reason": "Неоднозначная формулировка",
        },
    )
    resp = client.post("/api/requests", json={"text": "Что-то странное творится"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["subtasks"][0]["route"] == "escalate"
    assert data["reactions"][0]["kind"] == "escalated"
    assert data["ticket"]["escalated"] is True


def test_llm_unavailable_safe_route(client, monkeypatch):
    make_fake_llm(monkeypatch, unavailable=True)
    resp = client.post("/api/requests", json={"text": "Не работает Wi-Fi"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["subtasks"][0]["route"] == "escalate"
    assert data["reactions"][0]["kind"] == "escalated"


def test_empty_text_422(client, monkeypatch):
    make_fake_llm(monkeypatch)
    resp = client.post("/api/requests", json={"text": "   "})
    assert resp.status_code == 422


def test_profanity_masked_and_english_translated(client, monkeypatch):
    make_fake_llm(monkeypatch, translate="Не работает Wi-Fi в общежитии.")
    resp = client.post("/api/requests", json={"text": "fucking wifi does not work, сука"})
    assert resp.status_code == 200, resp.text
    detail = client.get(f"/api/requests/{resp.json()['request_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["lang"] == "en"
    assert body["translation"] == "Не работает Wi-Fi в общежитии."
    assert "с***" in body["masked_text"]
    assert "f******" in body["masked_text"]


# ---------------------------------------------------------------------------
# Дедупликация и диалог
# ---------------------------------------------------------------------------

def test_duplicate_request_attached_to_open_ticket(client, monkeypatch):
    make_fake_llm(monkeypatch)
    first = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    assert first.status_code == 200
    second = client.post("/api/requests", json={"text": "Опять не работает вайфай MISIS-Guest"})
    assert second.status_code == 200, second.text
    data = second.json()
    assert data["duplicate"] is True
    assert data["ticket"]["id"] == first.json()["ticket"]["id"]
    assert data["reactions"][0]["kind"] == "answer"


def test_no_duplicate_after_ticket_resolved(client, monkeypatch):
    """Решённая заявка (status «решена») в дедупликацию не попадает (FR-014 — только открытые)."""
    make_fake_llm(
        monkeypatch,
        classify={"route": "cert_order", "service": "certs", "category": "cert_order",
                  "priority": "low", "confidence": 0.95, "reason": "Запрос справки"},
    )
    first = client.post("/api/requests", json={"text": "Нужна справка с места учёбы"})
    assert first.status_code == 200
    assert first.json()["reactions"][0]["kind"] == "auth_required"
    assert first.json()["ticket"]["status"] == "решена"

    again = client.post("/api/requests", json={"text": "Нужна справка с места учёбы"})
    assert again.status_code == 200
    assert again.json()["duplicate"] is False


def test_reply_dialog_two_rounds_limit(client, monkeypatch):
    make_fake_llm(monkeypatch)
    created = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    request_id = created.json()["request_id"]
    r1 = client.post(f"/api/requests/{request_id}/reply", json={"text": "На всех устройствах"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["ticket"]["status"] == "ждёт ответа пользователя"
    r2 = client.post(f"/api/requests/{request_id}/reply", json={"text": "Ошибка «не удалось подключиться»"})
    assert r2.status_code == 200, r2.text
    # FR-025: после 2-го раунда уточнений — эскалация оператору с историей переписки
    assert r2.json()["ticket"]["escalated"] is True
    assert r2.json()["ticket"]["status"] == "в работе"
    assert r2.json()["reactions"][0]["kind"] == "escalated"
    r3 = client.post(f"/api/requests/{request_id}/reply", json={"text": "Ну что там?"})
    assert r3.status_code == 409
    assert "завершён" in r3.json()["detail"]


def test_reply_forbidden_for_stranger(client, monkeypatch):
    make_fake_llm(monkeypatch)
    created = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    request_id = created.json()["request_id"]
    other = client.__class__(client.app)  # второй клиент — другая cookie-сессия
    resp = other.post(f"/api/requests/{request_id}/reply", json={"text": "На всех устройствах"})
    assert resp.status_code == 403


def test_get_requests_requires_login(client, monkeypatch):
    make_fake_llm(monkeypatch)
    resp = client.get("/api/requests")
    assert resp.status_code == 401
    assert "почте МИСИС" in resp.json()["detail"]


def test_get_request_detail_access(client, monkeypatch, db_engine):
    from sqlalchemy.orm import sessionmaker

    from app.models import User

    # оператор smirnov@misis.ru уже создан autouse-фикстурой (роль operator)
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    with maker() as setup_db:
        op = setup_db.query(User).filter_by(email="smirnov@misis.ru").one()
        assert op.role == "operator"

    make_fake_llm(monkeypatch)
    client.post("/api/auth/login", json={"email": "ivanov@misis.ru", "password": "student123"})
    created = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    request_id = created.json()["request_id"]
    own = client.get(f"/api/requests/{request_id}")
    assert own.status_code == 200
    dialog = own.json()["dialog"]
    assert len(dialog) == 1 and dialog[0]["role"] == "agent"  # уточняющий вопрос

    stranger = client.__class__(client.app)
    stranger.post("/api/auth/login", json={"email": "petrova@edu.misis.ru", "password": "student123"})
    forbidden = stranger.get(f"/api/requests/{request_id}")
    assert forbidden.status_code == 403

    operator = client.__class__(client.app)
    operator.post("/api/auth/login", json={"email": "smirnov@misis.ru", "password": "operator123"})
    allowed = operator.get(f"/api/requests/{request_id}")
    assert allowed.status_code == 200


def test_guest_can_read_own_request_detail(client, monkeypatch):
    make_fake_llm(monkeypatch)
    created = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    detail = client.get(f"/api/requests/{created.json()['request_id']}")
    assert detail.status_code == 200
