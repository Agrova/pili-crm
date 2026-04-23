"""Tool: match arrived shipment items to pending orders."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "match_shipment"
DESCRIPTION = (
    "Сопоставляет список товаров из пришедшей поставки с ожидающими заказами "
    "клиентов (все заказы кроме delivered/cancelled). Приоритет — более ранний "
    "заказ. Возвращает три списка: matched (однозначное совпадение), "
    "ambiguous (несколько кандидатов), unmatched (нет в ожидающих заказах)."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Названия товаров из поставки",
        }
    },
    "required": ["items"],
}

_MATCH_SQL = text(
    """
    SELECT
        p.id          AS product_id,
        p.name        AS product_name,
        s.name        AS supplier,
        o.id          AS order_id,
        o.status::text AS order_status,
        c.id          AS customer_id,
        c.name        AS customer_name,
        c.telegram_id,
        c.phone,
        oi.unit_price,
        oi.quantity
    FROM catalog_product p
    LEFT JOIN catalog_product_listing cpl ON cpl.product_id = p.id AND cpl.is_primary = true
    LEFT JOIN catalog_supplier s ON s.id = cpl.source_id
    JOIN orders_order_item oi ON oi.product_id = p.id
    JOIN orders_order o       ON o.id = oi.order_id
    JOIN orders_customer c    ON c.id = o.customer_id
    WHERE p.name ILIKE :pat
      AND o.status NOT IN ('delivered', 'cancelled')
    ORDER BY o.id ASC, oi.id ASC
    """
)


async def run(session: AsyncSession, items: list[str]) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for raw in items:
        query = (raw or "").strip()
        if not query:
            unmatched.append({"input_item": raw, "reason": "Пустой ввод"})
            continue

        rows = (
            await session.execute(_MATCH_SQL, {"pat": f"%{query}%"})
        ).mappings().all()

        if not rows:
            unmatched.append(
                {
                    "input_item": query,
                    "reason": "Не найден в ожидающих заказах",
                    "suggested_action": {
                        "action": "add_to_stock",
                        "product_name": query,
                    },
                }
            )
            continue

        # Group by product to detect ambiguity.
        by_product: dict[int, list[dict[str, Any]]] = {}
        for r in rows:
            by_product.setdefault(r["product_id"], []).append(dict(r))

        # If exactly one product has a name equal to the query (case-insensitive),
        # prefer it over substring-only matches.
        exact_pids = [
            pid
            for pid, entries in by_product.items()
            if entries[0]["product_name"].lower() == query.lower()
        ]
        if len(exact_pids) == 1:
            by_product = {exact_pids[0]: by_product[exact_pids[0]]}

        if len(by_product) == 1:
            first = next(iter(by_product.values()))[0]
            matched.append(
                {
                    "input_item": query,
                    "product_name": first["product_name"],
                    "supplier": first["supplier"],
                    "order_id": first["order_id"],
                    "order_status": first["order_status"],
                    "customer_name": first["customer_name"],
                    "telegram_id": first["telegram_id"],
                    "phone": first["phone"],
                    "quantity": _num(first["quantity"]),
                    "unit_price": _num(first["unit_price"]),
                }
            )
        else:
            candidates = []
            for pid, entries in by_product.items():
                first = entries[0]
                candidates.append(
                    {
                        "product_id": pid,
                        "product_name": first["product_name"],
                        "supplier": first["supplier"],
                        "order_id": first["order_id"],
                        "customer_name": first["customer_name"],
                    }
                )
            ambiguous.append({"input_item": query, "candidates": candidates})

    return {"matched": matched, "ambiguous": ambiguous, "unmatched": unmatched}


def _num(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)


def format_text(result: dict[str, Any]) -> str:
    lines: list[str] = []
    m = result.get("matched", [])
    a = result.get("ambiguous", [])
    u = result.get("unmatched", [])

    if m:
        lines.append(f"✅ Сопоставлено ({len(m)}):")
        for item in m:
            price = item.get("unit_price")
            price_s = f"{price:.0f} ₽" if price else "—"
            lines.append(
                f"  • «{item['input_item']}» → заказ #{item['order_id']} "
                f"({item['order_status']}), клиент: {item['customer_name']}, "
                f"tg: {item.get('telegram_id') or '—'}, цена: {price_s}"
            )

    if a:
        lines.append(f"\n❓ Неоднозначно ({len(a)}) — нужен выбор оператора:")
        for item in a:
            lines.append(f"  • «{item['input_item']}»:")
            for c in item["candidates"]:
                lines.append(
                    f"      - {c['product_name']} ({c['supplier']}) → "
                    f"заказ #{c['order_id']}, {c['customer_name']}"
                )

    if u:
        lines.append(f"\n❌ Без заказа ({len(u)}):")
        for item in u:
            lines.append(f"  • «{item['input_item']}» — {item['reason']}")
            hint = item.get("suggested_action")
            if hint and hint.get("action") == "add_to_stock":
                lines.append(
                    f"    → чтобы добавить на склад: "
                    f"add_to_stock('{hint['product_name']}')"
                )

    return "\n".join(lines) or "Нет данных."
