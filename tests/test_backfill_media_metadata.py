"""Tests for ingestion/backfill_media_metadata.py (ADR-015 Task 2 Phase B).

Each test points the backfill script at a temp directory by monkey-patching
`DEFAULT_EXPORTS_ROOT` in the backfill module; the existing Kazakh seed
account (`+77471057849`, id=1) is the target. Account dir name must match
the phone literally — `tmp_path / "+77471057849" / result.json`.

The backfill connects via `settings.database_url`, which `pytest_configure`
points at `test_database_url`, so writes land on the test DB.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from ingestion import backfill_media_metadata as backfill
from ingestion.tg_import import run_import

KAZAKH_ACCOUNT_ID = 1
KAZAKH_PHONE = "+77471057849"
FIXTURES = Path(__file__).parent / "fixtures"


# ─── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def telegram_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT 1 FROM communications_telegram_chat LIMIT 0")
            )
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"DB not available: {exc}")
    yield engine
    await engine.dispose()


@pytest.fixture
async def clean_telegram(telegram_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async def _delete() -> None:
        async with telegram_engine.begin() as conn:
            # cascade-deletes message_media via FK ON DELETE CASCADE
            await conn.execute(text("DELETE FROM communications_telegram_message"))
            await conn.execute(text("DELETE FROM communications_telegram_chat"))

    await _delete()
    yield telegram_engine
    await _delete()


def _materialize_account_dir(
    base: Path, phone: str, fixture_path: Path | None = None, *, empty: bool = False
) -> Path:
    """Create `base/<phone>/result.json` for the backfill script to find."""
    account_dir = base / phone
    account_dir.mkdir(parents=True, exist_ok=True)
    if empty:
        (account_dir / "result.json").write_text(
            json.dumps({"chats": {"list": []}}), encoding="utf-8"
        )
    else:
        assert fixture_path is not None
        (account_dir / "result.json").write_bytes(fixture_path.read_bytes())
    return account_dir


def _patch_exports_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    monkeypatch.setattr(backfill, "DEFAULT_EXPORTS_ROOT", root)


async def _delete_media_rows(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM communications_telegram_message_media"))


async def _media_count(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text(
            "SELECT COUNT(*) FROM communications_telegram_message_media"
        ))).scalar_one())


# ─── 8 ── backfill on mixed-media fixture → table populated correctly ────────


async def test_backfill_populates_media_table_with_mixed_types(
    clean_telegram: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed: import messages so DB has them; then wipe media rows to simulate
    # pre-ADR-015 state where messages exist but no media metadata row does.
    await run_import(
        FIXTURES / "telegram_export_media.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
    )
    await _delete_media_rows(clean_telegram)
    assert await _media_count(clean_telegram) == 0

    _materialize_account_dir(tmp_path, KAZAKH_PHONE, FIXTURES / "telegram_export_media.json")
    _patch_exports_root(monkeypatch, tmp_path)

    report = await backfill.run_backfill()

    assert len(report.accounts) == 1
    stats = report.accounts[0]
    assert stats.phone == KAZAKH_PHONE
    assert stats.chats_parsed == 1
    assert stats.chats_found_in_db == 1
    assert stats.chats_missing_in_db == 0
    assert stats.media_in_json == 5  # ids 2,3,4,5,6 (id=1 is text-only)
    assert stats.media_found_in_db == 5
    assert stats.records_inserted == 5
    assert stats.warnings_no_message == 0

    # Spot-check distribution by media_type
    async with clean_telegram.connect() as conn:
        type_counts = dict((await conn.execute(text(
            "SELECT media_type, COUNT(*) "
            "FROM communications_telegram_message_media "
            "GROUP BY media_type"
        ))).all())
    assert type_counts == {"photo": 2, "file": 2, "video_file": 1}


# ─── 9 ── backfill on empty result.json → 0 records, no errors ───────────────


async def test_backfill_on_empty_export(
    clean_telegram: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _materialize_account_dir(tmp_path, KAZAKH_PHONE, empty=True)
    _patch_exports_root(monkeypatch, tmp_path)

    report = await backfill.run_backfill()

    assert len(report.accounts) == 1
    stats = report.accounts[0]
    assert stats.chats_parsed == 0
    assert stats.media_in_json == 0
    assert stats.records_inserted == 0
    assert stats.warnings_no_message == 0
    assert await _media_count(clean_telegram) == 0


# ─── 10 ── chat in JSON missing in DB → warning, script continues ────────────


async def test_backfill_warns_on_chat_missing_in_db(
    clean_telegram: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DB is empty; JSON has a chat with media — that chat is "missing in DB".
    _materialize_account_dir(tmp_path, KAZAKH_PHONE, FIXTURES / "telegram_export_media.json")
    _patch_exports_root(monkeypatch, tmp_path)

    report = await backfill.run_backfill()

    assert len(report.accounts) == 1
    stats = report.accounts[0]
    assert stats.chats_parsed == 1
    assert stats.chats_found_in_db == 0
    assert stats.chats_missing_in_db == 1
    assert stats.records_inserted == 0
    assert await _media_count(clean_telegram) == 0


# ─── 11 ── repeated backfill is idempotent — second run inserts 0 ────────────


async def test_backfill_repeated_run_inserts_zero(
    clean_telegram: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await run_import(
        FIXTURES / "telegram_export_media.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
    )
    await _delete_media_rows(clean_telegram)

    _materialize_account_dir(tmp_path, KAZAKH_PHONE, FIXTURES / "telegram_export_media.json")
    _patch_exports_root(monkeypatch, tmp_path)

    r1 = await backfill.run_backfill()
    assert r1.accounts[0].records_inserted == 5
    count_after_first = await _media_count(clean_telegram)
    assert count_after_first == 5

    r2 = await backfill.run_backfill()
    assert r2.accounts[0].records_inserted == 0
    assert r2.accounts[0].media_found_in_db == 5  # candidates still detected
    assert await _media_count(clean_telegram) == 5


# ─── 12 ── --verify-style report — no writes, counts match ───────────────────


async def test_backfill_verify_does_not_write(
    clean_telegram: AsyncEngine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await run_import(
        FIXTURES / "telegram_export_media.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
    )
    media_count_before = await _media_count(clean_telegram)
    assert media_count_before == 5

    _materialize_account_dir(tmp_path, KAZAKH_PHONE, FIXTURES / "telegram_export_media.json")
    _patch_exports_root(monkeypatch, tmp_path)

    rc = await backfill._verify(clean_telegram, KAZAKH_PHONE)
    assert rc == 0

    out = capsys.readouterr().out
    assert "Verify summary:" in out
    assert "media messages in JSON:      5" in out
    assert "media metadata records (DB): 5" in out
    # No write should have occurred
    assert await _media_count(clean_telegram) == media_count_before


# ─── 13 ── --dry-run → no DB writes, report still produced ───────────────────


async def test_backfill_dry_run_does_not_write(
    clean_telegram: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await run_import(
        FIXTURES / "telegram_export_media.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
    )
    await _delete_media_rows(clean_telegram)
    assert await _media_count(clean_telegram) == 0

    _materialize_account_dir(tmp_path, KAZAKH_PHONE, FIXTURES / "telegram_export_media.json")
    _patch_exports_root(monkeypatch, tmp_path)

    report = await backfill.run_backfill(dry_run=True)
    stats = report.accounts[0]

    assert stats.media_in_json == 5
    assert stats.media_found_in_db == 5
    assert stats.records_inserted == 0  # dry-run: report counts but does not insert
    assert await _media_count(clean_telegram) == 0
