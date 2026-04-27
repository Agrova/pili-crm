"""ADR-014 Task 5: service layer for media_extract CLI.

Public surface:

- ``PendingMediaMessage`` — DTO returned by the selector.
- ``ExtractionResult``   — DTO consumed by the writer.
- ``ExtractorKind``      — routing decision enum.
- ``select_pending_messages``       — selector with chat+account JOIN + optional
                                      preflight classification filter.
- ``count_by_classification``       — pending-message counts grouped by preflight
                                      classification (for ``--dry-run`` output).
- ``get_latest_preflight_for_chat`` — latest preflight_classification for a chat.
- ``decide_extractor``              — routing rules from ADR-014 §5.
- ``extract_office_or_placeholder`` — xlsx / docx / placeholder branch.
- ``save_extraction``               — idempotent writer (ON CONFLICT DO NOTHING),
                                      with explicit ``regenerate`` overwrite.

CLI orchestration lives in ``cli.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import BigInteger, Boolean, Integer, String, bindparam, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.media_extract import office, vision
from analysis.media_extract.vision import VisionExtractionResult
from app.communications.models import CommunicationsTelegramMessageMediaExtraction

logger = logging.getLogger("analysis.media_extract.service")


_UQ_CONSTRAINT = "uq_communications_telegram_message_media_extraction_message_id"


# ── DTOs ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PendingMediaMessage:
    """A media-bearing message awaiting extraction."""

    message_id: int
    media_type: str
    mime_type: str | None
    file_name: str | None
    relative_path: str | None
    file_size_bytes: int | None
    phone_number: str  # E.164, e.g. "+77471057849"

    def absolute_path(self, exports_root: Path) -> Path | None:
        """Resolve the on-disk path; ``None`` if the file was not exported."""
        if self.relative_path is None:
            return None
        return exports_root / self.phone_number / self.relative_path


@dataclass(frozen=True)
class ExtractionResult:
    """Result of running an extractor on a single message."""

    message_id: int
    extracted_text: str
    extraction_method: str


class ExtractorKind(StrEnum):
    VISION = "vision"
    XLSX = "xlsx"
    DOCX = "docx"
    PLACEHOLDER = "placeholder"


# ── Selector ────────────────────────────────────────────────────────────────

_SELECT_BODY = """
    SELECT m.id            AS message_id,
           mm.media_type   AS media_type,
           mm.mime_type    AS mime_type,
           mm.file_name    AS file_name,
           mm.relative_path AS relative_path,
           mm.file_size_bytes AS file_size_bytes,
           acc.phone_number   AS phone_number
    FROM communications_telegram_message m
    JOIN communications_telegram_message_media mm
        ON mm.message_id = m.id
    JOIN communications_telegram_chat ch
        ON ch.id = m.chat_id
    JOIN communications_telegram_account acc
        ON acc.id = ch.owner_account_id"""

_LATERAL_LATEST_PREFLIGHT = """
    LEFT JOIN LATERAL (
        SELECT preflight_classification
        FROM analysis_chat_analysis
        WHERE chat_id = m.chat_id
        ORDER BY analyzed_at DESC
        LIMIT 1
    ) lp ON TRUE"""

_SELECT_WHERE = """
    WHERE
        (:chat_id IS NULL OR m.chat_id = :chat_id)
        AND (:message_id IS NULL OR m.id = :message_id)
        AND (m.id > :after_message_id)
        AND (NOT :skip_existing OR NOT EXISTS (
            SELECT 1
            FROM communications_telegram_message_media_extraction me
            WHERE me.message_id = m.id
              AND me.extractor_version = :extractor_version
        ))"""

_SELECT_TAIL = """
    ORDER BY m.id
    LIMIT :batch_size"""

_BASE_BINDPARAMS = [
    bindparam("chat_id", type_=BigInteger),
    bindparam("message_id", type_=BigInteger),
    bindparam("after_message_id", type_=BigInteger),
    bindparam("skip_existing", type_=Boolean),
    bindparam("extractor_version", type_=String),
    bindparam("batch_size", type_=Integer),
]

_SELECT_SQL = text(_SELECT_BODY + _SELECT_WHERE + _SELECT_TAIL).bindparams(
    *_BASE_BINDPARAMS
)


def _build_filtered_query(normal: set[str], include_unknown: bool):
    """Build a classification-filtered SELECT statement."""
    parts: list[str] = []
    if include_unknown:
        parts.append("lp.preflight_classification IS NULL")
    if normal:
        parts.append("lp.preflight_classification IN :classifications")
    class_filter = "\n    AND (" + " OR ".join(parts) + ")"
    sql = _SELECT_BODY + _LATERAL_LATEST_PREFLIGHT + _SELECT_WHERE + class_filter + _SELECT_TAIL
    extra = [bindparam("classifications", expanding=True)] if normal else []
    return text(sql).bindparams(*_BASE_BINDPARAMS, *extra)


async def select_pending_messages(
    session: AsyncSession,
    *,
    chat_id: int | None = None,
    message_id: int | None = None,
    extractor_version: str,
    batch_size: int = 100,
    skip_existing: bool = True,
    after_message_id: int = 0,
    allowed_classifications: set[str] | None = None,
) -> list[PendingMediaMessage]:
    """Return up to ``batch_size`` media messages awaiting extraction.

    Single SQL with chat→account JOIN — no N+1. ``skip_existing=False`` is
    used by ``--regenerate`` to surface already-extracted rows for overwrite.
    ``after_message_id`` enables cursor-style pagination (the CLI passes the
    last seen ``message_id`` of the previous batch); pass ``0`` for a full
    scan from the beginning.

    ``allowed_classifications``: if ``None`` or contains ``'all'``, no preflight
    filter is applied. Otherwise only messages from chats whose latest preflight
    record matches are returned. ``'unknown'`` means chats with no record.
    """
    base_params: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "after_message_id": after_message_id,
        "skip_existing": skip_existing,
        "extractor_version": extractor_version,
        "batch_size": batch_size,
    }

    if allowed_classifications is None or "all" in allowed_classifications:
        stmt = _SELECT_SQL
        params = base_params
    else:
        normal = allowed_classifications - {"unknown"}
        include_unknown = "unknown" in allowed_classifications
        stmt = _build_filtered_query(normal, include_unknown)
        params = {
            **base_params,
            **({"classifications": list(normal)} if normal else {}),
        }

    rows = await session.execute(stmt, params)
    return [
        PendingMediaMessage(
            message_id=int(r.message_id),
            media_type=r.media_type,
            mime_type=r.mime_type,
            file_name=r.file_name,
            relative_path=r.relative_path,
            file_size_bytes=r.file_size_bytes,
            phone_number=r.phone_number,
        )
        for r in rows
    ]


# ── Preflight helpers ────────────────────────────────────────────────────────

_COUNT_BY_CLASS_SQL = text("""
    SELECT lp.preflight_classification AS classification,
           COUNT(DISTINCT m.chat_id)   AS chat_count,
           COUNT(*)                    AS message_count
    FROM communications_telegram_message m
    JOIN communications_telegram_message_media mm ON mm.message_id = m.id
    LEFT JOIN LATERAL (
        SELECT preflight_classification
        FROM analysis_chat_analysis
        WHERE chat_id = m.chat_id
        ORDER BY analyzed_at DESC
        LIMIT 1
    ) lp ON TRUE
    WHERE lp.preflight_classification IN :classifications
      AND NOT EXISTS (
        SELECT 1 FROM communications_telegram_message_media_extraction me
        WHERE me.message_id = m.id AND me.extractor_version = :extractor_version
      )
    GROUP BY lp.preflight_classification
""").bindparams(
    bindparam("classifications", expanding=True),
    bindparam("extractor_version", type_=String),
)

_COUNT_UNKNOWN_SQL = text("""
    SELECT COUNT(DISTINCT m.chat_id) AS chat_count,
           COUNT(*)                  AS message_count
    FROM communications_telegram_message m
    JOIN communications_telegram_message_media mm ON mm.message_id = m.id
    LEFT JOIN LATERAL (
        SELECT preflight_classification
        FROM analysis_chat_analysis
        WHERE chat_id = m.chat_id
        ORDER BY analyzed_at DESC
        LIMIT 1
    ) lp ON TRUE
    WHERE lp.preflight_classification IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM communications_telegram_message_media_extraction me
        WHERE me.message_id = m.id AND me.extractor_version = :extractor_version
      )
""").bindparams(bindparam("extractor_version", type_=String))

_GET_PREFLIGHT_SQL = text("""
    SELECT preflight_classification
    FROM analysis_chat_analysis
    WHERE chat_id = :chat_id
    ORDER BY analyzed_at DESC
    LIMIT 1
""").bindparams(bindparam("chat_id", type_=BigInteger))


async def count_by_classification(
    session: AsyncSession,
    allowed_classifications: set[str],
    extractor_version: str,
) -> dict[str, tuple[int, int]]:
    """Return ``{classification: (chat_count, pending_message_count)}`` for dry-run output.

    ``'unknown'`` key counts chats with no preflight record at all.
    """
    result: dict[str, tuple[int, int]] = {}
    normal = allowed_classifications - {"unknown", "all"}
    include_unknown = "unknown" in allowed_classifications

    if normal:
        rows = await session.execute(
            _COUNT_BY_CLASS_SQL,
            {"classifications": list(normal), "extractor_version": extractor_version},
        )
        for row in rows:
            result[row.classification] = (int(row.chat_count), int(row.message_count))
        for c in normal:
            result.setdefault(c, (0, 0))

    if include_unknown:
        row = (
            await session.execute(_COUNT_UNKNOWN_SQL, {"extractor_version": extractor_version})
        ).one()
        result["unknown"] = (int(row.chat_count), int(row.message_count))

    return result


async def get_latest_preflight_for_chat(
    session: AsyncSession,
    chat_id: int,
) -> str | None:
    """Return the latest ``preflight_classification`` for a chat, or ``None`` if no record."""
    row = (await session.execute(_GET_PREFLIGHT_SQL, {"chat_id": chat_id})).first()
    return row.preflight_classification if row else None


# ── Routing ─────────────────────────────────────────────────────────────────


_XLSX_MIME_MARKERS = ("spreadsheetml", "excel")
_DOCX_MIME_MARKERS = ("wordprocessingml", "msword")
_XLSX_EXTENSIONS = (".xlsx", ".xls")
_DOCX_EXTENSIONS = (".docx", ".doc")


def decide_extractor(msg: PendingMediaMessage) -> ExtractorKind:
    """Routing rules from ADR-014 §5."""
    if msg.relative_path is None:
        return ExtractorKind.PLACEHOLDER

    if msg.media_type == "photo":
        return ExtractorKind.VISION

    if msg.media_type == "file":
        mime = (msg.mime_type or "").lower()
        name_lower = (msg.file_name or "").lower()

        if mime.startswith("image/"):
            return ExtractorKind.VISION

        if any(marker in mime for marker in _XLSX_MIME_MARKERS) or name_lower.endswith(
            _XLSX_EXTENSIONS
        ):
            return ExtractorKind.XLSX

        if any(marker in mime for marker in _DOCX_MIME_MARKERS) or name_lower.endswith(
            _DOCX_EXTENSIONS
        ):
            return ExtractorKind.DOCX

    return ExtractorKind.PLACEHOLDER


# ── Office / placeholder ────────────────────────────────────────────────────


def _format_placeholder(msg: PendingMediaMessage, *, suffix: str | None = None) -> str:
    name = msg.file_name or "<unnamed>"
    mime = msg.mime_type or "<unknown>"
    size_part = (
        f"size: {msg.file_size_bytes} bytes"
        if msg.file_size_bytes is not None
        else "size: unknown"
    )
    base = f"[file: {name}, type: {mime}, {size_part}]"
    extras: list[str] = []
    if msg.relative_path is None:
        extras.append("(file not exported)")
    if suffix:
        extras.append(suffix)
    if extras:
        return base + " " + " ".join(extras)
    return base


async def extract_office_or_placeholder(
    msg: PendingMediaMessage,
    kind: ExtractorKind,
    exports_root: Path,
) -> ExtractionResult:
    """Run xlsx / docx / placeholder branch synchronously.

    Any ``OfficeParseError`` or missing file degrades to a placeholder so
    one bad file cannot block a full prod run of ~7160 messages.
    """
    if kind is ExtractorKind.PLACEHOLDER:
        return ExtractionResult(
            message_id=msg.message_id,
            extracted_text=_format_placeholder(msg),
            extraction_method="placeholder",
        )

    if kind not in (ExtractorKind.XLSX, ExtractorKind.DOCX):
        raise ValueError(
            f"extract_office_or_placeholder: unsupported kind {kind!r}; "
            "vision routing must go through extract_image_or_fail"
        )

    abs_path = msg.absolute_path(exports_root)
    if abs_path is None or not abs_path.exists():
        return ExtractionResult(
            message_id=msg.message_id,
            extracted_text=_format_placeholder(
                msg, suffix="(file not found on disk)"
            ),
            extraction_method="placeholder",
        )

    try:
        if kind is ExtractorKind.XLSX:
            text_out = office.extract_xlsx(abs_path)
            method = "xlsx_openpyxl"
        else:
            text_out = office.extract_docx(abs_path)
            method = "docx_python_docx"
    except office.OfficeParseError as exc:
        logger.warning(
            "office parse error for message_id=%s file=%s: %s",
            msg.message_id,
            abs_path.name,
            exc,
        )
        name = msg.file_name or abs_path.name
        mime = msg.mime_type or "<unknown>"
        return ExtractionResult(
            message_id=msg.message_id,
            extracted_text=f"[file: {name}, type: {mime}, parse error: {exc}]",
            extraction_method="placeholder",
        )

    return ExtractionResult(
        message_id=msg.message_id,
        extracted_text=text_out,
        extraction_method=method,
    )


# ── Writer ──────────────────────────────────────────────────────────────────


async def save_extraction(
    session: AsyncSession,
    result: ExtractionResult,
    extractor_version: str,
    *,
    regenerate: bool = False,
) -> bool:
    """Persist ``result`` into ``communications_telegram_message_media_extraction``.

    ``regenerate=True``  → DELETE existing row, INSERT new (always writes).
    ``regenerate=False`` → ``ON CONFLICT DO NOTHING`` on the unique constraint;
                           returns ``False`` if a row already existed.

    Uses ``flush()`` so the standard rollback-after-test fixture stays valid;
    the CLI driver is responsible for ``commit()``.
    """
    table = CommunicationsTelegramMessageMediaExtraction.__table__

    if regenerate:
        await session.execute(
            delete(CommunicationsTelegramMessageMediaExtraction).where(
                CommunicationsTelegramMessageMediaExtraction.message_id
                == result.message_id
            )
        )
        await session.execute(
            pg_insert(table).values(
                message_id=result.message_id,
                extracted_text=result.extracted_text,
                extraction_method=result.extraction_method,
                extractor_version=extractor_version,
            )
        )
        await session.flush()
        return True

    stmt = (
        pg_insert(table)
        .values(
            message_id=result.message_id,
            extracted_text=result.extracted_text,
            extraction_method=result.extraction_method,
            extractor_version=extractor_version,
        )
        .on_conflict_do_nothing(constraint=_UQ_CONSTRAINT)
        .returning(table.c.id)
    )
    inserted = (await session.execute(stmt)).scalar()
    await session.flush()
    return inserted is not None


# ── Vision wrapper (Phase B) ───────────────────────────────────────────────


_KNOWN_VISION_METHODS: dict[str, str] = {
    "qwen3-vl-30b-a3b-instruct-4bit": "vision_qwen3-vl-30b-a3b",
    "qwen3-vl-8b-instruct-mlx-4bit": "vision_qwen3-vl-8b",
}


def derive_extraction_method_from_model(model_id: str) -> str:
    """Map an HF model id to the short ``extraction_method`` tag.

    Recognises the two ADR-014 reference models and falls back to a
    sanitized basename for everything else.
    """
    basename = model_id.rsplit("/", 1)[-1].lower()
    if basename in _KNOWN_VISION_METHODS:
        return _KNOWN_VISION_METHODS[basename]
    sanitized = re.sub(r"[^a-z0-9]+", "-", basename).strip("-")
    return f"vision_{sanitized}"


# Indirection so tests can patch ``service._vision_extract_image`` instead of
# reaching into ``vision`` from the outside.
async def _vision_extract_image(
    path: Path,
    model_id: str,
    endpoint: str,
) -> VisionExtractionResult:
    return await vision.extract_image(path, model_id, endpoint)


async def extract_image_or_fail(
    msg: PendingMediaMessage,
    exports_root: Path,
    model_id: str,
    endpoint: str,
) -> ExtractionResult:
    """Vision branch.

    Behaviour:
    - ``relative_path`` missing or file not on disk → placeholder.
    - ``VisionImageError`` (decode failure, broken image) → placeholder.
    - ``VisionAPIError`` (LM Studio dead, OOM, etc.) → propagate; the CLI
      treats this as a fatal batch-level failure.
    """
    abs_path = msg.absolute_path(exports_root)
    if abs_path is None:
        return ExtractionResult(
            message_id=msg.message_id,
            extracted_text=_format_placeholder(msg),
            extraction_method="placeholder",
        )
    if not abs_path.exists():
        return ExtractionResult(
            message_id=msg.message_id,
            extracted_text=_format_placeholder(
                msg, suffix="(file not found on disk)"
            ),
            extraction_method="placeholder",
        )

    try:
        vision_result = await _vision_extract_image(abs_path, model_id, endpoint)
    except vision.VisionImageError as exc:
        logger.warning(
            "vision image error for message_id=%s file=%s: %s",
            msg.message_id,
            abs_path.name,
            exc,
        )
        name = msg.file_name or abs_path.name
        mime = msg.mime_type or "<unknown>"
        return ExtractionResult(
            message_id=msg.message_id,
            extracted_text=f"[file: {name}, type: {mime}, parse error: {exc}]",
            extraction_method="placeholder",
        )

    if vision_result.extraction_method == "vision":
        method = derive_extraction_method_from_model(model_id)
    else:
        method = vision_result.extraction_method

    return ExtractionResult(
        message_id=msg.message_id,
        extracted_text=vision_result.text,
        extraction_method=method,
    )
