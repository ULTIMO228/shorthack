"""Тесты тест-панели оператора (FR-020, FR-024, T045).

Проверяет:
- GET /api/admin/status-board (сервисы + последняя проверка, порядок по id, 401/403),
- PATCH /api/admin/services/{id} (emulated переключается, real -> 409, 404),
- GET /api/admin/tools (реестр ADMIN_INVOKABLE, типы, параметры),
- POST /api/admin/tools/{name}/invoke (check_site, check_wifi, simulate_wave, 404 для неизвестных, 422 для невалидных params).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession, sessionmaker

from app import tools
from app.models import Event, Service, ServiceCheck, User
from tests.conftest import FakeHttpResponse


@pytest.fixture()
def db_session(db_engine):
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()


def test_status_board_returns_services_with_last_check(db_session, operator_client, client):
    """2 сервиса: у одного 2 проверки -> last_check самая поздняя; у второго last_check=None.
    Порядок по services.id; гость -> 401."""
    # Гость
    assert client.get("/api/admin/status-board").status_code == 401

    s1 = Service(name="misis.ru", check_type="real", state="up")
    s2 = Service(name="MISIS-EDU", check_type="emulated", state="up")
    db_session.add_all([s1, s2])
    db_session.commit()

    now = datetime.now(timezone.utc)
    c1 = ServiceCheck(
        service_id=s1.id,
        ok=True,
        http_code=200,
        latency_ms=120,
        checked_at=now - timedelta(seconds=60),
    )
    c2 = ServiceCheck(
        service_id=s1.id,
        ok=False,
        http_code=500,
        latency_ms=300,
        checked_at=now,  # более поздняя проверка
    )
    db_session.add_all([c1, c2])
    db_session.commit()

    resp = operator_client.get("/api/admin/status-board")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 2
    assert data[0]["name"] == "misis.ru"
    assert data[0]["id"] == s1.id
    assert data[0]["last_check"] is not None
    assert data[0]["last_check"]["http_code"] == 500
    assert data[0]["last_check"]["ok"] is False

    assert data[1]["name"] == "MISIS-EDU"
    assert data[1]["id"] == s2.id
    assert data[1]["last_check"] is None


def test_status_board_operator_only(client, db_session):
    """Студент -> 403."""
    student = User(email="student@edu.misis.ru", full_name="Студент", role="student")
    db_session.add(student)
    db_session.commit()

    login_resp = client.post("/api/auth/login", json={"email": "student@edu.misis.ru"})
    assert login_resp.status_code == 200

    resp = client.get("/api/admin/status-board")
    assert resp.status_code == 403


def test_patch_service_emulated_ok(db_session, operator_client):
    """PATCH emulated-сервиса down -> 200, service.state=='down', event service_state_change."""
    s = Service(name="MISIS-Guest", check_type="emulated", state="up")
    db_session.add(s)
    db_session.commit()

    resp = operator_client.patch(f"/api/admin/services/{s.id}", json={"state": "down"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["service"]["state"] == "down"

    db_session.refresh(s)
    assert s.state == "down"

    ev = db_session.scalars(
        select(Event).where(Event.action == "service_state_change")
    ).first()
    assert ev is not None
    assert ev.actor == "operator"


def test_patch_service_real_conflict_409(db_session, operator_client):
    """PATCH misis.ru (check_type=real) -> 409, state в БД не изменился."""
    s = Service(name="misis.ru", check_type="real", state="up")
    db_session.add(s)
    db_session.commit()

    resp = operator_client.patch(f"/api/admin/services/{s.id}", json={"state": "down"})
    assert resp.status_code == 409

    db_session.refresh(s)
    assert s.state == "up"


def test_patch_service_not_found_404(operator_client):
    """id=999 -> 404."""
    resp = operator_client.patch("/api/admin/services/999", json={"state": "down"})
    assert resp.status_code == 404


def test_list_tools_registry_shape(operator_client):
    """200, ровно 4 инструмента в порядке ADMIN_INVOKABLE, типы и параметры."""
    resp = operator_client.get("/api/admin/tools")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 4

    names = [t["name"] for t in data]
    assert names == list(tools.ADMIN_INVOKABLE)

    sim_tool = next(t for t in data if t["name"] == "simulate_wave")
    assert sim_tool["type"] == "simulation"
    sim_params = {p["name"]: p for p in sim_tool["params"]}
    assert "service" in sim_params
    assert "count" in sim_params
    assert sim_params["count"]["default"] == 3

    wifi_tool = next(t for t in data if t["name"] == "check_wifi")
    assert wifi_tool["type"] == "exec"
    wifi_params = {p["name"]: p for p in wifi_tool["params"]}
    assert "network" in wifi_params


def test_invoke_check_wifi_up_and_down(db_session, operator_client):
    """Эмулированные сети up/down -> ok true/false, result.state."""
    s_edu = Service(name="MISIS-EDU", check_type="emulated", state="up")
    db_session.add(s_edu)
    db_session.commit()

    # 1. Проверка up
    resp1 = operator_client.post(
        "/api/admin/tools/check_wifi/invoke",
        json={"params": {"network": "wifi_edu"}},
    )
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1["ok"] is True
    assert data1["result"]["state"] == "up"

    # 2. Переводим в down
    s_edu.state = "down"
    db_session.commit()

    resp2 = operator_client.post(
        "/api/admin/tools/check_wifi/invoke",
        json={"params": {"network": "wifi_edu"}},
    )
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert data2["ok"] is False
    assert data2["result"]["state"] == "down"


def test_invoke_check_site_with_fake_http(operator_client, fake_http):
    """fake_http 200 -> ok=true, http_code=200; 500 -> ok=false (HTTP-статус 200!)."""
    fake_http["https://misis.ru"] = FakeHttpResponse(200)

    resp1 = operator_client.post("/api/admin/tools/check_site/invoke", json={"params": {}})
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()
    assert data1["ok"] is True
    assert data1["result"]["http_code"] == 200

    # 500 на сайте
    fake_http["https://misis.ru"] = FakeHttpResponse(500)
    resp2 = operator_client.post("/api/admin/tools/check_site/invoke", json={"params": {}})
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert data2["ok"] is False
    assert data2["result"]["http_code"] == 500


def test_invoke_unknown_tool_404(operator_client):
    """Несуществующее имя и llm-инструмент реестра (kb_agent) -> 404."""
    assert operator_client.post("/api/admin/tools/non_existent/invoke", json={}).status_code == 404
    assert operator_client.post("/api/admin/tools/kb_agent/invoke", json={}).status_code == 404


def test_invoke_params_validation_422(operator_client):
    """Валидация параметров:
    - check_wifi без network -> 422
    - check_wifi network="internet" -> 422
    - simulate_wave count=0 и =99 -> 422
    - лишний ключ -> 422
    - check_site url='https://evil.example' -> 422
    """
    # check_wifi без network
    assert operator_client.post(
        "/api/admin/tools/check_wifi/invoke", json={"params": {}}
    ).status_code == 422

    # check_wifi network='internet'
    assert operator_client.post(
        "/api/admin/tools/check_wifi/invoke", json={"params": {"network": "internet"}}
    ).status_code == 422

    # simulate_wave count=0
    assert operator_client.post(
        "/api/admin/tools/simulate_wave/invoke",
        json={"params": {"service": "wifi_edu", "count": 0}},
    ).status_code == 422

    # simulate_wave count=99
    assert operator_client.post(
        "/api/admin/tools/simulate_wave/invoke",
        json={"params": {"service": "wifi_edu", "count": 99}},
    ).status_code == 422

    # лишний ключ
    assert operator_client.post(
        "/api/admin/tools/check_wifi/invoke",
        json={"params": {"network": "wifi_edu", "extra_key": 123}},
    ).status_code == 422

    # check_site url='https://evil.example'
    assert operator_client.post(
        "/api/admin/tools/check_site/invoke",
        json={"params": {"url": "https://evil.example"}},
    ).status_code == 422


def test_invoke_simulate_wave_delegates_to_incidents(operator_client, client, db_session, monkeypatch):
    """Делегирование в incidents.simulate_wave; гость -> 401, студент -> 403."""
    # 1. Гость 401
    assert client.post(
        "/api/admin/tools/simulate_wave/invoke",
        json={"params": {"service": "wifi_edu", "count": 3}},
    ).status_code == 401

    # 2. Студент 403
    student = User(email="student2@edu.misis.ru", full_name="Студент", role="student")
    db_session.add(student)
    db_session.commit()
    client.post("/api/auth/login", json={"email": "student2@edu.misis.ru"})
    assert client.post(
        "/api/admin/tools/simulate_wave/invoke",
        json={"params": {"service": "wifi_edu", "count": 3}},
    ).status_code == 403

    # 3. Оператор: spy
    called_with: dict = {}

    def spy_simulate_wave(db, *, service: str, count: int = 3):
        called_with["service"] = service
        called_with["count"] = count
        return {"incident_id": 42, "created_requests": [101, 102, 103]}

    monkeypatch.setattr("app.incidents.simulate_wave", spy_simulate_wave)

    resp = operator_client.post(
        "/api/admin/tools/simulate_wave/invoke",
        json={"params": {"service": "wifi_edu", "count": 3}},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tool"] == "simulate_wave"
    assert data["ok"] is True
    assert data["result"]["incident_id"] == 42
    assert data["result"]["created_requests"] == [101, 102, 103]
    assert called_with["service"] == "wifi_edu"
    assert called_with["count"] == 3


def test_invoke_params_null_values_handling(operator_client, monkeypatch):
    """Null-значения: required -> 422 (не 500 KeyError), optional с default -> использует default."""
    # 1. Required null -> 422
    resp1 = operator_client.post(
        "/api/admin/tools/simulate_wave/invoke",
        json={"params": {"service": None}},
    )
    assert resp1.status_code == 422
    assert "service" in resp1.json()["detail"]

    resp2 = operator_client.post(
        "/api/admin/tools/check_wifi/invoke",
        json={"params": {"network": None}},
    )
    assert resp2.status_code == 422
    assert "network" in resp2.json()["detail"]

    # 2. Optional с default передано как null -> подставляется default
    spy_called: dict = {}

    def spy_simulate(db, *, service: str, count: int = 3):
        spy_called["service"] = service
        spy_called["count"] = count
        return {"incident_id": None, "created_requests": []}

    monkeypatch.setattr("app.incidents.simulate_wave", spy_simulate)
    resp3 = operator_client.post(
        "/api/admin/tools/simulate_wave/invoke",
        json={"params": {"service": "site", "count": None}},
    )
    assert resp3.status_code == 200
    assert spy_called["service"] == "site"
    assert spy_called["count"] == 3

