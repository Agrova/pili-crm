"""Tool: resolve a pending price conflict (ADR-008 section 7)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from app.pricing.models import PricingPriceCalculation
from app.warehouse.models import (
    WarehousePendingPriceResolution,
    WarehouseReceiptItem,
    WarehouseStockItem,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "resolve_price_resolution"
DESCRIPTION = (
    "Разрешает ценовой конфликт при поступлении товара (ADR-008). "
    "Параметры: receipt_item_id — идентификатор позиции прихода из "
    "list_pending_price_resolutions; choice — один из keep_old, use_new, "
    "weighted_average. Операция атомарна. Требует двух подтверждений оператора."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "receipt_item_id": {
            "type": "integer",
            "description": "receipt_item_id из list_pending_price_resolutions",
        },
        "choice": {
            "type": "string",
            "enum": ["keep_old", "use_new", "weighted_average"],
            "description": "Способ разрешения конфликта",
        },
    },
    "required": ["receipt_item_id", "choice"],
}

_VALID_CHOICES = frozenset({"keep_old", "use_new", "weighted_average"})
_FORMULA_VERSION = "adr-008-weighted-v1"
_ZERO = Decimal("0")


def _rounding_step(price: Decimal) -> int:
    return 10 if price < Decimal("1000") else 100


def _apply_rounding(price: Decimal, step: int) -> Decimal:
    s = Decimal(str(step))
    return (price / s).to_integral_value(rounding=ROUND_CEILING) * s


def _margin_from_calc(calc: PricingPriceCalculation) -> float:
    if calc.margin_percent is not None:
        return float(calc.margin_percent)
    return 0.0


async def run(
    session: AsyncSession,
    receipt_item_id: int,
    choice: str,
) -> dict[str, Any]:
    if choice not in _VALID_CHOICES:
        return {
            "error": "invalid_choice",
            "valid": sorted(_VALID_CHOICES),
        }

    # Load pending record by receipt_item_id (ADR-008 § 7: lookup by receipt_item_id).
    pending_result = await session.execute(
        select(WarehousePendingPriceResolution).where(
            WarehousePendingPriceResolution.receipt_item_id == receipt_item_id
        )
    )
    pending = pending_result.scalar_one_or_none()
    if pending is None:
        return {
            "error": "not_found",
            "message": "resolution not pending or already resolved",
            "receipt_item_id": receipt_item_id,
        }

    # Load related objects.
    stock_item = (
        await session.execute(
            select(WarehouseStockItem).where(
                WarehouseStockItem.id == pending.existing_stock_item_id
            )
        )
    ).scalar_one()

    receipt_item = (
        await session.execute(
            select(WarehouseReceiptItem).where(
                WarehouseReceiptItem.id == pending.receipt_item_id
            )
        )
    ).scalar_one()

    new_calc = (
        await session.execute(
            select(PricingPriceCalculation).where(
                PricingPriceCalculation.id == pending.new_price_calculation_id
            )
        )
    ).scalar_one()

    existing_calc = (
        await session.execute(
            select(PricingPriceCalculation).where(
                PricingPriceCalculation.id == stock_item.price_calculation_id
            )
        )
    ).scalar_one()

    # --- Apply resolution (all in the same transaction) ---

    if choice == "keep_old":
        stock_item.quantity = stock_item.quantity + receipt_item.quantity
        stock_item.receipt_item_id = receipt_item.id
        # price_calculation_id unchanged
        final_calc = existing_calc

    elif choice == "use_new":
        stock_item.price_calculation_id = new_calc.id
        stock_item.quantity = stock_item.quantity + receipt_item.quantity
        stock_item.receipt_item_id = receipt_item.id
        final_calc = new_calc

    else:  # weighted_average
        existing_qty = stock_item.quantity
        new_qty = receipt_item.quantity
        total_qty = existing_qty + new_qty

        raw_price = (
            existing_qty * existing_calc.final_price
            + new_qty * new_calc.final_price
        ) / total_qty
        raw_price = raw_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        step = _rounding_step(raw_price)
        final_price = _apply_rounding(raw_price, step)

        # Inherit purchase_type from existing stock calculation.
        purchase_type = existing_calc.purchase_type

        weighted_calc = PricingPriceCalculation(
            product_id=stock_item.product_id,
            input_params={
                "method": "weighted_average",
                "existing_stock_item_id": stock_item.id,
                "receipt_item_id": receipt_item.id,
                "existing_price": float(existing_calc.final_price),
                "new_price": float(new_calc.final_price),
                "existing_quantity": float(existing_qty),
                "new_quantity": float(new_qty),
            },
            breakdown={
                "method": "weighted_average",
                "weighted_price": float(raw_price),
            },
            final_price=final_price,
            currency="RUB",
            calculated_at=datetime.now(tz=UTC),
            formula_version=_FORMULA_VERSION,
            purchase_type=purchase_type,
            pre_round_price=raw_price,
            rounding_step=step,
            margin_percent=Decimal("0"),
            discount_percent=None,
        )
        session.add(weighted_calc)
        await session.flush()

        stock_item.price_calculation_id = weighted_calc.id
        stock_item.quantity = total_qty
        stock_item.receipt_item_id = receipt_item.id
        final_calc = weighted_calc

    # Delete pending record.
    await session.delete(pending)
    await session.flush()

    # Build margin for response.
    margin = _margin_from_calc(final_calc)

    return {
        "ok": True,
        "stock_item_id": stock_item.id,
        "product_id": stock_item.product_id,
        "quantity": float(stock_item.quantity),
        "final_price": float(final_calc.final_price),
        "margin_percent": margin,
    }


def format_text(result: dict[str, Any]) -> str:
    if result.get("ok"):
        return (
            f"✅ Конфликт разрешён. Товар {result['product_id']}: "
            f"остаток {result['quantity']:g} шт, "
            f"цена {result['final_price']:,.0f} ₽, "
            f"маржа {result['margin_percent']}%."
        )
    err = result.get("error")
    if err == "not_found":
        return (
            f"❌ Конфликт для receipt_item_id={result.get('receipt_item_id')} "
            "не найден или уже разрешён."
        )
    if err == "invalid_choice":
        return (
            f"❌ Неверный выбор. Допустимые: {', '.join(result.get('valid', []))}."
        )
    return f"Ошибка: {result}"
