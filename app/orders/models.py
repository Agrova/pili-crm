from __future__ import annotations

import enum
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from app.shared.types import currency_column


class OrdersOrderStatus(enum.StrEnum):
    draft = "draft"
    confirmed = "confirmed"
    in_procurement = "in_procurement"
    shipped_by_supplier = "shipped_by_supplier"
    received_by_forwarder = "received_by_forwarder"
    arrived = "arrived"
    delivered = "delivered"
    cancelled = "cancelled"


class OrdersOrderItemStatus(enum.StrEnum):
    pending = "pending"
    ordered = "ordered"
    shipped = "shipped"
    at_forwarder = "at_forwarder"
    arrived = "arrived"
    delivered = "delivered"
    cancelled = "cancelled"


# Lower weight = earlier in pipeline. Used by derive_order_status.
ITEM_STATUS_WEIGHT: dict[str, int] = {
    "pending": 0,
    "ordered": 1,
    "shipped": 2,
    "at_forwarder": 3,
    "arrived": 4,
    "delivered": 5,
    "cancelled": 99,
}

ITEM_TO_ORDER_STATUS_MAP: dict[str, str] = {
    "pending": "in_procurement",
    "ordered": "in_procurement",
    "shipped": "shipped_by_supplier",
    "at_forwarder": "received_by_forwarder",
    "arrived": "arrived",
    "delivered": "delivered",
}

# Item statuses that are still awaiting a shipment (used in shipment matching).
PENDING_ITEM_STATUSES: frozenset[OrdersOrderItemStatus] = frozenset(
    {
        OrdersOrderItemStatus.pending,
        OrdersOrderItemStatus.ordered,
        OrdersOrderItemStatus.shipped,
        OrdersOrderItemStatus.at_forwarder,
    }
)

# Statuses after which the record must not be mutated.
IMMUTABLE_ORDER_STATUSES: frozenset[OrdersOrderStatus] = frozenset(
    {
        OrdersOrderStatus.confirmed,
        OrdersOrderStatus.delivered,
        OrdersOrderStatus.cancelled,
    }
)

IMMUTABLE_ITEM_STATUSES: frozenset[OrdersOrderItemStatus] = frozenset(
    {
        OrdersOrderItemStatus.delivered,
        OrdersOrderItemStatus.cancelled,
    }
)


class OrdersCustomer(Base, TimestampMixin):
    __tablename__ = "orders_customer"
    __table_args__ = (
        Index(
            "uq_orders_customer_email",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index(
            "uq_orders_customer_telegram_id",
            "telegram_id",
            unique=True,
            postgresql_where=text("telegram_id IS NOT NULL"),
        ),
        CheckConstraint(
            "email IS NOT NULL OR phone IS NOT NULL OR telegram_id IS NOT NULL",
            name="ck_orders_customer_contact",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    telegram_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ADR-009: username stored separately — changes independently from telegram_id
    telegram_username: Mapped[str | None] = mapped_column(Text, nullable=True)

    profile: Mapped[OrdersCustomerProfile | None] = relationship(
        "OrdersCustomerProfile", back_populates="customer", uselist=False
    )
    orders: Mapped[list[OrdersOrder]] = relationship(
        "OrdersOrder", back_populates="customer"
    )


class OrdersCustomerProfile(Base, TimestampMixin):
    __tablename__ = "orders_customer_profile"
    __table_args__ = (
        UniqueConstraint("customer_id", name="uq_orders_customer_profile_customer_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("orders_customer.id", ondelete="CASCADE"),
        nullable=False,
    )
    addresses: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ADR-009 + ADR-011: structured profile facts extracted from Telegram
    # conversations. All three are JSONB *arrays* — `preferences` and
    # `incidents` per ADR-009 §structure; `delivery_preferences` per ADR-011
    # §apply (analyzer overwrites wholesale with `is_primary=false` entries,
    # operator promotes one to `is_primary=true` during verification).
    preferences: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    delivery_preferences: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    incidents: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)

    customer: Mapped[OrdersCustomer] = relationship(
        "OrdersCustomer", back_populates="profile"
    )


class OrdersOrder(Base, TimestampMixin):
    __tablename__ = "orders_order"
    __table_args__ = (
        Index("ix_orders_order_customer_id", "customer_id"),
        Index("ix_orders_order_status", "status"),
        Index("ix_orders_order_created_at", "created_at"),
        CheckConstraint(
            "total_price >= 0 OR total_price IS NULL",
            name="ck_orders_order_total_price",
        ),
        CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_orders_order_currency",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("orders_customer.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[OrdersOrderStatus] = mapped_column(
        SAEnum(OrdersOrderStatus, name="orders_order_status"),
        nullable=False,
    )
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str | None] = currency_column(nullable=True)
    # ADR-004 fields
    delivery_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    delivery_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivery_paid_by_customer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    order_discount_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )

    customer: Mapped[OrdersCustomer] = relationship(
        "OrdersCustomer", back_populates="orders"
    )
    items: Mapped[list[OrdersOrderItem]] = relationship(
        "OrdersOrderItem", back_populates="order"
    )


class OrdersOrderItem(Base, TimestampMixin):
    __tablename__ = "orders_order_item"
    __table_args__ = (
        Index("ix_orders_order_item_order_id", "order_id"),
        Index("ix_orders_order_item_product_id", "product_id"),
        Index("ix_orders_order_item_status", "status"),
        Index(
            "ix_orders_order_item_price_calculation_id",
            "price_calculation_id",
            postgresql_where=text("price_calculation_id IS NOT NULL"),
        ),
        CheckConstraint("quantity > 0", name="ck_orders_order_item_quantity"),
        CheckConstraint(
            "unit_price >= 0 OR unit_price IS NULL",
            name="ck_orders_order_item_unit_price",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("orders_order.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_product.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    status: Mapped[OrdersOrderItemStatus] = mapped_column(
        SAEnum(OrdersOrderItemStatus, name="orders_order_item_status"),
        nullable=False,
        server_default=OrdersOrderItemStatus.pending.value,
    )
    # ADR-004 field
    operator_adjusted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    price_calculation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("pricing_price_calculation.id", ondelete="SET NULL"),
        nullable=True,
    )

    order: Mapped[OrdersOrder] = relationship("OrdersOrder", back_populates="items")
