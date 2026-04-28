"""ADR-013 Task 1: preflight columns — migration and Pydantic schema tests.

Tests 1–6: database schema (require PostgreSQL at migration head).
Tests 7–10: pure Pydantic unit tests (no DB required).

Mutating tests wrap writes in a transaction that is always rolled back, so the
shared DB is not contaminated.
"""

from __future__ import annotations

import subprocess

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.analysis.schemas import PreflightClassification
from app.config import settings

_REVISION = "f465f34797b8"  # ADR-013 migration
_PARENT = "bbd6e538e338"  # parent (ADR-012)

_NEW_COLUMNS = (
    "preflight_classification",
    "preflight_confidence",
    "preflight_reason",
    "skipped_reason",
)


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


async def _seed_chat_id(conn: object) -> int:
    """Return an existing chat_id, or create one in the current transaction.

    The caller is expected to be inside a transaction that will be rolled back.
    """
    existing = (
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "SELECT id FROM communications_telegram_chat "
                "ORDER BY id LIMIT 1"
            )
        )
    ).scalar()
    if existing is not None:
        return int(existing)
    # Use the ADR-012 seed account (+77471057849) as FK target
    new_id = (
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type) "
                "SELECT id, 'adr013-test-chat', 'private' "
                "FROM communications_telegram_account "
                "WHERE phone_number = '+77471057849' "
                "RETURNING id"
            )
        )
    ).scalar()
    assert new_id is not None, "Failed to create test chat"
    return int(new_id)


# ---------------------------------------------------------------------------
# Test 1 — upgrade/downgrade reversibility
# ---------------------------------------------------------------------------


async def test_migration_upgrade_downgrade() -> None:
    """Downgrade removes 4 columns + CHECK; upgrade restores them."""
    for col in _NEW_COLUMNS:
        assert await _scalar(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'analysis_chat_analysis' AND column_name = :c",
            c=col,
        ) == 1, f"Precondition: {col} must be present at head"

    try:
        _alembic("downgrade", _PARENT)

        for col in _NEW_COLUMNS:
            assert await _scalar(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = 'analysis_chat_analysis' "
                "AND column_name = :c",
                c=col,
            ) == 0, f"{col} should be absent after downgrade"
        assert await _scalar(
            "SELECT COUNT(*) FROM pg_constraint "
            "WHERE conname = 'ck_analysis_chat_analysis_skipped_consistency'"
        ) == 0, "CHECK constraint must be dropped on downgrade"
    finally:
        _alembic("upgrade", "head")

    for col in _NEW_COLUMNS:
        assert await _scalar(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_name = 'analysis_chat_analysis' "
            "AND column_name = :c",
            c=col,
        ) == 1
    assert await _scalar(
        "SELECT COUNT(*) FROM pg_constraint "
        "WHERE conname = 'ck_analysis_chat_analysis_skipped_consistency'"
    ) == 1


# ---------------------------------------------------------------------------
# Test 2 — new columns are nullable: minimal INSERT without them succeeds
# ---------------------------------------------------------------------------


async def test_new_columns_nullable() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                chat_id = await _seed_chat_id(conn)
                await conn.execute(
                    text(
                        "INSERT INTO analysis_chat_analysis "
                        "(chat_id, analyzed_at, analyzer_version, "
                        " messages_analyzed_up_to, narrative_markdown, "
                        " structured_extract, chunks_count) "
                        "VALUES (:chat_id, NOW(), 'v1.0+test-nullable', "
                        " '0', 'narrative', "
                        " '{\"_v\": 1, \"identity\": null}'::jsonb, 1)"
                    ),
                    {"chat_id": chat_id},
                )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 3 — CHECK allows NULL skipped_reason (normal, full-analysis rows)
# ---------------------------------------------------------------------------


async def test_check_constraint_allows_null_skipped_reason() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                chat_id = await _seed_chat_id(conn)
                await conn.execute(
                    text(
                        "INSERT INTO analysis_chat_analysis "
                        "(chat_id, analyzed_at, analyzer_version, "
                        " messages_analyzed_up_to, narrative_markdown, "
                        " structured_extract, chunks_count) "
                        "VALUES (:chat_id, NOW(), 'v1.0+test-null-skip', "
                        " '42', 'full narrative here', "
                        " '{\"_v\": 1, \"identity\": {\"name_guess\": \"X\"}}'::jsonb, 3)"
                    ),
                    {"chat_id": chat_id},
                )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 4 — CHECK rejects skipped row with non-empty narrative
# ---------------------------------------------------------------------------


async def test_check_constraint_rejects_skipped_with_narrative() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                chat_id = await _seed_chat_id(conn)
                with pytest.raises(IntegrityError):
                    await conn.execute(
                        text(
                            "INSERT INTO analysis_chat_analysis "
                            "(chat_id, analyzed_at, analyzer_version, "
                            " messages_analyzed_up_to, narrative_markdown, "
                            " structured_extract, chunks_count, "
                            " skipped_reason) "
                            "VALUES (:chat_id, NOW(), 'v1.0+test-bad-skip', "
                            " '0', 'some narrative', "
                            " '{\"_v\": 1}'::jsonb, 0, 'not_client')"
                        ),
                        {"chat_id": chat_id},
                    )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 5 — CHECK allows skipped row with empty narrative + minimal extract
# ---------------------------------------------------------------------------


async def test_check_constraint_allows_skipped_with_empty_narrative() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                chat_id = await _seed_chat_id(conn)
                await conn.execute(
                    text(
                        "INSERT INTO analysis_chat_analysis "
                        "(chat_id, analyzed_at, analyzer_version, "
                        " messages_analyzed_up_to, narrative_markdown, "
                        " structured_extract, chunks_count, "
                        " skipped_reason) "
                        "VALUES (:chat_id, NOW(), 'v1.0+test-ok-skip', "
                        " '0', '', "
                        " '{\"_v\": 1}'::jsonb, 0, 'not_client')"
                    ),
                    {"chat_id": chat_id},
                )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 6 — CHECK rejects skipped row with non-minimal extract
# ---------------------------------------------------------------------------


async def test_check_constraint_rejects_skipped_with_nonempty_extract() -> None:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            try:
                chat_id = await _seed_chat_id(conn)
                with pytest.raises(IntegrityError):
                    await conn.execute(
                        text(
                            "INSERT INTO analysis_chat_analysis "
                            "(chat_id, analyzed_at, analyzer_version, "
                            " messages_analyzed_up_to, narrative_markdown, "
                            " structured_extract, chunks_count, "
                            " skipped_reason) "
                            "VALUES (:chat_id, NOW(), 'v1.0+test-bad-extract', "
                            " '0', '', "
                            " '{\"_v\": 1, \"identity\": "
                            "   {\"name_guess\": \"X\"}}'::jsonb, "
                            " 0, 'not_client')"
                        ),
                        {"chat_id": chat_id},
                    )
            finally:
                await trans.rollback()
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Test 7 — PreflightClassification accepts every classification × confidence
# ---------------------------------------------------------------------------


def test_preflight_classification_valid() -> None:
    classifications = [
        "client",
        "possible_client",
        "not_client",
        "family",
        "friend",
        "service",
        "empty",
    ]
    confidences = ["low", "medium", "high"]
    for c in classifications:
        for conf in confidences:
            pc = PreflightClassification.model_validate(
                {"classification": c, "confidence": conf, "reason": "ok"}
            )
            assert pc.classification == c
            assert pc.confidence == conf


# ---------------------------------------------------------------------------
# Test 8 — unknown classification rejected
# ---------------------------------------------------------------------------


def test_preflight_classification_rejects_unknown_class() -> None:
    with pytest.raises(ValidationError):
        PreflightClassification.model_validate(
            {"classification": "unknown", "confidence": "high", "reason": "x"}
        )


# ---------------------------------------------------------------------------
# Test 9 — extra='forbid' rejects stray fields
# ---------------------------------------------------------------------------


def test_preflight_classification_rejects_extra_field() -> None:
    """Extra fields are silently ignored (extra='ignore') and absent from the result.

    Regression guard: if extra mode reverts to 'forbid', this test will catch it.
    """
    result = PreflightClassification.model_validate(
        {
            "classification": "client",
            "confidence": "high",
            "reason": "x",
            "bonus_field": "nope",
        }
    )
    assert "bonus_field" not in result.model_dump()


# ---------------------------------------------------------------------------
# Test 10 — JSON roundtrip preserves all fields
# ---------------------------------------------------------------------------


def test_preflight_classification_roundtrip() -> None:
    original = PreflightClassification.model_validate(
        {
            "classification": "possible_client",
            "confidence": "medium",
            "reason": "Спрашивал про инструменты, но без заказа",
        }
    )
    dumped = original.model_dump_json()
    restored = PreflightClassification.model_validate_json(dumped)
    assert restored.classification == "possible_client"
    assert restored.confidence == "medium"
    assert restored.reason == "Спрашивал про инструменты, но без заказа"
