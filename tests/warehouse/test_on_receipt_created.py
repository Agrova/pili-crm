"""Tests for warehouse.services.on_warehouse_receipt_item_created (ADR-007/008 Package 2b)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogListingPrice
from app.pricing.models import PricingExchangeRate, PricingPriceCalculation
from app.procurement.models import (
    ProcurementPurchase,
    ProcurementPurchaseItem,
    ProcurementPurchaseStatus,
    ProcurementShipment,
)
from app.warehouse.models import (
    WarehousePendingPriceResolution,
    WarehouseReceipt,
    WarehouseReceiptItem,
    WarehouseStockItem,
)
from app.warehouse.services import DEFAULT_STOCK_LOCATION, on_warehouse_receipt_item_created

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)


async def _get_ids(session: AsyncSession) -> tuple[int, int]:
    """Return (product_id, supplier_id) from seed data."""
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


async def _make_rub_exchange_rate(session: AsyncSession, *, currency: str = "USD") -> PricingExchangeRate:
    """Insert a USD→RUB exchange rate so the hook can calculate prices."""
    rate = PricingExchangeRate(
        from_currency=currency,
        to_currency="RUB",
        rate=Decimal("90.0000"),
        valid_from=_NOW,
        source="manual",  # type: ignore[arg-type]
    )
    session.add(rate)
    await session.flush()
    return rate


async def _make_purchase_chain(
    session: AsyncSession,
    product_id: int,
    supplier_id: int,
    *,
    unit_cost: Decimal = Decimal("500.00"),
    currency: str = "RUB",
) -> tuple[ProcurementPurchase, ProcurementShipment, ProcurementReceipt_]:
    """Create purchase → purchase_item → shipment chain, return all three."""
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

    shipment = ProcurementShipment(
        purchase_id=purchase.id,
    )
    session.add(shipment)
    await session.flush()
    return purchase, shipment


async def _make_receipt_item(
    session: AsyncSession,
    shipment: ProcurementShipment,
    product_id: int,
    quantity: Decimal = Decimal("3"),
) -> WarehouseReceiptItem:
    receipt = WarehouseReceipt(
        shipment_id=shipment.id,
        received_at=_NOW,
    )
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


# Alias for type annotation only
class ProcurementReceipt_:
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_receipt_creates_stock_item(db_session: AsyncSession) -> None:
    product_id, supplier_id = await _get_ids(db_session)
    purchase, shipment = await _make_purchase_chain(
        db_session, product_id, supplier_id
    )
    ri = await _make_receipt_item(db_session, shipment, product_id)

    await on_warehouse_receipt_item_created(ri.id, db_session)
    await db_session.flush()

    stock = (
        await db_session.execute(
            select(WarehouseStockItem).where(
                WarehouseStockItem.product_id == product_id,
                WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
            )
        )
    ).scalar_one_or_none()

    assert stock is not None
    assert stock.quantity == ri.quantity
    assert stock.price_calculation_id is not None

    # No pending created.
    pending = (
        await db_session.execute(
            select(WarehousePendingPriceResolution).where(
                WarehousePendingPriceResolution.receipt_item_id == ri.id
            )
        )
    ).scalar_one_or_none()
    assert pending is None


@pytest.mark.asyncio
async def test_second_receipt_same_price_merges_quantity(db_session: AsyncSession) -> None:
    product_id, supplier_id = await _get_ids(db_session)
    purchase, shipment = await _make_purchase_chain(
        db_session, product_id, supplier_id, unit_cost=Decimal("500.00")
    )
    ri1 = await _make_receipt_item(db_session, shipment, product_id, Decimal("2"))
    await on_warehouse_receipt_item_created(ri1.id, db_session)
    await db_session.flush()

    # Second receipt with identical cost.
    purchase2, shipment2 = await _make_purchase_chain(
        db_session, product_id, supplier_id, unit_cost=Decimal("500.00")
    )
    ri2 = await _make_receipt_item(db_session, shipment2, product_id, Decimal("3"))
    await on_warehouse_receipt_item_created(ri2.id, db_session)
    await db_session.flush()

    stock = (
        await db_session.execute(
            select(WarehouseStockItem).where(
                WarehouseStockItem.product_id == product_id,
                WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
            )
        )
    ).scalar_one()
    # Quantities merged.
    assert stock.quantity == Decimal("5")

    # No pending resolution.
    pending_rows = (
        await db_session.execute(
            select(WarehousePendingPriceResolution).where(
                WarehousePendingPriceResolution.receipt_item_id.in_([ri1.id, ri2.id])
            )
        )
    ).scalars().all()
    assert pending_rows == []


@pytest.mark.asyncio
async def test_conflicting_price_creates_pending(db_session: AsyncSession) -> None:
    product_id, supplier_id = await _get_ids(db_session)

    # First receipt at 500 RUB.
    purchase, shipment = await _make_purchase_chain(
        db_session, product_id, supplier_id, unit_cost=Decimal("500.00")
    )
    ri1 = await _make_receipt_item(db_session, shipment, product_id, Decimal("2"))
    await on_warehouse_receipt_item_created(ri1.id, db_session)
    await db_session.flush()

    original_qty = (
        await db_session.execute(
            select(WarehouseStockItem.quantity).where(
                WarehouseStockItem.product_id == product_id,
                WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
            )
        )
    ).scalar_one()

    # Second receipt at 9999 RUB — definitely different by more than rounding step.
    purchase2, shipment2 = await _make_purchase_chain(
        db_session, product_id, supplier_id, unit_cost=Decimal("9999.00")
    )
    ri2 = await _make_receipt_item(db_session, shipment2, product_id, Decimal("1"))
    await on_warehouse_receipt_item_created(ri2.id, db_session)
    await db_session.flush()

    # Stock quantity unchanged.
    current_qty = (
        await db_session.execute(
            select(WarehouseStockItem.quantity).where(
                WarehouseStockItem.product_id == product_id,
                WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
            )
        )
    ).scalar_one()
    assert current_qty == original_qty

    # Pending created.
    pending = (
        await db_session.execute(
            select(WarehousePendingPriceResolution).where(
                WarehousePendingPriceResolution.receipt_item_id == ri2.id
            )
        )
    ).scalar_one()
    assert pending.receipt_item_id == ri2.id
    assert pending.new_price_calculation_id is not None


@pytest.mark.asyncio
async def test_listing_price_always_recorded(db_session: AsyncSession) -> None:
    product_id, supplier_id = await _get_ids(db_session)
    purchase, shipment = await _make_purchase_chain(
        db_session, product_id, supplier_id, unit_cost=Decimal("300.00")
    )
    ri = await _make_receipt_item(db_session, shipment, product_id)

    await on_warehouse_receipt_item_created(ri.id, db_session)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(CatalogListingPrice).where(
                CatalogListingPrice.source_ref == str(purchase.id)
            )
        )
    ).scalars().all()
    assert len(rows) >= 1


@pytest.mark.asyncio
async def test_missing_purchase_item_skips_gracefully(db_session: AsyncSession) -> None:
    product_id, supplier_id = await _get_ids(db_session)

    # Create a purchase with NO purchase_item for this product.
    purchase = ProcurementPurchase(
        supplier_id=supplier_id,
        status=ProcurementPurchaseStatus.delivered,
        currency="RUB",
    )
    db_session.add(purchase)
    await db_session.flush()

    shipment = ProcurementShipment(purchase_id=purchase.id)
    db_session.add(shipment)
    await db_session.flush()

    # Get a different product to put in the receipt item.
    other_product_id = (
        await db_session.execute(
            text("SELECT id FROM catalog_product WHERE id != :pid LIMIT 1"),
            {"pid": product_id},
        )
    ).scalar_one()

    ri = await _make_receipt_item(db_session, shipment, other_product_id)

    # Should not raise, should log WARNING and return.
    await on_warehouse_receipt_item_created(ri.id, db_session)
    await db_session.flush()

    # No stock created.
    stock = (
        await db_session.execute(
            select(WarehouseStockItem).where(
                WarehouseStockItem.product_id == other_product_id,
                WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
            )
        )
    ).scalar_one_or_none()
    assert stock is None


@pytest.mark.asyncio
async def test_cascade_delete_receipt_item_removes_pending(db_session: AsyncSession) -> None:
    product_id, supplier_id = await _get_ids(db_session)

    # First receipt.
    purchase, shipment = await _make_purchase_chain(
        db_session, product_id, supplier_id, unit_cost=Decimal("500.00")
    )
    ri1 = await _make_receipt_item(db_session, shipment, product_id, Decimal("1"))
    await on_warehouse_receipt_item_created(ri1.id, db_session)
    await db_session.flush()

    # Second receipt with very different price → pending.
    purchase2, shipment2 = await _make_purchase_chain(
        db_session, product_id, supplier_id, unit_cost=Decimal("9999.00")
    )
    ri2 = await _make_receipt_item(db_session, shipment2, product_id, Decimal("1"))
    await on_warehouse_receipt_item_created(ri2.id, db_session)
    await db_session.flush()

    # Verify pending exists.
    pending_id = (
        await db_session.execute(
            select(WarehousePendingPriceResolution.id).where(
                WarehousePendingPriceResolution.receipt_item_id == ri2.id
            )
        )
    ).scalar_one()
    assert pending_id is not None

    # Delete receipt_item → pending should CASCADE.
    await db_session.execute(
        text("DELETE FROM warehouse_receipt_item WHERE id = :id"),
        {"id": ri2.id},
    )
    await db_session.flush()

    gone = (
        await db_session.execute(
            select(WarehousePendingPriceResolution).where(
                WarehousePendingPriceResolution.id == pending_id
            )
        )
    ).scalar_one_or_none()
    assert gone is None


@pytest.mark.asyncio
async def test_unique_pending_per_receipt_item(db_session: AsyncSession) -> None:
    """Inserting two pending records for the same receipt_item_id → IntegrityError."""
    product_id, supplier_id = await _get_ids(db_session)

    purchase, shipment = await _make_purchase_chain(
        db_session, product_id, supplier_id, unit_cost=Decimal("500.00")
    )
    ri = await _make_receipt_item(db_session, shipment, product_id)
    await on_warehouse_receipt_item_created(ri.id, db_session)
    await db_session.flush()

    stock = (
        await db_session.execute(
            select(WarehouseStockItem).where(
                WarehouseStockItem.product_id == product_id,
                WarehouseStockItem.location == DEFAULT_STOCK_LOCATION,
            )
        )
    ).scalar_one()

    calc = (
        await db_session.execute(
            select(PricingPriceCalculation).limit(1)
        )
    ).scalar_one()

    # First pending OK.
    p1 = WarehousePendingPriceResolution(
        receipt_item_id=ri.id,
        existing_stock_item_id=stock.id,
        new_price_calculation_id=calc.id,
    )
    db_session.add(p1)
    await db_session.flush()

    # Second pending with same receipt_item_id → UNIQUE violation.
    p2 = WarehousePendingPriceResolution(
        receipt_item_id=ri.id,
        existing_stock_item_id=stock.id,
        new_price_calculation_id=calc.id,
    )
    db_session.add(p2)
    with pytest.raises(IntegrityError):
        await db_session.flush()
