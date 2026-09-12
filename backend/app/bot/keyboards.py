"""Inline-клавиатуры для Telegram-бота (K1, K2) по контракту contracts/bot-contract.md §3."""

from typing import Any


def make_cert_catalog_keyboard(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """K1: Клавиатура каталога справок + кнопка 'Мои заказы'."""
    inline_keyboard = []
    for item in catalog:
        cert_type = item.get("type", "")
        title = item.get("title", cert_type)
        inline_keyboard.append([{"text": title, "callback_data": f"cert:{cert_type}"}])

    inline_keyboard.append([{"text": "Мои заказы", "callback_data": "cert:my"}])
    return {"inline_keyboard": inline_keyboard}


def make_cert_order_confirm_keyboard(cert_type: str) -> dict[str, Any]:
    """K2: Подтверждение заказа справки: [✅ Заказать] [Отмена]."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Заказать", "callback_data": f"cert_order:{cert_type}"},
                {"text": "Отмена", "callback_data": "noop"},
            ]
        ]
    }
