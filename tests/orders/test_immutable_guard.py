"""Tests for IMMUTABLE_ORDER_STATUSES / IMMUTABLE_ITEM_STATUSES guards."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.orders.models import (
    IMMUTABLE_ITEM_STATUSES,
    IMMUTABLE_ORDER_STATUSES,
    OrdersOrderItemStatus,
    OrdersOrderStatus,
)
from app.orders.repository import OrderItemInput, create_customer, create_order
from app.orders.service import (
    add_order_item,
    assert_item_mutable,
    assert_order_mutable,
    create_draft_order,
)


# ── Pure unit tests — no DB needed ──────────────────────────────────────────

def test_assert_order_mutable_raises_for_confirmed() -> None:
    order = MagicMock()
    order.id = 1
    order.status = OrdersOrderStatus.confirmed
    with pytest.raises(ValueError, match="cannot be mutated"):
        assert_order_mutable(order)


def test_assert_order_mutable_raises_for_delivered() -> None:
    order = MagicMock()
    order.id = 2
    order.status = OrdersOrderStatus.delivered
    with pytest.raises(ValueError, match="cannot be mutated"):
        assert_order_mutable(order)


def test_assert_order_mutable_allows_draft() -> None:
    order = MagicMock()
    order.id = 3
    order.status = OrdersOrderStatus.draft
    assert_order_mutable(order)  # must not raise


def test_assert_order_mutable_allows_in_procurement() -> None:
    order = MagicMock()
    order.id = 4
    order.status = OrdersOrderStatus.in_procurement
    assert_order_mutable(order)  # must not raise


def test_assert_item_mutable_raises_for_delivered() -> None:
    item = MagicMock()
    item.id = 10
    item.status = OrdersOrderItemStatus.delivered
    with pytest.raises(ValueError, match="cannot be mutated"):
        assert_item_mutable(item)


def test_assert_item_mutable_raises_for_cancelled() -> None:
    item = MagicMock()
    item.id = 11
    item.status = OrdersOrderItemStatus.cancelled
    with pytest.raises(ValueError, match="cannot be mutated"):
        assert_item_mutable(item)


def test_assert_item_mutable_allows_pending() -> None:
    item = MagicMock()
    item.id = 12
    item.status = OrdersOrderItemStatus.pending
    assert_item_mutable(item)  # must not raise


# ── Frozenset membership sanity ──────────────────────────────────────────────

def test_immutable_order_statuses_contains_confirmed() -> None:
    assert OrdersOrderStatus.confirmed in IMMUTABLE_ORDER_STATUSES


def test_immutable_order_statuses_excludes_draft() -> None:
    assert OrdersOrderStatus.draft not in IMMUTABLE_ORDER_STATUSES


def test_immutable_item_statuses_contains_delivered_and_cancelled() -> None:
    assert OrdersOrderItemStatus.delivered in IMMUTABLE_ITEM_STATUSES
    assert OrdersOrderItemStatus.cancelled in IMMUTABLE_ITEM_STATUSES


# ── Integration tests — require DB ──────────────────────────────────────────

async def _make_customer(db: AsyncSession) -> int:
    cust = await create_customer(db, name="Guard Test User", telegram_id="@guard_test_ci")
    return cust.id


async def _product_id(db: AsyncSession) -> int:
    from app.catalog.repository import find_or_create_product
    p = await find_or_create_product(db, "Guard Test Widget")
    return p.id


async def test_add_order_item_blocked_on_confirmed_order(
    db_session: AsyncSession,
) -> None:
    """Adding an item to a confirmed order must raise ValueError.

    Note: create_order triggers a DB derive-status trigger on item INSERT that
    moves the order from 'confirmed' to 'in_procurement'. We force the order
    back to 'confirmed' via SQL to test the guard path directly.
    """
    from sqlalchemy import text

    cid = await _make_customer(db_session)
    order = await create_draft_order(db_session, customer_id=cid, items=[], origin="operator")
    order_id = order.id  # capture before expire invalidates lazy attrs
    # Force status to confirmed (bypasses normal transition)
    await db_session.execute(
        text("UPDATE orders_order SET status='confirmed' WHERE id=:oid"),
        {"oid": order_id},
    )
    db_session.expire(order)  # invalidate identity-map cache so get() re-reads DB
    pid = await _product_id(db_session)
    with pytest.raises(ValueError, match="cannot be mutated"):
        await add_order_item(
            db_session,
            order_id=order_id,
            product_id=pid,
            quantity=Decimal("1"),
            unit_price=Decimal("50"),
            currency="RUB",
        )


async def test_add_order_item_allowed_on_draft_order(
    db_session: AsyncSession,
) -> None:
    """Adding an item to a draft order must succeed."""
    cid = await _make_customer(db_session)
    order = await create_draft_order(db_session, customer_id=cid, items=[], origin="operator")
    pid = await _product_id(db_session)
    item = await add_order_item(
        db_session,
        order_id=order.id,
        product_id=pid,
        quantity=Decimal("2"),
        unit_price=Decimal("75"),
        currency="RUB",
    )
    assert item.order_id == order.id
    assert item.quantity == Decimal("2")
