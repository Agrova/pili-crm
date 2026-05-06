"""Tests for MCP tools: list_draft_orders and verify_draft_order."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings

_CRM_MCP = Path(__file__).resolve().parent.parent.parent / "crm-mcp"
if str(_CRM_MCP) not in sys.path:
    sys.path.insert(0, str(_CRM_MCP))

from tools import list_draft_orders, verify_draft_order  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1 FROM orders_order LIMIT 0"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"DB not available: {exc}")
    yield eng
    await eng.dispose()


@pytest.fixture
async def clean(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async def _wipe() -> None:
        async with engine.begin() as conn:
            # orders_order_item cascades from orders_order
            await conn.execute(
                text(
                    "DELETE FROM orders_order"
                    " WHERE customer_id IN ("
                    "   SELECT id FROM orders_customer WHERE name LIKE 'TEST_DO_%'"
                    " )"
                )
            )
            await conn.execute(
                text("DELETE FROM orders_customer WHERE name LIKE 'TEST_DO_%'")
            )

    await _wipe()
    yield engine
    await _wipe()


@pytest.fixture
def session_factory(clean: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(clean, expire_on_commit=False)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _insert_customer(engine: AsyncEngine, *, name: str, telegram_id: str) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO orders_customer (name, telegram_id)"
                " VALUES (:n, :tg) RETURNING id"
            ),
            {"n": name, "tg": telegram_id},
        )
        return int(row.scalar_one())


async def _insert_draft_order(engine: AsyncEngine, *, customer_id: int) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO orders_order"
                "  (customer_id, status, currency, delivery_paid_by_customer)"
                " VALUES (:cid, 'draft', 'RUB', true)"
                " RETURNING id"
            ),
            {"cid": customer_id},
        )
        return int(row.scalar_one())


async def _first_product_id(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        row = await conn.execute(text("SELECT id FROM catalog_product LIMIT 1"))
        pid = row.scalar_one_or_none()
        if pid is None:
            pytest.skip("No products in catalog — run seed first")
        return int(pid)


async def _insert_order_item(
    engine: AsyncEngine, *, order_id: int, product_id: int
) -> int:
    """Insert an item and reset the order status back to draft.

    The derive_order_status DB trigger fires on items INSERT and changes the
    order status from 'draft' to 'in_procurement'. Since the trigger is on
    orders_order_item (not orders_order), a direct UPDATE on orders_order
    does NOT re-trigger derivation and safely returns the order to 'draft'
    for test purposes.
    """
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO orders_order_item"
                "  (order_id, product_id, quantity, unit_price)"
                " VALUES (:oid, :pid, 1, 100.00)"
                " RETURNING id"
            ),
            {"oid": order_id, "pid": product_id},
        )
        item_id = int(row.scalar_one())
        # Reset order status to 'draft' — trigger only fires on order_item changes.
        await conn.execute(
            text(
                "UPDATE orders_order"
                " SET status = 'draft'::orders_order_status"
                " WHERE id = :oid"
            ),
            {"oid": order_id},
        )
        return item_id


async def _get_order_status(engine: AsyncEngine, order_id: int) -> str | None:
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT status::text AS s FROM orders_order WHERE id = :oid"),
            {"oid": order_id},
        )
        m = row.mappings().first()
        return m["s"] if m else None


async def _order_exists(engine: AsyncEngine, order_id: int) -> bool:
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT 1 FROM orders_order WHERE id = :oid"),
            {"oid": order_id},
        )
        return row.first() is not None


async def _item_exists(engine: AsyncEngine, item_id: int) -> bool:
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT 1 FROM orders_order_item WHERE id = :iid"),
            {"iid": item_id},
        )
        return row.first() is not None


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_list_draft_orders_returns_drafts(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Two draft orders for one customer — both are returned by list_draft_orders."""
    cid = await _insert_customer(
        clean, name="TEST_DO_ListDrafts", telegram_id="tgdo_list"
    )
    oid1 = await _insert_draft_order(clean, customer_id=cid)
    oid2 = await _insert_draft_order(clean, customer_id=cid)

    async with session_factory() as session:
        result = await list_draft_orders.run(session, customer_id=cid)

    assert result["customer_id_filter"] == cid
    order_ids = {o["order_id"] for o in result["orders"]}
    assert oid1 in order_ids
    assert oid2 in order_ids
    for o in result["orders"]:
        assert o["customer"]["id"] == cid


async def test_list_draft_orders_no_drafts(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Customer with no orders → empty list; format_text reflects the filter."""
    cid = await _insert_customer(
        clean, name="TEST_DO_NoDrafts", telegram_id="tgdo_nodrafts"
    )

    async with session_factory() as session:
        result = await list_draft_orders.run(session, customer_id=cid)

    assert result["orders"] == []
    text_with_filter = list_draft_orders.format_text(result)
    assert f"id={cid}" in text_with_filter

    # Verify format_text for global no-filter case directly
    global_empty = {"orders": [], "customer_id_filter": None}
    assert list_draft_orders.format_text(global_empty) == "Черновых заказов нет."


async def test_verify_draft_order_confirm(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Confirming a draft order changes its status to confirmed in the DB."""
    cid = await _insert_customer(
        clean, name="TEST_DO_Confirm", telegram_id="tgdo_confirm"
    )
    oid = await _insert_draft_order(clean, customer_id=cid)

    async with session_factory() as session:
        result = await verify_draft_order.run(session, order_id=oid, action="confirm")

    assert result["status"] == "ok"
    assert result["action"] == "confirmed"
    assert result["order_id"] == oid
    assert result["display_id"] == f"З-{oid:03d}"

    status_in_db = await _get_order_status(clean, oid)
    assert status_in_db == "confirmed"

    text_out = verify_draft_order.format_text(result)
    assert "✅" in text_out
    assert "confirmed" in text_out


async def test_verify_draft_order_reject(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Rejecting a draft order deletes the order and cascades to its items."""
    pid = await _first_product_id(clean)
    cid = await _insert_customer(
        clean, name="TEST_DO_Reject", telegram_id="tgdo_reject"
    )
    oid = await _insert_draft_order(clean, customer_id=cid)
    iid = await _insert_order_item(clean, order_id=oid, product_id=pid)

    async with session_factory() as session:
        result = await verify_draft_order.run(session, order_id=oid, action="reject")

    assert result["status"] == "ok"
    assert result["action"] == "rejected"
    assert result["order_id"] == oid

    assert not await _order_exists(clean, oid)
    assert not await _item_exists(clean, iid)

    text_out = verify_draft_order.format_text(result)
    assert "🗑" in text_out


async def test_verify_draft_order_not_found(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Calling verify_draft_order with a nonexistent order_id returns order_not_found."""
    async with session_factory() as session:
        result = await verify_draft_order.run(session, order_id=999_999_999, action="confirm")

    assert result["status"] == "error"
    assert result["error"] == "order_not_found"
    assert result["order_id"] == 999_999_999

    text_out = verify_draft_order.format_text(result)
    assert "не найден" in text_out


async def test_verify_draft_order_already_confirmed(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Attempting to confirm an already-confirmed order returns not_a_draft error."""
    cid = await _insert_customer(
        clean, name="TEST_DO_AlreadyConf", telegram_id="tgdo_alreadyconf"
    )
    oid = await _insert_draft_order(clean, customer_id=cid)

    # First confirm
    async with session_factory() as session:
        first = await verify_draft_order.run(session, order_id=oid, action="confirm")
    assert first["status"] == "ok"

    # Second confirm — should fail
    async with session_factory() as session:
        second = await verify_draft_order.run(session, order_id=oid, action="confirm")

    assert second["status"] == "error"
    assert second["error"] == "not_a_draft"
    assert second["current_status"] == "confirmed"

    text_out = verify_draft_order.format_text(second)
    assert "не является черновиком" in text_out
    assert "confirmed" in text_out
