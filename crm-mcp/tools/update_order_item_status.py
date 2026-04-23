"""Tool: update the status of a specific order item and re-derive order status."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "update_order_item_status"
DESCRIPTION = (
    "Обновляет статус позиции заказа. Принимает название товара и новый статус "
    "(на русском или английском). Автоматически пересчитывает статус заказа. "
    "Используй когда оператор сообщает об изменении статуса товара: "
    "«Veritas Shooting Board передан клиенту», «стамеска Pfeil получена на склад»."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_name": {
            "type": "string",
            "minLength": 1,
            "description": "Название товара (ILIKE по catalog_product.name).",
        },
        "new_status": {
            "type": "string",
            "description": (
                "Новый статус: pending, ordered, shipped, at_forwarder, "
                "arrived, delivered, cancelled. Принимаются русские названия."
            ),
        },
        "customer_name": {
            "type": "string",
            "description": "Имя клиента для уточнения при нескольких совпадениях.",
        },
        "order_id": {
            "type": "integer",
            "description": "Номер заказа если известен.",
        },
    },
    "required": ["product_name", "new_status"],
}

# Russian aliases → enum value
_RU_STATUS: dict[str, str] = {
    "заказан у поставщика": "ordered",
    "заказан": "ordered",
    "отправлен поставщиком": "shipped",
    "отправлен": "shipped",
    "получен форвардером": "at_forwarder",
    "у форвардера": "at_forwarder",
    "склад сша": "at_forwarder",
    "получен на склад": "arrived",
    "получен": "arrived",
    "пришёл": "arrived",
    "передан клиенту": "delivered",
    "выдан": "delivered",
    "забрал": "delivered",
    "отменён": "cancelled",
    "отмена": "cancelled",
}

_VALID_STATUSES = frozenset(
    {"pending", "ordered", "shipped", "at_forwarder", "arrived", "delivered", "cancelled"}
)

_LOOKUP_SQL = text(
    """
    SELECT
        oi.id               AS item_id,
        oi.order_id,
        oi.status::text     AS item_status,
        o.status::text      AS order_status,
        p.id                AS product_id,
        p.name              AS product_name,
        s.name              AS supplier,
        c.id                AS customer_id,
        c.name              AS customer_name
    FROM orders_order_item oi
    JOIN catalog_product p  ON p.id  = oi.product_id
    LEFT JOIN catalog_product_listing cpl ON cpl.product_id = p.id AND cpl.is_primary = true
    LEFT JOIN catalog_supplier s ON s.id = cpl.source_id
    JOIN orders_order o     ON o.id  = oi.order_id
    JOIN orders_customer c  ON c.id  = o.customer_id
    WHERE p.name ILIKE :pat
      AND oi.status NOT IN ('delivered', 'cancelled')
      AND (CAST(:customer_pat AS TEXT) IS NULL OR c.name ILIKE :customer_pat)
      AND (CAST(:order_id AS BIGINT) IS NULL OR o.id = CAST(:order_id AS BIGINT))
    ORDER BY o.id ASC, oi.id ASC
    LIMIT 20
    """
)

_UPDATE_SQL = text(
    """
    UPDATE orders_order_item
    SET status     = CAST(:new_status AS orders_order_item_status),
        updated_at = NOW()
    WHERE id = :item_id
    """
)

_STATUS_AFTER_SQL = text(
    """
    SELECT
        oi.status::text  AS item_status,
        o.status::text   AS order_status
    FROM orders_order_item oi
    JOIN orders_order o ON o.id = oi.order_id
    WHERE oi.id = :item_id
    """
)


def _parse_status(raw: str) -> str | None:
    """Return enum value or None if unrecognised."""
    normalised = raw.strip().lower()
    if normalised in _VALID_STATUSES:
        return normalised
    return _RU_STATUS.get(normalised)


async def run(
    session: AsyncSession,
    product_name: str,
    new_status: str,
    customer_name: str | None = None,
    order_id: int | None = None,
) -> dict[str, Any]:
    # 1. Parse and validate status
    parsed = _parse_status(new_status)
    if parsed is None:
        return {
            "status": "error",
            "error": (
                f"Нераспознанный статус «{new_status}». "
                f"Допустимые значения: {', '.join(sorted(_VALID_STATUSES))}. "
                "Также принимаются: «передан клиенту», «получен на склад», "
                "«отправлен поставщиком», «получен форвардером» и др."
            ),
        }

    # 2. Search for matching active items
    query = (product_name or "").strip()
    customer_pat = f"%{customer_name.strip()}%" if customer_name else None

    rows = (
        await session.execute(
            _LOOKUP_SQL,
            {
                "pat": f"%{query}%",
                "customer_pat": customer_pat,
                "order_id": order_id,
            },
        )
    ).mappings().all()

    if not rows:
        return {
            "status": "not_found",
            "product_name": query,
            "message": (
                "Активных позиций с таким товаром не найдено "
                "(возможно уже delivered/cancelled, или товар не существует)."
            ),
        }

    # Prefer exact product name match over substring
    exact = [r for r in rows if r["product_name"].lower() == query.lower()]
    candidates = exact if exact else list(rows)

    if len(candidates) > 1:
        return {
            "status": "ambiguous",
            "product_name": query,
            "candidates": [
                {
                    "item_id": r["item_id"],
                    "product_name": r["product_name"],
                    "supplier": r["supplier"],
                    "order_id": r["order_id"],
                    "customer_name": r["customer_name"],
                    "item_status": r["item_status"],
                }
                for r in candidates
            ],
        }

    chosen = candidates[0]
    item_id = chosen["item_id"]
    chosen_order_id = chosen["order_id"]
    old_item_status = chosen["item_status"]
    old_order_status = chosen["order_status"]

    # 3. Update item status — DB trigger automatically updates orders_order.status
    await session.execute(_UPDATE_SQL, {"new_status": parsed, "item_id": item_id})
    await session.commit()

    # 4. Read back new statuses (trigger has already fired within the same transaction)
    after = (
        await session.execute(_STATUS_AFTER_SQL, {"item_id": item_id})
    ).mappings().one()
    new_item_status = after["item_status"]
    new_order_status = after["order_status"]

    return {
        "status": "ok",
        "item_id": item_id,
        "order_id": chosen_order_id,
        "product_name": chosen["product_name"],
        "supplier": chosen["supplier"],
        "customer_name": chosen["customer_name"],
        "old_item_status": old_item_status,
        "new_item_status": new_item_status,
        "old_order_status": old_order_status,
        "new_order_status": new_order_status,
    }


def format_text(result: dict[str, Any]) -> str:
    s = result.get("status")

    if s == "ok":
        lines = [
            f"✅ Статус обновлён: «{result['product_name']}» "
            f"({result['supplier']}), заказ #{result['order_id']}, "
            f"клиент: {result['customer_name']}.",
            f"   Позиция: {result['old_item_status']} → {result['new_item_status']}",
        ]
        if result["old_order_status"] != result["new_order_status"]:
            lines.append(
                f"   Заказ:   {result['old_order_status']} → {result['new_order_status']}"
            )
        else:
            lines.append(f"   Заказ:   {result['new_order_status']} (не изменился)")
        return "\n".join(lines)

    if s == "not_found":
        return (
            f"❌ «{result['product_name']}» — активных позиций не найдено. "
            f"{result['message']}"
        )

    if s == "ambiguous":
        lines = [
            f"❓ По запросу «{result['product_name']}» нашлось несколько позиций, "
            "уточни номер заказа или имя клиента:"
        ]
        for i, c in enumerate(result["candidates"], 1):
            lines.append(
                f"  {i}. {c['product_name']} — заказ #{c['order_id']}, "
                f"клиент {c['customer_name']} ({c['item_status']})"
            )
        return "\n".join(lines)

    return f"Ошибка: {result.get('error', 'неизвестная ошибка')}"
