from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base_model import Base, TimestampMixin


class WarehouseReceipt(Base, TimestampMixin):
    __tablename__ = "warehouse_receipt"
    __table_args__ = (
        Index("ix_warehouse_receipt_shipment_id", "shipment_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("procurement_shipment.id", ondelete="RESTRICT"),
        nullable=False,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    items: Mapped[list[WarehouseReceiptItem]] = relationship(
        "WarehouseReceiptItem", back_populates="receipt"
    )


class WarehouseReceiptItem(Base, TimestampMixin):
    __tablename__ = "warehouse_receipt_item"
    __table_args__ = (
        Index("ix_warehouse_receipt_item_receipt_id", "receipt_id"),
        Index("ix_warehouse_receipt_item_product_id", "product_id"),
        CheckConstraint("quantity > 0", name="ck_warehouse_receipt_item_quantity"),
        CheckConstraint(
            "actual_weight_per_unit > 0 OR actual_weight_per_unit IS NULL",
            name="ck_warehouse_receipt_item_actual_weight",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    receipt_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("warehouse_receipt.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_product.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    actual_weight_per_unit: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 3), nullable=True
    )

    receipt: Mapped[WarehouseReceipt] = relationship(
        "WarehouseReceipt", back_populates="items"
    )


class WarehouseStockItem(Base, TimestampMixin):
    __tablename__ = "warehouse_stock_item"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "location", name="uq_warehouse_stock_item_product_location"
        ),
        Index("ix_warehouse_stock_item_product_id", "product_id"),
        CheckConstraint("quantity >= 0", name="ck_warehouse_stock_item_quantity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("catalog_product.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)

    reservations: Mapped[list[WarehouseReservation]] = relationship(
        "WarehouseReservation", back_populates="stock_item"
    )


class WarehouseReservation(Base, TimestampMixin):
    __tablename__ = "warehouse_reservation"
    __table_args__ = (
        Index("ix_warehouse_reservation_order_item_id", "order_item_id"),
        Index("ix_warehouse_reservation_stock_item_id", "stock_item_id"),
        Index(
            "ix_warehouse_reservation_released_at_null",
            "released_at",
            postgresql_where=text("released_at IS NULL"),
        ),
        CheckConstraint("quantity > 0", name="ck_warehouse_reservation_quantity"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    order_item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("orders_order_item.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stock_item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("warehouse_stock_item.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    stock_item: Mapped[WarehouseStockItem] = relationship(
        "WarehouseStockItem", back_populates="reservations"
    )
