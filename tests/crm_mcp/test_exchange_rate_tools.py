"""Tests for MCP tools: get_current_exchange_rate, set_exchange_rate."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings

_CRM_MCP = Path(__file__).resolve().parent.parent.parent / "crm-mcp"
if str(_CRM_MCP) not in sys.path:
    sys.path.insert(0, str(_CRM_MCP))

from tools import get_current_exchange_rate, set_exchange_rate  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1 FROM pricing_exchange_rate LIMIT 0"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"DB not available: {exc}")
    yield eng
    await eng.dispose()


@pytest.fixture
async def clean(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async def _wipe() -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text("TRUNCATE pricing_exchange_rate RESTART IDENTITY")
            )

    await _wipe()
    yield engine
    await _wipe()


@pytest.fixture
def session_factory(clean: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(clean, expire_on_commit=False)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _insert_rate(
    engine: AsyncEngine,
    *,
    currency: str = "USD",
    rate: str = "80.00",
    valid_from: datetime | None = None,
) -> int:
    if valid_from is None:
        valid_from = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO pricing_exchange_rate"
                " (from_currency, to_currency, rate, source, valid_from)"
                " VALUES (:cur, 'RUB', :rate, 'manual', :vf)"
                " RETURNING id"
            ),
            {"cur": currency, "rate": rate, "vf": valid_from},
        )
        return int(row.scalar_one())


# ── get_current_exchange_rate ─────────────────────────────────────────────────


async def test_get_rate_empty_db(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        result = await get_current_exchange_rate.run(session, currency="USD")
    assert result["status"] == "not_found"
    assert "USD" in result["message"]
    assert result["currency"] == "USD"


async def test_get_rate_returns_latest(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _insert_rate(clean, currency="USD", rate="80.00", valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc))
    await _insert_rate(clean, currency="USD", rate="85.00", valid_from=datetime(2026, 6, 1, tzinfo=timezone.utc))

    async with session_factory() as session:
        result = await get_current_exchange_rate.run(session, currency="USD")

    assert result["status"] == "ok"
    assert result["rate"].startswith("85.")


async def test_get_rate_currency_filter(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _insert_rate(clean, currency="USD", rate="82.00")
    await _insert_rate(clean, currency="EUR", rate="90.00")

    async with session_factory() as session:
        result = await get_current_exchange_rate.run(session, currency="EUR")

    assert result["status"] == "ok"
    assert result["currency"] == "EUR"
    assert result["rate"].startswith("90.")


# ── set_exchange_rate ─────────────────────────────────────────────────────────


async def test_set_rate_creates_record(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        result = await set_exchange_rate.run(session, currency="USD", rate="82.50")

    assert result["status"] == "ok"
    assert result["currency"] == "USD"
    assert result["rate"].startswith("82.")
    assert result["source"] == "manual"

    async with clean.connect() as conn:
        count = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pricing_exchange_rate"
                    " WHERE from_currency = 'USD' AND to_currency = 'RUB'"
                )
            )
        ).scalar()
    assert count == 1


async def test_set_rate_history_preserved(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        r1 = await set_exchange_rate.run(session, currency="USD", rate="80.00")
    async with session_factory() as session:
        r2 = await set_exchange_rate.run(session, currency="USD", rate="85.00")

    assert r1["status"] == "ok"
    assert r2["status"] == "ok"
    assert r1["id"] != r2["id"]

    async with clean.connect() as conn:
        count = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM pricing_exchange_rate"
                    " WHERE from_currency = 'USD'"
                )
            )
        ).scalar()
    assert count == 2


async def test_set_rate_invalid_rate(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        result = await set_exchange_rate.run(session, currency="USD", rate="abc")

    assert result["status"] == "error"
    assert "rate" in result["error"].lower()


async def test_set_rate_then_get(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        set_result = await set_exchange_rate.run(session, currency="USD", rate="83.75")
    assert set_result["status"] == "ok"

    async with session_factory() as session:
        get_result = await get_current_exchange_rate.run(session, currency="USD")

    assert get_result["status"] == "ok"
    assert get_result["rate"].startswith("83.")
    assert get_result["id"] == set_result["id"]
