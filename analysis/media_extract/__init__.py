"""ADR-014 media_extract sub-package.

Hosts the office parsers (``office.py``), vision client (``vision.py``),
prompts (``prompts.py``), service layer (``service.py``) and the CLI entry
point (``cli.py`` / ``__main__.py`` — invoke via
``python3 -m analysis.media_extract``).
"""

from __future__ import annotations

from pathlib import Path

MEDIA_EXTRACTOR_VERSION = "v1.1+qwen3-vl-8b"

TELEGRAM_EXPORTS_ROOT = Path.home() / "pili-crm-data" / "tg-exports"
