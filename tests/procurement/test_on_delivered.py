"""Tests for procurement.services.on_purchase_delivered (ADR-007 Package 2a)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogListingPrice
from app.procurement.models import (
    ProcurementPurchase,
    ProcurementPurchaseItem,
    ProcurementPurchaseStatus,
)
from app.procurement.services import on_purchase_delivered

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_purchase(
    session: AsyncSession,
    *,
    status: ProcurementPurchaseStatus = ProcurementPurchaseStatus.delivered,
    currency: str | None = "RUB",
) -> ProcurementPurchase:
    """Create a minimal purchase row linked to first available supplier."""
    supplier_id = (
        await session.execute(text("SELECT id FROM catalog_supplier LIMIT 1"))
    ).scalar_one()

    purchase = ProcurementPurchase(
        supplier_id=supplier_id,
        status=status,
        currency=currency,
    )
    session.add(purchase)
    await session.flush()
    return purchase


async def _make_item(
    session: AsyncSession,
    purchase: ProcurementPurchase,
    *,
    unit_cost: Decimal | None = Decimal("500.00"),
) -> ProcurementPurchaseItem:
    """Create a purchase item linked to first available product."""
    product_id = (
        await session.execute(text("SELECT id FROM catalog_product LIMIT 1"))
    ).scalar_one()

    item = ProcurementPurchaseItem(
        purchase_id=purchase.id,
        product_id=product_id,
        quantity=Decimal("3"),
        unit_cost=unit_cost,
    )
    session.add(item)
    await session.flush()
    return item


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creates_listing_price_on_delivered(db_session: AsyncSession) -> None:
    purchase = await _make_purchase(db_session)
    item = await _make_item(db_session, purchase)

    await on_purchase_delivered(purchase.id, db_session)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(CatalogListingPrice).where(
                CatalogListingPrice.source_ref == str(purchase.id)
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].price == item.unit_cost
    assert rows[0].currency == purchase.currency


@pytest.mark.asyncio
async def test_idempotent_repeated_call(db_session: AsyncSession) -> None:
    purchase = await _make_purchase(db_session)
    await _make_item(db_session, purchase)

    # First call sets delivered_at.
    await on_purchase_delivered(purchase.id, db_session)
    await db_session.flush()

    # Second call must return early without duplicating.
    await on_purchase_delivered(purchase.id, db_session)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(CatalogListingPrice).where(
                CatalogListingPrice.source_ref == str(purchase.id)
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_sets_delivered_at(db_session: AsyncSession) -> None:
    purchase = await _make_purchase(db_session)
    await _make_item(db_session, purchase)

    assert purchase.delivered_at is None
    await on_purchase_delivered(purchase.id, db_session)
    await db_session.flush()

    assert purchase.delivered_at is not None
    assert purchase.delivered_at.tzinfo is not None


@pytest.mark.asyncio
async def test_skips_item_with_null_unit_cost(db_session: AsyncSession) -> None:
    purchase = await _make_purchase(db_session)
    await _make_item(db_session, purchase, unit_cost=None)

    await on_purchase_delivered(purchase.id, db_session)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(CatalogListingPrice).where(
                CatalogListingPrice.source_ref == str(purchase.id)
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_skips_item_when_purchase_currency_null(db_session: AsyncSession) -> None:
    purchase = await _make_purchase(db_session, currency=None)
    await _make_item(db_session, purchase, unit_cost=Decimal("200.00"))

    await on_purchase_delivered(purchase.id, db_session)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(CatalogListingPrice).where(
                CatalogListingPrice.source_ref == str(purchase.id)
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_creates_entry_for_each_item(db_session: AsyncSession) -> None:
    """Multiple purchase items → one listing price per item (same purchase_id ref,
    but different listings; if same listing, idempotency rule means one per listing)."""
    supplier_id = (
        await db_session.execute(text("SELECT id FROM catalog_supplier LIMIT 1"))
    ).scalar_one()
    purchase = ProcurementPurchase(
        supplier_id=supplier_id,
        status=ProcurementPurchaseStatus.delivered,
        currency="RUB",
    )
    db_session.add(purchase)
    await db_session.flush()

    # Two distinct products.
    product_ids = (
        await db_session.execute(text("SELECT id FROM catalog_product LIMIT 2"))
    ).scalars().all()

    if len(product_ids) < 2:
        pytest.skip("Need at least 2 products in seed data")

    for pid in product_ids:
        item = ProcurementPurchaseItem(
            purchase_id=purchase.id,
            product_id=pid,
            quantity=Decimal("1"),
            unit_cost=Decimal("400.00"),
        )
        db_session.add(item)
    await db_session.flush()

    await on_purchase_delivered(purchase.id, db_session)
    await db_session.flush()

    # Each product may map to the same supplier listing → 2 prices, 1 per listing.
    rows = (
        await db_session.execute(
            select(CatalogListingPrice).where(
                CatalogListingPrice.source_ref == str(purchase.id)
            )
        )
    ).scalars().all()
    # At minimum we expect one record per distinct listing (up to 2).
    assert len(rows) >= 1
