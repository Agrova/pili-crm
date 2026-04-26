"""ADR-011 Task 3: CLI orchestrator for the analysis pipeline.

End-to-end batch driver that, for each selected Telegram chat:

1. Loads messages → fixed-size chunks (``analysis.chunking``).
2. Per-chunk summary → master summary → narrative (``MASTER_SUMMARY_PROMPT``,
   ``NARRATIVE_PROMPT`` from ``analysis.prompts``).
3. Strict-JSON ``StructuredExtract`` from the narrative
   (``STRUCTURED_EXTRACT_PROMPT`` or ``…_WITH_SCHEMA`` variant) with up to
   ``EXTRACT_RETRY_ATTEMPTS`` retries on Pydantic validation failure.
4. Catalog matching for every ``OrderItem`` (``analysis.matching``).
5. Persistence via the public service layer
   (``app.analysis.service.record_full_analysis`` +
   ``apply_analysis_to_customer``). The orchestrator never writes SQL of
   its own — module boundaries (ADR-001) are respected.

Operational features:

- **Lock / stale-state check** at startup (``analysis.state_check``).
  Another live process → exit code 1. Stale checkpoints → ``--resume`` /
  ``--restart`` / interactive prompt.
- **SIGINT** (Ctrl-C) sets a global flag; the per-chat loop checks it
  between chats and exits cleanly. Hitting Ctrl-C twice within 5s force-kills
  via ``KeyboardInterrupt``.
- **Selectors**: ``--chat-id``, ``--chat-ids``, ``--all``, ``--since``,
  ``--review-status`` are mutually exclusive (one is required, except for
  ``--status`` mode).
- **--dry-run** prints the planned chat list and exits.
- **--status** prints the current state of ``analysis_chat_analysis_state``
  and exits.
- **--force** re-applies analyses that already produced orders. *Confirmed*
  and *cancelled* orders are preserved by the service layer — only analyzer-
  drafted rows in ``analysis_created_entities`` are rolled back.
- **--prompt-variant {example,schema}** picks the structured-extract prompt
  flavour for A/B testing on the first real-chat run.

Auto-detected ``ANALYZER_VERSION`` from ``app.analysis`` is used everywhere —
do **not** hard-code the string here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analysis import state_check
from analysis.chunking import (
    DEFAULT_CHUNK_SIZE,
    format_messages_for_prompt,
    load_chat_messages,
    split_into_chunks,
)
from analysis.llm_client import LLMRequestError, LMStudioClient
from analysis.matching import (
    CatalogEntry,
    load_catalog,
    match_extract,
)
from analysis.prompts import (
    CHUNK_SUMMARY_PROMPT,
    MASTER_SUMMARY_PROMPT,
    NARRATIVE_PROMPT,
    STRUCTURED_EXTRACT_PROMPT,
    STRUCTURED_EXTRACT_PROMPT_WITH_SCHEMA,
    render,
)
from app.analysis import ANALYZER_VERSION
from app.analysis.exceptions import (
    AnalysisAlreadyAppliedError,
    MultipleCustomersForChatError,
)
from app.analysis.models import AnalysisChatAnalysis, AnalysisChatAnalysisState
from app.analysis.schemas import StructuredExtract
from app.analysis.service import (
    apply_analysis_to_customer,
    mark_done,
    mark_failed,
    record_full_analysis,
    set_stage,
    update_chunk_progress,
)
from app.communications.models import (
    CommunicationsTelegramChat,
    CommunicationsTelegramMessage,
    TelegramChatReviewStatus,
)
from app.database import async_session_factory

logger = logging.getLogger("analysis.run")

EXTRACT_RETRY_ATTEMPTS = 3
SIGINT_FORCE_KILL_WINDOW_SECONDS = 5.0

# Constrained-generation schema for structured_extract — forces LM Studio to
# emit a JSON object matching StructuredExtract on the token level, so the
# defensive _strip_json_fence + Pydantic-retry loop becomes a fallback.
EXTRACT_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "structured_extract",
        "schema": StructuredExtract.model_json_schema(),
        "strict": True,
    },
}

# ── SIGINT handling ─────────────────────────────────────────────────────────

_shutdown_requested: bool = False
_last_sigint_time: float | None = None


def install_sigint_handler() -> None:
    """Install the two-stroke SIGINT handler.

    First Ctrl-C sets ``_shutdown_requested`` and the orchestrator exits
    after the current chat finishes. A second Ctrl-C within
    ``SIGINT_FORCE_KILL_WINDOW_SECONDS`` raises ``KeyboardInterrupt`` to
    force-kill (operator override for unresponsive LLM calls).

    # TODO: full SIGINT integration test — current test is flag-assertion
    # simplification, full signal-raising test can be added later.
    """

    def _handler(signum: int, frame: object) -> None:
        global _shutdown_requested, _last_sigint_time
        now = time.monotonic()
        if (
            _shutdown_requested
            and _last_sigint_time is not None
            and (now - _last_sigint_time) <= SIGINT_FORCE_KILL_WINDOW_SECONDS
        ):
            print(
                "\n[run] second SIGINT — force-killing now.",
                file=sys.stderr,
            )
            raise KeyboardInterrupt
        _shutdown_requested = True
        _last_sigint_time = now
        print(
            "\n[run] SIGINT received — finishing current chat then exiting. "
            f"Press Ctrl-C again within {int(SIGINT_FORCE_KILL_WINDOW_SECONDS)}s "
            "to force-kill.",
            file=sys.stderr,
        )

    signal.signal(signal.SIGINT, _handler)


def reset_shutdown_flag() -> None:
    """Tests use this to isolate the global state."""
    global _shutdown_requested, _last_sigint_time
    _shutdown_requested = False
    _last_sigint_time = None


def shutdown_requested() -> bool:
    return _shutdown_requested


# ── Prompt-variant selection ────────────────────────────────────────────────

PromptVariant = str  # Literal["example", "schema"] enforced by argparse choices


def _select_extract_prompt(variant: PromptVariant) -> str:
    if variant == "schema":
        return STRUCTURED_EXTRACT_PROMPT_WITH_SCHEMA
    if variant == "example":
        return STRUCTURED_EXTRACT_PROMPT
    raise ValueError(f"unknown prompt variant: {variant!r}")


# ── Chat selection ──────────────────────────────────────────────────────────


async def select_chat_ids(session: AsyncSession, args: argparse.Namespace) -> list[int]:
    """Resolve CLI selectors into a concrete list of chat ids."""
    if args.chat_id is not None:
        return [int(args.chat_id)]

    if args.chat_ids:
        return [int(x) for x in args.chat_ids.split(",") if x.strip()]

    stmt = select(CommunicationsTelegramChat.id)
    if args.review_status is not None:
        stmt = stmt.where(
            CommunicationsTelegramChat.review_status
            == TelegramChatReviewStatus(args.review_status)
        )
    if args.since is not None:
        cutoff = _parse_since(args.since)
        # Chats with at least one message after cutoff.
        sub = (
            select(CommunicationsTelegramMessage.chat_id)
            .where(CommunicationsTelegramMessage.sent_at >= cutoff)
            .distinct()
        )
        stmt = stmt.where(CommunicationsTelegramChat.id.in_(sub))
    stmt = stmt.order_by(CommunicationsTelegramChat.id)
    result = await session.execute(stmt)
    return [int(cid) for cid in result.scalars().all()]


def _parse_since(value: str) -> datetime:
    """Accept ``YYYY-MM-DD`` or relative ``Nd`` (days)."""
    v = value.strip()
    if v.endswith("d") and v[:-1].isdigit():
        days = int(v[:-1])
        return datetime.now(tz=UTC) - timedelta(days=days)
    return datetime.fromisoformat(v).replace(tzinfo=UTC)


async def filter_already_processed(
    session: AsyncSession, chat_ids: list[int], *, force: bool
) -> tuple[list[int], list[int]]:
    """Return ``(to_process, skipped)``.

    A chat is *skipped* when an ``analysis_chat_analysis`` row already
    exists for the current ``ANALYZER_VERSION``, unless ``force=True``.
    """
    if not chat_ids or force:
        return list(chat_ids), []
    stmt = select(AnalysisChatAnalysis.chat_id).where(
        AnalysisChatAnalysis.chat_id.in_(chat_ids),
        AnalysisChatAnalysis.analyzer_version == ANALYZER_VERSION,
    )
    result = await session.execute(stmt)
    done = {int(cid) for cid in result.scalars().all()}
    to_process = [cid for cid in chat_ids if cid not in done]
    skipped = [cid for cid in chat_ids if cid in done]
    return to_process, skipped


# ── --status mode ───────────────────────────────────────────────────────────


async def cmd_status(session: AsyncSession) -> None:
    stmt = select(AnalysisChatAnalysisState).order_by(
        AnalysisChatAnalysisState.chat_id
    )
    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    if not rows:
        print("[status] no in-flight or stale analysis state rows")
        return
    print(f"[status] {len(rows)} state row(s):")
    for row in rows:
        print(
            f"  chat_id={row.chat_id} stage={row.stage} "
            f"chunks={row.chunks_done}/{row.chunks_total} "
            f"updated_at={row.updated_at} "
            f"failure={row.failure_reason!r}"
        )


# ── Per-chat orchestration ──────────────────────────────────────────────────


CommitFn = Callable[[], Awaitable[None]]


async def _default_commit(session: AsyncSession) -> CommitFn:
    async def commit() -> None:
        await session.commit()

    return commit


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


async def _summarise_chunks(
    chunks: list[list[Any]],
    llm: LMStudioClient,
    session: AsyncSession,
    *,
    chat_id: int,
    commit_fn: CommitFn,
) -> list[str]:
    summaries: list[str] = []
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        if shutdown_requested():
            raise _Interrupted()
        prompt = render(
            CHUNK_SUMMARY_PROMPT,
            chunk_messages=format_messages_for_prompt(chunk),
        )
        summary = await llm.complete(prompt)
        summaries.append(summary)
        await update_chunk_progress(
            session,
            chat_id=chat_id,
            chunks_done=idx,
            chunks_total=total,
            partial_result={"summaries": summaries},
        )
        await commit_fn()
    return summaries


class _Interrupted(Exception):
    """Internal signal: SIGINT was observed mid-chat."""


async def _build_extract(
    narrative: str, llm: LMStudioClient, *, prompt_variant: PromptVariant
) -> StructuredExtract:
    """Call Qwen up to ``EXTRACT_RETRY_ATTEMPTS`` times, parse strict JSON."""
    template = _select_extract_prompt(prompt_variant)
    prompt = render(template, narrative=narrative)
    last_error: Exception | None = None
    for attempt in range(1, EXTRACT_RETRY_ATTEMPTS + 1):
        raw = await llm.complete(prompt, response_format=EXTRACT_RESPONSE_FORMAT)
        try:
            return StructuredExtract.model_validate_json(_strip_json_fence(raw))
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "extract validation failed (attempt %d/%d): %s",
                attempt,
                EXTRACT_RETRY_ATTEMPTS,
                exc,
            )
            last_error = exc
    raise LLMRequestError(
        f"structured extract validation failed after "
        f"{EXTRACT_RETRY_ATTEMPTS} attempts: {last_error!r}",
        last_exception=last_error,
    )


async def process_chat(
    session: AsyncSession,
    *,
    chat_id: int,
    llm_client: LMStudioClient,
    catalog: list[CatalogEntry],
    chunk_size: int,
    prompt_variant: PromptVariant,
    force: bool,
    commit_fn: CommitFn | None = None,
) -> str:
    """Run the full pipeline for one chat. Returns ``done``/``failed``/``interrupted``.

    ``commit_fn`` is injected so tests can pass ``session.flush`` while the
    fixture rolls back. In production the CLI passes a ``session.commit``
    closure so each checkpoint is durable.
    """
    if commit_fn is None:
        commit_fn = await _default_commit(session)

    try:
        await set_stage(session, chat_id=chat_id, stage="loading")
        await commit_fn()

        messages = await load_chat_messages(session, chat_id)
        if not messages:
            await mark_failed(
                session, chat_id=chat_id, failure_reason="empty_chat"
            )
            await commit_fn()
            return "failed"

        chunks = split_into_chunks(messages, chunk_size=chunk_size)
        await set_stage(session, chat_id=chat_id, stage="chunk_summaries")
        await commit_fn()

        summaries = await _summarise_chunks(
            chunks, llm_client, session,
            chat_id=chat_id, commit_fn=commit_fn,
        )

        if shutdown_requested():
            return "interrupted"

        await set_stage(session, chat_id=chat_id, stage="master_summary")
        await commit_fn()
        master_summary = await llm_client.complete(
            render(MASTER_SUMMARY_PROMPT, chunk_summaries="\n\n".join(summaries))
        )

        if shutdown_requested():
            return "interrupted"

        await set_stage(session, chat_id=chat_id, stage="narrative")
        await commit_fn()
        narrative = await llm_client.complete(
            render(NARRATIVE_PROMPT, chat_history=master_summary)
        )

        if shutdown_requested():
            return "interrupted"

        await set_stage(session, chat_id=chat_id, stage="structured_extract")
        await commit_fn()
        extract = await _build_extract(
            narrative, llm_client, prompt_variant=prompt_variant
        )

        await set_stage(session, chat_id=chat_id, stage="matching")
        await commit_fn()
        matched_extract = await match_extract(extract, catalog, llm_client)

        await set_stage(session, chat_id=chat_id, stage="recording")
        await commit_fn()
        analysis = await record_full_analysis(
            session,
            chat_id=chat_id,
            analyzer_version=ANALYZER_VERSION,
            messages_analyzed_up_to=messages[-1].telegram_message_id,
            narrative_markdown=narrative,
            matched_extract=matched_extract,
            chunks_count=len(chunks),
        )

        try:
            result = await apply_analysis_to_customer(
                session, analysis_id=analysis.id, force=force
            )
        except AnalysisAlreadyAppliedError as exc:
            await mark_failed(
                session, chat_id=chat_id, failure_reason=f"already_applied: {exc}"
            )
            await commit_fn()
            return "failed"
        except MultipleCustomersForChatError as exc:
            await mark_failed(
                session,
                chat_id=chat_id,
                failure_reason=f"multiple_customers: {exc.customer_ids!r}",
            )
            await commit_fn()
            return "failed"

        logger.info(
            "chat_id=%s applied: customer=%s orders=%d items=%d pending=%d "
            "preferences=%d incidents=%d",
            chat_id,
            result.customer_id,
            result.orders_created,
            result.order_items_created,
            result.pending_items_created,
            result.preferences_added,
            result.incidents_added,
        )
        await mark_done(session, chat_id=chat_id)
        await commit_fn()
        return "done"

    except _Interrupted:
        return "interrupted"
    except LLMRequestError as exc:
        await mark_failed(
            session, chat_id=chat_id, failure_reason=f"llm_error: {exc}"
        )
        await commit_fn()
        return "failed"


# ── argparse ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analysis.run",
        description=(
            "Batch-analyse Telegram chats with Qwen3-14B (LM Studio). "
            "ADR-011 Task 3."
        ),
    )

    sel = parser.add_mutually_exclusive_group()
    sel.add_argument("--chat-id", type=int, help="Single chat id to analyse.")
    sel.add_argument(
        "--chat-ids",
        type=str,
        help="Comma-separated chat ids, e.g. '1,2,3'.",
    )
    sel.add_argument("--all", action="store_true", help="All chats.")
    sel.add_argument(
        "--since",
        type=str,
        help="Only chats with messages newer than YYYY-MM-DD or 'Nd' (days).",
    )
    sel.add_argument(
        "--review-status",
        choices=[s.value for s in TelegramChatReviewStatus],
        help="Filter chats by communications_telegram_chat.review_status.",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Messages per chunk (default {DEFAULT_CHUNK_SIZE}).",
    )
    parser.add_argument(
        "--prompt-variant",
        choices=["example", "schema"],
        default="example",
        help=(
            "Variant of structured extract prompt: 'example' uses static "
            "example payload (default, recommended), 'schema' uses Pydantic "
            "JSON Schema. For A/B testing on first runs."
        ),
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default=None,
        help="LM Studio endpoint URL (default http://localhost:1234/v1).",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned chat list and exit.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print analysis_chat_analysis_state and exit.",
    )

    grp = parser.add_mutually_exclusive_group()
    grp.add_argument(
        "--resume",
        action="store_true",
        help="Resume from stale checkpoints without prompting.",
    )
    grp.add_argument(
        "--restart",
        action="store_true",
        help="Drop stale checkpoints and re-run from scratch.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Re-apply analyses that already produced rows. Confirmed and "
            "cancelled orders are preserved by the service layer; only "
            "analyzer-drafted rows are rolled back before re-creation."
        ),
    )

    return parser


# ── main ────────────────────────────────────────────────────────────────────


async def _handle_stale(
    session: AsyncSession, args: argparse.Namespace
) -> int:
    """Apply --resume / --restart / interactive prompt. Returns exit code (0 to continue)."""
    running = await state_check.check_running_process(session)
    if running:
        print(
            f"[run] another process appears active (chat_ids={running}); "
            "exiting.",
            file=sys.stderr,
        )
        return 1

    stale = await state_check.get_stale_states(session)
    if not stale:
        return 0

    if args.resume:
        print(f"[run] --resume: keeping {len(stale)} stale checkpoint(s).")
        return 0
    if args.restart:
        chat_ids = [s.chat_id for s in stale]
        n = await state_check.restart_stale(session, chat_ids)
        await session.commit()
        print(f"[run] --restart: dropped {n} stale checkpoint(s).")
        return 0

    choice = state_check.prompt_resume_or_restart(stale)
    if choice == "restart":
        chat_ids = [s.chat_id for s in stale]
        n = await state_check.restart_stale(session, chat_ids)
        await session.commit()
        print(f"[run] dropped {n} stale checkpoint(s).")
    return 0


async def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)

    async with async_session_factory() as session:
        if args.status:
            await cmd_status(session)
            return 0

        rc = await _handle_stale(session, args)
        if rc != 0:
            return rc

        chat_ids = await select_chat_ids(session, args)
        if not chat_ids:
            print("[run] no chats matched selectors; nothing to do.")
            return 0

        to_process, skipped = await filter_already_processed(
            session, chat_ids, force=args.force
        )
        if skipped:
            print(
                f"[run] skipping {len(skipped)} already-analysed chat(s) "
                f"(use --force to re-process): {skipped}"
            )

        if args.dry_run:
            print(f"[run] dry-run: would process {len(to_process)} chat(s):")
            for cid in to_process:
                print(f"  - chat_id={cid}")
            return 0

        if not to_process:
            print("[run] nothing to process.")
            return 0

        catalog = await load_catalog(session)
        install_sigint_handler()

        endpoint = args.endpoint or None
        client_kwargs: dict[str, Any] = {}
        if endpoint:
            client_kwargs["endpoint"] = endpoint

        async with LMStudioClient(**client_kwargs) as llm:
            done_count = 0
            failed_count = 0
            interrupted_count = 0
            for cid in to_process:
                if shutdown_requested():
                    print("[run] shutdown requested — stopping before next chat.")
                    break
                print(f"[run] processing chat_id={cid}")
                try:
                    status = await process_chat(
                        session,
                        chat_id=cid,
                        llm_client=llm,
                        catalog=catalog,
                        chunk_size=args.chunk_size,
                        prompt_variant=args.prompt_variant,
                        force=args.force,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("unexpected error processing chat_id=%s", cid)
                    failed_count += 1
                    continue
                if status == "done":
                    done_count += 1
                elif status == "interrupted":
                    interrupted_count += 1
                    break
                else:
                    failed_count += 1

        print(
            f"[run] done={done_count} failed={failed_count} "
            f"interrupted={interrupted_count}"
        )
        return 0


def cli_entry() -> None:
    sys.exit(asyncio.run(main()))


if __name__ == "__main__":
    cli_entry()


__all__ = [
    "ANALYZER_VERSION",
    "EXTRACT_RETRY_ATTEMPTS",
    "build_parser",
    "cmd_status",
    "filter_already_processed",
    "install_sigint_handler",
    "main",
    "process_chat",
    "reset_shutdown_flag",
    "select_chat_ids",
    "shutdown_requested",
]
