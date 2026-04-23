"""Tool: add a product to free warehouse stock (no reservation)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "add_to_stock"
DESCRIPTION = (
    "Добавляет товар на склад как свободный остаток (без резерва под заказ). "
    "Используй после match_shipment для товаров, которые не ожидает ни один "
    "клиент. Повторный вызов с тем же product_name и location увеличивает "
    "quantity, не создаёт дубликат."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "product_name": {
            "type": "string",
            "minLength": 1,
            "description": "Название товара (ILIKE по catalog_product.name).",
        },
        "quantity": {
            "type": "number",
            "exclusiveMinimum": 0,
            "default": 1.0,
        },
        "location": {
            "type": "string",
            "default": "склад",
        },
    },
    "required": ["product_name"],
}

_LOOKUP_SQL = text(
    """
    SELECT
        p.id         AS product_id,
        p.name       AS product_name,
        s.name       AS supplier
    FROM catalog_product p
    LEFT JOIN catalog_product_listing cpl ON cpl.product_id = p.id AND cpl.is_primary = true
    LEFT JOIN catalog_supplier s ON s.id = cpl.source_id
    WHERE p.name ILIKE :pat
    ORDER BY p.name ASC
    LIMIT 10
    """
)

_UPSERT_SQL = text(
    """
    INSERT INTO warehouse_stock_item (product_id, quantity, location)
    VALUES (:pid, :qty, :loc)
    ON CONFLICT (product_id, location) DO UPDATE
    SET quantity = warehouse_stock_item.quantity + EXCLUDED.quantity,
        updated_at = NOW()
    RETURNING id, quantity
    """
)


async def run(
    session: AsyncSession,
    product_name: str,
    quantity: float = 1.0,
    location: str = "склад",
) -> dict[str, Any]:
    query = (product_name or "").strip()
    if not query:
        return {"status": "error", "error": "Пустое product_name"}
    if quantity <= 0:
        return {"status": "error", "error": "quantity должно быть > 0"}

    rows = (
        await session.execute(_LOOKUP_SQL, {"pat": f"%{query}%"})
    ).mappings().all()

    if not rows:
        return {
            "status": "not_found",
            "product_name": query,
            "message": "Товар не найден в каталоге",
        }

    exact = [r for r in rows if r["product_name"].lower() == query.lower()]
    if len(exact) == 1:
        chosen = exact[0]
    elif len(rows) == 1:
        chosen = rows[0]
    else:
        return {
            "status": "ambiguous",
            "product_name": query,
            "candidates": [
                {
                    "product_id": r["product_id"],
                    "name": r["product_name"],
                    "supplier": r["supplier"],
                }
                for r in rows
            ],
        }

    result = (
        await session.execute(
            _UPSERT_SQL,
            {"pid": chosen["product_id"], "qty": quantity, "loc": location},
        )
    ).mappings().one()
    await session.commit()

    return {
        "status": "ok",
        "product_id": chosen["product_id"],
        "product_name": chosen["product_name"],
        "supplier": chosen["supplier"],
        "added_quantity": float(quantity),
        "location": location,
        "new_total_quantity": float(result["quantity"]),
        "stock_item_id": int(result["id"]),
    }


def format_text(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status == "ok":
        return (
            f"✅ Добавлено на склад: {result['product_name']} "
            f"({result['supplier']}) +{result['added_quantity']:g} "
            f"в «{result['location']}». "
            f"Итоговый остаток: {result['new_total_quantity']:g}."
        )
    if status == "not_found":
        return (
            f"❌ «{result['product_name']}» не найден в каталоге. "
            "Сначала нужно добавить товар в catalog_product."
        )
    if status == "ambiguous":
        lines = [
            f"❓ По запросу «{result['product_name']}» несколько кандидатов, "
            "уточните точное название:"
        ]
        for c in result["candidates"]:
            lines.append(f"  • {c['name']} ({c['supplier']})")
        return "\n".join(lines)
    return f"Ошибка: {result.get('error', 'неизвестная ошибка')}"
