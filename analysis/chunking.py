"""ADR-011 Task 3: chunking — load chat messages and split into chunks.

Phase 1 of the analysis pipeline. Reads ``communications_telegram_message``
rows for a given chat (``text IS NOT NULL``, ordered by ``sent_at``),
returns lightweight ``ChatMessage`` records, and splits the sequence
into fixed-size chunks. Default chunk size is **300** (ADR-011 §4),
configurable via ``--chunk-size`` on ``analysis/run.py``.

The formatter ``format_messages_for_prompt`` produces lines of the
shape ``[YYYY-MM-DD HH:MM | id=12345 | from_user] text`` — the
``id=N`` token is essential, downstream prompts (``CHUNK_SUMMARY_PROMPT``
and onward) trace facts back through it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.communications.models import CommunicationsTelegramMessage

DEFAULT_CHUNK_SIZE = 300


@dataclass(frozen=True)
class ChatMessage:
    """One Telegram message ready for prompt rendering."""

    telegram_message_id: str
    sent_at: datetime
    from_user_id: str | None
    text: str


async def load_chat_messages(
    session: AsyncSession, chat_id: int
) -> list[ChatMessage]:
    """Load all text-bearing messages for ``chat_id``, ordered by ``sent_at``.

    Messages with ``text IS NULL`` are skipped at the SQL layer.
    """
    stmt = (
        select(
            CommunicationsTelegramMessage.telegram_message_id,
            CommunicationsTelegramMessage.sent_at,
            CommunicationsTelegramMessage.from_user_id,
            CommunicationsTelegramMessage.text,
        )
        .where(
            CommunicationsTelegramMessage.chat_id == chat_id,
            CommunicationsTelegramMessage.text.is_not(None),
        )
        .order_by(CommunicationsTelegramMessage.sent_at)
    )
    result = await session.execute(stmt)
    return [
        ChatMessage(
            telegram_message_id=row.telegram_message_id,
            sent_at=row.sent_at,
            from_user_id=row.from_user_id,
            text=row.text,
        )
        for row in result
    ]


def split_into_chunks(
    messages: list[ChatMessage], chunk_size: int = DEFAULT_CHUNK_SIZE
) -> list[list[ChatMessage]]:
    """Split a flat message sequence into fixed-size chunks.

    Last chunk may be shorter. Empty input → empty list (not ``[[]]``).
    """
    if chunk_size <= 0:
        raise ValueError(f"chunk_size must be positive, got {chunk_size}")
    if not messages:
        return []
    return [
        messages[i : i + chunk_size] for i in range(0, len(messages), chunk_size)
    ]


def format_messages_for_prompt(messages: list[ChatMessage]) -> str:
    """Render a chunk as the prompt-friendly multi-line block.

    Convention: ``[YYYY-MM-DD HH:MM | id=12345 | from_user] text``.
    """
    lines = []
    for m in messages:
        ts = m.sent_at.strftime("%Y-%m-%d %H:%M")
        from_user = m.from_user_id or "unknown"
        lines.append(f"[{ts} | id={m.telegram_message_id} | {from_user}] {m.text}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "ChatMessage",
    "load_chat_messages",
    "split_into_chunks",
    "format_messages_for_prompt",
]
