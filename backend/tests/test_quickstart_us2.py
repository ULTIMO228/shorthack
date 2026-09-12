"""Сценарии 2-3 quickstart.md (US2): end-to-end через POST /api/requests.

Сценарий 2: живой misis.ru → проверка check_site (httpx замокан на 200) →
реакция clarification, тикет «ждёт ответа пользователя».
Сценарий 3: MISIS-EDU в состоянии down → реакция outage_notice ≤ 2 c без вызовов LLM
в ветке сбоя (FR-026, SC-004), приоритет critical (FR-011), заявка «решена».
Вариант сценария 3 через реальный HTTP-код: newlms отвечает 500 → outage_notice.
PATCH /api/admin/services/{id} как переключатель — в test_tools.py.

LLM мокается через make_fake_llm (test_agent.py) с обёрткой-счётчиком вызовов.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import llm
from app.models import Event, Service, ServiceCheck
from app.seed import SERVICES
from test_agent import make_fake_llm
from test_tools import FakeHttpResponse, fake_http  # noqa: F401  (фикстура fake_http из test_tools)


@pytest.fixture(autouse=True)
def seed_services(db_engine):
    """5 сервисов из seed.SERVICES — нода auto_check требует строк сервисов в БД."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    if db.scalar(select(Service).limit(1)) is None:
        db.add_all(Service(name=n, check_type=t, state=s) for n, t, s in SERVICES)
        db.commit()
    db.close()


def make_counting_llm(monkeypatch, **kwargs) -> list:
    """make_fake_llm + счётчик вызовов llm.chat; вернуть список вызовов."""
    calls: list = []
    make_fake_llm(monkeypatch, **kwargs)
    original = llm.chat

    def counting(messages, **kw):
        calls.append(messages)
        return original(messages, **kw)

    monkeypatch.setattr("app.llm.chat", counting)
    return calls


def _set_service_state(db_engine, name: str, state: str) -> None:
    """Эмуляция ручного переключения сети (PATCH /api/admin/services — фаза Polish)."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    service = db.query(Service).filter_by(name=name).first()
    if service is None:
        service = Service(name=name, check_type="emulated", state=state)
        db.add(service)
    else:
        service.state = state
    db.commit()
    db.close()


def _db_rows(db_engine, model):
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    rows = db.query(model).all()
    db.close()
    return rows


# ---------------------------------------------------------------------------
# Сценарий 2: сайт живой → уточняющий вопрос
# ---------------------------------------------------------------------------

def test_quickstart_scenario_2_live_site_asks_clarification(client, monkeypatch, fake_http, db_engine):
    llm_calls = make_counting_llm(
        monkeypatch,
        classify={
            "route": "auto_check", "service": "site", "category": "availability",
            "priority": "high", "confidence": 0.91, "reason": "Жалоба на недоступность сайта",
        },
    )
    fake_http["https://misis.ru"] = FakeHttpResponse(200)

    resp = client.post("/api/requests", json={"text": "Не открывается сайт misis.ru", "channel": "web"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["duplicate"] is False
    sub = data["subtasks"][0]
    assert sub["route"] == "auto_check" and sub["service"] == "site"
    reaction = data["reactions"][0]
    assert reaction["kind"] == "clarification"
    assert "misis.ru" in reaction["text"]
    assert data["ticket"]["status"] == "ждёт ответа пользователя"

    # проверка выполнена и зафиксирована (service_checks)
    checks = _db_rows(db_engine, ServiceCheck)
    assert len(checks) == 1
    assert checks[0].ok is True and checks[0].http_code == 200
    # в журнале отметки инструмента
    events = _db_rows(db_engine, Event)
    tool_events = [e for e in events if e.actor == "tool:check_site"]
    assert {e.action for e in tool_events} == {"tool_call", "tool_result"}
    # LLM вызывался только на сплиттер и классификатор
    assert len(llm_calls) == 2


# ---------------------------------------------------------------------------
# Сценарий 3: MISIS-EDU в down → мгновенное извещение, critical, ≤ 2 c, без LLM
# ---------------------------------------------------------------------------

def test_quickstart_scenario_3_wifi_down_outage_notice(client, monkeypatch, fake_http, db_engine):
    llm_calls = make_counting_llm(
        monkeypatch,
        classify={
            "route": "auto_check", "service": "wifi_edu", "category": "availability",
            "priority": "high", "confidence": 0.88, "reason": "Жалоба на доступность сети",
        },
    )
    _set_service_state(db_engine, "MISIS-EDU", "down")

    started = time.perf_counter()
    resp = client.post(
        "/api/requests",
        json={"text": "Не работает Wi-Fi MISIS-EDU в 4 корпусе", "channel": "web"},
    )
    elapsed = time.perf_counter() - started

    assert resp.status_code == 200, resp.text
    data = resp.json()
    reaction = data["reactions"][0]
    assert reaction["kind"] == "outage_notice"
    assert "MISIS-EDU" in reaction["text"]
    # приоритет поднят до critical подтверждённым сбоем (FR-011)
    assert data["subtasks"][0]["priority"] == "critical"
    # SC-004: извещение выдано быстро и без модели
    assert elapsed < 2.0
    assert len(llm_calls) == 2  # split + classify; ветка сбоя LLM не звала
    # заявка зафиксирована с результатом проверки
    assert data["ticket"]["status"] == "решена"
    checks = _db_rows(db_engine, ServiceCheck)
    assert len(checks) == 1 and checks[0].ok is False
    events = _db_rows(db_engine, Event)
    assert any(e.actor == "tool:check_wifi" and e.action == "tool_result" for e in events)


def test_quickstart_scenario_3_lms_http_500_outage(client, monkeypatch, fake_http, db_engine):
    """Вариант сбоя через реальный HTTP-код: newlms отвечает 500 → outage_notice."""
    make_counting_llm(
        monkeypatch,
        classify={
            "route": "auto_check", "service": "lms", "category": "availability",
            "priority": "high", "confidence": 0.9, "reason": "Жалоба на недоступность LMS",
        },
    )
    fake_http["https://newlms.misis.ru"] = FakeHttpResponse(500)

    resp = client.post("/api/requests", json={"text": "Moodle не грузится", "channel": "web"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["reactions"][0]["kind"] == "outage_notice"
    assert "newlms" in data["reactions"][0]["text"]
    assert data["subtasks"][0]["priority"] == "critical"
    checks = _db_rows(db_engine, ServiceCheck)
    assert len(checks) == 1 and checks[0].ok is False and checks[0].http_code == 500
