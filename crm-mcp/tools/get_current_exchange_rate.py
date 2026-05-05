"""Tool: get current exchange rate for a currency pair (currency → RUB)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "get_current_exchange_rate"
DESCRIPTION = (
    "Возвращает актуальный расчётный курс для пары currency→RUB. "
    "По умолчанию USD. Если записей нет — предлагает добавить через set_exchange_rate."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "currency": {
            "type": "string",
            "description": "Код валюты источника. По умолчанию 'USD'.",
            "default": "USD",
        }
    },
    "required": [],
}

_SELECT_SQL = text(
    """
    SELECT id, from_currency, to_currency, rate, markup_percent, valid_from, source
    FROM pricing_exchange_rate
    WHERE from_currency = :currency
      AND to_currency = 'RUB'
    ORDER BY valid_from DESC
    LIMIT 1
    """
)


async def run(
    session: AsyncSession,
    currency: str = "USD",
) -> dict[str, Any]:
    currency = (currency or "USD").strip().upper()

    row = (
        await session.execute(_SELECT_SQL, {"currency": currency})
    ).mappings().first()

    if row is None:
        return {
            "status": "not_found",
            "currency": currency,
            "message": f"Курс {currency}→RUB не найден. Добавьте через set_exchange_rate.",
        }

    return {
        "status": "ok",
        "id": row["id"],
        "currency": currency,
        "rate": str(row["rate"]),
        "markup_percent": str(row["markup_percent"]) if row["markup_percent"] is not None else None,
        "valid_from": row["valid_from"].isoformat(),
        "source": row["source"],
    }


def format_text(result: dict[str, Any]) -> str:
    if result["status"] == "not_found":
        return f"❌ Курс {result['currency']}→RUB не найден. Добавьте через set_exchange_rate."

    currency = result["currency"]
    rate = result["rate"]
    source = result["source"]
    valid_from = result["valid_from"][:10]
    return f"✅ Курс {currency}→RUB: {rate} (source={source}, с {valid_from})"
