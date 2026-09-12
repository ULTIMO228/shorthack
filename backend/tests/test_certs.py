"""Тесты функционала заказа справок (US4, T032): каталог, заказы, админка, агент."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import certs
from app.models import CertOrder, Event, KbArticle, Request, Subtask, Ticket, User
from app.schemas import CertCatalogItem
from tests.test_agent import make_fake_llm


@pytest.fixture()
def db_session(db_engine):
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    session = maker()
    yield session
    session.close()


def make_operator(db_engine, email="smirnov@misis.ru") -> User:
    """Создать оператора в БД до входа."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, full_name="Смирнов Олег", role="operator")
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    return user


def seed_regulation_doc(db_engine) -> KbArticle:
    """Документ «Регламент заказа справок» в тестовую БД."""
    maker = sessionmaker(bind=db_engine, expire_on_commit=False)
    db = maker()
    article = db.scalar(
        select(KbArticle).where(KbArticle.topic == "certs", KbArticle.kind == "document")
    )
    if article is None:
        article = KbArticle(
            kind="document",
            title="Регламент заказа справок",
            topic="certs",
            body="Справки оформляются через ИИ-помощника или личный кабинет. Каталог: 5 справок. Срок изготовления — 3 дня.",
        )
        db.add(article)
        db.commit()
        db.refresh(article)
    db.close()
    return article


# ---------------------------------------------------------------------------
# Каталог (FR-041)
# ---------------------------------------------------------------------------

def test_catalog_public_returns_five_types(client):
    """Без логина 200, 5 элементов, набор типов соответствует спецификации."""
    res = client.get("/api/certs/catalog")
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data) == 5
    types = {item["type"] for item in data}
    assert types == {"payments", "callup", "medical", "study", "military"}

    # Проверка схемы и содержимого
    for item in data:
        validated = CertCatalogItem(**item)
        assert validated.title
        assert validated.description

    study_item = next(item for item in data if item["type"] == "study")
    assert study_item["title"] == "Справка с места учёбы"
    assert "PDF" in study_item["description"]


# ---------------------------------------------------------------------------
# Пользовательские заказы (FR-040, FR-043)
# ---------------------------------------------------------------------------

def test_create_order_authorized(client, db_session):
    """Авторизованный пользователь заказывает справку -> статус 'не обработана'."""
    login_res = client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    assert login_res.status_code == 200

    order_res = client.post("/api/certs/orders", json={"cert_type": "study"})
    assert order_res.status_code == 200, order_res.text
    data = order_res.json()["order"]
    assert data["cert_type"] == "study"
    assert data["title"] == "Справка с места учёбы"
    assert data["status"] == "не обработана"
    assert data["created_at"]

    # Проверка в БД
    order_in_db = db_session.get(CertOrder, data["id"])
    assert order_in_db is not None
    assert order_in_db.cert_type == "study"
    assert order_in_db.status == "не обработана"


def test_create_order_guest_401(client, db_session):
    """Гость без авторизации получает 401, заказ не создаётся."""
    res = client.post("/api/certs/orders", json={"cert_type": "study"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Для заказа справки войдите по корпоративной почте МИСИС"

    count = db_session.scalar(select(CertOrder))
    assert count is None


def test_create_order_unknown_type_422(client):
    """Неизвестный тип справки отсекается валидацией Pydantic Literal (422)."""
    client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    res = client.post("/api/certs/orders", json={"cert_type": "diploma"})
    assert res.status_code == 422


def test_my_orders_returns_own_desc(app, db_engine):
    """Каждый пользователь видит только свои заказы с сортировкой по created_at desc."""
    client1 = TestClient(app)
    client2 = TestClient(app)

    client1.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    client2.post("/api/auth/login", json={"email": "petrov@misis.ru"})

    # Иванов заказывает 2 справки
    res1 = client1.post("/api/certs/orders", json={"cert_type": "study"})
    id1 = res1.json()["order"]["id"]
    res2 = client1.post("/api/certs/orders", json={"cert_type": "payments"})
    id2 = res2.json()["order"]["id"]

    # Петров заказывает 1 справку
    res3 = client2.post("/api/certs/orders", json={"cert_type": "military"})
    id3 = res3.json()["order"]["id"]

    # Иванов проверяет свои заказы
    orders1 = client1.get("/api/certs/orders").json()
    assert len(orders1) == 2
    assert [o["id"] for o in orders1] == [id2, id1]
    assert orders1[0]["updated_at"] is not None

    # Петров проверяет свои заказы
    orders2 = client2.get("/api/certs/orders").json()
    assert len(orders2) == 1
    assert orders2[0]["id"] == id3


def test_my_orders_guest_401(client):
    """Гость без авторизации получает 401 при запросе списка заказов."""
    res = client.get("/api/certs/orders")
    assert res.status_code == 401
    assert res.json()["detail"] == "Для заказа справки войдите по корпоративной почте МИСИС"


# ---------------------------------------------------------------------------
# Админка: смена статусов и цепочка (FR-042)
# ---------------------------------------------------------------------------

def test_admin_patch_full_forward_chain(app, db_engine):
    """Оператор последовательно проводит заказ по цепочке из 4 статусов."""
    make_operator(db_engine, "smirnov@misis.ru")

    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    create_res = student_client.post("/api/certs/orders", json={"cert_type": "study"})
    order_id = create_res.json()["order"]["id"]

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    # 1. не обработана -> обрабатывается
    patch1 = op_client.patch(
        f"/api/admin/certs/orders/{order_id}",
        json={"status": "обрабатывается"},
    )
    assert patch1.status_code == 200, patch1.text
    assert patch1.json()["order"]["status"] == "обрабатывается"
    # Студент видит новый статус
    assert student_client.get("/api/certs/orders").json()[0]["status"] == "обрабатывается"

    # 2. обрабатывается -> готова и ждёт выдачи
    patch2 = op_client.patch(
        f"/api/admin/certs/orders/{order_id}",
        json={"status": "готова и ждёт выдачи"},
    )
    assert patch2.status_code == 200
    assert patch2.json()["order"]["status"] == "готова и ждёт выдачи"
    assert student_client.get("/api/certs/orders").json()[0]["status"] == "готова и ждёт выдачи"

    # 3. готова и ждёт выдачи -> забрана
    patch3 = op_client.patch(
        f"/api/admin/certs/orders/{order_id}",
        json={"status": "забрана"},
    )
    assert patch3.status_code == 200
    assert patch3.json()["order"]["status"] == "забрана"
    assert student_client.get("/api/certs/orders").json()[0]["status"] == "забрана"


def test_admin_patch_backward_409(app, db_engine):
    """Попытка перевести статус назад -> 409 с точным текстом ошибки."""
    make_operator(db_engine, "smirnov@misis.ru")
    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    create_res = student_client.post("/api/certs/orders", json={"cert_type": "study"})
    order_id = create_res.json()["order"]["id"]

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})
    op_client.patch(f"/api/admin/certs/orders/{order_id}", json={"status": "обрабатывается"})

    # Назад в "не обработана" -> 409
    res = op_client.patch(
        f"/api/admin/certs/orders/{order_id}",
        json={"status": "не обработана"},
    )
    assert res.status_code == 409
    assert res.json()["detail"] == "Переход «обрабатывается» → «не обработана» невозможен"


def test_admin_patch_skip_409(app, db_engine):
    """Попытка перескочить статус (скачок) -> 409."""
    make_operator(db_engine, "smirnov@misis.ru")
    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    create_res = student_client.post("/api/certs/orders", json={"cert_type": "study"})
    order_id = create_res.json()["order"]["id"]

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    res = op_client.patch(f"/api/admin/certs/orders/{order_id}", json={"status": "забрана"})
    assert res.status_code == 409
    assert res.json()["detail"] == "Переход «не обработана» → «забрана» невозможен"


def test_admin_patch_same_status_409(app, db_engine):
    """Попытка установить тот же самый статус -> 409."""
    make_operator(db_engine, "smirnov@misis.ru")
    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    create_res = student_client.post("/api/certs/orders", json={"cert_type": "study"})
    order_id = create_res.json()["order"]["id"]

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    res = op_client.patch(f"/api/admin/certs/orders/{order_id}", json={"status": "не обработана"})
    assert res.status_code == 409
    assert res.json()["detail"] == "Переход «не обработана» → «не обработана» невозможен"


def test_admin_patch_not_found_404(app, db_engine):
    """Несуществующий order_id -> 404."""
    make_operator(db_engine, "smirnov@misis.ru")
    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    res = op_client.patch("/api/admin/certs/orders/9999", json={"status": "обрабатывается"})
    assert res.status_code == 404
    assert res.json()["detail"] == "Заказ не найден"


def test_admin_list_orders(app, db_engine):
    """Список заказов в админке содержит данные пользователя (full_name, email, group_name)."""
    make_operator(db_engine, "smirnov@misis.ru")
    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    student_client.post("/api/certs/orders", json={"cert_type": "payments"})

    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})

    res = op_client.get("/api/admin/certs/orders")
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data) >= 1
    first = data[0]
    assert first["cert_type"] == "payments"
    assert first["title"] == "Справка о выплатах"
    assert first["user"]["email"] == "ivanov@misis.ru"
    assert first["user"]["full_name"]


def test_admin_requires_operator(app, db_engine):
    """Гость получает 401, студент получает 403 на обоих эндпоинтах админки."""
    guest_client = TestClient(app)
    assert guest_client.get("/api/admin/certs/orders").status_code == 401
    assert guest_client.patch("/api/admin/certs/orders/1", json={"status": "обрабатывается"}).status_code == 401

    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    assert student_client.get("/api/admin/certs/orders").status_code == 403
    assert student_client.patch("/api/admin/certs/orders/1", json={"status": "обрабатывается"}).status_code == 403


# ---------------------------------------------------------------------------
# Unit tests certs.py
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("справка о выплатах", "payments"),
        ("стипендия", "payments"),
        ("справка-вызов", "callup"),
        ("в военкомат", "military"),
        ("медотвод", "medical"),
        ("справка с места учёбы", "study"),
        ("какой-то текст", None),
    ],
)
def test_resolve_cert_type_keywords(text, expected):
    """Тестирование ключевых слов для извлечения типа справки."""
    assert certs.resolve_cert_type(text) == expected


def test_cert_status_chain():
    """Тестирование переходов статусов can_transition."""
    assert certs.can_transition("не обработана", "обрабатывается") is True
    assert certs.can_transition("обрабатывается", "готова и ждёт выдачи") is True
    assert certs.can_transition("готова и ждёт выдачи", "забрана") is True

    # Запрещённые переходы
    assert certs.can_transition("забрана", "не обработана") is False
    assert certs.can_transition("забрана", "обрабатывается") is False
    assert certs.can_transition("не обработана", "забрана") is False
    assert certs.can_transition("не обработана", "не обработана") is False
    assert certs.can_transition("unknown", "не обработана") is False


# ---------------------------------------------------------------------------
# Агент (T035): сквозные тесты через POST /api/requests
# ---------------------------------------------------------------------------

def test_agent_cert_order_authorized_creates_order(client, monkeypatch, db_session, db_engine):
    """Авторизованный пользователь запрашивает справку текстом -> заказ оформляется агентом."""
    seed_regulation_doc(db_engine)
    client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})

    make_fake_llm(
        monkeypatch,
        classify={
            "route": "cert_order",
            "service": "certs",
            "category": "cert_order",
            "priority": "low",
            "confidence": 0.95,
            "reason": "Запрос справки с места учёбы",
        },
        split={"subtasks": [{"summary": "Нужна справка с места учёбы"}]},
    )

    res = client.post(
        "/api/requests",
        json={"text": "Нужна справка с места учёбы", "channel": "web"},
    )
    assert res.status_code == 200, res.text
    data = res.json()

    # Реакция cert_ordered
    assert len(data["reactions"]) == 1
    reaction = data["reactions"][0]
    assert reaction["kind"] == "cert_ordered"
    assert "Справка с места учёбы" in reaction["text"]
    assert "не обработана" in reaction["text"]

    # Тикет и подзадача
    assert data["ticket"]["status"] == "решена"
    assert data["ticket"]["escalated"] is False
    assert data["subtasks"][0]["route"] == "cert_order"
    assert data["subtasks"][0]["status"] == "решена"
    assert "Запрос справки с места учёбы" in data["subtasks"][0]["route_reason"]

    # Заказ создан в БД
    orders = db_session.scalars(select(CertOrder)).all()
    assert len(orders) == 1
    assert orders[0].cert_type == "study"
    assert orders[0].status == "не обработана"

    # События журнала tool_call и tool_result
    events = db_session.scalars(
        select(Event).where(Event.actor == "tool:order_certificate")
    ).all()
    actions = {e.action for e in events}
    assert "tool_call" in actions
    assert "tool_result" in actions


def test_agent_cert_order_guest_auth_required(client, monkeypatch, db_session):
    """Гость запрашивает справку текстом -> реакция auth_required, заказ НЕ создаётся."""
    make_fake_llm(
        monkeypatch,
        classify={
            "route": "cert_order",
            "service": "certs",
            "category": "cert_order",
            "priority": "low",
            "confidence": 0.95,
            "reason": "Запрос справки",
        },
        split={"subtasks": [{"summary": "Нужна справка с места учёбы"}]},
    )

    res = client.post(
        "/api/requests",
        json={"text": "Нужна справка с места учёбы", "channel": "web"},
    )
    assert res.status_code == 200, res.text
    data = res.json()

    # Реакция auth_required
    assert len(data["reactions"]) == 1
    reaction = data["reactions"][0]
    assert reaction["kind"] == "auth_required"
    assert "корпоративной почте" in reaction["text"]

    # Тикет решён, заказ не создан
    assert data["ticket"]["status"] == "решена"
    assert db_session.scalar(select(CertOrder)) is None


def test_agent_cert_order_ambiguous_type_asks_clarification(client, monkeypatch, db_session):
    """Неопределённый тип справки -> уточняющий вопрос clarification со списком каталога."""
    client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})

    make_fake_llm(
        monkeypatch,
        classify={
            "route": "cert_order",
            "service": "certs",
            "category": "cert_order",
            "priority": "low",
            "confidence": 0.95,
            "reason": "Запрос справки",
        },
        split={"subtasks": [{"summary": "нужна какая-то справка"}]},
    )

    res = client.post(
        "/api/requests",
        json={"text": "нужна какая-то справка", "channel": "web"},
    )
    assert res.status_code == 200, res.text
    data = res.json()

    assert len(data["reactions"]) == 1
    reaction = data["reactions"][0]
    assert reaction["kind"] == "clarification"
    assert "Справка с места учёбы" in reaction["text"]
    assert "Справка о выплатах" in reaction["text"]

    # Тикет ждёт ответа пользователя, заказов нет
    assert data["ticket"]["status"] == "ждёт ответа пользователя"
    assert db_session.scalar(select(CertOrder)) is None


def test_quickstart_scenario_5_order_status_visible(app, db_engine):
    """Сценарий 5 quickstart.md: заказ справки студентом, смена статуса оператором, проверка студентом."""
    make_operator(db_engine, "smirnov@misis.ru")

    student_client = TestClient(app)
    student_client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})

    # 1. Заказ справки
    create_res = student_client.post("/api/certs/orders", json={"cert_type": "study"})
    assert create_res.status_code == 200
    order_id = create_res.json()["order"]["id"]
    assert create_res.json()["order"]["status"] == "не обработана"

    # 2. Оператор берёт в работу
    op_client = TestClient(app)
    op_client.post("/api/auth/login", json={"email": "smirnov@misis.ru"})
    patch_res = op_client.patch(
        f"/api/admin/certs/orders/{order_id}",
        json={"status": "обрабатывается"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["order"]["status"] == "обрабатывается"

    # 3. Студент проверяет свои заказы
    orders = student_client.get("/api/certs/orders").json()
    assert len(orders) == 1
    assert orders[0]["id"] == order_id
    assert orders[0]["status"] == "обрабатывается"
