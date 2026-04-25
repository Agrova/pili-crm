"""ADR-011 §9 / Task 3: tests for analysis/state_check.py.

Six tests:

1. ``check_running_process`` returns chat ids when state row is fresh.
2. ``check_running_process`` returns ``[]`` when only stale rows exist.
3. ``get_stale_states`` returns rows older than the threshold (and skips
   ``stage='done'``).
4. ``restart_stale`` deletes the targeted state rows and returns the count.
5. ``prompt_resume_or_restart`` returns ``"resume"`` for ``r``.
6. ``prompt_resume_or_restart`` returns ``"restart"`` for ``s`` and
   re-prompts on invalid input.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Side-effect imports: FK target must be mapped first.
import app.communications.models  # noqa: F401
from analysis import state_check
from app.analysis.models import AnalysisChatAnalysisState


async def _seed_chat(session: AsyncSession, marker: str) -> int:
    account_id = (
        await session.execute(
            text(
                "SELECT id FROM communications_telegram_account "
                "WHERE phone_number = '+77471057849'"
            )
        )
    ).scalar()
    assert account_id is not None, "ADR-012 seed account missing"
    chat_id = (
        await session.execute(
            text(
                "INSERT INTO communications_telegram_chat "
                "(owner_account_id, telegram_chat_id, chat_type, title) "
                "VALUES (:aid, :tg, 'personal_chat', :t) RETURNING id"
            ),
            {
                "aid": account_id,
                "tg": f"sc-{marker}-{datetime.now(tz=UTC).timestamp()}",
                "t": marker,
            },
        )
    ).scalar_one()
    await session.flush()
    return int(chat_id)


async def _seed_state(
    session: AsyncSession,
    *,
    chat_id: int,
    stage: str,
    updated_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO analysis_chat_analysis_state "
            "(chat_id, stage, created_at, updated_at) "
            "VALUES (:cid, :stage, :ts, :ts)"
        ),
        {"cid": chat_id, "stage": stage, "ts": updated_at},
    )
    await session.flush()


async def test_check_running_process_returns_fresh_chat_ids(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "fresh")
    fresh = datetime.now(tz=UTC) - timedelta(seconds=30)
    await _seed_state(db_session, chat_id=chat_id, stage="loading", updated_at=fresh)

    running = await state_check.check_running_process(db_session)
    assert chat_id in running


async def test_check_running_process_ignores_stale(
    db_session: AsyncSession,
) -> None:
    chat_id = await _seed_chat(db_session, "stale-only")
    old = datetime.now(tz=UTC) - timedelta(minutes=30)
    await _seed_state(db_session, chat_id=chat_id, stage="loading", updated_at=old)

    running = await state_check.check_running_process(db_session)
    assert chat_id not in running


async def test_get_stale_states_returns_old_non_done_rows(
    db_session: AsyncSession,
) -> None:
    stale_chat = await _seed_chat(db_session, "stale-row")
    fresh_chat = await _seed_chat(db_session, "fresh-row")

    stale_ts = datetime.now(tz=UTC) - timedelta(minutes=30)
    fresh_ts = datetime.now(tz=UTC) - timedelta(seconds=10)
    await _seed_state(
        db_session, chat_id=stale_chat, stage="chunk_summaries", updated_at=stale_ts
    )
    await _seed_state(
        db_session, chat_id=fresh_chat, stage="loading", updated_at=fresh_ts
    )

    stale = await state_check.get_stale_states(db_session)
    stale_chat_ids = {s.chat_id for s in stale}
    assert stale_chat in stale_chat_ids
    assert fresh_chat not in stale_chat_ids


async def test_restart_stale_deletes_rows_and_returns_count(
    db_session: AsyncSession,
) -> None:
    chat_a = await _seed_chat(db_session, "restart-a")
    chat_b = await _seed_chat(db_session, "restart-b")
    old = datetime.now(tz=UTC) - timedelta(minutes=30)
    await _seed_state(db_session, chat_id=chat_a, stage="loading", updated_at=old)
    await _seed_state(db_session, chat_id=chat_b, stage="loading", updated_at=old)

    n = await state_check.restart_stale(db_session, [chat_a, chat_b])
    assert n == 2

    remaining = await state_check.get_stale_states(db_session)
    remaining_ids = {s.chat_id for s in remaining}
    assert chat_a not in remaining_ids
    assert chat_b not in remaining_ids


def test_prompt_resume_returns_resume() -> None:
    fake_state = AnalysisChatAnalysisState(
        chat_id=1, stage="loading", chunks_done=2, chunks_total=10
    )
    answers = iter(["r"])

    def _input(_prompt: str) -> str:
        return next(answers)

    choice = state_check.prompt_resume_or_restart([fake_state], input_fn=_input)
    assert choice == "resume"


def test_prompt_resume_handles_invalid_then_restart() -> None:
    fake_state = AnalysisChatAnalysisState(
        chat_id=1, stage="loading", chunks_done=2, chunks_total=10
    )
    answers = iter(["", "maybe", "s"])

    def _input(_prompt: str) -> str:
        return next(answers)

    choice = state_check.prompt_resume_or_restart([fake_state], input_fn=_input)
    assert choice == "restart"


@pytest.mark.parametrize("answer,expected", [("resume", "resume"), ("restart", "restart")])
def test_prompt_resume_accepts_long_form(answer: str, expected: str) -> None:
    fake_state = AnalysisChatAnalysisState(
        chat_id=1, stage="loading", chunks_done=2, chunks_total=10
    )
    answers = iter([answer])

    def _input(_prompt: str) -> str:
        return next(answers)

    assert state_check.prompt_resume_or_restart([fake_state], input_fn=_input) == expected
