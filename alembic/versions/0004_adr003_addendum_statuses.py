"""ADR-003 addendum: replace order and item status enums

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-04-20 00:00:00.000000

Replaces orders_order_status (removes in_transit, ready_for_pickup;
adds shipped_by_supplier, received_by_forwarder) and replaces
orders_order_item_status (5 old values → 7 new values).
Remaps existing rows and derives order statuses from item statuses.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e5f6a1b2c3"
down_revision: str | None = "c3d4e5f6a1b2"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # ── Item status enum replacement ────────────────────────────────────────
    # Old values: pending_order, ordered, ordered_at_supplier, received,
    #             delivered_to_customer
    # New values: pending, ordered, shipped, at_forwarder, arrived,
    #             delivered, cancelled

    op.drop_index("ix_orders_order_item_status", table_name="orders_order_item")

    op.execute(
        "ALTER TABLE orders_order_item ALTER COLUMN status DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE orders_order_item ALTER COLUMN status TYPE TEXT"
    )

    op.execute(
        "UPDATE orders_order_item SET status = 'pending'"
        " WHERE status = 'pending_order'"
    )
    # 'ordered' already maps to 'ordered' — no update needed
    op.execute(
        "UPDATE orders_order_item SET status = 'ordered'"
        " WHERE status = 'ordered_at_supplier'"
    )
    op.execute(
        "UPDATE orders_order_item SET status = 'arrived'"
        " WHERE status = 'received'"
    )
    op.execute(
        "UPDATE orders_order_item SET status = 'delivered'"
        " WHERE status = 'delivered_to_customer'"
    )

    op.execute("DROP TYPE orders_order_item_status")
    op.execute(
        "CREATE TYPE orders_order_item_status AS ENUM ("
        "'pending', 'ordered', 'shipped', 'at_forwarder',"
        " 'arrived', 'delivered', 'cancelled')"
    )
    op.execute(
        "ALTER TABLE orders_order_item"
        " ALTER COLUMN status TYPE orders_order_item_status"
        " USING status::orders_order_item_status"
    )
    op.execute(
        "ALTER TABLE orders_order_item"
        " ALTER COLUMN status SET DEFAULT 'pending'"
    )

    op.create_index(
        "ix_orders_order_item_status", "orders_order_item", ["status"]
    )

    # ── Order status enum replacement ───────────────────────────────────────
    # Old values: draft, confirmed, in_procurement, in_transit, arrived,
    #             ready_for_pickup, delivered, cancelled
    # New values: draft, confirmed, in_procurement, shipped_by_supplier,
    #             received_by_forwarder, arrived, delivered, cancelled

    op.execute(
        "ALTER TABLE orders_order ALTER COLUMN status TYPE TEXT"
    )

    op.execute(
        "UPDATE orders_order SET status = 'shipped_by_supplier'"
        " WHERE status = 'in_transit'"
    )
    op.execute(
        "UPDATE orders_order SET status = 'arrived'"
        " WHERE status = 'ready_for_pickup'"
    )

    op.execute("DROP TYPE orders_order_status")
    op.execute(
        "CREATE TYPE orders_order_status AS ENUM ("
        "'draft', 'confirmed', 'in_procurement', 'shipped_by_supplier',"
        " 'received_by_forwarder', 'arrived', 'delivered', 'cancelled')"
    )
    op.execute(
        "ALTER TABLE orders_order"
        " ALTER COLUMN status TYPE orders_order_status"
        " USING status::orders_order_status"
    )

    # ── Derive order statuses from item statuses ────────────────────────────
    # For each order that has at least one non-cancelled item, compute the
    # minimum item weight and map it to the corresponding order status.
    op.execute(
        """
        UPDATE orders_order o
        SET status = (
            SELECT CASE MIN(
                CASE i.status
                    WHEN 'pending'      THEN 0
                    WHEN 'ordered'      THEN 1
                    WHEN 'shipped'      THEN 2
                    WHEN 'at_forwarder' THEN 3
                    WHEN 'arrived'      THEN 4
                    WHEN 'delivered'    THEN 5
                    ELSE 99
                END
            )
                WHEN 0 THEN 'in_procurement'
                WHEN 1 THEN 'in_procurement'
                WHEN 2 THEN 'shipped_by_supplier'
                WHEN 3 THEN 'received_by_forwarder'
                WHEN 4 THEN 'arrived'
                WHEN 5 THEN 'delivered'
            END::orders_order_status
            FROM orders_order_item i
            WHERE i.order_id = o.id
              AND i.status::text != 'cancelled'
        )
        WHERE EXISTS (
            SELECT 1 FROM orders_order_item i
            WHERE i.order_id = o.id
              AND i.status::text != 'cancelled'
        )
        """
    )

    # Orders where ALL items are cancelled → order becomes cancelled
    op.execute(
        """
        UPDATE orders_order o
        SET status = 'cancelled'
        WHERE NOT EXISTS (
            SELECT 1 FROM orders_order_item i
            WHERE i.order_id = o.id
              AND i.status::text != 'cancelled'
        )
        AND EXISTS (
            SELECT 1 FROM orders_order_item i2
            WHERE i2.order_id = o.id
        )
        """
    )


def downgrade() -> None:
    raise NotImplementedError(
        "Downgrade of ADR-003 addendum status migration is not supported."
    )
