from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base_model import Base, TimestampMixin
from app.shared.types import currency_column


class PricingExchangeRateSource(enum.StrEnum):
    api = "api"
    manual = "manual"


class PricingPurchaseType(enum.StrEnum):
    retail = "retail"
    manufacturer = "manufacturer"


class PricingExchangeRate(Base, TimestampMixin):
    __tablename__ = "pricing_exchange_rate"
    __table_args__ = (
        Index(
            "ix_pricing_exchange_rate_currencies_valid",
            "from_currency",
            "to_currency",
            "valid_from",
        ),
        CheckConstraint("rate > 0", name="ck_pricing_exchange_rate_rate"),
        CheckConstraint(
            "from_currency ~ '^[A-Z]{3}$'",
            name="ck_pricing_exchange_rate_from_currency",
        ),
        CheckConstraint(
            "to_currency ~ '^[A-Z]{3}$'",
            name="ck_pricing_exchange_rate_to_currency",
        ),
        CheckConstraint(
            "from_currency <> to_currency",
            name="ck_pricing_exchange_rate_different_currencies",
        ),
        CheckConstraint(
            "markup_percent >= 0 OR markup_percent IS NULL",
            name="ck_pricing_exchange_rate_markup_percent",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    from_currency: Mapped[str] = currency_column(nullable=False)
    to_currency: Mapped[str] = currency_column(nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    markup_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[PricingExchangeRateSource] = mapped_column(
        SAEnum(PricingExchangeRateSource, name="pricing_exchange_rate_source"),
        nullable=False,
    )


class PricingPriceCalculation(Base, TimestampMixin):
    __tablename__ = "pricing_price_calculation"
    __table_args__ = (
        Index("ix_pricing_price_calculation_product_id", "product_id"),
        Index("ix_pricing_price_calculation_calculated_at", "calculated_at"),
        Index(
            "ix_pricing_price_calculation_customer_id",
            "customer_id",
            postgresql_where=text("customer_id IS NOT NULL"),
        ),
        CheckConstraint("final_price >= 0", name="ck_pricing_price_calculation_final_price"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_pricing_price_calculation_currency",
        ),
        CheckConstraint(
            "pre_round_price >= 0",
            name="ck_pricing_price_calculation_pre_round_price",
        ),
        CheckConstraint(
            "margin_percent >= 0",
            name="ck_pricing_price_calculation_margin_percent",
        ),
        CheckConstraint(
            "discount_percent >= 0 OR discount_percent IS NULL",
            name="ck_pricing_price_calculation_discount_percent",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_product.id", ondelete="RESTRICT"),
        nullable=False,
    )
    input_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    final_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = currency_column(nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    formula_version: Mapped[str] = mapped_column(Text, nullable=False)
    # ADR-004 fields
    purchase_type: Mapped[PricingPurchaseType] = mapped_column(
        SAEnum(PricingPurchaseType, name="pricing_purchase_type"),
        nullable=False,
    )
    pre_round_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    rounding_step: Mapped[int] = mapped_column(Integer, nullable=False)
    margin_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    customer_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("orders_customer.id", ondelete="SET NULL"),
        nullable=True,
    )
