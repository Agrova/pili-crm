"""adr004 pricing policy

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-04-19 00:01:00.000000

Changes (ADR-004):
- New enum: pricing_purchase_type (retail, manufacturer)
- pricing_price_calculation: purchase_type, pre_round_price, rounding_step,
    margin_percent, discount_percent, customer_id
- orders_order_item: operator_adjusted
- orders_order: delivery_cost, delivery_method, delivery_paid_by_customer,
    order_discount_percent
- catalog_supplier: default_purchase_type
- finance_expense_category: bank_commission, overhead, customs, intermediary
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2c3d4e5f6a1"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create new enum type
    op.execute(
        "CREATE TYPE pricing_purchase_type AS ENUM ('retail', 'manufacturer')"
    )

    # 2. Extend finance_expense_category with ADR-004 values
    op.execute(
        "ALTER TYPE finance_expense_category ADD VALUE IF NOT EXISTS 'bank_commission'"
    )
    op.execute(
        "ALTER TYPE finance_expense_category ADD VALUE IF NOT EXISTS 'overhead'"
    )
    op.execute(
        "ALTER TYPE finance_expense_category ADD VALUE IF NOT EXISTS 'customs'"
    )
    op.execute(
        "ALTER TYPE finance_expense_category ADD VALUE IF NOT EXISTS 'intermediary'"
    )

    # 3. pricing_price_calculation — new columns
    op.add_column(
        "pricing_price_calculation",
        sa.Column(
            "purchase_type",
            postgresql.ENUM(
                "retail",
                "manufacturer",
                name="pricing_purchase_type",
                create_type=False,
            ),
            nullable=False,
            server_default="retail",
        ),
    )
    # Remove server_default after backfill (it's only needed during ALTER)
    op.alter_column(
        "pricing_price_calculation", "purchase_type", server_default=None
    )

    op.add_column(
        "pricing_price_calculation",
        sa.Column(
            "pre_round_price",
            sa.Numeric(18, 4),
            nullable=False,
            server_default="0",
        ),
    )
    op.alter_column(
        "pricing_price_calculation", "pre_round_price", server_default=None
    )

    op.add_column(
        "pricing_price_calculation",
        sa.Column(
            "rounding_step",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
    )
    op.alter_column(
        "pricing_price_calculation", "rounding_step", server_default=None
    )

    op.add_column(
        "pricing_price_calculation",
        sa.Column(
            "margin_percent",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="20.00",
        ),
    )
    op.alter_column(
        "pricing_price_calculation", "margin_percent", server_default=None
    )

    op.add_column(
        "pricing_price_calculation",
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=True),
    )

    op.add_column(
        "pricing_price_calculation",
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pricing_price_calculation_customer_id",
        "pricing_price_calculation",
        "orders_customer",
        ["customer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_pricing_price_calculation_customer_id",
        "pricing_price_calculation",
        ["customer_id"],
        postgresql_where=sa.text("customer_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_pricing_price_calculation_pre_round_price",
        "pricing_price_calculation",
        "pre_round_price >= 0",
    )
    op.create_check_constraint(
        "ck_pricing_price_calculation_margin_percent",
        "pricing_price_calculation",
        "margin_percent >= 0",
    )
    op.create_check_constraint(
        "ck_pricing_price_calculation_discount_percent",
        "pricing_price_calculation",
        "discount_percent >= 0 OR discount_percent IS NULL",
    )

    # 4. orders_order_item — operator_adjusted
    op.add_column(
        "orders_order_item",
        sa.Column(
            "operator_adjusted",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    # 5. orders_order — delivery + discount fields
    op.add_column(
        "orders_order",
        sa.Column("delivery_cost", sa.Numeric(18, 4), nullable=True),
    )
    op.add_column(
        "orders_order",
        sa.Column("delivery_method", sa.Text(), nullable=True),
    )
    op.add_column(
        "orders_order",
        sa.Column(
            "delivery_paid_by_customer",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
    )
    op.add_column(
        "orders_order",
        sa.Column("order_discount_percent", sa.Numeric(5, 2), nullable=True),
    )

    # 6. catalog_supplier — default_purchase_type
    op.add_column(
        "catalog_supplier",
        sa.Column(
            "default_purchase_type",
            postgresql.ENUM(
                "retail",
                "manufacturer",
                name="pricing_purchase_type",
                create_type=False,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # 6. catalog_supplier
    op.drop_column("catalog_supplier", "default_purchase_type")

    # 5. orders_order
    op.drop_column("orders_order", "order_discount_percent")
    op.drop_column("orders_order", "delivery_paid_by_customer")
    op.drop_column("orders_order", "delivery_method")
    op.drop_column("orders_order", "delivery_cost")

    # 4. orders_order_item
    op.drop_column("orders_order_item", "operator_adjusted")

    # 3. pricing_price_calculation
    op.drop_index(
        "ix_pricing_price_calculation_customer_id",
        table_name="pricing_price_calculation",
    )
    op.drop_constraint(
        "fk_pricing_price_calculation_customer_id",
        "pricing_price_calculation",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_pricing_price_calculation_discount_percent",
        "pricing_price_calculation",
        type_="check",
    )
    op.drop_constraint(
        "ck_pricing_price_calculation_margin_percent",
        "pricing_price_calculation",
        type_="check",
    )
    op.drop_constraint(
        "ck_pricing_price_calculation_pre_round_price",
        "pricing_price_calculation",
        type_="check",
    )
    op.drop_column("pricing_price_calculation", "customer_id")
    op.drop_column("pricing_price_calculation", "discount_percent")
    op.drop_column("pricing_price_calculation", "margin_percent")
    op.drop_column("pricing_price_calculation", "rounding_step")
    op.drop_column("pricing_price_calculation", "pre_round_price")
    op.drop_column("pricing_price_calculation", "purchase_type")

    # 1. Drop new enum type
    op.execute("DROP TYPE IF EXISTS pricing_purchase_type")

    # 2. finance_expense_category values cannot be removed from PostgreSQL enums
    # without recreating the type. Skipped — values remain in the enum.
    # To fully downgrade, run manually:
    #   see docs/adr/ADR-004-pricing-profit-policy.md
