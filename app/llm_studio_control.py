"""LM Studio REST API client for managing loaded models.

LM Studio /api/v0/models/{id}/load and /api/v0/models/{id}/unload are NOT
available in this installation — both return "Unexpected endpoint or method".
Therefore load_model, unload_model, and unload_all are implemented as no-ops
that emit a warning. ensure_model_loaded polls for the model to appear but
cannot trigger loading programmatically; the model must be loaded via the
LM Studio UI or CLI before calling this function.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger("app.llm_studio_control")


class LMStudioError(Exception): ...


class LMStudioTimeoutError(LMStudioError): ...


class LMStudioAPIError(LMStudioError): ...


async def list_loaded_models(endpoint: str = settings.LM_STUDIO_API_BASE) -> list[str]:
    """Return list of IDs of currently loaded models."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{endpoint}/api/v0/models")
    if resp.status_code != 200:
        raise LMStudioAPIError(
            f"GET /api/v0/models returned {resp.status_code}: {resp.text}"
        )
    data = resp.json()
    return [m["id"] for m in data.get("data", []) if m.get("state") == "loaded"]


async def load_model(model_id: str, endpoint: str = settings.LM_STUDIO_API_BASE) -> None:
    """Load model. LM Studio load API not available in this installation — no-op with warning."""
    logger.warning(
        "load_model: LM Studio load API not supported; cannot programmatically load %r. "
        "Load the model manually in LM Studio.",
        model_id,
    )


async def unload_model(model_id: str, endpoint: str = settings.LM_STUDIO_API_BASE) -> None:
    """Unload model. LM Studio unload API not available in this installation — no-op."""
    logger.warning(
        "unload_model: LM Studio unload API not supported; cannot programmatically unload %r.",
        model_id,
    )


async def unload_all(endpoint: str = settings.LM_STUDIO_API_BASE) -> None:
    """Unload all models. LM Studio unload API not available in this installation — no-op."""
    logger.warning(
        "unload_all: LM Studio unload API not supported; cannot programmatically unload models."
    )


async def ensure_model_loaded(
    model_id: str,
    endpoint: str = settings.LM_STUDIO_API_BASE,
    poll_timeout_seconds: int = 60,
    poll_interval_seconds: float = 2.0,
) -> None:
    """Ensure model_id is loaded and ready.

    Algorithm:
    1. list_loaded_models() — if model_id already present, return.
    2. If other models loaded and unload supported — unload_all().
       (unload is no-op in this installation)
    3. load_model(model_id). (no-op in this installation)
    4. Poll every poll_interval_seconds until model_id appears
       or poll_timeout_seconds elapses.
    5. On timeout — raise LMStudioTimeoutError.
    """
    loaded = await list_loaded_models(endpoint)
    if model_id in loaded:
        return

    if loaded:
        await unload_all(endpoint)

    await load_model(model_id, endpoint)

    deadline = time.monotonic() + poll_timeout_seconds
    while True:
        await asyncio.sleep(poll_interval_seconds)
        loaded = await list_loaded_models(endpoint)
        if model_id in loaded:
            return
        if time.monotonic() >= deadline:
            raise LMStudioTimeoutError(
                f"Model {model_id!r} not loaded after {poll_timeout_seconds}s"
            )
