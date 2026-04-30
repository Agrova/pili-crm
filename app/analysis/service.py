"""Public service layer for the ``analysis`` module (ADR-011 Task 2).

Three operational surfaces:

1. **Recording analyzer output.** ``record_full_analysis`` and
   ``record_skipped_analysis`` upsert the ``analysis_chat_analysis`` row
   that is the authoritative archive of a given analyzer-run against a
   chat.
2. **Applying results to active tables.** ``apply_analysis_to_customer``
   reads the most recent analysis for a chat and writes results into
   ``orders_customer_profile`` / ``orders_order`` per ADR-011 §7. The
   chat→customer lookup goes through ``communications_link`` (not a
   direct FK on ``communications_telegram_chat``) — see
   ``app.communications.service.get_customer_for_chat``.
3. **Progress tracking.** ``set_stage`` / ``update_chunk_progress`` /
   ``mark_done`` / ``mark_failed`` manipulate
   ``analysis_chat_analysis_state`` for resumable runs (Task 3 will call
   them from ``analysis/run.py``).

Transaction boundary: every function in this module works inside the
caller's open transaction. ``flush()`` is allowed; ``commit`` and
``rollback`` are the caller's responsibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import repository
from app.analysis.exceptions import (
    AnalysisAlreadyAppliedError,
    MultipleCustomersForChatError,
)
from app.analysis.identity_service import (
    auto_apply_safe_identity_updates,
    extract_identity_to_quarantine,
)
from app.analysis.models import (
    AnalysisChatAnalysis,
    AnalysisChatAnalysisState,
    AnalysisPendingMatchingStatus,
)
from app.analysis.schemas import (
    MatchedOrder,
    MatchedStructuredExtract,
    PreflightClassification,
    SkippedReason,
    StructuredExtract,
)
from app.communications.service import get_customer_for_chat
from app.orders.service import (
    add_order_item,
    append_incident_in_locked_profile,
    append_preference_in_locked_profile,
    create_draft_order,
    get_or_create_profile_for_update,
    upsert_delivery_preferences_in_locked_profile,
)

# ── Result dataclass ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnalysisApplicationResult:
    """Outcome of ``apply_analysis_to_customer`` (ADR-011 §7).

    ``customer_id`` is populated only when exactly one customer links to the
    chat. ``ambiguous_customer_ids`` is populated (and everything else stays
    zero) when the chat is linked to ≥2 customers — the operator is expected
    to disambiguate manually.
    """

    analysis_id: int
    customer_id: int | None
    orders_created: int = 0
    orders_filtered_historical: int = 0
    order_items_created: int = 0
    pending_items_created: int = 0
    preferences_added: int = 0
    incidents_added: int = 0
    delivery_preferences_updated: bool = False
    rolled_back_count: int = 0
    ambiguous_customer_ids: list[int] | None = None
    identities_quarantined: int = 0
    identities_auto_applied: int = 0
    identities_kept_pending: int = 0


# ── Helpers ─────────────────────────────────────────────────────────────────


def _is_actionable_order(order: MatchedOrder) -> bool:
    """Return True if the order should be materialised as a draft in orders_order.

    ADR-017: orders without items are historical mentions; terminal delivery
    statuses mean the order is already closed.
    """
    if not order.items:
        return False
    return order.status_delivery not in {"delivered", "returned"}


# ── Recording analyzer output ───────────────────────────────────────────────


async def record_full_analysis(
    session: AsyncSession,
    *,
    chat_id: int,
    analyzer_version: str,
    messages_analyzed_up_to: str,
    narrative_markdown: str,
    matched_extract: MatchedStructuredExtract,
    chunks_count: int,
    preflight: PreflightClassification | None = None,
) -> AnalysisChatAnalysis:
    """Archive a complete analyzer run (narrative + matched extract).

    ``preflight`` is optional — present only when ADR-013 preflight ran
    before full analysis. When supplied its three columns are populated so
    downstream dashboards can correlate preflight verdict with full result.
    """
    return await repository.upsert_analysis(
        session,
        chat_id=chat_id,
        analyzer_version=analyzer_version,
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to=messages_analyzed_up_to,
        narrative_markdown=narrative_markdown,
        structured_extract=matched_extract.model_dump(
            exclude_none=True, by_alias=True, mode="json"
        ),
        chunks_count=chunks_count,
        preflight_classification=(
            preflight.classification if preflight is not None else None
        ),
        preflight_confidence=(
            preflight.confidence if preflight is not None else None
        ),
        preflight_reason=(preflight.reason if preflight is not None else None),
        skipped_reason=None,
    )


async def record_skipped_analysis(
    session: AsyncSession,
    *,
    chat_id: int,
    analyzer_version: str,
    messages_analyzed_up_to: str,
    skipped_reason: SkippedReason,
    preflight: PreflightClassification,
) -> AnalysisChatAnalysis:
    """Archive a preflight-skipped chat (ADR-013 ``not_client`` / ``empty``).

    ``narrative_markdown`` is forced to the empty string and
    ``structured_extract`` to ``{"_v": 1}`` so the CHECK constraint
    ``ck_analysis_chat_analysis_skipped_consistency`` is satisfied. The
    ``{"_v": 1}`` invariant is produced by
    ``StructuredExtract(schema_version=1).model_dump(exclude_none=True,
    by_alias=True)`` — see ``tests/analysis/test_schemas.py``.
    """
    empty_extract = StructuredExtract.model_validate({"_v": 1}).model_dump(
        exclude_none=True, by_alias=True
    )
    return await repository.upsert_analysis(
        session,
        chat_id=chat_id,
        analyzer_version=analyzer_version,
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to=messages_analyzed_up_to,
        narrative_markdown="",
        structured_extract=empty_extract,
        chunks_count=0,
        preflight_classification=preflight.classification,
        preflight_confidence=preflight.confidence,
        preflight_reason=preflight.reason,
        skipped_reason=skipped_reason,
    )


# ── Progress tracking (analysis_chat_analysis_state) ────────────────────────


async def set_stage(
    session: AsyncSession,
    *,
    chat_id: int,
    stage: str,
    partial_result: dict[str, Any] | None = None,
) -> AnalysisChatAnalysisState:
    return await repository.upsert_state(
        session,
        chat_id=chat_id,
        stage=stage,
        partial_result=partial_result,
    )


async def update_chunk_progress(
    session: AsyncSession,
    *,
    chat_id: int,
    chunks_done: int,
    chunks_total: int,
    partial_result: dict[str, Any] | None = None,
) -> AnalysisChatAnalysisState:
    return await repository.upsert_state(
        session,
        chat_id=chat_id,
        stage="chunk_summaries",
        chunks_done=chunks_done,
        chunks_total=chunks_total,
        partial_result=partial_result,
    )


async def mark_done(session: AsyncSession, *, chat_id: int) -> None:
    """Finished chat: state row is deleted (ADR-011 §9)."""
    await repository.delete_state(session, chat_id)


async def mark_failed(
    session: AsyncSession, *, chat_id: int, failure_reason: str
) -> AnalysisChatAnalysisState:
    return await repository.upsert_state(
        session,
        chat_id=chat_id,
        stage="failed",
        failure_reason=failure_reason,
    )


# ── Applying analyzer output to active tables (ADR-011 §7) ──────────────────


def _source_ids_fingerprint(element: dict[str, Any]) -> frozenset[str] | None:
    ids = element.get("source_message_ids")
    if isinstance(ids, list) and ids:
        return frozenset(str(i) for i in ids)
    return None


def _existing_fingerprints(
    rows: list[dict[str, Any]] | None,
) -> set[frozenset[str]]:
    if not rows:
        return set()
    out: set[frozenset[str]] = set()
    for row in rows:
        fp = _source_ids_fingerprint(row)
        if fp is not None:
            out.add(fp)
    return out


async def apply_analysis_to_customer(
    session: AsyncSession,
    *,
    analysis_id: int,
    force: bool = False,
) -> AnalysisApplicationResult:
    """Apply a stored analysis to the customer linked to its chat (ADR-011 §7).

    Contract decisions (Phase 1 / Phase 2 approved by operator):

    - **Chat → customer** via ``communications_link`` (polymorphic table).
      ``communications_telegram_chat`` has no ``customer_id`` column —
      linkage is message-level (ADR-003 / ADR-010). See
      ``get_customer_for_chat``.
    - **≥2 linked customers** → ``MultipleCustomersForChatError`` is caught
      here; the result is returned with ``ambiguous_customer_ids`` populated
      and all counters at zero.
    - **Already-applied runs** raise ``AnalysisAlreadyAppliedError`` unless
      ``force=True``. Re-application blocked because draft-order creation
      is not idempotent (profile appends are dedup'd by
      ``source_message_ids``, orders are not). ``force=True`` rolls back the
      prior analyzer-created journal rows first.
    - **Pending items** (``ambiguous`` / ``not_found``) are *not* journaled
      in ``analysis_created_entities`` — they are operator-facing drafts,
      not created catalog entities.
    """
    analysis = await repository.get_analysis_by_id(session, analysis_id)
    if analysis is None:
        raise ValueError(f"Analysis {analysis_id} not found")

    chat_id = analysis.chat_id
    analyzer_version = analysis.analyzer_version

    try:
        customer_id = await get_customer_for_chat(session, chat_id)
    except MultipleCustomersForChatError as exc:
        # ≥2 linked customers — identity attribution is operator-only.
        # The analysis row stays for re-apply once the operator
        # disambiguates the link.
        return AnalysisApplicationResult(
            analysis_id=analysis_id,
            customer_id=None,
            ambiguous_customer_ids=exc.customer_ids,
        )

    # Force/already-applied check only when there is a customer to apply to.
    # An unreviewed chat (customer_id=None) can't have existing journal
    # rows for this (analyzer_version, chat_id) since nothing was applied.
    rolled_back_count = 0
    if customer_id is not None:
        existing_entries = await repository.list_created_entities(
            session,
            analyzer_version=analyzer_version,
            source_chat_id=chat_id,
            created_by="analyzer",
        )
        if existing_entries:
            if not force:
                raise AnalysisAlreadyAppliedError(
                    analysis_id=analysis_id,
                    analyzer_version=analyzer_version,
                    chat_id=chat_id,
                    existing_entity_count=len(existing_entries),
                )
            rolled_back_count = await repository.delete_created_entities(
                session,
                analyzer_version=analyzer_version,
                source_chat_id=chat_id,
                created_by="analyzer",
            )

    # Skipped analyses have nothing to apply — the structured_extract is the
    # ``{"_v": 1}`` sentinel, narrative is empty, identity is absent.
    if analysis.skipped_reason is not None:
        return AnalysisApplicationResult(
            analysis_id=analysis_id,
            customer_id=customer_id,
            rolled_back_count=rolled_back_count,
        )

    extract = MatchedStructuredExtract.model_validate(analysis.structured_extract)

    # Identity quarantine (ADR-011 X1). Runs before the unreviewed-chat
    # early-return so identity from a customer-less chat is still saved
    # (with customer_id=NULL) — operator binds it to a customer later.
    identities_quarantined = 0
    identities_auto_applied = 0
    identities_kept_pending = 0
    if extract.identity is not None:
        identity_data = extract.identity.model_dump(exclude_none=True)
        if identity_data:
            extracted_ids = await extract_identity_to_quarantine(
                session,
                chat_id=chat_id,
                customer_id=customer_id,
                analyzer_version=analyzer_version,
                identity_data=identity_data,
            )
            identities_quarantined = len(extracted_ids)
            if customer_id is not None and extracted_ids:
                metrics = await auto_apply_safe_identity_updates(
                    session,
                    customer_id=customer_id,
                    extracted_ids=extracted_ids,
                )
                identities_auto_applied = metrics["auto_applied"]
                identities_kept_pending = metrics["kept_pending"]
            else:
                identities_kept_pending = identities_quarantined

    # Unreviewed chat: identity is parked, no profile/orders to write.
    if customer_id is None:
        return AnalysisApplicationResult(
            analysis_id=analysis_id,
            customer_id=None,
            identities_quarantined=identities_quarantined,
            identities_kept_pending=identities_kept_pending,
        )

    profile = await get_or_create_profile_for_update(session, customer_id)

    preferences_added = 0
    if extract.preferences:
        existing_prefs_fps = _existing_fingerprints(profile.preferences)
        for pref in extract.preferences:
            element = pref.model_dump(exclude_none=True, by_alias=True, mode="json")
            fp = _source_ids_fingerprint(element)
            if fp is not None and fp in existing_prefs_fps:
                continue
            await append_preference_in_locked_profile(
                session, profile, element, confidence="suggested"
            )
            if fp is not None:
                existing_prefs_fps.add(fp)
            preferences_added += 1

    incidents_added = 0
    if extract.incidents:
        existing_inc_fps = _existing_fingerprints(profile.incidents)
        for inc in extract.incidents:
            element = inc.model_dump(exclude_none=True, by_alias=True, mode="json")
            fp = _source_ids_fingerprint(element)
            if fp is not None and fp in existing_inc_fps:
                continue
            await append_incident_in_locked_profile(
                session, profile, element, confidence="suggested"
            )
            if fp is not None:
                existing_inc_fps.add(fp)
            incidents_added += 1

    delivery_updated = False
    if extract.delivery_preferences is not None:
        dp = extract.delivery_preferences.model_dump(
            exclude_none=True, by_alias=True, mode="json"
        )
        if dp:  # skip empty dict (all fields None)
            await upsert_delivery_preferences_in_locked_profile(
                session, profile, dp, confidence="suggested"
            )
            delivery_updated = True

    orders_created = 0
    orders_filtered_historical = 0
    order_items_created = 0
    pending_items_created = 0

    if extract.orders:
        for matched_order in extract.orders:
            if not _is_actionable_order(matched_order):
                orders_filtered_historical += 1
                continue
            order = await create_draft_order(
                session, customer_id, items=[], origin="analysis"
            )
            orders_created += 1
            await repository.record_created_entity(
                session,
                analyzer_version=analyzer_version,
                source_chat_id=chat_id,
                entity_type="orders_order",
                entity_id=order.id,
                created_by="analyzer",
            )
            if not matched_order.items:
                continue
            for matched_item in matched_order.items:
                status = matched_item.matching_status
                qty = matched_item.quantity or Decimal("1")
                if status == "confident_match":
                    assert matched_item.matched_product_id is not None
                    item = await add_order_item(
                        session,
                        order_id=order.id,
                        product_id=matched_item.matched_product_id,
                        quantity=qty,
                        unit_price=matched_item.unit_price,
                        currency=matched_item.currency,
                    )
                    order_items_created += 1
                    await repository.record_created_entity(
                        session,
                        analyzer_version=analyzer_version,
                        source_chat_id=chat_id,
                        entity_type="orders_order_item",
                        entity_id=item.id,
                        created_by="analyzer",
                    )
                else:
                    candidates_dump = (
                        [
                            c.model_dump(exclude_none=True, mode="json")
                            for c in matched_item.candidates
                        ]
                        if matched_item.candidates
                        else None
                    )
                    pending_status = (
                        AnalysisPendingMatchingStatus.ambiguous
                        if status == "ambiguous"
                        else AnalysisPendingMatchingStatus.not_found
                    )
                    await repository.create_pending_order_item(
                        session,
                        order_id=order.id,
                        items_text=matched_item.items_text or "",
                        matching_status=pending_status,
                        quantity=matched_item.quantity,
                        unit_price=matched_item.unit_price,
                        currency=matched_item.currency,
                        candidates=candidates_dump,
                        source_message_ids=matched_item.source_message_ids,
                    )
                    pending_items_created += 1

    return AnalysisApplicationResult(
        analysis_id=analysis_id,
        customer_id=customer_id,
        orders_created=orders_created,
        orders_filtered_historical=orders_filtered_historical,
        order_items_created=order_items_created,
        pending_items_created=pending_items_created,
        preferences_added=preferences_added,
        incidents_added=incidents_added,
        delivery_preferences_updated=delivery_updated,
        rolled_back_count=rolled_back_count,
        identities_quarantined=identities_quarantined,
        identities_auto_applied=identities_auto_applied,
        identities_kept_pending=identities_kept_pending,
    )
