"""Tests for analysis/media_extract/loop_detector.py and vision loop integration.

Unit tests (1-8): test detect_repetition_loop in isolation.
Integration tests (9-11): test vision.extract_image() with mocked httpx
    to verify loop cases are correctly returned as VisionExtractionResult.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from PIL import Image

from analysis.media_extract.loop_detector import detect_repetition_loop
from analysis.media_extract.vision import VisionExtractionResult, extract_image

# ── helpers shared by integration tests ───────────────────────────────────────

MODEL_ID = "test-vision-model"
ENDPOINT = "http://localhost:1234/v1"


def _make_image_file(tmp_path: Path, width: int = 200, height: int = 200) -> Path:
    img = Image.new("RGB", (width, height), color=(100, 100, 100))
    path = tmp_path / "photo.jpg"
    img.save(path, "JPEG")
    return path


def _http_mock(response_content: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = response_content
    resp.json.return_value = {"choices": [{"message": {"content": response_content}}]}
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return patch("httpx.AsyncClient", return_value=mock_ctx)


# ── Unit test 1: no loop in normal text ───────────────────────────────────────


def test_no_loop_in_normal_text():
    words = [f"word{i}" for i in range(100)]
    text = " ".join(words)
    result = detect_repetition_loop(text)
    assert result.is_loop is False
    assert result.salvaged_prefix is None
    assert result.repeated_phrase is None
    assert result.repetition_count == 0


# ── Unit test 2: short text below threshold ────────────────────────────────────


def test_short_text_below_threshold():
    text = "один два три четыре пять"
    result = detect_repetition_loop(text)
    assert result.is_loop is False


# ── Unit test 3: loop with salvageable prefix ──────────────────────────────────


def test_loop_with_salvageable_prefix():
    # Prefix is "Это циркулярная пила." (21 chars) — above the 20-char threshold
    prefix = "Это циркулярная пила."
    repeating = "Видно корпус зелёного цвета."
    text = prefix + " " + (repeating + " ") * 6
    result = detect_repetition_loop(text)
    assert result.is_loop is True
    assert result.salvaged_prefix is not None
    assert len(result.salvaged_prefix) >= 20
    assert result.repeated_phrase is not None
    assert result.repeated_phrase.startswith("Видно")
    assert result.repetition_count >= 6


# ── Unit test 4: loop from start → no salvageable prefix ─────────────────────


def test_loop_from_start():
    # "Это товар. " * 10 — loop starts at position 0, no valid prefix
    text = "Это товар. " * 10
    result = detect_repetition_loop(text)
    assert result.is_loop is True
    assert result.salvaged_prefix is None
    assert result.repeated_phrase is not None
    assert result.repetition_count >= 5


# ── Unit test 5: loop with 8-word phrase ──────────────────────────────────────


def test_loop_with_8_word_phrase():
    phrase = "слово один два три четыре пять шесть семь"
    text = (phrase + " ") * 6
    result = detect_repetition_loop(text)
    assert result.is_loop is True
    assert result.repetition_count >= 6


# ── Unit test 6: threshold boundary ───────────────────────────────────────────


def test_loop_at_threshold_boundary():
    phrase = "alpha beta gamma"
    # exactly 5 repetitions → must be detected
    text_5 = (phrase + " ") * 5
    result_5 = detect_repetition_loop(text_5)
    assert result_5.is_loop is True

    # exactly 4 repetitions → must NOT be detected
    text_4 = (phrase + " ") * 4
    result_4 = detect_repetition_loop(text_4)
    assert result_4.is_loop is False


# ── Unit test 7: short prefix not salvaged ────────────────────────────────────


def test_short_prefix_not_salvaged():
    # Prefix "А" is only 1 char — below 20-char threshold
    prefix = "А"
    repeating = "слово раз слово два слово три"
    text = prefix + " " + (repeating + " ") * 6
    result = detect_repetition_loop(text)
    assert result.is_loop is True
    assert result.salvaged_prefix is None


# ── Unit test 8: unicode handling ─────────────────────────────────────────────


def test_unicode_handling():
    # Prefix is short (below 20), but loop must still be detected correctly
    prefix = "😀 тест"
    repeating = "Это повтор юникода строки."
    text = prefix + " " + (repeating + " ") * 6
    result = detect_repetition_loop(text)
    assert result.is_loop is True
    assert result.repeated_phrase is not None
    # split on whitespace must work cleanly with Cyrillic and emoji
    assert "Это" in result.repeated_phrase or "повтор" in result.repeated_phrase


# ── Integration test 9: normal response → extraction_method "vision" ──────────


async def test_vision_normal_response_writes_method_vision(tmp_path):
    img_path = _make_image_file(tmp_path)
    normal_response = (
        "Описание: Циркулярная пила Makita на белом фоне. "
        "Корпус зелёного цвета, диск 190 мм.\n"
        "Текст на изображении: Makita"
    )
    with _http_mock(normal_response):
        result = await extract_image(img_path, MODEL_ID, ENDPOINT)
    assert isinstance(result, VisionExtractionResult)
    assert result.extraction_method == "vision"
    assert result.text.startswith("[Изображение]\n")
    assert "[VISION_LOOP_DETECTED:" not in result.text


# ── Integration test 10: loop with prefix → vision-loop-salvaged ─────────────


async def test_vision_loop_with_prefix_writes_method_loop_salvaged(tmp_path, caplog):
    img_path = _make_image_file(tmp_path)
    prefix = "Это циркулярная пила."
    repeating = "Видно корпус зелёного цвета."
    loop_response = prefix + " " + (repeating + " ") * 6
    with (
        caplog.at_level(logging.WARNING, logger="analysis.media_extract.vision"),
        _http_mock(loop_response),
    ):
        result = await extract_image(img_path, MODEL_ID, ENDPOINT)
    assert isinstance(result, VisionExtractionResult)
    assert result.extraction_method == "vision-loop-salvaged"
    assert "[VISION_LOOP_DETECTED:" in result.text
    assert "сохранена начальная часть ответа" in result.text
    assert "Vision loop detected" in caplog.text


# ── Integration test 11: loop from start → vision-loop-discarded ─────────────


async def test_vision_loop_no_prefix_writes_method_loop_discarded(tmp_path, caplog):
    img_path = _make_image_file(tmp_path)
    loop_response = "Это товар. " * 10
    with (
        caplog.at_level(logging.WARNING, logger="analysis.media_extract.vision"),
        _http_mock(loop_response),
    ):
        result = await extract_image(img_path, MODEL_ID, ENDPOINT)
    assert isinstance(result, VisionExtractionResult)
    assert result.extraction_method == "vision-loop-discarded"
    assert "[VISION_LOOP_DETECTED:" in result.text
    assert "Описание изображения недоступно" in result.text
    assert "Vision loop detected" in caplog.text
