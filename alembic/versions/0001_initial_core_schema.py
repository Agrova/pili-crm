"""initial core schema

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-04-19 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Enum types ---
    op.execute(
        "CREATE TYPE catalog_attribute_source AS ENUM ('manual', 'parsed', 'supplier')"
    )
    op.execute(
        "CREATE TYPE orders_order_status AS ENUM ("
        "'draft', 'confirmed', 'in_procurement', 'in_transit', "
        "'arrived', 'ready_for_pickup', 'delivered', 'cancelled')"
    )
    op.execute(
        "CREATE TYPE procurement_purchase_status AS ENUM "
        "('planned', 'placed', 'paid', 'shipped', 'delivered', 'cancelled')"
    )
    op.execute(
        "CREATE TYPE pricing_exchange_rate_source AS ENUM ('api', 'manual')"
    )
    op.execute(
        "CREATE TYPE communications_link_target_module AS ENUM "
        "('catalog', 'orders', 'procurement', 'warehouse')"
    )
    op.execute(
        "CREATE TYPE communications_link_confidence AS ENUM "
        "('manual', 'auto', 'suggested')"
    )
    op.execute(
        "CREATE TYPE finance_entry_type AS ENUM "
        "('income', 'expense', 'transfer', 'exchange')"
    )
    op.execute(
        "CREATE TYPE finance_expense_category AS ENUM "
        "('purchase', 'logistics', 'packaging', 'commission', 'tax', 'other')"
    )
    op.execute("CREATE TYPE finance_tax_type AS ENUM ('general')")
    op.execute(
        "CREATE TYPE finance_exchange_rate_source AS ENUM ('bank_statement', 'manual')"
    )

    # --- catalog_supplier ---
    op.create_table(
        "catalog_supplier",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("contact_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_catalog_supplier_name"),
    )

    # --- catalog_product ---
    op.create_table(
        "catalog_product",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("sku", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("declared_weight", sa.Numeric(10, 3), nullable=True),
        sa.Column("actual_weight", sa.Numeric(10, 3), nullable=True),
        sa.Column("photo_url", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"],
            ["catalog_supplier.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "declared_weight > 0 OR declared_weight IS NULL",
            name="ck_catalog_product_declared_weight",
        ),
        sa.CheckConstraint(
            "actual_weight > 0 OR actual_weight IS NULL",
            name="ck_catalog_product_actual_weight",
        ),
    )
    op.create_index(
        "uq_catalog_product_supplier_sku",
        "catalog_product",
        ["supplier_id", "sku"],
        unique=True,
        postgresql_where=sa.text("sku IS NOT NULL"),
    )
    op.create_index(
        "ix_catalog_product_supplier_id", "catalog_product", ["supplier_id"]
    )
    op.create_index("ix_catalog_product_category", "catalog_product", ["category"])

    # --- catalog_product_attribute ---
    op.create_table(
        "catalog_product_attribute",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(
                "manual",
                "parsed",
                "supplier",
                name="catalog_attribute_source",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["catalog_product.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id", "key", name="uq_catalog_product_attribute_product_key"
        ),
    )
    op.create_index(
        "ix_catalog_product_attribute_product_id",
        "catalog_product_attribute",
        ["product_id"],
    )

    # --- orders_customer ---
    op.create_table(
        "orders_customer",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("telegram_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "email IS NOT NULL OR phone IS NOT NULL OR telegram_id IS NOT NULL",
            name="ck_orders_customer_contact",
        ),
    )
    op.create_index(
        "uq_orders_customer_email",
        "orders_customer",
        ["email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.create_index(
        "uq_orders_customer_telegram_id",
        "orders_customer",
        ["telegram_id"],
        unique=True,
        postgresql_where=sa.text("telegram_id IS NOT NULL"),
    )

    # --- orders_customer_profile ---
    op.create_table(
        "orders_customer_profile",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("addresses", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["orders_customer.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "customer_id", name="uq_orders_customer_profile_customer_id"
        ),
    )

    # --- orders_order ---
    op.create_table(
        "orders_order",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "draft",
                "confirmed",
                "in_procurement",
                "in_transit",
                "arrived",
                "ready_for_pickup",
                "delivered",
                "cancelled",
                name="orders_order_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("total_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["orders_customer.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "total_price >= 0 OR total_price IS NULL",
            name="ck_orders_order_total_price",
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_orders_order_currency"
        ),
    )
    op.create_index("ix_orders_order_customer_id", "orders_order", ["customer_id"])
    op.create_index("ix_orders_order_status", "orders_order", ["status"])
    op.create_index("ix_orders_order_created_at", "orders_order", ["created_at"])

    # --- pricing_exchange_rate ---
    op.create_table(
        "pricing_exchange_rate",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("from_currency", sa.CHAR(3), nullable=False),
        sa.Column("to_currency", sa.CHAR(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("markup_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(
                "api", "manual", name="pricing_exchange_rate_source", create_type=False
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("rate > 0", name="ck_pricing_exchange_rate_rate"),
        sa.CheckConstraint(
            "from_currency ~ '^[A-Z]{3}$'",
            name="ck_pricing_exchange_rate_from_currency",
        ),
        sa.CheckConstraint(
            "to_currency ~ '^[A-Z]{3}$'",
            name="ck_pricing_exchange_rate_to_currency",
        ),
        sa.CheckConstraint(
            "from_currency <> to_currency",
            name="ck_pricing_exchange_rate_different_currencies",
        ),
        sa.CheckConstraint(
            "markup_percent >= 0 OR markup_percent IS NULL",
            name="ck_pricing_exchange_rate_markup_percent",
        ),
    )
    op.create_index(
        "ix_pricing_exchange_rate_currencies_valid",
        "pricing_exchange_rate",
        ["from_currency", "to_currency", "valid_from"],
    )

    # --- pricing_price_calculation ---
    op.create_table(
        "pricing_price_calculation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("input_params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("final_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("formula_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["catalog_product.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "final_price >= 0", name="ck_pricing_price_calculation_final_price"
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_pricing_price_calculation_currency",
        ),
    )
    op.create_index(
        "ix_pricing_price_calculation_product_id",
        "pricing_price_calculation",
        ["product_id"],
    )
    op.create_index(
        "ix_pricing_price_calculation_calculated_at",
        "pricing_price_calculation",
        ["calculated_at"],
    )

    # --- orders_order_item ---
    op.create_table(
        "orders_order_item",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("unit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_calculation_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders_order.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["product_id"], ["catalog_product.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["price_calculation_id"],
            ["pricing_price_calculation.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity > 0", name="ck_orders_order_item_quantity"),
        sa.CheckConstraint(
            "unit_price >= 0 OR unit_price IS NULL",
            name="ck_orders_order_item_unit_price",
        ),
    )
    op.create_index(
        "ix_orders_order_item_order_id", "orders_order_item", ["order_id"]
    )
    op.create_index(
        "ix_orders_order_item_product_id", "orders_order_item", ["product_id"]
    )
    op.create_index(
        "ix_orders_order_item_price_calculation_id",
        "orders_order_item",
        ["price_calculation_id"],
        postgresql_where=sa.text("price_calculation_id IS NOT NULL"),
    )

    # --- procurement_purchase ---
    op.create_table(
        "procurement_purchase",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "planned",
                "placed",
                "paid",
                "shipped",
                "delivered",
                "cancelled",
                name="procurement_purchase_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("total_cost", sa.Numeric(18, 4), nullable=True),
        sa.Column("currency", sa.CHAR(3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["catalog_supplier.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders_order.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "total_cost >= 0 OR total_cost IS NULL",
            name="ck_procurement_purchase_total_cost",
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_procurement_purchase_currency"
        ),
    )
    op.create_index(
        "ix_procurement_purchase_supplier_id",
        "procurement_purchase",
        ["supplier_id"],
    )
    op.create_index(
        "ix_procurement_purchase_order_id",
        "procurement_purchase",
        ["order_id"],
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )
    op.create_index(
        "ix_procurement_purchase_status", "procurement_purchase", ["status"]
    )

    # --- procurement_purchase_item ---
    op.create_table(
        "procurement_purchase_item",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("purchase_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(18, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["purchase_id"], ["procurement_purchase.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["catalog_product.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "quantity > 0", name="ck_procurement_purchase_item_quantity"
        ),
        sa.CheckConstraint(
            "unit_cost >= 0 OR unit_cost IS NULL",
            name="ck_procurement_purchase_item_unit_cost",
        ),
    )
    op.create_index(
        "ix_procurement_purchase_item_purchase_id",
        "procurement_purchase_item",
        ["purchase_id"],
    )
    op.create_index(
        "ix_procurement_purchase_item_product_id",
        "procurement_purchase_item",
        ["product_id"],
    )

    # --- procurement_shipment ---
    op.create_table(
        "procurement_shipment",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("purchase_id", sa.BigInteger(), nullable=False),
        sa.Column("tracking_number", sa.Text(), nullable=True),
        sa.Column("carrier", sa.Text(), nullable=True),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_arrival", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["purchase_id"], ["procurement_purchase.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_procurement_shipment_purchase_id",
        "procurement_shipment",
        ["purchase_id"],
    )
    op.create_index(
        "ix_procurement_shipment_tracking_number",
        "procurement_shipment",
        ["tracking_number"],
        postgresql_where=sa.text("tracking_number IS NOT NULL"),
    )

    # --- procurement_tracking_event ---
    op.create_table(
        "procurement_tracking_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["procurement_shipment.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_procurement_tracking_event_shipment_id",
        "procurement_tracking_event",
        ["shipment_id"],
    )
    op.create_index(
        "ix_procurement_tracking_event_event_at",
        "procurement_tracking_event",
        ["event_at"],
    )

    # --- warehouse_receipt ---
    op.create_table(
        "warehouse_receipt",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("shipment_id", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["shipment_id"], ["procurement_shipment.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_warehouse_receipt_shipment_id", "warehouse_receipt", ["shipment_id"]
    )

    # --- warehouse_receipt_item ---
    op.create_table(
        "warehouse_receipt_item",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("receipt_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("actual_weight_per_unit", sa.Numeric(10, 3), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"], ["warehouse_receipt.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["catalog_product.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "quantity > 0", name="ck_warehouse_receipt_item_quantity"
        ),
        sa.CheckConstraint(
            "actual_weight_per_unit > 0 OR actual_weight_per_unit IS NULL",
            name="ck_warehouse_receipt_item_actual_weight",
        ),
    )
    op.create_index(
        "ix_warehouse_receipt_item_receipt_id",
        "warehouse_receipt_item",
        ["receipt_id"],
    )
    op.create_index(
        "ix_warehouse_receipt_item_product_id",
        "warehouse_receipt_item",
        ["product_id"],
    )

    # --- warehouse_stock_item ---
    op.create_table(
        "warehouse_stock_item",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["catalog_product.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id", "location", name="uq_warehouse_stock_item_product_location"
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_warehouse_stock_item_quantity"),
    )
    op.create_index(
        "ix_warehouse_stock_item_product_id", "warehouse_stock_item", ["product_id"]
    )

    # --- warehouse_reservation ---
    op.create_table(
        "warehouse_reservation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("order_item_id", sa.BigInteger(), nullable=False),
        sa.Column("stock_item_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"], ["orders_order_item.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["stock_item_id"], ["warehouse_stock_item.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity > 0", name="ck_warehouse_reservation_quantity"),
    )
    op.create_index(
        "ix_warehouse_reservation_order_item_id",
        "warehouse_reservation",
        ["order_item_id"],
    )
    op.create_index(
        "ix_warehouse_reservation_stock_item_id",
        "warehouse_reservation",
        ["stock_item_id"],
    )
    op.create_index(
        "ix_warehouse_reservation_released_at_null",
        "warehouse_reservation",
        ["released_at"],
        postgresql_where=sa.text("released_at IS NULL"),
    )

    # --- finance_ledger_entry ---
    op.create_table(
        "finance_ledger_entry",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "entry_type",
            postgresql.ENUM(
                "income",
                "expense",
                "transfer",
                "exchange",
                name="finance_entry_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("related_module", sa.Text(), nullable=True),
        sa.Column("related_entity", sa.Text(), nullable=True),
        sa.Column("related_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="ck_finance_ledger_entry_currency"
        ),
    )
    op.create_index(
        "ix_finance_ledger_entry_entry_at", "finance_ledger_entry", ["entry_at"]
    )
    op.create_index(
        "ix_finance_ledger_entry_entry_type", "finance_ledger_entry", ["entry_type"]
    )
    op.create_index(
        "ix_finance_ledger_entry_related",
        "finance_ledger_entry",
        ["related_module", "related_entity", "related_id"],
        postgresql_where=sa.text("related_id IS NOT NULL"),
    )

    # --- finance_exchange_rate ---
    op.create_table(
        "finance_exchange_rate",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("from_currency", sa.CHAR(3), nullable=False),
        sa.Column("to_currency", sa.CHAR(3), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "source",
            postgresql.ENUM(
                "bank_statement",
                "manual",
                name="finance_exchange_rate_source",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("bank", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("rate > 0", name="ck_finance_exchange_rate_rate"),
        sa.CheckConstraint(
            "from_currency ~ '^[A-Z]{3}$'",
            name="ck_finance_exchange_rate_from_currency",
        ),
        sa.CheckConstraint(
            "to_currency ~ '^[A-Z]{3}$'",
            name="ck_finance_exchange_rate_to_currency",
        ),
        sa.CheckConstraint(
            "from_currency <> to_currency",
            name="ck_finance_exchange_rate_different_currencies",
        ),
    )
    op.create_index(
        "ix_finance_exchange_rate_currencies_observed",
        "finance_exchange_rate",
        ["from_currency", "to_currency", "observed_at"],
    )

    # --- finance_exchange_operation ---
    op.create_table(
        "finance_exchange_operation",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("operated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("from_currency", sa.CHAR(3), nullable=False),
        sa.Column("from_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("to_currency", sa.CHAR(3), nullable=False),
        sa.Column("to_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("bank_exchange_rate_id", sa.BigInteger(), nullable=False),
        sa.Column("bank", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["bank_exchange_rate_id"],
            ["finance_exchange_rate.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "from_amount > 0", name="ck_finance_exchange_operation_from_amount"
        ),
        sa.CheckConstraint(
            "to_amount > 0", name="ck_finance_exchange_operation_to_amount"
        ),
        sa.CheckConstraint(
            "from_currency <> to_currency",
            name="ck_finance_exchange_operation_different_currencies",
        ),
        sa.CheckConstraint(
            "from_currency ~ '^[A-Z]{3}$'",
            name="ck_finance_exchange_operation_from_currency",
        ),
        sa.CheckConstraint(
            "to_currency ~ '^[A-Z]{3}$'",
            name="ck_finance_exchange_operation_to_currency",
        ),
    )
    op.create_index(
        "ix_finance_exchange_operation_operated_at",
        "finance_exchange_operation",
        ["operated_at"],
    )
    op.create_index(
        "ix_finance_exchange_operation_bank_exchange_rate_id",
        "finance_exchange_operation",
        ["bank_exchange_rate_id"],
    )

    # --- finance_expense ---
    op.create_table(
        "finance_expense",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("ledger_entry_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "category",
            postgresql.ENUM(
                "purchase",
                "logistics",
                "packaging",
                "commission",
                "tax",
                "other",
                name="finance_expense_category",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("supplier_id", sa.BigInteger(), nullable=True),
        sa.Column("purchase_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ledger_entry_id"], ["finance_ledger_entry.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["catalog_supplier.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["purchase_id"], ["procurement_purchase.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ledger_entry_id", name="uq_finance_expense_ledger_entry_id"
        ),
    )
    op.create_index("ix_finance_expense_category", "finance_expense", ["category"])
    op.create_index(
        "ix_finance_expense_supplier_id",
        "finance_expense",
        ["supplier_id"],
        postgresql_where=sa.text("supplier_id IS NOT NULL"),
    )
    op.create_index(
        "ix_finance_expense_purchase_id",
        "finance_expense",
        ["purchase_id"],
        postgresql_where=sa.text("purchase_id IS NOT NULL"),
    )

    # --- finance_tax_entry ---
    op.create_table(
        "finance_tax_entry",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("ledger_entry_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "tax_type",
            postgresql.ENUM("general", name="finance_tax_type", create_type=False),
            nullable=False,
        ),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("base_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column("tax_amount", sa.Numeric(18, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ledger_entry_id"], ["finance_ledger_entry.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ledger_entry_id", name="uq_finance_tax_entry_ledger_entry_id"
        ),
        sa.CheckConstraint("base_amount >= 0", name="ck_finance_tax_entry_base_amount"),
        sa.CheckConstraint("tax_amount >= 0", name="ck_finance_tax_entry_tax_amount"),
    )
    op.create_index(
        "ix_finance_tax_entry_tax_type_period",
        "finance_tax_entry",
        ["tax_type", "period"],
    )

    # --- communications_email_thread ---
    op.create_table(
        "communications_email_thread",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("gmail_thread_id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column(
            "participants", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "gmail_thread_id",
            name="uq_communications_email_thread_gmail_thread_id",
        ),
    )

    # --- communications_email_message ---
    op.create_table(
        "communications_email_message",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("thread_id", sa.BigInteger(), nullable=False),
        sa.Column("gmail_message_id", sa.Text(), nullable=False),
        sa.Column("from_address", sa.Text(), nullable=False),
        sa.Column("to_addresses", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_mime", postgresql.BYTEA(), nullable=True),
        sa.Column("parsed_body", sa.Text(), nullable=True),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], ["communications_email_thread.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "gmail_message_id",
            name="uq_communications_email_message_gmail_message_id",
        ),
    )
    op.create_index(
        "ix_communications_email_message_thread_id",
        "communications_email_message",
        ["thread_id"],
    )
    op.create_index(
        "ix_communications_email_message_from_address",
        "communications_email_message",
        ["from_address"],
    )
    op.create_index(
        "ix_communications_email_message_sent_at",
        "communications_email_message",
        ["sent_at"],
    )

    # --- communications_telegram_chat ---
    op.create_table(
        "communications_telegram_chat",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("telegram_chat_id", sa.Text(), nullable=False),
        sa.Column("chat_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_chat_id",
            name="uq_communications_telegram_chat_telegram_chat_id",
        ),
    )

    # --- communications_telegram_message ---
    op.create_table(
        "communications_telegram_message",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_message_id", sa.Text(), nullable=False),
        sa.Column("from_user_id", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column(
            "raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"], ["communications_telegram_chat.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chat_id",
            "telegram_message_id",
            name="uq_communications_telegram_message_chat_msg",
        ),
    )
    op.create_index(
        "ix_communications_telegram_message_chat_id",
        "communications_telegram_message",
        ["chat_id"],
    )
    op.create_index(
        "ix_communications_telegram_message_sent_at",
        "communications_telegram_message",
        ["sent_at"],
    )

    # --- communications_link ---
    op.create_table(
        "communications_link",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("email_message_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "target_module",
            postgresql.ENUM(
                "catalog",
                "orders",
                "procurement",
                "warehouse",
                name="communications_link_target_module",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("target_entity", sa.Text(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "link_confidence",
            postgresql.ENUM(
                "manual",
                "auto",
                "suggested",
                name="communications_link_confidence",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["email_message_id"],
            ["communications_email_message.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["telegram_message_id"],
            ["communications_telegram_message.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(email_message_id IS NOT NULL AND telegram_message_id IS NULL)"
            " OR (email_message_id IS NULL AND telegram_message_id IS NOT NULL)",
            name="ck_communications_link_source",
        ),
    )
    op.create_index(
        "ix_communications_link_email_message_id",
        "communications_link",
        ["email_message_id"],
        postgresql_where=sa.text("email_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_communications_link_telegram_message_id",
        "communications_link",
        ["telegram_message_id"],
        postgresql_where=sa.text("telegram_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_communications_link_target",
        "communications_link",
        ["target_module", "target_entity", "target_id"],
    )


def downgrade() -> None:
    op.drop_table("communications_link")
    op.drop_table("communications_telegram_message")
    op.drop_table("communications_telegram_chat")
    op.drop_table("communications_email_message")
    op.drop_table("communications_email_thread")
    op.drop_table("finance_tax_entry")
    op.drop_table("finance_expense")
    op.drop_table("finance_exchange_operation")
    op.drop_table("finance_exchange_rate")
    op.drop_table("finance_ledger_entry")
    op.drop_table("warehouse_reservation")
    op.drop_table("warehouse_stock_item")
    op.drop_table("warehouse_receipt_item")
    op.drop_table("warehouse_receipt")
    op.drop_table("procurement_tracking_event")
    op.drop_table("procurement_shipment")
    op.drop_table("procurement_purchase_item")
    op.drop_table("procurement_purchase")
    op.drop_table("orders_order_item")
    op.drop_table("pricing_price_calculation")
    op.drop_table("pricing_exchange_rate")
    op.drop_table("orders_order")
    op.drop_table("orders_customer_profile")
    op.drop_table("orders_customer")
    op.drop_table("catalog_product_attribute")
    op.drop_table("catalog_product")
    op.drop_table("catalog_supplier")

    op.execute("DROP TYPE IF EXISTS finance_exchange_rate_source")
    op.execute("DROP TYPE IF EXISTS finance_tax_type")
    op.execute("DROP TYPE IF EXISTS finance_expense_category")
    op.execute("DROP TYPE IF EXISTS finance_entry_type")
    op.execute("DROP TYPE IF EXISTS communications_link_confidence")
    op.execute("DROP TYPE IF EXISTS communications_link_target_module")
    op.execute("DROP TYPE IF EXISTS pricing_exchange_rate_source")
    op.execute("DROP TYPE IF EXISTS procurement_purchase_status")
    op.execute("DROP TYPE IF EXISTS orders_order_status")
    op.execute("DROP TYPE IF EXISTS catalog_attribute_source")
