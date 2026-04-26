"""ADR-011 Task 3 integration tests for ``analysis/run.py``.

Exercises the orchestrator end-to-end against the real database with a
**mock LM Studio**. Items 23-25 of the TZ checklist:

- ``cmd_status`` reports state rows.
- ``select_chat_ids`` honours ``--all`` / ``--review-status`` / ``--chat-ids``.
- ``filter_already_processed`` skips chats with an existing analysis.
- ``process_chat`` happy path: 10-message chat → 1 chunk → analysis row +
  state cleared (``mark_done``).
- ``process_chat`` chunked path: 400-message chat at ``chunk_size=300``
  produces 2 chunks (chunk-summary called twice).
- ``process_chat`` empty chat → ``failed`` status with empty_chat reason.

Mock LM Studio matches each prompt by Russian-language prefix and returns
canned responses. ``commit_fn`` is set to ``session.flush`` so the
rollback fixture continues to work.
"""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Side-effect imports: FK targets must be mapped before tests run.
import app.catalog.models  # noqa: F401
import app.communications.models  # noqa: F401
import app.orders.models  # noqa: F401
import app.pricing.models  # noqa: F401
from analysis import run
from analysis.matching import CatalogEntry
from app.analysis import ANALYZER_VERSION
from app.analysis import repository as analysis_repo

# ── Mock LM Studio ──────────────────────────────────────────────────────────


@dataclass
class _MockLM:
    """Routes prompts to canned responses by Russian-language prefix."""

    chunk_summary: str = "Краткое саммари фрагмента."
    master_summary: str = "Мастер-саммари."
    narrative: str = "# Клиент\nИван [id=1]"
    extract: dict[str, Any] = field(
        default_factory=lambda: {
            "_v": 1,
            "identity": {"name_guess": "Иван"},
        }
    )
    matching: dict[str, Any] = field(
        default_factory=lambda: {
            "decision": "not_found",
            "product_id": None,
            "candidate_ids": None,
            "note": "n/a",
        }
    )
    calls: list[str] = field(default_factory=list)

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        response_format: dict[str, object] | None = None,
    ) -> str:
        self.calls.append(prompt)
        if prompt.startswith("Ты помощник владельца"):
            return self.chunk_summary
        if prompt.startswith("Тебе дан список кратких саммари"):
            return self.master_summary
        if prompt.startswith("Тебе дан материал по одной"):
            return self.narrative
        if prompt.startswith("Тебе дан портрет клиента"):
            return json.dumps(self.extract, ensure_ascii=False)
        if prompt.startswith("Тебе дана текстовая формулировка"):
            return json.dumps(self.matching, ensure_ascii=False)
        raise AssertionError(f"unexpected prompt prefix: {prompt[:80]!r}")


# ── Seed helpers ────────────────────────────────────────────────────────────


async def _seed_chat(db: AsyncSession, marker: str) -> int:
    account_id = (
        await db.execute(
            text(
                "SELECT id FROM communications_telegram_account "
                "WHERE phone_number = '+77471057849'"
            )
        )
    ).scalar()
    assert account_id is not None, "ADR-012 seed account missing"
    chat_id = (
        await db.execute(
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type, title, "
                " review_status) "
                "VALUES (:aid, :tg, 'personal_chat', :t, 'unreviewed') "
                "RETURNING id"
            ),
            {
                "aid": account_id,
                "tg": f"int-{marker}-{datetime.now(tz=UTC).timestamp()}",
                "t": marker,
            },
        )
    ).scalar_one()
    await db.flush()
    return int(chat_id)


async def _seed_messages(
    db: AsyncSession, chat_id: int, count: int, *, prefix: str = "m"
) -> None:
    base = datetime.now(tz=UTC) - timedelta(hours=count)
    for i in range(count):
        await db.execute(
            text(
                "INSERT INTO communications_telegram_message "
                "(chat_id, telegram_message_id, sent_at, text) "
                "VALUES (:cid, :tmid, :sent, :body)"
            ),
            {
                "cid": chat_id,
                "tmid": f"{prefix}-{chat_id}-{i}",
                "sent": base + timedelta(minutes=i),
                "body": f"сообщение {i}",
            },
        )
    await db.flush()


async def _flush(session: AsyncSession) -> None:
    await session.flush()


def _make_args(**overrides: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = dict(
        chat_id=None,
        chat_ids=None,
        all=False,
        since=None,
        review_status=None,
        chunk_size=300,
        prompt_variant="example",
        endpoint=None,
        dry_run=False,
        status=False,
        resume=False,
        restart=False,
        force=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


# ── Tests ───────────────────────────────────────────────────────────────────


async def test_select_chat_ids_chat_id_singleton(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "sel-single")
    args = _make_args(chat_id=chat_id)
    assert await run.select_chat_ids(db_session, args) == [chat_id]


async def test_select_chat_ids_chat_ids_csv(db_session: AsyncSession) -> None:
    a = await _seed_chat(db_session, "sel-a")
    b = await _seed_chat(db_session, "sel-b")
    args = _make_args(chat_ids=f"{a},{b}")
    assert await run.select_chat_ids(db_session, args) == [a, b]


async def test_select_chat_ids_review_status_filter(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "sel-rev")
    args = _make_args(review_status="unreviewed")
    ids = await run.select_chat_ids(db_session, args)
    assert chat_id in ids


async def test_filter_already_processed_skips_existing(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "skip-existing")
    await analysis_repo.upsert_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version=ANALYZER_VERSION,
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to="0",
        narrative_markdown="n",
        structured_extract={"_v": 1},
        chunks_count=0,
    )
    await db_session.flush()

    to_process, skipped = await run.filter_already_processed(
        db_session, [chat_id], force=False
    )
    assert to_process == []
    assert skipped == [chat_id]


async def test_filter_already_processed_force_returns_all(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "force-existing")
    await analysis_repo.upsert_analysis(
        db_session,
        chat_id=chat_id,
        analyzer_version=ANALYZER_VERSION,
        analyzed_at=datetime.now(tz=UTC),
        messages_analyzed_up_to="0",
        narrative_markdown="n",
        structured_extract={"_v": 1},
        chunks_count=0,
    )
    await db_session.flush()

    to_process, skipped = await run.filter_already_processed(
        db_session, [chat_id], force=True
    )
    assert to_process == [chat_id]
    assert skipped == []


async def test_cmd_status_prints_state_rows(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "status")
    await db_session.execute(
        text(
            "INSERT INTO analysis_chat_analysis_state "
            "(chat_id, stage, chunks_done, chunks_total) "
            "VALUES (:cid, 'chunk_summaries', 1, 3)"
        ),
        {"cid": chat_id},
    )
    await db_session.flush()

    buf = io.StringIO()
    with redirect_stdout(buf):
        await run.cmd_status(db_session)
    output = buf.getvalue()
    assert "chunk_summaries" in output
    assert str(chat_id) in output


async def test_process_chat_empty_chat_marks_failed(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "empty")
    llm = _MockLM()

    async def commit() -> None:
        await db_session.flush()

    status = await run.process_chat(
        db_session,
        chat_id=chat_id,
        llm_client=llm,  # type: ignore[arg-type]
        catalog=[],
        chunk_size=300,
        prompt_variant="example",
        force=False,
        commit_fn=commit,
    )
    assert status == "failed"
    state_row = (
        await db_session.execute(
            text(
                "SELECT stage, failure_reason FROM analysis_chat_analysis_state "
                "WHERE chat_id = :cid"
            ),
            {"cid": chat_id},
        )
    ).first()
    assert state_row is not None
    assert state_row.stage == "failed"
    assert state_row.failure_reason == "empty_chat"
    assert llm.calls == []


async def test_process_chat_happy_path_one_chunk(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "happy-1chunk")
    await _seed_messages(db_session, chat_id, count=10)
    llm = _MockLM()
    catalog: list[CatalogEntry] = []

    async def commit() -> None:
        await db_session.flush()

    status = await run.process_chat(
        db_session,
        chat_id=chat_id,
        llm_client=llm,  # type: ignore[arg-type]
        catalog=catalog,
        chunk_size=300,
        prompt_variant="example",
        force=False,
        commit_fn=commit,
    )
    assert status == "done"

    # Analysis row must exist for current ANALYZER_VERSION.
    arow = await analysis_repo.get_analysis_by_chat_and_version(
        db_session, chat_id, ANALYZER_VERSION
    )
    assert arow is not None
    assert arow.chunks_count == 1
    assert arow.narrative_markdown.startswith("# Клиент")

    # State row must be cleared (mark_done deletes it).
    remaining = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM analysis_chat_analysis_state "
                "WHERE chat_id = :cid"
            ),
            {"cid": chat_id},
        )
    ).scalar()
    assert remaining == 0

    # Chunk summary should be called once for one chunk.
    chunk_calls = [c for c in llm.calls if c.startswith("Ты помощник владельца")]
    assert len(chunk_calls) == 1


async def test_process_chat_two_chunks_at_size_300(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "happy-2chunks")
    await _seed_messages(db_session, chat_id, count=400)
    llm = _MockLM()

    async def commit() -> None:
        await db_session.flush()

    status = await run.process_chat(
        db_session,
        chat_id=chat_id,
        llm_client=llm,  # type: ignore[arg-type]
        catalog=[],
        chunk_size=300,
        prompt_variant="example",
        force=False,
        commit_fn=commit,
    )
    assert status == "done"

    arow = await analysis_repo.get_analysis_by_chat_and_version(
        db_session, chat_id, ANALYZER_VERSION
    )
    assert arow is not None
    assert arow.chunks_count == 2

    chunk_calls = [c for c in llm.calls if c.startswith("Ты помощник владельца")]
    assert len(chunk_calls) == 2
