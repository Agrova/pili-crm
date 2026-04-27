"""Tests for media_extract preflight classification filter (ADR-014 / ADR-013)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.media_extract.cli import build_parser
from analysis.media_extract.service import select_pending_messages

EXTRACTOR_VERSION = "v1.0-filter-test"
SEED_PHONE = "+77471057849"


# ── seed helpers ──────────────────────────────────────────────────────────────


async def _seed_account_id(session: AsyncSession) -> int:
    return int(
        (
            await session.execute(
                text(
                    "SELECT id FROM communications_telegram_account "
                    "WHERE phone_number = :phone"
                ),
                {"phone": SEED_PHONE},
            )
        ).scalar_one()
    )


async def _seed_chat(session: AsyncSession, account_id: int, tag: str) -> int:
    cid = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type, title) "
                "VALUES (:aid, :tg, 'personal_chat', :title) RETURNING id"
            ),
            {
                "aid": account_id,
                "tg": f"tg-flt-{tag}-{datetime.now(tz=UTC).timestamp()}",
                "title": f"filter test {tag}",
            },
        )
    ).scalar_one()
    await session.flush()
    return int(cid)


async def _seed_photo_message(
    session: AsyncSession, chat_id: int, *, tag: str
) -> int:
    mid = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_message "
                "(chat_id, telegram_message_id, sent_at) "
                "VALUES (:cid, :tg, NOW()) RETURNING id"
            ),
            {
                "cid": chat_id,
                "tg": f"flt-msg-{tag}-{datetime.now(tz=UTC).timestamp()}",
            },
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO communications_telegram_message_media "
            "(message_id, media_type, mime_type, file_name, "
            " relative_path, file_size_bytes) "
            "VALUES (:mid, 'photo', 'image/jpeg', 'p.jpg', "
            "        'chats/x/p.jpg', 1024)"
        ),
        {"mid": mid},
    )
    await session.flush()
    return int(mid)


async def _seed_preflight(
    session: AsyncSession,
    chat_id: int,
    *,
    classification: str | None,
    analyzer_version: str = "v1.0-test",
    analyzed_at: datetime | None = None,
) -> None:
    ts = analyzed_at or datetime.now(tz=UTC)
    await session.execute(
        text(
            "INSERT INTO analysis_chat_analysis "
            "(chat_id, analyzed_at, analyzer_version, messages_analyzed_up_to, "
            " narrative_markdown, structured_extract, chunks_count, "
            " preflight_classification) "
            "VALUES (:chat_id, :analyzed_at, :analyzer_version, 'all', "
            "        '', '{\"_v\": 1}'::jsonb, 0, :classification)"
        ),
        {
            "chat_id": chat_id,
            "analyzed_at": ts,
            "analyzer_version": analyzer_version,
            "classification": classification,
        },
    )
    await session.flush()


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_filter_default_passes_client_and_possible_client(
    db_session: AsyncSession,
) -> None:
    """client/possible_client filter includes client chat, excludes not_client."""
    aid = await _seed_account_id(db_session)
    chat_client = await _seed_chat(db_session, aid, tag="fc-client")
    chat_not = await _seed_chat(db_session, aid, tag="fc-not")

    await _seed_preflight(db_session, chat_client, classification="client")
    await _seed_preflight(db_session, chat_not, classification="not_client")

    msg_client = await _seed_photo_message(db_session, chat_client, tag="fc-client")
    msg_not = await _seed_photo_message(db_session, chat_not, tag="fc-not")

    found = await select_pending_messages(
        db_session,
        extractor_version=EXTRACTOR_VERSION,
        allowed_classifications={"client", "possible_client"},
    )
    ids = {m.message_id for m in found}
    assert msg_client in ids
    assert msg_not not in ids


async def test_filter_unknown_includes_chats_without_preflight(
    db_session: AsyncSession,
) -> None:
    """'unknown' filter includes chats with no preflight record, excludes not_client."""
    aid = await _seed_account_id(db_session)
    chat_nopre = await _seed_chat(db_session, aid, tag="fu-nopre")
    chat_not = await _seed_chat(db_session, aid, tag="fu-not")

    await _seed_preflight(db_session, chat_not, classification="not_client")
    # no preflight for chat_nopre

    msg_nopre = await _seed_photo_message(db_session, chat_nopre, tag="fu-nopre")
    msg_not = await _seed_photo_message(db_session, chat_not, tag="fu-not")

    found = await select_pending_messages(
        db_session,
        extractor_version=EXTRACTOR_VERSION,
        allowed_classifications={"unknown"},
    )
    ids = {m.message_id for m in found}
    assert msg_nopre in ids
    assert msg_not not in ids


async def test_filter_unknown_combined_with_client(
    db_session: AsyncSession,
) -> None:
    """'client,unknown' includes client and no-preflight chats, excludes not_client."""
    aid = await _seed_account_id(db_session)
    chat_client = await _seed_chat(db_session, aid, tag="fuc-client")
    chat_nopre = await _seed_chat(db_session, aid, tag="fuc-nopre")
    chat_not = await _seed_chat(db_session, aid, tag="fuc-not")

    await _seed_preflight(db_session, chat_client, classification="client")
    await _seed_preflight(db_session, chat_not, classification="not_client")
    # no preflight for chat_nopre

    msg_client = await _seed_photo_message(db_session, chat_client, tag="fuc-client")
    msg_nopre = await _seed_photo_message(db_session, chat_nopre, tag="fuc-nopre")
    msg_not = await _seed_photo_message(db_session, chat_not, tag="fuc-not")

    found = await select_pending_messages(
        db_session,
        extractor_version=EXTRACTOR_VERSION,
        allowed_classifications={"client", "unknown"},
    )
    ids = {m.message_id for m in found}
    assert msg_client in ids
    assert msg_nopre in ids
    assert msg_not not in ids


async def test_filter_all_disables_filter(
    db_session: AsyncSession,
) -> None:
    """'all' processes chats with any classification."""
    aid = await _seed_account_id(db_session)
    chat_a = await _seed_chat(db_session, aid, tag="fall-a")
    chat_b = await _seed_chat(db_session, aid, tag="fall-b")
    chat_c = await _seed_chat(db_session, aid, tag="fall-c")

    await _seed_preflight(db_session, chat_a, classification="client")
    await _seed_preflight(db_session, chat_b, classification="not_client")
    await _seed_preflight(db_session, chat_c, classification="family")

    msg_a = await _seed_photo_message(db_session, chat_a, tag="fall-a")
    msg_b = await _seed_photo_message(db_session, chat_b, tag="fall-b")
    msg_c = await _seed_photo_message(db_session, chat_c, tag="fall-c")

    found = await select_pending_messages(
        db_session,
        extractor_version=EXTRACTOR_VERSION,
        allowed_classifications={"all"},
    )
    ids = {m.message_id for m in found}
    assert msg_a in ids
    assert msg_b in ids
    assert msg_c in ids


async def test_filter_uses_latest_preflight_record(
    db_session: AsyncSession,
) -> None:
    """Latest preflight record by analyzed_at is used; older not_client is ignored."""
    aid = await _seed_account_id(db_session)
    chat = await _seed_chat(db_session, aid, tag="flat-chat")

    old_time = datetime(2026, 1, 1, tzinfo=UTC)
    new_time = datetime(2026, 4, 27, tzinfo=UTC)

    await _seed_preflight(
        db_session,
        chat,
        classification="not_client",
        analyzer_version="v0.9-old",
        analyzed_at=old_time,
    )
    await _seed_preflight(
        db_session,
        chat,
        classification="client",
        analyzer_version="v1.0-new",
        analyzed_at=new_time,
    )

    msg = await _seed_photo_message(db_session, chat, tag="flat-msg")

    found = await select_pending_messages(
        db_session,
        extractor_version=EXTRACTOR_VERSION,
        allowed_classifications={"client", "possible_client"},
    )
    assert msg in {m.message_id for m in found}


def test_filter_invalid_value_rejected() -> None:
    """--classification with unknown values raises argparse error (SystemExit != 0)."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--all", "--classification", "garbage"])
    assert exc_info.value.code != 0


async def test_filter_with_pc_suffix_version(
    db_session: AsyncSession,
) -> None:
    """Non-standard analyzer_version is handled correctly; filter is by classification only."""
    aid = await _seed_account_id(db_session)
    chat = await _seed_chat(db_session, aid, tag="fpc-chat")

    await _seed_preflight(
        db_session,
        chat,
        classification="client",
        analyzer_version="v1.0+qwen3-14b@pc",
    )
    msg = await _seed_photo_message(db_session, chat, tag="fpc-msg")

    found = await select_pending_messages(
        db_session,
        extractor_version=EXTRACTOR_VERSION,
        allowed_classifications={"client", "possible_client"},
    )
    assert msg in {m.message_id for m in found}
