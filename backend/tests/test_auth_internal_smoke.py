"""Смоук-тест каркаса ядра: логин/гость/me (T008), журнал событий (T009), internal-хуки (T012)."""

from sqlalchemy.orm import sessionmaker

from app import events, models


def test_login_creates_user_and_sets_cookie(client):
    r = client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    assert r.status_code == 200
    user = r.json()["user"]
    assert user["email"] == "ivanov@misis.ru"
    assert user["full_name"] == "Ivanov"
    assert user["role"] == "student"
    assert "session_id" in client.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["guest"] is False
    assert body["user"]["email"] == "ivanov@misis.ru"


def test_login_accepts_edu_domain_and_builds_full_name(client):
    r = client.post("/api/auth/login", json={"email": "petrov.ivan@edu.misis.ru"})
    assert r.status_code == 200
    assert r.json()["user"]["full_name"] == "Petrov Ivan"


def test_login_rejects_non_misis_email(client):
    for email in ("user@gmail.com", "ivanov@misis.ru.evil.com", "@misis.ru"):
        r = client.post("/api/auth/login", json={"email": email})
        assert r.status_code == 400
        assert "МИСИС" in r.json()["detail"]


def test_guest_me_creates_guest_session(client, db_engine):
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json() == {"user": None, "guest": True}
    # Гостевая сессия создана и записана в БД
    token = client.cookies.get("session_id")
    assert token
    db = sessionmaker(bind=db_engine)()
    sess = db.get(models.Session, token)
    assert sess is not None
    assert sess.user_id is None


def test_guest_session_bound_to_user_on_login(client, db_engine):
    # Гость получает сессию, затем логинится — токен тот же, привязка к user_id
    guest_token = client.get("/api/auth/me").cookies["session_id"]
    r = client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    assert r.status_code == 200
    assert client.cookies["session_id"] == guest_token

    db = sessionmaker(bind=db_engine)()
    sess = db.get(models.Session, guest_token)
    assert sess is not None
    assert sess.user_id is not None
    # Одна сессия — новая при логине не плодится
    assert db.query(models.Session).count() == 1


def test_logout_deauths_session_and_keeps_history(client, db_engine):
    """Выход — деавторизация (user_id → null), не удаление: сессия живёт,
    обращения на неё сохраняются (FR-016; requests.session_id NOT NULL)."""
    client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})
    token = client.cookies["session_id"]
    db = sessionmaker(bind=db_engine, expire_on_commit=False)()
    db.add(models.Request(session_id=token, channel="web", raw_text="текст", masked_text="текст"))
    db.commit()

    r = client.post("/api/auth/logout")
    assert r.status_code == 204
    me = client.get("/api/auth/me")
    assert me.json()["guest"] is True

    sess = db.get(models.Session, token)
    assert sess is not None and sess.user_id is None  # сессия не удалена
    assert db.query(models.Request).count() == 1  # история обращений сохранена


def test_internal_requires_bot_token(client):
    r = client.get("/api/internal/outbound")
    assert r.status_code == 401
    r = client.get("/api/internal/outbound", headers={"X-Bot-Token": "wrong"})
    assert r.status_code == 401
    r = client.post("/api/internal/tg/link", json={"chat_id": 1, "email": "a@misis.ru"})
    assert r.status_code == 401


def test_internal_outbound_polling_and_ack(client, bot_token, db_engine):
    headers = {"X-Bot-Token": bot_token}
    r = client.get("/api/internal/outbound", headers=headers)
    assert r.status_code == 200
    assert r.json() == []

    db = sessionmaker(bind=db_engine, expire_on_commit=False)()
    db.add(models.OutboundMessage(chat_id=123456, text="тест", status="pending"))
    db.commit()

    r = client.get("/api/internal/outbound", headers=headers)
    assert [m["id"] for m in r.json()] == [1]
    assert r.json()[0]["text"] == "тест"

    r = client.post("/api/internal/outbound/1/ack", json={"status": "sent"}, headers=headers)
    assert r.status_code == 200
    assert r.json() == {"id": 1, "status": "sent"}
    assert client.get("/api/internal/outbound", headers=headers).json() == []

    r = client.post("/api/internal/outbound/999/ack", json={"status": "sent"}, headers=headers)
    assert r.status_code == 404


def test_tg_link_flow_confirm_and_attempts_limit(client, bot_token):
    headers = {"X-Bot-Token": bot_token}
    client.post("/api/auth/login", json={"email": "ivanov@misis.ru"})

    # Неизвестная почта → пользователь создаётся (сценарий S1: привязка без веб-входа)
    r = client.post(
        "/api/internal/tg/link", json={"chat_id": 654321, "email": "nobody@misis.ru"}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["confirm_code"]

    # Запрос кода → состояние awaiting_confirm
    r = client.post(
        "/api/internal/tg/link", json={"chat_id": 123456, "email": "ivanov@misis.ru"}, headers=headers
    )
    assert r.status_code == 200
    code = r.json()["confirm_code"]
    assert len(code) == 6 and code.isdigit()

    state = client.get("/api/internal/tg/link", params={"chat_id": 123456}, headers=headers)
    assert state.json()["state"] == "awaiting_confirm"
    assert state.json()["user_id"] is not None

    # Подтверждение без запроса кода у другого чата → 404
    r = client.post("/api/internal/tg/link/confirm", json={"chat_id": 999, "code": code}, headers=headers)
    assert r.status_code == 404

    # 3 неверные попытки → отказ, состояние сброшено
    for i in range(3):
        r = client.post(
            "/api/internal/tg/link/confirm", json={"chat_id": 123456, "code": "000000"}, headers=headers
        )
    assert r.status_code == 400
    assert "попыток" in r.json()["detail"]
    state = client.get("/api/internal/tg/link", params={"chat_id": 123456}, headers=headers)
    assert state.json()["state"] == "awaiting_email"

    # Повторный запрос кода, успешное подтверждение
    r = client.post(
        "/api/internal/tg/link", json={"chat_id": 123456, "email": "ivanov@misis.ru"}, headers=headers
    )
    code = r.json()["confirm_code"]
    r = client.post("/api/internal/tg/link/confirm", json={"chat_id": 123456, "code": code}, headers=headers)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["user"]["email"] == "ivanov@misis.ru"

    state = client.get("/api/internal/tg/link", params={"chat_id": 123456}, headers=headers)
    assert state.json()["state"] == "idle"


def test_log_event_writes_json_payload(db_engine):
    db = sessionmaker(bind=db_engine, expire_on_commit=False)()
    db.add(models.Session(id="tok-test", user_id=None))
    db.commit()
    db.add(models.Request(session_id="tok-test", channel="web", raw_text="x", masked_text="x"))
    db.commit()
    db.add(models.Ticket(request_id=1, status="новая"))
    db.commit()

    event = events.log_event(db, ticket_id=1, actor="agent", action="classified", payload={"confidence": 0.91})
    assert event.id is not None

    loaded = db.get(models.Event, event.id)
    assert loaded.ticket_id == 1
    assert loaded.actor == "agent"
    assert loaded.action == "classified"
    assert loaded.payload == '{"confidence": 0.91}'

    # payload=None → NULL в БД
    event = events.log_event(db, ticket_id=None, actor="system", action="heartbeat")
    assert db.get(models.Event, event.id).payload is None
