from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base_model import Base, TimestampMixin
from app.shared.types import CatalogSourceKind, currency_column

# References pricing_purchase_type enum without cross-module Python import
_PRICING_PURCHASE_TYPE_ENUM = SAEnum(
    "retail", "manufacturer", name="pricing_purchase_type", create_type=False
)


class CatalogAttributeSource(enum.StrEnum):
    manual = "manual"
    parsed = "parsed"
    supplier = "supplier"


class CatalogPriceSource(enum.StrEnum):
    manual = "manual"
    parsed = "parsed"
    email = "email"
    purchase = "purchase"


class CatalogSupplier(Base, TimestampMixin):
    __tablename__ = "catalog_supplier"
    __table_args__ = (
        UniqueConstraint("name", name="uq_catalog_supplier_name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # ADR-004 field
    default_purchase_type: Mapped[str | None] = mapped_column(
        _PRICING_PURCHASE_TYPE_ENUM, nullable=True
    )
    kind: Mapped[CatalogSourceKind] = mapped_column(
        SAEnum(CatalogSourceKind, name="catalog_source_kind"),
        nullable=False,
        server_default="both",
    )

    listings: Mapped[list[CatalogProductListing]] = relationship(
        "CatalogProductListing", back_populates="source"
    )


class CatalogProduct(Base, TimestampMixin):
    __tablename__ = "catalog_product"
    __table_args__ = (
        Index("ix_catalog_product_category", "category"),
        CheckConstraint(
            "declared_weight > 0 OR declared_weight IS NULL",
            name="ck_catalog_product_declared_weight",
        ),
        CheckConstraint(
            "actual_weight > 0 OR actual_weight IS NULL",
            name="ck_catalog_product_actual_weight",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    declared_weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    actual_weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    attributes: Mapped[list[CatalogProductAttribute]] = relationship(
        "CatalogProductAttribute", back_populates="product"
    )
    listings: Mapped[list[CatalogProductListing]] = relationship(
        "CatalogProductListing", back_populates="product"
    )


class CatalogProductAttribute(Base, TimestampMixin):
    __tablename__ = "catalog_product_attribute"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "key", name="uq_catalog_product_attribute_product_key"
        ),
        Index("ix_catalog_product_attribute_product_id", "product_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_product.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[CatalogAttributeSource] = mapped_column(
        SAEnum(CatalogAttributeSource, name="catalog_attribute_source"),
        nullable=False,
    )

    product: Mapped[CatalogProduct] = relationship(
        "CatalogProduct", back_populates="attributes"
    )


class CatalogProductListing(Base, TimestampMixin):
    __tablename__ = "catalog_product_listing"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "source_id", name="uq_catalog_product_listing_product_source"
        ),
        Index(
            "uq_catalog_product_listing_primary",
            "product_id",
            unique=True,
            postgresql_where=text("is_primary = true"),
        ),
        Index("ix_catalog_product_listing_product_id", "product_id"),
        Index("ix_catalog_product_listing_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_product.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_supplier.id", ondelete="RESTRICT"),
        nullable=False,
    )
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sku_at_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    product: Mapped[CatalogProduct] = relationship(
        "CatalogProduct", back_populates="listings"
    )
    source: Mapped[CatalogSupplier] = relationship(
        "CatalogSupplier", back_populates="listings"
    )
    prices: Mapped[list[CatalogListingPrice]] = relationship(
        "CatalogListingPrice", back_populates="listing"
    )


class CatalogListingPrice(Base):
    __tablename__ = "catalog_listing_price"
    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_catalog_listing_price_price"),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_catalog_listing_price_currency"
        ),
        Index(
            "ix_catalog_listing_price_listing_observed",
            text("listing_id"),
            text("observed_at DESC"),
        ),
        Index("ix_catalog_listing_price_source", "source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_product_listing.id", ondelete="CASCADE"),
        nullable=False,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = currency_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[CatalogPriceSource] = mapped_column(
        SAEnum(CatalogPriceSource, name="catalog_price_source"),
        nullable=False,
    )
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    listing: Mapped[CatalogProductListing] = relationship(
        "CatalogProductListing", back_populates="prices"
    )
