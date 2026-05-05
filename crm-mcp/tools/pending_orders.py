"""Tool: list pending orders (confirmed / in_procurement / shipped_by_supplier)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "pending_orders"
DESCRIPTION = (
    "Возвращает заказы в активной стадии (confirmed / in_procurement / "
    "shipped_by_supplier / received_by_forwarder) с товарами и контактами "
    "клиентов. Опциональный фильтр по имени клиента (ILIKE)."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_name": {
            "type": "string",
            "description": "Подстрока имени клиента для фильтра (опц.)",
        }
    },
}

_SQL = text(
    """
    SELECT
        o.id            AS order_id,
        o.status::text  AS status,
        o.total_price,
        c.id            AS customer_id,
        c.name          AS customer_name,
        c.telegram_id,
        c.phone,
        c.email,
        oi.id           AS item_id,
        oi.quantity,
        oi.unit_price,
        p.name          AS product_name,
        s.name          AS supplier
    FROM orders_order o
    JOIN orders_customer c   ON c.id = o.customer_id
    LEFT JOIN orders_order_item oi ON oi.order_id = o.id
    LEFT JOIN catalog_product p    ON p.id = oi.product_id
    LEFT JOIN catalog_product_listing cpl ON cpl.product_id = p.id AND cpl.is_primary = true
    LEFT JOIN catalog_supplier s   ON s.id = cpl.source_id
    WHERE o.status IN (
        'confirmed', 'in_procurement', 'shipped_by_supplier', 'received_by_forwarder'
    )
      AND (CAST(:cname AS TEXT) IS NULL OR c.name ILIKE CAST(:cname_pat AS TEXT))
    ORDER BY o.id ASC, oi.id ASC
    """
)

_PENDING_RESOLUTIONS_SQL = text(
    "SELECT COUNT(*) FROM warehouse_pending_price_resolution"
)


async def run(
    session: AsyncSession, customer_name: str | None = None
) -> dict[str, Any]:
    cname = (customer_name or "").strip() or None
    rows = (
        await session.execute(
            _SQL,
            {
                "cname": cname,
                "cname_pat": f"%{cname}%" if cname else None,
            },
        )
    ).mappings().all()

    orders: dict[int, dict[str, Any]] = {}
    for r in rows:
        oid = r["order_id"]
        bucket = orders.setdefault(
            oid,
            {
                "order_id": oid,
                "status": r["status"],
                "total_price": _num(r["total_price"]),
                "customer": {
                    "id": r["customer_id"],
                    "name": r["customer_name"],
                    "telegram_id": r["telegram_id"],
                    "phone": r["phone"],
                    "email": r["email"],
                },
                "items": [],
            },
        )
        if r["item_id"] is not None:
            bucket["items"].append(
                {
                    "product_name": r["product_name"],
                    "supplier": r["supplier"],
                    "quantity": _num(r["quantity"]),
                    "unit_price": _num(r["unit_price"]),
                }
            )

    result: dict[str, Any] = {"orders": list(orders.values())}

    # ADR-008 § 8: surface pending price-conflict count so operator notices them.
    resolution_count = (
        await session.execute(_PENDING_RESOLUTIONS_SQL)
    ).scalar_one()
    if resolution_count:
        result["pending_actions"] = {"price_resolutions_count": int(resolution_count)}

    return result


def _num(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)


def format_text(result: dict[str, Any]) -> str:
    orders = result.get("orders", [])
    lines = []
    if not orders:
        lines.append("Активных заказов нет.")
    else:
        lines.append(f"Активных заказов: {len(orders)}")
        for o in orders:
            c = o["customer"]
            total = f"{o['total_price']:.0f} ₽" if o.get("total_price") else "—"
            contact = c.get("telegram_id") or c.get("phone") or c.get("email") or "—"
            lines.append(
                f"\n#{o['order_id']} [{o['status']}] — {c['name']} ({contact}), "
                f"сумма: {total}"
            )
            for it in o["items"]:
                price = it.get("unit_price")
                price_s = f"{price:.0f} ₽" if price else "—"
                lines.append(
                    f"  • {it['product_name']} ({it.get('supplier') or '—'}) "
                    f"×{it['quantity']:.0f} по {price_s}"
                )

    pending = result.get("pending_actions", {})
    if pending.get("price_resolutions_count"):
        lines.append(
            f"\n⚠️  Внимание: {pending['price_resolutions_count']} "
            "неразрешённых ценовых конфликтов — используй list_pending_price_resolutions."
        )

    return "\n".join(lines)
