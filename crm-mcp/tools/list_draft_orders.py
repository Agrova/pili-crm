"""Tool: list draft orders (status='draft') with optional customer_id filter."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "list_draft_orders"
DESCRIPTION = (
    "Возвращает заказы со статусом draft с позициями. "
    "Опциональный фильтр по customer_id. "
    "Используется после apply_pending_analysis для просмотра созданных черновиков."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_id": {
            "type": "integer",
            "minimum": 1,
            "description": "id клиента для фильтра (опц.); без фильтра — все draft-заказы",
        }
    },
}

_SQL = text(
    """
    SELECT
        o.id            AS order_id,
        o.total_price,
        o.currency,
        o.created_at,
        c.id            AS customer_id,
        c.name          AS customer_name,
        c.telegram_id,
        oi.id           AS item_id,
        oi.quantity,
        oi.unit_price,
        p.name          AS product_name
    FROM orders_order o
    JOIN orders_customer c         ON c.id = o.customer_id
    LEFT JOIN orders_order_item oi ON oi.order_id = o.id
    LEFT JOIN catalog_product p    ON p.id = oi.product_id
    WHERE o.status = 'draft'
      AND (CAST(:customer_id AS BIGINT) IS NULL OR o.customer_id = :customer_id)
    ORDER BY o.id ASC, oi.id ASC
    """
)


async def run(
    session: AsyncSession, customer_id: int | None = None
) -> dict[str, Any]:
    cid = int(customer_id) if customer_id is not None else None
    rows = (
        await session.execute(_SQL, {"customer_id": cid})
    ).mappings().all()

    orders: dict[int, dict[str, Any]] = {}
    for r in rows:
        oid = r["order_id"]
        bucket = orders.setdefault(
            oid,
            {
                "order_id": oid,
                "display_id": f"З-{oid:03d}",
                "customer": {
                    "id": r["customer_id"],
                    "name": r["customer_name"],
                    "telegram_id": r["telegram_id"],
                },
                "created_at": (
                    r["created_at"].isoformat()
                    if hasattr(r["created_at"], "isoformat")
                    else str(r["created_at"])
                ),
                "total_price": _num(r["total_price"]),
                "currency": r["currency"] or "RUB",
                "items": [],
            },
        )
        if r["item_id"] is not None:
            bucket["items"].append(
                {
                    "product_name": r["product_name"],
                    "quantity": _num(r["quantity"]),
                    "unit_price": _num(r["unit_price"]),
                }
            )

    return {"orders": list(orders.values()), "customer_id_filter": cid}


def _num(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)


def format_text(result: dict[str, Any]) -> str:
    orders = result.get("orders", [])
    cid_filter = result.get("customer_id_filter")

    if not orders:
        if cid_filter is not None:
            return f"Черновых заказов для клиента id={cid_filter} нет."
        return "Черновых заказов нет."

    lines = [f"Черновиков: {len(orders)}"]
    for o in orders:
        c = o["customer"]
        date_str = o["created_at"][:10]
        lines.append(f"\n{o['display_id']} | {c['name']} | {date_str}")
        if o["items"]:
            for it in o["items"]:
                price = it.get("unit_price")
                price_s = f"{price:.0f} ₽" if price is not None else "—"
                qty = it.get("quantity")
                qty_s = f"{qty:.0f}" if qty is not None else "?"
                lines.append(f"  • {it['product_name']} ×{qty_s} по {price_s}")
        else:
            lines.append("  (нет позиций)")

    return "\n".join(lines)
