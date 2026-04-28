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

from collections.abc import Callable

import httpx
import pytest

from analysis import llm_client as llm_client_module
from analysis.llm_client import LLMRequestError, LMStudioClient, _inject_no_think


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


async def test_default_timeout_is_300_seconds() -> None:
    """Hotfix: timeout raised 120 → 300s → 900s after extended ADR-011 runs."""
    client = LMStudioClient()
    assert client.timeout == 900.0
    await client.aclose()


def _capturing_handler(
    captured: list[dict[str, object]],
) -> Callable[[httpx.Request], httpx.Response]:
    """Mock transport that records the JSON body of /chat/completions."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return _models_response()
        if request.url.path.endswith("/chat/completions"):
            import json as _json

            captured.append(_json.loads(request.content))
            return _completion_response("ok")
        return httpx.Response(404)

    return handler


async def test_payload_always_disables_thinking_for_plain_call() -> None:
    """chat_template_kwargs.enable_thinking=False applied for chunk_summary
    style (plain) calls."""
    captured: list[dict[str, object]] = []
    transport = httpx.MockTransport(_capturing_handler(captured))
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        await client.complete("chunk-summary prompt")
    assert len(captured) == 1
    assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "response_format" not in captured[0]


async def test_payload_disables_thinking_for_master_summary() -> None:
    """master_summary goes through the same complete() path — separate
    assertion to guard against regressions if master ever splits off."""
    captured: list[dict[str, object]] = []
    transport = httpx.MockTransport(_capturing_handler(captured))
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        await client.complete("master summary of N chunk summaries")
    assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "response_format" not in captured[0]


async def test_payload_disables_thinking_for_narrative() -> None:
    captured: list[dict[str, object]] = []
    transport = httpx.MockTransport(_capturing_handler(captured))
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        await client.complete("narrative prompt")
    assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "response_format" not in captured[0]


async def test_payload_with_response_format_for_structured_extract() -> None:
    """response_format propagates verbatim and thinking stays disabled."""
    from app.analysis.schemas import StructuredExtract

    rf: dict[str, object] = {
        "type": "json_schema",
        "json_schema": {
            "name": "structured_extract",
            "schema": StructuredExtract.model_json_schema(),
            "strict": True,
        },
    }
    captured: list[dict[str, object]] = []
    transport = httpx.MockTransport(_capturing_handler(captured))
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        await client.complete("extract prompt", response_format=rf)
    assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured[0]["response_format"] == rf
    js = captured[0]["response_format"]["json_schema"]
    assert js["name"] == "structured_extract"
    assert js["strict"] is True
    assert js["schema"] == StructuredExtract.model_json_schema()


async def test_payload_with_response_format_for_matching() -> None:
    from analysis.matching import MATCHING_RESPONSE_FORMAT

    captured: list[dict[str, object]] = []
    transport = httpx.MockTransport(_capturing_handler(captured))
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        await client.complete("matching prompt", response_format=MATCHING_RESPONSE_FORMAT)
    assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}
    rf = captured[0]["response_format"]
    assert isinstance(rf, dict)
    assert rf["type"] == "json_schema"
    schema = rf["json_schema"]["schema"]
    props = schema["properties"]
    assert set(props.keys()) == {"decision", "product_id", "candidate_ids", "note"}
    assert props["decision"]["enum"] == [
        "confident_match",
        "ambiguous",
        "not_found",
    ]
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["decision", "note"]


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


# ---------------------------------------------------------------------------
# Hotfix #4: /no_think suffix — отключение Qwen3 reasoning на CUDA backend
# ---------------------------------------------------------------------------


async def test_user_messages_get_no_think_suffix() -> None:
    """Workaround for CUDA backend ignoring chat_template_kwargs.enable_thinking."""
    captured: list[dict[str, object]] = []
    transport = httpx.MockTransport(_capturing_handler(captured))
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        await client.complete("test prompt")
    user_msg = captured[0]["messages"][0]  # type: ignore[index]
    assert user_msg["content"] == "test prompt\n\n/no_think"
    assert user_msg["content"].endswith("/no_think")


async def test_user_messages_already_with_no_think_not_duplicated() -> None:
    """Idempotency: existing /no_think suffix is not duplicated."""
    captured: list[dict[str, object]] = []
    transport = httpx.MockTransport(_capturing_handler(captured))
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        await client.complete("test prompt /no_think")
    user_msg = captured[0]["messages"][0]  # type: ignore[index]
    assert user_msg["content"].count("/no_think") == 1


def test_non_user_messages_not_modified() -> None:
    """Suffix is added only to user messages, not system or assistant."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "a"},
    ]
    result = _inject_no_think(messages)
    assert result[0]["content"] == "sys"
    assert result[1]["content"] == "u\n\n/no_think"
    assert result[2]["content"] == "a"


async def test_chat_template_kwargs_preserved() -> None:
    """Existing enable_thinking=False parameter is preserved (Mac MLX still uses it)."""
    captured: list[dict[str, object]] = []
    transport = httpx.MockTransport(_capturing_handler(captured))
    async with httpx.AsyncClient(transport=transport) as http:
        client = LMStudioClient(client=http)
        await client.complete("any prompt")
    assert captured[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured[0]["messages"][-1]["content"].endswith("/no_think")  # type: ignore[index]
