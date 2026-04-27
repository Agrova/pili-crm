"""ADR-013 Task 3: tests for analysis/preflight/service.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.preflight import PREFLIGHT_VERSION
from analysis.preflight.service import (
    ChatPreview,
    build_preview,
    classify_chat,
    is_empty_chat,
    render_prompt,
    select_pending_chats,
)

SEED_PHONE = "+77471057849"


# ── seed helpers ───────────────────────────────────────────────────────────


async def _seed_account(session: AsyncSession) -> tuple[int, str]:
    aid = (
        await session.execute(
            text(
                "SELECT id FROM communications_telegram_account "
                "WHERE phone_number = :phone"
            ),
            {"phone": SEED_PHONE},
        )
    ).scalar_one()
    op_uid = "12345-operator"
    await session.execute(
        text(
            "UPDATE communications_telegram_account "
            "SET telegram_user_id = :uid WHERE id = :aid"
        ),
        {"uid": op_uid, "aid": aid},
    )
    await session.flush()
    return int(aid), op_uid


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
                "tg": f"tg-pre-{tag}-{datetime.now(tz=UTC).timestamp()}",
                "title": f"preflight test {tag}",
            },
        )
    ).scalar_one()
    await session.flush()
    return int(cid)


async def _seed_message(
    session: AsyncSession,
    chat_id: int,
    *,
    sent_at: datetime,
    from_user_id: str | None,
    text_value: str | None,
    tag: str,
) -> int:
    mid = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_message "
                "(chat_id, telegram_message_id, from_user_id, sent_at, text) "
                "VALUES (:cid, :tg, :fuid, :sent_at, :text) RETURNING id"
            ),
            {
                "cid": chat_id,
                "tg": f"pre-msg-{tag}-{datetime.now(tz=UTC).timestamp()}",
                "fuid": from_user_id,
                "sent_at": sent_at,
                "text": text_value,
            },
        )
    ).scalar_one()
    await session.flush()
    return int(mid)


async def _record_preflight(
    session: AsyncSession, chat_id: int, *, analyzer_version: str = PREFLIGHT_VERSION
) -> None:
    await session.execute(
        text(
            "INSERT INTO analysis_chat_analysis "
            "(chat_id, analyzer_version, analyzed_at, "
            " messages_analyzed_up_to, narrative_markdown, "
            " structured_extract, chunks_count, "
            " preflight_classification, preflight_confidence, "
            " preflight_reason, skipped_reason) "
            "VALUES (:cid, :ver, NOW(), '', '', "
            "        '{\"_v\": 1}'::jsonb, 0, "
            "        'client', 'high', 'r', NULL)"
        ),
        {"cid": chat_id, "ver": analyzer_version},
    )
    await session.flush()


# ── 1-3. select_pending_chats ──────────────────────────────────────────────


async def test_select_pending_chats_excludes_classified(
    db_session: AsyncSession,
) -> None:
    aid, _ = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "excl")
    await _record_preflight(db_session, chat)

    pending = await select_pending_chats(db_session, chat_id=chat)
    assert chat not in pending


async def test_select_pending_chats_returns_unreviewed(
    db_session: AsyncSession,
) -> None:
    aid, _ = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "unr")

    pending = await select_pending_chats(db_session, chat_id=chat)
    assert chat in pending


async def test_select_pending_chats_filter_by_chat_id(
    db_session: AsyncSession,
) -> None:
    aid, _ = await _seed_account(db_session)
    a = await _seed_chat(db_session, aid, "fa")
    b = await _seed_chat(db_session, aid, "fb")

    pending = await select_pending_chats(db_session, chat_id=a)
    assert pending == [a]
    assert b not in pending


# ── 4-7. build_preview ─────────────────────────────────────────────────────


async def test_build_preview_short_chat(db_session: AsyncSession) -> None:
    aid, op_uid = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "short")
    base = datetime.now(tz=UTC) - timedelta(days=1)
    for i in range(5):
        await _seed_message(
            db_session,
            chat,
            sent_at=base + timedelta(minutes=i),
            from_user_id=op_uid if i % 2 == 0 else "999",
            text_value=f"msg-{i}",
            tag=f"s{i}",
        )

    preview = await build_preview(db_session, chat, head=5, tail=5)
    assert preview.total_messages == 5
    # short chat: all in first_messages, last empty (no duplicates)
    assert len(preview.first_messages) == 5
    assert preview.last_messages == []
    texts = [t for _, t in preview.first_messages]
    assert texts == [f"msg-{i}" for i in range(5)]


async def test_build_preview_long_chat(db_session: AsyncSession) -> None:
    aid, op_uid = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "long")
    base = datetime.now(tz=UTC) - timedelta(days=10)
    for i in range(100):
        await _seed_message(
            db_session,
            chat,
            sent_at=base + timedelta(minutes=i),
            from_user_id=op_uid if i % 2 == 0 else "999",
            text_value=f"m{i}",
            tag=f"l{i}",
        )

    preview = await build_preview(db_session, chat, head=5, tail=5)
    assert preview.total_messages == 100
    assert len(preview.first_messages) == 5
    assert len(preview.last_messages) == 5
    assert [t for _, t in preview.first_messages] == [f"m{i}" for i in range(5)]
    assert [t for _, t in preview.last_messages] == [f"m{i}" for i in range(95, 100)]


async def test_build_preview_metadata_counts(db_session: AsyncSession) -> None:
    aid, op_uid = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "meta")
    base = datetime.now(tz=UTC) - timedelta(days=1)
    # 3 outgoing, 2 incoming
    for i, fuid in enumerate([op_uid, "x", op_uid, "x", op_uid]):
        await _seed_message(
            db_session, chat,
            sent_at=base + timedelta(minutes=i),
            from_user_id=fuid, text_value="t", tag=f"mt{i}",
        )
    preview = await build_preview(db_session, chat)
    assert preview.outgoing_count == 3
    assert preview.incoming_count == 2
    assert preview.total_messages == 5


async def test_build_preview_message_truncation(db_session: AsyncSession) -> None:
    aid, op_uid = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "trunc")
    long_text = "Z" * 500
    await _seed_message(
        db_session, chat,
        sent_at=datetime.now(tz=UTC),
        from_user_id=op_uid, text_value=long_text, tag="trunc1",
    )
    preview = await build_preview(db_session, chat)
    _, body = preview.first_messages[0]
    assert len(body) <= 220  # 200 chars + ellipsis
    assert body.startswith("Z" * 200)


# ── 8. render_prompt ───────────────────────────────────────────────────────


def test_render_prompt_includes_all_placeholders() -> None:
    preview = ChatPreview(
        chat_id=1,
        title="Сергей",
        total_messages=2,
        outgoing_count=1,
        incoming_count=1,
        first_message_date="2024-01-01 10:00",
        last_message_date="2024-01-02 11:00",
        first_messages=[("Оператор", "привет"), ("Клиент", "здравствуйте")],
        last_messages=[("Клиент", "ок")],
    )
    out = render_prompt(preview)
    assert "Сергей" in out
    assert "Оператор: привет" in out
    assert "Клиент: здравствуйте" in out
    assert "2024-01-01 10:00" in out
    # No unfilled placeholders {something}
    import re
    assert re.search(r"\{[a-z_]+\}", out) is None


# ── 9-12. classify_chat ────────────────────────────────────────────────────


def _preview_stub() -> ChatPreview:
    return ChatPreview(
        chat_id=1, title="x", total_messages=1, outgoing_count=0,
        incoming_count=1, first_message_date="2024-01-01 10:00",
        last_message_date="2024-01-01 10:00",
        first_messages=[("Клиент", "hi")], last_messages=[],
    )


async def test_classify_chat_parses_valid_response() -> None:
    llm = AsyncMock()
    llm.complete.return_value = (
        '{"classification": "client", "confidence": "high", "reason": "ok"}'
    )
    out = await classify_chat(1, _preview_stub(), llm, "qwen/qwen3-14b")
    assert out is not None
    assert out.classification == "client"
    assert out.confidence == "high"


async def test_classify_chat_strips_markdown_fences() -> None:
    llm = AsyncMock()
    llm.complete.return_value = (
        '```json\n'
        '{"classification": "friend", "confidence": "medium", "reason": "r"}\n'
        '```'
    )
    out = await classify_chat(1, _preview_stub(), llm, "qwen/qwen3-14b")
    assert out is not None
    assert out.classification == "friend"


async def test_classify_chat_returns_none_on_invalid_json() -> None:
    llm = AsyncMock()
    llm.complete.return_value = "not json at all"
    out = await classify_chat(1, _preview_stub(), llm, "qwen/qwen3-14b")
    assert out is None


async def test_classify_chat_returns_none_on_extra_field() -> None:
    llm = AsyncMock()
    llm.complete.return_value = (
        '{"classification": "client", "confidence": "high", '
        '"reason": "r", "extra": "nope"}'
    )
    out = await classify_chat(1, _preview_stub(), llm, "qwen/qwen3-14b")
    assert out is None


# ── 13-14. is_empty_chat ───────────────────────────────────────────────────


async def test_is_empty_chat_returns_true_for_no_messages(
    db_session: AsyncSession,
) -> None:
    aid, _ = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "empty")
    assert await is_empty_chat(db_session, chat) is True


async def test_is_empty_chat_returns_false_for_chat_with_messages(
    db_session: AsyncSession,
) -> None:
    aid, op_uid = await _seed_account(db_session)
    chat = await _seed_chat(db_session, aid, "filled")
    await _seed_message(
        db_session, chat,
        sent_at=datetime.now(tz=UTC),
        from_user_id=op_uid, text_value="hi", tag="f1",
    )
    assert await is_empty_chat(db_session, chat) is False
