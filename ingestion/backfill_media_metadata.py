"""ingestion/backfill_media_metadata.py — One-time backfill of media metadata.

ADR-015 Task 2 Phase B. Re-parses each account's `result.json` and writes
`ParsedMediaMetadata` into `communications_telegram_message_media` for every
existing message that has media but no row in the new table yet.

CLI:
    python3 -m ingestion.backfill_media_metadata
    python3 -m ingestion.backfill_media_metadata --account +77471057849
    python3 -m ingestion.backfill_media_metadata --dry-run
    python3 -m ingestion.backfill_media_metadata --verbose
    python3 -m ingestion.backfill_media_metadata --verify

Idempotent — ON CONFLICT DO NOTHING on `uq_communications_telegram_message_media_message_id`.
Graceful SIGINT — finishes the current chat batch, then exits with a partial
report and exit code 130.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.communications.models import (
    CommunicationsTelegramAccount,
    CommunicationsTelegramChat,
    CommunicationsTelegramMessage,
    CommunicationsTelegramMessageMedia,
)
from app.config import settings
from ingestion.parser import ParsedChat, parse_export
from ingestion.tg_import import (
    DEFAULT_EXPORTS_ROOT,
    find_result_json,
)

log = logging.getLogger(__name__)

MESSAGE_LOOKUP_BATCH_SIZE = 500
MEDIA_INSERT_BATCH_SIZE = 500


@dataclass
class AccountStats:
    phone: str
    chats_parsed: int = 0
    chats_found_in_db: int = 0
    chats_missing_in_db: int = 0
    media_in_json: int = 0
    media_found_in_db: int = 0
    records_inserted: int = 0
    warnings_no_message: int = 0


@dataclass
class BackfillReport:
    accounts: list[AccountStats] = field(default_factory=list)
    interrupted: bool = False


# ─── SIGINT handling ──────────────────────────────────────────────────────────


_INTERRUPT = asyncio.Event()


def _install_sigint_handler() -> None:
    """Set a flag on first Ctrl+C; second one goes through default handler."""

    def _handler(signum: int, frame: object) -> None:  # noqa: ARG001
        if _INTERRUPT.is_set():
            # Second Ctrl+C — restore default and re-raise immediately.
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            raise KeyboardInterrupt
        log.warning(
            "SIGINT received — finishing current chat batch, then exiting. "
            "Press Ctrl+C again to abort immediately."
        )
        _INTERRUPT.set()

    signal.signal(signal.SIGINT, _handler)


# ─── DB helpers ───────────────────────────────────────────────────────────────


async def _list_accounts(
    engine: AsyncEngine, phone_filter: str | None
) -> list[tuple[int, str]]:
    """Return [(account_id, phone_number), ...] in stable order."""
    stmt = select(
        CommunicationsTelegramAccount.id,
        CommunicationsTelegramAccount.phone_number,
    ).order_by(CommunicationsTelegramAccount.id)
    if phone_filter is not None:
        stmt = stmt.where(
            CommunicationsTelegramAccount.phone_number == phone_filter
        )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [(int(r[0]), str(r[1])) for r in rows]


async def _chat_id_map(
    engine: AsyncEngine, owner_account_id: int
) -> dict[str, int]:
    """telegram_chat_id (str) → chat.id for one account."""
    stmt = select(
        CommunicationsTelegramChat.id,
        CommunicationsTelegramChat.telegram_chat_id,
    ).where(CommunicationsTelegramChat.owner_account_id == owner_account_id)
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return {str(r[1]): int(r[0]) for r in rows}


async def _message_id_map_for_chat(
    engine: AsyncEngine,
    chat_db_id: int,
    telegram_message_ids: list[str],
) -> dict[str, int]:
    """For one chat, return telegram_message_id → message.id for given ids.

    Batched at MESSAGE_LOOKUP_BATCH_SIZE to avoid huge IN-lists.
    """
    out: dict[str, int] = {}
    for i in range(0, len(telegram_message_ids), MESSAGE_LOOKUP_BATCH_SIZE):
        chunk = telegram_message_ids[i : i + MESSAGE_LOOKUP_BATCH_SIZE]
        stmt = select(
            CommunicationsTelegramMessage.id,
            CommunicationsTelegramMessage.telegram_message_id,
        ).where(
            CommunicationsTelegramMessage.chat_id == chat_db_id,
            CommunicationsTelegramMessage.telegram_message_id.in_(chunk),
        )
        async with engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        for row in rows:
            out[str(row[1])] = int(row[0])
    return out


async def _insert_media_batch(
    engine: AsyncEngine, values: list[dict[str, object]]
) -> int:
    """Insert media records in batches; returns total rows actually inserted."""
    if not values:
        return 0
    inserted_total = 0
    async with engine.begin() as conn:
        for i in range(0, len(values), MEDIA_INSERT_BATCH_SIZE):
            chunk = values[i : i + MEDIA_INSERT_BATCH_SIZE]
            result = await conn.execute(
                pg_insert(CommunicationsTelegramMessageMedia)
                .values(chunk)
                .on_conflict_do_nothing(
                    constraint="uq_communications_telegram_message_media_message_id"
                )
                .returning(CommunicationsTelegramMessageMedia.id)
            )
            inserted_total += len(result.fetchall())
    return inserted_total


# ─── Per-account driver ───────────────────────────────────────────────────────


async def _process_account(
    engine: AsyncEngine,
    account_id: int,
    phone: str,
    *,
    dry_run: bool,
    verbose: bool,
) -> AccountStats:
    stats = AccountStats(phone=phone)

    account_dir = DEFAULT_EXPORTS_ROOT / phone
    if not account_dir.is_dir():
        log.warning("Account %s: directory %s does not exist — skipping",
                    phone, account_dir)
        return stats

    try:
        json_path = find_result_json(account_dir)
    except FileNotFoundError as exc:
        log.warning("Account %s: %s", phone, exc)
        return stats

    log.info("Account %s: parsing %s", phone, json_path)
    parsed_chats: list[ParsedChat] = parse_export(json_path)
    stats.chats_parsed = len(parsed_chats)

    chat_map = await _chat_id_map(engine, account_id)
    if verbose:
        log.debug("Account %s: %d chats in DB, %d chats in JSON",
                  phone, len(chat_map), len(parsed_chats))

    for chat in parsed_chats:
        if _INTERRUPT.is_set():
            log.warning("Interrupted before chat %r", chat.title or chat.telegram_chat_id)
            break

        chat_db_id = chat_map.get(chat.telegram_chat_id)
        if chat_db_id is None:
            stats.chats_missing_in_db += 1
            log.warning(
                "Account %s: chat %r (telegram_chat_id=%s) not in DB — skipping",
                phone, chat.title, chat.telegram_chat_id,
            )
            continue
        stats.chats_found_in_db += 1

        media_msgs = [m for m in chat.messages if m.media is not None]
        stats.media_in_json += len(media_msgs)
        if not media_msgs:
            continue

        tg_ids = [m.telegram_message_id for m in media_msgs]
        msg_id_map = await _message_id_map_for_chat(engine, chat_db_id, tg_ids)

        values: list[dict[str, object]] = []
        for msg in media_msgs:
            db_id = msg_id_map.get(msg.telegram_message_id)
            if db_id is None:
                stats.warnings_no_message += 1
                if verbose:
                    log.debug(
                        "Account %s chat %s: telegram_message_id=%s "
                        "has media in JSON but no row in DB",
                        phone, chat.telegram_chat_id, msg.telegram_message_id,
                    )
                continue
            assert msg.media is not None  # guaranteed by media_msgs filter
            values.append({
                "message_id": db_id,
                "media_type": msg.media.media_type,
                "file_name": msg.media.file_name,
                "relative_path": msg.media.relative_path,
                "file_size_bytes": msg.media.file_size_bytes,
                "mime_type": msg.media.mime_type,
            })
        stats.media_found_in_db += len(values)

        if not dry_run and values:
            inserted = await _insert_media_batch(engine, values)
            stats.records_inserted += inserted
            if verbose:
                log.debug(
                    "Account %s chat %r: inserted %d / candidate %d",
                    phone, chat.title, inserted, len(values),
                )

    return stats


# ─── Verify mode ──────────────────────────────────────────────────────────────


async def _verify(engine: AsyncEngine, phone_filter: str | None) -> int:
    accounts = await _list_accounts(engine, phone_filter)
    if not accounts:
        print("No matching accounts in DB.", file=sys.stderr)
        return 1

    print("Verify summary:")
    grand_json = 0
    grand_db_account = 0
    for account_id, phone in accounts:
        account_dir = DEFAULT_EXPORTS_ROOT / phone
        try:
            json_path = find_result_json(account_dir)
            parsed_chats = parse_export(json_path)
            json_media_count = sum(
                1 for c in parsed_chats for m in c.messages if m.media is not None
            )
        except FileNotFoundError as exc:
            print(f"  Account {phone}: result.json not found ({exc})")
            json_media_count = 0

        # Count media records belonging to this account's chats.
        async with engine.connect() as conn:
            db_count = (await conn.execute(text(
                "SELECT COUNT(*) "
                "FROM communications_telegram_message_media mm "
                "JOIN communications_telegram_message m ON m.id = mm.message_id "
                "JOIN communications_telegram_chat c ON c.id = m.chat_id "
                "WHERE c.owner_account_id = :aid"
            ), {"aid": account_id})).scalar_one()

        print(f"  Account {phone}:")
        print(f"    media messages in JSON:      {json_media_count}")
        print(f"    media metadata records (DB): {db_count}")
        grand_json += json_media_count
        grand_db_account += int(db_count)

    async with engine.connect() as conn:
        total_db = (await conn.execute(text(
            "SELECT COUNT(*) FROM communications_telegram_message_media"
        ))).scalar_one()

    print("  Total:")
    print(f"    media messages in JSON (sum):    {grand_json}")
    print(f"    media metadata records (sum):    {grand_db_account}")
    print(f"    media metadata records (table):  {total_db}")
    return 0


# ─── Reporting ────────────────────────────────────────────────────────────────


def _print_report(report: BackfillReport, *, dry_run: bool) -> None:
    header = "Backfill summary (DRY-RUN — no writes):" if dry_run else "Backfill summary:"
    print(header)

    totals = AccountStats(phone="TOTAL")
    for s in report.accounts:
        print(f"  Account {s.phone}:")
        print(f"    chats parsed:                    {s.chats_parsed}")
        print(f"    chats found in DB:               {s.chats_found_in_db}")
        print(f"    chats missing in DB:             {s.chats_missing_in_db}")
        print(f"    media messages in JSON:          {s.media_in_json}")
        print(f"    media messages found in DB:      {s.media_found_in_db}")
        print(f"    media metadata records inserted: {s.records_inserted}")
        print(f"    warnings (media in JSON, no message in DB): {s.warnings_no_message}")
        totals.chats_parsed += s.chats_parsed
        totals.chats_found_in_db += s.chats_found_in_db
        totals.chats_missing_in_db += s.chats_missing_in_db
        totals.media_in_json += s.media_in_json
        totals.media_found_in_db += s.media_found_in_db
        totals.records_inserted += s.records_inserted
        totals.warnings_no_message += s.warnings_no_message

    print("  Total:")
    print(f"    chats parsed:                    {totals.chats_parsed}")
    print(f"    chats found in DB:               {totals.chats_found_in_db}")
    print(f"    chats missing in DB:             {totals.chats_missing_in_db}")
    print(f"    media messages in JSON:          {totals.media_in_json}")
    print(f"    media messages found in DB:      {totals.media_found_in_db}")
    print(f"    media metadata records inserted: {totals.records_inserted}")
    print(f"    warnings (media in JSON, no message in DB): {totals.warnings_no_message}")

    if report.interrupted:
        print("\n[interrupted by SIGINT — partial results above]")


# ─── Public entry point ───────────────────────────────────────────────────────


async def run_backfill(
    *,
    phone_filter: str | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    database_url: str | None = None,
) -> BackfillReport:
    """Public entry point — used by CLI and tests."""
    url = database_url or settings.database_url
    engine = create_async_engine(url, poolclass=NullPool)
    report = BackfillReport()
    try:
        accounts = await _list_accounts(engine, phone_filter)
        if not accounts:
            log.warning(
                "No accounts in DB%s",
                f" matching {phone_filter}" if phone_filter else "",
            )
            return report
        for account_id, phone in accounts:
            if _INTERRUPT.is_set():
                report.interrupted = True
                break
            stats = await _process_account(
                engine,
                account_id,
                phone,
                dry_run=dry_run,
                verbose=verbose,
            )
            report.accounts.append(stats)
        if _INTERRUPT.is_set():
            report.interrupted = True
    finally:
        await engine.dispose()
    return report


# ─── CLI ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill normalized media metadata into "
            "communications_telegram_message_media (ADR-015 Task 2)."
        ),
    )
    parser.add_argument(
        "--account",
        metavar="+PHONE",
        help="Process only the given account (E.164, e.g. +77471057849).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report without writing to the DB.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logging (per-chat detail).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Compare media counts in JSON vs DB; no writes.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    _install_sigint_handler()

    if args.verify:
        engine = create_async_engine(settings.database_url, poolclass=NullPool)

        async def _go() -> int:
            try:
                return await _verify(engine, args.account)
            finally:
                await engine.dispose()

        sys.exit(asyncio.run(_go()))

    report = asyncio.run(
        run_backfill(
            phone_filter=args.account,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    )
    _print_report(report, dry_run=args.dry_run)
    sys.exit(130 if report.interrupted else 0)


if __name__ == "__main__":
    main()
