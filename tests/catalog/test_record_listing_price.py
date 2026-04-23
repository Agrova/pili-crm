"""Tests for catalog.services.record_listing_price_from_purchase."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogListingPrice, CatalogProductListing
from app.catalog.services import record_listing_price_from_purchase


async def _first_product_and_supplier(session: AsyncSession) -> tuple[int, int]:
    """Return (product_id, source_id) from an existing primary listing."""
    row = (
        await session.execute(
            select(
                CatalogProductListing.product_id,
                CatalogProductListing.source_id,
            ).where(CatalogProductListing.is_primary.is_(True)).limit(1)
        )
    ).one()
    return int(row.product_id), int(row.source_id)


async def _product_without_listing_of_supplier(
    session: AsyncSession, exclude_source_id: int
) -> tuple[int, int]:
    """Return (product_id, some_other_source_id) where the product has no listing
    for that source — for testing listing creation."""
    # Find a supplier id that differs from exclude_source_id.
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "SELECT id FROM catalog_supplier WHERE id != :sid LIMIT 1"
            ),
            {"sid": exclude_source_id},
        )
    ).one()
    other_source_id = int(row.id)

    # Find a product that has no listing for other_source_id.
    row2 = (
        await session.execute(
            text(
                """
                SELECT p.id FROM catalog_product p
                WHERE NOT EXISTS (
                    SELECT 1 FROM catalog_product_listing l
                    WHERE l.product_id = p.id AND l.source_id = :sid
                )
                LIMIT 1
                """
            ),
            {"sid": other_source_id},
        )
    ).one()
    return int(row2.id), other_source_id


_OBS = datetime(2026, 4, 1, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_records_price_for_existing_listing(db_session: AsyncSession) -> None:
    product_id, source_id = await _first_product_and_supplier(db_session)

    await record_listing_price_from_purchase(
        session=db_session,
        product_id=product_id,
        source_id=source_id,
        unit_cost=Decimal("500.00"),
        currency="RUB",
        observed_at=_OBS,
        purchase_id=99001,
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(CatalogListingPrice).where(
                CatalogListingPrice.source_ref == "99001"
            )
        )
    ).scalar_one()
    assert row.price == Decimal("500.00")
    assert row.currency == "RUB"
    assert row.source.value == "purchase"


@pytest.mark.asyncio
async def test_creates_non_primary_listing_when_product_has_listings(
    db_session: AsyncSession,
) -> None:
    product_id, existing_source_id = await _first_product_and_supplier(db_session)
    _, other_source_id = await _product_without_listing_of_supplier(
        db_session, existing_source_id
    )

    # Use a product that already has a primary listing but call with a new source.
    listing_before = (
        await db_session.execute(
            select(CatalogProductListing).where(
                CatalogProductListing.product_id == product_id,
                CatalogProductListing.is_primary.is_(True),
            )
        )
    ).scalar_one()

    await record_listing_price_from_purchase(
        session=db_session,
        product_id=product_id,
        source_id=other_source_id,
        unit_cost=Decimal("300.00"),
        currency="USD",
        observed_at=_OBS,
        purchase_id=99002,
    )
    await db_session.flush()

    new_listing = (
        await db_session.execute(
            select(CatalogProductListing).where(
                CatalogProductListing.product_id == product_id,
                CatalogProductListing.source_id == other_source_id,
            )
        )
    ).scalar_one()
    # Product had a primary listing → new one must be non-primary.
    assert new_listing.is_primary is False
    # Primary listing unchanged.
    assert listing_before.is_primary is True


@pytest.mark.asyncio
async def test_idempotent_same_purchase_id(db_session: AsyncSession) -> None:
    product_id, source_id = await _first_product_and_supplier(db_session)

    for _ in range(3):
        await record_listing_price_from_purchase(
            session=db_session,
            product_id=product_id,
            source_id=source_id,
            unit_cost=Decimal("100.00"),
            currency="RUB",
            observed_at=_OBS,
            purchase_id=99003,
        )
    await db_session.flush()

    count = (
        await db_session.execute(
            select(CatalogListingPrice).where(
                CatalogListingPrice.source_ref == "99003"
            )
        )
    ).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_different_purchase_ids_create_separate_records(
    db_session: AsyncSession,
) -> None:
    product_id, source_id = await _first_product_and_supplier(db_session)

    for pid in (99004, 99005):
        await record_listing_price_from_purchase(
            session=db_session,
            product_id=product_id,
            source_id=source_id,
            unit_cost=Decimal("200.00"),
            currency="RUB",
            observed_at=_OBS,
            purchase_id=pid,
        )
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(CatalogListingPrice).where(
                CatalogListingPrice.source_ref.in_(["99004", "99005"])
            )
        )
    ).scalars().all()
    assert len(rows) == 2
