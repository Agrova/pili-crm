"""ADR-013 Task 3: preflight CLI orchestrator.

Run: ``python3 -m analysis.preflight --all``.

Selects chats without preflight verdict, builds preview samples
(title + metadata + first/last messages), calls Qwen3-14B via
``analysis.llm_client.LMStudioClient``, persists via
``app.analysis.service.record_skipped_analysis``. Empty chats are
short-circuited to ``classification='not_client'`` /
``skipped_reason='empty'`` without an LLM call.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from tqdm import tqdm

from analysis.llm_client import DEFAULT_ENDPOINT, LMStudioClient
from analysis.preflight import DEFAULT_MODEL, PREFLIGHT_VERSION
from analysis.preflight.service import (
    build_preview,
    classify_chat,
    is_empty_chat,
    render_prompt,
    select_pending_chats,
)
from app.analysis import repository as analysis_repo
from app.analysis.schemas import PreflightClassification, StructuredExtract
from app.analysis.service import record_skipped_analysis
from app.config import settings
from app.database import async_session_factory
from app.llm_studio_control import LMStudioTimeoutError, ensure_model_loaded

_EMPTY_EXTRACT = StructuredExtract.model_validate({"_v": 1}).model_dump(
    exclude_none=True, by_alias=True
)

logger = logging.getLogger("analysis.preflight.cli")

_EXIT_OK = 0
_EXIT_ERROR = 1
_EXIT_LM_STUDIO_TIMEOUT = 2


# ── SIGINT handling (one-stroke graceful, two-stroke force) ────────────────


_shutdown_requested = False
_last_sigint_time: float | None = None
_FORCE_KILL_WINDOW_SECONDS = 5.0


def _install_sigint_handler() -> None:
    def _handler(_signum: int, _frame: object) -> None:
        global _shutdown_requested, _last_sigint_time
        now = time.monotonic()
        if (
            _shutdown_requested
            and _last_sigint_time is not None
            and (now - _last_sigint_time) <= _FORCE_KILL_WINDOW_SECONDS
        ):
            print(
                "\n[preflight] second SIGINT — force-killing now.",
                file=sys.stderr,
            )
            raise KeyboardInterrupt
        _shutdown_requested = True
        _last_sigint_time = now
        print(
            "\n[preflight] SIGINT — finishing current chat, then exiting. "
            f"Press Ctrl-C again within {int(_FORCE_KILL_WINDOW_SECONDS)}s to "
            "force-kill.",
            file=sys.stderr,
        )

    signal.signal(signal.SIGINT, _handler)


def _reset_shutdown_flag() -> None:
    global _shutdown_requested, _last_sigint_time
    _shutdown_requested = False
    _last_sigint_time = None


# ── argparse ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analysis.preflight",
        description=(
            "Classify Telegram chats with Qwen3-14B as client / not_client / "
            "etc. (ADR-013 Task 3). Writes preflight-only rows to "
            "analysis_chat_analysis."
        ),
    )

    sel = parser.add_mutually_exclusive_group(required=True)
    sel.add_argument("--all", action="store_true", help="Process all pending chats.")
    sel.add_argument("--chat-id", type=int, help="Single chat id.")

    parser.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"LM Studio model id (default {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default=DEFAULT_ENDPOINT,
        help=f"LM Studio OpenAI endpoint (default {DEFAULT_ENDPOINT}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build preview and prompt but do not call LLM and do not write.",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Re-classify chats that already have a preflight verdict.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="DEBUG-level logging.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Selector batch size (default 50).",
    )
    return parser


# ── stats ──────────────────────────────────────────────────────────────────


@dataclass
class _Stats:
    total_pending: int = 0
    classified: int = 0
    by_classification: Counter[str] = field(default_factory=Counter)
    empty: int = 0
    errors: int = 0


def _describe_mode(args: argparse.Namespace) -> str:
    if args.all:
        return "--all"
    if args.chat_id is not None:
        return f"--chat-id {args.chat_id}"
    return "<unknown>"


def _print_summary(
    stats: _Stats,
    *,
    args: argparse.Namespace,
    model_id: str,
    elapsed_seconds: float,
) -> None:
    bc = stats.by_classification
    print()
    print("Preflight summary:")
    print(f"  Mode: {_describe_mode(args)}")
    print(f"  Preflight version: {PREFLIGHT_VERSION}")
    print(f"  Model: {model_id}")
    print(f"  Total pending:       {stats.total_pending}")
    print(f"  Classified:          {stats.classified}")
    print(f"    client:            {bc.get('client', 0)}")
    print(f"    possible_client:   {bc.get('possible_client', 0)}")
    print(f"    not_client:        {bc.get('not_client', 0)}")
    print(f"    family:            {bc.get('family', 0)}")
    print(f"    friend:            {bc.get('friend', 0)}")
    print(f"    service:           {bc.get('service', 0)}")
    print(f"  Empty (skipped LLM): {stats.empty}")
    print(f"  Errors (no save):    {stats.errors}")
    print(f"  Time elapsed:        {elapsed_seconds:.1f}s")


# ── per-chat processing ────────────────────────────────────────────────────


async def _process_chat(
    session: AsyncSession,
    chat_id: int,
    *,
    args: argparse.Namespace,
    llm: LMStudioClient | None,
    model_id: str,
    stats: _Stats,
) -> None:
    if await is_empty_chat(session, chat_id):
        stats.empty += 1
        if args.dry_run:
            return
        empty_verdict = PreflightClassification(
            classification="not_client",
            confidence="high",
            reason="empty chat",
        )
        await record_skipped_analysis(
            session,
            chat_id=chat_id,
            analyzer_version=PREFLIGHT_VERSION,
            messages_analyzed_up_to="",
            skipped_reason="empty",
            preflight=empty_verdict,
        )
        return

    preview = await build_preview(session, chat_id)
    prompt = render_prompt(preview)

    if args.dry_run:
        print(f"\n=== preflight dry-run chat_id={chat_id} ===")
        print(prompt)
        print("=== end ===\n")
        return

    assert llm is not None, "LLM client must be present for non-dry-run"
    verdict = await classify_chat(chat_id, preview, llm, model_id)
    if verdict is None:
        stats.errors += 1
        return

    skipped: str | None = None
    if verdict.classification == "not_client" and verdict.confidence == "high":
        skipped = "not_client"

    if skipped is not None:
        await record_skipped_analysis(
            session,
            chat_id=chat_id,
            analyzer_version=PREFLIGHT_VERSION,
            messages_analyzed_up_to=preview.last_message_date or "",
            skipped_reason=skipped,
            preflight=verdict,
        )
    else:
        # Preflight-only verdict (verdict was not "not_client/high"): archive
        # the row with skipped_reason=NULL so full analysis (different
        # analyzer_version) can later use it as preflight cache.
        await analysis_repo.upsert_analysis(
            session,
            chat_id=chat_id,
            analyzer_version=PREFLIGHT_VERSION,
            analyzed_at=datetime.now(tz=UTC),
            messages_analyzed_up_to=preview.last_message_date or "",
            narrative_markdown="",
            structured_extract=_EMPTY_EXTRACT,
            chunks_count=0,
            preflight_classification=verdict.classification,
            preflight_confidence=verdict.confidence,
            preflight_reason=verdict.reason,
            skipped_reason=None,
        )

    stats.classified += 1
    stats.by_classification[verdict.classification] += 1


# ── main ───────────────────────────────────────────────────────────────────


async def main(args: argparse.Namespace) -> int:
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    model_id = args.model
    skip_existing = not args.regenerate

    _reset_shutdown_flag()
    _install_sigint_handler()

    stats = _Stats()
    started = time.monotonic()

    # Ensure model loaded only when we'll actually call the LLM.
    if not args.dry_run:
        try:
            await ensure_model_loaded(model_id, settings.LM_STUDIO_API_BASE)
        except LMStudioTimeoutError:
            print(
                f"Qwen3-14B ({model_id}) is not loaded in LM Studio. "
                "Please load it manually via LM Studio UI, then re-run.",
                file=sys.stderr,
            )
            return _EXIT_LM_STUDIO_TIMEOUT

    llm: LMStudioClient | None = None
    try:
        if not args.dry_run:
            llm = LMStudioClient(endpoint=args.endpoint)

        async with async_session_factory() as session:
            try:
                pending = await select_pending_chats(
                    session,
                    chat_id=args.chat_id,
                    skip_existing=skip_existing,
                )
            except Exception:
                logger.exception("Failed to select pending chats")
                return _EXIT_ERROR

            stats.total_pending = len(pending)
            if not pending:
                logger.info("No pending chats — nothing to do.")
                _print_summary(
                    stats,
                    args=args,
                    model_id=model_id,
                    elapsed_seconds=time.monotonic() - started,
                )
                return _EXIT_OK

            with tqdm(total=len(pending), unit="chat", desc="preflight") as bar:
                for chat_id in pending:
                    if _shutdown_requested:
                        break
                    try:
                        await _process_chat(
                            session,
                            chat_id,
                            args=args,
                            llm=llm,
                            model_id=model_id,
                            stats=stats,
                        )
                    except Exception:
                        logger.exception(
                            "preflight: fatal error on chat_id=%s — aborting",
                            chat_id,
                        )
                        if not args.dry_run:
                            await session.rollback()
                        _print_summary(
                            stats,
                            args=args,
                            model_id=model_id,
                            elapsed_seconds=time.monotonic() - started,
                        )
                        return _EXIT_ERROR

                    if not args.dry_run:
                        await session.commit()
                    bar.update(1)

    finally:
        if llm is not None:
            await llm.aclose()

    _print_summary(
        stats,
        args=args,
        model_id=model_id,
        elapsed_seconds=time.monotonic() - started,
    )
    return _EXIT_OK


def cli_entry() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args)))


__all__ = ["build_parser", "cli_entry", "main"]
