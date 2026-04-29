"""ADR-011 X1 iter 4: parallel identity extraction path.

Extracts ``Identity`` directly from chunked messages (with role tags),
bypassing the narrative-pipeline's lossy compression that previously
dropped phone+address out of single delivery-string messages
(see commit cbc9e62 — chat 6544 smoke).

Pipeline integration: called after ``_build_extract`` in
``analysis/run.py``; the result is written over
``structured_extract.identity`` so the rest of the pipeline
(``apply_analysis_to_customer`` etc.) sees the lossless identity.

Context-window protection: client messages pass through untouched,
operator messages are kept only when shorter than
``OPERATOR_MAX_LEN_CHARS`` — long operator product explanations carry
no identity signal but eat the token budget on big chats (chat 5942
is ~72K chars). See ADR-011 X1 iter 4 brief, blocker resolution A+.

Graceful degrade: any failure (LLM timeout, JSON parse, validation,
LM Studio down) is logged with full traceback and yields an empty
``Identity`` whose ``confidence_notes`` carries the WARNING — the
orchestrator continues on to ``apply_analysis_to_customer``.
"""

from __future__ import annotations

import logging

from analysis.chunking import ChatMessage, format_messages_for_prompt
from analysis.llm_client import LMStudioClient
from analysis.prompts import IDENTITY_EXTRACT_PROMPT, render
from app.analysis.schemas import Identity
from app.config import OPERATOR_TELEGRAM_USER_IDS

logger = logging.getLogger(__name__)

# Long operator messages (product explanations, item descriptions) carry
# no identity signal but consume the token budget. Short operator messages
# («Добрый день, Анна», «Кристина, отправил трек») do carry name signals
# via addressings — those must be kept.
OPERATOR_MAX_LEN_CHARS = 200


def _strip_json_fence(raw: str) -> str:
    """Tolerate ``` ```json ... ``` ``` wrapping that Qwen occasionally emits."""
    s = raw.strip()
    if not s.startswith("```"):
        return s
    s = s.removeprefix("```").lstrip()
    s = s.removeprefix("json").removeprefix("JSON").lstrip()
    if s.endswith("```"):
        s = s[:-3].rstrip()
    return s


def _filter_messages_for_identity(
    chunks: list[list[ChatMessage]],
) -> list[ChatMessage]:
    """Drop long operator messages so the prompt fits the context window."""
    filtered: list[ChatMessage] = []
    for chunk in chunks:
        for msg in chunk:
            is_operator = msg.from_user_id in OPERATOR_TELEGRAM_USER_IDS
            if is_operator and len(msg.text or "") >= OPERATOR_MAX_LEN_CHARS:
                continue
            filtered.append(msg)
    return filtered


async def extract_identity_from_chunks(
    chunks: list[list[ChatMessage]],
    llm: LMStudioClient,
) -> Identity:
    """Single-pass identity extraction directly from chunked messages.

    Returns an ``Identity`` with up to all 8 fields populated where the
    LLM found them (``null`` otherwise). On any extraction failure
    (``LLMRequestError``, ``ValidationError``, ``ValueError`` from JSON
    parse, or unexpected exception) — logs the full traceback and
    returns an ``Identity`` with all-null fields plus a WARNING token
    in ``confidence_notes`` so downstream code can detect the degrade.
    """
    total = sum(len(chunk) for chunk in chunks)
    filtered = _filter_messages_for_identity(chunks)
    logger.info(
        "identity_extract: %d/%d messages after filtering "
        "(operator messages >=%d chars dropped)",
        len(filtered),
        total,
        OPERATOR_MAX_LEN_CHARS,
    )

    if not filtered:
        return Identity(
            confidence_notes="WARNING: identity extraction skipped: empty chat after filtering"
        )

    prompt = render(
        IDENTITY_EXTRACT_PROMPT,
        messages=format_messages_for_prompt(filtered),
    )

    try:
        raw = await llm.complete(prompt)
        return Identity.model_validate_json(_strip_json_fence(raw))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "identity extraction failed (%d/%d messages): %s",
            len(filtered),
            total,
            type(exc).__name__,
        )
        short = str(exc).splitlines()[0][:200] if str(exc) else ""
        return Identity(
            confidence_notes=(
                f"WARNING: identity extraction failed: "
                f"{type(exc).__name__}: {short}"
            )
        )


__all__ = [
    "OPERATOR_MAX_LEN_CHARS",
    "extract_identity_from_chunks",
]
