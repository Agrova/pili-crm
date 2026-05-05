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

Multi-pass windowing (hotfix for Mac 24 GB kernel panics): when
filtered messages exceed ``IDENTITY_WINDOW_SIZE``, the extraction runs
in windows of that size and results are merged field-by-field (first
non-null value wins). This prevents GPU OOM on large chats (>1500 msg)
without losing identity signals that appear late in the conversation.

Graceful degrade: any failure (LLM timeout, JSON parse, validation,
LM Studio down) is logged with full traceback and yields an empty
``Identity`` whose ``confidence_notes`` carries the WARNING — the
orchestrator continues on to ``apply_analysis_to_customer``.
"""

from __future__ import annotations

import logging
import re

from analysis.chunking import ChatMessage, format_messages_for_prompt
from analysis.llm_client import LMStudioClient
from analysis.prompts import IDENTITY_EXTRACT_PROMPT, render
from app.analysis.schemas import Identity
from app.config import OPERATOR_NAME_VARIANTS, OPERATOR_TELEGRAM_USER_IDS

logger = logging.getLogger(__name__)

# Long operator messages (product explanations, item descriptions) carry
# no identity signal but consume the token budget. Short operator messages
# («Добрый день, Анна», «Кристина, отправил трек») do carry name signals
# via addressings — those must be kept.
OPERATOR_MAX_LEN_CHARS = 200

# Maximum filtered messages per LLM call. Larger chats are processed in
# windows of this size and merged. Keeps prompt ~5-8K chars, safe for
# context_length=16384 on Mac 24 GB (prevents IOGPUFamily kernel panic).
IDENTITY_WINDOW_SIZE = 200


def _is_operator_name(name: str | None) -> bool:
    """Return True if any token in `name` matches OPERATOR_NAME_VARIANTS.

    Tokenization: split by non-word chars (preserving hyphens), lowercase.
    None or empty input returns False (defensive guard).
    """
    if name is None:
        return False
    tokens = re.findall(r"[\w-]+", name.lower(), flags=re.UNICODE)
    return any(token in OPERATOR_NAME_VARIANTS for token in tokens)


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


def _merge_identities(results: list[Identity]) -> Identity:
    """Merge multiple Identity results: first non-null value wins per field.

    ``confidence_notes`` from all windows are concatenated so the audit
    trail is preserved.
    """
    merged = Identity()
    notes: list[str] = []
    fields = [
        "name_guess", "phone", "address", "email",
        "instagram", "telegram_username", "city", "confidence_notes",
    ]
    for identity in results:
        for field in fields:
            if field == "confidence_notes":
                val = getattr(identity, field, None)
                if val:
                    notes.append(val)
                continue
            if getattr(merged, field, None) is None:
                val = getattr(identity, field, None)
                if val is not None:
                    setattr(merged, field, val)
    if notes:
        merged.confidence_notes = " | ".join(notes)
    return merged


async def _extract_window(
    window: list[ChatMessage],
    llm: LMStudioClient,
    window_idx: int,
    total_filtered: int,
    total_messages: int,
) -> Identity:
    """Run a single identity extraction LLM call for one window."""
    prompt = render(
        IDENTITY_EXTRACT_PROMPT,
        messages=format_messages_for_prompt(window),
    )
    try:
        raw = await llm.complete(prompt)
        return Identity.model_validate_json(_strip_json_fence(raw))
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "identity extraction failed window=%d (%d/%d messages): %s",
            window_idx,
            total_filtered,
            total_messages,
            type(exc).__name__,
        )
        short = str(exc).splitlines()[0][:200] if str(exc) else ""
        return Identity(
            confidence_notes=(
                f"WARNING: identity extraction failed window={window_idx}: "
                f"{type(exc).__name__}: {short}"
            )
        )


async def extract_identity_from_chunks(
    chunks: list[list[ChatMessage]],
    llm: LMStudioClient,
) -> Identity:
    """Multi-pass identity extraction directly from chunked messages.

    Splits filtered messages into windows of ``IDENTITY_WINDOW_SIZE`` and
    runs one LLM call per window, then merges results (first non-null wins).
    This prevents GPU OOM on large chats while preserving all identity
    signals regardless of where they appear in the conversation.

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

    # Split into windows
    windows = [
        filtered[i : i + IDENTITY_WINDOW_SIZE]
        for i in range(0, len(filtered), IDENTITY_WINDOW_SIZE)
    ]
    n_windows = len(windows)
    if n_windows > 1:
        logger.info(
            "identity_extract: %d messages → %d windows of %d",
            len(filtered),
            n_windows,
            IDENTITY_WINDOW_SIZE,
        )

    results: list[Identity] = []
    for idx, window in enumerate(windows):
        identity = await _extract_window(window, llm, idx, len(filtered), total)
        results.append(identity)

    identity = _merge_identities(results) if len(results) > 1 else results[0]

    # Operator-name blocklist (v1.4 final sanity check)
    if _is_operator_name(identity.name_guess):
        logger.warning(
            "identity_extract: name_guess=%r rejected (matches operator name variant)",
            identity.name_guess,
        )
        original_name = identity.name_guess
        identity.name_guess = None
        note = f" [REJECTED operator name: {original_name!r}]"
        identity.confidence_notes = (identity.confidence_notes or "") + note

    return identity


__all__ = [
    "IDENTITY_WINDOW_SIZE",
    "OPERATOR_MAX_LEN_CHARS",
    "extract_identity_from_chunks",
]
