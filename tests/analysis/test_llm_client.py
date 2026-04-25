"""ADR-011 Task 3: tests for analysis/llm_client.py.

Five tests per TZ:

1. Successful call → returns text content.
2. Retry: first 2 attempts fail, 3rd succeeds → returns text.
3. Retry: all 3 attempts fail → raises ``LLMRequestError``.
4. Timeout (TimeoutException) is treated as a retryable error.
5. Auto-detect model via ``/v1/models``.

We use ``httpx.MockTransport`` to intercept HTTP without a live server.
``RETRY_BACKOFF_SECONDS`` is monkeypatched to (0, 0, 0) so the test
suite stays fast.
"""

from __future__ import annotations

import httpx
import pytest

from analysis import llm_client as llm_client_module
from analysis.llm_client import LLMRequestError, LMStudioClient


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero out retry sleeps so tests stay fast."""
    monkeypatch.setattr(llm_client_module, "RETRY_BACKOFF_SECONDS", (0.0, 0.0, 0.0))


def _models_response(*, model_id: str = "qwen3-14b") -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": [{"id": model_id, "object": "model"}]},
    )


def _completion_response(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text}}]
        },
    )


async def test_complete_returns_text_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return _models_response()
        if request.url.path.endswith("/chat/completions"):
            return _completion_response("ответ модели")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        result = await client.complete("привет")
    assert result == "ответ модели"


async def test_complete_retries_then_succeeds() -> None:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return _models_response()
        if request.url.path.endswith("/chat/completions"):
            state["calls"] += 1
            if state["calls"] <= 2:
                return httpx.Response(503)
            return _completion_response("ok")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        result = await client.complete("p")
    assert result == "ok"
    assert state["calls"] == 3


async def test_complete_raises_after_max_retries() -> None:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return _models_response()
        if request.url.path.endswith("/chat/completions"):
            state["calls"] += 1
            return httpx.Response(500)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        with pytest.raises(LLMRequestError) as excinfo:
            await client.complete("p")
    assert state["calls"] == 3
    assert excinfo.value.last_exception is not None


async def test_complete_treats_timeout_as_retryable() -> None:
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return _models_response()
        if request.url.path.endswith("/chat/completions"):
            state["calls"] += 1
            if state["calls"] == 1:
                raise httpx.ReadTimeout("timeout", request=request)
            return _completion_response("recovered")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        result = await client.complete("p")
    assert result == "recovered"
    assert state["calls"] == 2


async def test_detect_model_returns_first_available() -> None:
    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "qwen3-14b-instruct", "object": "model"},
                        {"id": "other", "object": "model"},
                    ]
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        first = await client.detect_model()
        # second call must be cached — no additional /models request
        second = await client.detect_model()
    assert first == second == "qwen3-14b-instruct"
    assert seen_paths.count("/v1/models") == 1
