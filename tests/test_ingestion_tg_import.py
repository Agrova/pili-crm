"""Tests for ingestion/parser.py (1–8) and ingestion/tg_import.py (9–12)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from ingestion.parser import parse_export, parse_message
from ingestion.tg_import import run_import

FIXTURES = Path(__file__).parent / "fixtures"


# ─── 1 ── text-only message ──────────────────────────────────────────────────


def test_parse_message_text_only() -> None:
    msg = {
        "id": 1,
        "type": "message",
        "date": "2024-01-01T10:00:00",
        "date_unixtime": "1704103200",
        "from": "Alice",
        "from_id": "user100001",
        "text": "Hello world",
        "text_entities": [{"type": "plain", "text": "Hello world"}],
    }
    result = parse_message(msg)
    assert result is not None
    assert result.text == "Hello world"
    assert result.media is None
    assert result.telegram_message_id == "1"
    assert result.from_user_id == "user100001"


# ─── 2 ── photo with caption ─────────────────────────────────────────────────


def test_parse_message_photo_with_caption() -> None:
    msg = {
        "id": 2,
        "type": "message",
        "date": "2024-01-01T10:00:00",
        "date_unixtime": "1704103200",
        "from": "Alice",
        "from_id": "user100001",
        "photo": "chats/chat_001/photos/photo_2024-01-01_001.jpg",
        "photo_file_size": 245120,
        "width": 800,
        "height": 600,
        "text": "Вот фото товара",
        "text_entities": [{"type": "plain", "text": "Вот фото товара"}],
    }
    result = parse_message(msg)
    assert result is not None
    assert result.text == "Вот фото товара"
    assert result.media is not None
    assert result.media.media_type == "photo"
    assert result.media.relative_path == "chats/chat_001/photos/photo_2024-01-01_001.jpg"
    assert result.media.file_size_bytes == 245120
    assert result.media.file_name == "photo_2024-01-01_001.jpg"


# ─── 3 ── photo without caption ──────────────────────────────────────────────


def test_parse_message_photo_without_caption() -> None:
    msg = {
        "id": 3,
        "type": "message",
        "date": "2024-01-01T10:00:00",
        "date_unixtime": "1704103200",
        "from": "Alice",
        "from_id": "user100001",
        "photo": "chats/chat_001/photos/photo_2024-01-01_002.jpg",
        "photo_file_size": 102400,
        "width": 600,
        "height": 400,
        "text": "",
        "text_entities": [],
    }
    result = parse_message(msg)
    assert result is not None
    assert result.text is None
    assert result.media is not None
    assert result.media.media_type == "photo"
    assert result.media.relative_path == "chats/chat_001/photos/photo_2024-01-01_002.jpg"


# ─── 4 ── voice message skipped (no text) ────────────────────────────────────


def test_parse_message_voice_skipped() -> None:
    msg = {
        "id": 4,
        "type": "message",
        "date": "2024-01-01T10:00:00",
        "date_unixtime": "1704103200",
        "from": "Alice",
        "from_id": "user100001",
        "file": "chats/chat_001/voice_messages/audio_1.ogg",
        "file_size": 54321,
        "media_type": "voice_message",
        "mime_type": "audio/ogg",
        "duration_seconds": 5,
        "text": "",
        "text_entities": [],
    }
    assert parse_message(msg) is None


# ─── 5 ── sticker skipped (no text) ──────────────────────────────────────────


def test_parse_message_sticker_skipped() -> None:
    msg = {
        "id": 5,
        "type": "message",
        "date": "2024-01-01T10:00:00",
        "date_unixtime": "1704103200",
        "from": "Alice",
        "from_id": "user100001",
        "file": "chats/chat_001/stickers/sticker.webp",
        "file_name": "sticker.webp",
        "file_size": 21786,
        "media_type": "sticker",
        "sticker_emoji": "😊",
        "mime_type": "image/webp",
        "width": 512,
        "height": 512,
        "text": "",
        "text_entities": [],
    }
    assert parse_message(msg) is None


# ─── 6 ── service message skipped ────────────────────────────────────────────


def test_parse_message_service_skipped() -> None:
    msg = {
        "id": 6,
        "type": "service",
        "date": "2024-01-01T10:00:00",
        "date_unixtime": "1704103200",
        "actor": "Alice",
        "actor_id": "user100001",
        "action": "phone_call",
        "duration_seconds": 42,
        "discard_reason": "hangup",
        "text": "",
        "text_entities": [],
    }
    assert parse_message(msg) is None


# ─── 7 ── reply message ───────────────────────────────────────────────────────


def test_parse_message_reply() -> None:
    msg = {
        "id": 7,
        "type": "message",
        "date": "2024-01-01T10:00:00",
        "date_unixtime": "1704103200",
        "from": "Alice",
        "from_id": "user100001",
        "reply_to_message_id": 3,
        "text": "Да, конечно",
        "text_entities": [{"type": "plain", "text": "Да, конечно"}],
    }
    result = parse_message(msg)
    assert result is not None
    assert result.reply_to_telegram_message_id == "3"
    assert isinstance(result.reply_to_telegram_message_id, str)
    assert result.text == "Да, конечно"


# ─── 8 ── parse_export filters non-personal chats ────────────────────────────


def test_parse_export_filters_non_personal() -> None:
    fixture = FIXTURES / "telegram_export_non_personal.json"
    chats = parse_export(fixture)
    types = {c.chat_type for c in chats}
    assert types == {"personal_chat"}, f"Non-personal types found: {types - {'personal_chat'}}"
    titles = {c.title for c in chats}
    assert "Personal Group" not in titles
    assert "Saved Messages" not in titles
    assert "Ivan Petrov" in titles
    assert len(chats) == 1


# ─── Fixtures for integration tests ──────────────────────────────────────────


@pytest.fixture
async def telegram_engine() -> AsyncIterator[AsyncEngine]:
    """Async engine pointed at the test DB; skips the test if DB is unavailable."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT 1 FROM communications_telegram_chat LIMIT 0")
            )
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"DB not available for integration tests: {exc}")
    yield engine
    await engine.dispose()


@pytest.fixture
async def clean_telegram(telegram_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Truncate telegram tables before and after each integration test."""

    async def _delete() -> None:
        async with telegram_engine.begin() as conn:
            await conn.execute(text("DELETE FROM communications_telegram_message"))
            await conn.execute(text("DELETE FROM communications_telegram_chat"))

    await _delete()
    yield telegram_engine
    await _delete()


# ─── 9 ── first run: creates chats and messages ───────────────────────────────


async def test_import_first_run_creates_chats_and_messages(
    clean_telegram: AsyncEngine,
) -> None:
    result = await run_import(FIXTURES / "telegram_export_minimal.json")

    assert result.chats_total == 2
    assert result.chats_new == 2
    assert result.chats_updated == 0
    assert result.chats_failed == 0
    assert result.msgs_inserted == 6

    async with clean_telegram.connect() as conn:
        chat_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_chat")
            )
        ).scalar_one()
        msg_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_message")
            )
        ).scalar_one()
        statuses = (
            await conn.execute(
                text(
                    "SELECT DISTINCT review_status::text"
                    " FROM communications_telegram_chat"
                )
            )
        ).scalars().all()
        watermarks = (
            await conn.execute(
                text(
                    "SELECT last_imported_message_id"
                    " FROM communications_telegram_chat"
                    " ORDER BY id"
                )
            )
        ).scalars().all()

    assert chat_count == 2
    assert msg_count == 6
    assert set(statuses) == {"unreviewed"}
    assert all(w is not None for w in watermarks)
    # Alice chat max id=3, Bob chat max id=12
    assert set(watermarks) == {"3", "12"}


# ─── 10 ── second run: idempotent ────────────────────────────────────────────


async def test_import_second_run_idempotent(clean_telegram: AsyncEngine) -> None:
    await run_import(FIXTURES / "telegram_export_minimal.json")
    r2 = await run_import(FIXTURES / "telegram_export_minimal.json")

    assert r2.chats_new == 0
    assert r2.chats_updated == 2
    assert r2.msgs_inserted == 0
    assert r2.msgs_skipped == 6
    assert r2.chats_failed == 0

    async with clean_telegram.connect() as conn:
        msg_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_message")
            )
        ).scalar_one()

    assert msg_count == 6


# ─── 11 ── incremental: watermark advances correctly ─────────────────────────


async def test_import_incremental(clean_telegram: AsyncEngine) -> None:
    r1 = await run_import(FIXTURES / "telegram_export_incremental_part1.json")
    assert r1.chats_new == 1
    assert r1.msgs_inserted == 5
    assert r1.chats_failed == 0

    r2 = await run_import(FIXTURES / "telegram_export_incremental_part2.json")
    # part2 has ids 1-10; watermark=5 after part1, so only 6-10 are new
    assert r2.chats_updated == 1
    assert r2.msgs_inserted == 5
    assert r2.msgs_skipped == 5
    assert r2.chats_failed == 0

    async with clean_telegram.connect() as conn:
        msg_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_message")
            )
        ).scalar_one()
        watermark = (
            await conn.execute(
                text(
                    "SELECT last_imported_message_id"
                    " FROM communications_telegram_chat"
                    " WHERE telegram_chat_id = '300003'"
                )
            )
        ).scalar_one()

    assert msg_count == 10
    assert watermark == "10"


# ─── 12 ── dry-run: no DB writes ─────────────────────────────────────────────


async def test_dry_run_does_not_write_to_db(clean_telegram: AsyncEngine) -> None:
    async with clean_telegram.connect() as conn:
        chat_before = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_chat")
            )
        ).scalar_one()
        msg_before = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_message")
            )
        ).scalar_one()

    await run_import(FIXTURES / "telegram_export_minimal.json", dry_run=True)

    async with clean_telegram.connect() as conn:
        chat_after = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_chat")
            )
        ).scalar_one()
        msg_after = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_message")
            )
        ).scalar_one()

    assert chat_before == 0
    assert msg_before == 0
    assert chat_after == 0
    assert msg_after == 0
