"""order item status

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-04-20 00:00:00.000000

Adds per-item status needed for shipment matching (pending vs delivered).
Values mirror Excel source: pending_order, ordered, ordered_at_supplier,
received, delivered_to_customer.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a1b2"
down_revision: str | None = "b2c3d4e5f6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ITEM_STATUS_ENUM = "orders_order_item_status"
ITEM_STATUSES = (
    "pending_order",
    "ordered",
    "ordered_at_supplier",
    "received",
    "delivered_to_customer",
)


def upgrade() -> None:
    op.execute(
        f"CREATE TYPE {ITEM_STATUS_ENUM} AS ENUM ("
        + ", ".join(f"'{s}'" for s in ITEM_STATUSES)
        + ")"
    )
    op.add_column(
        "orders_order_item",
        sa.Column(
            "status",
            sa.Enum(*ITEM_STATUSES, name=ITEM_STATUS_ENUM, create_type=False),
            nullable=False,
            server_default="pending_order",
        ),
    )
    op.create_index(
        "ix_orders_order_item_status", "orders_order_item", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_orders_order_item_status", table_name="orders_order_item")
    op.drop_column("orders_order_item", "status")
    op.execute(f"DROP TYPE {ITEM_STATUS_ENUM}")
