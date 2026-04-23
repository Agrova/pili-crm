"""Tests for create_order repository function."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.finance.models import FinanceLedgerEntry
from app.orders.models import OrdersOrderItem, OrdersOrderItemStatus, OrdersOrderStatus
from app.orders.repository import (
    OrderItemInput,
    create_customer,
    create_order,
)


async def _make_customer(db: AsyncSession) -> int:
    cust = await create_customer(db, name="Test Buyer", telegram_id="@test_buyer_ci")
    return cust.id


async def test_create_basic_order(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session)
    result = await create_order(
        db_session,
        customer_id=cid,
        items=[
            OrderItemInput(
                product_name="Veritas Jack Plane", price=Decimal("28500"), quantity=Decimal("1")
            ),
            OrderItemInput(
                product_name="Лезвие PMV-11 тест", price=Decimal("4200"), quantity=Decimal("1")
            ),
        ],
    )

    assert result.order_id > 0
    assert result.order_display == f"З-{result.order_id:03d}"
    # DB trigger derives order status from items: all pending → in_procurement
    assert result.status == "confirmed"  # result reflects status at insert time
    assert result.total == Decimal("32700")
    assert result.paid == Decimal("0")
    assert result.debt == Decimal("32700")
    assert len(result.items) == 2

    # Verify order in DB — trigger has fired, status now reflects derivation
    from app.orders.models import OrdersOrder
    order = await db_session.get(OrdersOrder, result.order_id)
    assert order is not None
    # pending items → in_procurement per ADR-003/006 derivation rule
    assert order.status == OrdersOrderStatus.in_procurement
    assert order.currency == "RUB"
    assert order.total_price == Decimal("32700")

    # Verify items have pending status
    items = (
        await db_session.execute(
            select(OrdersOrderItem).where(OrdersOrderItem.order_id == result.order_id)
        )
    ).scalars().all()
    assert len(items) == 2
    assert all(i.status == OrdersOrderItemStatus.pending for i in items)


async def test_create_with_full_payment(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session)
    result = await create_order(
        db_session,
        customer_id=cid,
        items=[
            OrderItemInput(
                product_name="Shapton Glass Stone тест",
                price=Decimal("12000"),
                quantity=Decimal("1"),
            )
        ],
        paid_amount=Decimal("12000"),
    )

    assert result.paid == Decimal("12000")
    assert result.debt == Decimal("0")

    # Ledger entry must exist
    entries = (
        await db_session.execute(
            select(FinanceLedgerEntry).where(
                FinanceLedgerEntry.related_module == "orders",
                FinanceLedgerEntry.related_id == result.order_id,
            )
        )
    ).scalars().all()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.amount == Decimal("12000")
    assert entry.currency == "RUB"
    assert str(entry.entry_type) == "income"
    assert f"З-{result.order_id:03d}" in (entry.description or "")


async def test_create_partial_payment(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session)
    result = await create_order(
        db_session,
        customer_id=cid,
        items=[
            OrderItemInput(
                product_name="Pfeil стамеска тест",
                price=Decimal("9000"),
                quantity=Decimal("2"),
            )
        ],
        paid_amount=Decimal("9000"),
    )

    assert result.total == Decimal("18000")
    assert result.paid == Decimal("9000")
    assert result.debt == Decimal("9000")


async def test_create_unknown_product_auto_created(db_session: AsyncSession) -> None:
    """Product not in catalog must be created automatically."""
    unique_name = "УникальныйТоварТестXYZ_987654"
    cid = await _make_customer(db_session)
    result = await create_order(
        db_session,
        customer_id=cid,
        items=[
            OrderItemInput(
                product_name=unique_name,
                price=Decimal("1500"),
            )
        ],
    )
    assert result.order_id > 0
    assert result.items[0].product_name == unique_name


async def test_order_number_autoincrement(db_session: AsyncSession) -> None:
    """Two consecutive orders should have different IDs."""
    cid = await _make_customer(db_session)

    r1 = await create_order(
        db_session,
        customer_id=cid,
        items=[OrderItemInput(product_name="Товар А тест", price=Decimal("100"))],
    )
    r2 = await create_order(
        db_session,
        customer_id=cid,
        items=[OrderItemInput(product_name="Товар Б тест", price=Decimal("200"))],
    )
    assert r2.order_id > r1.order_id


async def test_create_order_unknown_customer(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not found"):
        await create_order(
            db_session,
            customer_id=999_999_999,
            items=[OrderItemInput(product_name="X", price=Decimal("1"))],
        )


async def test_create_order_no_items(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session)
    with pytest.raises(ValueError, match="item"):
        await create_order(db_session, customer_id=cid, items=[])


async def test_no_ledger_entry_without_payment(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session)
    result = await create_order(
        db_session,
        customer_id=cid,
        items=[OrderItemInput(product_name="Товар без оплаты", price=Decimal("500"))],
        paid_amount=Decimal("0"),
    )
    entries = (
        await db_session.execute(
            select(FinanceLedgerEntry).where(
                FinanceLedgerEntry.related_id == result.order_id,
                FinanceLedgerEntry.related_module == "orders",
            )
        )
    ).scalars().all()
    assert entries == []
