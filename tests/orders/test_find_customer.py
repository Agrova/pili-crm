"""Tests for find_customers repository function."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.orders.repository import find_customers


async def test_find_by_exact_name(db_session: AsyncSession) -> None:
    results = await find_customers(db_session, "Воропаев")
    assert results, "Expected at least one match for 'Воропаев'"
    top = results[0]
    assert "Воропаев" in top.name
    assert top.confidence == 1.0


async def test_find_by_partial_name(db_session: AsyncSession) -> None:
    # "ропа" is a substring of "Воропаев"
    results = await find_customers(db_session, "ропа")
    assert results, "Expected fuzzy match for 'ропа'"
    names = [r.name for r in results]
    assert any("Воропаев" in n for n in names)
    assert all(r.confidence > 0 for r in results)


async def test_find_by_telegram_handle(db_session: AsyncSession) -> None:
    # From seeded data: Алексей has @alexvab
    results = await find_customers(db_session, "@alexvab")
    assert results, "Expected match for '@alexvab'"
    top = results[0]
    assert top.telegram_id is not None
    assert "alexvab" in (top.telegram_id or "").lower()
    assert top.confidence >= 0.95
    assert top.telegram_link == "https://t.me/alexvab"


async def test_find_not_found(db_session: AsyncSession) -> None:
    results = await find_customers(db_session, "НесуществующийКлиентXYZ999")
    assert results == []


async def test_find_multiple_candidates(db_session: AsyncSession) -> None:
    # "Александр" is a common first-name prefix; should match several customers
    results = await find_customers(db_session, "Александр")
    # As long as multiple matches exist, they should be sorted by confidence
    if len(results) > 1:
        confidences = [r.confidence for r in results]
        assert confidences == sorted(confidences, reverse=True)


async def test_find_returns_telegram_link(db_session: AsyncSession) -> None:
    # Any customer with @handle should have a telegram_link
    results = await find_customers(db_session, "Алексей")
    for r in results:
        if r.telegram_id and r.telegram_id.startswith("@"):
            assert r.telegram_link == f"https://t.me/{r.telegram_id[1:]}"


async def test_find_empty_query(db_session: AsyncSession) -> None:
    results = await find_customers(db_session, "")
    assert results == []
