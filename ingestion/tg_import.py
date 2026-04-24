"""ingestion/tg_import.py — Historical import of Telegram Desktop JSON Export.

ADR-012: multi-account. Account is detected from the folder structure
(E.164 wrapper `+PHONE/`). Every chat is written with `owner_account_id`;
conflict resolution is on `(owner_account_id, telegram_chat_id)`.

CLI:
    python -m ingestion.tg_import [--input-dir PATH] [--dry-run] [--verbose]

One transaction per chat. A failed chat is rolled back and logged; remaining
chats continue. Idempotent: watermark + ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from collections.abc import Generator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.communications.models import (
    CommunicationsTelegramChat,
    CommunicationsTelegramMessage,
    TelegramChatReviewStatus,
)
from app.communications.service import (
    get_account_by_phone,
    update_account_timestamps,
)
from app.config import settings
from ingestion.parser import ParsedChat, parse_export

log = logging.getLogger(__name__)

MESSAGE_INSERT_BATCH_SIZE = 500
DEFAULT_EXPORTS_ROOT = Path("/Users/protey/pili-crm-data/tg-exports")
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


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


def _normalize_phone(raw: str) -> str:
    """Strip whitespace — Telegram export writes '+7 916 187 9839'."""
    return "".join(raw.split())


def detect_account_phone(input_dir: Path | None) -> tuple[str, Path]:
    """Resolve (E.164 phone, account directory) from CLI argument or default scan.

    Rules (ADR-012 §6):
      - input_dir is None        → scan DEFAULT_EXPORTS_ROOT for +E164 subdirs.
      - input_dir name is E.164  → account_dir = input_dir.
      - input_dir parent is E.164 (legacy DataExport_*) → account_dir = parent.
      - otherwise                → refuse with an instructive message.
    """
    if input_dir is None:
        if not DEFAULT_EXPORTS_ROOT.is_dir():
            raise FileNotFoundError(
                f"Default exports root {DEFAULT_EXPORTS_ROOT} does not exist. "
                f"Pass --input-dir explicitly."
            )
        accounts = sorted(
            p for p in DEFAULT_EXPORTS_ROOT.iterdir()
            if p.is_dir() and E164_RE.match(p.name)
        )
        if not accounts:
            raise FileNotFoundError(
                f"No E.164 account directories found under {DEFAULT_EXPORTS_ROOT}. "
                f"Expected a subfolder like '+77471057849/'. See ADR-012 §5."
            )
        if len(accounts) > 1:
            names = ", ".join(p.name for p in accounts)
            raise RuntimeError(
                f"Multiple accounts found ({names}). "
                f"Pass --input-dir to pick one."
            )
        account_dir = accounts[0]
        return account_dir.name, account_dir

    input_dir = input_dir.resolve()
    if E164_RE.match(input_dir.name):
        return input_dir.name, input_dir
    if E164_RE.match(input_dir.parent.name):
        return input_dir.parent.name, input_dir.parent
    raise RuntimeError(
        f"{input_dir} is not inside an E.164 account directory "
        f"(expected `.../+PHONE/` wrapper). See ADR-012 §5."
    )


def find_result_json(account_dir: Path) -> Path:
    """Locate result.json inside an account directory.

    Priority (ADR-012 §6):
      1. `{account_dir}/result.json` — flat layout (preferred).
      2. Most recent `{account_dir}/DataExport_*/result.json` — legacy fallback.
    """
    flat = account_dir / "result.json"
    if flat.exists():
        return flat
    legacy = sorted(account_dir.glob("DataExport_*/result.json"))
    if legacy:
        return legacy[-1]
    raise FileNotFoundError(
        f"No result.json found in {account_dir} "
        f"(neither flat nor DataExport_*/result.json)."
    )


def _read_personal_information(json_path: Path) -> dict[str, str]:
    """Read only the `personal_information` block from a Telegram export.

    Returns normalized dict with 'phone_number' (whitespace stripped) and
    'user_id' (as str) — both may be absent.
    """
    with json_path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    pi = data.get("personal_information", {}) or {}
    out: dict[str, str] = {}
    raw_phone = pi.get("phone_number")
    if isinstance(raw_phone, str) and raw_phone:
        out["phone_number"] = _normalize_phone(raw_phone)
    raw_uid = pi.get("user_id")
    if raw_uid is not None:
        out["user_id"] = str(raw_uid)
    return out


async def _import_one_chat(
    engine: AsyncEngine,
    chat: ParsedChat,
    owner_account_id: int,
    *,
    progress_prefix: str | None = None,
) -> tuple[str, int, int]:
    """Import one chat in a single transaction.

    Returns (status, inserted, skipped); status is 'new' or 'updated'.
    Raises on DB error — engine.begin() rolls back the transaction automatically.
    """
    async with engine.begin() as conn:
        sel = await conn.execute(
            select(
                CommunicationsTelegramChat.id,
                CommunicationsTelegramChat.last_imported_message_id,
            ).where(
                CommunicationsTelegramChat.owner_account_id == owner_account_id,
                CommunicationsTelegramChat.telegram_chat_id == chat.telegram_chat_id,
            )
        )
        existing = sel.fetchone()

        if existing is None:
            ins = await conn.execute(
                pg_insert(CommunicationsTelegramChat)
                .values(
                    owner_account_id=owner_account_id,
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
                    f"INSERT chat returned no row for "
                    f"owner={owner_account_id} telegram_chat_id={chat.telegram_chat_id}"
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

        new_msgs = [
            m
            for m in chat.messages
            if watermark is None or int(m.telegram_message_id) > watermark
        ]
        skipped = len(chat.messages) - len(new_msgs)

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

            max_id = str(max(int(m.telegram_message_id) for m in new_msgs))
            await conn.execute(
                update(CommunicationsTelegramChat)
                .where(
                    CommunicationsTelegramChat.id == chat_db_id,
                )
                .values(last_imported_message_id=max_id)
            )

    return status, inserted, skipped


async def run_import(
    json_path: Path,
    owner_account_id: int,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> ImportResult:
    """Parse json_path and import into the DB under the given account.

    Public entry point, used by CLI and tests. One transaction per chat.
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
                    engine,
                    chat,
                    owner_account_id,
                    progress_prefix=progress_prefix,
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


async def _run_cli(
    input_dir: Path | None, *, dry_run: bool, verbose: bool
) -> int:
    try:
        phone, account_dir = detect_account_phone(input_dir)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        json_path = find_result_json(account_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        personal_info = _read_personal_information(json_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: failed to read {json_path}: {exc}", file=sys.stderr)
        return 1

    json_phone = personal_info.get("phone_number")
    if json_phone and json_phone != phone:
        print(
            f"error: phone mismatch — folder says {phone}, "
            f"but result.json says {json_phone}. "
            f"Make sure the export was placed in the correct account folder.",
            file=sys.stderr,
        )
        return 1

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            account = await get_account_by_phone(session, phone)
        if account is None:
            print(
                f"error: account {phone} is not registered. "
                f"Run:\n"
                f"  python3 -m ingestion.register_account "
                f"--phone {phone} --display-name '<label>'",
                file=sys.stderr,
            )
            return 1

        print(
            f"Importing {json_path} → account id={account.id} "
            f"({account.display_name})"
        )
        result = await run_import(
            json_path,
            owner_account_id=account.id,
            dry_run=dry_run,
            verbose=verbose,
        )

        if not dry_run and result.chats_failed < result.chats_total:
            json_user_id = personal_info.get("user_id")
            async with factory() as session:
                await update_account_timestamps(
                    session,
                    account.id,
                    telegram_user_id=json_user_id,
                )
    finally:
        await engine.dispose()

    return 0


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description=(
            "Import Telegram Desktop JSON Export into PiliStrogai CRM "
            "(multi-account, ADR-012)."
        )
    )
    parser.add_argument(
        "--input-dir",
        metavar="PATH",
        help=(
            "Account directory (e.g. `.../+79161879839/`) or legacy "
            "`.../+PHONE/DataExport_*/`. Default: auto-detect under "
            "/Users/protey/pili-crm-data/tg-exports/."
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

    input_dir = Path(args.input_dir) if args.input_dir else None

    code = asyncio.run(
        _run_cli(input_dir, dry_run=args.dry_run, verbose=args.verbose)
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
