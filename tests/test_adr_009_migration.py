"""ADR-009 migration and Pydantic schema tests.

Tests 1–4: database schema (require seeded PostgreSQL at migration head).
Tests 5–8: pure Pydantic unit tests (no DB required).
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.orders.schemas import (
    CustomerProfileJSONB,
    PreferenceEntry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REVISION = "6bb45bb3dcb5"  # our migration
_PARENT = "4f8fe83398af"  # parent migration


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


async def _col_exists(table: str, column: str) -> bool:
    """Return True if the column exists in the current DB schema."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = :c"
                    ),
                    {"t": table, "c": column},
                )
            ).scalar()
            return bool(count)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 1 — upgrade / downgrade reversibility
# ---------------------------------------------------------------------------


async def test_migration_upgrade_downgrade() -> None:
    """Downgrade -1 removes columns, enum, and index; upgrade head restores them."""
    # Pre-condition: migration must already be applied
    assert await _col_exists(
        "orders_customer", "telegram_username"
    ), "Expected migration to be at head before this test"

    try:
        _alembic("downgrade", "4f8fe83398af")

        # All six added columns must be absent
        for table, col in [
            ("orders_customer", "telegram_username"),
            ("orders_customer_profile", "preferences"),
            ("orders_customer_profile", "delivery_preferences"),
            ("orders_customer_profile", "incidents"),
            ("communications_telegram_chat", "last_imported_message_id"),
            ("communications_telegram_chat", "review_status"),
        ]:
            assert not await _col_exists(table, col), (
                f"{table}.{col} should be absent after downgrade"
            )

        # Enum must be gone
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                enum_count = (
                    await conn.execute(
                        text(
                            "SELECT COUNT(*) FROM pg_type "
                            "WHERE typname = 'telegram_chat_review_status'"
                        )
                    )
                ).scalar()
                assert enum_count == 0, "Enum must be absent after downgrade"

                idx_count = (
                    await conn.execute(
                        text(
                            "SELECT COUNT(*) FROM pg_indexes "
                            "WHERE indexname = 'ix_telegram_chat_unreviewed'"
                        )
                    )
                ).scalar()
                assert idx_count == 0, "Partial index must be absent after downgrade"
        finally:
            await engine.dispose()

    finally:
        # Always restore head so subsequent tests are not broken
        _alembic("upgrade", "head")

    # Verify head is fully restored
    assert await _col_exists("orders_customer", "telegram_username")
    assert await _col_exists("communications_telegram_chat", "review_status")


# ---------------------------------------------------------------------------
# Test 2 — existing rows not affected
# ---------------------------------------------------------------------------


async def test_existing_customers_not_affected(db_session: AsyncSession) -> None:
    """Existing customer rows survive migration; new columns are NULL."""
    total = (
        await db_session.execute(text("SELECT COUNT(*) FROM orders_customer"))
    ).scalar()
    assert total and total > 0, "Seed data expected"

    non_null = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM orders_customer "
                "WHERE telegram_username IS NOT NULL"
            )
        )
    ).scalar()
    assert non_null == 0, (
        f"All {total} existing customers should have telegram_username = NULL, "
        f"got {non_null} non-NULL"
    )

    # Check profile JSONB fields (customers with profiles only)
    profile_non_null = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM orders_customer_profile "
                "WHERE preferences IS NOT NULL "
                "   OR delivery_preferences IS NOT NULL "
                "   OR incidents IS NOT NULL"
            )
        )
    ).scalar()
    assert profile_non_null == 0, (
        f"All existing customer profiles should have NULL JSONB fields, "
        f"got {profile_non_null} non-NULL"
    )

    # Check telegram chats
    chat_non_null = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM communications_telegram_chat "
                "WHERE last_imported_message_id IS NOT NULL "
                "   OR review_status IS NOT NULL"
            )
        )
    ).scalar()
    assert chat_non_null == 0, (
        f"All existing telegram chats should have NULL new fields, "
        f"got {chat_non_null} non-NULL"
    )


# ---------------------------------------------------------------------------
# Test 3 — enum values and order
# ---------------------------------------------------------------------------


async def test_enum_values_and_order(db_session: AsyncSession) -> None:
    """enum_range() returns exactly the four values in the declared order."""
    row = (
        await db_session.execute(
            text(
                "SELECT ARRAY(SELECT unnest("
                "enum_range(NULL::telegram_chat_review_status)"
                ")::text) AS vals"
            )
        )
    ).mappings().one()
    assert row["vals"] == ["unreviewed", "linked", "new_customer", "ignored"], (
        f"Unexpected enum order: {row['vals']}"
    )


# ---------------------------------------------------------------------------
# Test 4 — partial index existence and predicate
# ---------------------------------------------------------------------------


async def test_partial_index_exists(db_session: AsyncSession) -> None:
    """ix_telegram_chat_unreviewed exists on the correct table with the right predicate."""
    row = (
        await db_session.execute(
            text(
                "SELECT tablename, indexdef "
                "FROM pg_indexes "
                "WHERE indexname = 'ix_telegram_chat_unreviewed'"
            )
        )
    ).mappings().one_or_none()

    assert row is not None, "Partial index ix_telegram_chat_unreviewed not found"
    assert row["tablename"] == "communications_telegram_chat"
    assert "unreviewed" in row["indexdef"], (
        f"Expected predicate mentioning 'unreviewed' in: {row['indexdef']}"
    )


# ---------------------------------------------------------------------------
# Test 5 — PreferenceEntry valid roundtrip
# ---------------------------------------------------------------------------


def test_pydantic_preferences_valid() -> None:
    """PreferenceEntry accepts valid data; _v alias ↔ schema_version roundtrip."""
    now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)
    data = {
        "_v": 1,
        "product_id": 42,
        "note": "хочет синий",
        "source_message_id": "789",
        "confidence": "suggested",
        "extracted_at": now.isoformat(),
    }
    entry = PreferenceEntry.model_validate(data)
    assert entry.schema_version == 1

    # Roundtrip: serialise with alias, re-parse
    dumped = entry.model_dump(by_alias=True)
    assert "_v" in dumped
    assert "schema_version" not in dumped

    entry2 = PreferenceEntry.model_validate(dumped)
    assert entry2.schema_version == 1
    assert entry2.product_id == 42
    assert entry2.note == "хочет синий"


# ---------------------------------------------------------------------------
# Test 6 — extra keys forbidden
# ---------------------------------------------------------------------------


def test_pydantic_forbid_extra_keys() -> None:
    """Extra keys in any entry model raise ValidationError (extra='forbid')."""
    now = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)
    with pytest.raises(ValidationError):
        PreferenceEntry.model_validate(
            {
                "_v": 1,
                "product_id": 1,
                "note": "test",
                "confidence": "manual",
                "extracted_at": now.isoformat(),
                "unexpected_field": "boom",
            }
        )


# ---------------------------------------------------------------------------
# Test 7 — delivery_preferences primary invariant: valid case
# ---------------------------------------------------------------------------


def test_delivery_primary_invariant_valid() -> None:
    """CustomerProfileJSONB is valid when exactly one delivery entry is primary."""
    profile = CustomerProfileJSONB.model_validate(
        {
            "delivery_preferences": [
                {"_v": 1, "method": "СДЭК", "source": "manual", "is_primary": True},
                {"_v": 1, "method": "Самовывоз", "source": "manual", "is_primary": False},
            ]
        }
    )
    assert profile.delivery_preferences is not None
    assert len(profile.delivery_preferences) == 2
    primaries = [d for d in profile.delivery_preferences if d.is_primary]
    assert len(primaries) == 1


# ---------------------------------------------------------------------------
# Test 8 — delivery_preferences primary invariant: invalid cases
# ---------------------------------------------------------------------------


def test_delivery_primary_invariant_invalid() -> None:
    """CustomerProfileJSONB raises ValidationError when primary count != 1."""
    # (a) Zero primaries
    with pytest.raises(ValidationError) as exc_info:
        CustomerProfileJSONB.model_validate(
            {
                "delivery_preferences": [
                    {"_v": 1, "method": "СДЭК", "source": "manual", "is_primary": False},
                    {"_v": 1, "method": "Самовывоз", "source": "manual", "is_primary": False},
                ]
            }
        )
    assert "got 0" in str(exc_info.value)

    # (b) Two primaries
    with pytest.raises(ValidationError) as exc_info:
        CustomerProfileJSONB.model_validate(
            {
                "delivery_preferences": [
                    {"_v": 1, "method": "СДЭК", "source": "manual", "is_primary": True},
                    {"_v": 1, "method": "Самовывоз", "source": "manual", "is_primary": True},
                ]
            }
        )
    assert "got 2" in str(exc_info.value)
