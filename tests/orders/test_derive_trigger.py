"""Tests for PostgreSQL derive_order_status function and triggers (ADR-006).

All tests use real PostgreSQL via db_session fixture and roll back after each test.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ── helpers ──────────────────────────────────────────────────────────────────

async def _derive(session: AsyncSession, order_id: int) -> str:
    row = (
        await session.execute(
            text("SELECT derive_order_status(:oid)::text AS s"),
            {"oid": order_id},
        )
    ).mappings().one()
    return row["s"]


async def _order_status(session: AsyncSession, order_id: int) -> str:
    row = (
        await session.execute(
            text("SELECT status::text AS s FROM orders_order WHERE id = :oid"),
            {"oid": order_id},
        )
    ).mappings().one()
    return row["s"]


async def _order_updated_at(session: AsyncSession, order_id: int) -> object:
    row = (
        await session.execute(
            text("SELECT updated_at FROM orders_order WHERE id = :oid"),
            {"oid": order_id},
        )
    ).mappings().one()
    return row["updated_at"]


async def _create_order(session: AsyncSession) -> int:
    """Insert a minimal order with status='draft', return its id."""
    row = (
        await session.execute(
            text(
                """
                INSERT INTO orders_order (customer_id, status, delivery_paid_by_customer)
                SELECT id, 'draft', true FROM orders_customer LIMIT 1
                RETURNING id
                """
            )
        )
    ).mappings().one()
    await session.flush()
    return row["id"]


async def _first_product_id(session: AsyncSession) -> int:
    row = (
        await session.execute(text("SELECT id FROM catalog_product LIMIT 1"))
    ).mappings().one()
    return row["id"]


async def _add_item(
    session: AsyncSession,
    order_id: int,
    status: str,
    product_id: int | None = None,
) -> int:
    if product_id is None:
        product_id = await _first_product_id(session)
    row = (
        await session.execute(
            text(
                """
                INSERT INTO orders_order_item (order_id, product_id, quantity, status)
                VALUES (:oid, :pid, 1, CAST(:st AS orders_order_item_status))
                RETURNING id
                """
            ),
            {"oid": order_id, "pid": product_id, "st": status},
        )
    ).mappings().one()
    await session.flush()
    return row["id"]


async def _set_item_status(
    session: AsyncSession, item_id: int, status: str
) -> None:
    await session.execute(
        text(
            "UPDATE orders_order_item SET status = CAST(:st AS orders_order_item_status)"
            " WHERE id = :iid"
        ),
        {"st": status, "iid": item_id},
    )
    await session.flush()


# ── derive_order_status function tests (direct SQL calls) ────────────────────

async def test_single_pending(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    await _add_item(db_session, oid, "pending")
    assert await _derive(db_session, oid) == "in_procurement"


async def test_single_ordered(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    await _add_item(db_session, oid, "ordered")
    assert await _derive(db_session, oid) == "in_procurement"


async def test_mixed_arrived_delivered(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    pid = await _first_product_id(db_session)
    await _add_item(db_session, oid, "arrived", pid)
    await _add_item(db_session, oid, "delivered", pid)
    # min(arrived, delivered) = arrived
    assert await _derive(db_session, oid) == "arrived"


async def test_all_delivered(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    pid = await _first_product_id(db_session)
    await _add_item(db_session, oid, "delivered", pid)
    await _add_item(db_session, oid, "delivered", pid)
    assert await _derive(db_session, oid) == "delivered"


async def test_all_cancelled_items(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    pid = await _first_product_id(db_session)
    await _add_item(db_session, oid, "cancelled", pid)
    await _add_item(db_session, oid, "cancelled", pid)
    assert await _derive(db_session, oid) == "cancelled"


async def test_one_cancelled_one_ordered(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    pid = await _first_product_id(db_session)
    await _add_item(db_session, oid, "cancelled", pid)
    await _add_item(db_session, oid, "ordered", pid)
    # cancelled ignored; only ordered remains → in_procurement
    assert await _derive(db_session, oid) == "in_procurement"


async def test_no_items(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    assert await _derive(db_session, oid) == "draft"


# ── trigger tests (real operations) ─────────────────────────────────────────

async def test_trigger_on_update(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    iid = await _add_item(db_session, oid, "pending")
    # After INSERT trigger fires → should be in_procurement
    assert await _order_status(db_session, oid) == "in_procurement"

    await _set_item_status(db_session, iid, "shipped")
    assert await _order_status(db_session, oid) == "shipped_by_supplier"


async def test_trigger_idempotent(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    iid = await _add_item(db_session, oid, "arrived")
    ts_before = await _order_updated_at(db_session, oid)

    # Update item to same status — trigger must NOT update order.updated_at
    await _set_item_status(db_session, iid, "arrived")
    ts_after = await _order_updated_at(db_session, oid)

    assert ts_before == ts_after


async def test_trigger_on_insert(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    pid = await _first_product_id(db_session)
    await _add_item(db_session, oid, "delivered", pid)
    assert await _order_status(db_session, oid) == "delivered"

    # Insert a pending item — order must regress to in_procurement (min)
    await _add_item(db_session, oid, "pending", pid)
    assert await _order_status(db_session, oid) == "in_procurement"


async def test_trigger_on_delete(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    iid = await _add_item(db_session, oid, "pending")
    assert await _order_status(db_session, oid) == "in_procurement"

    # Delete the only item → order should revert to draft
    await db_session.execute(
        text("DELETE FROM orders_order_item WHERE id = :iid"), {"iid": iid}
    )
    await db_session.flush()
    assert await _order_status(db_session, oid) == "draft"


async def test_trigger_not_on_non_status_update(db_session: AsyncSession) -> None:
    oid = await _create_order(db_session)
    iid = await _add_item(db_session, oid, "arrived")
    ts_before = await _order_updated_at(db_session, oid)

    # Update quantity — trigger is AFTER UPDATE OF status, so should not fire
    await db_session.execute(
        text("UPDATE orders_order_item SET quantity = 2 WHERE id = :iid"),
        {"iid": iid},
    )
    await db_session.flush()
    ts_after = await _order_updated_at(db_session, oid)

    assert ts_before == ts_after


# ── atomicity test (I-1 scenario) ───────────────────────────────────────────

async def test_derivation_without_fastapi(db_session: AsyncSession) -> None:
    """Trigger fires via direct SQL without any FastAPI involvement."""
    oid = await _create_order(db_session)
    iid = await _add_item(db_session, oid, "pending")
    assert await _order_status(db_session, oid) == "in_procurement"

    # Direct SQL UPDATE — no HTTP call, no Python derive logic
    await db_session.execute(
        text(
            "UPDATE orders_order_item"
            " SET status = 'ordered'::orders_order_item_status"
            " WHERE id = :iid"
        ),
        {"iid": iid},
    )
    await db_session.flush()

    # Trigger must have updated order status in the same transaction
    assert await _order_status(db_session, oid) == "in_procurement"

    await db_session.execute(
        text(
            "UPDATE orders_order_item"
            " SET status = 'shipped'::orders_order_item_status"
            " WHERE id = :iid"
        ),
        {"iid": iid},
    )
    await db_session.flush()
    assert await _order_status(db_session, oid) == "shipped_by_supplier"
