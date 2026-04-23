"""Tool: list customers with active-order count."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "list_customers"
DESCRIPTION = (
    "Возвращает список клиентов с контактами и количеством активных заказов "
    "(все кроме delivered/cancelled). Опциональный фильтр по подстроке имени."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "search": {
            "type": "string",
            "description": "Подстрока имени клиента для фильтра (опц.)",
        }
    },
}

_SQL = text(
    """
    SELECT
        c.id,
        c.name,
        c.telegram_id,
        c.phone,
        c.email,
        COUNT(o.id) FILTER (
            WHERE o.status NOT IN ('delivered', 'cancelled')
        ) AS active_orders
    FROM orders_customer c
    LEFT JOIN orders_order o ON o.customer_id = c.id
    WHERE (CAST(:q AS TEXT) IS NULL OR c.name ILIKE CAST(:q_pat AS TEXT))
    GROUP BY c.id, c.name, c.telegram_id, c.phone, c.email
    ORDER BY c.name ASC
    """
)


async def run(session: AsyncSession, search: str | None = None) -> dict[str, Any]:
    q = (search or "").strip() or None
    rows = (
        await session.execute(
            _SQL, {"q": q, "q_pat": f"%{q}%" if q else None}
        )
    ).mappings().all()
    return {
        "customers": [
            {
                "id": r["id"],
                "name": r["name"],
                "telegram_id": r["telegram_id"],
                "phone": r["phone"],
                "email": r["email"],
                "active_orders": int(r["active_orders"] or 0),
            }
            for r in rows
        ]
    }


def format_text(result: dict[str, Any]) -> str:
    cs = result.get("customers", [])
    if not cs:
        return "Клиенты не найдены."
    lines = [f"Клиентов: {len(cs)}"]
    for c in cs:
        contact = c.get("telegram_id") or c.get("phone") or c.get("email") or "—"
        lines.append(
            f"  • {c['name']} — {contact}, активных заказов: {c['active_orders']}"
        )
    return "\n".join(lines)
