"""ADR-011 Task 3: tests for analysis/chunking.py.

Five tests per TZ:

1. Empty chat → empty chunk list (not ``[[]]``).
2. Chat shorter than chunk_size → single chunk.
3. Large chat → N chunks, last may be partial.
4. ``sent_at`` ordering preserved across DB read.
5. Messages with ``text IS NULL`` are skipped at the SQL layer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Side-effect imports: FK targets must be mapped before tests run.
import app.catalog.models  # noqa: F401
import app.communications.models  # noqa: F401
import app.orders.models  # noqa: F401
import app.pricing.models  # noqa: F401
from analysis.chunking import (
    DEFAULT_CHUNK_SIZE,
    ChatMessage,
    format_messages_for_prompt,
    load_chat_messages,
    split_into_chunks,
)


async def _seed_chat(db: AsyncSession, title: str) -> int:
    account_id = (
        await db.execute(
            text(
                "SELECT id FROM communications_telegram_account "
                "WHERE phone_number = '+77471057849'"
            )
        )
    ).scalar()
    assert account_id is not None, "ADR-012 seed account missing"
    chat_id = (
        await db.execute(
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type, title) "
                "VALUES (:aid, :tg, 'personal_chat', :t) RETURNING id"
            ),
            {
                "aid": account_id,
                "tg": f"chunk-{title}-{datetime.now(tz=UTC).timestamp()}",
                "t": title,
            },
        )
    ).scalar_one()
    await db.flush()
    return int(chat_id)


async def _seed_message(
    db: AsyncSession,
    chat_id: int,
    *,
    body: str | None,
    sent_at: datetime,
    tmid: str,
) -> None:
    await db.execute(
        text(
            "INSERT INTO communications_telegram_message "
            "(chat_id, telegram_message_id, sent_at, text) "
            "VALUES (:cid, :tmid, :sent, :body)"
        ),
        {"cid": chat_id, "tmid": tmid, "sent": sent_at, "body": body},
    )
    await db.flush()


# ── DB-backed tests for load_chat_messages ──────────────────────────────────


async def test_chunking_empty_chat_returns_no_chunks(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "empty")
    messages = await load_chat_messages(db_session, chat_id)
    assert messages == []
    assert split_into_chunks(messages) == []


async def test_chunking_small_chat_returns_one_chunk(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "small")
    base = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    for i in range(10):
        await _seed_message(
            db_session,
            chat_id,
            body=f"msg {i}",
            sent_at=base + timedelta(minutes=i),
            tmid=f"sm-{i}",
        )
    messages = await load_chat_messages(db_session, chat_id)
    chunks = split_into_chunks(messages, chunk_size=DEFAULT_CHUNK_SIZE)
    assert len(chunks) == 1
    assert len(chunks[0]) == 10


async def test_chunking_large_chat_partial_last_chunk(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "large")
    base = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    for i in range(7):
        await _seed_message(
            db_session,
            chat_id,
            body=f"msg {i}",
            sent_at=base + timedelta(minutes=i),
            tmid=f"lg-{i}",
        )
    messages = await load_chat_messages(db_session, chat_id)
    chunks = split_into_chunks(messages, chunk_size=3)
    assert [len(c) for c in chunks] == [3, 3, 1]


async def test_chunking_preserves_sent_at_order(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, "ordered")
    base = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    # Insert out of chronological order to verify SQL ORDER BY works.
    for tmid, offset_min in [("c", 30), ("a", 0), ("b", 15)]:
        await _seed_message(
            db_session,
            chat_id,
            body=f"msg-{tmid}",
            sent_at=base + timedelta(minutes=offset_min),
            tmid=f"ord-{tmid}",
        )
    messages = await load_chat_messages(db_session, chat_id)
    assert [m.text for m in messages] == ["msg-a", "msg-b", "msg-c"]


async def test_chunking_skips_text_is_null(db_session: AsyncSession) -> None:
    chat_id = await _seed_chat(db_session, "nulls")
    base = datetime(2025, 1, 1, 10, 0, tzinfo=UTC)
    await _seed_message(
        db_session, chat_id, body="present", sent_at=base, tmid="n-1"
    )
    await _seed_message(
        db_session,
        chat_id,
        body=None,
        sent_at=base + timedelta(minutes=1),
        tmid="n-2",
    )
    await _seed_message(
        db_session,
        chat_id,
        body="also present",
        sent_at=base + timedelta(minutes=2),
        tmid="n-3",
    )
    messages = await load_chat_messages(db_session, chat_id)
    assert [m.text for m in messages] == ["present", "also present"]


# ── Pure-Python helpers ─────────────────────────────────────────────────────


def test_split_into_chunks_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError, match="chunk_size must be positive"):
        split_into_chunks(
            [
                ChatMessage(
                    telegram_message_id="1",
                    sent_at=datetime(2025, 1, 1, tzinfo=UTC),
                    from_user_id=None,
                    text="x",
                )
            ],
            chunk_size=0,
        )


def test_format_messages_for_prompt_includes_id_token() -> None:
    msgs = [
        ChatMessage(
            telegram_message_id="42",
            sent_at=datetime(2025, 3, 15, 14, 30, tzinfo=UTC),
            from_user_id="user_a",
            text="привет",
        ),
        ChatMessage(
            telegram_message_id="43",
            sent_at=datetime(2025, 3, 15, 14, 31, tzinfo=UTC),
            from_user_id=None,
            text="и тебе",
        ),
    ]
    rendered = format_messages_for_prompt(msgs)
    assert "[2025-03-15 14:30 | id=42 | клиент] привет" in rendered
    assert "[2025-03-15 14:31 | id=43 | клиент] и тебе" in rendered
    # Точная подстрока с закрывающей скобкой — гарантия, что role tag
    # не разъехался в формате (например, лишний пробел или другой суффикс).
    assert "| клиент]" in rendered


def test_chunking_tags_operator_messages_ru_account() -> None:
    """from_user_id == primary RU operator → tagged [операт.]."""
    msgs = [
        ChatMessage(
            telegram_message_id="100",
            sent_at=datetime(2025, 3, 15, 12, 0, tzinfo=UTC),
            from_user_id="user5748681414",
            text="отправил трек",
        )
    ]
    rendered = format_messages_for_prompt(msgs)
    assert "[2025-03-15 12:00 | id=100 | операт.] отправил трек" in rendered


def test_chunking_tags_operator_messages_kz_account() -> None:
    """from_user_id == secondary KZ operator → tagged [операт.]."""
    msgs = [
        ChatMessage(
            telegram_message_id="101",
            sent_at=datetime(2025, 3, 15, 12, 1, tzinfo=UTC),
            from_user_id="user565055562",
            text="есть в наличии",
        )
    ]
    rendered = format_messages_for_prompt(msgs)
    assert "[2025-03-15 12:01 | id=101 | операт.] есть в наличии" in rendered


def test_chunking_tags_client_messages() -> None:
    """from_user_id != any operator → tagged [клиент]."""
    msgs = [
        ChatMessage(
            telegram_message_id="200",
            sent_at=datetime(2025, 3, 15, 12, 5, tzinfo=UTC),
            from_user_id="user326226592",
            text="нужен рубанок",
        )
    ]
    rendered = format_messages_for_prompt(msgs)
    assert "[2025-03-15 12:05 | id=200 | клиент] нужен рубанок" in rendered


def test_chunking_handles_null_from_user_id() -> None:
    """from_user_id is None → fallback to [клиент] (defensive default)."""
    msgs = [
        ChatMessage(
            telegram_message_id="300",
            sent_at=datetime(2025, 3, 15, 12, 10, tzinfo=UTC),
            from_user_id=None,
            text="старое сообщение без user_id",
        )
    ]
    rendered = format_messages_for_prompt(msgs)
    assert (
        "[2025-03-15 12:10 | id=300 | клиент] старое сообщение без user_id"
        in rendered
    )


def test_chunking_handles_mixed_messages() -> None:
    """Список оператор-RU + оператор-KZ + клиент тегируется корректно построчно."""
    base = datetime(2025, 3, 15, 12, 0, tzinfo=UTC)
    msgs = [
        ChatMessage(
            telegram_message_id="1",
            sent_at=base,
            from_user_id="user5748681414",
            text="оп RU",
        ),
        ChatMessage(
            telegram_message_id="2",
            sent_at=base + timedelta(minutes=1),
            from_user_id="user565055562",
            text="оп KZ",
        ),
        ChatMessage(
            telegram_message_id="3",
            sent_at=base + timedelta(minutes=2),
            from_user_id="user326226592",
            text="клиент",
        ),
    ]
    lines = format_messages_for_prompt(msgs).splitlines()
    assert len(lines) == 3
    assert "| операт.] оп RU" in lines[0]
    assert "| операт.] оп KZ" in lines[1]
    assert "| клиент] клиент" in lines[2]
