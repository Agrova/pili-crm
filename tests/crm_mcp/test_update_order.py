"""Tests for MCP tool: update_order."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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

from tools import update_order  # noqa: E402

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
            # CASCADE removes order_items
            await conn.execute(
                text(
                    "DELETE FROM orders_order WHERE customer_id IN ("
                    "  SELECT id FROM orders_customer WHERE name LIKE 'TEST_UO_%'"
                    ")"
                )
            )
            await conn.execute(
                text(
                    "DELETE FROM orders_customer WHERE name LIKE 'TEST_UO_%'"
                    "   OR telegram_id LIKE 'tguo_%'"
                )
            )
            await conn.execute(
                text("DELETE FROM catalog_product WHERE name LIKE 'TEST_UO_%'")
            )

    await _wipe()
    yield engine
    await _wipe()


@pytest.fixture
def session_factory(clean: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(clean, expire_on_commit=False)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _insert_customer(engine: AsyncEngine, *, name: str) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO orders_customer (name, telegram_id)"
                " VALUES (:n, :tg) RETURNING id"
            ),
            {"n": name, "tg": f"tguo_{name[-6:]}"},
        )
        return int(row.scalar_one())


async def _insert_order(
    engine: AsyncEngine, *, customer_id: int, status: str = "confirmed"
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO orders_order (customer_id, status, currency)"
                " VALUES (:cid, CAST(:st AS orders_order_status), 'RUB') RETURNING id"
            ),
            {"cid": customer_id, "st": status},
        )
        return int(row.scalar_one())


async def _insert_product(engine: AsyncEngine, *, name: str) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text("INSERT INTO catalog_product (name) VALUES (:n) RETURNING id"),
            {"n": name},
        )
        return int(row.scalar_one())


async def _get_order_total(engine: AsyncEngine, order_id: int) -> float | None:
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT total_price FROM orders_order WHERE id = :oid"),
            {"oid": order_id},
        )
        val = row.scalar_one_or_none()
        return float(val) if val is not None else None


async def _get_order_items(engine: AsyncEngine, order_id: int) -> list[dict[str, Any]]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT oi.id, oi.product_id, p.name AS product_name,"
                "       oi.quantity, oi.unit_price"
                " FROM orders_order_item oi"
                " JOIN catalog_product p ON p.id = oi.product_id"
                " WHERE oi.order_id = :oid"
            ),
            {"oid": order_id},
        )
        return [dict(r) for r in rows.mappings()]


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_order_not_found(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        result = await update_order.run(
            session,
            order_id=9_999_999,
            items_to_add=[{"product_name": "anything", "price": 100}],
        )
    assert result["status"] == "error"
    assert result["error"] == "order_not_found"


async def test_order_cancelled(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    cid = await _insert_customer(clean, name="TEST_UO_Cancelled")
    oid = await _insert_order(clean, customer_id=cid, status="cancelled")

    async with session_factory() as session:
        result = await update_order.run(
            session,
            order_id=oid,
            items_to_add=[{"product_name": "TEST_UO_Item", "price": 50}],
        )
    assert result["status"] == "error"
    assert result["error"] == "order_cancelled"
    assert result["order_status"] == "cancelled"


async def test_happy_path_adds_item_and_recalculates_total(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    cid = await _insert_customer(clean, name="TEST_UO_Happy")
    oid = await _insert_order(clean, customer_id=cid)
    await _insert_product(clean, name="TEST_UO_Existing Product")

    async with session_factory() as session:
        result = await update_order.run(
            session,
            order_id=oid,
            items_to_add=[
                {"product_name": "TEST_UO_Existing Product", "price": 150.0, "quantity": 2}
            ],
        )

    assert result["status"] == "ok"
    assert result["order_id"] == oid
    assert result["order_display"] == f"З-{oid:03d}"
    assert result["customer_id"] == cid
    assert len(result["added_items"]) == 1
    assert result["added_items"][0]["unit_price"] == 150.0
    assert result["added_items"][0]["quantity"] == 2.0
    assert result["added_items"][0]["line_total"] == 300.0
    assert result["new_total"] == pytest.approx(300.0)

    # Verify DB
    total = await _get_order_total(clean, oid)
    assert total == pytest.approx(300.0)
    items = await _get_order_items(clean, oid)
    assert len(items) == 1
    assert items[0]["product_name"] == "TEST_UO_Existing Product"


async def test_new_product_auto_created(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    cid = await _insert_customer(clean, name="TEST_UO_AutoCreate")
    oid = await _insert_order(clean, customer_id=cid)

    new_product_name = "TEST_UO_Brand New Widget"

    async with session_factory() as session:
        result = await update_order.run(
            session,
            order_id=oid,
            items_to_add=[{"product_name": new_product_name, "price": 99.0}],
        )

    assert result["status"] == "ok"
    assert result["added_items"][0]["product_name"] == new_product_name

    # Product must exist in catalog now
    async with clean.connect() as conn:
        row = await conn.execute(
            text("SELECT id FROM catalog_product WHERE name = :n"),
            {"n": new_product_name},
        )
        assert row.scalar_one_or_none() is not None


async def test_existing_product_found_by_ilike(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _insert_product(clean, name="TEST_UO_ILIKE Target Product")
    cid = await _insert_customer(clean, name="TEST_UO_ILike")
    oid = await _insert_order(clean, customer_id=cid)

    async with session_factory() as session:
        result = await update_order.run(
            session,
            order_id=oid,
            items_to_add=[{"product_name": "ILIKE Target Product", "price": 55.0}],
        )

    assert result["status"] == "ok"
    assert result["added_items"][0]["product_name"] == "TEST_UO_ILIKE Target Product"
