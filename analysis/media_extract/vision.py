"""ADR-014 Task 4: vision-based image description via LM Studio.

Sends images to a vision-LLM (Qwen3-VL via LM Studio OpenAI-compatible API)
and returns a structured description following ADR-014 §6.
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from pathlib import Path

import httpx

from analysis.media_extract.prompts import VISION_PROMPT

logger = logging.getLogger("analysis.media_extract.vision")


class VisionExtractError(Exception):
    """Базовая ошибка vision-обработчика."""


class VisionAPIError(VisionExtractError):
    """Ошибка HTTP-запроса к LM Studio."""


class VisionImageError(VisionExtractError):
    """Ошибка чтения или обработки изображения."""


def _prepare_image(path: Path, max_dimension: int) -> tuple[str, tuple[int, int], tuple[int, int]]:
    """Open, resize if needed, convert to JPEG, return base64 string and dimensions."""
    try:
        from PIL import Image, UnidentifiedImageError

        try:
            img = Image.open(path)
            img.load()
        except (UnidentifiedImageError, Exception) as exc:
            raise VisionImageError(f"Cannot open image '{path.name}': {exc}") from exc

        orig_size = (img.width, img.height)
        width, height = orig_size

        if max(width, height) > max_dimension:
            if width >= height:
                new_w = max_dimension
                new_h = int(height * max_dimension / width)
            else:
                new_h = max_dimension
                new_w = int(width * max_dimension / height)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        sent_size = (img.width, img.height)

        if img.mode != "RGB":
            img = img.convert("RGB")

        buf = BytesIO()
        img.save(buf, "JPEG", quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    except VisionImageError:
        raise
    except Exception as exc:
        raise VisionImageError(f"Image processing failed for '{path.name}': {exc}") from exc

    return b64, orig_size, sent_size


def _strip_fences(text: str) -> str:
    """Remove markdown code fences that LLMs sometimes add around their output."""
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return text


async def extract_image(
    path: Path,
    model_id: str,
    endpoint: str = "http://localhost:1234/v1",
    timeout_seconds: float = 120.0,
    max_dimension: int = 1568,
) -> str:
    """Извлекает текстовое описание изображения через vision-LLM.

    Raises:
        FileNotFoundError: файл не существует.
        VisionImageError: ошибка обработки изображения.
        VisionAPIError: ошибка HTTP / ответ модели не соответствует шаблону / timeout.
    """
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    b64, orig_size, sent_size = _prepare_image(path, max_dimension)

    logger.info(
        "Processing image %s (original: %dx%d, sent: %dx%d)",
        path.name,
        orig_size[0],
        orig_size[1],
        sent_size[0],
        sent_size[1],
    )

    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.1,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(f"{endpoint}/chat/completions", json=payload)
    except httpx.TimeoutException as exc:
        logger.error("Timeout calling vision API for %s: %s", path.name, exc)
        raise VisionAPIError(f"Timeout calling vision API for '{path.name}'") from exc
    except httpx.HTTPError as exc:
        logger.error("HTTP error calling vision API for %s: %s", path.name, exc)
        raise VisionAPIError(f"HTTP error calling vision API: {exc}") from exc

    if resp.status_code != 200:
        logger.error(
            "Vision API returned %d for %s: %s", resp.status_code, path.name, resp.text
        )
        raise VisionAPIError(
            f"Vision API returned HTTP {resp.status_code} for '{path.name}': {resp.text}"
        )

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        logger.error("Unexpected vision API response structure for %s: %s", path.name, exc)
        raise VisionAPIError(
            f"Unexpected vision API response structure for '{path.name}'"
        ) from exc

    cleaned = _strip_fences(content)

    if "Описание:" not in cleaned or "Текст на изображении:" not in cleaned:
        logger.warning(
            "Vision model response for %s does not match expected template", path.name
        )
        raise VisionAPIError(
            f"Vision model response for '{path.name}' does not contain required sections "
            "'Описание:' and 'Текст на изображении:'"
        )

    return "[Изображение]\n" + cleaned
