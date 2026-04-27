"""ADR-014 Task 5: CLI orchestrator for media_extract.

Run: ``python3 -m analysis.media_extract --all``.

The driver streams batches from ``select_pending_messages`` and dispatches
each row through ``decide_extractor``. Office and placeholder rows are
processed inline (cheap); image rows go through ``extract_image_or_fail``
which talks to LM Studio. ``ensure_model_loaded`` is invoked once, at
startup, only if the planned batch contains at least one VISION row.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from tqdm import tqdm

from analysis.media_extract import (
    MEDIA_EXTRACTOR_VERSION,
    TELEGRAM_EXPORTS_ROOT,
)
from analysis.media_extract.service import (
    ExtractorKind,
    PendingMediaMessage,
    decide_extractor,
    derive_extraction_method_from_model,
    extract_image_or_fail,
    extract_office_or_placeholder,
    save_extraction,
    select_pending_messages,
)
from app.config import settings
from app.database import async_session_factory
from app.llm_studio_control import (
    LMStudioTimeoutError,
    ensure_model_loaded,
    unload_all,
)

logger = logging.getLogger("analysis.media_extract.cli")


_EXIT_OK = 0
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
                "\n[media_extract] second SIGINT — force-killing now.",
                file=sys.stderr,
            )
            raise KeyboardInterrupt
        _shutdown_requested = True
        _last_sigint_time = now
        print(
            "\n[media_extract] SIGINT — finishing current message, then exiting. "
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
        prog="analysis.media_extract",
        description=(
            "Extract text from Telegram media (photos, xlsx, docx) and write "
            "into communications_telegram_message_media_extraction. ADR-014 "
            "Task 5."
        ),
    )

    sel = parser.add_mutually_exclusive_group(required=True)
    sel.add_argument("--all", action="store_true", help="Process all pending media.")
    sel.add_argument("--chat-id", type=int, help="Single chat id.")
    sel.add_argument("--message-id", type=int, help="Single message id.")

    model = parser.add_mutually_exclusive_group()
    model.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override vision model HuggingFace id (mutually exclusive with --use-fallback-model).",
    )
    model.add_argument(
        "--use-fallback-model",
        action="store_true",
        help="Use MEDIA_EXTRACT_MODEL_FALLBACK (8B) instead of the primary 30B model.",
    )

    parser.add_argument(
        "--endpoint",
        type=str,
        default=settings.MEDIA_EXTRACT_DEFAULT_ENDPOINT,
        help=f"LM Studio endpoint (default {settings.MEDIA_EXTRACT_DEFAULT_ENDPOINT}).",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Overwrite existing extraction rows (DELETE + INSERT).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan and route messages but do not call vision API or write to DB.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="DEBUG-level logging.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Selector batch size (default 100).",
    )
    parser.add_argument(
        "--unload-after",
        action="store_true",
        help=(
            "Call unload_all on the LM Studio endpoint at the end of the run. "
            "No-op on the current LM Studio version (logged via warning)."
        ),
    )

    return parser


# ── helpers ────────────────────────────────────────────────────────────────


@dataclass
class _Stats:
    processed: int = 0
    saved: int = 0
    skipped_existing: int = 0
    errors: int = 0
    by_kind: Counter[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.by_kind = Counter()


def _resolve_model_id(args: argparse.Namespace) -> str:
    if args.model:
        return args.model
    if args.use_fallback_model:
        return settings.MEDIA_EXTRACT_MODEL_FALLBACK
    return settings.MEDIA_EXTRACT_MODEL_PRIMARY


def _describe_mode(args: argparse.Namespace) -> str:
    if args.all:
        return "--all"
    if args.chat_id is not None:
        return f"--chat-id {args.chat_id}"
    if args.message_id is not None:
        return f"--message-id {args.message_id}"
    return "<unknown>"


async def _iter_batches(
    session: AsyncSession,
    *,
    chat_id: int | None,
    message_id: int | None,
    extractor_version: str,
    batch_size: int,
    skip_existing: bool,
) -> AsyncIterator[list[PendingMediaMessage]]:
    """Yield successive batches.

    With ``skip_existing=True`` (the default), each batch returns rows that
    have *no* extraction yet — once a batch finishes, the next call surfaces
    the next set, so we don't need cursor-style offsets. With
    ``skip_existing=False`` (``--regenerate``) we'd loop forever the same way,
    so we track ``last_seen_id`` and require the next batch to advance past it.
    """
    after = 0
    while True:
        batch = await select_pending_messages(
            session,
            chat_id=chat_id,
            message_id=message_id,
            extractor_version=extractor_version,
            batch_size=batch_size,
            skip_existing=skip_existing,
            after_message_id=after,
        )
        if not batch:
            return
        after = batch[-1].message_id
        yield batch


async def _process_message(
    session: AsyncSession,
    msg: PendingMediaMessage,
    *,
    args: argparse.Namespace,
    model_id: str,
    extractor_version: str,
    stats: _Stats,
) -> None:
    kind = decide_extractor(msg)

    if kind is ExtractorKind.VISION:
        if args.dry_run:
            stats.processed += 1
            stats.by_kind[ExtractorKind.VISION.value] += 1
            return
        result = await extract_image_or_fail(
            msg, TELEGRAM_EXPORTS_ROOT, model_id, args.endpoint
        )
    else:
        result = await extract_office_or_placeholder(
            msg, kind, TELEGRAM_EXPORTS_ROOT
        )

    stats.processed += 1
    stats.by_kind[kind.value] += 1
    if result.extraction_method == "placeholder" and kind is not ExtractorKind.PLACEHOLDER:
        # Office/vision path that degraded into a placeholder due to a parse
        # error or a missing file. Counts as a recoverable error in the
        # final summary.
        stats.errors += 1

    if args.dry_run:
        return

    written = await save_extraction(
        session,
        result,
        extractor_version,
        regenerate=args.regenerate,
    )
    if written:
        stats.saved += 1
    else:
        stats.skipped_existing += 1


def _print_summary(
    stats: _Stats,
    *,
    args: argparse.Namespace,
    model_id: str,
    extractor_version: str,
    elapsed_seconds: float,
) -> None:
    saved = 0 if args.dry_run else stats.saved
    print()
    print("Media extraction summary:")
    print(f"  Mode:                 {_describe_mode(args)}")
    print(f"  Extractor version:    {extractor_version}")
    print(f"  Vision model:         {model_id}")
    print(f"  Processed:            {stats.processed}")
    print(f"    vision:             {stats.by_kind.get('vision', 0)}")
    print(f"    xlsx:               {stats.by_kind.get('xlsx', 0)}")
    print(f"    docx:               {stats.by_kind.get('docx', 0)}")
    print(f"    placeholder:        {stats.by_kind.get('placeholder', 0)}")
    print(f"  Skipped (existing):   {stats.skipped_existing}")
    print(f"  Errors (placeholder): {stats.errors}")
    print(f"  Time elapsed:         {elapsed_seconds:.1f}s")
    print(f"  Saved to DB:          {saved}{' (dry-run)' if args.dry_run else ''}")


# ── main ───────────────────────────────────────────────────────────────────


async def _has_vision_pending(
    session: AsyncSession,
    *,
    chat_id: int | None,
    message_id: int | None,
    extractor_version: str,
    skip_existing: bool,
) -> bool:
    """Cheap probe: peek the first batch and check if any row needs vision."""
    peek = await select_pending_messages(
        session,
        chat_id=chat_id,
        message_id=message_id,
        extractor_version=extractor_version,
        batch_size=200,
        skip_existing=skip_existing,
    )
    return any(decide_extractor(m) is ExtractorKind.VISION for m in peek)


async def main(args: argparse.Namespace) -> int:
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    extractor_version = MEDIA_EXTRACTOR_VERSION
    model_id = _resolve_model_id(args)
    skip_existing = not args.regenerate

    chat_id: int | None = args.chat_id
    message_id: int | None = args.message_id

    _reset_shutdown_flag()
    _install_sigint_handler()

    stats = _Stats()
    started = time.monotonic()

    async with async_session_factory() as session:
        try:
            need_vision = await _has_vision_pending(
                session,
                chat_id=chat_id,
                message_id=message_id,
                extractor_version=extractor_version,
                skip_existing=skip_existing,
            )
        except Exception:
            logger.exception("Failed to probe for pending vision messages")
            return 1

        if need_vision and not args.dry_run:
            try:
                await ensure_model_loaded(model_id, args.endpoint)
            except LMStudioTimeoutError:
                print(
                    f"Vision model {model_id} is not loaded in LM Studio. "
                    "Please open LM Studio and load it manually, then re-run.",
                    file=sys.stderr,
                )
                return _EXIT_LM_STUDIO_TIMEOUT
            logger.info("Vision model %s ready (method tag: %s)",
                        model_id, derive_extraction_method_from_model(model_id))
        elif need_vision and args.dry_run:
            logger.info(
                "Vision messages present but --dry-run: skipping ensure_model_loaded for %s",
                model_id,
            )

        # Total is unknown — tqdm runs as an open-ended counter.
        with tqdm(unit="msg", desc="media_extract") as bar:
            async for batch in _iter_batches(
                session,
                chat_id=chat_id,
                message_id=message_id,
                extractor_version=extractor_version,
                batch_size=args.batch_size,
                skip_existing=skip_existing,
            ):
                for msg in batch:
                    if _shutdown_requested:
                        break
                    try:
                        await _process_message(
                            session,
                            msg,
                            args=args,
                            model_id=model_id,
                            extractor_version=extractor_version,
                            stats=stats,
                        )
                    except Exception:
                        logger.exception(
                            "Aborting batch — fatal error processing message_id=%s",
                            msg.message_id,
                        )
                        if not args.dry_run:
                            await session.rollback()
                        _print_summary(
                            stats,
                            args=args,
                            model_id=model_id,
                            extractor_version=extractor_version,
                            elapsed_seconds=time.monotonic() - started,
                        )
                        return 1
                    bar.update(1)

                if not args.dry_run:
                    await session.commit()

                if _shutdown_requested:
                    break

        if args.unload_after:
            try:
                await unload_all(args.endpoint)
            except Exception:
                logger.exception("unload_all failed (non-fatal)")

    _print_summary(
        stats,
        args=args,
        model_id=model_id,
        extractor_version=extractor_version,
        elapsed_seconds=time.monotonic() - started,
    )
    return _EXIT_OK


def cli_entry() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args)))


__all__ = ["build_parser", "cli_entry", "main"]
