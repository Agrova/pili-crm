"""ADR-011 X1 iter 4: unit tests for ``analysis/identity_extract.py``.

Mock-LM-Studio tests — no real HTTP calls. Coverage:

- happy path returns full Identity (4 SDEK fields populated);
- empty-output Identity (all-null);
- third-party-name pass-through (the prompt does the filtering, the
  pipeline must not mutate the LLM's verdict);
- graceful degrade on LLM error → all-null Identity + WARNING in
  ``confidence_notes`` + ``logger.exception`` traceback;
- graceful degrade on JSON validation error;
- operator-message length filter drops long product explanations and
  keeps short addressings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from analysis.chunking import ChatMessage
from analysis.identity_extract import (
    OPERATOR_MAX_LEN_CHARS,
    _filter_messages_for_identity,
    extract_identity_from_chunks,
)
from analysis.llm_client import LLMRequestError
from app.config import OPERATOR_TELEGRAM_USER_IDS

# Pick a known operator id from settings — _filter_messages_for_identity
# treats this as ``[операт.]``.
_OPERATOR_ID: str = next(iter(OPERATOR_TELEGRAM_USER_IDS))
_CLIENT_ID: str = "999_definitely_not_operator"


@dataclass
class _FakeLLM:
    """Stub conforming to ``LMStudioClient.complete`` shape."""

    responses: list[str] = field(default_factory=list)
    raises: list[BaseException] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        response_format: dict[str, object] | None = None,
    ) -> str:
        self.prompts.append(prompt)
        if self.raises:
            raise self.raises.pop(0)
        if not self.responses:
            raise AssertionError("FakeLLM ran out of canned responses")
        return self.responses.pop(0)


def _msg(tmid: str, who: str, text: str) -> ChatMessage:
    return ChatMessage(
        telegram_message_id=tmid,
        sent_at=datetime(2025, 3, 15, 12, 0, tzinfo=UTC),
        from_user_id=who,
        text=text,
    )


# ── happy path ──────────────────────────────────────────────────────────────


async def test_extract_identity_returns_full_identity() -> None:
    """SDEK-style delivery message → 4 populated identity fields."""
    chunks = [[
        _msg("100", _CLIENT_ID, "Здравствуйте, рубанок есть?"),
        _msg("101", _OPERATOR_ID, "Добрый день, Анна. Есть, 45000."),
        _msg(
            "102",
            _CLIENT_ID,
            "Беру. Сдэк: ул. Тверская 1, кв 5. Иванова Анна Петровна +79161234567",
        ),
    ]]
    llm = _FakeLLM(responses=[
        '{"name_guess": "Иванова Анна Петровна", '
        '"telegram_username": null, '
        '"phone": "+79161234567", "email": null, "city": null, '
        '"address": "ул. Тверская 1, кв 5", '
        '"delivery_method": "СДЭК", '
        '"confidence_notes": "из подписи клиента в строке доставки"}'
    ])

    identity = await extract_identity_from_chunks(chunks, llm)  # type: ignore[arg-type]

    assert identity.name_guess == "Иванова Анна Петровна"
    assert identity.phone == "+79161234567"
    assert identity.address == "ул. Тверская 1, кв 5"
    assert identity.delivery_method == "СДЭК"
    assert identity.confidence_notes is not None
    assert len(llm.prompts) == 1


async def test_extract_identity_handles_no_identity() -> None:
    """LLM returns all-null → Identity stays empty (no exception)."""
    chunks = [[_msg("1", _CLIENT_ID, "общий вопрос про каталог")]]
    llm = _FakeLLM(responses=[
        '{"name_guess": null, "telegram_username": null, '
        '"phone": null, "email": null, "city": null, "address": null, '
        '"delivery_method": null, "confidence_notes": null}'
    ])

    identity = await extract_identity_from_chunks(chunks, llm)  # type: ignore[arg-type]

    assert identity.name_guess is None
    assert identity.phone is None
    assert identity.address is None
    assert identity.delivery_method is None
    assert identity.confidence_notes is None


async def test_extract_identity_passes_through_llm_third_party_filter() -> None:
    """Pipeline must not mutate LLM verdict — the prompt does the filtering."""
    chunks = [[
        _msg("1", _CLIENT_ID, "Передай Фёдору Алексеевичу что я возьму."),
        _msg("2", _CLIENT_ID, "Меня зовут Анна, я заказчик."),
    ]]
    llm = _FakeLLM(responses=[
        '{"name_guess": "Анна", "telegram_username": null, '
        '"phone": null, "email": null, "city": null, "address": null, '
        '"delivery_method": null, '
        '"confidence_notes": "само-представление клиента"}'
    ])

    identity = await extract_identity_from_chunks(chunks, llm)  # type: ignore[arg-type]

    # No mention of Фёдор Алексеевич in the result — and the pipeline
    # didn't touch the LLM verdict either way.
    assert identity.name_guess == "Анна"


# ── graceful degrade ────────────────────────────────────────────────────────


async def test_extract_identity_graceful_degrade_on_llm_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    chunks = [[_msg("1", _CLIENT_ID, "test")]]
    llm = _FakeLLM(raises=[LLMRequestError("LM Studio down after 3 attempts")])

    with caplog.at_level(logging.ERROR, logger="analysis.identity_extract"):
        identity = await extract_identity_from_chunks(chunks, llm)  # type: ignore[arg-type]

    assert identity.name_guess is None
    assert identity.phone is None
    assert identity.address is None
    assert identity.delivery_method is None
    assert identity.confidence_notes is not None
    assert identity.confidence_notes.startswith(
        "WARNING: identity extraction failed:"
    )
    assert "LLMRequestError" in identity.confidence_notes
    # logger.exception was called → ERROR record with traceback
    assert any(
        rec.levelno >= logging.ERROR
        and "identity extraction failed" in rec.message
        for rec in caplog.records
    )


async def test_extract_identity_graceful_degrade_on_validation_error() -> None:
    """Malformed JSON triggers graceful degrade, not a crash."""
    chunks = [[_msg("1", _CLIENT_ID, "test")]]
    llm = _FakeLLM(responses=["not a json at all, qwen acted up"])

    identity = await extract_identity_from_chunks(chunks, llm)  # type: ignore[arg-type]

    assert identity.name_guess is None
    assert identity.confidence_notes is not None
    assert identity.confidence_notes.startswith(
        "WARNING: identity extraction failed:"
    )


# ── operator message length filter ──────────────────────────────────────────


def test_filter_drops_long_operator_messages_keeps_short_ones() -> None:
    """Long operator product explanations dropped; short addressings kept."""
    long_op_text = "Подробное описание рубанка Veritas: " + ("X" * 250)
    assert len(long_op_text) >= OPERATOR_MAX_LEN_CHARS
    short_op_text = "Добрый день, Анна!"
    assert len(short_op_text) < OPERATOR_MAX_LEN_CHARS

    chunks = [[
        _msg("1", _CLIENT_ID, "клиентское сообщение"),
        _msg("2", _OPERATOR_ID, long_op_text),
        _msg("3", _OPERATOR_ID, short_op_text),
        _msg("4", _CLIENT_ID, "ещё клиентское"),
    ]]

    filtered = _filter_messages_for_identity(chunks)
    tmids = [m.telegram_message_id for m in filtered]

    assert "1" in tmids and "4" in tmids  # client untouched
    assert "3" in tmids  # short operator kept
    assert "2" not in tmids  # long operator dropped


async def test_extract_identity_skips_when_filter_empties_chunks() -> None:
    """If filter removes everything, return empty Identity with WARNING."""
    long_op_text = "X" * (OPERATOR_MAX_LEN_CHARS + 1)
    chunks = [[_msg("1", _OPERATOR_ID, long_op_text)]]
    llm = _FakeLLM()  # No responses queued — must not be called.

    identity = await extract_identity_from_chunks(chunks, llm)  # type: ignore[arg-type]

    assert identity.confidence_notes is not None
    assert "WARNING" in identity.confidence_notes
    assert llm.prompts == []


# ── operator-name blocklist (v1.4) ──────────────────────────────────────────


async def test_blocklist_rejects_operator_first_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LLM emits name_guess='Рома' (client addressed operator) → rejected."""
    chunks = [[_msg("1", _CLIENT_ID, "Рома, привет! Рубанок привезли?")]]
    llm = _FakeLLM(responses=[
        '{"name_guess": "Рома", "telegram_username": null, '
        '"phone": null, "email": null, "city": null, "address": null, '
        '"delivery_method": null, '
        '"confidence_notes": "имя из обращения оператора"}'
    ])

    with caplog.at_level(logging.WARNING, logger="analysis.identity_extract"):
        result = await extract_identity_from_chunks(chunks, llm)  # type: ignore[arg-type]

    assert result.name_guess is None
    assert result.confidence_notes is not None
    assert "REJECTED operator name" in result.confidence_notes
    assert "'Рома'" in result.confidence_notes
    assert any(
        "matches operator name variant" in rec.message
        for rec in caplog.records
        if rec.levelno >= logging.WARNING
    )


async def test_blocklist_rejects_compound_name_with_operator_token() -> None:
    """Compound name_guess with operator token → fully rejected (no partial save)."""
    chunks = [[_msg("1", _CLIENT_ID, "Рома Татарков здесь")]]
    llm = _FakeLLM(responses=[
        '{"name_guess": "Рома Татарков", "telegram_username": null, '
        '"phone": null, "email": null, "city": null, "address": null, '
        '"delivery_method": null, "confidence_notes": null}'
    ])

    result = await extract_identity_from_chunks(chunks, llm)  # type: ignore[arg-type]

    assert result.name_guess is None
    assert result.confidence_notes is not None
    assert "REJECTED operator name" in result.confidence_notes
    assert "'Рома Татарков'" in result.confidence_notes


async def test_blocklist_preserves_legitimate_client_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legitimate client name_guess → untouched by blocklist."""
    chunks = [[_msg("1", _CLIENT_ID, "Меня зовут Иван Петров, заказчик")]]
    llm = _FakeLLM(responses=[
        '{"name_guess": "Иван Петров", "telegram_username": null, '
        '"phone": null, "email": null, "city": null, "address": null, '
        '"delivery_method": null, "confidence_notes": "само-представление"}'
    ])

    with caplog.at_level(logging.WARNING, logger="analysis.identity_extract"):
        result = await extract_identity_from_chunks(chunks, llm)  # type: ignore[arg-type]

    assert result.name_guess == "Иван Петров"
    assert "REJECTED" not in (result.confidence_notes or "")
    assert not any(
        "matches operator name variant" in rec.message
        for rec in caplog.records
    )
