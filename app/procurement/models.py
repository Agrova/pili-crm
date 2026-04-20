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
    Numeric,
    Text,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base_model import Base, TimestampMixin
from app.shared.types import currency_column


class ProcurementPurchaseStatus(enum.StrEnum):
    planned = "planned"
    placed = "placed"
    paid = "paid"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"


class ProcurementPurchase(Base, TimestampMixin):
    __tablename__ = "procurement_purchase"
    __table_args__ = (
        Index("ix_procurement_purchase_supplier_id", "supplier_id"),
        Index(
            "ix_procurement_purchase_order_id",
            "order_id",
            postgresql_where=text("order_id IS NOT NULL"),
        ),
        Index("ix_procurement_purchase_status", "status"),
        CheckConstraint(
            "total_cost >= 0 OR total_cost IS NULL",
            name="ck_procurement_purchase_total_cost",
        ),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_procurement_purchase_currency",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    supplier_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_supplier.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("orders_order.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[ProcurementPurchaseStatus] = mapped_column(
        SAEnum(ProcurementPurchaseStatus, name="procurement_purchase_status"),
        nullable=False,
    )
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str | None] = currency_column(nullable=True)

    items: Mapped[list[ProcurementPurchaseItem]] = relationship(
        "ProcurementPurchaseItem", back_populates="purchase"
    )
    shipments: Mapped[list[ProcurementShipment]] = relationship(
        "ProcurementShipment", back_populates="purchase"
    )


class ProcurementPurchaseItem(Base, TimestampMixin):
    __tablename__ = "procurement_purchase_item"
    __table_args__ = (
        Index("ix_procurement_purchase_item_purchase_id", "purchase_id"),
        Index("ix_procurement_purchase_item_product_id", "product_id"),
        CheckConstraint("quantity > 0", name="ck_procurement_purchase_item_quantity"),
        CheckConstraint(
            "unit_cost >= 0 OR unit_cost IS NULL",
            name="ck_procurement_purchase_item_unit_cost",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    purchase_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("procurement_purchase.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_product.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)

    purchase: Mapped[ProcurementPurchase] = relationship(
        "ProcurementPurchase", back_populates="items"
    )


class ProcurementShipment(Base, TimestampMixin):
    __tablename__ = "procurement_shipment"
    __table_args__ = (
        Index("ix_procurement_shipment_purchase_id", "purchase_id"),
        Index(
            "ix_procurement_shipment_tracking_number",
            "tracking_number",
            postgresql_where=text("tracking_number IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    purchase_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("procurement_purchase.id", ondelete="CASCADE"),
        nullable=False,
    )
    tracking_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    carrier: Mapped[str | None] = mapped_column(Text, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expected_arrival: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    purchase: Mapped[ProcurementPurchase] = relationship(
        "ProcurementPurchase", back_populates="shipments"
    )
    events: Mapped[list[ProcurementTrackingEvent]] = relationship(
        "ProcurementTrackingEvent", back_populates="shipment"
    )


class ProcurementTrackingEvent(Base, TimestampMixin):
    __tablename__ = "procurement_tracking_event"
    __table_args__ = (
        Index("ix_procurement_tracking_event_shipment_id", "shipment_id"),
        Index("ix_procurement_tracking_event_event_at", "event_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("procurement_shipment.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    shipment: Mapped[ProcurementShipment] = relationship(
        "ProcurementShipment", back_populates="events"
    )
