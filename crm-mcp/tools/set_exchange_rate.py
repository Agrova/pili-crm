"""Tool: add a new exchange rate record (currency → RUB). Immutable history."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "set_exchange_rate"
DESCRIPTION = (
    "Добавляет новую запись курса currency→RUB (source=manual, valid_from=now). "
    "Существующие записи не изменяются — история иммутабельна. "
    "Требует подтверждения оператора перед вызовом."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "currency": {
            "type": "string",
            "description": "Код валюты источника. Напр. 'USD'.",
        },
        "rate": {
            "type": "string",
            "description": "Курс к RUB. Строка для точного Decimal. Напр. '82.50'.",
        },
        "note": {
            "type": "string",
            "description": "Необязательный комментарий (игнорируется, поля note нет в таблице).",
        },
    },
    "required": ["currency", "rate"],
}

_INSERT_SQL = text(
    """
    INSERT INTO pricing_exchange_rate (from_currency, to_currency, rate, source, valid_from)
    VALUES (:currency, 'RUB', :rate, 'manual', now())
    RETURNING id, from_currency, to_currency, rate, valid_from, source
    """
)


async def run(
    session: AsyncSession,
    currency: str,
    rate: str,
    note: str | None = None,  # accepted, ignored — no note column in table
) -> dict[str, Any]:
    currency = (currency or "").strip().upper()

    try:
        rate_decimal = Decimal(rate)
    except (InvalidOperation, ValueError):
        return {
            "status": "error",
            "error": f"Некорректное значение rate: {rate!r}. Ожидается число, напр. '82.50'.",
        }

    if rate_decimal <= 0:
        return {
            "status": "error",
            "error": f"rate должен быть > 0, получено: {rate!r}.",
        }

    try:
        row = (
            await session.execute(
                _INSERT_SQL,
                {"currency": currency, "rate": rate_decimal},
            )
        ).mappings().one()
        await session.commit()
    except Exception as exc:
        await session.rollback()
        return {"status": "error", "error": str(exc)}

    return {
        "status": "ok",
        "id": row["id"],
        "currency": currency,
        "rate": str(row["rate"]),
        "valid_from": row["valid_from"].isoformat(),
        "source": row["source"],
    }


def format_text(result: dict[str, Any]) -> str:
    if result["status"] != "ok":
        return f"❌ Ошибка: {result.get('error', 'неизвестная ошибка')}"

    currency = result["currency"]
    rate = result["rate"]
    rid = result["id"]
    valid_from = result["valid_from"][:16].replace("T", " ")
    return f"✅ Курс {currency}→RUB обновлён: {rate} (id={rid}, с {valid_from} UTC)"
