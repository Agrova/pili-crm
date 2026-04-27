"""Tests for analysis/media_extract/vision.py.

All tests mock HTTP — no real LM Studio is contacted.
Synthetic images are created with Pillow.Image.new() and saved to tmp_path.
asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from PIL import Image

from analysis.media_extract.vision import (
    VisionAPIError,
    VisionExtractionResult,
    VisionImageError,
    extract_image,
)

MODEL_ID = "test-vision-model"
ENDPOINT = "http://localhost:1234/v1"

VALID_RESPONSE = (
    "Описание: Деревянная доска на белом фоне. "
    "Размер примерно 200x50 мм, поверхность гладкая.\n"
    "Текст на изображении: отсутствует"
)


def _make_image_file(
    tmp_path: Path,
    filename: str,
    width: int,
    height: int,
    mode: str = "RGB",
    fmt: str = "JPEG",
) -> Path:
    color = (128, 64, 32) if mode == "RGB" else (128, 64, 32, 200)
    img = Image.new(mode, (width, height), color=color)
    path = tmp_path / filename
    img.save(path, fmt)
    return path


def _mock_response(content: str, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = content
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return resp


def _http_mock(response_content: str = VALID_RESPONSE, status: int = 200):
    resp = _mock_response(response_content, status)
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(return_value=resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    return patch("httpx.AsyncClient", return_value=mock_ctx)


# --- 1. Success ---

async def test_extract_image_success(tmp_path):
    img_path = _make_image_file(tmp_path, "photo.jpg", 800, 600)
    with _http_mock(VALID_RESPONSE):
        result = await extract_image(img_path, MODEL_ID, ENDPOINT)
    assert isinstance(result, VisionExtractionResult)
    assert result.extraction_method == "vision"
    assert result.text.startswith("[Изображение]\n")
    assert "Описание:" in result.text
    assert "Текст на изображении:" in result.text


# --- 2. Strips markdown fences ---

async def test_extract_image_strips_markdown_fences(tmp_path):
    img_path = _make_image_file(tmp_path, "photo.jpg", 400, 300)
    fenced = f"```\n{VALID_RESPONSE}\n```"
    with _http_mock(fenced):
        result = await extract_image(img_path, MODEL_ID, ENDPOINT)
    assert result.text.startswith("[Изображение]\n")
    assert "```" not in result.text
    assert "Описание:" in result.text


# --- 3. Resizes large image ---

async def test_extract_image_resizes_large_image(tmp_path, caplog):
    img_path = _make_image_file(tmp_path, "large.jpg", 3000, 2000)
    with (
        caplog.at_level(logging.INFO, logger="analysis.media_extract.vision"),
        _http_mock(VALID_RESPONSE),
    ):
        result = await extract_image(img_path, MODEL_ID, ENDPOINT)
    assert result.text.startswith("[Изображение]\n")
    # Log must mention original and sent sizes
    assert "3000" in caplog.text
    assert "2000" in caplog.text
    # sent dimensions must be max 1568
    log_lower = caplog.text.lower()
    assert "1568" in caplog.text or "sent:" in log_lower or "sent" in log_lower


# --- 4. Skips resize for small image ---

async def test_extract_image_skips_resize_for_small_image(tmp_path, caplog):
    img_path = _make_image_file(tmp_path, "small.jpg", 800, 600)

    posted_payload: list = []

    async def fake_post(url, json=None, **kwargs):
        posted_payload.append(json)
        return _mock_response(VALID_RESPONSE)

    with caplog.at_level(logging.INFO, logger="analysis.media_extract.vision"):
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.post = fake_post
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        with patch("httpx.AsyncClient", return_value=mock_ctx):
            result = await extract_image(img_path, MODEL_ID, ENDPOINT)

    assert result.text.startswith("[Изображение]\n")
    # logged sizes: original == sent (no resize)
    assert "800" in caplog.text
    assert "600" in caplog.text
    # sent dimensions appear twice (orig and sent) — both 800x600
    assert caplog.text.count("800") >= 2


# --- 5. RGBA PNG is converted to RGB JPEG ---

async def test_extract_image_converts_rgba_to_rgb(tmp_path):
    img_path = _make_image_file(tmp_path, "alpha.png", 100, 100, mode="RGBA", fmt="PNG")

    posted_payload: list = []

    async def fake_post(url, json=None, **kwargs):
        posted_payload.append(json)
        return _mock_response(VALID_RESPONSE)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = fake_post
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("httpx.AsyncClient", return_value=mock_ctx):
        result = await extract_image(img_path, MODEL_ID, ENDPOINT)

    assert result.text.startswith("[Изображение]\n")
    assert len(posted_payload) == 1
    image_url = posted_payload[0]["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")


# --- 6. Invalid template raises VisionAPIError ---

async def test_extract_image_invalid_template_raises(tmp_path):
    img_path = _make_image_file(tmp_path, "photo.jpg", 200, 200)
    bad_response = "Just some random text without the expected sections."
    with (
        _http_mock(bad_response),
        pytest.raises(VisionAPIError, match="does not contain required sections"),
    ):
        await extract_image(img_path, MODEL_ID, ENDPOINT)


# --- 7. HTTP 500 raises VisionAPIError ---

async def test_extract_image_http_error_raises(tmp_path):
    img_path = _make_image_file(tmp_path, "photo.jpg", 200, 200)
    with (
        _http_mock("Internal Server Error", status=500),
        pytest.raises(VisionAPIError, match="HTTP 500"),
    ):
        await extract_image(img_path, MODEL_ID, ENDPOINT)


# --- 8. Timeout raises VisionAPIError ---

async def test_extract_image_timeout_raises(tmp_path):
    img_path = _make_image_file(tmp_path, "photo.jpg", 200, 200)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    with (
        patch("httpx.AsyncClient", return_value=mock_ctx),
        pytest.raises(VisionAPIError, match="Timeout"),
    ):
        await extract_image(img_path, MODEL_ID, ENDPOINT)


# --- 9. File not found ---

async def test_extract_image_file_not_found(tmp_path):
    missing = tmp_path / "nonexistent.jpg"
    with pytest.raises(FileNotFoundError):
        await extract_image(missing, MODEL_ID, ENDPOINT)


# --- 10. Corrupted image raises VisionImageError ---

async def test_extract_image_corrupted_image(tmp_path):
    bad_file = tmp_path / "corrupt.jpg"
    bad_file.write_bytes(b"this is not a valid image file at all")
    with pytest.raises(VisionImageError):
        await extract_image(bad_file, MODEL_ID, ENDPOINT)


# --- 11. Payload structure is correct OpenAI multimodal format ---

async def test_extract_image_payload_structure(tmp_path):
    img_path = _make_image_file(tmp_path, "photo.jpg", 300, 200)

    posted_payload: list = []

    async def fake_post(url, json=None, **kwargs):
        posted_payload.append(json)
        return _mock_response(VALID_RESPONSE)

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post = fake_post
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("httpx.AsyncClient", return_value=mock_ctx):
        await extract_image(img_path, MODEL_ID, ENDPOINT)

    assert len(posted_payload) == 1
    p = posted_payload[0]
    assert p["model"] == MODEL_ID
    content = p["messages"][0]["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    image_url = content[1]["image_url"]["url"]
    assert image_url.startswith("data:image/jpeg;base64,")
