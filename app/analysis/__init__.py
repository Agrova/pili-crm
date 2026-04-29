"""ADR-011: Telegram chat analysis pipeline.

Ninth application module of the monolith. Hosts the schema and Pydantic models
backing the LLM-driven analysis of Telegram conversations (Qwen3-14B via
LM Studio). Business logic (repository, service, runner, MCP tools) arrives in
Tasks 2–6 of ADR-011.

ANALYZER_VERSION is bumped whenever the prompts or the structured_extract
schema change; old analysis_chat_analysis rows stay in place as history.
"""

from __future__ import annotations

import re

ANALYZER_VERSION_BASE: str = "analysis-v1.3+qwen3-14b"

# Backward-compatible alias. Existing `from app.analysis import ANALYZER_VERSION`
# imports resolve to the base (== mac default, no suffix).
ANALYZER_VERSION: str = ANALYZER_VERSION_BASE

# ADR-013 Task 2: legacy preflight classifications imported from
# `tg_scan_results.json` (Qwen3-14B, pre-ADR-013 scan). Distinct version means
# these rows carry *only* preflight fields — no narrative, no structured extract.
TOOLSHOP_LEGACY_VERSION: str = "v0.9+qwen3-14b-toolshop-legacy"

_WORKER_TAG_RE = re.compile(r"^[a-z0-9-]+$")


def make_analyzer_version(worker_tag: str = "mac") -> str:
    """Compose analyzer_version with worker tag suffix.

    Backward compatibility: worker_tag='mac' (default) returns just BASE
    without suffix, matching pre-addendum-2 records in the DB.
    Other tags get '@<tag>' appended: 'pc' → 'analysis-v1.3+qwen3-14b@pc'.
    """
    if not _WORKER_TAG_RE.match(worker_tag):
        raise ValueError(f"Invalid worker_tag: {worker_tag!r}")
    if worker_tag == "mac":
        return ANALYZER_VERSION_BASE
    return f"{ANALYZER_VERSION_BASE}@{worker_tag}"


__all__: list[str] = [
    "ANALYZER_VERSION",
    "ANALYZER_VERSION_BASE",
    "TOOLSHOP_LEGACY_VERSION",
    "make_analyzer_version",
]
