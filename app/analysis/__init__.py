"""ADR-011: Telegram chat analysis pipeline.

Ninth application module of the monolith. Hosts the schema and Pydantic models
backing the LLM-driven analysis of Telegram conversations (Qwen3-14B via
LM Studio). Business logic (repository, service, runner, MCP tools) arrives in
Tasks 2–6 of ADR-011.

ANALYZER_VERSION is bumped whenever the prompts or the structured_extract
schema change; old analysis_chat_analysis rows stay in place as history.
"""

from __future__ import annotations

ANALYZER_VERSION: str = "v1.0+qwen3-14b"

__all__: list[str] = []
