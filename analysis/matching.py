"""ADR-011 §5 / Task 3: catalog matching for analyzer-extracted order items.

Two-stage decision per :class:`OrderItem`:

1. **Fuzzy pre-filter** over ``catalog_product.name`` using
   ``rapidfuzz.fuzz.token_set_ratio``. Top-20 candidates are kept;
   anything below ``DISCARD_THRESHOLD`` (40) is discarded outright.
2. **Verdict**:
   - 0 candidates → ``not_found`` (no Qwen call).
   - 1+ candidates and the top score is ≥ ``CONFIDENT_THRESHOLD`` (85)
     **and** ahead of the second-best by ≥ ``CONFIDENT_MARGIN`` (15)
     → ``confident_match`` (no Qwen call).
   - Otherwise — ask Qwen via :data:`MATCHING_PROMPT`, parse the JSON
     verdict, return ``confident_match`` / ``ambiguous`` / ``not_found``.

The ``CONFIDENT_MARGIN`` rule is the operator's Phase-1 safeguard: two
products like "Veritas #5 PM-V11" vs "Veritas #5 O1" can both score
~87 against the short query "Veritas #5"; without the margin we would
auto-pick one and lose the operator's chance to disambiguate.

Output is a :class:`MatchedStructuredExtract` — the same shape the
service layer (``apply_analysis_to_customer``) expects.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.prompts import MATCHING_PROMPT, render
from app.analysis.schemas import (
    MatchedOrder,
    MatchedOrderItem,
    MatchedStructuredExtract,
    MatchingStatus,
    Order,
    OrderItem,
    ProductCandidate,
    StructuredExtract,
)
from app.catalog.models import CatalogProduct

logger = logging.getLogger(__name__)

CONFIDENT_THRESHOLD: int = 85
CONFIDENT_MARGIN: int = 15
DISCARD_THRESHOLD: int = 40
TOP_N: int = 20

_PUNCT_RE = re.compile(r"[.,;:()/]")


@dataclass(frozen=True)
class CatalogEntry:
    """Catalog product as seen by the matcher."""

    product_id: int
    name: str


@dataclass(frozen=True)
class FuzzyCandidate:
    """One pre-filter candidate with its rapidfuzz score."""

    product_id: int
    name: str
    score: float


@dataclass(frozen=True)
class MatchDecision:
    """Final per-item verdict from :func:`decide_match`."""

    status: MatchingStatus
    matched_product_id: int | None = None
    candidates: list[ProductCandidate] | None = None
    not_found_reason: str | None = None


class _LLMCaller(Protocol):
    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = ...,
        temperature: float = ...,
        max_tokens: int = ...,
    ) -> str: ...


# ── Catalog loading ─────────────────────────────────────────────────────────


async def load_catalog(session: AsyncSession) -> list[CatalogEntry]:
    """Load every product (id, name) for fuzzy pre-filter.

    Pulled once per run; the catalog has ~100s of rows, fits comfortably
    in memory.
    """
    stmt = select(CatalogProduct.id, CatalogProduct.name)
    result = await session.execute(stmt)
    return [CatalogEntry(product_id=row.id, name=row.name) for row in result]


# ── Fuzzy pre-filter ────────────────────────────────────────────────────────


def _normalize(text: str) -> str:
    return _PUNCT_RE.sub(" ", text.lower())


def fuzzy_candidates(
    items_text: str, catalog: list[CatalogEntry]
) -> list[FuzzyCandidate]:
    """Top-``TOP_N`` candidates by ``token_set_ratio``, score ≥ ``DISCARD_THRESHOLD``.

    Returns ``[]`` for empty catalog or empty query.
    """
    if not catalog or not items_text.strip():
        return []
    query = _normalize(items_text)
    choices = {entry.product_id: _normalize(entry.name) for entry in catalog}
    extracted = process.extract(
        query,
        choices,
        scorer=fuzz.token_set_ratio,
        limit=TOP_N,
        score_cutoff=DISCARD_THRESHOLD,
    )
    by_id = {entry.product_id: entry for entry in catalog}
    return [
        FuzzyCandidate(
            product_id=pid,
            name=by_id[pid].name,
            score=float(score),
        )
        for _, score, pid in extracted
    ]


# ── Decision logic ──────────────────────────────────────────────────────────


def _is_confident(candidates: list[FuzzyCandidate]) -> bool:
    if not candidates:
        return False
    top = candidates[0]
    if top.score < CONFIDENT_THRESHOLD:
        return False
    if len(candidates) == 1:
        return True
    return (top.score - candidates[1].score) >= CONFIDENT_MARGIN


def _format_candidates_block(candidates: list[FuzzyCandidate]) -> str:
    return "\n".join(f"- id={c.product_id}: {c.name}" for c in candidates)


def _strip_json_fence(raw: str) -> str:
    """Tolerate the rare case where Qwen wraps JSON in ```json ... ```."""
    s = raw.strip()
    if not s.startswith("```"):
        return s
    s = s.removeprefix("```").lstrip()
    s = s.removeprefix("json").removeprefix("JSON").lstrip()
    if s.endswith("```"):
        s = s[:-3].rstrip()
    return s


def _parse_qwen_verdict(
    raw: str, candidates: list[FuzzyCandidate]
) -> MatchDecision:
    """Parse the JSON verdict from MATCHING_PROMPT into a :class:`MatchDecision`.

    Falls back to ``not_found`` on any parse / shape error — the caller
    treats that as the safe default.
    """
    try:
        data = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError:
        logger.warning("matching: Qwen returned non-JSON: %r", raw[:200])
        return MatchDecision(status="not_found", not_found_reason="qwen_invalid_json")

    decision = data.get("decision")
    note = data.get("note") or ""
    candidate_ids_in_pool = {c.product_id for c in candidates}
    name_by_id = {c.product_id: c.name for c in candidates}

    if decision == "confident_match":
        pid = data.get("product_id")
        if isinstance(pid, int) and pid in candidate_ids_in_pool:
            return MatchDecision(
                status="confident_match", matched_product_id=pid
            )
        logger.warning(
            "matching: confident_match with invalid product_id=%r (pool=%s)",
            pid,
            sorted(candidate_ids_in_pool),
        )
        return MatchDecision(
            status="not_found", not_found_reason="qwen_invalid_product_id"
        )

    if decision == "ambiguous":
        ids = data.get("candidate_ids") or []
        valid_ids = [
            i for i in ids if isinstance(i, int) and i in candidate_ids_in_pool
        ]
        if not valid_ids:
            return MatchDecision(
                status="not_found",
                not_found_reason="qwen_ambiguous_with_no_valid_ids",
            )
        return MatchDecision(
            status="ambiguous",
            candidates=[
                ProductCandidate(
                    product_id=i,
                    confidence_note=note or f"Qwen marked ambiguous; {name_by_id[i]}",
                )
                for i in valid_ids
            ],
        )

    if decision == "not_found":
        return MatchDecision(status="not_found", not_found_reason=note or None)

    logger.warning("matching: unknown decision=%r", decision)
    return MatchDecision(status="not_found", not_found_reason="qwen_unknown_decision")


async def decide_match(
    items_text: str,
    catalog: list[CatalogEntry],
    llm_client: _LLMCaller,
) -> MatchDecision:
    """Decide the matching verdict for one extracted order-item.

    See module docstring for the two-stage logic.
    """
    candidates = fuzzy_candidates(items_text, catalog)

    if not candidates:
        return MatchDecision(
            status="not_found", not_found_reason="no_fuzzy_candidates"
        )

    if _is_confident(candidates):
        return MatchDecision(
            status="confident_match", matched_product_id=candidates[0].product_id
        )

    prompt = render(
        MATCHING_PROMPT,
        items_text=items_text,
        candidates=_format_candidates_block(candidates),
    )
    raw = await llm_client.complete(prompt)
    return _parse_qwen_verdict(raw, candidates)


# ── Whole-extract pass ──────────────────────────────────────────────────────


async def match_extract(
    extract: StructuredExtract,
    catalog: list[CatalogEntry],
    llm_client: _LLMCaller,
) -> MatchedStructuredExtract:
    """Decorate every order item in ``extract`` with a matching verdict.

    Returns a :class:`MatchedStructuredExtract` that
    :func:`app.analysis.service.record_full_analysis` can persist as-is.
    Identity / preferences / delivery / incidents / payments pass through
    untouched.
    """
    matched_orders: list[MatchedOrder] | None = None
    if extract.orders:
        matched_orders = []
        for order in extract.orders:
            matched_items = await _match_order_items(order, catalog, llm_client)
            matched_orders.append(
                _rebuild_order(order, matched_items)
            )

    # ``schema_version`` is aliased to JSON key ``_v`` (Pydantic Field alias);
    # constructing by alias avoids the alias-vs-attribute mypy false positive.
    return MatchedStructuredExtract.model_validate(
        {
            "_v": extract.schema_version,
            "identity": extract.identity,
            "preferences": extract.preferences,
            "delivery_preferences": extract.delivery_preferences,
            "incidents": extract.incidents,
            "orders": matched_orders,
            "payments": extract.payments,
        }
    )


async def _match_order_items(
    order: Order,
    catalog: list[CatalogEntry],
    llm_client: _LLMCaller,
) -> list[MatchedOrderItem] | None:
    if not order.items:
        return None
    out: list[MatchedOrderItem] = []
    for item in order.items:
        text = (item.items_text or "").strip()
        if not text:
            decision = MatchDecision(
                status="not_found", not_found_reason="empty_items_text"
            )
        else:
            decision = await decide_match(text, catalog, llm_client)
        out.append(_build_matched_item(item, decision))
    return out


def _build_matched_item(item: OrderItem, decision: MatchDecision) -> MatchedOrderItem:
    base = item.model_dump(exclude_none=False, by_alias=False)
    return MatchedOrderItem(
        **base,
        matching_status=decision.status,
        matched_product_id=decision.matched_product_id,
        candidates=decision.candidates,
        not_found_reason=decision.not_found_reason,
    )


def _rebuild_order(
    order: Order, matched_items: list[MatchedOrderItem] | None
) -> MatchedOrder:
    return MatchedOrder(
        description=order.description,
        items=matched_items,
        status_delivery=order.status_delivery,
        status_payment=order.status_payment,
        date_guess=order.date_guess,
        source_message_ids=order.source_message_ids,
    )


__all__ = [
    "CONFIDENT_THRESHOLD",
    "CONFIDENT_MARGIN",
    "DISCARD_THRESHOLD",
    "TOP_N",
    "CatalogEntry",
    "FuzzyCandidate",
    "MatchDecision",
    "load_catalog",
    "fuzzy_candidates",
    "decide_match",
    "match_extract",
]
