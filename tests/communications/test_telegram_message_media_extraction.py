"""ADR-014 Task 2: unit tests for CommunicationsTelegramMessageMediaExtraction model.

Uses the standard db_session fixture (rollback after each test).
Requires a live test DB with the schema applied (alembic upgrade head).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.communications.models import CommunicationsTelegramMessageMediaExtraction


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
            {"aid": account_id, "tg": f"tg-extraction-test-{datetime.now(tz=UTC).timestamp()}"},
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
            {"cid": chat_id, "mid": "msg-extraction-test-1"},
        )
    ).scalar_one()
    await session.flush()
    return int(message_id)


# ── 1 ── создание записи ───────────────────────────────────────────────────────


async def test_create_extraction_record(db_session: AsyncSession) -> None:
    message_id = await _seed_message_id(db_session)

    extraction = CommunicationsTelegramMessageMediaExtraction(
        message_id=message_id,
        extracted_text="[Изображение]\nОписание: Деревянная доска.",
        extraction_method="vision_qwen3-vl-30b-a3b",
        extractor_version="v1.0",
    )
    db_session.add(extraction)
    await db_session.flush()

    assert extraction.id is not None
    assert extraction.message_id == message_id
    assert extraction.extracted_text == "[Изображение]\nОписание: Деревянная доска."
    assert extraction.extraction_method == "vision_qwen3-vl-30b-a3b"
    assert extraction.extractor_version == "v1.0"
    assert extraction.created_at is not None


# ── 2 ── уникальность по message_id ───────────────────────────────────────────


async def test_unique_message_id_raises_integrity_error(db_session: AsyncSession) -> None:
    message_id = await _seed_message_id(db_session)

    db_session.add(
        CommunicationsTelegramMessageMediaExtraction(
            message_id=message_id,
            extracted_text="first",
            extraction_method="placeholder",
            extractor_version="v1.0",
        )
    )
    await db_session.flush()

    db_session.add(
        CommunicationsTelegramMessageMediaExtraction(
            message_id=message_id,
            extracted_text="second",
            extraction_method="placeholder",
            extractor_version="v1.0",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


# ── 3 ── каскадное удаление ────────────────────────────────────────────────────


async def test_cascade_delete_on_message_delete(db_session: AsyncSession) -> None:
    message_id = await _seed_message_id(db_session)

    extraction = CommunicationsTelegramMessageMediaExtraction(
        message_id=message_id,
        extracted_text="[file: doc.pdf, type: application/pdf, size: 1024 bytes]",
        extraction_method="placeholder",
        extractor_version="v1.0",
    )
    db_session.add(extraction)
    await db_session.flush()
    extraction_id = extraction.id

    await db_session.execute(
        text("DELETE FROM communications_telegram_message WHERE id = :id"),
        {"id": message_id},
    )
    await db_session.flush()

    remaining = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM communications_telegram_message_media_extraction "
                "WHERE id = :id"
            ),
            {"id": extraction_id},
        )
    ).scalar()
    assert remaining == 0


# ── 4 ── NOT NULL constraints ──────────────────────────────────────────────────


async def test_not_null_constraints(db_session: AsyncSession) -> None:
    message_id = await _seed_message_id(db_session)

    for kwargs in [
        dict(extraction_method="placeholder", extractor_version="v1.0"),
        dict(extracted_text="text", extractor_version="v1.0"),
        dict(extracted_text="text", extraction_method="placeholder"),
    ]:
        db_session.add(
            CommunicationsTelegramMessageMediaExtraction(
                message_id=message_id, **kwargs  # type: ignore[arg-type]
            )
        )
        with pytest.raises(IntegrityError):
            await db_session.flush()
        await db_session.rollback()

        # re-seed after rollback
        message_id = await _seed_message_id(db_session)
