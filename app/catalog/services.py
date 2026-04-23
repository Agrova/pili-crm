"""Catalog domain services — side-effectful operations on catalog data."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import (
    CatalogListingPrice,
    CatalogPriceSource,
    CatalogProductListing,
)


async def record_listing_price_from_purchase(
    session: AsyncSession,
    product_id: int,
    source_id: int,
    unit_cost: Decimal,
    currency: str,
    observed_at: datetime,
    purchase_id: int,
) -> None:
    """Record a supplier price observation from a delivered purchase.

    Idempotent: if a catalog_listing_price with source='purchase' and
    source_ref=str(purchase_id) already exists for this listing, returns immediately.

    Creates the listing if it doesn't exist (is_primary=True only when it's the
    product's very first listing).
    """
    # Find or create the listing for this (product, supplier) pair.
    listing_result = await session.execute(
        select(CatalogProductListing).where(
            CatalogProductListing.product_id == product_id,
            CatalogProductListing.source_id == source_id,
        )
    )
    listing = listing_result.scalar_one_or_none()

    if listing is None:
        # Check if this product already has any listing → non-primary.
        any_listing_result = await session.execute(
            select(CatalogProductListing.id).where(
                CatalogProductListing.product_id == product_id
            ).limit(1)
        )
        has_existing = any_listing_result.scalar_one_or_none() is not None
        listing = CatalogProductListing(
            product_id=product_id,
            source_id=source_id,
            is_primary=not has_existing,
        )
        session.add(listing)
        await session.flush()

    # Idempotency: skip if already recorded for this purchase.
    source_ref = str(purchase_id)
    dup_result = await session.execute(
        select(CatalogListingPrice.id).where(
            CatalogListingPrice.listing_id == listing.id,
            CatalogListingPrice.source == CatalogPriceSource.purchase,
            CatalogListingPrice.source_ref == source_ref,
        ).limit(1)
    )
    if dup_result.scalar_one_or_none() is not None:
        return

    price_record = CatalogListingPrice(
        listing_id=listing.id,
        price=unit_cost,
        currency=currency,
        observed_at=observed_at,
        source=CatalogPriceSource.purchase,
        source_ref=source_ref,
    )
    session.add(price_record)
