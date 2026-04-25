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
