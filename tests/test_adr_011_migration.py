"""ADR-011 Task 1: analysis module schema — migration and Pydantic schema tests.

Tests 1–3: database schema (require PostgreSQL at migration head).
Tests 4–7: pure Pydantic unit tests (no DB required).
"""

from __future__ import annotations

import subprocess

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.analysis.schemas import StructuredExtract
from app.config import settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REVISION = "a7f1d9c2e384"  # ADR-011 migration
_PARENT = "c3d94a7f1e82"  # parent (ADR-010 addendum: reply + media metadata)

_ANALYSIS_TABLES = (
    "analysis_chat_analysis",
    "analysis_chat_analysis_state",
    "analysis_pending_order_item",
    "analysis_created_entities",
)


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


async def _table_exists(name: str) -> bool:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM information_schema.tables "
                        "WHERE table_name = :n"
                    ),
                    {"n": name},
                )
            ).scalar()
            return bool(count)
    finally:
        await engine.dispose()


async def _enum_exists(name: str) -> bool:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            count = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM pg_type WHERE typname = :n"),
                    {"n": name},
                )
            ).scalar()
            return bool(count)
    finally:
        await engine.dispose()


async def _seed_count() -> int:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            val = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM catalog_supplier "
                        "WHERE name = 'Unknown (auto)'"
                    )
                )
            ).scalar()
            return int(val or 0)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 1 — upgrade / downgrade reversibility
# ---------------------------------------------------------------------------


async def test_migration_upgrade_downgrade() -> None:
    """Downgrade removes all four tables + enum; upgrade head restores them."""
    # Pre-condition: migration must already be at head
    for table in _ANALYSIS_TABLES:
        assert await _table_exists(table), (
            f"Expected migration to be at head: {table} missing"
        )
    assert await _enum_exists("analysis_pending_matching_status")

    try:
        _alembic("downgrade", _PARENT)

        for table in _ANALYSIS_TABLES:
            assert not await _table_exists(table), (
                f"{table} should be absent after downgrade"
            )
        assert not await _enum_exists("analysis_pending_matching_status"), (
            "Enum must be absent after downgrade"
        )
    finally:
        # Always restore head so subsequent tests are not broken
        _alembic("upgrade", "head")

    # Verify head is fully restored
    for table in _ANALYSIS_TABLES:
        assert await _table_exists(table)
    assert await _enum_exists("analysis_pending_matching_status")


# ---------------------------------------------------------------------------
# Test 2 — seed supplier 'Unknown (auto)' created by migration
# ---------------------------------------------------------------------------


async def test_seed_supplier_created() -> None:
    """Seed catalog_supplier 'Unknown (auto)' is present at head; absent after downgrade."""
    # At head
    assert await _seed_count() == 1, "Seed 'Unknown (auto)' missing at head"

    try:
        _alembic("downgrade", _PARENT)
        assert await _seed_count() == 0, "Seed should be removed on downgrade"
    finally:
        _alembic("upgrade", "head")

    assert await _seed_count() == 1, "Seed must reappear after re-upgrade"


# ---------------------------------------------------------------------------
# Test 3 — enum values and order
# ---------------------------------------------------------------------------


async def test_enum_values() -> None:
    """enum_range(NULL::analysis_pending_matching_status) == ['ambiguous', 'not_found']."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT ARRAY(SELECT unnest("
                        "enum_range(NULL::analysis_pending_matching_status)"
                        ")::text) AS vals"
                    )
                )
            ).mappings().one()
        assert row["vals"] == ["ambiguous", "not_found"], (
            f"Unexpected enum order: {row['vals']}"
        )
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 4 — full JSON example from ADR-011 §3 parses
# ---------------------------------------------------------------------------


def test_pydantic_valid_extract() -> None:
    """The illustrative JSON in ADR-011 §3 validates as StructuredExtract."""
    data = {
        "_v": 1,
        "identity": {
            "name_guess": "Сергей Иванов",
            "telegram_username": "s_drilling",
            "phone": "+7...",
            "email": None,
            "city": "Казахстан, Алматы",
            "confidence_notes": "Имя упомянуто в первом сообщении",
        },
        "preferences": [
            {
                "product_hint": "Veritas зензубель",
                "note": "Интересовался несколько раз",
                "source_message_ids": ["123", "456"],
            }
        ],
        "delivery_preferences": {
            "method": "СДЭК",
            "preferred_time": "вечер",
            "notes": None,
        },
        "incidents": [
            {
                "date": "2025-03-15",
                "summary": "Царапина, скидка 5%",
                "resolved": True,
                "source_message_ids": ["789"],
            }
        ],
        "orders": [
            {
                "description": "Февральский заказ",
                "items": [
                    {
                        "items_text": "зензубель Veritas 05P44.01",
                        "quantity": 1,
                        "unit_price": 30600,
                        "currency": "RUB",
                        "source_message_ids": ["234"],
                    }
                ],
                "status_delivery": "delivered",
                "status_payment": "paid",
                "date_guess": "2025-02-20",
                "source_message_ids": ["234", "235"],
            }
        ],
        "payments": [
            {
                "amount": 30600,
                "currency": "RUB",
                "method": "bank_transfer",
                "date_guess": "2025-02-22",
                "source_message_ids": ["240"],
            }
        ],
    }

    extract = StructuredExtract.model_validate(data)

    assert extract.schema_version == 1
    assert extract.identity is not None
    assert extract.identity.name_guess == "Сергей Иванов"
    assert extract.orders is not None
    assert len(extract.orders) == 1
    assert extract.orders[0].items is not None
    assert extract.orders[0].items[0].currency == "RUB"
    assert extract.payments is not None
    assert extract.payments[0].method == "bank_transfer"


# ---------------------------------------------------------------------------
# Test 5 — extra='forbid' rejects stray keys
# ---------------------------------------------------------------------------


def test_pydantic_invalid_extra_key() -> None:
    """Extra keys are silently ignored (extra='ignore') and absent from the result.

    Regression guard: if extra mode is ever switched back to 'forbid', this test
    will catch it and the corresponding MLX hotfix will need re-evaluation.
    """
    result = StructuredExtract.model_validate({"_v": 1, "not_a_real_field": "boom"})
    assert "not_a_real_field" not in result.model_dump()


# ---------------------------------------------------------------------------
# Test 6 — alias roundtrip: _v ↔ schema_version preserves all fields
# ---------------------------------------------------------------------------


def test_pydantic_roundtrip() -> None:
    """model_dump_json(by_alias=True) → model_validate_json() preserves state."""
    data = {
        "_v": 1,
        "identity": {
            "name_guess": "Ivan",
            "telegram_username": None,
            "phone": "+1",
            "email": None,
            "city": None,
            "confidence_notes": None,
        },
        "preferences": [
            {
                "product_hint": "hammer",
                "note": "mentioned twice",
                "source_message_ids": ["10"],
            }
        ],
        "delivery_preferences": None,
        "incidents": None,
        "orders": None,
        "payments": None,
    }
    original = StructuredExtract.model_validate(data)

    dumped = original.model_dump_json(by_alias=True)
    assert '"_v":1' in dumped
    assert "schema_version" not in dumped

    restored = StructuredExtract.model_validate_json(dumped)
    assert restored.schema_version == 1
    assert restored.identity is not None
    assert restored.identity.name_guess == "Ivan"
    assert restored.identity.phone == "+1"
    assert restored.preferences is not None
    assert restored.preferences[0].product_hint == "hammer"
    assert restored.preferences[0].source_message_ids == ["10"]
    # Sections the LLM did not attempt remain None (not []) — semantic signal.
    assert restored.delivery_preferences is None
    assert restored.orders is None


# ---------------------------------------------------------------------------
# Test 7 — minimal valid extract (only _v, everything else None)
# ---------------------------------------------------------------------------


def test_pydantic_all_null() -> None:
    """StructuredExtract(_v=1) with every other field None is valid."""
    extract = StructuredExtract.model_validate({"_v": 1})
    assert extract.schema_version == 1
    assert extract.identity is None
    assert extract.preferences is None
    assert extract.delivery_preferences is None
    assert extract.incidents is None
    assert extract.orders is None
    assert extract.payments is None
