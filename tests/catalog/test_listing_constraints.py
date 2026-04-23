"""Constraint tests for catalog_product_listing."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


async def _first_product_id(s: AsyncSession) -> int:
    return (
        await s.execute(text("SELECT id FROM catalog_product LIMIT 1"))
    ).scalar_one()


async def _first_supplier_id(s: AsyncSession) -> int:
    # Must be a supplier that actually has listings so ON DELETE RESTRICT fires.
    # (After ADR-011 the seed 'Unknown (auto)' has no listings.)
    return (
        await s.execute(
            text(
                "SELECT id FROM catalog_supplier "
                "WHERE id IN (SELECT source_id FROM catalog_product_listing) "
                "ORDER BY id LIMIT 1"
            )
        )
    ).scalar_one()


async def _second_supplier_id(s: AsyncSession) -> int:
    return (
        await s.execute(text("SELECT id FROM catalog_supplier ORDER BY id LIMIT 1 OFFSET 1"))
    ).scalar_one()


@pytest.mark.asyncio
async def test_unique_product_source(db_session: AsyncSession) -> None:
    """Two listings for the same (product_id, source_id) must raise IntegrityError."""
    pid = await _first_product_id(db_session)
    sid = await _second_supplier_id(db_session)

    # Remove any existing listing for this pair so the test is self-contained.
    await db_session.execute(
        text(
            "DELETE FROM catalog_product_listing "
            "WHERE product_id = :pid AND source_id = :sid AND is_primary = false"
        ),
        {"pid": pid, "sid": sid},
    )

    await db_session.execute(
        text(
            "INSERT INTO catalog_product_listing (product_id, source_id, is_primary) "
            "VALUES (:pid, :sid, false)"
        ),
        {"pid": pid, "sid": sid},
    )

    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO catalog_product_listing (product_id, source_id, is_primary) "
                "VALUES (:pid, :sid, false)"
            ),
            {"pid": pid, "sid": sid},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_unique_primary_per_product(db_session: AsyncSession) -> None:
    """Two is_primary=true listings for the same product must raise IntegrityError."""
    pid = await _first_product_id(db_session)
    sid = await _second_supplier_id(db_session)

    # The seed already inserted one primary listing; inserting another must fail.
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO catalog_product_listing (product_id, source_id, is_primary) "
                "VALUES (:pid, :sid, true)"
            ),
            {"pid": pid, "sid": sid},
        )
        await db_session.flush()


@pytest.mark.asyncio
async def test_cascade_delete_product(db_session: AsyncSession) -> None:
    """Deleting a product must cascade-delete its listings."""
    new_pid = (
        await db_session.execute(
            text("INSERT INTO catalog_product (name) VALUES ('_test_cascade') RETURNING id")
        )
    ).scalar_one()
    sid = await _first_supplier_id(db_session)
    await db_session.execute(
        text(
            "INSERT INTO catalog_product_listing (product_id, source_id, is_primary) "
            "VALUES (:pid, :sid, true)"
        ),
        {"pid": new_pid, "sid": sid},
    )
    await db_session.flush()

    await db_session.execute(
        text("DELETE FROM catalog_product WHERE id = :pid"), {"pid": new_pid}
    )
    await db_session.flush()

    remaining = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM catalog_product_listing WHERE product_id = :pid"
            ),
            {"pid": new_pid},
        )
    ).scalar()
    assert remaining == 0


@pytest.mark.asyncio
async def test_restrict_delete_supplier(db_session: AsyncSession) -> None:
    """Deleting a supplier that has listings must raise IntegrityError."""
    sid = await _first_supplier_id(db_session)
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("DELETE FROM catalog_supplier WHERE id = :sid"), {"sid": sid}
        )
        await db_session.flush()
