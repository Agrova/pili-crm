"""Tool: list pending price-conflict resolutions (ADR-008 section 6)."""

from __future__ import annotations

import logging
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "list_pending_price_resolutions"
DESCRIPTION = (
    "Возвращает все неразрешённые ценовые конфликты на складе (ADR-008). "
    "Для каждого конфликта показывает три сценария разрешения: keep_old, "
    "use_new, weighted_average — с расчётом выручки, себестоимости, прибыли "
    "и маржинальности. Без параметров."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
}

logger = logging.getLogger(__name__)

_SQL = text(
    """
    SELECT
        ppr.id                          AS resolution_id,
        ppr.receipt_item_id,
        ppr.existing_stock_item_id,
        ppr.new_price_calculation_id,
        ri.product_id,
        ri.quantity                     AS receipt_quantity,
        p.name                          AS product_name,
        si.quantity                     AS existing_quantity,
        si.price_calculation_id         AS existing_calc_id,
        ec.final_price                  AS existing_final_price,
        ec.breakdown                    AS existing_breakdown,
        nc.final_price                  AS new_final_price,
        nc.breakdown                    AS new_breakdown
    FROM warehouse_pending_price_resolution ppr
    JOIN warehouse_receipt_item ri         ON ri.id  = ppr.receipt_item_id
    JOIN catalog_product p                 ON p.id   = ri.product_id
    JOIN warehouse_stock_item si           ON si.id  = ppr.existing_stock_item_id
    LEFT JOIN pricing_price_calculation ec ON ec.id  = si.price_calculation_id
    JOIN pricing_price_calculation nc      ON nc.id  = ppr.new_price_calculation_id
    ORDER BY ppr.created_at ASC
    """
)

_D1 = Decimal("0.1")
_ZERO = Decimal("0")


def _cost_from_breakdown(breakdown: dict[str, Any] | None, label: str) -> Decimal:
    """Extract unit cost (RUB) from a pricing_price_calculation.breakdown dict.

    ADR-008 § 6: look for base_cost_rub (both paths store it in breakdown).
    Falls back to purchase_cost_rub for retail path. Returns 0 with warning
    when neither field is present.
    """
    if breakdown is None:
        logger.warning("breakdown is None for %s — returning cost=0", label)
        return _ZERO
    for key in ("base_cost_rub", "purchase_cost_rub"):
        val = breakdown.get(key)
        if val is not None:
            try:
                return Decimal(str(val))
            except Exception:
                logger.warning("Cannot parse %s=%r from breakdown %s", key, val, label)
    logger.warning(
        "Neither base_cost_rub nor purchase_cost_rub found in breakdown %s — cost=0",
        label,
    )
    return _ZERO


def _rounding_step(price: Decimal) -> int:
    return 10 if price < Decimal("1000") else 100


def _apply_rounding(price: Decimal, step: int) -> Decimal:
    s = Decimal(str(step))
    return (price / s).to_integral_value(rounding=ROUND_CEILING) * s


def _scenario(
    final_unit_price: Decimal,
    total_qty: Decimal,
    total_cost: Decimal,
) -> dict[str, Any]:
    total_revenue = (final_unit_price * total_qty).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    profit = (total_revenue - total_cost).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    margin = (
        (profit / total_cost * Decimal("100")).quantize(_D1, rounding=ROUND_HALF_UP)
        if total_cost
        else _ZERO
    )
    return {
        "final_unit_price": float(final_unit_price),
        "total_quantity": float(total_qty),
        "total_revenue": float(total_revenue),
        "total_cost": float(total_cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        "profit_rub": float(profit),
        "margin_percent": float(margin),
    }


async def run(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.execute(_SQL)).mappings().all()

    conflicts: list[dict[str, Any]] = []
    for r in rows:
        existing_qty = Decimal(str(r["existing_quantity"]))
        new_qty = Decimal(str(r["receipt_quantity"]))
        total_qty = existing_qty + new_qty

        raw_ep = r["existing_final_price"]
        existing_price = Decimal(str(raw_ep)) if raw_ep is not None else _ZERO
        new_price = Decimal(str(r["new_final_price"]))

        existing_cost = _cost_from_breakdown(
            r["existing_breakdown"], f"calc={r['existing_calc_id']}"
        )
        new_cost = _cost_from_breakdown(
            r["new_breakdown"], f"calc={r['new_price_calculation_id']}"
        )

        total_cost = (existing_qty * existing_cost + new_qty * new_cost).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )

        # Weighted average price (preview — not persisted here)
        raw_wa = (existing_qty * existing_price + new_qty * new_price) / total_qty
        raw_wa = raw_wa.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        wa_step = _rounding_step(raw_wa)
        wa_price = _apply_rounding(raw_wa, wa_step)

        conflicts.append(
            {
                "resolution_id": r["resolution_id"],
                "receipt_item_id": r["receipt_item_id"],
                "product": {
                    "id": r["product_id"],
                    "name": r["product_name"],
                },
                "existing_stock": {
                    "stock_item_id": r["existing_stock_item_id"],
                    "quantity": float(existing_qty),
                    "unit_cost_rub": float(existing_cost),
                    "unit_price_rub": float(existing_price),
                },
                "new_receipt": {
                    "quantity": float(new_qty),
                    "unit_cost_rub": float(new_cost),
                    "unit_price_rub": float(new_price),
                },
                "scenarios": {
                    "keep_old": _scenario(existing_price, total_qty, total_cost),
                    "use_new": _scenario(new_price, total_qty, total_cost),
                    "weighted_average": _scenario(wa_price, total_qty, total_cost),
                },
            }
        )

    return {"conflicts": conflicts, "total": len(conflicts)}


def format_text(result: dict[str, Any]) -> str:
    conflicts = result.get("conflicts", [])
    if not conflicts:
        return "Ценовых конфликтов нет — все поступления обработаны."

    lines = [f"Неразрешённых ценовых конфликтов: {len(conflicts)}"]
    for c in conflicts:
        prod = c["product"]
        es = c["existing_stock"]
        nr = c["new_receipt"]
        sc = c["scenarios"]
        lines.append(
            f"\n[ID={c['receipt_item_id']}] {prod['name']}"
            f"\n  Склад: {es['quantity']:g} шт × {es['unit_price_rub']:,.0f} ₽"
            f"\n  Новое поступление: {nr['quantity']:g} шт × {nr['unit_price_rub']:,.0f} ₽"
        )
        for key, label in (
            ("keep_old", "Оставить старую"),
            ("use_new", "Новая цена"),
            ("weighted_average", "Средневзвешенная"),
        ):
            s = sc[key]
            lines.append(
                f"  [{key}] {label}: цена {s['final_unit_price']:,.0f} ₽, "
                f"маржа {s['margin_percent']}%"
            )
    return "\n".join(lines)
