"""analysis/import_preflight_from_toolshop.py — Import legacy preflight
classifications from `tg_scan_results.json` (ADR-013 Task 2).

Background
----------
Before ADR-013, a one-off Qwen3-14B scan produced
``/Users/protey/Downloads/tool-shop-crm/tg_scan_results.json`` — a list of
303 chat verdicts. ADR-013 reuses this output as a preflight cache so the
analyzer pipeline can skip re-classifying chats that were already judged.

Each JSON record becomes one row in ``analysis_chat_analysis`` with:

- ``analyzer_version = "v0.9+qwen3-14b-toolshop-legacy"`` — distinct marker;
  preflight only, no full-analysis artifacts.
- ``messages_analyzed_up_to = "preflight_only"`` — watermark placeholder;
  signals that only phase 0 (preflight) ran.
- ``narrative_markdown = ""`` and ``structured_extract = {"_v": 1}`` — minimal
  values that satisfy ``ck_analysis_chat_analysis_skipped_consistency`` when
  ``skipped_reason IS NULL``.
- ``skipped_reason = NULL`` — importing a classification is *not* a skip
  decision; that is the analyzer script's job (ADR-013 Task 3).

Category mapping (ADR-013 § 4)
------------------------------
``client``→``client``, ``possible_client``→``possible_client``,
``unknown``→``possible_client``, ``friend``→``friend``,
``family``→``family``, ``service``→``service``.

Confidence binning
------------------
``tg_scan_results.json`` stores confidence as a float, but
``PreflightConfidence`` is ``Literal["low","medium","high"]``. We bin:

- ``< 0.6``  → ``low``
- ``< 0.85`` → ``medium``
- ``>= 0.85``→ ``high``

Records without a confidence field default to ``"medium"``.

Chat lookup
-----------
The scan file has no ``telegram_chat_id`` — it carries only the chat ``name``.
ADR-013 § 4 allows matching by ``title``. We look up
``communications_telegram_chat`` rows with ``title = name`` under
``owner_account_id = 1`` (single-account installation today). Outcomes:

- 0 rows  → ``not_found`` (chat was never imported; skip, keep name for report).
- 1 row   → import.
- >1 rows → ``ambiguous_title`` (skip, log chat id list; keep name for report).

Idempotency
-----------
``UNIQUE (chat_id, analyzer_version)`` on ``analysis_chat_analysis``. Before
inserting, we probe with ``SELECT 1 FROM analysis_chat_analysis WHERE
chat_id = :id AND analyzer_version = :v``. Existing rows are counted as
``already_imported`` and left untouched.

Transaction boundary
--------------------
``import_preflight()`` does **no** commits or rollbacks — it just performs
SELECT/INSERT statements on the session the caller owns. The caller decides
whether to commit or roll back. The ``main()`` CLI wraps the call in
``try: commit() / except: rollback()``. In ``--dry-run`` mode no INSERTs run;
the function returns a report of what *would* have happened.

CLI
---
    python -m analysis.import_preflight_from_toolshop \\
        --input-file PATH [--dry-run] [--verbose]

``--dry-run`` performs lookups but commits no changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.analysis import TOOLSHOP_LEGACY_VERSION
from app.analysis.models import AnalysisChatAnalysis
from app.communications.models import CommunicationsTelegramChat
from app.config import settings

log = logging.getLogger(__name__)

DEFAULT_OWNER_ACCOUNT_ID = 1
WATERMARK_PLACEHOLDER = "preflight_only"
EMPTY_STRUCTURED_EXTRACT: dict[str, Any] = {"_v": 1}

_CATEGORY_MAP: dict[str, str] = {
    "client": "client",
    "possible_client": "possible_client",
    "unknown": "possible_client",
    "friend": "friend",
    "family": "family",
    "service": "service",
    "empty": "empty",
}


def _bin_confidence(value: Any) -> str:
    """Bin a float confidence (0–1) into the low/medium/high enum."""
    if value is None:
        return "medium"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "medium"
    if f < 0.6:
        return "low"
    if f < 0.85:
        return "medium"
    return "high"


def _map_category(category: str) -> str:
    try:
        return _CATEGORY_MAP[category]
    except KeyError as exc:
        raise ValueError(f"Unknown category in scan file: {category!r}") from exc


@dataclass
class ImportReport:
    input_path: Path
    total: int = 0
    imported: int = 0
    already_imported: int = 0
    not_found_names: list[str] = field(default_factory=list)
    ambiguous_names: list[str] = field(default_factory=list)
    json_distribution: Counter[str] = field(default_factory=Counter)
    imported_distribution: Counter[str] = field(default_factory=Counter)
    dry_run: bool = False


async def import_preflight(
    session: AsyncSession,
    records: list[dict[str, Any]],
    *,
    dry_run: bool = False,
    verbose: bool = False,
    owner_account_id: int = DEFAULT_OWNER_ACCOUNT_ID,
    input_path: Path | None = None,
) -> ImportReport:
    """Core import routine. Performs SELECTs and (unless dry_run) INSERTs on
    ``session``. Does not commit or roll back — the caller owns the transaction.
    """
    report = ImportReport(input_path=input_path or Path("<memory>"), dry_run=dry_run)
    report.total = len(records)
    now = datetime.now(UTC)

    for rec in records:
        name = rec.get("name")
        category = rec.get("category")
        if not name or not category:
            log.warning("skip record missing name/category: %r", rec)
            continue
        report.json_distribution[category] += 1

        lookup = await session.execute(
            select(CommunicationsTelegramChat.id).where(
                CommunicationsTelegramChat.title == name,
                CommunicationsTelegramChat.owner_account_id == owner_account_id,
            )
        )
        chat_ids = [row[0] for row in lookup.all()]

        if not chat_ids:
            report.not_found_names.append(name)
            if verbose:
                log.info("not_found: %r", name)
            continue
        if len(chat_ids) > 1:
            report.ambiguous_names.append(name)
            log.warning(
                "ambiguous_title: %r matches chat ids %s — skipped", name, chat_ids
            )
            continue

        chat_id = chat_ids[0]

        existing = await session.execute(
            select(AnalysisChatAnalysis.id).where(
                AnalysisChatAnalysis.chat_id == chat_id,
                AnalysisChatAnalysis.analyzer_version == TOOLSHOP_LEGACY_VERSION,
            )
        )
        if existing.scalar_one_or_none() is not None:
            report.already_imported += 1
            if verbose:
                log.info("already_imported: %r (chat_id=%s)", name, chat_id)
            continue

        mapped_category = _map_category(category)
        confidence = _bin_confidence(rec.get("confidence"))
        reason = rec.get("reason") or ""

        if not dry_run:
            await session.execute(
                insert(AnalysisChatAnalysis).values(
                    chat_id=chat_id,
                    analyzer_version=TOOLSHOP_LEGACY_VERSION,
                    analyzed_at=now,
                    messages_analyzed_up_to=WATERMARK_PLACEHOLDER,
                    narrative_markdown="",
                    structured_extract=EMPTY_STRUCTURED_EXTRACT,
                    chunks_count=0,
                    preflight_classification=mapped_category,
                    preflight_confidence=confidence,
                    preflight_reason=reason,
                    skipped_reason=None,
                )
            )

        report.imported += 1
        report.imported_distribution[mapped_category] += 1
        if verbose:
            log.info(
                "%s: %r chat_id=%s class=%s conf=%s",
                "would_import" if dry_run else "imported",
                name, chat_id, mapped_category, confidence,
            )

    return report


def _print_report(report: ImportReport) -> None:
    print("Preflight import report")
    print("=======================")
    print(f"Input: {report.input_path}")
    print(f"Total records in JSON: {report.total}")
    print()
    print(f"Imported: {report.imported}")
    print(f"Already imported (skipped): {report.already_imported}")
    print(f"Not found in DB: {len(report.not_found_names)}")
    print(f"Ambiguous title in DB: {len(report.ambiguous_names)}")
    print()

    print("JSON categories (input):")
    for cat, count in sorted(report.json_distribution.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:16s} {count}")
    print()
    print("Mapped classifications (imported only):")
    for cat, count in sorted(
        report.imported_distribution.items(), key=lambda kv: -kv[1]
    ):
        print(f"  {cat:16s} {count}")
    print()

    if report.not_found_names:
        print("Not found names:")
        for name in report.not_found_names:
            print(f"  - {name}")
        print()
    if report.ambiguous_names:
        print("Ambiguous names (title matched multiple chats):")
        for name in report.ambiguous_names:
            print(f"  - {name}")
        print()

    mode = "dry-run" if report.dry_run else "write"
    print(f"Mode: {mode} (dry-run={report.dry_run})")


async def run(
    input_file: Path,
    *,
    dry_run: bool,
    verbose: bool,
) -> ImportReport:
    records = json.loads(input_file.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(
            f"Expected a JSON array at top level of {input_file}, got {type(records).__name__}"
        )

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            try:
                report = await import_preflight(
                    session,
                    records,
                    dry_run=dry_run,
                    verbose=verbose,
                    input_path=input_file,
                )
            except Exception:
                await session.rollback()
                raise
            if dry_run:
                await session.rollback()
                tx_note = "rolled back (dry-run)"
            else:
                await session.commit()
                tx_note = "committed"
    finally:
        await engine.dispose()

    _print_report(report)
    print(f"Transaction: {tx_note}")
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Import legacy Qwen preflight classifications from "
            "tg_scan_results.json into analysis_chat_analysis (ADR-013 Task 2)."
        ),
    )
    parser.add_argument("--input-file", required=True, metavar="PATH")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    path = Path(args.input_file)
    if not path.exists():
        print(f"ERROR: input file not found: {path}", file=sys.stderr)
        return 2

    asyncio.run(run(path, dry_run=args.dry_run, verbose=args.verbose))
    return 0


if __name__ == "__main__":
    sys.exit(main())
