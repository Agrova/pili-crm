"""ADR-011 Task 3: LM Studio HTTP client (OpenAI-compatible).

Synchronous-shape async client over ``httpx.AsyncClient``. Talks to
LM Studio (or any OpenAI-compatible endpoint) at
``http://localhost:1234/v1`` by default. Single ``complete(prompt)``
call returns the assistant text from
``POST /v1/chat/completions``.

Behavioural decisions (locked in by ADR-011 §12 + Phase-1 review):

- **Disable thinking** via two mechanisms (hotfix #4):
  ``chat_template_kwargs.enable_thinking=False`` (works on Mac MLX backend)
  plus ``\\n\\n/no_think`` suffix appended to every user-message (works on
  CUDA llama.cpp backend where the kwarg is ignored). Suffix is idempotent —
  not added when already present in the prompt.
- **Auto-detect model** via ``GET /v1/models`` on first call; first
  available model id is used. Cached for the client lifetime.
- **Retry**: 3 attempts, exponential backoff 1s / 4s / 16s. Final
  failure raises :class:`LLMRequestError` — caller (orchestrator)
  marks the chat as ``failed`` and proceeds.
- **Timeout** per HTTP call: 300s (configurable).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "http://localhost:1234/v1"
# Empirically calibrated after the first real ADR-011 Task 3 run on
# chat 5450: Qwen3-8b's reasoning + JSON-generation occasionally
# exceeds 120s on a long narrative. 300s gives ~2x headroom.
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (1.0, 4.0, 16.0)


def _inject_no_think(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append ``\\n\\n/no_think`` to every user-message (idempotent).

    Workaround: CUDA llama.cpp backend ignores
    ``chat_template_kwargs.enable_thinking=False`` (hotfix #4).
    """
    result: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content", "")
        if msg.get("role") == "user" and "/no_think" not in str(content):
            result.append({**msg, "content": str(content) + "\n\n/no_think"})
        else:
            result.append(msg)
    return result


class LLMRequestError(RuntimeError):
    """All ``max_retries`` attempts to LM Studio failed."""

    def __init__(self, message: str, *, last_exception: BaseException | None = None):
        super().__init__(message)
        self.last_exception = last_exception


class LMStudioClient:
    """Minimal async client for LM Studio's OpenAI-compatible endpoint."""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None
        self._model_id: str | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> LMStudioClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def detect_model(self) -> str:
        """Return the first model id from ``GET /v1/models``. Cached."""
        if self._model_id is not None:
            return self._model_id
        url = f"{self.endpoint}/models"
        resp = await self._client.get(url)
        resp.raise_for_status()
        data = resp.json()
        models = data.get("data", [])
        if not models:
            raise LLMRequestError(
                f"LM Studio returned no models at {url}; "
                "is a model loaded?"
            )
        first = models[0]
        model_id = first.get("id") if isinstance(first, dict) else None
        if not isinstance(model_id, str) or not model_id:
            raise LLMRequestError(
                f"LM Studio returned malformed model entry: {first!r}"
            )
        self._model_id = model_id
        return model_id

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Send a chat-completion request, retrying transient errors.

        Returns the assistant's textual reply (``choices[0].message.content``).
        """
        model_id = await self.detect_model()

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        messages = _inject_no_think(messages)

        payload: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if response_format is not None:
            payload["response_format"] = response_format

        url = f"{self.endpoint}/chat/completions"
        last_exc: BaseException | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await self._client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    raise LLMRequestError(
                        f"LM Studio returned no choices: {data!r}"
                    )
                content = choices[0].get("message", {}).get("content")
                if not isinstance(content, str):
                    raise LLMRequestError(
                        f"LM Studio returned non-string content: {content!r}"
                    )
                return content
            except (httpx.HTTPError, LLMRequestError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                backoff = RETRY_BACKOFF_SECONDS[
                    min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)
                ]
                logger.warning(
                    "LM Studio call failed (attempt %d/%d): %s — retry in %.1fs",
                    attempt,
                    self.max_retries,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
        raise LLMRequestError(
            f"LM Studio call failed after {self.max_retries} attempts: "
            f"{last_exc!r}",
            last_exception=last_exc,
        )


__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MAX_RETRIES",
    "RETRY_BACKOFF_SECONDS",
    "LLMRequestError",
    "LMStudioClient",
]
