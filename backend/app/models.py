"""ORM-модели по data-model.md (specs/001-misis-support-assistant)."""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """Пользователь (студент / сотрудник / оператор)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    group_name: Mapped[str | None] = mapped_column(String, nullable=True)
    role: Mapped[str] = mapped_column(String, nullable=False)  # student / staff / operator
    password_hash: Mapped[str] = mapped_column(String, nullable=False)  # pbkdf2$iter$salt$hash

    requests: Mapped[list["Request"]] = relationship(back_populates="user")
    cert_orders: Mapped[list["CertOrder"]] = relationship(back_populates="user")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user")


class Session(Base):
    """Сессия по токену cookie; user_id=null — гостевая сессия (FR-016)."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # secrets.token_urlsafe(32)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    user: Mapped["User | None"] = relationship(back_populates="sessions")
    requests: Mapped[list["Request"]] = relationship(back_populates="session")


class Request(Base):
    """Обращение пользователя (сырой и замаскированный текст, язык, перевод)."""

    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)  # web / telegram
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    masked_text: Mapped[str] = mapped_column(Text, nullable=False)
    lang: Mapped[str | None] = mapped_column(String(2), nullable=True)  # ISO-2
    translation: Mapped[str | None] = mapped_column(Text, nullable=True)
    duplicate_of_ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    user: Mapped["User | None"] = relationship(back_populates="requests")
    session: Mapped["Session"] = relationship(back_populates="requests")
    subtasks: Mapped[list["Subtask"]] = relationship(back_populates="request")
    # foreign_keys: между requests и tickets два FK (ещё requests.duplicate_of_ticket_id),
    # без явного указания SQLAlchemy не может выбрать путь соединения
    tickets: Mapped[list["Ticket"]] = relationship(
        back_populates="request", foreign_keys="Ticket.request_id"
    )


class Subtask(Base):
    """Подзадача обращения: результат сплиттера и классификатора."""

    __tablename__ = "subtasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=True)
    service: Mapped[str | None] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String, nullable=True)
    route: Mapped[str] = mapped_column(String, nullable=True)
    route_reason: Mapped[str] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=True)

    request: Mapped["Request"] = relationship(back_populates="subtasks")
    service_checks: Mapped[list["ServiceCheck"]] = relationship(back_populates="subtask")


class Ticket(Base):
    """Заявка — агрегат по обращению; эскалация — признак, не статус (Q2)."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("requests.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    incident_id: Mapped[int | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    dialog_rounds: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    request: Mapped["Request"] = relationship(
        back_populates="tickets", foreign_keys="Ticket.request_id"
    )
    incident: Mapped["Incident | None"] = relationship(back_populates="tickets")


class Service(Base):
    """Сервис для статус-борда (FR-024): real / emulated проверки."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=True)
    check_type: Mapped[str] = mapped_column(String, nullable=True)  # real / emulated
    state: Mapped[str] = mapped_column(String, nullable=True)  # up / down
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class ServiceCheck(Base):
    """Результат проверки доступности сервиса (FR-022/023)."""

    __tablename__ = "service_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), nullable=False)
    subtask_id: Mapped[int | None] = mapped_column(ForeignKey("subtasks.id"), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=True)
    http_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    subtask: Mapped["Subtask | None"] = relationship(back_populates="service_checks")


class KbArticle(Base):
    """Статья базы знаний; embedding (JSON) заполняется после индексации."""

    __tablename__ = "kb_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String, nullable=True)  # document / template
    title: Mapped[str] = mapped_column(String, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=True)
    topic: Mapped[str] = mapped_column(String, nullable=True)
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON, 256 float
    source: Mapped[str] = mapped_column(String, nullable=True)  # seed / operator
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=True)


class CertOrder(Base):
    """Заказ справки (FR-040..043); переходы статусов — только вперёд (FR-042)."""

    __tablename__ = "cert_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    cert_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped["User"] = relationship(back_populates="cert_orders")


class Incident(Base):
    """Инцидент: всплеск однотипных обращений за окно детекции (15 мин)."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String, nullable=True)
    category: Mapped[str] = mapped_column(String, nullable=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=True)  # active / resolved
    notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    broadcast_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="incident")


class Event(Base):
    """Журнал действий (FR-061); payload — JSON-строка."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int | None] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    actor: Mapped[str] = mapped_column(String, nullable=True)  # user/agent/tool:<name>/operator/system
    action: Mapped[str] = mapped_column(String, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class TgLink(Base):
    """Привязка Telegram-чата к пользователю (фича 003)."""

    __tablename__ = "tg_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    state: Mapped[str] = mapped_column(String, nullable=True)
    confirm_code: Mapped[str | None] = mapped_column(String(6), nullable=True)
    confirm_attempts: Mapped[int | None] = mapped_column(Integer, default=0)  # лимит 3 попытки кода
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class OutboundMessage(Base):
    """Исходящее сообщение в бот; бот забирает поллингом (фича 003)."""

    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=True)  # pending / sent / failed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
