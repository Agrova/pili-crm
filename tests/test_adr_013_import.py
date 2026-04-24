"""ADR-013 Task 2: tests for `analysis/import_preflight_from_toolshop.py`.

The standard `db_session` fixture (see tests/conftest.py) rolls back everything
in `finally`, so tests can freely INSERT test chats and invoke `import_preflight`
without any explicit cleanup. `import_preflight()` itself does not commit —
the caller (CLI in production, these tests in test) owns the transaction.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.import_preflight_from_toolshop import (
    _bin_confidence,
    _map_category,
    import_preflight,
)
from app.analysis import TOOLSHOP_LEGACY_VERSION
from app.analysis.models import AnalysisChatAnalysis

MOCK_FIXTURE = Path(__file__).parent / "fixtures" / "tg_scan_results_mock.json"
PRESENT_NAMES = (
    "ClientChat",
    "UnknownChat",
    "FriendChat",
    "ServiceChat",
    "FamilyChat",
    "NoConfidenceChat",
)
MISSING_NAME = "MissingChat"


async def _seed_account_id(session: AsyncSession) -> int:
    account_id = (
        await session.execute(
            text(
                "SELECT id FROM communications_telegram_account "
                "WHERE phone_number = '+77471057849'"
            )
        )
    ).scalar()
    assert account_id is not None, "ADR-012 seed account missing"
    return int(account_id)


async def _create_chat(
    session: AsyncSession, *, account_id: int, title: str, tg_id: str | None = None
) -> int:
    chat_id = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type, title) "
                "VALUES (:aid, :tg, 'personal_chat', :t) RETURNING id"
            ),
            {"aid": account_id, "tg": tg_id or f"adr013-test-{title}", "t": title},
        )
    ).scalar()
    assert chat_id is not None
    return int(chat_id)


async def _setup_present_chats(session: AsyncSession) -> tuple[int, dict[str, int]]:
    account_id = await _seed_account_id(session)
    chat_ids: dict[str, int] = {}
    for title in PRESENT_NAMES:
        chat_ids[title] = await _create_chat(
            session, account_id=account_id, title=title
        )
    await session.flush()
    return account_id, chat_ids


def _load_records() -> list[dict[str, object]]:
    return json.loads(MOCK_FIXTURE.read_text(encoding="utf-8"))


# ─── Unit tests (no DB) ───────────────────────────────────────────────────────


def test_bin_confidence_branches() -> None:
    assert _bin_confidence(0.1) == "low"
    assert _bin_confidence(0.59) == "low"
    assert _bin_confidence(0.6) == "medium"
    assert _bin_confidence(0.84) == "medium"
    assert _bin_confidence(0.85) == "high"
    assert _bin_confidence(0.99) == "high"
    assert _bin_confidence(None) == "medium"


def test_map_category_unknown_to_possible_client() -> None:
    assert _map_category("unknown") == "possible_client"
    assert _map_category("client") == "client"
    assert _map_category("service") == "service"
    with pytest.raises(ValueError):
        _map_category("bogus")


# ─── Integration tests (use db_session) ──────────────────────────────────────


async def test_import_mock_file(db_session: AsyncSession) -> None:
    """All six present titles import; MissingChat reports not_found."""
    account_id, _ = await _setup_present_chats(db_session)
    records = _load_records()

    report = await import_preflight(
        db_session, records, owner_account_id=account_id
    )
    await db_session.flush()

    assert report.total == 7
    assert report.imported == 6
    assert report.already_imported == 0
    assert report.not_found_names == [MISSING_NAME]
    assert report.ambiguous_names == []

    rows = (
        await db_session.execute(
            select(AnalysisChatAnalysis).where(
                AnalysisChatAnalysis.analyzer_version == TOOLSHOP_LEGACY_VERSION
            )
        )
    ).scalars().all()
    assert len(rows) == 6


async def test_import_maps_unknown_to_possible_client(
    db_session: AsyncSession,
) -> None:
    account_id, chat_ids = await _setup_present_chats(db_session)
    records = _load_records()

    await import_preflight(db_session, records, owner_account_id=account_id)
    await db_session.flush()

    row = (
        await db_session.execute(
            select(AnalysisChatAnalysis).where(
                AnalysisChatAnalysis.chat_id == chat_ids["UnknownChat"],
                AnalysisChatAnalysis.analyzer_version == TOOLSHOP_LEGACY_VERSION,
            )
        )
    ).scalar_one()
    assert row.preflight_classification == "possible_client"


async def test_import_defaults_medium_confidence_when_absent(
    db_session: AsyncSession,
) -> None:
    account_id, chat_ids = await _setup_present_chats(db_session)
    records = _load_records()

    await import_preflight(db_session, records, owner_account_id=account_id)
    await db_session.flush()

    row = (
        await db_session.execute(
            select(AnalysisChatAnalysis).where(
                AnalysisChatAnalysis.chat_id == chat_ids["NoConfidenceChat"],
                AnalysisChatAnalysis.analyzer_version == TOOLSHOP_LEGACY_VERSION,
            )
        )
    ).scalar_one()
    assert row.preflight_confidence == "medium"


async def test_import_idempotent(db_session: AsyncSession) -> None:
    account_id, _ = await _setup_present_chats(db_session)
    records = _load_records()

    r1 = await import_preflight(db_session, records, owner_account_id=account_id)
    await db_session.flush()
    r2 = await import_preflight(db_session, records, owner_account_id=account_id)
    await db_session.flush()

    assert r1.imported == 6
    assert r1.already_imported == 0
    assert r2.imported == 0
    assert r2.already_imported == 6

    total = (
        await db_session.execute(
            select(AnalysisChatAnalysis).where(
                AnalysisChatAnalysis.analyzer_version == TOOLSHOP_LEGACY_VERSION
            )
        )
    ).all()
    assert len(total) == 6


async def test_import_not_found_in_db(db_session: AsyncSession) -> None:
    """Running on a clean DB (no test chats) → all 7 names → not_found."""
    account_id = await _seed_account_id(db_session)
    records = _load_records()

    report = await import_preflight(
        db_session, records, owner_account_id=account_id
    )

    assert report.imported == 0
    assert report.already_imported == 0
    assert len(report.not_found_names) == 7


async def test_import_dry_run_does_not_write(db_session: AsyncSession) -> None:
    account_id, _ = await _setup_present_chats(db_session)
    records = _load_records()

    report = await import_preflight(
        db_session, records, dry_run=True, owner_account_id=account_id
    )
    await db_session.flush()

    assert report.imported == 6
    assert report.dry_run is True
    rows = (
        await db_session.execute(
            select(AnalysisChatAnalysis).where(
                AnalysisChatAnalysis.analyzer_version == TOOLSHOP_LEGACY_VERSION
            )
        )
    ).all()
    assert rows == []


async def test_import_skipped_reason_is_null(db_session: AsyncSession) -> None:
    account_id, _ = await _setup_present_chats(db_session)
    records = _load_records()

    await import_preflight(db_session, records, owner_account_id=account_id)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(AnalysisChatAnalysis.skipped_reason).where(
                AnalysisChatAnalysis.analyzer_version == TOOLSHOP_LEGACY_VERSION
            )
        )
    ).scalars().all()
    assert rows and all(r is None for r in rows)


async def test_import_watermark_placeholder(db_session: AsyncSession) -> None:
    account_id, _ = await _setup_present_chats(db_session)
    records = _load_records()

    await import_preflight(db_session, records, owner_account_id=account_id)
    await db_session.flush()

    rows = (
        await db_session.execute(
            select(
                AnalysisChatAnalysis.messages_analyzed_up_to,
                AnalysisChatAnalysis.narrative_markdown,
                AnalysisChatAnalysis.structured_extract,
                AnalysisChatAnalysis.chunks_count,
            ).where(
                AnalysisChatAnalysis.analyzer_version == TOOLSHOP_LEGACY_VERSION
            )
        )
    ).all()
    assert len(rows) == 6
    for wm, narrative, struct, chunks in rows:
        assert wm == "preflight_only"
        assert narrative == ""
        assert struct == {"_v": 1}
        assert chunks == 0
