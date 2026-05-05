"""Tool: add items to an existing order (iteration 1 — items_to_add only).

items_to_remove and price_adjustments are deferred to iteration 2 (G5).
See crm-mcp/IMPROVEMENTS.md for the pending entry.
"""

from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.orders.models import IMMUTABLE_ORDER_STATUSES

NAME = "update_order"
DESCRIPTION = (
    "Добавляет позиции в существующий заказ (итерация 1: только items_to_add). "
    "Автоматически создаёт товар в каталоге если не найден. "
    "Пересчитывает total_price заказа. "
    "Требует подтверждения оператора (write-tool)."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_id": {
            "type": "integer",
            "minimum": 1,
            "description": "Номер заказа (id из orders_order).",
        },
        "items_to_add": {
            "type": "array",
            "minItems": 1,
            "description": "Список позиций для добавления в заказ.",
            "items": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "minLength": 1},
                    "price": {"type": "number", "exclusiveMinimum": 0},
                    "quantity": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "default": 1,
                    },
                },
                "required": ["product_name", "price"],
            },
        },
    },
    "required": ["order_id", "items_to_add"],
}

# ── SQL ─────────────────────────────────────────────────────────────────────

_GET_ORDER_SQL = text(
    "SELECT id, customer_id, status::text AS status, total_price "
    "FROM orders_order WHERE id = :oid"
)
_FIND_PRODUCT_SQL = text(
    "SELECT id, name FROM catalog_product WHERE lower(name) = lower(:name) LIMIT 1"
)
_FIND_PRODUCT_ILIKE_SQL = text(
    "SELECT id, name FROM catalog_product WHERE name ILIKE :pat LIMIT 2"
)
_CREATE_PRODUCT_SQL = text(
    "INSERT INTO catalog_product (name) VALUES (:name) RETURNING id, name"
)
_INSERT_ITEM_SQL = text(
    """
    INSERT INTO orders_order_item (order_id, product_id, quantity, unit_price, status)
    VALUES (:oid, :pid, :qty, :price, 'pending'::orders_order_item_status)
    RETURNING id
    """
)
_SUM_TOTAL_SQL = text(
    "SELECT COALESCE(SUM(unit_price * quantity), 0) "
    "FROM orders_order_item "
    "WHERE order_id = :oid AND status::text != 'cancelled'"
)
_UPDATE_TOTAL_SQL = text(
    "UPDATE orders_order SET total_price = :total WHERE id = :oid"
)


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _find_or_create_product(
    session: AsyncSession, name: str
) -> dict[str, Any]:
    """Return {id, name} for an existing or newly created product."""
    row = (
        await session.execute(_FIND_PRODUCT_SQL, {"name": name})
    ).mappings().one_or_none()
    if row:
        return dict(row)

    ilike = (
        await session.execute(_FIND_PRODUCT_ILIKE_SQL, {"pat": f"%{name}%"})
    ).mappings().all()
    if len(ilike) == 1:
        return dict(ilike[0])

    row = (
        await session.execute(_CREATE_PRODUCT_SQL, {"name": name})
    ).mappings().one()
    print(f"[update_order] auto-created product: {row['name']}", file=sys.stderr)
    return dict(row)


# ── Main ─────────────────────────────────────────────────────────────────────


async def run(
    session: AsyncSession,
    order_id: int,
    items_to_add: list[dict[str, Any]],
) -> dict[str, Any]:
    oid = int(order_id)

    order = (
        await session.execute(_GET_ORDER_SQL, {"oid": oid})
    ).mappings().one_or_none()
    if order is None:
        return {"status": "error", "error": "order_not_found", "order_id": oid}
    if order["status"] in {s.value for s in IMMUTABLE_ORDER_STATUSES}:
        return {
            "status": "error",
            "error": "order_immutable",
            "order_id": oid,
            "order_status": order["status"],
        }

    try:
        added_items: list[dict[str, Any]] = []

        for inp in items_to_add:
            pname = (inp.get("product_name") or "").strip()
            if not pname:
                raise ValueError("product_name не может быть пустым")
            try:
                price = Decimal(str(inp["price"]))
                qty = Decimal(str(inp.get("quantity", 1)))
            except (InvalidOperation, KeyError) as e:
                raise ValueError(
                    f"Некорректная цена/количество для «{pname}»: {e}"
                ) from e

            product = await _find_or_create_product(session, pname)

            item_row = (
                await session.execute(
                    _INSERT_ITEM_SQL,
                    {
                        "oid": oid,
                        "pid": product["id"],
                        "qty": str(qty),
                        "price": str(price),
                    },
                )
            ).mappings().one()

            line = price * qty
            added_items.append(
                {
                    "item_id": item_row["id"],
                    "product_id": product["id"],
                    "product_name": product["name"],
                    "quantity": float(qty),
                    "unit_price": float(price),
                    "line_total": float(line),
                }
            )

        new_total = Decimal(
            str((await session.execute(_SUM_TOTAL_SQL, {"oid": oid})).scalar())
        )
        await session.execute(_UPDATE_TOTAL_SQL, {"total": str(new_total), "oid": oid})
        await session.commit()

    except Exception as exc:
        await session.rollback()
        return {"status": "error", "error": str(exc)}

    return {
        "status": "ok",
        "order_id": oid,
        "order_display": f"З-{oid:03d}",
        "customer_id": order["customer_id"],
        "added_items": added_items,
        "new_total": float(new_total),
        "order_status": order["status"],
    }


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        err = result.get("error", "неизвестная ошибка")
        if err == "order_not_found":
            return f"Ошибка: заказ id={result.get('order_id')} не найден."
        if err == "order_immutable":
            oid_val = result.get("order_id", 0)
            st = result.get("order_status", "?")
            return f"Ошибка: заказ З-{oid_val:03d} в статусе '{st}' — редактирование запрещено."
        return f"Ошибка: {err}"
    n = len(result["added_items"])
    total = result["new_total"]
    noun = "позиция" if n == 1 else "позиций"
    return (
        f"✅ В заказ {result['order_display']} добавлено {n} {noun}. "
        f"Новый итог: {total:,.0f} ₽."
    )
