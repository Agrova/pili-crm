"""ADR-011 Task 2 tests: schemas (MatchedStructuredExtract + {"_v": 1} invariant)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.analysis.schemas import (
    MatchedOrder,
    MatchedOrderItem,
    MatchedStructuredExtract,
    ProductCandidate,
    StructuredExtract,
)

# ── {"_v": 1} invariant (ADR-011 Task 2 TZ test №5) ────────────────────────


def test_empty_structured_extract_dumps_to_v1_only() -> None:
    """`StructuredExtract(schema_version=1)` round-trips to exactly `{"_v": 1}`.

    `record_skipped_analysis` depends on this to satisfy the CHECK
    constraint `ck_analysis_chat_analysis_skipped_consistency`. The dump
    must use `by_alias=True` — without it the key stays as
    `schema_version`.
    """
    m = StructuredExtract(schema_version=1)  # type: ignore[call-arg]
    assert m.model_dump(exclude_none=True, by_alias=True) == {"_v": 1}


def test_empty_structured_extract_without_alias_keeps_python_name() -> None:
    """Documentation of current pydantic behaviour — `by_alias=True` is required."""
    m = StructuredExtract(schema_version=1)  # type: ignore[call-arg]
    assert m.model_dump(exclude_none=True) == {"schema_version": 1}


def test_structured_extract_v1_invariant_under_varied_inputs() -> None:
    """Any constructor input that supplies only `_v=1` must dump to `{"_v": 1}`.

    Guards against accidental leakage of defaults into the serialised form
    which would violate the CHECK constraint for skipped rows.
    """
    # Via alias key
    m1 = StructuredExtract.model_validate({"_v": 1})
    assert m1.model_dump(exclude_none=True, by_alias=True) == {"_v": 1}
    # Via python-name key
    m2 = StructuredExtract(schema_version=1)  # type: ignore[call-arg]
    assert m2.model_dump(exclude_none=True, by_alias=True) == {"_v": 1}
    # Explicit None overrides collapsed
    m3 = StructuredExtract.model_validate(
        {
            "_v": 1,
            "identity": None,
            "preferences": None,
            "delivery_preferences": None,
            "incidents": None,
            "orders": None,
            "payments": None,
        }
    )
    assert m3.model_dump(exclude_none=True, by_alias=True) == {"_v": 1}


# ── MatchedOrderItem validator ──────────────────────────────────────────────


def test_confident_match_requires_matched_product_id() -> None:
    with pytest.raises(ValidationError, match="matched_product_id is required"):
        MatchedOrderItem(matching_status="confident_match")


def test_confident_match_accepts_matched_product_id() -> None:
    item = MatchedOrderItem(matching_status="confident_match", matched_product_id=42)
    assert item.matched_product_id == 42


def test_ambiguous_requires_non_empty_candidates() -> None:
    with pytest.raises(ValidationError, match="candidates must be non-empty"):
        MatchedOrderItem(matching_status="ambiguous")
    with pytest.raises(ValidationError, match="candidates must be non-empty"):
        MatchedOrderItem(matching_status="ambiguous", candidates=[])


def test_ambiguous_accepts_candidates() -> None:
    item = MatchedOrderItem(
        matching_status="ambiguous",
        candidates=[
            ProductCandidate(product_id=1, confidence_note="contains 'Veritas'"),
            ProductCandidate(product_id=2, confidence_note="PM-V11 variant"),
        ],
    )
    assert item.candidates is not None
    assert len(item.candidates) == 2


def test_not_found_allows_missing_reason() -> None:
    item = MatchedOrderItem(matching_status="not_found")
    assert item.not_found_reason is None


def test_not_found_accepts_reason() -> None:
    item = MatchedOrderItem(
        matching_status="not_found", not_found_reason="no catalog entry"
    )
    assert item.not_found_reason == "no catalog entry"


def test_matched_structured_extract_full_shape() -> None:
    extract = MatchedStructuredExtract(  # type: ignore[call-arg]
        schema_version=1,
        orders=[
            MatchedOrder(
                description="test order",
                items=[
                    MatchedOrderItem(
                        items_text="Veritas 05P44",
                        matching_status="confident_match",
                        matched_product_id=10,
                    ),
                ],
            )
        ],
    )
    assert extract.orders is not None
    assert extract.orders[0].items is not None
    assert extract.orders[0].items[0].matched_product_id == 10
