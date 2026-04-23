"""adr006_derive_status_trigger

Revision ID: 4f8fe83398af
Revises: 79d855cd5adf
Create Date: 2026-04-22 21:18:42.542047

Creates PL/pgSQL function derive_order_status and three triggers
AFTER UPDATE OF status / INSERT / DELETE on orders_order_item.
Performs initial sync to fix any inconsistencies from incident I-1.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "4f8fe83398af"
down_revision: str | None = "79d855cd5adf"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # 1. Derivation function
    op.execute(
        """
        CREATE OR REPLACE FUNCTION derive_order_status(p_order_id BIGINT)
        RETURNS orders_order_status
        LANGUAGE plpgsql
        STABLE
        AS $$
        DECLARE
            v_active_count INTEGER;
            v_min_item_status orders_order_item_status;
            v_result orders_order_status;
        BEGIN
            SELECT COUNT(*), MIN(status)
            INTO v_active_count, v_min_item_status
            FROM orders_order_item
            WHERE order_id = p_order_id
              AND status != 'cancelled';

            IF v_active_count = 0 THEN
                IF EXISTS (SELECT 1 FROM orders_order_item WHERE order_id = p_order_id) THEN
                    RETURN 'cancelled';
                ELSE
                    RETURN 'draft';
                END IF;
            END IF;

            v_result := CASE v_min_item_status
                WHEN 'pending'      THEN 'in_procurement'::orders_order_status
                WHEN 'ordered'      THEN 'in_procurement'::orders_order_status
                WHEN 'shipped'      THEN 'shipped_by_supplier'::orders_order_status
                WHEN 'at_forwarder' THEN 'received_by_forwarder'::orders_order_status
                WHEN 'arrived'      THEN 'arrived'::orders_order_status
                WHEN 'delivered'    THEN 'delivered'::orders_order_status
            END;

            IF v_result IS NULL THEN
                RAISE EXCEPTION 'derive_order_status: unexpected item status %', v_min_item_status;
            END IF;

            RETURN v_result;
        END;
        $$
        """
    )

    # 2. Trigger function
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trg_order_item_derive_status()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        DECLARE
            v_new_status orders_order_status;
            v_current_status orders_order_status;
            v_order_id BIGINT;
        BEGIN
            v_order_id := COALESCE(NEW.order_id, OLD.order_id);

            v_new_status := derive_order_status(v_order_id);

            SELECT status INTO v_current_status
            FROM orders_order
            WHERE id = v_order_id;

            IF v_current_status IS DISTINCT FROM v_new_status THEN
                UPDATE orders_order
                SET status = v_new_status, updated_at = now()
                WHERE id = v_order_id;
            END IF;

            RETURN NULL;
        END;
        $$
        """
    )

    # 3. Triggers
    op.execute(
        """
        CREATE TRIGGER orders_order_item_derive_status_on_update
        AFTER UPDATE OF status ON orders_order_item
        FOR EACH ROW
        EXECUTE FUNCTION trg_order_item_derive_status()
        """
    )
    op.execute(
        """
        CREATE TRIGGER orders_order_item_derive_status_on_insert
        AFTER INSERT ON orders_order_item
        FOR EACH ROW
        EXECUTE FUNCTION trg_order_item_derive_status()
        """
    )
    op.execute(
        """
        CREATE TRIGGER orders_order_item_derive_status_on_delete
        AFTER DELETE ON orders_order_item
        FOR EACH ROW
        EXECUTE FUNCTION trg_order_item_derive_status()
        """
    )

    # 4. Initial sync — fix any inconsistencies from incident I-1
    op.execute(
        """
        UPDATE orders_order o
        SET status = derive_order_status(o.id),
            updated_at = now()
        WHERE EXISTS (SELECT 1 FROM orders_order_item WHERE order_id = o.id)
          AND o.status IS DISTINCT FROM derive_order_status(o.id)
        """
    )


def downgrade() -> None:
    # NOTE: order status values are NOT reverted to pre-upgrade state.
    # After downgrade, existing orders keep their correct (derived) status,
    # but new item status changes will no longer propagate automatically.
    op.execute(
        "DROP TRIGGER IF EXISTS orders_order_item_derive_status_on_delete ON orders_order_item"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS orders_order_item_derive_status_on_insert ON orders_order_item"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS orders_order_item_derive_status_on_update ON orders_order_item"
    )
    op.execute("DROP FUNCTION IF EXISTS trg_order_item_derive_status()")
    op.execute("DROP FUNCTION IF EXISTS derive_order_status(BIGINT)")
