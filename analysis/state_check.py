"""ADR-011 §9 / Task 3: lock + stale checkpoint inspection at startup.

``analysis_chat_analysis_state`` is the source of truth for resumable
runs. Before launching a fresh pipeline pass, ``analysis/run.py`` calls
into this module to:

1. Detect another running process (state row with ``updated_at`` newer
   than ``STALE_THRESHOLD_MINUTES``) → :func:`check_running_process`
   returns ``True`` and the CLI exits with code 1.
2. Detect prior interrupted runs (state row older than the threshold)
   → :func:`get_stale_states`. The CLI either:
   - applies ``--resume`` (continue from the checkpoint), or
   - applies ``--restart`` (delete state rows, start over), or
   - asks the operator interactively via :func:`prompt_resume_or_restart`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import AnalysisChatAnalysisState

STALE_THRESHOLD_MINUTES = 10

ResumeChoice = Literal["resume", "restart"]


def _stale_cutoff(now: datetime | None = None) -> datetime:
    moment = now or datetime.now(tz=UTC)
    return moment - timedelta(minutes=STALE_THRESHOLD_MINUTES)


async def check_running_process(
    session: AsyncSession, *, now: datetime | None = None
) -> list[int]:
    """Return chat ids whose state row was updated less than 10 min ago.

    Non-empty list means another process is alive — caller exits with
    code 1. Empty list means the slate is clear.
    """
    cutoff = _stale_cutoff(now)
    stmt = select(AnalysisChatAnalysisState.chat_id).where(
        AnalysisChatAnalysisState.stage != "done",
        AnalysisChatAnalysisState.updated_at >= cutoff,
    )
    result = await session.execute(stmt)
    return [int(cid) for cid in result.scalars().all()]


async def get_stale_states(
    session: AsyncSession, *, now: datetime | None = None
) -> list[AnalysisChatAnalysisState]:
    """State rows older than the stale threshold and not done — resumable."""
    cutoff = _stale_cutoff(now)
    stmt = select(AnalysisChatAnalysisState).where(
        AnalysisChatAnalysisState.stage != "done",
        AnalysisChatAnalysisState.updated_at < cutoff,
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def restart_stale(session: AsyncSession, chat_ids: list[int]) -> int:
    """Drop state rows for the given chat ids — they will be re-processed."""
    if not chat_ids:
        return 0
    stmt = delete(AnalysisChatAnalysisState).where(
        AnalysisChatAnalysisState.chat_id.in_(chat_ids)
    )
    result = await session.execute(stmt)
    rowcount = getattr(result, "rowcount", 0) or 0
    return int(rowcount)


def prompt_resume_or_restart(
    stale: list[AnalysisChatAnalysisState],
    *,
    input_fn: object = input,
) -> ResumeChoice:
    """Ask the operator how to handle stale state rows.

    ``input_fn`` is injected so tests can drive the prompt without
    monkeypatching builtins.
    """
    print(
        f"\nFound {len(stale)} stale analysis state row(s) "
        f"(updated >= {STALE_THRESHOLD_MINUTES} min ago):"
    )
    for s in stale:
        print(
            f"  chat_id={s.chat_id}  stage={s.stage}  "
            f"chunks={s.chunks_done}/{s.chunks_total}  updated_at={s.updated_at}"
        )
    while True:
        # input_fn is callable in production (built-in `input`); typed as object
        # so test injectables don't have to satisfy a Callable signature exactly.
        answer = input_fn(  # type: ignore[operator]
            "[r]esume from checkpoint or [s]tart over? "
        ).strip().lower()
        if answer in {"r", "resume"}:
            return "resume"
        if answer in {"s", "start", "restart"}:
            return "restart"
        print("Please answer 'r' (resume) or 's' (start over).")


__all__ = [
    "STALE_THRESHOLD_MINUTES",
    "ResumeChoice",
    "check_running_process",
    "get_stale_states",
    "restart_stale",
    "prompt_resume_or_restart",
]
