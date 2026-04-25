"""ADR-011 §5 / Task 3: tests for analysis/matching.py.

Five tests per TZ:

11. Empty catalog → ``not_found`` without invoking Qwen.
12. One confident candidate (score ≥ 85, margin ≥ 15) → ``confident_match``
    without invoking Qwen.
13. Many candidates (no auto-confident pick) → Qwen returns one id →
    ``confident_match``.
14. Qwen returns multiple ids → ``ambiguous`` with candidates.
15. Qwen returns ``not_found`` → ``not_found``.

A ``_FakeLLM`` records every prompt and returns canned responses.
``decide_match`` is the single entry point under test.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from analysis import matching
from analysis.matching import (
    CatalogEntry,
    decide_match,
)


@dataclass
class _FakeLLM:
    """Records every ``complete()`` call; returns the next queued response."""

    responses: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> str:
        self.calls.append(prompt)
        if not self.responses:
            raise AssertionError("FakeLLM ran out of canned responses")
        return self.responses.pop(0)


# ── Tests ───────────────────────────────────────────────────────────────────


async def test_empty_catalog_returns_not_found_without_qwen() -> None:
    llm = _FakeLLM()
    decision = await decide_match("Veritas зензубель 05P44.01", [], llm)
    assert decision.status == "not_found"
    assert decision.matched_product_id is None
    assert decision.not_found_reason == "no_fuzzy_candidates"
    assert llm.calls == []


async def test_single_strong_candidate_yields_confident_match_without_qwen() -> None:
    llm = _FakeLLM()
    catalog = [
        CatalogEntry(product_id=42, name="Зензубель Veritas 05P44.01"),
        # second entry is wildly different — won't make the discard cutoff
        CatalogEntry(product_id=99, name="Стамеска Narex 8mm"),
    ]
    decision = await decide_match("зензубель veritas 05P44.01", catalog, llm)
    assert decision.status == "confident_match"
    assert decision.matched_product_id == 42
    assert llm.calls == []


async def test_close_competitors_force_qwen_then_confident_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two near-identical names so the margin guard kicks in: both score
    # high, but the gap is < CONFIDENT_MARGIN — Qwen has to disambiguate.
    catalog = [
        CatalogEntry(product_id=1, name="Рубанок Veritas #5 сталь PM-V11"),
        CatalogEntry(product_id=2, name="Рубанок Veritas #5 сталь O1"),
    ]
    llm = _FakeLLM(
        responses=[
            json.dumps(
                {
                    "decision": "confident_match",
                    "product_id": 2,
                    "candidate_ids": None,
                    "note": "клиент уточнял O1",
                }
            )
        ]
    )
    decision = await decide_match("Рубанок Veritas #5", catalog, llm)
    assert decision.status == "confident_match"
    assert decision.matched_product_id == 2
    # Qwen *was* called because pre-filter could not be confident
    assert len(llm.calls) == 1


async def test_qwen_ambiguous_yields_candidates() -> None:
    catalog = [
        CatalogEntry(product_id=1, name="Рубанок Veritas #5 PM-V11"),
        CatalogEntry(product_id=2, name="Рубанок Veritas #5 O1"),
        CatalogEntry(product_id=3, name="Рубанок Veritas #5 A2"),
    ]
    llm = _FakeLLM(
        responses=[
            json.dumps(
                {
                    "decision": "ambiguous",
                    "product_id": None,
                    "candidate_ids": [1, 2],
                    "note": "сталь не уточнена",
                }
            )
        ]
    )
    decision = await decide_match("рубанок Veritas #5", catalog, llm)
    assert decision.status == "ambiguous"
    assert decision.matched_product_id is None
    assert decision.candidates is not None
    assert sorted(c.product_id for c in decision.candidates) == [1, 2]


async def test_qwen_not_found_propagates() -> None:
    catalog = [
        CatalogEntry(product_id=1, name="Рубанок Veritas #5 PM-V11"),
        CatalogEntry(product_id=2, name="Рубанок Veritas #5 O1"),
    ]
    llm = _FakeLLM(
        responses=[
            json.dumps(
                {
                    "decision": "not_found",
                    "product_id": None,
                    "candidate_ids": None,
                    "note": "ни один кандидат не подходит",
                }
            )
        ]
    )
    decision = await decide_match("рубанок Veritas #5", catalog, llm)
    assert decision.status == "not_found"
    assert decision.matched_product_id is None
    assert decision.candidates is None
    assert decision.not_found_reason == "ни один кандидат не подходит"


# ── Sanity tests on the helpers ─────────────────────────────────────────────


def test_fuzzy_candidates_returns_empty_for_empty_query() -> None:
    catalog = [CatalogEntry(product_id=1, name="Рубанок Veritas")]
    assert matching.fuzzy_candidates("", catalog) == []


def test_fuzzy_candidates_respects_top_n_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(matching, "TOP_N", 3)
    # 10 catalog entries, all containing "рубанок" so all clear the
    # discard cutoff.
    catalog = [
        CatalogEntry(product_id=i, name=f"Рубанок модель {i}") for i in range(10)
    ]
    candidates = matching.fuzzy_candidates("Рубанок модель 5", catalog)
    assert len(candidates) == 3
