"""ADR-011 Task 2: tests for the analysis service layer.

Covers:
- ``record_full_analysis`` / ``record_skipped_analysis`` — including the
  ``{"_v": 1}`` CHECK invariant for skipped rows.
- Progress helpers (``set_stage`` / ``update_chunk_progress`` / ``mark_done``
  / ``mark_failed``).
- ``apply_analysis_to_customer`` — happy path, unlinked chat, ambiguous
  chat (≥2 customers), skipped analysis, dedup of preferences/incidents,
  ``AnalysisAlreadyAppliedError`` and the ``force=True`` rollback path.
- Module-boundary check: ``app.analysis`` never imports
  ``app.orders.models`` directly (ADR-011 §7 decision).
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Side-effect imports: FK targets must be mapped before test setup runs.
import app.catalog.models  # noqa: F401
import app.communications.models  # noqa: F401
import app.orders.models  # noqa: F401
import app.pricing.models  # noqa: F401
from app.analysis import repository as analysis_repo
from app.analysis import service
from app.analysis.exceptions import (
    AnalysisAlreadyAppliedError,
    MultipleCustomersForChatError,
)
from app.analysis.schemas import (
    MatchedOrder,
    MatchedOrderItem,
    MatchedStructuredExtract,
    Preference,
    PreflightClassification,
    ProductCandidate,
    StructuredExtract,
)
from app.communications.service import get_customer_for_chat
from app.orders.models import OrdersCustomerProfile, OrdersOrderStatus
from app.orders.repository import create_customer

# ── seed helpers ────────────────────────────────────────────────────────────


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
                "tg": f"svc-{title}-{datetime.now(tz=UTC).timestamp()}",
                "t": title,
            },
        )
    ).scalar_one()
    await session.flush()
    return int(chat_id)


async def _seed_message(session: AsyncSession, chat_id: int, body: str) -> int:
    msg_id = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_message "
                "(chat_id, telegram_message_id, sent_at, text) "
                "VALUES (:cid, :tmid, :sent, :text) RETURNING id"
            ),
            {
                "cid": chat_id,
                "tmid": f"m-{datetime.now(tz=UTC).timestamp()}",
                "sent": datetime.now(tz=UTC),
                "text": body,
            },
        )
    ).scalar_one()
    await session.flush()
    return int(msg_id)


async def _link_message_to_customer(
    session: AsyncSession, message_id: int, customer_id: int
) -> None:
    await session.execute(
        text(
            "INSERT INTO communications_link "
            "(telegram_message_id, target_module, target_entity, "
            " target_id, link_confidence) "
            "VALUES (:mid, 'orders', 'orders_customer', :tid, 'manual')"
        ),
        {"mid": message_id, "tid": customer_id},
    )
    await session.flush()


async def _link_chat_to_customer(
    session: AsyncSession, chat_id: int, customer_id: int
) -> None:
    """Create a message in the chat and link it to the customer."""
    msg_id = await _seed_message(session, chat_id, f"hello {customer_id}")
    await _link_message_to_customer(session, msg_id, customer_id)


async def _make_customer(session: AsyncSession, suffix: str) -> int:
    c = await create_customer(
        session, name=f"Analysis Svc {suffix}", telegram_id=f"@svc_{suffix}"
    )
    return c.id


# ── record_full_analysis / record_skipped_analysis ─────────────────────────


async def test_record_full_analysis_roundtrip(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, "full")
    extract = MatchedStructuredExtract(schema_version=1)  # type: ignore[call-arg]
    row = await service.record_full_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.svc+full",
        messages_analyzed_up_to="42",
        narrative_markdown="# narrative",
        matched_extract=extract,
        chunks_count=3,
    )
    assert row.chat_id == chat_id
    assert row.narrative_markdown == "# narrative"
    assert row.chunks_count == 3
    assert row.skipped_reason is None
    assert row.structured_extract == {"_v": 1}


async def test_record_full_analysis_propagates_preflight(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "full_pf")
    pf = PreflightClassification(
        classification="client", confidence="high", reason="buying in bulk"
    )
    row = await service.record_full_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.svc+pf",
        messages_analyzed_up_to="42",
        narrative_markdown="n",
        matched_extract=MatchedStructuredExtract(schema_version=1),  # type: ignore[call-arg]
        chunks_count=1,
        preflight=pf,
    )
    assert row.preflight_classification == "client"
    assert row.preflight_confidence == "high"
    assert row.preflight_reason == "buying in bulk"


async def test_record_skipped_analysis_enforces_v1_invariant(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "skip")
    pf = PreflightClassification(
        classification="not_client", confidence="high", reason="just a friend"
    )
    row = await service.record_skipped_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.svc+skip",
        messages_analyzed_up_to="0",
        skipped_reason="not_client",
        preflight=pf,
    )
    # CHECK constraint requires both of these:
    assert row.narrative_markdown == ""
    assert row.structured_extract == {"_v": 1}
    assert row.skipped_reason == "not_client"
    # And preflight fields are mirrored to the columns:
    assert row.preflight_classification == "not_client"


async def test_record_skipped_analysis_v1_matches_empty_structured_extract() -> None:
    """Sanity: the sentinel produced at runtime matches the schema dump."""
    assert StructuredExtract.model_validate({"_v": 1}).model_dump(
        exclude_none=True, by_alias=True
    ) == {"_v": 1}


# ── progress helpers ────────────────────────────────────────────────────────


async def test_set_stage_and_mark_done(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, "stage")
    await service.set_stage(db_session, chat_id=chat_id, stage="chunking")
    state = await analysis_repo.get_state(db_session, chat_id)
    assert state is not None
    assert state.stage == "chunking"

    await service.mark_done(db_session, chat_id=chat_id)
    assert await analysis_repo.get_state(db_session, chat_id) is None


async def test_update_chunk_progress(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, "chunk")
    await service.update_chunk_progress(
        db_session,
        chat_id=chat_id,
        chunks_done=1,
        chunks_total=5,
        partial_result={"summaries": ["a"]},
    )
    state = await analysis_repo.get_state(db_session, chat_id)
    assert state is not None
    assert state.stage == "chunk_summaries"
    assert state.chunks_done == 1
    assert state.chunks_total == 5
    assert state.partial_result == {"summaries": ["a"]}


async def test_mark_failed(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, "fail")
    await service.mark_failed(
        db_session, chat_id=chat_id, failure_reason="LM Studio 500"
    )
    state = await analysis_repo.get_state(db_session, chat_id)
    assert state is not None
    assert state.stage == "failed"
    assert state.failure_reason == "LM Studio 500"


# ── get_customer_for_chat ───────────────────────────────────────────────────


async def test_get_customer_for_chat_unlinked_returns_none(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "unlinked")
    assert await get_customer_for_chat(db_session, chat_id) is None


async def test_get_customer_for_chat_single_link(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "single")
    cid = await _make_customer(db_session, "single")
    await _link_chat_to_customer(db_session, chat_id, cid)
    assert await get_customer_for_chat(db_session, chat_id) == cid


async def test_get_customer_for_chat_multiple_raises(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "multi")
    c1 = await _make_customer(db_session, "m1")
    c2 = await _make_customer(db_session, "m2")
    await _link_chat_to_customer(db_session, chat_id, c1)
    await _link_chat_to_customer(db_session, chat_id, c2)
    with pytest.raises(MultipleCustomersForChatError) as exc:
        await get_customer_for_chat(db_session, chat_id)
    assert set(exc.value.customer_ids) == {c1, c2}
    assert exc.value.chat_id == chat_id


# ── apply_analysis_to_customer — happy path ─────────────────────────────────


async def _archive_analysis(
    session: AsyncSession,
    chat_id: int,
    *,
    extract: MatchedStructuredExtract,
    analyzer_version: str = "v0.svc+apply",
) -> int:
    row = await service.record_full_analysis(
        session,
        chat_id=chat_id,
        analyzer_version=analyzer_version,
        messages_analyzed_up_to="1",
        narrative_markdown="n",
        matched_extract=extract,
        chunks_count=1,
    )
    return row.id


async def test_apply_analysis_happy_path(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, "apply_ok")
    customer_id = await _make_customer(db_session, "apply_ok")
    await _link_chat_to_customer(db_session, chat_id, customer_id)

    pid = await _seeded_product_id(db_session)
    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        preferences=[
            Preference(
                product_hint="Veritas",
                note="любит",
                source_message_ids=["m-1"],
            ),
        ],
        orders=[
            MatchedOrder(
                description="past order",
                items=[
                    MatchedOrderItem(
                        items_text="Veritas 05P44",
                        quantity=Decimal("2"),
                        unit_price=Decimal("100"),
                        currency="RUB",
                        matching_status="confident_match",
                        matched_product_id=pid,
                        source_message_ids=["m-2"],
                    ),
                    MatchedOrderItem(
                        items_text="mystery plane",
                        matching_status="ambiguous",
                        candidates=[
                            ProductCandidate(
                                product_id=pid, confidence_note="fuzzy"
                            ),
                        ],
                        source_message_ids=["m-3"],
                    ),
                    MatchedOrderItem(
                        items_text="unicorn product",
                        matching_status="not_found",
                        not_found_reason="no catalog match",
                    ),
                ],
            )
        ],
    )
    analysis_id = await _archive_analysis(db_session, chat_id, extract=extract)

    result = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    assert result.customer_id == customer_id
    assert result.orders_created == 1
    assert result.order_items_created == 1
    assert result.pending_items_created == 2
    assert result.preferences_added == 1
    assert result.incidents_added == 0
    assert result.delivery_preferences_updated is False
    assert result.ambiguous_customer_ids is None

    # Journal: 1 order + 1 confident_match item — pending items are NOT
    # journaled, per ADR-011 §8.
    journal = await analysis_repo.list_created_entities(
        db_session,
        analyzer_version="v0.svc+apply",
        source_chat_id=chat_id,
    )
    assert {e.entity_type for e in journal} == {"orders_order", "orders_order_item"}
    assert len(journal) == 2


async def test_apply_analysis_updates_delivery_and_incidents(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "apply_delivery")
    customer_id = await _make_customer(db_session, "apply_delivery")
    await _link_chat_to_customer(db_session, chat_id, customer_id)

    extract = MatchedStructuredExtract.model_validate(
        {
            "_v": 1,
            "delivery_preferences": {"method": "СДЭК", "preferred_time": "вечер"},
            "incidents": [
                {
                    "date": "2025-03-15",
                    "summary": "царапина",
                    "resolved": True,
                    "source_message_ids": ["m-100"],
                }
            ],
        }
    )
    analysis_id = await _archive_analysis(db_session, chat_id, extract=extract)
    result = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    assert result.delivery_preferences_updated is True
    assert result.incidents_added == 1
    assert result.orders_created == 0


# ── apply_analysis_to_customer — edge cases ─────────────────────────────────


async def test_apply_analysis_unlinked_chat_returns_empty_result(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "apply_unlinked")
    analysis_id = await _archive_analysis(
        db_session, chat_id, extract=MatchedStructuredExtract(schema_version=1)  # type: ignore[call-arg]
    )
    result = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    assert result.customer_id is None
    assert result.ambiguous_customer_ids is None
    assert result.orders_created == 0
    assert result.preferences_added == 0


async def test_apply_analysis_ambiguous_chat_returns_ids(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "apply_ambiguous")
    c1 = await _make_customer(db_session, "amb1")
    c2 = await _make_customer(db_session, "amb2")
    await _link_chat_to_customer(db_session, chat_id, c1)
    await _link_chat_to_customer(db_session, chat_id, c2)

    analysis_id = await _archive_analysis(
        db_session,
        chat_id,
        extract=MatchedStructuredExtract(  # type: ignore[call-arg]
            schema_version=1,
            preferences=[Preference(product_hint="x", source_message_ids=["m-1"])],
        ),
    )
    result = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    assert result.customer_id is None
    assert result.ambiguous_customer_ids is not None
    assert set(result.ambiguous_customer_ids) == {c1, c2}
    assert result.orders_created == 0
    assert result.preferences_added == 0  # nothing written because ambiguous


async def test_apply_analysis_skipped_row_is_noop(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "apply_skipped")
    customer_id = await _make_customer(db_session, "apply_skipped")
    await _link_chat_to_customer(db_session, chat_id, customer_id)

    pf = PreflightClassification(
        classification="not_client", confidence="high", reason="friend"
    )
    row = await service.record_skipped_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version="v0.svc+skip_apply",
        messages_analyzed_up_to="0",
        skipped_reason="not_client",
        preflight=pf,
    )
    result = await service.apply_analysis_to_customer(
        db_session, analysis_id=row.id
    )
    assert result.customer_id == customer_id
    assert result.orders_created == 0
    assert result.preferences_added == 0
    assert result.delivery_preferences_updated is False


async def test_apply_analysis_dedups_preferences_by_source_message_ids(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "apply_dedup")
    customer_id = await _make_customer(db_session, "apply_dedup")
    await _link_chat_to_customer(db_session, chat_id, customer_id)

    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        preferences=[
            Preference(product_hint="A", source_message_ids=["m-1"]),
            Preference(product_hint="B", source_message_ids=["m-2"]),
        ],
    )
    analysis_id = await _archive_analysis(
        db_session,
        chat_id,
        extract=extract,
        analyzer_version="v0.svc+dedup-1",
    )
    r1 = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    assert r1.preferences_added == 2

    # Second analysis carrying one new fingerprint and one duplicate.
    extract2 = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        preferences=[
            Preference(product_hint="A-again", source_message_ids=["m-1"]),
            Preference(product_hint="C", source_message_ids=["m-9"]),
        ],
    )
    analysis_id2 = await _archive_analysis(
        db_session,
        chat_id,
        extract=extract2,
        analyzer_version="v0.svc+dedup-2",
    )
    r2 = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id2
    )
    assert r2.preferences_added == 1  # only the "C/m-9" element

    await db_session.flush()
    profile_prefs = (
        await db_session.execute(
            text("SELECT preferences FROM orders_customer_profile WHERE customer_id = :cid"),
            {"cid": customer_id},
        )
    ).scalar_one()
    assert profile_prefs is not None
    hints = {p["product_hint"] for p in profile_prefs}
    assert hints == {"A", "B", "C"}


# ── AnalysisAlreadyAppliedError / force=True ────────────────────────────────


async def test_apply_analysis_already_applied_raises_without_force(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "already_applied")
    customer_id = await _make_customer(db_session, "already_applied")
    await _link_chat_to_customer(db_session, chat_id, customer_id)

    pid = await _seeded_product_id(db_session)
    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        orders=[
            MatchedOrder(
                items=[
                    MatchedOrderItem(
                        items_text="x",
                        quantity=Decimal("1"),
                        matching_status="confident_match",
                        matched_product_id=pid,
                    )
                ]
            )
        ],
    )
    analysis_id = await _archive_analysis(
        db_session, chat_id, extract=extract, analyzer_version="v0.svc+twice"
    )
    await service.apply_analysis_to_customer(db_session, analysis_id=analysis_id)

    with pytest.raises(AnalysisAlreadyAppliedError) as exc:
        await service.apply_analysis_to_customer(
            db_session, analysis_id=analysis_id
        )
    assert exc.value.analyzer_version == "v0.svc+twice"
    assert exc.value.chat_id == chat_id
    assert exc.value.existing_entity_count >= 1


async def test_apply_analysis_force_rolls_back_and_reapplies(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "force_apply")
    customer_id = await _make_customer(db_session, "force_apply")
    await _link_chat_to_customer(db_session, chat_id, customer_id)

    pid = await _seeded_product_id(db_session)
    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        orders=[
            MatchedOrder(
                items=[
                    MatchedOrderItem(
                        items_text="x",
                        quantity=Decimal("1"),
                        matching_status="confident_match",
                        matched_product_id=pid,
                    )
                ]
            )
        ],
    )
    analysis_id = await _archive_analysis(
        db_session, chat_id, extract=extract, analyzer_version="v0.svc+force"
    )
    first = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    assert first.orders_created == 1
    assert first.rolled_back_count == 0

    second = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id, force=True
    )
    # Prior 2 journal rows (order + item) were rolled back before re-apply.
    assert second.rolled_back_count == 2
    assert second.orders_created == 1
    assert second.order_items_created == 1

    journal = await analysis_repo.list_created_entities(
        db_session,
        analyzer_version="v0.svc+force",
        source_chat_id=chat_id,
    )
    # After force+re-apply only the newest pair remains (prior rows gone).
    assert len(journal) == 2


# ── profile exists across re-applies ────────────────────────────────────────


async def test_apply_analysis_uses_existing_profile(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "existing_profile")
    customer_id = await _make_customer(db_session, "existing_profile")
    await _link_chat_to_customer(db_session, chat_id, customer_id)
    # Pre-seed the profile with manual preferences — they must survive.
    profile = OrdersCustomerProfile(
        customer_id=customer_id,
        preferences=[{"product_hint": "manual entry", "confidence": "manual"}],
    )
    db_session.add(profile)
    await db_session.flush()

    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        preferences=[
            Preference(product_hint="auto", source_message_ids=["m-11"]),
        ],
    )
    analysis_id = await _archive_analysis(
        db_session, chat_id, extract=extract, analyzer_version="v0.svc+profile"
    )
    result = await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    assert result.preferences_added == 1

    await db_session.flush()
    refreshed = (
        await db_session.execute(
            text("SELECT preferences FROM orders_customer_profile WHERE customer_id = :cid"),
            {"cid": customer_id},
        )
    ).scalar_one()
    hints = {p["product_hint"] for p in refreshed}
    assert hints == {"manual entry", "auto"}


# ── module boundary (ADR-011 §7) ────────────────────────────────────────────


def test_analysis_module_does_not_import_orders_models() -> None:
    """No file under app/analysis imports app.orders.models.

    Writes to ``orders_*`` tables must go through ``app.orders.service``
    helpers — this guards the module boundary declared in ADR-001 v2.
    """
    offenders: list[tuple[str, int]] = []
    analysis_dir = Path(__file__).resolve().parents[2] / "app" / "analysis"
    for py_file in analysis_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == "app.orders.models":
                    offenders.append((str(py_file), node.lineno))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.orders.models":
                        offenders.append((str(py_file), node.lineno))
    assert offenders == [], f"analysis imports orders.models: {offenders}"


async def test_apply_analysis_missing_analysis_raises(
    db_session: AsyncSession,
) -> None:
    with pytest.raises(ValueError, match="not found"):
        await service.apply_analysis_to_customer(
            db_session, analysis_id=999_999_999
        )


# ── order currency inherited from draft, not argument ───────────────────────


async def test_apply_creates_draft_orders_with_status_draft(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "draft_check")
    customer_id = await _make_customer(db_session, "draft_check")
    await _link_chat_to_customer(db_session, chat_id, customer_id)

    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        orders=[
            MatchedOrder(
                description="d",
                items=[
                    MatchedOrderItem(
                        items_text="mystery",
                        matching_status="not_found",
                    )
                ],
            )
        ],
    )
    analysis_id = await _archive_analysis(
        db_session, chat_id, extract=extract, analyzer_version="v0.svc+draft"
    )
    await service.apply_analysis_to_customer(
        db_session, analysis_id=analysis_id
    )
    # The created order should be a draft.
    journal = await analysis_repo.list_created_entities(
        db_session,
        analyzer_version="v0.svc+draft",
        source_chat_id=chat_id,
        entity_type="orders_order",
    )
    assert len(journal) == 1
    order_status = (
        await db_session.execute(
            text("SELECT status FROM orders_order WHERE id = :oid"),
            {"oid": journal[0].entity_id},
        )
    ).scalar_one()
    assert order_status == OrdersOrderStatus.draft.value


# ── Test #31: transaction boundary (service must NOT commit/rollback) ──────
#
# The project-wide contract (ADR-001 v2 + standing practice since ADR-013
# Task 2) is: module services flush() but never commit()/rollback(). Testing
# it via the conftest rollback doesn't cover the case where the service
# itself calls commit — after commit there's nothing to rollback, and the
# data leaks. The proxy below counts calls directly.


async def test_service_does_not_commit_or_rollback(
    db_session: AsyncSession,
) -> None:
    """``record_full_analysis`` + ``apply_analysis_to_customer`` (happy path)
    must not call ``session.commit()`` / ``session.rollback()`` — those
    belong to the caller (MCP tool, run.py, etc.)."""
    commits: list[None] = []
    rollbacks: list[None] = []

    real_commit = db_session.commit
    real_rollback = db_session.rollback

    async def counting_commit() -> None:
        commits.append(None)
        await real_commit()

    async def counting_rollback() -> None:
        rollbacks.append(None)
        await real_rollback()

    db_session.commit = counting_commit  # type: ignore[method-assign]
    db_session.rollback = counting_rollback  # type: ignore[method-assign]
    try:
        chat_id = await _seed_chat(db_session, "tx_boundary")
        customer_id = await _make_customer(db_session, "tx_boundary")
        await _link_chat_to_customer(db_session, chat_id, customer_id)
        pid = await _seeded_product_id(db_session)

        extract = MatchedStructuredExtract(  # type: ignore[call-arg]
            schema_version=1,
            preferences=[
                Preference(product_hint="X", source_message_ids=["m-tx-1"]),
            ],
            orders=[
                MatchedOrder(
                    items=[
                        MatchedOrderItem(
                            items_text="x",
                            quantity=Decimal("1"),
                            matching_status="confident_match",
                            matched_product_id=pid,
                        ),
                    ],
                )
            ],
        )
        row = await service.record_full_analysis(
            db_session,
            chat_id=chat_id,
            analyzer_version="v0.svc+tx",
            messages_analyzed_up_to="1",
            narrative_markdown="n",
            matched_extract=extract,
            chunks_count=1,
        )
        await service.apply_analysis_to_customer(
            db_session, analysis_id=row.id
        )
    finally:
        db_session.commit = real_commit  # type: ignore[method-assign]
        db_session.rollback = real_rollback  # type: ignore[method-assign]

    assert commits == [], f"service.commit called {len(commits)} time(s)"
    assert rollbacks == [], f"service.rollback called {len(rollbacks)} time(s)"


async def test_orders_helpers_do_not_commit_or_rollback(
    db_session: AsyncSession,
) -> None:
    """The 6 ADR-011-facing helpers in ``app/orders/service.py`` share the
    same transaction-boundary contract. A smaller guard here catches
    regressions on the orders side without duplicating the full scenario."""
    from app.orders.service import (
        append_preference_in_locked_profile,
        create_draft_order,
        get_or_create_profile_for_update,
    )

    commits: list[None] = []
    rollbacks: list[None] = []
    real_commit = db_session.commit
    real_rollback = db_session.rollback

    async def counting_commit() -> None:
        commits.append(None)
        await real_commit()

    async def counting_rollback() -> None:
        rollbacks.append(None)
        await real_rollback()

    db_session.commit = counting_commit  # type: ignore[method-assign]
    db_session.rollback = counting_rollback  # type: ignore[method-assign]
    try:
        customer_id = await _make_customer(db_session, "orders_tx")
        profile = await get_or_create_profile_for_update(db_session, customer_id)
        await append_preference_in_locked_profile(
            db_session,
            profile,
            {"product_hint": "Z", "source_message_ids": ["m-z"]},
            confidence="suggested",
        )
        await create_draft_order(
            db_session, customer_id, items=[], origin="analysis"
        )
    finally:
        db_session.commit = real_commit  # type: ignore[method-assign]
        db_session.rollback = real_rollback  # type: ignore[method-assign]

    assert commits == []
    assert rollbacks == []


# ── Test #18: FOR UPDATE lock serialises concurrent apply ──────────────────
#
# ``get_or_create_profile_for_update`` uses ``SELECT … FOR UPDATE`` to
# serialise two parallel analyzer runs targeting the same customer's
# profile — otherwise dedup counters (preferences, incidents) would race.
# This test drives two concurrent sessions through the same profile and
# asserts the second acquisition was blocked long enough for contention
# to be observable. Requires real PostgreSQL (SQLite has no row locks).


@pytest.mark.requires_postgres
async def test_profile_lock_serializes_concurrent_apply(
    db_session: AsyncSession,
) -> None:
    """Concurrency: two apply calls for the same customer serialise on the
    profile row lock — the second waits for the first to commit/rollback.

    The first session holds the FOR UPDATE lock for ``hold_seconds`` seconds
    before rolling back; the second session's lock acquisition must take
    at least close to that duration. A relaxed lower bound (80 % of the
    hold duration) absorbs scheduler jitter.
    """
    import asyncio
    import time

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.config import settings

    # Seed a customer *and* its profile on the test's own session so both
    # workers only need to LOCK (not insert) the profile. Pre-creating the
    # profile is critical: otherwise the two workers race on the UNIQUE
    # constraint for ``customer_id``, which serialises them too — and the
    # test would pass even if ``.with_for_update()`` were missing. We must
    # commit so other connections can see the seed.
    customer_id = await _make_customer(db_session, "lockrace")
    profile = OrdersCustomerProfile(customer_id=customer_id)
    db_session.add(profile)
    await db_session.commit()

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    hold_seconds = 0.4
    first_holding = asyncio.Event()
    second_done = asyncio.Event()

    from app.orders.service import get_or_create_profile_for_update

    async def worker_first() -> float:
        async with maker() as s:
            t0 = time.monotonic()
            await get_or_create_profile_for_update(s, customer_id)
            first_holding.set()
            # Hold the lock, then release by rollback.
            await asyncio.sleep(hold_seconds)
            await s.rollback()
            return time.monotonic() - t0

    async def worker_second() -> float:
        # Start only after worker_first has taken the lock.
        await first_holding.wait()
        async with maker() as s:
            t0 = time.monotonic()
            await get_or_create_profile_for_update(s, customer_id)
            elapsed = time.monotonic() - t0
            await s.rollback()
            second_done.set()
            return elapsed

    try:
        elapsed_first, elapsed_second = await asyncio.gather(
            worker_first(), worker_second()
        )
    finally:
        # Clean up the committed seed row.
        async with maker() as cleanup:
            await cleanup.execute(
                text("DELETE FROM orders_customer_profile WHERE customer_id = :cid"),
                {"cid": customer_id},
            )
            await cleanup.execute(
                text("DELETE FROM orders_customer WHERE id = :cid"),
                {"cid": customer_id},
            )
            await cleanup.commit()
        await engine.dispose()

    assert elapsed_second >= hold_seconds * 0.8, (
        f"no lock contention observed: "
        f"first={elapsed_first:.3f}s, second={elapsed_second:.3f}s "
        f"(expected second >= {hold_seconds * 0.8:.3f}s)"
    )
