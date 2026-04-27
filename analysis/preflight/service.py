"""ADR-013 Task 3: preflight service layer.

Selector, preview builder, prompt renderer, LLM-classify wrapper, and the
empty-chat probe. Persistence happens via
``app.analysis.service.record_skipped_analysis`` — preflight rows always
satisfy the skipped-consistency CHECK constraint (empty narrative,
``{"_v": 1}`` extract).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.preflight import (
    MESSAGE_TEXT_MAX_CHARS,
    PREVIEW_HEAD,
    PREVIEW_TAIL,
)
from analysis.preflight.prompts import PREFLIGHT_PROMPT_TEMPLATE
from app.analysis.schemas import PreflightClassification

logger = logging.getLogger(__name__)


# Operator label / client label rendered in the prompt preview.
OPERATOR_LABEL = "Оператор"
CLIENT_LABEL = "Клиент"

_ALLOWED_KEYS = {"classification", "confidence", "reason"}


class _LLMClient(Protocol):
    async def complete(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class ChatPreview:
    chat_id: int
    title: str | None
    total_messages: int
    outgoing_count: int
    incoming_count: int
    first_message_date: str | None
    last_message_date: str | None
    first_messages: list[tuple[str, str]]
    last_messages: list[tuple[str, str]]


# ── selector ───────────────────────────────────────────────────────────────


async def select_pending_chats(
    session: AsyncSession,
    *,
    chat_id: int | None = None,
    skip_existing: bool = True,
) -> list[int]:
    """Return chat ids that need preflight classification.

    ``skip_existing=True`` excludes chats that already have any
    ``analysis_chat_analysis`` row with a non-NULL
    ``preflight_classification``. ``chat_id`` narrows to a single chat.
    """
    sql = text(
        """
        SELECT ch.id
        FROM communications_telegram_chat ch
        WHERE
            (CAST(:chat_id AS BIGINT) IS NULL OR ch.id = CAST(:chat_id AS BIGINT))
            AND (
                NOT CAST(:skip_existing AS BOOLEAN)
                OR NOT EXISTS (
                    SELECT 1 FROM analysis_chat_analysis a
                    WHERE a.chat_id = ch.id
                      AND a.preflight_classification IS NOT NULL
                )
            )
        ORDER BY ch.id
        """
    )
    rows = await session.execute(
        sql, {"chat_id": chat_id, "skip_existing": skip_existing}
    )
    return [int(r[0]) for r in rows]


# ── preview builder ────────────────────────────────────────────────────────


def _truncate(s: str | None) -> str:
    if s is None:
        return ""
    s = s.strip()
    if len(s) > MESSAGE_TEXT_MAX_CHARS:
        return s[:MESSAGE_TEXT_MAX_CHARS] + "…"
    return s


def _format_dt(value: object) -> str | None:
    if value is None:
        return None
    # value is a timezone-aware datetime from PG
    return value.strftime("%Y-%m-%d %H:%M")  # type: ignore[attr-defined]


async def build_preview(
    session: AsyncSession,
    chat_id: int,
    head: int = PREVIEW_HEAD,
    tail: int = PREVIEW_TAIL,
) -> ChatPreview:
    """Materialise a ``ChatPreview`` for one chat."""
    title_row = await session.execute(
        text("SELECT title FROM communications_telegram_chat WHERE id = :cid"),
        {"cid": chat_id},
    )
    title: str | None = title_row.scalar()

    operator_user_id_row = await session.execute(
        text(
            "SELECT a.telegram_user_id "
            "FROM communications_telegram_account a "
            "JOIN communications_telegram_chat c "
            "  ON c.owner_account_id = a.id "
            "WHERE c.id = :cid"
        ),
        {"cid": chat_id},
    )
    operator_user_id: str | None = operator_user_id_row.scalar()

    metadata_row = (
        await session.execute(
            text(
                "SELECT "
                "  COUNT(*) AS total, "
                "  COUNT(*) FILTER (WHERE from_user_id = :ouid) AS outgoing, "
                "  MIN(sent_at) AS first_at, "
                "  MAX(sent_at) AS last_at "
                "FROM communications_telegram_message WHERE chat_id = :cid"
            ),
            {"cid": chat_id, "ouid": operator_user_id},
        )
    ).one()
    total = int(metadata_row.total or 0)
    outgoing = int(metadata_row.outgoing or 0)
    incoming = total - outgoing

    first_messages: list[tuple[str, str]] = []
    last_messages: list[tuple[str, str]] = []

    if total > 0:
        if total <= head + tail:
            rows = (
                await session.execute(
                    text(
                        "SELECT from_user_id, text "
                        "FROM communications_telegram_message "
                        "WHERE chat_id = :cid "
                        "ORDER BY sent_at, id"
                    ),
                    {"cid": chat_id},
                )
            ).all()
            all_msgs = [
                (
                    OPERATOR_LABEL
                    if (operator_user_id is not None and r.from_user_id == operator_user_id)
                    else CLIENT_LABEL,
                    _truncate(r.text),
                )
                for r in rows
            ]
            first_messages = all_msgs
            last_messages = []
        else:
            head_rows = (
                await session.execute(
                    text(
                        "SELECT from_user_id, text "
                        "FROM communications_telegram_message "
                        "WHERE chat_id = :cid "
                        "ORDER BY sent_at, id "
                        "LIMIT :lim"
                    ),
                    {"cid": chat_id, "lim": head},
                )
            ).all()
            tail_rows = (
                await session.execute(
                    text(
                        "SELECT from_user_id, text FROM ("
                        "  SELECT from_user_id, text, sent_at, id "
                        "  FROM communications_telegram_message "
                        "  WHERE chat_id = :cid "
                        "  ORDER BY sent_at DESC, id DESC LIMIT :lim"
                        ") t ORDER BY sent_at, id"
                    ),
                    {"cid": chat_id, "lim": tail},
                )
            ).all()
            first_messages = [
                (
                    OPERATOR_LABEL
                    if (operator_user_id is not None and r.from_user_id == operator_user_id)
                    else CLIENT_LABEL,
                    _truncate(r.text),
                )
                for r in head_rows
            ]
            last_messages = [
                (
                    OPERATOR_LABEL
                    if (operator_user_id is not None and r.from_user_id == operator_user_id)
                    else CLIENT_LABEL,
                    _truncate(r.text),
                )
                for r in tail_rows
            ]

    return ChatPreview(
        chat_id=chat_id,
        title=title,
        total_messages=total,
        outgoing_count=outgoing,
        incoming_count=incoming,
        first_message_date=_format_dt(metadata_row.first_at),
        last_message_date=_format_dt(metadata_row.last_at),
        first_messages=first_messages,
        last_messages=last_messages,
    )


# ── prompt renderer ────────────────────────────────────────────────────────


def _format_messages(messages: list[tuple[str, str]]) -> str:
    if not messages:
        return "(нет)"
    return "\n".join(f"{sender}: {text_}" for sender, text_ in messages)


def render_prompt(preview: ChatPreview) -> str:
    return PREFLIGHT_PROMPT_TEMPLATE.format(
        title=preview.title or "(без названия)",
        total_messages=preview.total_messages,
        outgoing_count=preview.outgoing_count,
        incoming_count=preview.incoming_count,
        first_message_date=preview.first_message_date or "нет",
        last_message_date=preview.last_message_date or "нет",
        first_5_messages=_format_messages(preview.first_messages),
        last_5_messages=_format_messages(preview.last_messages),
    )


# ── LLM classify ───────────────────────────────────────────────────────────


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```json"):
        s = s.removeprefix("```json")
    elif s.startswith("```"):
        s = s.removeprefix("```")
    s = s.removesuffix("```")
    return s.strip()


async def classify_chat(
    chat_id: int,
    preview: ChatPreview,
    llm_client: _LLMClient,
    model_id: str,
) -> PreflightClassification | None:
    """Run one LLM round-trip and parse the verdict.

    Returns ``None`` when the response is not valid JSON, contains keys
    outside the allowed schema, or fails Pydantic validation. Errors are
    logged but never raised — preflight is best-effort.
    """
    del model_id  # client auto-detects loaded model
    prompt = render_prompt(preview)
    try:
        raw = await llm_client.complete(prompt)
    except Exception:
        logger.exception("preflight: LLM request failed for chat_id=%s", chat_id)
        return None

    body = _strip_json_fence(raw)
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        logger.error(
            "preflight: invalid JSON from LLM for chat_id=%s — %r", chat_id, raw
        )
        return None

    if not isinstance(parsed, dict):
        logger.error(
            "preflight: LLM returned non-object for chat_id=%s — %r", chat_id, parsed
        )
        return None

    extra_keys = set(parsed.keys()) - _ALLOWED_KEYS
    if extra_keys:
        logger.error(
            "preflight: LLM returned extra keys %r for chat_id=%s",
            extra_keys, chat_id,
        )
        return None

    try:
        return PreflightClassification.model_validate(parsed)
    except ValidationError as exc:
        logger.error(
            "preflight: pydantic validation failed for chat_id=%s — %s",
            chat_id, exc,
        )
        return None


# ── empty-chat probe ───────────────────────────────────────────────────────


async def is_empty_chat(session: AsyncSession, chat_id: int) -> bool:
    count = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM communications_telegram_message "
                "WHERE chat_id = :cid"
            ),
            {"cid": chat_id},
        )
    ).scalar()
    return int(count or 0) == 0
