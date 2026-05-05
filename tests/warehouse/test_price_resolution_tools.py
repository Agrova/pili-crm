"""Tests for list_pending_price_resolutions and resolve_price_resolution MCP tools.

ADR-008 Package 3 — Блок 1.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

# crm-mcp tools are in a separate package root — add it to sys.path.
_MCP_ROOT = Path(__file__).resolve().parents[2] / "crm-mcp"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from tools import list_pending_price_resolutions, resolve_price_resolution  # noqa: E402

from app.pricing.models import PricingPriceCalculation  # noqa: E402
from app.procurement.models import (  # noqa: E402
    ProcurementPurchase,
    ProcurementPurchaseItem,
    ProcurementPurchaseStatus,
    ProcurementShipment,
)
from app.warehouse.models import (  # noqa: E402
    WarehousePendingPriceResolution,
    WarehouseReceipt,
    WarehouseReceiptItem,
    WarehouseStockItem,
)
from app.warehouse.services import (  # noqa: E402
    DEFAULT_STOCK_LOCATION,
    on_warehouse_receipt_item_created,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)


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


async def _make_purchase_chain(
    session: AsyncSession,
    product_id: int,
    supplier_id: int,
    *,
    unit_cost: Decimal,
    currency: str = "RUB",
) -> tuple[ProcurementPurchase, ProcurementShipment]:
    purchase = ProcurementPurchase(
        supplier_id=supplier_id,
        status=ProcurementPurchaseStatus.delivered,
        currency=currency,
    )
    session.add(purchase)
    await session.flush()

    item = ProcurementPurchaseItem(
        purchase_id=purchase.id,
        product_id=product_id,
        quantity=Decimal("5"),
        unit_cost=unit_cost,
    )
    session.add(item)

    shipment = ProcurementShipment(purchase_id=purchase.id)
    session.add(shipment)
    await session.flush()
    return purchase, shipment


async def _make_receipt_item(
    session: AsyncSession,
    shipment: ProcurementShipment,
    product_id: int,
    quantity: Decimal = Decimal("3"),
) -> WarehouseReceiptItem:
    receipt = WarehouseReceipt(shipment_id=shipment.id, received_at=_NOW)
    session.add(receipt)
    await session.flush()

    ri = WarehouseReceiptItem(
        receipt_id=receipt.id,
        product_id=product_id,
        quantity=quantity,
    )
    session.add(ri)
    await session.flush()
    return ri


async def _setup_conflict(
    session: AsyncSession,
    *,
    existing_unit_cost: Decimal = Decimal("500.00"),
    existing_qty: Decimal = Decimal("3"),
    new_unit_cost: Decimal = Decimal("9999.00"),
    new_qty: Decimal = Decimal("2"),
) -> tuple[int, int]:
    """Create a price conflict and return (product_id, receipt_item_id_of_conflict)."""
    product_id, supplier_id = await _get_ids(session)

    # First receipt — creates stock.
    purchase1, shipment1 = await _make_purchase_chain(
        session, product_id, supplier_id, unit_cost=existing_unit_cost
    )
    ri1 = await _make_receipt_item(session, shipment1, product_id, existing_qty)
    await on_warehouse_receipt_item_created(ri1.id, session)
    await session.flush()

    # Second receipt with very different price — creates pending.
    purchase2, shipment2 = await _make_purchase_chain(
        session, product_id, supplier_id, unit_cost=new_unit_cost
    )
    ri2 = await _make_receipt_item(session, shipment2, product_id, new_qty)
    await on_warehouse_receipt_item_created(ri2.id, session)
    await session.flush()

    return product_id, ri2.id


# ---------------------------------------------------------------------------
# list_pending_price_resolutions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty_when_no_conflicts(db_session: AsyncSession) -> None:
    result = await list_pending_price_resolutions.run(db_session)
    assert result["total"] == 0
    assert result["conflicts"] == []


@pytest.mark.asyncio
async def test_list_returns_correct_scenarios(db_session: AsyncSession) -> None:
    product_id, conflict_ri_id = await _setup_conflict(db_session)

    result = await list_pending_price_resolutions.run(db_session)

    assert result["total"] >= 1
    conflict = next(
        c for c in result["conflicts"] if c["receipt_item_id"] == conflict_ri_id
    )

    assert conflict["product"]["id"] == product_id
    sc = conflict["scenarios"]

    # All three scenarios must be present.
    for key in ("keep_old", "use_new", "weighted_average"):
        s = sc[key]
        assert s["total_quantity"] == pytest.approx(5.0)  # 3 + 2
        assert s["final_unit_price"] > 0
        assert s["total_revenue"] == pytest.approx(s["final_unit_price"] * 5, rel=0.01)
        assert s["total_cost"] > 0
        assert s["profit_rub"] == pytest.approx(
            s["total_revenue"] - s["total_cost"], abs=1
        )

    # keep_old price equals existing stock price.
    assert sc["keep_old"]["final_unit_price"] == pytest.approx(
        conflict["existing_stock"]["unit_price_rub"]
    )
    # use_new price equals new receipt price.
    assert sc["use_new"]["final_unit_price"] == pytest.approx(
        conflict["new_receipt"]["unit_price_rub"]
    )
    # weighted_average price is between the two.
    assert (
        sc["keep_old"]["final_unit_price"]
        <= sc["weighted_average"]["final_unit_price"]
        <= sc["use_new"]["final_unit_price"]
    )


# ---------------------------------------------------------------------------
# resolve_price_resolution — keep_old
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_keep_old(db_session: AsyncSession) -> None:
    product_id, conflict_ri_id = await _setup_conflict(
        db_session,
        existing_qty=Decimal("3"),
        new_qty=Decimal("2"),
    )

    stock_before = (
        await db_session.execute(
            select(WarehouseStockItem).where(
                WarehouseStockItem.product_id == product_id,
                WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
            )
        )
    ).scalar_one()
    old_calc_id = stock_before.price_calculation_id
    qty_before = stock_before.quantity

    result = await resolve_price_resolution.run(
        db_session, receipt_item_id=conflict_ri_id, choice="keep_old"
    )
    await db_session.flush()

    assert result["ok"] is True

    # Reload stock.
    await db_session.refresh(stock_before)
    assert stock_before.quantity == qty_before + Decimal("2")
    assert stock_before.price_calculation_id == old_calc_id  # unchanged
    assert stock_before.receipt_item_id == conflict_ri_id

    # Pending deleted.
    pending = (
        await db_session.execute(
            select(WarehousePendingPriceResolution).where(
                WarehousePendingPriceResolution.receipt_item_id == conflict_ri_id
            )
        )
    ).scalar_one_or_none()
    assert pending is None


# ---------------------------------------------------------------------------
# resolve_price_resolution — use_new
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_use_new(db_session: AsyncSession) -> None:
    product_id, conflict_ri_id = await _setup_conflict(db_session)

    stock_before = (
        await db_session.execute(
            select(WarehouseStockItem).where(
                WarehouseStockItem.product_id == product_id,
                WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
            )
        )
    ).scalar_one()
    old_calc_id = stock_before.price_calculation_id

    # Load the pending to know what new_price_calculation_id is.
    pending = (
        await db_session.execute(
            select(WarehousePendingPriceResolution).where(
                WarehousePendingPriceResolution.receipt_item_id == conflict_ri_id
            )
        )
    ).scalar_one()
    expected_new_calc_id = pending.new_price_calculation_id

    result = await resolve_price_resolution.run(
        db_session, receipt_item_id=conflict_ri_id, choice="use_new"
    )
    await db_session.flush()

    assert result["ok"] is True

    await db_session.refresh(stock_before)
    assert stock_before.price_calculation_id == expected_new_calc_id
    assert stock_before.price_calculation_id != old_calc_id

    # Pending deleted.
    gone = (
        await db_session.execute(
            select(WarehousePendingPriceResolution).where(
                WarehousePendingPriceResolution.receipt_item_id == conflict_ri_id
            )
        )
    ).scalar_one_or_none()
    assert gone is None


# ---------------------------------------------------------------------------
# resolve_price_resolution — weighted_average
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_weighted_average(db_session: AsyncSession) -> None:
    product_id, conflict_ri_id = await _setup_conflict(db_session)

    stock_before = (
        await db_session.execute(
            select(WarehouseStockItem).where(
                WarehouseStockItem.product_id == product_id,
                WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
            )
        )
    ).scalar_one()
    old_calc_id = stock_before.price_calculation_id

    result = await resolve_price_resolution.run(
        db_session, receipt_item_id=conflict_ri_id, choice="weighted_average"
    )
    await db_session.flush()

    assert result["ok"] is True

    await db_session.refresh(stock_before)
    new_calc_id = stock_before.price_calculation_id
    assert new_calc_id != old_calc_id

    # Verify new PricingPriceCalculation has the correct formula_version.
    new_calc = (
        await db_session.execute(
            select(PricingPriceCalculation).where(
                PricingPriceCalculation.id == new_calc_id
            )
        )
    ).scalar_one()
    assert new_calc.formula_version == "adr-008-weighted-v1"
    assert new_calc.breakdown["method"] == "weighted_average"
    assert "weighted_price" in new_calc.breakdown

    # Pending deleted.
    gone = (
        await db_session.execute(
            select(WarehousePendingPriceResolution).where(
                WarehousePendingPriceResolution.receipt_item_id == conflict_ri_id
            )
        )
    ).scalar_one_or_none()
    assert gone is None


# ---------------------------------------------------------------------------
# resolve_price_resolution — error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_not_found(db_session: AsyncSession) -> None:
    result = await resolve_price_resolution.run(
        db_session, receipt_item_id=999_999_999, choice="keep_old"
    )
    assert result["error"] == "not_found"
    assert result["receipt_item_id"] == 999_999_999


@pytest.mark.asyncio
async def test_resolve_invalid_choice(db_session: AsyncSession) -> None:
    result = await resolve_price_resolution.run(
        db_session, receipt_item_id=1, choice="bad_value"
    )
    assert result["error"] == "invalid_choice"
    assert set(result["valid"]) == {"keep_old", "use_new", "weighted_average"}
