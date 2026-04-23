"""Tests for catalog_listing_price table and price views."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def _first_listing_id(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("SELECT id FROM catalog_product_listing WHERE is_primary = true LIMIT 1")
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_price_non_negative(db_session: AsyncSession) -> None:
    """INSERT with price = -1 must raise IntegrityError."""
    lid = await _first_listing_id(db_session)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO catalog_listing_price "
                "(listing_id, price, currency, observed_at, source) "
                "VALUES (:lid, -1, 'USD', NOW(), 'manual')"
            ),
            {"lid": lid},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_view_last_price(db_session: AsyncSession) -> None:
    """v_listing_last_price must return the most recent price for a listing."""
    lid = await _first_listing_id(db_session)

    # Insert three prices at different timestamps
    await db_session.execute(
        text(
            "INSERT INTO catalog_listing_price "
            "(listing_id, price, currency, observed_at, source) VALUES "
            "(:lid, 100, 'USD', '2026-01-01 10:00:00+00', 'manual'), "
            "(:lid, 200, 'USD', '2026-01-02 10:00:00+00', 'manual'), "
            "(:lid, 350, 'USD', '2026-01-03 10:00:00+00', 'manual')"
        ),
        {"lid": lid},
    )
    await db_session.flush()

    last_price = (
        await db_session.execute(
            text(
                "SELECT price FROM v_listing_last_price WHERE listing_id = :lid"
            ),
            {"lid": lid},
        )
    ).scalar_one()

    from decimal import Decimal
    assert Decimal(str(last_price)) == Decimal("350")


@pytest.mark.asyncio
async def test_view_product_current_price(db_session: AsyncSession) -> None:
    """v_product_current_price.primary_last_price comes from the primary listing."""
    # Find a product with exactly one primary listing
    pid = (
        await db_session.execute(
            text(
                "SELECT product_id FROM catalog_product_listing "
                "WHERE is_primary = true LIMIT 1"
            )
        )
    ).scalar_one()

    # Get the primary listing id
    primary_lid = (
        await db_session.execute(
            text(
                "SELECT id FROM catalog_product_listing "
                "WHERE product_id = :pid AND is_primary = true"
            ),
            {"pid": pid},
        )
    ).scalar_one()

    # Insert a price for the primary listing
    await db_session.execute(
        text(
            "INSERT INTO catalog_listing_price "
            "(listing_id, price, currency, observed_at, source) "
            "VALUES (:lid, 999, 'RUB', NOW(), 'manual')"
        ),
        {"lid": primary_lid},
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            text(
                "SELECT primary_last_price FROM v_product_current_price "
                "WHERE product_id = :pid"
            ),
            {"pid": pid},
        )
    ).mappings().one()

    from decimal import Decimal
    assert Decimal(str(row["primary_last_price"])) == Decimal("999")
