"""ADR-007 Package 1 migration tests: data integrity after upgrade/downgrade."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_all_products_have_primary_listing(db_session: AsyncSession) -> None:
    product_count = (
        await db_session.execute(text("SELECT COUNT(*) FROM catalog_product"))
    ).scalar()
    primary_count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM catalog_product_listing WHERE is_primary = true")
        )
    ).scalar()
    assert product_count == primary_count, (
        f"products={product_count} but primary listings={primary_count}"
    )


@pytest.mark.asyncio
async def test_sku_at_source_matches_product_sku(db_session: AsyncSession) -> None:
    """sku_at_source must equal product.sku for all primary listings (NULL allowed)."""
    mismatches = (
        await db_session.execute(
            text(
                """
                SELECT COUNT(*)
                FROM catalog_product_listing cpl
                JOIN catalog_product p ON p.id = cpl.product_id
                WHERE cpl.is_primary = true
                  AND cpl.sku_at_source IS DISTINCT FROM p.sku
                """
            )
        )
    ).scalar()
    assert mismatches == 0, f"{mismatches} listings have sku_at_source != product.sku"


@pytest.mark.asyncio
async def test_views_exist(db_session: AsyncSession) -> None:
    row = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM pg_views "
                "WHERE viewname IN ('v_listing_last_price', 'v_product_current_price')"
            )
        )
    ).scalar()
    assert row == 2, "Expected both views to exist"


@pytest.mark.asyncio
async def test_catalog_product_has_no_supplier_id(db_session: AsyncSession) -> None:
    col_exists = (
        await db_session.execute(
            text(
                """
                SELECT COUNT(*) FROM information_schema.columns
                WHERE table_name = 'catalog_product'
                  AND column_name = 'supplier_id'
                """
            )
        )
    ).scalar()
    assert col_exists == 0, "catalog_product.supplier_id should not exist after migration"
