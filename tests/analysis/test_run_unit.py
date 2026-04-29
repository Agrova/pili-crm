"""ADR-011 Task 3 unit tests for ``analysis/run.py``.

No-DB tests — argparse wiring, pure helpers, SIGINT flag flow,
_build_extract retry, _select_extract_prompt branching. Items 16-22 of
the TZ checklist; the DB-bound items live in ``test_run_integration.py``.
"""

from __future__ import annotations

import json
import signal
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from analysis import run
from analysis.chunking import DEFAULT_CHUNK_SIZE
from analysis.llm_client import LLMRequestError
from analysis.prompts import (
    STRUCTURED_EXTRACT_PROMPT,
    STRUCTURED_EXTRACT_PROMPT_WITH_SCHEMA,
)
from app.analysis import ANALYZER_VERSION, ANALYZER_VERSION_BASE, make_analyzer_version


@dataclass
class _FakeLLM:
    responses: list[str] = field(default_factory=list)
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
        if not self.responses:
            raise AssertionError("FakeLLM ran out of canned responses")
        return self.responses.pop(0)


# ── argparse ────────────────────────────────────────────────────────────────


def test_parser_defaults_chunk_size_to_300() -> None:
    args = run.build_parser().parse_args(["--all"])
    assert args.chunk_size == DEFAULT_CHUNK_SIZE
    assert args.chunk_size == 300


def test_parser_defaults_prompt_variant_to_example() -> None:
    args = run.build_parser().parse_args(["--all"])
    assert args.prompt_variant == "example"


def test_parser_accepts_schema_variant() -> None:
    args = run.build_parser().parse_args(["--all", "--prompt-variant", "schema"])
    assert args.prompt_variant == "schema"


def test_parser_rejects_unknown_prompt_variant() -> None:
    with pytest.raises(SystemExit):
        run.build_parser().parse_args(["--all", "--prompt-variant", "yolo"])


def test_parser_rejects_combined_selectors() -> None:
    with pytest.raises(SystemExit):
        run.build_parser().parse_args(["--all", "--chat-id", "1"])


def test_parser_rejects_resume_with_restart() -> None:
    with pytest.raises(SystemExit):
        run.build_parser().parse_args(["--all", "--resume", "--restart"])


def test_parser_chat_ids_passes_through_string() -> None:
    args = run.build_parser().parse_args(["--chat-ids", "1,2,3"])
    assert args.chat_ids == "1,2,3"


# ── _select_extract_prompt ──────────────────────────────────────────────────


def test_select_extract_prompt_example_returns_default_template() -> None:
    assert run._select_extract_prompt("example") is STRUCTURED_EXTRACT_PROMPT


def test_select_extract_prompt_schema_returns_schema_template() -> None:
    assert (
        run._select_extract_prompt("schema") is STRUCTURED_EXTRACT_PROMPT_WITH_SCHEMA
    )


def test_select_extract_prompt_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown prompt variant"):
        run._select_extract_prompt("nope")


# ── pure helpers ────────────────────────────────────────────────────────────


def test_strip_json_fence_strips_markdown_block() -> None:
    raw = "```json\n{\"_v\": 1}\n```"
    assert run._strip_json_fence(raw) == '{"_v": 1}'


def test_strip_json_fence_passthrough_for_plain_json() -> None:
    raw = '  {"_v": 1}  '
    assert run._strip_json_fence(raw) == '{"_v": 1}'


def test_parse_since_relative_days() -> None:
    cutoff = run._parse_since("7d")
    delta = datetime.now(tz=UTC) - cutoff
    assert 6.9 <= delta.days + delta.seconds / 86400 <= 7.1


def test_parse_since_iso_date() -> None:
    cutoff = run._parse_since("2025-03-15")
    assert cutoff.year == 2025 and cutoff.month == 3 and cutoff.day == 15
    assert cutoff.tzinfo is UTC


# ── SIGINT flag flow ────────────────────────────────────────────────────────


def test_install_sigint_handler_sets_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag-assertion test: invoke the handler directly and check the global flag.

    A full signal-raising test (``os.kill(os.getpid(), signal.SIGINT)``) was
    deliberately deferred — see the TODO in ``analysis/run.py``.
    """
    run.reset_shutdown_flag()
    assert run.shutdown_requested() is False

    run.install_sigint_handler()
    handler = signal.getsignal(signal.SIGINT)
    assert callable(handler)
    handler(signal.SIGINT, None)  # type: ignore[arg-type,misc]

    assert run.shutdown_requested() is True
    run.reset_shutdown_flag()


# ── _build_extract retry semantics ──────────────────────────────────────────


async def test_build_extract_returns_parsed_on_first_attempt() -> None:
    payload = json.dumps({"_v": 1, "identity": {"name_guess": "Иван"}})
    llm = _FakeLLM(responses=[payload])
    extract = await run._build_extract(
        "narrative", llm, prompt_variant="example"
    )
    assert extract.identity is not None
    assert extract.identity.name_guess == "Иван"
    assert len(llm.calls) == 1


async def test_build_extract_retries_then_raises() -> None:
    bad = "not valid json"
    llm = _FakeLLM(responses=[bad, bad, bad])
    with pytest.raises(LLMRequestError):
        await run._build_extract("narrative", llm, prompt_variant="example")
    assert len(llm.calls) == run.EXTRACT_RETRY_ATTEMPTS == 3


async def test_build_extract_recovers_on_retry() -> None:
    good = json.dumps({"_v": 1})
    llm = _FakeLLM(responses=["junk", good])
    extract = await run._build_extract(
        "narrative", llm, prompt_variant="example"
    )
    assert extract.schema_version == 1
    assert len(llm.calls) == 2


# ── ADR-011 Addendum 2: make_analyzer_version + CLI flags ──────────────────


def test_make_analyzer_version_default_mac_no_suffix() -> None:
    """Backward compat: worker_tag='mac' (default) → no suffix."""
    assert make_analyzer_version("mac") == "analysis-v1.2+qwen3-14b"
    assert make_analyzer_version() == "analysis-v1.2+qwen3-14b"
    assert make_analyzer_version("mac") == ANALYZER_VERSION_BASE
    assert make_analyzer_version("mac") == ANALYZER_VERSION


def test_make_analyzer_version_pc_with_suffix() -> None:
    """Non-mac tag gets @<tag> suffix."""
    assert make_analyzer_version("pc") == "analysis-v1.2+qwen3-14b@pc"
    assert make_analyzer_version("worker-1") == "analysis-v1.2+qwen3-14b@worker-1"


def test_make_analyzer_version_invalid_tag_raises() -> None:
    """Invalid characters in tag raise ValueError."""
    with pytest.raises(ValueError):
        make_analyzer_version("PC")       # uppercase
    with pytest.raises(ValueError):
        make_analyzer_version("pc!")      # special char
    with pytest.raises(ValueError):
        make_analyzer_version("")         # empty
    with pytest.raises(ValueError):
        make_analyzer_version("mac@oops")  # @ is not allowed


def test_run_cli_worker_tag_validation() -> None:
    """CLI rejects invalid --worker-tag values (uppercase → parser.error → exit 2)."""
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "DATABASE_URL": "postgresql+asyncpg://nobody@nowhere/none",
        "TEST_DATABASE_URL": "postgresql+asyncpg://pili:pili@localhost:5432/pili_crm_test",
    }
    result = subprocess.run(
        [
            sys.executable, "-m", "analysis.run",
            "--chat-id", "1",
            "--worker-tag", "PC",
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "worker-tag" in combined.lower() or "invalid" in combined.lower()


async def test_run_no_apply_skips_apply_call(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When no_apply=True, apply_analysis_to_customer is NOT called."""
    import logging
    from unittest.mock import AsyncMock, MagicMock

    run.reset_shutdown_flag()

    fake_analysis = MagicMock()
    fake_analysis.id = 999

    monkeypatch.setattr("analysis.run.set_stage", AsyncMock())
    monkeypatch.setattr("analysis.run.mark_done", AsyncMock())
    monkeypatch.setattr("analysis.run.mark_failed", AsyncMock())
    mock_record = AsyncMock(return_value=fake_analysis)
    monkeypatch.setattr("analysis.run.record_full_analysis", mock_record)
    mock_apply = AsyncMock()
    monkeypatch.setattr("analysis.run.apply_analysis_to_customer", mock_apply)

    fake_msg = MagicMock()
    fake_msg.telegram_message_id = 42
    monkeypatch.setattr(
        "analysis.run.load_chat_messages", AsyncMock(return_value=[fake_msg])
    )
    monkeypatch.setattr(
        "analysis.run.split_into_chunks",
        lambda msgs, *, chunk_size: [msgs],
    )
    monkeypatch.setattr(
        "analysis.run._summarise_chunks", AsyncMock(return_value=["summary"])
    )
    monkeypatch.setattr(
        "analysis.run._build_extract", AsyncMock(return_value=MagicMock())
    )
    monkeypatch.setattr(
        "analysis.run.match_extract", AsyncMock(return_value=MagicMock())
    )

    llm = _FakeLLM(responses=["master_summary_text", "narrative_text"])
    session = MagicMock()
    commit_fn = AsyncMock()

    with caplog.at_level(logging.INFO, logger="analysis.run"):
        result = await run.process_chat(
            session,
            chat_id=1,
            llm_client=llm,  # type: ignore[arg-type]
            catalog=[],
            chunk_size=300,
            prompt_variant="example",
            force=False,
            commit_fn=commit_fn,
            analyzer_version="analysis-v1.0+qwen3-14b",
            no_apply=True,
        )

    assert result == "done"
    assert mock_apply.call_count == 0
    assert mock_record.call_count == 1
    assert any(
        "--no-apply" in r.message and "skipping" in r.message
        for r in caplog.records
    )
