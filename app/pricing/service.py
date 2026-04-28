"""Pricing formula — pure functions, no side-effects, no DB access.

All parameters arrive through schema objects. This module does NOT import
from app.pricing.constants — all values come from the caller via schemas.

Pipeline: base_cost_rub → apply_margin → apply_discount → apply_rounding
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from app.pricing.schemas import (
    ItemDiscountAllocation,
    ManufacturerPriceInput,
    OrderDiscountAllocation,
    PriceCalculationResult,
    RetailPriceInput,
)

# Internal precision constant — not a business constant, just arithmetic hygiene.
_D4 = Decimal("0.0001")
_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Base-cost helpers
# ---------------------------------------------------------------------------


def calculate_retail_base_cost(params: RetailPriceInput) -> Decimal:
    """Compute base cost in RUB for the retail purchase path.

    purchase_currency == "RUB":  purchase_cost unchanged
    purchase_currency != "RUB":  purchase_cost × pricing_exchange_rate

    shipping_cost_rub = weight_kg × shipping_per_kg_usd × pricing_exchange_rate
    base_cost_rub     = purchase_cost_rub + shipping_cost_rub
    """
    rate = params.pricing_exchange_rate or _ZERO

    if params.purchase_currency != "RUB":
        purchase_cost_rub = (params.purchase_cost * rate).quantize(_D4, rounding=ROUND_HALF_UP)
    else:
        purchase_cost_rub = params.purchase_cost.quantize(_D4, rounding=ROUND_HALF_UP)

    shipping_cost_rub = (
        params.weight_kg * params.shipping_per_kg_usd * rate
    ).quantize(_D4, rounding=ROUND_HALF_UP)

    return (purchase_cost_rub + shipping_cost_rub).quantize(_D4, rounding=ROUND_HALF_UP)


def calculate_manufacturer_base_cost(params: ManufacturerPriceInput) -> Decimal:
    """Compute base cost in RUB for the manufacturer purchase path.

    base_cost = product_price_fcy × rate
              + logistics legs (origin_shipping, intl_shipping, kz_to_moscow)
              + customs_fee
              + intermediary_fee
    None legs are excluded. Decimal("0.00") legs contribute 0 but appear in breakdown.
    """
    product_price_rub = (params.product_price_fcy * params.pricing_exchange_rate).quantize(
        _D4, rounding=ROUND_HALF_UP
    )

    logistics_total = _ZERO
    for leg in (params.origin_shipping, params.intl_shipping, params.kz_to_moscow):
        if leg is not None:
            logistics_total += leg

    extras = _ZERO
    if params.customs_fee is not None:
        extras += params.customs_fee
    if params.intermediary_fee is not None:
        extras += params.intermediary_fee

    return (product_price_rub + logistics_total + extras).quantize(
        _D4, rounding=ROUND_HALF_UP
    )


# ---------------------------------------------------------------------------
# Shared pipeline steps
# ---------------------------------------------------------------------------


def apply_margin(
    base_cost: Decimal, margin_percent: Decimal
) -> tuple[Decimal, Decimal]:
    """Apply margin percentage to base cost.

    Returns (subtotal, margin_amount).
    subtotal = base_cost + margin_amount
    """
    margin_amount = (base_cost * margin_percent / Decimal("100")).quantize(
        _D4, rounding=ROUND_HALF_UP
    )
    subtotal = (base_cost + margin_amount).quantize(_D4, rounding=ROUND_HALF_UP)
    return subtotal, margin_amount


def apply_discount(
    subtotal: Decimal, discount_percent: Decimal | None
) -> tuple[Decimal, Decimal]:
    """Apply optional discount percentage to subtotal.

    Returns (price_after_discount, discount_amount).
    discount_amount = 0 when discount_percent is None or 0.
    """
    if not discount_percent:
        return subtotal, _ZERO.quantize(_D4)
    discount_amount = (subtotal * discount_percent / Decimal("100")).quantize(
        _D4, rounding=ROUND_HALF_UP
    )
    price_after_discount = (subtotal - discount_amount).quantize(
        _D4, rounding=ROUND_HALF_UP
    )
    return price_after_discount, discount_amount


def determine_rounding_step(price: Decimal, override: int | None = None) -> int:
    """Return the rounding step to use.

    Rules (ADR-004):
      - operator override: use override value
      - price < 1000 RUB  → step 10
      - price >= 1000 RUB → step 100
    """
    if override is not None:
        return override
    threshold = Decimal("1000.00")  # mirrors ROUNDING_THRESHOLD_RUB constant
    return 10 if price < threshold else 100


def apply_rounding(price: Decimal, rounding_step: int) -> Decimal:
    """Round price up to the nearest multiple of rounding_step (ceiling).

    final_price = ceil(price / step) × step
    """
    step = Decimal(str(rounding_step))
    return ((price / step).to_integral_value(rounding=ROUND_CEILING) * step).quantize(
        _D4, rounding=ROUND_HALF_UP
    )


# ---------------------------------------------------------------------------
# Breakdown builders
# ---------------------------------------------------------------------------


def _d(v: Decimal) -> float:
    """Convert Decimal to float for JSON-serialisable breakdown dict."""
    return float(v)


def build_retail_breakdown(
    params: RetailPriceInput,
    purchase_cost_rub: Decimal,
    shipping_cost_rub: Decimal,
    base_cost_rub: Decimal,
    margin_percent: Decimal,
    margin_amount: Decimal,
    subtotal: Decimal,
    discount_percent: Decimal | None,
    discount_amount: Decimal,
    pre_round_price: Decimal,
    rounding_step: int,
    final_price: Decimal,
) -> dict[str, Any]:
    """Build full audit-ready breakdown dict for retail path."""
    needs_rate = (
        params.purchase_currency != "RUB"
        or params.shipping_per_kg_usd > _ZERO
    )

    bd: dict[str, Any] = {
        "purchase_type": "retail",
        "purchase_cost": _d(params.purchase_cost),
        "purchase_currency": params.purchase_currency,
        "purchase_cost_rub": _d(purchase_cost_rub),
        "weight_kg": _d(params.weight_kg),
        "shipping_per_kg_usd": _d(params.shipping_per_kg_usd),
        "shipping_currency": params.shipping_currency,
    }

    if needs_rate:
        bd["pricing_exchange_rate"] = _d(params.pricing_exchange_rate)  # type: ignore[arg-type]
        bd["pricing_rate_id"] = params.pricing_rate_id

    bd.update(
        {
            "shipping_cost_rub": _d(shipping_cost_rub),
            "base_cost_rub": _d(base_cost_rub),
            "margin_percent": _d(margin_percent),
            "margin_amount": _d(margin_amount),
            "subtotal": _d(subtotal),
            "discount_percent": _d(discount_percent) if discount_percent else None,
            "discount_amount": _d(discount_amount),
            "pre_round_price": _d(pre_round_price),
            "rounding_step": rounding_step,
            "final_price": _d(final_price),
        }
    )
    return bd


def build_manufacturer_breakdown(
    params: ManufacturerPriceInput,
    product_price_rub: Decimal,
    base_cost_rub: Decimal,
    margin_percent: Decimal,
    margin_amount: Decimal,
    subtotal: Decimal,
    discount_percent: Decimal | None,
    discount_amount: Decimal,
    pre_round_price: Decimal,
    rounding_step: int,
    final_price: Decimal,
) -> dict[str, Any]:
    """Build full audit-ready breakdown dict for manufacturer path.

    Logistics legs: None → omitted from breakdown; Decimal("0.00") → included.
    """
    logistics: dict[str, float] = {}
    for key, val in (
        ("origin_shipping", params.origin_shipping),
        ("intl_shipping", params.intl_shipping),
        ("kz_to_moscow", params.kz_to_moscow),
    ):
        if val is not None:
            logistics[key] = _d(val)

    bd: dict[str, Any] = {
        "purchase_type": "manufacturer",
        "product_price_fcy": _d(params.product_price_fcy),
        "currency": params.currency,
        "pricing_exchange_rate": _d(params.pricing_exchange_rate),
        "pricing_rate_id": params.pricing_rate_id,
        "product_price_rub": _d(product_price_rub),
    }

    if logistics:
        bd["logistics"] = logistics

    if params.customs_fee is not None:
        bd["customs_fee"] = _d(params.customs_fee)
    if params.intermediary_fee is not None:
        bd["intermediary_fee"] = _d(params.intermediary_fee)

    bd.update(
        {
            "base_cost_rub": _d(base_cost_rub),
            "margin_percent": _d(margin_percent),
            "margin_amount": _d(margin_amount),
            "subtotal": _d(subtotal),
            "discount_percent": _d(discount_percent) if discount_percent else None,
            "discount_amount": _d(discount_amount),
            "pre_round_price": _d(pre_round_price),
            "rounding_step": rounding_step,
            "final_price": _d(final_price),
        }
    )
    return bd


# ---------------------------------------------------------------------------
# Orchestrators
# ---------------------------------------------------------------------------


def calculate_retail_price(params: RetailPriceInput) -> PriceCalculationResult:
    """Full retail price calculation pipeline.

    base_cost → margin → discount → rounding → breakdown
    """
    rate = params.pricing_exchange_rate or _ZERO

    if params.purchase_currency != "RUB":
        purchase_cost_rub = (params.purchase_cost * rate).quantize(
            _D4, rounding=ROUND_HALF_UP
        )
    else:
        purchase_cost_rub = params.purchase_cost.quantize(_D4, rounding=ROUND_HALF_UP)

    shipping_cost_rub = (
        params.weight_kg * params.shipping_per_kg_usd * rate
    ).quantize(_D4, rounding=ROUND_HALF_UP)

    base_cost_rub = (purchase_cost_rub + shipping_cost_rub).quantize(
        _D4, rounding=ROUND_HALF_UP
    )

    subtotal, margin_amount = apply_margin(base_cost_rub, params.margin_percent)
    pre_round_price, discount_amount = apply_discount(subtotal, params.discount_percent)
    step = determine_rounding_step(pre_round_price, params.rounding_step)
    final_price = apply_rounding(pre_round_price, step)

    breakdown = build_retail_breakdown(
        params=params,
        purchase_cost_rub=purchase_cost_rub,
        shipping_cost_rub=shipping_cost_rub,
        base_cost_rub=base_cost_rub,
        margin_percent=params.margin_percent,
        margin_amount=margin_amount,
        subtotal=subtotal,
        discount_percent=params.discount_percent,
        discount_amount=discount_amount,
        pre_round_price=pre_round_price,
        rounding_step=step,
        final_price=final_price,
    )

    return PriceCalculationResult(
        purchase_type="retail",
        base_cost_rub=base_cost_rub,
        margin_percent=params.margin_percent,
        margin_amount=margin_amount,
        subtotal=subtotal,
        discount_percent=params.discount_percent,
        discount_amount=discount_amount,
        pre_round_price=pre_round_price,
        rounding_step=step,
        final_price=final_price,
        breakdown=breakdown,
    )


def calculate_manufacturer_price(
    params: ManufacturerPriceInput,
) -> PriceCalculationResult:
    """Full manufacturer price calculation pipeline.

    base_cost → margin → discount → rounding → breakdown
    """
    product_price_rub = (
        params.product_price_fcy * params.pricing_exchange_rate
    ).quantize(_D4, rounding=ROUND_HALF_UP)

    logistics_total = _ZERO
    for leg in (params.origin_shipping, params.intl_shipping, params.kz_to_moscow):
        if leg is not None:
            logistics_total += leg

    extras = _ZERO
    if params.customs_fee is not None:
        extras += params.customs_fee
    if params.intermediary_fee is not None:
        extras += params.intermediary_fee

    base_cost_rub = (product_price_rub + logistics_total + extras).quantize(
        _D4, rounding=ROUND_HALF_UP
    )

    subtotal, margin_amount = apply_margin(base_cost_rub, params.margin_percent)
    pre_round_price, discount_amount = apply_discount(subtotal, params.discount_percent)
    step = determine_rounding_step(pre_round_price, params.rounding_step)
    final_price = apply_rounding(pre_round_price, step)

    breakdown = build_manufacturer_breakdown(
        params=params,
        product_price_rub=product_price_rub,
        base_cost_rub=base_cost_rub,
        margin_percent=params.margin_percent,
        margin_amount=margin_amount,
        subtotal=subtotal,
        discount_percent=params.discount_percent,
        discount_amount=discount_amount,
        pre_round_price=pre_round_price,
        rounding_step=step,
        final_price=final_price,
    )

    return PriceCalculationResult(
        purchase_type="manufacturer",
        base_cost_rub=base_cost_rub,
        margin_percent=params.margin_percent,
        margin_amount=margin_amount,
        subtotal=subtotal,
        discount_percent=params.discount_percent,
        discount_amount=discount_amount,
        pre_round_price=pre_round_price,
        rounding_step=step,
        final_price=final_price,
        breakdown=breakdown,
    )


# ---------------------------------------------------------------------------
# Weighted average price (ADR-008 — Package 3 resolution helper)
# ---------------------------------------------------------------------------


def _weighted_price_pair(
    existing_quantity: Decimal,
    existing_price: Decimal,
    new_quantity: Decimal,
    new_price: Decimal,
) -> Decimal:
    """Weighted average of two price tiers by quantity.

    Returns (existing_qty × existing_price + new_qty × new_price) / total_qty,
    quantized to 2 decimal places (ROUND_HALF_UP).

    Raises ValueError when total_quantity == 0.
    """
    total = existing_quantity + new_quantity
    if total == _ZERO:
        raise ValueError("total_quantity must be > 0")
    raw = (
        existing_quantity * existing_price + new_quantity * new_price
    ) / total
    return raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_weighted_price(
    prices: list[Decimal],
    quantities: list[int],
) -> Decimal:
    """Calculate quantity-weighted average price.

    Formula:
        weighted_avg = sum(prices[i] * quantities[i]) / sum(quantities[i])

    Both lists must have equal length and at least one element. All
    quantities must be positive (> 0). All prices must be non-negative
    (>= 0).

    Returns the weighted average as Decimal, NOT rounded — caller is
    responsible for applying ADR-004 rounding policy if needed.

    Raises:
        ValueError: lists are empty, lists have different lengths,
            any quantity <= 0, any price < 0, or sum of quantities
            is zero (only possible if all quantities are 0, but that
            is already caught by the per-element check).
    """
    if not prices or not quantities:
        raise ValueError("prices and quantities must not be empty")
    if len(prices) != len(quantities):
        raise ValueError(
            f"prices and quantities must have equal length, got {len(prices)} and {len(quantities)}"
        )
    for q in quantities:
        if q <= 0:
            raise ValueError(f"all quantities must be positive (> 0), got quantity={q}")
    for p in prices:
        if p < _ZERO:
            raise ValueError(f"all prices must be non-negative (>= 0), got price={p}")

    total_qty = sum(quantities)
    if total_qty == 0:
        raise ValueError("sum of quantities must be > 0")

    weighted_sum = sum(p * q for p, q in zip(prices, quantities, strict=True))
    return weighted_sum / Decimal(total_qty)


# ---------------------------------------------------------------------------
# Order-level discount allocation
# ---------------------------------------------------------------------------


def allocate_order_discount(
    items: list[tuple[int, Decimal]],
    discount_percent: Decimal,
) -> OrderDiscountAllocation:
    """Allocate an order-level discount proportionally across order items.

    items: list of (order_item_id, item_price)
    discount_percent: percentage of total order value to discount (e.g. Decimal("7"))

    Proportional allocation; last item receives the remainder to avoid
    rounding drift (sum of all allocations == total_discount exactly).
    """
    if not items:
        return OrderDiscountAllocation(item_allocations=[])

    total_price = sum(price for _, price in items)
    if total_price == _ZERO:
        return OrderDiscountAllocation(
            item_allocations=[
                ItemDiscountAllocation(
                    order_item_id=item_id,
                    original_price=price,
                    allocated_discount=_ZERO.quantize(_D4),
                    net_price=price,
                )
                for item_id, price in items
            ]
        )

    total_discount = (total_price * discount_percent / Decimal("100")).quantize(
        _D4, rounding=ROUND_HALF_UP
    )

    allocations: list[ItemDiscountAllocation] = []
    running_allocated = _ZERO

    for i, (item_id, price) in enumerate(items):
        is_last = i == len(items) - 1
        if is_last:
            allocated = total_discount - running_allocated
        else:
            allocated = (price / total_price * total_discount).quantize(
                _D4, rounding=ROUND_HALF_UP
            )
            running_allocated += allocated

        allocations.append(
            ItemDiscountAllocation(
                order_item_id=item_id,
                original_price=price,
                allocated_discount=allocated,
                net_price=(price - allocated).quantize(_D4, rounding=ROUND_HALF_UP),
            )
        )

    return OrderDiscountAllocation(item_allocations=allocations)
