"""ADR-012 Task 1: multi-account Telegram schema — migration tests.

Verifies:
  - Upgrade/downgrade roundtrip is clean.
  - CHECK constraint enforces E.164 phone format.
  - UNIQUE on phone_number.
  - Seed (Kazakhstan account) is present at head, removed on downgrade.
  - Backfill invariant (no NULL owner_account_id).
  - New composite UNIQUE (owner_account_id, telegram_chat_id) behavior.
  - Old single-column UNIQUE is gone.
  - FK RESTRICT prevents account deletion with dependent chats.

Tests use the DB at head. Mutating tests wrap their writes in a transaction
that is always rolled back, so the shared DB is not contaminated.
"""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

_REVISION = "bbd6e538e338"  # ADR-012 migration
_PARENT = "a7f1d9c2e384"  # parent (ADR-011)


def _alembic(*args: str) -> None:
    result = subprocess.run(
        ["alembic", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic {' '.join(args)} failed\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


async def _scalar(sql: str, **params: object) -> object:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params)).scalar()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 1 — upgrade/downgrade reversibility
# ---------------------------------------------------------------------------


async def test_migration_upgrade_downgrade() -> None:
    """Down to parent removes table/column/constraint; up to head restores them."""
    # Pre-condition: at head
    assert await _scalar(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name = 'communications_telegram_account'"
    ) == 1

    try:
        _alembic("downgrade", _PARENT)

        # Table gone
        assert await _scalar(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_name = 'communications_telegram_account'"
        ) == 0
        # Column gone
        assert await _scalar(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'communications_telegram_chat' "
            "AND column_name = 'owner_account_id'"
        ) == 0
        # Old UNIQUE restored
        assert await _scalar(
            "SELECT COUNT(*) FROM pg_constraint "
            "WHERE conname = 'uq_communications_telegram_chat_telegram_chat_id'"
        ) == 1
        # New UNIQUE absent
        assert await _scalar(
            "SELECT COUNT(*) FROM pg_constraint "
            "WHERE conname = 'uq_communications_telegram_chat_owner_telegram_chat_id'"
        ) == 0
    finally:
        _alembic("upgrade", "head")

    # Head restored
    assert await _scalar(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_name = 'communications_telegram_account'"
    ) == 1
    assert await _scalar(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_name = 'communications_telegram_chat' "
        "AND column_name = 'owner_account_id'"
    ) == 1


# ---------------------------------------------------------------------------
# Test 2 — CHECK constraint rejects non-E.164 phones
# ---------------------------------------------------------------------------


async def test_account_phone_check_constraint() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                with pytest.raises(IntegrityError):
                    await conn.execute(
                        text(
                            "INSERT INTO communications_telegram_account "
                            "(phone_number, display_name) VALUES (:p, :d)"
                        ),
                        {"p": "77471057849", "d": "Bad"},  # no '+'
                    )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 3 — UNIQUE on phone_number
# ---------------------------------------------------------------------------


async def test_account_phone_unique() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                with pytest.raises(IntegrityError):
                    await conn.execute(
                        text(
                            "INSERT INTO communications_telegram_account "
                            "(phone_number, display_name) VALUES "
                            "('+77471057849', 'Duplicate')"
                        )
                    )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 4 — seed row exists with the expected values
# ---------------------------------------------------------------------------


async def test_account_seed_exists() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT phone_number, display_name, "
                        "first_import_at, last_import_at "
                        "FROM communications_telegram_account "
                        "WHERE phone_number = '+77471057849'"
                    )
                )
            ).mappings().first()
        assert row is not None
        assert row["display_name"] == "Казахстан (+77471057849)"
        assert row["first_import_at"] is not None
        assert row["last_import_at"] is not None
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 5 — backfill invariant
# ---------------------------------------------------------------------------


async def test_chat_backfill() -> None:
    """No chat may have NULL owner_account_id (guarded by NOT NULL, but assert)."""
    null_count = await _scalar(
        "SELECT COUNT(*) FROM communications_telegram_chat "
        "WHERE owner_account_id IS NULL"
    )
    assert null_count == 0


# ---------------------------------------------------------------------------
# Test 6 — new UNIQUE: same account + same telegram_chat_id rejected
# ---------------------------------------------------------------------------


async def test_new_unique_same_account() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                await conn.execute(
                    text(
                        "INSERT INTO communications_telegram_chat "
                        "(owner_account_id, telegram_chat_id, chat_type) "
                        "VALUES (1, 'dup-test', 'private')"
                    )
                )
                with pytest.raises(IntegrityError):
                    await conn.execute(
                        text(
                            "INSERT INTO communications_telegram_chat "
                            "(owner_account_id, telegram_chat_id, chat_type) "
                            "VALUES (1, 'dup-test', 'private')"
                        )
                    )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 7 — new UNIQUE: same telegram_chat_id in DIFFERENT accounts OK
# ---------------------------------------------------------------------------


async def test_new_unique_different_accounts() -> None:
    """The core ADR-012 guarantee: collision of telegram_chat_id is only
    a conflict within the same owner account."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                acc2_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO communications_telegram_account "
                            "(phone_number, display_name) "
                            "VALUES ('+79161879839', 'Россия test') "
                            "RETURNING id"
                        )
                    )
                ).scalar()
                await conn.execute(
                    text(
                        "INSERT INTO communications_telegram_chat "
                        "(owner_account_id, telegram_chat_id, chat_type) "
                        "VALUES (1, 'collision-id', 'private')"
                    )
                )
                # Same telegram_chat_id, different owner — must succeed
                await conn.execute(
                    text(
                        "INSERT INTO communications_telegram_chat "
                        "(owner_account_id, telegram_chat_id, chat_type) "
                        "VALUES (:acc, 'collision-id', 'private')"
                    ),
                    {"acc": acc2_id},
                )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 8 — old UNIQUE is gone
# ---------------------------------------------------------------------------


async def test_old_unique_removed() -> None:
    cnt = await _scalar(
        "SELECT COUNT(*) FROM pg_constraint "
        "WHERE conname = 'uq_communications_telegram_chat_telegram_chat_id'"
    )
    assert cnt == 0, "Old single-column UNIQUE must be dropped by the migration"


# ---------------------------------------------------------------------------
# Test 9 — FK RESTRICT: can't delete an account that still owns chats
# ---------------------------------------------------------------------------


async def test_fk_restrict_on_account_delete() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                acc_id = (
                    await conn.execute(
                        text(
                            "INSERT INTO communications_telegram_account "
                            "(phone_number, display_name) "
                            "VALUES ('+12025551234', 'FK test') "
                            "RETURNING id"
                        )
                    )
                ).scalar()
                await conn.execute(
                    text(
                        "INSERT INTO communications_telegram_chat "
                        "(owner_account_id, telegram_chat_id, chat_type) "
                        "VALUES (:acc, 'fk-test-chat', 'private')"
                    ),
                    {"acc": acc_id},
                )
                with pytest.raises(IntegrityError):
                    await conn.execute(
                        text(
                            "DELETE FROM communications_telegram_account "
                            "WHERE id = :acc"
                        ),
                        {"acc": acc_id},
                    )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()
