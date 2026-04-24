"""ADR-011 Task 2 tests: analysis repository CRUD over all four module tables."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# Side-effect imports: analysis models FK the communications chat table, so
# SQLAlchemy needs the referenced class loaded before mapper resolution runs.
import app.communications.models  # noqa: F401
import app.orders.models  # noqa: F401
from app.analysis import repository as repo
from app.analysis.models import AnalysisPendingMatchingStatus


async def _seed_chat_id(session: AsyncSession) -> int:
    """Create a throwaway Telegram chat for the test's ingress point."""
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
            {"aid": account_id, "tg": f"tg-{datetime.now(tz=UTC).timestamp()}", "t": "test"},
        )
    ).scalar_one()
    await session.flush()
    return int(chat_id)


async def _seed_draft_order(session: AsyncSession) -> int:
    """Create a minimal draft order for pending-order-item tests."""
    customer_id = (
        await session.execute(text("SELECT id FROM orders_customer LIMIT 1"))
    ).scalar_one()
    order_id = (
        await session.execute(
            text(
                "INSERT INTO orders_order (customer_id, status, currency) "
                "VALUES (:cid, 'draft', 'RUB') RETURNING id"
            ),
            {"cid": customer_id},
        )
    ).scalar_one()
    await session.flush()
    return int(order_id)


# ── analysis_chat_analysis ──────────────────────────────────────────────────


async def test_upsert_analysis_inserts_new_row(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat_id(db_session)
    row = await repo.upsert_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.test+repo",
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to="42",
        narrative_markdown="# narrative",
        structured_extract={"_v": 1, "identity": {"name_guess": "Test"}},
        chunks_count=1,
    )
    assert row.id > 0
    assert row.chat_id == chat_id
    assert row.analyzer_version == "v0.test+repo"


async def test_upsert_analysis_updates_on_conflict(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat_id(db_session)
    first = await repo.upsert_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.test+repo",
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to="42",
        narrative_markdown="first",
        structured_extract={"_v": 1},
        chunks_count=1,
    )
    second = await repo.upsert_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.test+repo",
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to="99",
        narrative_markdown="second",
        structured_extract={"_v": 1, "identity": {"name_guess": "Changed"}},
        chunks_count=2,
    )
    assert first.id == second.id
    assert second.narrative_markdown == "second"
    assert second.messages_analyzed_up_to == "99"
    assert second.chunks_count == 2


async def test_upsert_analysis_different_version_creates_new_row(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat_id(db_session)
    a = await repo.upsert_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.test+repo-A",
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to="10",
        narrative_markdown="A",
        structured_extract={"_v": 1},
        chunks_count=1,
    )
    b = await repo.upsert_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.test+repo-B",
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to="10",
        narrative_markdown="B",
        structured_extract={"_v": 1},
        chunks_count=1,
    )
    assert a.id != b.id
    # Listing sees both
    all_rows = await repo.list_analyses_for_chat(db_session, chat_id)
    ids = {r.id for r in all_rows}
    assert {a.id, b.id}.issubset(ids)


async def test_get_analysis_by_chat_and_version(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat_id(db_session)
    created = await repo.upsert_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.test+repo",
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to="1",
        narrative_markdown="",
        structured_extract={"_v": 1},
        chunks_count=0,
        skipped_reason="empty",
        preflight_classification="empty",
        preflight_confidence="high",
        preflight_reason="no text",
    )
    fetched = await repo.get_analysis_by_chat_and_version(
        db_session, chat_id, "v0.test+repo"
    )
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.skipped_reason == "empty"
    assert fetched.preflight_classification == "empty"

    miss = await repo.get_analysis_by_chat_and_version(
        db_session, chat_id, "v0.none"
    )
    assert miss is None


async def test_upsert_analysis_skipped_row_must_match_check_constraint(
    db_session: AsyncSession,
) -> None:
    """Feeding a non-`{"_v": 1}` payload alongside skipped_reason must fail."""
    chat_id = await _seed_chat_id(db_session)
    with pytest.raises(IntegrityError):
        await repo.upsert_analysis(
            db_session,
            chat_id=chat_id,
            analyzer_version="v0.test+repo",
            analyzed_at=datetime.now(tz=UTC),
            messages_analyzed_up_to="1",
            narrative_markdown="",
            # Intentionally too rich for a skipped row — CHECK should reject.
            structured_extract={"_v": 1, "identity": {"name_guess": "X"}},
            chunks_count=0,
            skipped_reason="not_client",
        )


# ── analysis_chat_analysis_state ────────────────────────────────────────────


async def test_state_upsert_and_read(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat_id(db_session)
    created = await repo.upsert_state(
        db_session,
        chat_id=chat_id,
        stage="chunking",
        chunks_done=0,
        chunks_total=4,
    )
    assert created.stage == "chunking"

    updated = await repo.upsert_state(
        db_session,
        chat_id=chat_id,
        stage="chunk_summaries",
        chunks_done=2,
        chunks_total=4,
        partial_result={"summaries": ["a", "b"]},
    )
    assert updated.stage == "chunk_summaries"
    assert updated.chunks_done == 2
    assert updated.partial_result == {"summaries": ["a", "b"]}

    fetched = await repo.get_state(db_session, chat_id)
    assert fetched is not None
    assert fetched.chunks_done == 2


async def test_state_delete(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat_id(db_session)
    await repo.upsert_state(db_session, chat_id=chat_id, stage="done")
    await repo.delete_state(db_session, chat_id)
    assert await repo.get_state(db_session, chat_id) is None


# ── analysis_pending_order_item ─────────────────────────────────────────────


async def test_pending_order_item_create_list_delete(db_session: AsyncSession) -> None:
    order_id = await _seed_draft_order(db_session)
    a = await repo.create_pending_order_item(
        db_session,
        order_id=order_id,
        items_text="ambiguous thing",
        matching_status=AnalysisPendingMatchingStatus.ambiguous,
        quantity=Decimal("1"),
        unit_price=Decimal("100"),
        currency="RUB",
        candidates=[{"product_id": 1, "confidence_note": "fuzzy"}],
        source_message_ids=["m1", "m2"],
    )
    b = await repo.create_pending_order_item(
        db_session,
        order_id=order_id,
        items_text="missing thing",
        matching_status=AnalysisPendingMatchingStatus.not_found,
    )
    listed = await repo.list_pending_order_items(db_session, order_id)
    assert [r.id for r in listed] == [a.id, b.id]
    assert listed[0].candidates == [{"product_id": 1, "confidence_note": "fuzzy"}]

    await repo.delete_pending_order_items_for_order(db_session, order_id)
    assert await repo.list_pending_order_items(db_session, order_id) == []


# ── analysis_created_entities ───────────────────────────────────────────────


async def test_created_entity_analyzer_requires_source_chat_id(
    db_session: AsyncSession,
) -> None:
    """CHECK constraint from ADR-011 Task 2 migration 208c6dd6037b."""
    with pytest.raises(IntegrityError):
        await repo.record_created_entity(
            db_session,
            analyzer_version="v0.test",
            entity_type="orders_order",
            entity_id=12345,
            created_by="analyzer",
            source_chat_id=None,
        )


async def test_created_entity_operator_may_skip_source_chat_id(
    db_session: AsyncSession,
) -> None:
    row = await repo.record_created_entity(
        db_session,
        analyzer_version="v0.test",
        entity_type="catalog_product",
        entity_id=99,
        created_by="operator",
        source_chat_id=None,
    )
    assert row.id > 0


async def test_created_entities_list_and_delete_filtered(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat_id(db_session)
    await repo.record_created_entity(
        db_session,
        analyzer_version="v0.test+A",
        entity_type="orders_order",
        entity_id=1,
        created_by="analyzer",
        source_chat_id=chat_id,
    )
    await repo.record_created_entity(
        db_session,
        analyzer_version="v0.test+A",
        entity_type="orders_order_item",
        entity_id=2,
        created_by="analyzer",
        source_chat_id=chat_id,
    )
    await repo.record_created_entity(
        db_session,
        analyzer_version="v0.test+B",
        entity_type="orders_order",
        entity_id=3,
        created_by="analyzer",
        source_chat_id=chat_id,
    )

    only_a = await repo.list_created_entities(
        db_session, analyzer_version="v0.test+A", source_chat_id=chat_id
    )
    assert {e.entity_id for e in only_a} == {1, 2}

    deleted = await repo.delete_created_entities(
        db_session,
        analyzer_version="v0.test+A",
        source_chat_id=chat_id,
        created_by="analyzer",
    )
    assert deleted == 2

    # B row untouched
    remaining = await repo.list_created_entities(
        db_session, analyzer_version="v0.test+B", source_chat_id=chat_id
    )
    assert len(remaining) == 1
    assert remaining[0].entity_id == 3
