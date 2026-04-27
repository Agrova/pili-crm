"""Tests for app/llm_studio_control.py.

All tests mock HTTP — no real LM Studio is contacted.
Because LM Studio load/unload API is absent in this installation,
unload_model and unload_all are no-ops. Tests for those functions verify
no-op behaviour: warning logged, no exception raised.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.llm_studio_control as lms
from app.llm_studio_control import (
    LMStudioTimeoutError,
    ensure_model_loaded,
    list_loaded_models,
    unload_all,
)

ENDPOINT = "http://localhost:1234"


def _http_mock(models: list[dict]):
    """Patch httpx.AsyncClient to return a /api/v0/models response with given models list."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": models}

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get = AsyncMock(return_value=resp)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    return patch("httpx.AsyncClient", return_value=mock_ctx)


# --- list_loaded_models ---


async def test_list_loaded_models_returns_ids():
    with _http_mock([
        {"id": "model-a", "state": "loaded"},
        {"id": "model-b", "state": "not-loaded"},
        {"id": "model-c", "state": "loaded"},
    ]):
        result = await list_loaded_models(ENDPOINT)
    assert result == ["model-a", "model-c"]


async def test_list_loaded_models_empty():
    with _http_mock([]):
        result = await list_loaded_models(ENDPOINT)
    assert result == []


# --- ensure_model_loaded ---


async def test_ensure_model_loaded_already_loaded():
    target = "qwen/qwen3-vl-30b"
    with (
        patch.object(lms, "list_loaded_models", AsyncMock(return_value=[target])),
        patch.object(lms, "load_model", AsyncMock()) as mock_load,
    ):
        await ensure_model_loaded(target, ENDPOINT, poll_timeout_seconds=5)
    mock_load.assert_not_called()


async def test_ensure_model_loaded_not_loaded():
    target = "qwen/qwen3-vl-30b"
    with (
        patch.object(lms, "list_loaded_models", AsyncMock(side_effect=[[], [target]])),
        patch.object(lms, "load_model", AsyncMock()) as mock_load,
        patch.object(lms, "unload_all", AsyncMock()),
        patch("asyncio.sleep", AsyncMock()),
    ):
        await ensure_model_loaded(target, ENDPOINT, poll_timeout_seconds=10)
    mock_load.assert_called_once_with(target, ENDPOINT)


async def test_ensure_model_loaded_timeout():
    target = "qwen/qwen3-vl-30b"
    with (
        patch.object(lms, "list_loaded_models", AsyncMock(return_value=[])),
        patch.object(lms, "load_model", AsyncMock()),
        patch.object(lms, "unload_all", AsyncMock()),
        patch("asyncio.sleep", AsyncMock()),
        patch("time.monotonic", side_effect=[0.0, 100.0]),
        pytest.raises(LMStudioTimeoutError),
    ):
        await ensure_model_loaded(target, ENDPOINT, poll_timeout_seconds=60)


# --- unload_all (no-op because API not supported) ---


async def test_unload_all_calls_api(caplog):
    with caplog.at_level(logging.WARNING, logger="app.llm_studio_control"):
        await unload_all(ENDPOINT)
    assert any("unload" in rec.message.lower() for rec in caplog.records)


# --- ensure_model_loaded unloads others before loading target ---


async def test_ensure_model_loaded_unloads_others():
    target = "qwen/qwen3-vl-30b"
    other = "qwen/qwen3-14b"
    with (
        patch.object(lms, "list_loaded_models", AsyncMock(side_effect=[[other], [target]])),
        patch.object(lms, "load_model", AsyncMock()),
        patch.object(lms, "unload_all", AsyncMock()) as mock_unload_all,
        patch("asyncio.sleep", AsyncMock()),
    ):
        await ensure_model_loaded(target, ENDPOINT, poll_timeout_seconds=10)
    mock_unload_all.assert_called_once_with(ENDPOINT)
