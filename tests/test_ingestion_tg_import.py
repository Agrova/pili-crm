"""Tests for ingestion/parser.py (1–8) and ingestion/tg_import.py (9–15 + collision).

Parser tests (1–8) are pure-Python and always run. DB-writing tests invoke
`run_import()` with an explicit `owner_account_id` (ADR-012 multi-account).
The Kazakh seed account (id=1) is provided by the ADR-012 Task 1 migration.
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
from ingestion.parser import parse_export, parse_message
from ingestion.tg_import import MESSAGE_INSERT_BATCH_SIZE, run_import

KAZAKH_ACCOUNT_ID = 1  # seeded by the ADR-012 Task 1 migration
RUSSIAN_PHONE_E164 = "+79161879839"


def _make_export_json(
    tmp_path: Path,
    *,
    chat_id: int = 999001,
    title: str = "Test Chat",
    n_messages: int,
) -> Path:
    """Write a minimal Telegram export JSON with n_messages into tmp_path."""
    messages = [
        {
            "id": i,
            "type": "message",
            "date": "2024-01-01T10:00:00",
            "date_unixtime": str(1704103200 + i),
            "from": "Alice",
            "from_id": "user100001",
            "text": f"message {i}",
            "text_entities": [{"type": "plain", "text": f"message {i}"}],
        }
        for i in range(1, n_messages + 1)
    ]
    data = {
        "chats": {
            "list": [
                {
                    "id": chat_id,
                    "type": "personal_chat",
                    "name": title,
                    "messages": messages,
                }
            ]
        }
    }
    path = tmp_path / "result.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path

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
    """Truncate telegram tables before and after each integration test.

    The account registry (communications_telegram_account) is left alone so
    that the Kazakh seed (id=1) remains available. Ad-hoc test accounts
    (e.g. the Russian collision-test account) clean up after themselves.
    """

    async def _delete() -> None:
        async with telegram_engine.begin() as conn:
            await conn.execute(text("DELETE FROM communications_telegram_message"))
            await conn.execute(text("DELETE FROM communications_telegram_chat"))

    await _delete()
    yield telegram_engine
    await _delete()


@pytest.fixture
async def russian_account_id(
    clean_telegram: AsyncEngine,
) -> AsyncIterator[int]:
    """Create a second Telegram account for the collision test; drop it after."""
    async with clean_telegram.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "INSERT INTO communications_telegram_account "
                    "(phone_number, display_name, notes) "
                    "VALUES (:p, :d, :n) RETURNING id"
                ),
                {
                    "p": RUSSIAN_PHONE_E164,
                    "d": "Россия (test)",
                    "n": "collision test",
                },
            )
        ).first()
        assert row is not None
        account_id = int(row[0])
    try:
        yield account_id
    finally:
        # FK communications_telegram_chat.owner_account_id is ON DELETE RESTRICT,
        # so purge this account's chats (and their messages) before deleting it.
        async with clean_telegram.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM communications_telegram_message "
                    "WHERE chat_id IN ("
                    "  SELECT id FROM communications_telegram_chat "
                    "  WHERE owner_account_id = :i"
                    ")"
                ),
                {"i": account_id},
            )
            await conn.execute(
                text(
                    "DELETE FROM communications_telegram_chat "
                    "WHERE owner_account_id = :i"
                ),
                {"i": account_id},
            )
            await conn.execute(
                text(
                    "DELETE FROM communications_telegram_account WHERE id = :i"
                ),
                {"i": account_id},
            )


# ─── 9 ── first run: creates chats and messages ───────────────────────────────


async def test_import_first_run_creates_chats_and_messages(
    clean_telegram: AsyncEngine,
) -> None:
    result = await run_import(
        FIXTURES / "telegram_export_minimal.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
    )

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
    await run_import(
        FIXTURES / "telegram_export_minimal.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
    )
    r2 = await run_import(
        FIXTURES / "telegram_export_minimal.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
    )

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
    r1 = await run_import(
        FIXTURES / "telegram_export_incremental_part1.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
    )
    assert r1.chats_new == 1
    assert r1.msgs_inserted == 5
    assert r1.chats_failed == 0

    r2 = await run_import(
        FIXTURES / "telegram_export_incremental_part2.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
    )
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

    await run_import(
        FIXTURES / "telegram_export_minimal.json",
        owner_account_id=KAZAKH_ACCOUNT_ID,
        dry_run=True,
    )

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


# ─── 13 ── batching: 5 000-message chat imports without parameter-limit error ──


async def test_import_large_chat_batching(
    clean_telegram: AsyncEngine, tmp_path: Path
) -> None:
    n = 5000
    json_path = _make_export_json(tmp_path, chat_id=500001, n_messages=n)

    result = await run_import(json_path, owner_account_id=KAZAKH_ACCOUNT_ID)

    assert result.chats_failed == 0
    assert result.chats_new == 1
    assert result.msgs_inserted == n

    async with clean_telegram.connect() as conn:
        msg_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_message")
            )
        ).scalar_one()

    assert msg_count == n


# ─── 14 ── idempotency: second run on large chat inserts 0 extra rows ─────────


async def test_import_large_chat_idempotent(
    clean_telegram: AsyncEngine, tmp_path: Path
) -> None:
    n = 5000
    json_path = _make_export_json(tmp_path, chat_id=500002, n_messages=n)

    await run_import(json_path, owner_account_id=KAZAKH_ACCOUNT_ID)
    r2 = await run_import(json_path, owner_account_id=KAZAKH_ACCOUNT_ID)

    assert r2.chats_failed == 0
    assert r2.msgs_inserted == 0
    assert r2.msgs_skipped == n

    async with clean_telegram.connect() as conn:
        msg_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_message")
            )
        ).scalar_one()

    assert msg_count == n


# ─── 15 ── batch boundary sizes: BATCH-1, BATCH, BATCH+1 all succeed ──────────


@pytest.mark.parametrize(
    "n_messages",
    [
        MESSAGE_INSERT_BATCH_SIZE - 1,
        MESSAGE_INSERT_BATCH_SIZE,
        MESSAGE_INSERT_BATCH_SIZE + 1,
    ],
)
async def test_import_batch_boundary(
    clean_telegram: AsyncEngine, tmp_path: Path, n_messages: int
) -> None:
    json_path = _make_export_json(
        tmp_path, chat_id=600000 + n_messages, n_messages=n_messages
    )

    result = await run_import(json_path, owner_account_id=KAZAKH_ACCOUNT_ID)

    assert result.chats_failed == 0
    assert result.msgs_inserted == n_messages

    async with clean_telegram.connect() as conn:
        msg_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM communications_telegram_message")
            )
        ).scalar_one()

    assert msg_count == n_messages


# ─── 16 ── ADR-012 CRITICAL: same telegram_chat_id in two accounts ────────────


async def test_two_chats_same_telegram_chat_id_different_accounts(
    clean_telegram: AsyncEngine, russian_account_id: int, tmp_path: Path
) -> None:
    """Two chats with the same Telegram-level chat_id but in different
    accounts must import as two independent rows (ADR-012 core invariant).
    """
    shared_tg_chat_id = 12345
    kz_dir = tmp_path / "kz"
    ru_dir = tmp_path / "ru"
    kz_dir.mkdir()
    ru_dir.mkdir()

    json_a = _make_export_json(
        kz_dir, chat_id=shared_tg_chat_id, title="Максим (KZ)", n_messages=3
    )
    json_b = _make_export_json(
        ru_dir, chat_id=shared_tg_chat_id, title="Константин (RU)", n_messages=4
    )

    r_kz = await run_import(json_a, owner_account_id=KAZAKH_ACCOUNT_ID)
    r_ru = await run_import(json_b, owner_account_id=russian_account_id)

    assert r_kz.chats_new == 1
    assert r_ru.chats_new == 1
    assert r_kz.chats_failed == 0
    assert r_ru.chats_failed == 0

    async with clean_telegram.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, owner_account_id, title "
                    "FROM communications_telegram_chat "
                    "WHERE telegram_chat_id = :tcid "
                    "ORDER BY owner_account_id"
                ),
                {"tcid": str(shared_tg_chat_id)},
            )
        ).all()
        counts_per_chat = (
            await conn.execute(
                text(
                    "SELECT c.owner_account_id, COUNT(m.id) "
                    "FROM communications_telegram_chat c "
                    "LEFT JOIN communications_telegram_message m ON m.chat_id = c.id "
                    "WHERE c.telegram_chat_id = :tcid "
                    "GROUP BY c.owner_account_id "
                    "ORDER BY c.owner_account_id"
                ),
                {"tcid": str(shared_tg_chat_id)},
            )
        ).all()

    assert len(rows) == 2, f"expected 2 chat rows, got {len(rows)}: {rows}"
    owners = {r[1] for r in rows}
    ids = {r[0] for r in rows}
    titles = {r[2] for r in rows}
    assert owners == {KAZAKH_ACCOUNT_ID, russian_account_id}
    assert len(ids) == 2, "chat primary keys must differ"
    assert titles == {"Максим (KZ)", "Константин (RU)"}

    per_owner = {row[0]: row[1] for row in counts_per_chat}
    assert per_owner[KAZAKH_ACCOUNT_ID] == 3
    assert per_owner[russian_account_id] == 4


# ─── 17 ── detect_account_phone / find_result_json (pure-python) ──────────────


def test_detect_account_phone_flat(tmp_path: Path) -> None:
    from ingestion.tg_import import detect_account_phone

    account_dir = tmp_path / "+79161879839"
    account_dir.mkdir()

    phone, resolved = detect_account_phone(account_dir)
    assert phone == "+79161879839"
    assert resolved.resolve() == account_dir.resolve()


def test_detect_account_phone_legacy_subdir(tmp_path: Path) -> None:
    from ingestion.tg_import import detect_account_phone

    account_dir = tmp_path / "+77471057849"
    (account_dir / "DataExport_2026-04-11").mkdir(parents=True)

    phone, resolved = detect_account_phone(
        account_dir / "DataExport_2026-04-11"
    )
    assert phone == "+77471057849"
    assert resolved.resolve() == account_dir.resolve()


def test_detect_account_phone_missing_wrapper(tmp_path: Path) -> None:
    from ingestion.tg_import import detect_account_phone

    bad = tmp_path / "DataExport_2026-04-11"
    bad.mkdir()

    with pytest.raises(RuntimeError, match="E.164"):
        detect_account_phone(bad)


def test_find_result_json_prefers_flat(tmp_path: Path) -> None:
    from ingestion.tg_import import find_result_json

    account_dir = tmp_path / "+79161879839"
    account_dir.mkdir()
    flat = account_dir / "result.json"
    flat.write_text("{}", encoding="utf-8")
    legacy_dir = account_dir / "DataExport_2026-04-11"
    legacy_dir.mkdir()
    (legacy_dir / "result.json").write_text("{}", encoding="utf-8")

    resolved = find_result_json(account_dir)
    assert resolved == flat


def test_find_result_json_legacy_fallback(tmp_path: Path) -> None:
    from ingestion.tg_import import find_result_json

    account_dir = tmp_path / "+77471057849"
    (account_dir / "DataExport_2026-04-11").mkdir(parents=True)
    legacy = account_dir / "DataExport_2026-04-11" / "result.json"
    legacy.write_text("{}", encoding="utf-8")

    resolved = find_result_json(account_dir)
    assert resolved == legacy
