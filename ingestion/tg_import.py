"""ingestion/tg_import.py — One-shot historical import of Telegram Desktop JSON Export.

CLI:
    python -m ingestion.tg_import [--input-dir PATH] [--dry-run] [--verbose]

One transaction per chat.  A failed chat is rolled back and logged; remaining
chats continue.  Idempotent: watermark + ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.communications.models import (
    CommunicationsTelegramChat,
    CommunicationsTelegramMessage,
    TelegramChatReviewStatus,
)
from app.config import settings
from ingestion.parser import ParsedChat, parse_export

log = logging.getLogger(__name__)

MESSAGE_INSERT_BATCH_SIZE = 500


def _iter_chunks[T](seq: Sequence[T], size: int) -> Generator[Sequence[T], None, None]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


@dataclass
class ImportResult:
    chats_total: int = 0
    chats_new: int = 0
    chats_updated: int = 0
    chats_failed: int = 0
    msgs_inserted: int = 0
    msgs_skipped: int = 0
    elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)


async def _import_one_chat(
    engine: AsyncEngine,
    chat: ParsedChat,
    *,
    progress_prefix: str | None = None,
) -> tuple[str, int, int]:
    """Import one chat in a single transaction.

    Returns (status, inserted, skipped); status is 'new' or 'updated'.
    Raises on DB error — engine.begin() rolls back the transaction automatically.

    When progress_prefix is given and the chat requires multiple INSERT batches,
    prints one line per batch: "{progress_prefix} (batch N/M)".
    """
    async with engine.begin() as conn:
        # 1. Find existing chat row
        sel = await conn.execute(
            select(
                CommunicationsTelegramChat.id,
                CommunicationsTelegramChat.last_imported_message_id,
            ).where(
                CommunicationsTelegramChat.telegram_chat_id == chat.telegram_chat_id
            )
        )
        existing = sel.fetchone()

        if existing is None:
            ins = await conn.execute(
                pg_insert(CommunicationsTelegramChat)
                .values(
                    telegram_chat_id=chat.telegram_chat_id,
                    chat_type=chat.chat_type,
                    title=chat.title,
                    review_status=TelegramChatReviewStatus.unreviewed,
                )
                .returning(
                    CommunicationsTelegramChat.id,
                    CommunicationsTelegramChat.last_imported_message_id,
                )
            )
            new_row = ins.fetchone()
            if new_row is None:
                raise RuntimeError(
                    f"INSERT chat returned no row for telegram_chat_id={chat.telegram_chat_id}"
                )
            chat_db_id: int = new_row[0]
            watermark_str: str | None = new_row[1]
            status = "new"
        else:
            chat_db_id = existing[0]
            watermark_str = existing[1]
            status = "updated"

        watermark: int | None = (
            int(watermark_str) if watermark_str is not None else None
        )

        # 2. Filter messages newer than the watermark
        new_msgs = [
            m
            for m in chat.messages
            if watermark is None or int(m.telegram_message_id) > watermark
        ]
        skipped = len(chat.messages) - len(new_msgs)

        # 3. Batch-insert messages — chunked to stay under asyncpg's 32767-param limit
        inserted = 0
        if new_msgs:
            chunks = list(_iter_chunks(new_msgs, MESSAGE_INSERT_BATCH_SIZE))
            total_batches = len(chunks)
            for batch_num, chunk in enumerate(chunks, 1):
                if progress_prefix is not None and total_batches > 1:
                    print(
                        f"{progress_prefix} (batch {batch_num}/{total_batches})",
                        flush=True,
                    )
                values = [
                    {
                        "chat_id": chat_db_id,
                        "telegram_message_id": msg.telegram_message_id,
                        "from_user_id": msg.from_user_id,
                        "sent_at": msg.sent_at,
                        "text": msg.text,
                        "raw_payload": msg.raw_payload,
                        "reply_to_telegram_message_id": msg.reply_to_telegram_message_id,
                    }
                    for msg in chunk
                ]
                batch_result = await conn.execute(
                    pg_insert(CommunicationsTelegramMessage)
                    .values(values)
                    .on_conflict_do_nothing(
                        constraint="uq_communications_telegram_message_chat_msg"
                    )
                )
                inserted += (
                    batch_result.rowcount
                    if batch_result.rowcount >= 0
                    else len(chunk)
                )

            # 4. Advance watermark to max imported message_id
            max_id = str(max(int(m.telegram_message_id) for m in new_msgs))
            await conn.execute(
                update(CommunicationsTelegramChat)
                .where(
                    CommunicationsTelegramChat.telegram_chat_id
                    == chat.telegram_chat_id
                )
                .values(last_imported_message_id=max_id)
            )

    return status, inserted, skipped


async def run_import(
    json_path: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> ImportResult:
    """Parse json_path and import into the DB.  Public entry point, used by CLI and tests.

    One transaction per chat.  Errors in one chat do not stop remaining chats.
    """
    result = ImportResult()
    t0 = time.monotonic()

    chats = parse_export(json_path)
    result.chats_total = len(chats)
    total_parsed = sum(len(c.messages) for c in chats)
    log.info("Parsed %d chats, %d messages total", len(chats), total_parsed)

    if dry_run:
        for i, chat in enumerate(chats, 1):
            label = chat.title or chat.telegram_chat_id
            print(
                f"[dry-run {i}/{len(chats)}] {label!r}: {len(chat.messages)} messages"
            )
        result.elapsed_seconds = time.monotonic() - t0
        return result

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        for i, chat in enumerate(chats, 1):
            label = chat.title or chat.telegram_chat_id
            progress_prefix = f"[{i}/{len(chats)}] {label}: {len(chat.messages)} messages"
            if len(chat.messages) <= MESSAGE_INSERT_BATCH_SIZE:
                print(progress_prefix, flush=True)
            try:
                status, inserted, skipped = await _import_one_chat(
                    engine, chat, progress_prefix=progress_prefix
                )
                if status == "new":
                    result.chats_new += 1
                else:
                    result.chats_updated += 1
                result.msgs_inserted += inserted
                result.msgs_skipped += skipped
                if verbose:
                    log.debug(
                        "  %s: status=%s inserted=%d skipped=%d",
                        label,
                        status,
                        inserted,
                        skipped,
                    )
            except Exception as exc:
                log.error("FAILED %r: %s", label, exc)
                result.chats_failed += 1
                result.errors.append(f"{label}: {exc}")
    finally:
        await engine.dispose()

    result.elapsed_seconds = time.monotonic() - t0
    _print_report(result)
    return result


def _print_report(r: ImportResult) -> None:
    print(
        f"\n=== Import complete ({r.elapsed_seconds:.1f}s) ===\n"
        f"Chats   : total={r.chats_total}  new={r.chats_new}"
        f"  updated={r.chats_updated}  failed={r.chats_failed}\n"
        f"Messages: inserted={r.msgs_inserted}  skipped={r.msgs_skipped}"
    )
    for err in r.errors:
        print(f"  ERROR: {err}", file=sys.stderr)


def _find_latest_export(base: Path) -> Path:
    """Return the most recently created DataExport_* directory under base."""
    candidates = sorted(base.glob("DataExport_*"))
    if not candidates:
        raise FileNotFoundError(f"No DataExport_* directory found in {base}")
    return candidates[-1]


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Import Telegram Desktop JSON Export into PiliStrogai CRM."
    )
    parser.add_argument(
        "--input-dir",
        metavar="PATH",
        help=(
            "Path to the export folder containing result.json. "
            "Default: latest DataExport_* in ~/pili-crm-data/tg-exports/"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and print stats without writing to the DB.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.input_dir:
        input_dir = Path(args.input_dir)
    else:
        default_base = Path.home() / "pili-crm-data" / "tg-exports"
        try:
            input_dir = _find_latest_export(default_base)
        except FileNotFoundError as exc:
            print(
                f"error: {exc}\nUse --input-dir to specify the export folder.",
                file=sys.stderr,
            )
            sys.exit(1)

    json_path = input_dir / "result.json"
    if not json_path.exists():
        print(f"error: result.json not found in {input_dir}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run_import(json_path, dry_run=args.dry_run, verbose=args.verbose))


if __name__ == "__main__":
    main()
