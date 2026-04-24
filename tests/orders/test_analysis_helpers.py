"""ADR-011 Task 2: tests for the analysis-facing helpers in orders.service.

Cover the six functions added to let ``app/analysis/service.py`` mutate
``orders_customer_profile`` and create draft orders without importing
``app.orders.models`` directly.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.catalog.models  # noqa: F401  — ensure FK targets are mapped
import app.communications.models  # noqa: F401
import app.orders.models  # noqa: F401
import app.pricing.models  # noqa: F401
from app.orders.models import OrdersOrder, OrdersOrderStatus
from app.orders.repository import create_customer
from app.orders.service import (
    add_order_item,
    append_incident_in_locked_profile,
    append_preference_in_locked_profile,
    create_draft_order,
    get_or_create_profile_for_update,
    upsert_delivery_preferences_in_locked_profile,
)


async def _make_customer(db: AsyncSession, suffix: str) -> int:
    c = await create_customer(db, name=f"Analysis Test {suffix}", telegram_id=f"@t_{suffix}")
    return c.id


async def _seeded_product_id(db: AsyncSession) -> int:
    pid = (
        await db.execute(text("SELECT id FROM catalog_product ORDER BY id LIMIT 1"))
    ).scalar()
    assert pid is not None, "seeded product required"
    return int(pid)


# ── get_or_create_profile_for_update ────────────────────────────────────────


async def test_get_or_create_profile_creates_when_absent(
    db_session: AsyncSession,
) -> None:
    cid = await _make_customer(db_session, "prof_create")
    profile = await get_or_create_profile_for_update(db_session, cid)
    assert profile.id > 0
    assert profile.customer_id == cid
    # Re-fetch: same row, no duplicate
    p2 = await get_or_create_profile_for_update(db_session, cid)
    assert p2.id == profile.id


async def test_get_or_create_profile_unknown_customer_raises(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(ValueError, match="not found"):
        await get_or_create_profile_for_update(db_session, 999_999_999)


# ── append_preference_in_locked_profile ─────────────────────────────────────


async def test_append_preference_stamps_confidence(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session, "pref")
    profile = await get_or_create_profile_for_update(db_session, cid)
    await append_preference_in_locked_profile(
        db_session,
        profile,
        {"product_hint": "Veritas", "note": "любит", "source_message_ids": ["1"]},
        confidence="suggested",
    )
    await append_preference_in_locked_profile(
        db_session,
        profile,
        {"product_hint": "Shapton", "note": "точит", "source_message_ids": ["9"]},
        confidence="manual",
    )
    assert profile.preferences is not None
    assert len(profile.preferences) == 2
    assert profile.preferences[0]["confidence"] == "suggested"
    assert profile.preferences[1]["confidence"] == "manual"
    assert profile.preferences[0]["product_hint"] == "Veritas"


# ── append_incident_in_locked_profile ───────────────────────────────────────


async def test_append_incident_stamps_confidence(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session, "inc")
    profile = await get_or_create_profile_for_update(db_session, cid)
    await append_incident_in_locked_profile(
        db_session,
        profile,
        {
            "date": "2025-03-15",
            "summary": "Царапина на товаре",
            "resolved": True,
            "source_message_ids": ["789"],
        },
        confidence="suggested",
    )
    assert profile.incidents is not None
    assert len(profile.incidents) == 1
    assert profile.incidents[0]["confidence"] == "suggested"
    assert profile.incidents[0]["summary"] == "Царапина на товаре"


# ── upsert_delivery_preferences_in_locked_profile ───────────────────────────


async def test_upsert_delivery_preferences_overwrites_as_single_element_array(
    db_session: AsyncSession,
) -> None:
    cid = await _make_customer(db_session, "del")
    profile = await get_or_create_profile_for_update(db_session, cid)
    # Seed with operator-confirmed primary
    profile.delivery_preferences = [
        {"method": "самовывоз", "is_primary": True, "confidence": "manual"}
    ]
    await db_session.flush()

    await upsert_delivery_preferences_in_locked_profile(
        db_session,
        profile,
        {"method": "СДЭК", "preferred_time": "вечер", "notes": None},
        confidence="suggested",
    )

    assert profile.delivery_preferences is not None
    assert len(profile.delivery_preferences) == 1
    elem = profile.delivery_preferences[0]
    assert elem["method"] == "СДЭК"
    assert elem["confidence"] == "suggested"
    assert elem["is_primary"] is False


# ── create_draft_order + add_order_item ─────────────────────────────────────


async def test_create_draft_order_happy_path(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session, "draft")
    order = await create_draft_order(db_session, cid, items=[], origin="analysis")
    assert order.id > 0
    assert order.status == OrdersOrderStatus.draft
    assert order.currency == "RUB"

    # Re-fetch to confirm persistence
    fetched = await db_session.get(OrdersOrder, order.id)
    assert fetched is not None
    assert fetched.status == OrdersOrderStatus.draft


async def test_create_draft_order_rejects_prefilled_items(
    db_session: AsyncSession,
) -> None:
    cid = await _make_customer(db_session, "draft_items")
    with pytest.raises(ValueError, match="non-empty items"):
        await create_draft_order(db_session, cid, items=["bogus"], origin="analysis")


async def test_create_draft_order_unknown_customer_raises(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(ValueError, match="not found"):
        await create_draft_order(
            db_session, 999_999_999, items=[], origin="analysis"
        )


async def test_add_order_item_to_draft(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session, "add_item")
    order = await create_draft_order(db_session, cid, items=[], origin="analysis")
    pid = await _seeded_product_id(db_session)
    item = await add_order_item(
        db_session,
        order_id=order.id,
        product_id=pid,
        quantity=Decimal("2"),
        unit_price=Decimal("150"),
        currency="RUB",
    )
    assert item.id > 0
    assert item.order_id == order.id
    assert item.product_id == pid
    assert item.quantity == Decimal("2")


async def test_add_order_item_rejects_zero_quantity(db_session: AsyncSession) -> None:
    cid = await _make_customer(db_session, "bad_qty")
    order = await create_draft_order(db_session, cid, items=[], origin="analysis")
    pid = await _seeded_product_id(db_session)
    with pytest.raises(ValueError, match="quantity must be > 0"):
        await add_order_item(
            db_session,
            order_id=order.id,
            product_id=pid,
            quantity=Decimal("0"),
            unit_price=Decimal("1"),
            currency="RUB",
        )


async def test_add_order_item_unknown_order_raises(db_session: AsyncSession) -> None:
    pid = await _seeded_product_id(db_session)
    with pytest.raises(ValueError, match="not found"):
        await add_order_item(
            db_session,
            order_id=999_999_999,
            product_id=pid,
            quantity=Decimal("1"),
            unit_price=Decimal("10"),
            currency="RUB",
        )
