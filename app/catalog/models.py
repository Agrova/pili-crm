from __future__ import annotations

import enum
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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

# References pricing_purchase_type enum without cross-module Python import
_PRICING_PURCHASE_TYPE_ENUM = SAEnum(
    "retail", "manufacturer", name="pricing_purchase_type", create_type=False
)


class CatalogAttributeSource(enum.StrEnum):
    manual = "manual"
    parsed = "parsed"
    supplier = "supplier"


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

    products: Mapped[list[CatalogProduct]] = relationship(
        "CatalogProduct", back_populates="supplier"
    )


class CatalogProduct(Base, TimestampMixin):
    __tablename__ = "catalog_product"
    __table_args__ = (
        Index(
            "uq_catalog_product_supplier_sku",
            "supplier_id",
            "sku",
            unique=True,
            postgresql_where=text("sku IS NOT NULL"),
        ),
        Index("ix_catalog_product_supplier_id", "supplier_id"),
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
    supplier_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_supplier.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    declared_weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    actual_weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    supplier: Mapped[CatalogSupplier] = relationship(
        "CatalogSupplier", back_populates="products"
    )
    attributes: Mapped[list[CatalogProductAttribute]] = relationship(
        "CatalogProductAttribute", back_populates="product"
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
