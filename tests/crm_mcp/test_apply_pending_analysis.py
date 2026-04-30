"""Tests for MCP tool: apply_pending_analysis."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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

from tools import apply_pending_analysis  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            await conn.execute(
                text("SELECT 1 FROM communications_telegram_chat LIMIT 0")
            )
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"DB not available: {exc}")
    yield eng
    await eng.dispose()


@pytest.fixture
async def clean(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async def _wipe() -> None:
        async with engine.begin() as conn:
            # CASCADE drops analysis_chat_analysis rows
            await conn.execute(
                text("DELETE FROM communications_telegram_chat WHERE title LIKE 'TEST_APA_%'")
            )
            await conn.execute(
                text(
                    "DELETE FROM orders_customer"
                    " WHERE name LIKE 'TEST_APA_%'"
                    "    OR telegram_id LIKE 'tgapa_%'"
                )
            )

    await _wipe()
    yield engine
    await _wipe()


@pytest.fixture
def session_factory(clean: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(clean, expire_on_commit=False)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _insert_chat(
    engine: AsyncEngine,
    *,
    title: str,
    review_status: str = "unreviewed",
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO communications_telegram_chat"
                "  (owner_account_id, telegram_chat_id, chat_type, title, review_status)"
                " VALUES (1, :tg, 'personal_chat', :title,"
                "         CAST(:st AS telegram_chat_review_status))"
                " RETURNING id"
            ),
            {
                "tg": f"tgapa_{title[-10:]}",
                "title": title,
                "st": review_status,
            },
        )
        return int(row.scalar_one())


async def _insert_skipped_analysis(
    engine: AsyncEngine,
    *,
    chat_id: int,
    analyzer_version: str = "test-apa-v1",
) -> int:
    """Insert a skipped analysis — minimal valid row satisfying CHECK constraints."""
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO analysis_chat_analysis"
                "  (chat_id, analyzer_version, analyzed_at,"
                "   messages_analyzed_up_to, narrative_markdown,"
                "   structured_extract, chunks_count, skipped_reason)"
                " VALUES (:cid, :ver, :at, '0', '',"
                "         '{\"_v\": 1}'::jsonb, 0, 'not_client')"
                " RETURNING id"
            ),
            {
                "cid": chat_id,
                "ver": analyzer_version,
                "at": datetime.now(tz=UTC),
            },
        )
        return int(row.scalar_one())


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_chat_not_found(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        result = await apply_pending_analysis.run(session, chat_id=9_999_999)
    assert result["status"] == "error"
    assert result["error"] == "chat_not_found"
    assert result["chat_id"] == 9_999_999


async def test_chat_not_linked_unreviewed(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    chat_id = await _insert_chat(
        clean, title="TEST_APA_Unreviewed", review_status="unreviewed"
    )
    async with session_factory() as session:
        result = await apply_pending_analysis.run(session, chat_id=chat_id)
    assert result["status"] == "error"
    assert result["error"] == "chat_not_linked"
    assert result["review_status"] == "unreviewed"


async def test_chat_not_linked_ignored(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    chat_id = await _insert_chat(
        clean, title="TEST_APA_Ignored", review_status="ignored"
    )
    async with session_factory() as session:
        result = await apply_pending_analysis.run(session, chat_id=chat_id)
    assert result["status"] == "error"
    assert result["error"] == "chat_not_linked"
    assert result["review_status"] == "ignored"


async def test_no_analysis_found(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    chat_id = await _insert_chat(
        clean, title="TEST_APA_NoAnalysis", review_status="linked"
    )
    async with session_factory() as session:
        result = await apply_pending_analysis.run(session, chat_id=chat_id)
    assert result["status"] == "error"
    assert result["error"] == "no_analysis_found"
    assert "analysis/run.py" in result.get("message", "")


async def test_happy_path_skipped_analysis(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Skipped analysis with no customer link → ok, all counters zero, customer_id=None."""
    chat_id = await _insert_chat(
        clean, title="TEST_APA_Skipped", review_status="linked"
    )
    analysis_id = await _insert_skipped_analysis(clean, chat_id=chat_id)

    async with session_factory() as session:
        result = await apply_pending_analysis.run(session, chat_id=chat_id)

    assert result["status"] == "ok"
    assert result["chat_id"] == chat_id
    assert result["analysis_id"] == analysis_id
    assert result["customer_id"] is None
    assert result["orders_created"] == 0
    assert result["identities_quarantined"] == 0
    assert result["rolled_back_count"] == 0


async def test_new_customer_status_accepted(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """review_status='new_customer' is also a linked state."""
    chat_id = await _insert_chat(
        clean, title="TEST_APA_NewCust", review_status="new_customer"
    )
    await _insert_skipped_analysis(clean, chat_id=chat_id)

    async with session_factory() as session:
        result = await apply_pending_analysis.run(session, chat_id=chat_id)

    assert result["status"] == "ok"
