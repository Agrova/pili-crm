"""ADR-017 G4.6: tests for _is_actionable_order filter and cascade force=True.

Cases:
  a) items=None  → filtered, orders_filtered_historical=1
  b) items=[]    → filtered, orders_filtered_historical=1
  c) items non-empty + status_delivery='delivered' → filtered
  d) items non-empty + status_delivery='returned'  → filtered
  e) items non-empty + status_delivery='shipped'   → NOT filtered (in-flight)
  f) force=True cascade: prior draft orders deleted from orders_order
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.catalog.models  # noqa: F401
import app.communications.models  # noqa: F401
import app.orders.models  # noqa: F401
import app.pricing.models  # noqa: F401
from app.analysis import repository as analysis_repo
from app.analysis import service
from app.analysis.schemas import (
    MatchedOrder,
    MatchedOrderItem,
    MatchedStructuredExtract,
)
from app.orders.repository import create_customer

# ── seed helpers (mirrors test_service.py) ──────────────────────────────────


async def _seeded_product_id(session: AsyncSession) -> int:
    pid = (
        await session.execute(text("SELECT id FROM catalog_product ORDER BY id LIMIT 1"))
    ).scalar()
    assert pid is not None, "catalog seed required"
    return int(pid)


async def _seed_chat(session: AsyncSession, title: str) -> int:
    account_id = (
        await session.execute(
            text(
                "SELECT id FROM communications_telegram_account "
                "WHERE phone_number = '+77471057849'"
            )
        )
    ).scalar()
    assert account_id is not None, "ADR-012 seed account missing"
    chat_id = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type, title) "
                "VALUES (:aid, :tg, 'personal_chat', :t) RETURNING id"
            ),
            {
                "aid": account_id,
                "tg": f"f-{title}-{datetime.now(tz=UTC).timestamp()}",
                "t": title,
            },
        )
    ).scalar_one()
    await session.flush()
    return int(chat_id)


async def _make_customer(session: AsyncSession, suffix: str) -> int:
    c = await create_customer(
        session, name=f"Filter Test {suffix}", telegram_id=f"@ft_{suffix}"
    )
    return c.id


async def _link_chat_to_customer(
    session: AsyncSession, chat_id: int, customer_id: int
) -> None:
    msg_id = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_message "
                "(chat_id, telegram_message_id, sent_at, text) "
                "VALUES (:cid, :tmid, :sent, :body) RETURNING id"
            ),
            {
                "cid": chat_id,
                "tmid": f"m-{datetime.now(tz=UTC).timestamp()}",
                "sent": datetime.now(tz=UTC),
                "body": "hello",
            },
        )
    ).scalar_one()
    await session.flush()
    await session.execute(
        text(
            "INSERT INTO communications_link "
            "(telegram_message_id, target_module, target_entity, "
            " target_id, link_confidence) "
            "VALUES (:mid, 'orders', 'orders_customer', :tid, 'manual')"
        ),
        {"mid": msg_id, "tid": customer_id},
    )
    await session.flush()


async def _archive(
    session: AsyncSession,
    chat_id: int,
    extract: MatchedStructuredExtract,
    version: str = "v0.filter+test",
) -> int:
    row = await service.record_full_analysis(
        session,
        chat_id=chat_id,
        analyzer_version=version,
        messages_analyzed_up_to="1",
        narrative_markdown="n",
        matched_extract=extract,
        chunks_count=1,
    )
    return row.id


def _not_found_item(text_hint: str = "some item") -> MatchedOrderItem:
    return MatchedOrderItem(items_text=text_hint, matching_status="not_found")


# ── test cases ───────────────────────────────────────────────────────────────


async def test_filter_items_none(db_session: AsyncSession) -> None:
    """(a) items=None → filtered, counter=1, no orders_order row."""
    chat_id = await _seed_chat(db_session, "filter-a")
    cid = await _make_customer(db_session, "filter-a")
    await _link_chat_to_customer(db_session, chat_id, cid)

    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        orders=[MatchedOrder(description="old order", items=None)],
    )
    aid = await _archive(db_session, chat_id, extract)
    result = await service.apply_analysis_to_customer(db_session, analysis_id=aid)

    assert result.orders_created == 0
    assert result.orders_filtered_historical == 1
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM orders_order WHERE customer_id = :cid"),
            {"cid": cid},
        )
    ).scalar_one()
    assert count == 0


async def test_filter_items_empty_list(db_session: AsyncSession) -> None:
    """(b) items=[] → filtered, counter=1, no orders_order row."""
    chat_id = await _seed_chat(db_session, "filter-b")
    cid = await _make_customer(db_session, "filter-b")
    await _link_chat_to_customer(db_session, chat_id, cid)

    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        orders=[MatchedOrder(description="empty items", items=[])],
    )
    aid = await _archive(db_session, chat_id, extract)
    result = await service.apply_analysis_to_customer(db_session, analysis_id=aid)

    assert result.orders_created == 0
    assert result.orders_filtered_historical == 1
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM orders_order WHERE customer_id = :cid"),
            {"cid": cid},
        )
    ).scalar_one()
    assert count == 0


async def test_filter_status_delivered(db_session: AsyncSession) -> None:
    """(c) items non-empty + status_delivery='delivered' → filtered."""
    chat_id = await _seed_chat(db_session, "filter-c")
    cid = await _make_customer(db_session, "filter-c")
    await _link_chat_to_customer(db_session, chat_id, cid)

    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        orders=[
            MatchedOrder(
                items=[_not_found_item("plane")],
                status_delivery="delivered",
            )
        ],
    )
    aid = await _archive(db_session, chat_id, extract)
    result = await service.apply_analysis_to_customer(db_session, analysis_id=aid)

    assert result.orders_created == 0
    assert result.orders_filtered_historical == 1


async def test_filter_status_returned(db_session: AsyncSession) -> None:
    """(d) items non-empty + status_delivery='returned' → filtered."""
    chat_id = await _seed_chat(db_session, "filter-d")
    cid = await _make_customer(db_session, "filter-d")
    await _link_chat_to_customer(db_session, chat_id, cid)

    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        orders=[
            MatchedOrder(
                items=[_not_found_item("returned item")],
                status_delivery="returned",
            )
        ],
    )
    aid = await _archive(db_session, chat_id, extract)
    result = await service.apply_analysis_to_customer(db_session, analysis_id=aid)

    assert result.orders_created == 0
    assert result.orders_filtered_historical == 1


async def test_filter_status_shipped_passes(db_session: AsyncSession) -> None:
    """(e) items non-empty + status_delivery='shipped' → NOT filtered (in-flight)."""
    chat_id = await _seed_chat(db_session, "filter-e")
    cid = await _make_customer(db_session, "filter-e")
    await _link_chat_to_customer(db_session, chat_id, cid)

    pid = await _seeded_product_id(db_session)
    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        orders=[
            MatchedOrder(
                items=[
                    MatchedOrderItem(
                        items_text="active item",
                        quantity=Decimal("1"),
                        matching_status="confident_match",
                        matched_product_id=pid,
                    )
                ],
                status_delivery="shipped",
            )
        ],
    )
    aid = await _archive(db_session, chat_id, extract)
    result = await service.apply_analysis_to_customer(db_session, analysis_id=aid)

    assert result.orders_created == 1
    assert result.orders_filtered_historical == 0
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM orders_order WHERE customer_id = :cid"),
            {"cid": cid},
        )
    ).scalar_one()
    assert count == 1


async def test_force_cascade_deletes_draft_orders(db_session: AsyncSession) -> None:
    """(f) force=True: prior actionable orders deleted, historical never created.

    First run: 2 actionable + 1 historical.
    Second run (force=True): both prior orders_order rows deleted,
    only second run's orders remain.
    """
    chat_id = await _seed_chat(db_session, "filter-f")
    cid = await _make_customer(db_session, "filter-f")
    await _link_chat_to_customer(db_session, chat_id, cid)
    pid = await _seeded_product_id(db_session)

    def _extract_two_actionable_one_historical() -> MatchedStructuredExtract:
        return MatchedStructuredExtract(  # type: ignore[call-arg]
            schema_version=1,
            orders=[
                MatchedOrder(
                    items=[
                        MatchedOrderItem(
                            items_text="item-A",
                            quantity=Decimal("1"),
                            matching_status="confident_match",
                            matched_product_id=pid,
                        )
                    ],
                    status_delivery="shipped",
                ),
                MatchedOrder(
                    items=[_not_found_item("item-B")],
                    status_delivery="ordered",
                ),
                MatchedOrder(
                    description="old delivered order",
                    items=None,
                ),
            ],
        )

    version = "v0.filter+cascade"
    aid = await _archive(db_session, chat_id, _extract_two_actionable_one_historical(), version)

    first = await service.apply_analysis_to_customer(db_session, analysis_id=aid)
    assert first.orders_created == 2
    assert first.orders_filtered_historical == 1

    orders_after_first = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM orders_order WHERE customer_id = :cid"),
            {"cid": cid},
        )
    ).scalar_one()
    assert orders_after_first == 2

    second = await service.apply_analysis_to_customer(
        db_session, analysis_id=aid, force=True
    )
    assert second.orders_created == 2
    assert second.orders_filtered_historical == 1
    assert second.rolled_back_count > 0

    orders_after_force = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM orders_order WHERE customer_id = :cid"),
            {"cid": cid},
        )
    ).scalar_one()
    # Only the 2 orders from the second run remain — first run's orders deleted.
    assert orders_after_force == 2

    journal = await analysis_repo.list_created_entities(
        db_session, analyzer_version=version, source_chat_id=chat_id
    )
    order_journal_ids = [e.entity_id for e in journal if e.entity_type == "orders_order"]
    # Exactly 2 journal entries for orders_order (second run only).
    assert len(order_journal_ids) == 2
