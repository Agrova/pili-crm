"""Tests for ADR-010 addendum: reply_to_telegram_message_id column + partial composite index."""

from __future__ import annotations

import subprocess

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

PARENT_REVISION = "6bb45bb3dcb5"
INDEX_NAME = "ix_telegram_message_reply_to"


def _alembic(*args: str) -> None:
    """Run an alembic sub-command; raise RuntimeError on non-zero exit."""
    result = subprocess.run(
        ["alembic", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic {' '.join(args)} failed\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


async def _column_exists() -> bool:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.columns"
                        " WHERE table_name = 'communications_telegram_message'"
                        " AND column_name = 'reply_to_telegram_message_id'"
                    )
                )
            ).scalar()
            return bool(count)
    finally:
        await engine.dispose()


async def _get_index_indexdef() -> str | None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
                    {"name": INDEX_NAME},
                )
            ).fetchone()
            return row[0] if row else None
    finally:
        await engine.dispose()


async def test_reply_migration_upgrade_downgrade() -> None:
    """Roundtrip: downgrade removes column+index; upgrade restores them."""
    try:
        _alembic("downgrade", PARENT_REVISION)

        assert not await _column_exists(), "column must not exist after downgrade"
        assert await _get_index_indexdef() is None, "index must not exist after downgrade"

        _alembic("upgrade", "head")

        assert await _column_exists(), "column must exist after upgrade"
        assert await _get_index_indexdef() is not None, "index must exist after upgrade"
    finally:
        # Always restore head so subsequent tests see the applied migration
        _alembic("upgrade", "head")


async def test_existing_messages_not_affected() -> None:
    """Smoke check: column is nullable and migration doesn't populate it with non-NULL values.

    communications_telegram_message is empty before ingestion runs, so count == 0
    is trivially true. This test will become meaningful once tg_import.py fills the table.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM communications_telegram_message"
                        " WHERE reply_to_telegram_message_id IS NOT NULL"
                    )
                )
            ).scalar()
            assert count == 0, (
                f"{count} rows unexpectedly have non-NULL reply_to_telegram_message_id"
            )
    finally:
        await engine.dispose()


async def test_reply_partial_index_exists() -> None:
    """ix_telegram_message_reply_to exists and its predicate contains IS NOT NULL."""
    indexdef = await _get_index_indexdef()
    assert indexdef is not None, f"index {INDEX_NAME!r} not found in pg_indexes"
    assert "is not null" in indexdef.lower(), (
        f"expected 'IS NOT NULL' predicate in indexdef, got: {indexdef!r}"
    )


async def test_reply_composite_index_columns() -> None:
    """indexdef contains the exact ordered substring (chat_id, reply_to_telegram_message_id)."""
    indexdef = await _get_index_indexdef()
    assert indexdef is not None, f"index {INDEX_NAME!r} not found in pg_indexes"
    assert "(chat_id, reply_to_telegram_message_id)" in indexdef, (
        f"expected exact column order in indexdef, got: {indexdef!r}"
    )
