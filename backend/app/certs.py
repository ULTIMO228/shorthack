"""Каталог справок, цепочка статусов и оформление заказа (FR-040..043)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.events import log_event
from app.models import CertOrder, KbArticle

# Каталог FR-041 — типы, названия и описания дословно из contracts/api.md.
# Ключи = допустимые значения cert_type (data-model.md: cert_orders.cert_type).
CERT_CATALOG: dict[str, dict[str, str]] = {
    "payments": {
        "title": "Справка о выплатах",
        "description": (
            "Начисленная стипендия и другие выплаты. Документ с подписью "
            "и печатью на бланке вуза."
        ),
    },
    "callup": {
        "title": "Справка-вызов",
        "description": (
            "Учебный отпуск на период аттестации и защиты ВКР "
            "(предоставляется работодателю)."
        ),
    },
    "medical": {
        "title": "Справка для переболевших / медотвода / вакцинации",
        "description": (
            "Справка для переболевших, для получения медотвода "
            "или по факту вакцинации."
        ),
    },
    "study": {
        "title": "Справка с места учёбы",
        "description": "Подтверждает факт обучения; электронный PDF с ЭЦП в ЛК.",
    },
    "military": {
        "title": "Справка в военкомат",
        "description": "Отсрочка от призыва на период обучения (по приложению №4).",
    },
}

# Цепочка FR-042 — индексы задают допустимые переходы (только +1, только вперёд).
CERT_STATUSES: tuple[str, ...] = (
    "не обработана",
    "обрабатывается",
    "готова и ждёт выдачи",
    "забрана",
)

# Детерминированное распознавание типа справки из текста (для ноды cert_order).
# Порядок ключей важен: сначала более специфичные формулировки.
CERT_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "study": ("место учёбы", "места учёбы", "место учебы", "места учебы", "обучени"),
    "payments": ("выплат", "стипенди"),
    "callup": ("вызов", "учебный отпуск", "аттестац", "защит"),
    "medical": ("переболев", "медотвод", "вакцин", "медицин"),
    "military": ("военкомат", "призыв", "отсрочк"),
}


def catalog_items() -> list[dict[str, str]]:
    """Каталог для GET /api/certs/catalog: [{'type','title','description'}, ...]
    в порядке CERT_CATALOG (5 позиций, FR-041)."""
    return [
        {"type": cert_type, "title": info["title"], "description": info["description"]}
        for cert_type, info in CERT_CATALOG.items()
    ]


def title_of(cert_type: str) -> str:
    """Название справки для ответов; неизвестный тип → KeyError."""
    return CERT_CATALOG[cert_type]["title"]


def is_known_type(cert_type: str) -> bool:
    """cert_type входит в каталог FR-041."""
    return cert_type in CERT_CATALOG


def can_transition(current: str, new: str) -> bool:
    """FR-042: переход возможен только к следующему статусу по цепочке
    (index(new) == index(current) + 1). Тот же статус — False; скачки и назад — False."""
    if current not in CERT_STATUSES or new not in CERT_STATUSES:
        return False
    return CERT_STATUSES.index(new) == CERT_STATUSES.index(current) + 1


def set_status(
    db: OrmSession,
    order: CertOrder,
    new_status: str,
    *,
    actor: str = "operator",
) -> None:
    """Проверка цепочки + перевод статуса + журнал cert_status_change (FR-061).
    Нарушение → ValueError('Переход «{current}» → «{new}» невозможен') —
    роутер превращает в 409 (паттерн tickets.set_status, tickets.py:51-67)."""
    if not can_transition(order.status, new_status):
        raise ValueError(f"Переход «{order.status}» → «{new_status}» невозможен")
    old_status = order.status
    order.status = new_status
    order.updated_at = datetime.now(timezone.utc)
    db.commit()
    log_event(
        db,
        actor=actor,
        action="cert_status_change",
        payload={"order_id": order.id, "from": old_status, "to": new_status},
    )


def create_order(db: OrmSession, *, user_id: int, cert_type: str) -> CertOrder:
    """Создание заказа (FR-040): только авторизованный (user_id не None — иначе ValueError),
    только известный тип (иначе ValueError). Статус «не обработана». Коммит."""
    if user_id is None:
        raise ValueError("Заказ справки требует авторизации (user_id обязателен)")
    if not is_known_type(cert_type):
        raise ValueError(f"Неизвестный тип справки: {cert_type}")
    order = CertOrder(
        user_id=user_id,
        cert_type=cert_type,
        status=CERT_STATUSES[0],  # "не обработана"
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def resolve_cert_type(text: str | None) -> str | None:
    """Тип справки из текста обращения/подзадачи по CERT_TYPE_KEYWORDS (нижний регистр).
    Совпадений нет или несколько разных типов → None (нода задаст уточняющий вопрос)."""
    if not text:
        return None
    low = text.lower()
    matches = [
        ctype
        for ctype, kw_list in CERT_TYPE_KEYWORDS.items()
        if any(kw in low for kw in kw_list)
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def get_regulation(db: OrmSession) -> KbArticle | None:
    """Документ «Регламент заказа справок» из kb_articles (topic='certs', kind='document').
    Прямой select — без kb.py (фаза 5). Нет документа (тесты без сида) → None."""
    stmt = select(KbArticle).where(
        KbArticle.topic == "certs",
        KbArticle.kind == "document",
    )
    return db.scalars(stmt).first()


def order_certificate(
    db: OrmSession,
    *,
    user_id: int,
    cert_type: str,
    subtask_id: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Инструмент заказа (FR-040, type exec): обёртка create_order для реестра tools.
    Возвращает {'ok': True, 'order_id', 'cert_type', 'title', 'status'}.
    ValueError от create_order пролетает наружу — диспетчер оборачивает (ToolError/ok=False)."""
    order = create_order(db, user_id=user_id, cert_type=cert_type)
    return {
        "ok": True,
        "order_id": order.id,
        "cert_type": order.cert_type,
        "title": title_of(order.cert_type),
        "status": order.status,
    }
