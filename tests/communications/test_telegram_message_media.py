"""ADR-015 Task 1: unit tests for CommunicationsTelegramMessageMedia model.

Uses the standard db_session fixture (rollback after each test).
Requires a live test DB with the schema applied (alembic upgrade head).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.communications.models import CommunicationsTelegramMessageMedia


async def _seed_message_id(session: AsyncSession) -> int:
    """Insert a minimal Telegram message and return its DB id."""
    account_id = (
        await session.execute(
            text(
                "SELECT id FROM communications_telegram_account "
                "WHERE phone_number = '+77471057849'"
            )
        )
    ).scalar()
    assert account_id is not None, "ADR-012 seed account missing"

    chat_id = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type, title) "
                "VALUES (:aid, :tg, 'personal_chat', 'test') RETURNING id"
            ),
            {"aid": account_id, "tg": f"tg-media-test-{datetime.now(tz=UTC).timestamp()}"},
        )
    ).scalar_one()
    await session.flush()

    message_id = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_message "
                "(chat_id, telegram_message_id, sent_at) "
                "VALUES (:cid, :mid, NOW()) RETURNING id"
            ),
            {"cid": chat_id, "mid": "msg-media-test-1"},
        )
    ).scalar_one()
    await session.flush()
    return int(message_id)


# ── 1 ── создание записи ───────────────────────────────────────────────────────


async def test_create_media_record(db_session: AsyncSession) -> None:
    message_id = await _seed_message_id(db_session)

    media = CommunicationsTelegramMessageMedia(
        message_id=message_id,
        media_type="photo",
        file_name="photo.jpg",
        relative_path="chats/chat_1/photo.jpg",
        file_size_bytes=204800,
        mime_type="image/jpeg",
    )
    db_session.add(media)
    await db_session.flush()

    assert media.id is not None
    assert media.message_id == message_id
    assert media.media_type == "photo"
    assert media.file_name == "photo.jpg"
    assert media.relative_path == "chats/chat_1/photo.jpg"
    assert media.file_size_bytes == 204800
    assert media.mime_type == "image/jpeg"


# ── 2 ── уникальность по message_id ───────────────────────────────────────────


async def test_unique_message_id_raises_integrity_error(db_session: AsyncSession) -> None:
    message_id = await _seed_message_id(db_session)

    db_session.add(
        CommunicationsTelegramMessageMedia(message_id=message_id, media_type="photo")
    )
    await db_session.flush()

    db_session.add(
        CommunicationsTelegramMessageMedia(message_id=message_id, media_type="video_file")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── 3 ── каскадное удаление ────────────────────────────────────────────────────


async def test_cascade_delete_on_message_delete(db_session: AsyncSession) -> None:
    message_id = await _seed_message_id(db_session)

    media = CommunicationsTelegramMessageMedia(
        message_id=message_id, media_type="video_file"
    )
    db_session.add(media)
    await db_session.flush()
    media_id = media.id

    await db_session.execute(
        text("DELETE FROM communications_telegram_message WHERE id = :id"),
        {"id": message_id},
    )
    await db_session.flush()

    remaining = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM communications_telegram_message_media "
                "WHERE id = :id"
            ),
            {"id": media_id},
        )
    ).scalar()
    assert remaining == 0


# ── 4 ── nullable-поля ─────────────────────────────────────────────────────────


async def test_nullable_fields_are_null(db_session: AsyncSession) -> None:
    message_id = await _seed_message_id(db_session)

    media = CommunicationsTelegramMessageMedia(
        message_id=message_id,
        media_type="file",
    )
    db_session.add(media)
    await db_session.flush()

    assert media.id is not None
    assert media.file_name is None
    assert media.relative_path is None
    assert media.file_size_bytes is None
    assert media.mime_type is None
