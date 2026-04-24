"""CRUD over the four tables owned by the ``analysis`` module.

ADR-011 Task 2 — repository layer. All functions work inside the caller's
open transaction: ``flush()`` is allowed, ``commit``/``rollback`` are the
caller's responsibility.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import (
    AnalysisChatAnalysis,
    AnalysisChatAnalysisState,
    AnalysisCreatedEntity,
    AnalysisPendingMatchingStatus,
    AnalysisPendingOrderItem,
)

# ── analysis_chat_analysis ──────────────────────────────────────────────────


async def get_analysis_by_id(
    session: AsyncSession, analysis_id: int
) -> AnalysisChatAnalysis | None:
    return await session.get(AnalysisChatAnalysis, analysis_id)


async def get_analysis_by_chat_and_version(
    session: AsyncSession, chat_id: int, analyzer_version: str
) -> AnalysisChatAnalysis | None:
    stmt = select(AnalysisChatAnalysis).where(
        AnalysisChatAnalysis.chat_id == chat_id,
        AnalysisChatAnalysis.analyzer_version == analyzer_version,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_analyses_for_chat(
    session: AsyncSession, chat_id: int
) -> list[AnalysisChatAnalysis]:
    stmt = (
        select(AnalysisChatAnalysis)
        .where(AnalysisChatAnalysis.chat_id == chat_id)
        .order_by(AnalysisChatAnalysis.analyzed_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def upsert_analysis(
    session: AsyncSession,
    *,
    chat_id: int,
    analyzer_version: str,
    analyzed_at: datetime,
    messages_analyzed_up_to: str,
    narrative_markdown: str,
    structured_extract: dict[str, Any],
    chunks_count: int,
    preflight_classification: str | None = None,
    preflight_confidence: str | None = None,
    preflight_reason: str | None = None,
    skipped_reason: str | None = None,
) -> AnalysisChatAnalysis:
    """Insert or update a row keyed by ``(chat_id, analyzer_version)``.

    Uses PostgreSQL ``INSERT ... ON CONFLICT DO UPDATE`` so callers can repeat
    analysis for the same version without first deleting the old row.
    Returns the refreshed ORM row.
    """
    stmt = (
        pg_insert(AnalysisChatAnalysis)
        .values(
            chat_id=chat_id,
            analyzer_version=analyzer_version,
            analyzed_at=analyzed_at,
            messages_analyzed_up_to=messages_analyzed_up_to,
            narrative_markdown=narrative_markdown,
            structured_extract=structured_extract,
            chunks_count=chunks_count,
            preflight_classification=preflight_classification,
            preflight_confidence=preflight_confidence,
            preflight_reason=preflight_reason,
            skipped_reason=skipped_reason,
        )
        .on_conflict_do_update(
            constraint="uq_analysis_chat_analysis_chat_ver",
            set_={
                "analyzed_at": datetime.now(tz=UTC),
                "messages_analyzed_up_to": messages_analyzed_up_to,
                "narrative_markdown": narrative_markdown,
                "structured_extract": structured_extract,
                "chunks_count": chunks_count,
                "preflight_classification": preflight_classification,
                "preflight_confidence": preflight_confidence,
                "preflight_reason": preflight_reason,
                "skipped_reason": skipped_reason,
            },
        )
        .returning(AnalysisChatAnalysis.id)
    )
    result = await session.execute(stmt)
    row_id = result.scalar_one()
    await session.flush()
    # Use ORM load to return a managed instance with all columns hydrated.
    row = await session.get(AnalysisChatAnalysis, row_id)
    if row is None:  # pragma: no cover — just-inserted row must exist
        raise RuntimeError(
            f"upsert_analysis: row id={row_id} disappeared after insert"
        )
    await session.refresh(row)
    return row


# ── analysis_chat_analysis_state ────────────────────────────────────────────


async def get_state(
    session: AsyncSession, chat_id: int
) -> AnalysisChatAnalysisState | None:
    return await session.get(AnalysisChatAnalysisState, chat_id)


async def upsert_state(
    session: AsyncSession,
    *,
    chat_id: int,
    stage: str,
    chunks_done: int | None = None,
    chunks_total: int | None = None,
    partial_result: dict[str, Any] | None = None,
    failure_reason: str | None = None,
) -> AnalysisChatAnalysisState:
    stmt = (
        pg_insert(AnalysisChatAnalysisState)
        .values(
            chat_id=chat_id,
            stage=stage,
            chunks_done=chunks_done,
            chunks_total=chunks_total,
            partial_result=partial_result,
            failure_reason=failure_reason,
        )
        .on_conflict_do_update(
            index_elements=["chat_id"],
            set_={
                "stage": stage,
                "chunks_done": chunks_done,
                "chunks_total": chunks_total,
                "partial_result": partial_result,
                "failure_reason": failure_reason,
            },
        )
        .returning(AnalysisChatAnalysisState.chat_id)
    )
    await session.execute(stmt)
    await session.flush()
    row = await session.get(AnalysisChatAnalysisState, chat_id)
    if row is None:  # pragma: no cover
        raise RuntimeError(f"upsert_state: chat_id={chat_id} missing after insert")
    await session.refresh(row)
    return row


async def delete_state(session: AsyncSession, chat_id: int) -> None:
    await session.execute(
        delete(AnalysisChatAnalysisState).where(
            AnalysisChatAnalysisState.chat_id == chat_id
        )
    )
    await session.flush()


# ── analysis_pending_order_item ─────────────────────────────────────────────


async def create_pending_order_item(
    session: AsyncSession,
    *,
    order_id: int,
    items_text: str,
    matching_status: AnalysisPendingMatchingStatus,
    quantity: Decimal | None = None,
    unit_price: Decimal | None = None,
    currency: str | None = None,
    candidates: list[dict[str, Any]] | None = None,
    source_message_ids: list[str] | None = None,
) -> AnalysisPendingOrderItem:
    row = AnalysisPendingOrderItem(
        order_id=order_id,
        items_text=items_text,
        quantity=quantity,
        unit_price=unit_price,
        currency=currency,
        matching_status=matching_status,
        candidates=candidates,
        source_message_ids=source_message_ids,
    )
    session.add(row)
    await session.flush()
    return row


async def list_pending_order_items(
    session: AsyncSession, order_id: int
) -> list[AnalysisPendingOrderItem]:
    stmt = (
        select(AnalysisPendingOrderItem)
        .where(AnalysisPendingOrderItem.order_id == order_id)
        .order_by(AnalysisPendingOrderItem.id.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def delete_pending_order_items_for_order(
    session: AsyncSession, order_id: int
) -> None:
    await session.execute(
        delete(AnalysisPendingOrderItem).where(
            AnalysisPendingOrderItem.order_id == order_id
        )
    )
    await session.flush()


# ── analysis_created_entities ───────────────────────────────────────────────


async def record_created_entity(
    session: AsyncSession,
    *,
    analyzer_version: str,
    entity_type: str,
    entity_id: int,
    created_by: str,
    source_chat_id: int | None = None,
) -> AnalysisCreatedEntity:
    row = AnalysisCreatedEntity(
        analyzer_version=analyzer_version,
        source_chat_id=source_chat_id,
        entity_type=entity_type,
        entity_id=entity_id,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row


async def list_created_entities(
    session: AsyncSession,
    *,
    analyzer_version: str | None = None,
    source_chat_id: int | None = None,
    created_by: str | None = None,
    entity_type: str | None = None,
) -> list[AnalysisCreatedEntity]:
    stmt = select(AnalysisCreatedEntity)
    if analyzer_version is not None:
        stmt = stmt.where(AnalysisCreatedEntity.analyzer_version == analyzer_version)
    if source_chat_id is not None:
        stmt = stmt.where(AnalysisCreatedEntity.source_chat_id == source_chat_id)
    if created_by is not None:
        stmt = stmt.where(AnalysisCreatedEntity.created_by == created_by)
    if entity_type is not None:
        stmt = stmt.where(AnalysisCreatedEntity.entity_type == entity_type)
    stmt = stmt.order_by(AnalysisCreatedEntity.id.asc())
    return list((await session.execute(stmt)).scalars())


async def delete_created_entities(
    session: AsyncSession,
    *,
    analyzer_version: str,
    source_chat_id: int,
    created_by: str,
    entity_type: str | None = None,
) -> int:
    """Delete journal rows for a specific analyzer run.

    Returns the number of rows deleted. Used by the force-rollback path in
    ``apply_analysis_to_customer`` and by housekeeping jobs.
    """
    stmt = delete(AnalysisCreatedEntity).where(
        AnalysisCreatedEntity.analyzer_version == analyzer_version,
        AnalysisCreatedEntity.source_chat_id == source_chat_id,
        AnalysisCreatedEntity.created_by == created_by,
    )
    if entity_type is not None:
        stmt = stmt.where(AnalysisCreatedEntity.entity_type == entity_type)
    result = await session.execute(stmt)
    await session.flush()
    # CursorResult exposes rowcount at runtime; Result[Any] does not in the
    # typeshed, hence getattr. The DELETE above always runs against a core
    # CursorResult so the attribute is present.
    return int(getattr(result, "rowcount", 0) or 0)
