"""Регрессионные тесты подтверждённых багов фаз 1–3 (ревью) и добивка покрытия.

Покрывает: дедупликацию составного обращения на уровне подзадачи (500 → 200),
эскалацию после 2 раундов уточнений (FR-025), 409 на reply по решённой заявке,
границы слова в триггере «юридические темы», привязку гостевых обращений при
login (SC-005), деавторизацию при logout, строгую валидацию email, привязку
Telegram без веб-входа (S1 из 003/quickstart) и ветки ленивого graphiti-клиента.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy.orm import sessionmaker

from app import agent, auth, graphiti_client, llm, triggers
from app.models import KbArticle, Request, Service, Session as UserSession, User
from test_agent import make_fake_llm
from test_tools import FakeHttpResponse, fake_http  # noqa: F401  (фикстура fake_http)


def _fresh(db_engine):
    return sessionmaker(bind=db_engine, expire_on_commit=False)()


# ---------------------------------------------------------------------------
# B1: составное обращение, первая подзадача — дубль → 200, остальные разобраны
# ---------------------------------------------------------------------------

def test_composite_request_first_subtask_duplicate_no_500(client, monkeypatch, fake_http, db_engine):
    """Дубль первой подзадачи привязывается к открытой заявке, вторая классифицируется."""
    calls = {"split": 0, "classify": 0}

    def fake_chat(messages, *, model=None, temperature=0.2, timeout=25.0):
        text = messages if isinstance(messages, str) else "\n".join(m["content"] for m in messages)
        if '"subtasks"' in text:
            calls["split"] += 1
            if calls["split"] == 1:
                return json.dumps({"subtasks": [{"summary": "Не работает Wi-Fi MISIS-Guest"}]})
            return json.dumps(
                {"subtasks": [
                    {"summary": "Не работает Wi-Fi MISIS-Guest"},
                    {"summary": "Не открывается сайт misis.ru"},
                ]}
            )
        calls["classify"] += 1
        # строка «Проблема: <summary>» — без общего шаблона промпта (там сам есть «Wi-Fi»)
        problem = text.split("Проблема:", 1)[1].split("\n", 1)[0]
        if calls["classify"] == 1:
            # первая заявка — эскалация: тикет открытый («в работе»)
            payload = {"route": "escalate", "service": "wifi_guest", "category": "availability",
                       "priority": "high", "confidence": 0.9, "reason": "Жалоба на доступность"}
        elif "Wi-Fi" in problem:
            payload = {"route": "auto_check", "service": "wifi_guest", "category": "availability",
                       "priority": "high", "confidence": 0.9, "reason": "Жалоба на доступность сети"}
        else:
            payload = {"route": "auto_check", "service": "site", "category": "availability",
                       "priority": "high", "confidence": 0.9, "reason": "Жалоба на недоступность сайта"}
        return json.dumps(payload, ensure_ascii=False)

    monkeypatch.setattr("app.llm.chat", fake_chat)
    fake_http["https://misis.ru"] = FakeHttpResponse(200)

    first = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    assert first.status_code == 200, first.text
    open_ticket_id = first.json()["ticket"]["id"]
    assert first.json()["ticket"]["status"] == "в работе"
    assert first.json()["ticket"]["escalated"] is True

    second = client.post(
        "/api/requests",
        json={"text": "Не работает вайфай и ещё сайт не открывается"},
    )
    assert second.status_code == 200, second.text  # раньше: 500 (SubtaskOut без route)
    data = second.json()
    assert data["duplicate"] is False  # дублем оказалась не вся заявка
    assert len(data["subtasks"]) == 2
    assert data["subtasks"][0]["status"] == "решена"  # дубль помечен решённым
    assert data["subtasks"][1]["route"] == "auto_check"  # вторая классифицирована
    assert data["reactions"][0]["kind"] == "answer"  # реакция со ссылкой на открытую заявку
    # первая подзадача привязана к открытой заявке
    db = _fresh(db_engine)
    request2 = db.get(Request, data["request_id"])
    assert request2.duplicate_of_ticket_id == open_ticket_id
    db.close()


def test_composite_request_all_subtasks_duplicate(client, monkeypatch):
    """Все подзадачи — дубли → duplicate=True, ответ ссылается на открытый тикет."""
    make_fake_llm(
        monkeypatch,
        split={"subtasks": [{"summary": "Не работает Wi-Fi"}, {"summary": "Опять Wi-Fi не работает"}]},
    )
    first = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    assert first.status_code == 200
    second = client.post("/api/requests", json={"text": "Опять всё пропало"})
    assert second.status_code == 200, second.text
    data = second.json()
    assert data["duplicate"] is True
    assert data["ticket"]["id"] == first.json()["ticket"]["id"]
    assert data["reactions"][0]["kind"] == "answer"


# ---------------------------------------------------------------------------
# B2/B3: диалог — эскалация после 2 раундов; reply по решённой заявке → 409
# ---------------------------------------------------------------------------

def test_reply_on_resolved_ticket_is_409(client, monkeypatch):
    """«решена» достижима при dialog_rounds < 2 → reply до set_status давал 500."""
    make_fake_llm(
        monkeypatch,
        classify={"route": "cert_order", "service": "certs", "category": "cert_order",
                  "priority": "low", "confidence": 0.95, "reason": "Запрос справки"},
    )
    created = client.post("/api/requests", json={"text": "Нужна справка с места учёбы"})
    assert created.json()["ticket"]["status"] == "решена"
    resp = client.post(
        f"/api/requests/{created.json()['request_id']}/reply", json={"text": "А когда?"}
    )
    assert resp.status_code == 409
    assert "завершён" in resp.json()["detail"]


def test_reply_escalation_keeps_dialog_history(client, monkeypatch, db_engine):
    """История переписки собирается в events по тикету и уходит с эскалацией (FR-025)."""
    make_fake_llm(monkeypatch)
    created = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    request_id = created.json()["request_id"]
    client.post(f"/api/requests/{request_id}/reply", json={"text": "На всех устройствах"})
    r2 = client.post(f"/api/requests/{request_id}/reply", json={"text": "Ошибка подключения"})
    assert r2.status_code == 200
    ticket_id = r2.json()["ticket"]["id"]
    db = _fresh(db_engine)
    dialog = [e for e in db.query(__import__("app.models", fromlist=["Event"]).Event)
              .filter_by(ticket_id=ticket_id, action="dialog").all()]
    assert len(dialog) == 2  # оба ответа пользователя в журнале
    assert db.query(__import__("app.models", fromlist=["Event"]).Event) \
        .filter_by(ticket_id=ticket_id, action="escalated").count() == 1
    db.close()


# ---------------------------------------------------------------------------
# B4: триггер «юридические темы» — границы слова
# ---------------------------------------------------------------------------

def test_legal_trigger_word_boundaries():
    decision = triggers.apply("У меня судороги", route="kb", priority="medium", confidence=0.9)
    assert decision.route == "kb" and decision.triggered == []
    decision = triggers.apply("Какая судьба", route="kb", priority="medium", confidence=0.9)
    assert decision.route == "kb" and decision.triggered == []
    decision = triggers.apply("поиск по базе", route="kb", priority="medium", confidence=0.9)
    assert decision.route == "kb" and decision.triggered == []
    decision = triggers.apply("Хочу подать в суд", route="kb", priority="medium", confidence=0.9)
    assert decision.route == "escalate"
    assert "юридические темы" in decision.triggered
    decision = triggers.apply("намерен подать иск", route="kb", priority="medium", confidence=0.9)
    assert decision.route == "escalate"
    # основы слов по-прежнему срабатывают в любых словоформах
    decision = triggers.apply("претензия к работе сети", route="kb", priority="medium", confidence=0.9)
    assert decision.route == "escalate"


# ---------------------------------------------------------------------------
# B5/B6/B7: auth — привязка гостевых обращений, logout, валидация email
# ---------------------------------------------------------------------------

def test_guest_requests_bound_to_profile_on_login(client, monkeypatch):
    """SC-005: после логина гостевые обращения сессии видны в «моих обращениях»."""
    make_fake_llm(monkeypatch)
    created = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    assert created.status_code == 200
    login = client.post("/api/auth/login", json={"email": "ivanov@misis.ru", "password": "student123"})
    assert login.status_code == 200
    mine = client.get("/api/requests")
    assert mine.status_code == 200
    assert [item["id"] for item in mine.json()] == [created.json()["request_id"]]


@pytest.mark.parametrize("email", [
    "a@@misis.ru",
    "a @misis.ru",
    "@misis.ru",
    "ivanov@misis.ru.evil.com",
    "ivanov@sub.edu.misis.ru",  # поддомен edu.misis.ru не принимаем
    "a@misis.ru,b@misis.ru",
])
def test_is_misis_email_rejects_invalid(email):
    assert auth.is_misis_email(email) is False


@pytest.mark.parametrize("email", ["ivanov@misis.ru", "student@edu.misis.ru", "petrov.ivan@edu.misis.ru"])
def test_is_misis_email_accepts_valid(email):
    assert auth.is_misis_email(email) is True


def test_login_rejects_double_at(client):
    assert client.post("/api/auth/login", json={"email": "a@@misis.ru", "password": "x"}).status_code == 400


# ---------------------------------------------------------------------------
# B8: привязка Telegram без предварительного веб-входа (S1, 003/quickstart)
# ---------------------------------------------------------------------------

def test_tg_link_creates_unknown_user_end_to_end(client, bot_token, db_engine):
    """Чистая БД: tg/link по новой почте создаёт пользователя, код подтверждается."""
    headers = {"X-Bot-Token": bot_token}
    r = client.post("/api/internal/tg/link", json={"chat_id": 42, "email": "petrov@misis.ru"},
                    headers=headers)
    assert r.status_code == 200, r.text
    code = r.json()["confirm_code"]
    assert len(code) == 6 and code.isdigit()

    db = _fresh(db_engine)
    user = db.query(User).filter_by(email="petrov@misis.ru").one()
    assert user.full_name == "Petrov" and user.role == "student"
    db.close()

    r = client.post("/api/internal/tg/link/confirm", json={"chat_id": 42, "code": code},
                    headers=headers)
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "petrov@misis.ru"
    state = client.get("/api/internal/tg/link", params={"chat_id": 42}, headers=headers)
    assert state.json()["state"] == "idle"


# ---------------------------------------------------------------------------
# C: ленивый graphiti-клиент с замоканным Graphiti (Neo4j не поднимается)
# ---------------------------------------------------------------------------

class FakeGraphiti:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.episodes: list[dict] = []
        self.closed = False

    async def build_indices_and_constraints(self):
        return None

    async def add_episode(self, **kwargs):
        self.episodes.append(kwargs)

    async def close(self):
        self.closed = True


@pytest.fixture()
def fake_graphiti(monkeypatch):
    monkeypatch.setattr(graphiti_client, "_client", None)
    monkeypatch.setattr(graphiti_client, "Graphiti", FakeGraphiti)
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "secret")
    return FakeGraphiti


def test_get_graphiti_creates_client_once(fake_graphiti):
    client1 = asyncio.run(graphiti_client.get_graphiti())
    assert isinstance(client1, FakeGraphiti)
    assert client1.kwargs["uri"] == "bolt://localhost:7687"
    assert asyncio.run(graphiti_client.get_graphiti()) is client1  # повторно — тот же


def test_add_episode_via_fake_client(fake_graphiti):
    asyncio.run(graphiti_client.add_episode("клик", "тело эпизода"))
    client = asyncio.run(graphiti_client.get_graphiti())
    assert len(client.episodes) == 1
    assert client.episodes[0]["name"] == "клик"


def test_add_episode_error_propagates(fake_graphiti, monkeypatch):
    async def broken(self, **kwargs):
        raise RuntimeError("graph down")

    monkeypatch.setattr(FakeGraphiti, "add_episode", broken)
    with pytest.raises(RuntimeError):
        asyncio.run(graphiti_client.add_episode("x", "y"))


def test_close_graphiti_with_client(fake_graphiti):
    client = asyncio.run(graphiti_client.get_graphiti())
    asyncio.run(graphiti_client.close_graphiti())
    assert client.closed is True
    assert graphiti_client._client is None


# ---------------------------------------------------------------------------
# C: добивка покрытия agent.py — детект языка, перевод, шаблоны из kb_articles
# ---------------------------------------------------------------------------

def test_detect_lang_no_letters_returns_ru():
    assert agent.detect_lang("12345 !!!") == "ru"
    assert agent.detect_lang("hello world") == "en"
    assert agent.detect_lang("Не работает сеть") == "ru"


def test_translate_to_ru_returns_none_when_llm_down(monkeypatch):
    def broken(*args, **kwargs):
        raise llm.LLMUnavailable("down")

    monkeypatch.setattr("app.llm.chat", broken)
    assert agent.translate_to_ru("wifi does not work") is None


def test_render_template_from_kb_and_fallback(db_engine):
    db = _fresh(db_engine)
    db.add(KbArticle(kind="template", title="Шаблон: эскалация оператору",
                     body="Номер обращения: {{ticket}}.", source="seed", confirmed=True))
    db.commit()
    text = agent.render_template(db, agent.TEMPLATE_ESCALATION, ticket="SUP-2026-0007")
    assert text == "Номер обращения: SUP-2026-0007."
    # запасная копия без переменных и с переменными
    assert "недоступен" in agent.render_template(db, agent.TEMPLATE_OUTAGE, service="сайт")
    assert "SUP-" in agent.render_template(
        db, agent.TEMPLATE_ESCALATION, ticket="SUP-2026-0001"
    )
    db.close()


def test_service_display_default_for_unknown():
    assert agent.SERVICE_DISPLAY["wifi_edu"] == "Wi-Fi MISIS-EDU"


# ---------------------------------------------------------------------------
# C: покрытие tools.py — dispatch (service_id), resolve_check_tool, ошибки id
# ---------------------------------------------------------------------------

from app import tools  # noqa: E402


@pytest.fixture()
def db_session(db_engine):
    return _fresh(db_engine)


def test_resolve_check_tool_unknown_service():
    assert tools.resolve_check_tool(None) is None
    assert tools.resolve_check_tool("") is None
    assert tools.resolve_check_tool("account") is None
    name, params = tools.resolve_check_tool("wifi_edu")
    assert name == "check_wifi" and params == {"network": "wifi_edu"}


def test_dispatch_by_service_id_journals(db_session, fake_http):
    """dispatch: вызов по service_id с журналом tool_call/tool_result (FR-061)."""
    db_session.add(Service(name="misis.ru", check_type="real", state="up"))
    db_session.commit()
    service = db_session.query(Service).filter_by(name="misis.ru").one()
    fake_http["https://misis.ru"] = FakeHttpResponse(200)
    result = tools.dispatch(db_session, None, "check_site", {"service_id": service.id})
    assert result["ok"] is True and result["http_code"] == 200
    actions = [e.action for e in db_session.query(__import__("app.models", fromlist=["Event"]).Event)]
    assert actions == ["tool_call", "tool_result"]


def test_dispatch_unknown_tool_raises_key_error(db_session):
    with pytest.raises(KeyError):
        tools.dispatch(db_session, None, "no_such_tool", {})


def test_call_tool_with_missing_service_id_raises(db_session):
    """service_id несуществующего сервиса — ToolError (некорректные параметры)."""
    with pytest.raises(tools.ToolError):
        tools.call_tool(db_session, "check_wifi", {"service_id": 999})


# ---------------------------------------------------------------------------
# C: покрытие routers/requests.py — 404 reply, дубль в «моих», детали, диалог
# ---------------------------------------------------------------------------

def test_reply_unknown_request_404(client, monkeypatch):
    make_fake_llm(monkeypatch)
    resp = client.post("/api/requests/9999/reply", json={"text": "текст"})
    assert resp.status_code == 404


def test_my_requests_shows_duplicate_linked_ticket(client, monkeypatch):
    """Обращение-дубль без своего тикета видно в истории со ссылкой на открытый."""
    make_fake_llm(monkeypatch)
    first = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    assert first.status_code == 200
    second = client.post("/api/requests", json={"text": "Опять не работает вайфай MISIS-Guest"})
    assert second.json()["duplicate"] is True
    login = client.post("/api/auth/login", json={"email": "ivanov@misis.ru", "password": "student123"})
    assert login.status_code == 200
    mine = client.get("/api/requests")
    ids = [item["id"] for item in mine.json()]
    assert first.json()["request_id"] in ids
    assert second.json()["request_id"] in ids
    linked = [i for i in mine.json() if i["id"] == second.json()["request_id"]][0]
    assert linked["ticket"]["number"] == first.json()["ticket"]["number"]


def test_request_detail_unknown_404(client, monkeypatch):
    make_fake_llm(monkeypatch)
    resp = client.get("/api/requests/9999")
    assert resp.status_code == 404


def test_request_detail_skips_reactions_without_text(client, monkeypatch, db_engine):
    """reaction_sent без текста в payload не попадает в диалог."""
    from app.events import log_event

    make_fake_llm(monkeypatch)
    created = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    request_id = created.json()["request_id"]
    db = _fresh(db_engine)
    ticket_id = created.json()["ticket"]["id"]
    log_event(db, ticket_id=ticket_id, actor="agent", action="reaction_sent",
              payload={"kind": "escalated"})  # без «text»
    db.close()
    detail = client.get(f"/api/requests/{request_id}")
    assert detail.status_code == 200
    assert all("text" in m for m in detail.json()["dialog"])


# ---------------------------------------------------------------------------
# C: покрытие triggers.py — enforce_outage (FR-011, T027)
# ---------------------------------------------------------------------------

def test_enforce_outage_raises_and_never_lowers():
    assert triggers.enforce_outage("high") == "critical"
    assert triggers.enforce_outage("low") == "critical"
    assert triggers.enforce_outage("critical") == "critical"
    assert triggers.OUTAGE_PRIORITY_RULE == "подтверждённый сбой"


def test_reply_empty_text_422(client, monkeypatch):
    make_fake_llm(monkeypatch)
    created = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    resp = client.post(
        f"/api/requests/{created.json()['request_id']}/reply", json={"text": "   "}
    )
    assert resp.status_code == 422


def test_my_requests_skips_request_without_ticket(client, db_engine):
    """Обращение без тикета и без привязки (legacy-данные) не ломает историю."""
    db = _fresh(db_engine)
    db.add(UserSession(id="tok-legacy", user_id=None))
    db.commit()
    user = db.query(User).filter_by(email="ivanov@misis.ru").one()  # засидирован autouse-фикстурой
    db.add(Request(session_id="tok-legacy", user_id=user.id, channel="web",
                   raw_text="текст", masked_text="текст"))
    db.commit()
    db.close()

    client.post("/api/auth/login", json={"email": "ivanov@misis.ru", "password": "student123"})
    mine = client.get("/api/requests")
    assert mine.status_code == 200
    assert mine.json() == []


def test_request_detail_includes_user_dialog_messages(client, monkeypatch):
    """Ответы пользователя (events action=dialog) попадают в диалог деталей."""
    make_fake_llm(monkeypatch)
    created = client.post("/api/requests", json={"text": "Не работает вайфай MISIS-Guest"})
    request_id = created.json()["request_id"]
    client.post(f"/api/requests/{request_id}/reply", json={"text": "На всех устройствах"})
    detail = client.get(f"/api/requests/{request_id}")
    assert detail.status_code == 200
    roles = [m["role"] for m in detail.json()["dialog"]]
    assert "user" in roles
    assert roles.count("user") == 1


def test_check_wifi_service_id_with_null_name_falls_back(db_session):
    """services.name nullable: при service_id без имени результат подписываем сетью."""
    service = Service(name=None, check_type="emulated", state="down")
    db_session.add(service)
    db_session.commit()
    call = tools.call_tool(db_session, "check_wifi", {"service_id": service.id,
                                                      "network": "wifi_edu"})
    assert call.result["ok"] is False
    assert call.result["service"] == "wifi_edu"


# ---------------------------------------------------------------------------
# C: добивка покрытия kb.py / tools.py — битые эмбеддинги, fallback подзапросов
# ---------------------------------------------------------------------------

def test_kb_load_vector_treats_broken_embedding_as_unindexed(db_session, monkeypatch):
    """Битый JSON и не-список в embedding — «не проиндексирована» ( keyword-деградация)."""
    from app import kb

    db_session.add(KbArticle(kind="document", title="битый", topic="other", body="eduroam",
                             source="seed", confirmed=True, embedding="{не json"))
    db_session.add(KbArticle(kind="document", title="не список", topic="other", body="eduroam",
                             source="seed", confirmed=True, embedding='{"a": 1}'))
    db_session.commit()
    monkeypatch.setattr("app.llm.embed", lambda text, **kw: [1.0])
    # ни одного валидного вектора — keyword-режим; статьи всё равно находятся по токенам
    hits = kb.retrieve(db_session, "eduroam")
    assert {h.article.title for h in hits} == {"битый", "не список"}


def test_kb_agent_falls_back_to_reformulated_when_splitter_down(db_session, monkeypatch):
    """LLMUnavailable у разделителя подзапросов — поиск по переформулированному запросу."""
    from app import tools

    def fake_chat(messages, *, model=None, temperature=0.2, timeout=25.0):
        text = messages if isinstance(messages, str) else "\n".join(m["content"] for m in messages)
        if "Переформулируй" in text:
            return "eduroam настройка"
        if '"queries"' in text:
            raise llm.LLMUnavailable("разделитель недоступен (тест)")
        return "ответ"

    monkeypatch.setattr("app.llm.chat", fake_chat)
    call = tools.call_tool(db_session, "kb_agent", {"question": "как настроить eduroam?"})
    assert call.result["queries"] == ["eduroam настройка"]

