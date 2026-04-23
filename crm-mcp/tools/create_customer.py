"""Tool: create a new customer."""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "create_customer"
DESCRIPTION = (
    "Создаёт нового клиента. Обязательно: имя. "
    "Рекомендуется: telegram_id (@handle). "
    "Используй только после подтверждения оператором что клиент новый."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Полное имя клиента.",
        },
        "telegram_id": {
            "type": "string",
            "description": "@handle или display name в Telegram.",
        },
        "phone": {
            "type": "string",
            "description": "Телефон в любом формате.",
        },
        "email": {
            "type": "string",
            "description": "Email-адрес.",
        },
    },
    "required": ["name"],
}

_INSERT_SQL = text(
    """
    INSERT INTO orders_customer (name, telegram_id, phone, email)
    VALUES (:name, :telegram_id, :phone, :email)
    RETURNING id, name, telegram_id, phone, email
    """
)


def _tg_link(telegram_id: str | None) -> str | None:
    if telegram_id and telegram_id.startswith("@"):
        return f"https://t.me/{telegram_id[1:]}"
    return None


async def run(
    session: AsyncSession,
    name: str,
    telegram_id: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"status": "error", "error": "Имя клиента не может быть пустым."}

    # Normalise optional fields
    telegram_id = (telegram_id or "").strip() or None
    phone = (phone or "").strip() or None
    email = (email or "").strip() or None

    # DB constraint: at least one contact required
    if not any([telegram_id, phone, email]):
        telegram_id = f"@auto_{int(time.time())}"

    try:
        row = (
            await session.execute(
                _INSERT_SQL,
                {
                    "name": name,
                    "telegram_id": telegram_id,
                    "phone": phone,
                    "email": email,
                },
            )
        ).mappings().one()
        await session.commit()
    except Exception as exc:
        await session.rollback()
        return {"status": "error", "error": str(exc)}

    tg = row["telegram_id"]
    return {
        "status": "ok",
        "id": row["id"],
        "name": row["name"],
        "telegram_id": tg,
        "telegram_link": _tg_link(tg),
        "phone": row["phone"],
        "email": row["email"],
    }


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") == "ok":
        tg = result.get("telegram_link") or result.get("telegram_id") or "—"
        return (
            f"✅ Клиент создан: {result['name']} (id={result['id']}) — {tg}"
        )
    return f"Ошибка: {result.get('error', 'неизвестная ошибка')}"
