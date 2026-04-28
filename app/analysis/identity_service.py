"""Identity quarantine service (ADR-011 identity updates, X1).

Two operational surfaces:

1. ``extract_identity_to_quarantine`` — writes one ``analysis_extracted_identity``
   row per non-empty identity field (``status='pending'``).
2. ``auto_apply_safe_identity_updates`` — applies pending rows to
   ``orders_customer`` only when the target column is NULL **and**
   ``confidence='high'``. All other rows stay ``pending`` for operator
   moderation through Cowork.

Per-field confidence is **not** emitted by the current LLM schema
(``Identity.confidence_notes`` is free text), so all quarantine rows are
written with ``confidence='medium'`` by default. Auto-apply will therefore
not trigger on the current pipeline — by design. The structure is in
place for a future LLM-prompt iteration that emits per-field confidence.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import AnalysisExtractedIdentity
from app.orders.service import (
    IdentityColumn,
    get_customer_identity_columns,
    set_customer_identity_field,
)

# ── allowed contact_type values (mirror of CHECK constraint) ────────────────
#
# Mapped from LLM ``Identity`` keys. ``name_guess`` is renamed to ``name`` so
# the contact_type matches both the CHECK constraint and the eventual
# ``orders_customer.name`` column. ``confidence_notes`` is dropped silently —
# it is meta-information, not an identity value.

_LLM_KEY_TO_CONTACT_TYPE: dict[str, str] = {
    "phone": "phone",
    "email": "email",
    "telegram_username": "telegram_username",
    "city": "city",
    "address": "address",
    "delivery_method": "delivery_method",
    "name_guess": "name",
}

# Full mapping contact_type → orders_customer column for operator-driven
# overwrite (ADR-011 X1). Used by crm-mcp/tools/apply_identity_update.py and
# list_pending_identity_updates.py to map quarantine contact_type to the
# OrdersCustomer column. Single source of truth — do not duplicate locally.
OPERATOR_OVERWRITE_COLUMNS: dict[str, IdentityColumn] = {
    "phone": "phone",
    "email": "email",
    "telegram_username": "telegram_username",
    "name": "name",
}

# Subset of contact_types where auto-apply to ``orders_customer`` is
# technically possible. ``name`` is intentionally excluded: the column is
# NOT NULL so the "fill empty" gate never opens — operator must drive
# any name change through manual moderation.
_AUTO_APPLY_COLUMNS: dict[str, IdentityColumn] = {
    k: v for k, v in OPERATOR_OVERWRITE_COLUMNS.items() if k != "name"
}

_DEFAULT_CONFIDENCE = "medium"


async def extract_identity_to_quarantine(
    session: AsyncSession,
    *,
    chat_id: int,
    customer_id: int | None,
    analyzer_version: str,
    identity_data: dict[str, str | None],
    context_quotes: dict[str, str] | None = None,
) -> list[int]:
    """Write extracted identity fields to the quarantine table.

    ``identity_data`` is the dict produced by
    ``Identity.model_dump(exclude_none=True)`` — keys are LLM field names
    (``name_guess`` / ``telegram_username`` / ``phone`` / ``email`` / ``city``).

    Empty-string and ``None`` values are skipped. Keys outside the LLM
    identity schema (``confidence_notes``, hallucinated fields) are
    skipped silently.

    ``context_quotes`` keys are **contact_type** values
    (``name`` / ``phone`` / ...), not LLM keys.

    Returns the list of created ``extracted_id`` values.
    """
    quotes = context_quotes or {}
    extracted_ids: list[int] = []

    for llm_key, raw_value in identity_data.items():
        contact_type = _LLM_KEY_TO_CONTACT_TYPE.get(llm_key)
        if contact_type is None:
            continue
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue

        row = AnalysisExtractedIdentity(
            customer_id=customer_id,
            chat_id=chat_id,
            analyzer_version=analyzer_version,
            contact_type=contact_type,
            value=value,
            confidence=_DEFAULT_CONFIDENCE,
            context_quote=quotes.get(contact_type),
            # status defaults to 'pending' via server_default
        )
        session.add(row)
        await session.flush()
        extracted_ids.append(row.extracted_id)

    return extracted_ids


async def auto_apply_safe_identity_updates(
    session: AsyncSession,
    *,
    customer_id: int,
    extracted_ids: list[int] | None = None,
) -> dict[str, int]:
    """Apply ``pending`` rows to ``orders_customer`` when safe.

    Safe means: target column is NULL **and** ``confidence='high'``.
    Anything else stays ``pending`` for operator moderation.

    ``extracted_ids=None`` processes every pending row for the customer;
    a list narrows the scope (used by the ``apply_analysis_to_customer``
    integration to act only on rows it just created).

    Returns ``{'auto_applied': N, 'kept_pending': M}``.

    Note: the function does **not** wrap individual UPDATEs in savepoints,
    so a UNIQUE-violation on ``orders_customer.email`` would abort the
    surrounding transaction. With the current LLM schema confidence is
    always ``'medium'`` so auto-apply does not fire — the savepoint
    treatment is deferred until an LLM-prompt iteration starts emitting
    ``'high'`` confidence.
    """
    stmt = select(AnalysisExtractedIdentity).where(
        AnalysisExtractedIdentity.customer_id == customer_id,
        AnalysisExtractedIdentity.status == "pending",
    )
    if extracted_ids is not None:
        if not extracted_ids:
            return {"auto_applied": 0, "kept_pending": 0}
        stmt = stmt.where(AnalysisExtractedIdentity.extracted_id.in_(extracted_ids))

    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        return {"auto_applied": 0, "kept_pending": 0}

    current = await get_customer_identity_columns(session, customer_id)

    auto_applied = 0
    kept_pending = 0
    now = datetime.now(tz=UTC)

    for row in rows:
        column = _AUTO_APPLY_COLUMNS.get(row.contact_type)
        if column is None:
            kept_pending += 1
            continue
        if row.confidence != "high":
            kept_pending += 1
            continue
        if current.get(column) is not None:
            kept_pending += 1
            continue

        await set_customer_identity_field(session, customer_id, column, row.value)
        row.status = "applied"
        row.applied_action = "auto_filled_empty"
        row.applied_by = "auto"
        row.applied_at = now
        # Local snapshot stays in sync so two pending rows targeting the
        # same column don't both auto-apply within one call.
        current[column] = row.value
        auto_applied += 1

    await session.flush()
    return {"auto_applied": auto_applied, "kept_pending": kept_pending}
