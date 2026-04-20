"""Pydantic schemas for pricing input / output.

Constants are used here as field defaults ONLY.
service.py receives all parameters through these schemas
and never imports constants directly.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator, model_validator

from app.pricing.constants import (
    DEFAULT_MARGIN_PERCENT,
    DEFAULT_SHIPPING_CURRENCY,
    DEFAULT_SHIPPING_PER_KG_USD,
)


class RetailPriceInput(BaseModel):
    """Input for the retail purchase pricing path.

    Validation rule: pricing_exchange_rate and pricing_rate_id are
    required whenever FX conversion is needed, i.e. when
    purchase_currency != "RUB" OR shipping_per_kg_usd > 0.
    """

    purchase_cost: Decimal
    purchase_currency: str = "RUB"
    weight_kg: Decimal
    shipping_per_kg_usd: Decimal = DEFAULT_SHIPPING_PER_KG_USD
    shipping_currency: str = DEFAULT_SHIPPING_CURRENCY
    pricing_exchange_rate: Decimal | None = None
    pricing_rate_id: int | None = None
    margin_percent: Decimal = DEFAULT_MARGIN_PERCENT
    discount_percent: Decimal | None = None
    rounding_step: int | None = None  # None = auto by threshold

    @field_validator("purchase_currency", "shipping_currency")
    @classmethod
    def _validate_currency_code(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError("Currency code must be 3 uppercase ASCII letters")
        return v

    @model_validator(mode="after")
    def _validate_exchange_rate_required(self) -> RetailPriceInput:
        needs_rate = (
            self.purchase_currency != "RUB"
            or self.shipping_per_kg_usd > Decimal("0")
        )
        if needs_rate:
            if self.pricing_exchange_rate is None:
                raise ValueError(
                    "pricing_exchange_rate is required when FX conversion is needed "
                    "(purchase in foreign currency or shipping_per_kg_usd > 0)"
                )
            if self.pricing_rate_id is None:
                raise ValueError(
                    "pricing_rate_id is required when FX conversion is needed"
                )
        return self


class ManufacturerPriceInput(BaseModel):
    """Input for the manufacturer (direct / via Kazakhstan) pricing path."""

    product_price_fcy: Decimal
    currency: str  # foreign currency of the product price
    pricing_exchange_rate: Decimal
    pricing_rate_id: int
    # Logistics legs — None = not applicable (omitted from breakdown)
    # Decimal("0.00") = explicitly zero (included in breakdown)
    origin_shipping: Decimal | None = None
    intl_shipping: Decimal | None = None
    kz_to_moscow: Decimal | None = None
    customs_fee: Decimal | None = None
    intermediary_fee: Decimal | None = None
    margin_percent: Decimal = DEFAULT_MARGIN_PERCENT
    discount_percent: Decimal | None = None
    rounding_step: int | None = None  # None = auto by threshold

    @field_validator("currency")
    @classmethod
    def _validate_currency_code(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError("Currency code must be 3 uppercase ASCII letters")
        return v


class PriceCalculationResult(BaseModel):
    """Full breakdown result returned by service functions."""

    purchase_type: str  # "retail" | "manufacturer"
    base_cost_rub: Decimal
    margin_percent: Decimal
    margin_amount: Decimal
    subtotal: Decimal
    discount_percent: Decimal | None
    discount_amount: Decimal
    pre_round_price: Decimal
    rounding_step: int
    final_price: Decimal
    breakdown: dict[str, Any]


class ItemDiscountAllocation(BaseModel):
    order_item_id: int
    original_price: Decimal
    allocated_discount: Decimal
    net_price: Decimal


class OrderDiscountAllocation(BaseModel):
    item_allocations: list[ItemDiscountAllocation]
