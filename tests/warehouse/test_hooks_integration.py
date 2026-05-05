"""Integration tests: receive_stock wires hooks correctly (ADR-007/008 Блок 3).

Tests verify:
  1. receive_stock calls on_warehouse_receipt_item_created.
  2. When all purchase items received, receive_stock calls on_purchase_delivered
     in the same transaction.
  3. A hook exception rolls back the whole transaction (receipt_item not saved).

Note on session isolation: tests 1 & 2 patch session.commit → session.flush so
the db_session fixture's rollback undoes all changes. Test 3 relies on the
internal rollback inside receive_stock.run() when the hook raises.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

_MCP_ROOT = Path(__file__).resolve().parents[2] / "crm-mcp"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from tools import receive_stock  # noqa: E402

from app.procurement.models import (  # noqa: E402
    ProcurementPurchase,
    ProcurementPurchaseItem,
    ProcurementPurchaseStatus,
)
from app.warehouse.models import WarehouseReceiptItem  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_ids(session: AsyncSession) -> tuple[int, int]:
    row = (
        await session.execute(
            text(
                """
                SELECT p.id AS product_id, cpl.source_id AS supplier_id
                FROM catalog_product p
                JOIN catalog_product_listing cpl ON cpl.product_id = p.id
                LIMIT 1
                """
            )
        )
    ).one()
    return int(row.product_id), int(row.supplier_id)


async def _make_purchase_with_one_item(
    session: AsyncSession,
    product_id: int,
    supplier_id: int,
    *,
    unit_cost: Decimal = Decimal("500.00"),
) -> ProcurementPurchase:
    purchase = ProcurementPurchase(
        supplier_id=supplier_id,
        status=ProcurementPurchaseStatus.shipped,
        currency="RUB",
    )
    session.add(purchase)
    await session.flush()

    item = ProcurementPurchaseItem(
        purchase_id=purchase.id,
        product_id=product_id,
        quantity=Decimal("3"),
        unit_cost=unit_cost,
    )
    session.add(item)
    await session.flush()
    return purchase


# ---------------------------------------------------------------------------
# Test 1: receive_stock calls on_warehouse_receipt_item_created
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_receive_stock_calls_receipt_hook(db_session: AsyncSession) -> None:
    """receive_stock must invoke on_warehouse_receipt_item_created in the same tx."""
    product_id, supplier_id = await _get_ids(db_session)
    purchase = await _make_purchase_with_one_item(db_session, product_id, supplier_id)

    # Prevent actual commit so db_session fixture's rollback cleans up.
    db_session.commit = db_session.flush  # type: ignore[method-assign]

    hook_path = "tools.receive_stock.on_warehouse_receipt_item_created"
    with patch(hook_path, new_callable=AsyncMock) as mock_hook:
        result = await receive_stock.run(
            db_session,
            purchase_id=purchase.id,
            product_id=product_id,
            quantity=2.0,
        )

    assert result["status"] == "ok", result
    assert mock_hook.called, "on_warehouse_receipt_item_created was not called"
    call_args = mock_hook.call_args
    # First positional arg is the receipt_item_id (int), second is session.
    called_ri_id = call_args.args[0]
    assert isinstance(called_ri_id, int)
    assert called_ri_id == result["receipt_item_id"]


# ---------------------------------------------------------------------------
# Test 2: when all items received, on_purchase_delivered is called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_delivered_calls_on_purchase_delivered(db_session: AsyncSession) -> None:
    """After the only purchase item is received, receive_stock must call on_purchase_delivered."""
    product_id, supplier_id = await _get_ids(db_session)
    purchase = await _make_purchase_with_one_item(db_session, product_id, supplier_id)

    db_session.commit = db_session.flush  # type: ignore[method-assign]

    hook_ri_path = "tools.receive_stock.on_warehouse_receipt_item_created"
    hook_pd_path = "tools.receive_stock.on_purchase_delivered"
    with (
        patch(hook_ri_path, new_callable=AsyncMock),
        patch(hook_pd_path, new_callable=AsyncMock) as mock_delivered,
    ):
        result = await receive_stock.run(
            db_session,
            purchase_id=purchase.id,
            product_id=product_id,
            quantity=3.0,
        )

    assert result["status"] == "ok", result
    assert result["purchase_status"] == "delivered"
    assert mock_delivered.called, "on_purchase_delivered was not called"
    # Called with (purchase_id, session).
    assert mock_delivered.call_args.args[0] == purchase.id


# ---------------------------------------------------------------------------
# Test 3: hook exception rolls back the whole transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hook_failure_rolls_back_transaction(db_session: AsyncSession) -> None:
    """If on_warehouse_receipt_item_created raises, receive_stock must roll back
    the entire transaction — no WarehouseReceiptItem must be saved to the DB."""
    product_id, supplier_id = await _get_ids(db_session)
    purchase = await _make_purchase_with_one_item(db_session, product_id, supplier_id)

    # Count existing receipt items for this product before the call.
    count_before = (
        await db_session.execute(
            select(func.count(WarehouseReceiptItem.id)).where(
                WarehouseReceiptItem.product_id == product_id
            )
        )
    ).scalar_one()

    hook_path = "tools.receive_stock.on_warehouse_receipt_item_created"
    with (
        patch(hook_path, side_effect=RuntimeError("simulated hook failure")),
        pytest.raises(RuntimeError, match="simulated hook failure"),
    ):
        await receive_stock.run(
            db_session,
            purchase_id=purchase.id,
            product_id=product_id,
            quantity=2.0,
        )

    # After rollback, no new receipt items should exist.
    count_after = (
        await db_session.execute(
            select(func.count(WarehouseReceiptItem.id)).where(
                WarehouseReceiptItem.product_id == product_id
            )
        )
    ).scalar_one()
    assert count_after == count_before, (
        f"Transaction was not rolled back: expected {count_before} receipt items, "
        f"got {count_after}"
    )
