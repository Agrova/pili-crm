"""Tool: search catalog products with supplier and stock info."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "search_products"
DESCRIPTION = (
    "Ищет товары в каталоге по подстроке названия (ILIKE). Возвращает "
    "название, поставщика, заявленный и фактический вес, остаток на складе."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
    },
    "required": ["query"],
}

_SQL = text(
    """
    SELECT
        p.id,
        p.name,
        p.sku,
        p.declared_weight,
        p.actual_weight,
        s.name AS supplier,
        COALESCE(SUM(wsi.quantity), 0) AS stock_qty
    FROM catalog_product p
    LEFT JOIN catalog_product_listing cpl ON cpl.product_id = p.id AND cpl.is_primary = true
    LEFT JOIN catalog_supplier s ON s.id = cpl.source_id
    LEFT JOIN warehouse_stock_item wsi ON wsi.product_id = p.id
    WHERE p.name ILIKE :pat
    GROUP BY p.id, p.name, p.sku, p.declared_weight, p.actual_weight, s.name
    ORDER BY p.name ASC
    LIMIT :lim
    """
)


async def run(
    session: AsyncSession, query: str, limit: int = 20
) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"query": query, "results": []}
    rows = (
        await session.execute(
            _SQL, {"pat": f"%{q}%", "lim": max(1, min(limit, 50))}
        )
    ).mappings().all()
    return {
        "query": q,
        "results": [
            {
                "product_id": r["id"],
                "name": r["name"],
                "sku": r["sku"],
                "supplier": r["supplier"],
                "declared_weight": _num(r["declared_weight"]),
                "actual_weight": _num(r["actual_weight"]),
                "stock_qty": _num(r["stock_qty"]) or 0.0,
            }
            for r in rows
        ],
    }


def _num(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)


def format_text(result: dict[str, Any]) -> str:
    res = result.get("results", [])
    q = result.get("query", "")
    if not res:
        return f"Товары по запросу «{q}» не найдены."
    lines = [f"Найдено {len(res)} по запросу «{q}»:"]
    for r in res:
        weight = r.get("actual_weight") or r.get("declared_weight")
        weight_s = f"{weight:g} кг" if weight else "—"
        sku_s = f" [{r['sku']}]" if r.get("sku") else ""
        stock = r.get("stock_qty") or 0
        stock_s = f"склад: {stock:g}" if stock else "склад: 0"
        lines.append(
            f"  • {r['name']}{sku_s} ({r['supplier']}) "
            f"— {weight_s}, {stock_s}"
        )
    return "\n".join(lines)
