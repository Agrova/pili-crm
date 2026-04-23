"""Parse Telegram Desktop JSON Export into typed dataclasses.

No database knowledge here — pure transformation of raw dicts into
ParsedChat / ParsedMessage / ParsedMediaMetadata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_FILE_NOT_INCLUDED_MARKER = "(File not included"

# Media-related keys captured verbatim into ParsedMediaMetadata.raw_fields.
_MEDIA_RAW_KEYS = (
    "file",
    "file_name",
    "file_size",
    "photo",
    "photo_file_size",
    "mime_type",
    "thumbnail",
    "thumbnail_file_size",
    "width",
    "height",
    "duration_seconds",
    "sticker_emoji",
)


def _extract_text(raw: Any) -> str | None:
    """Collapse the text field (str or list of entity dicts) to plain text.

    Returns None for empty / absent text.
    """
    if isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        text = "".join(parts)
    elif isinstance(raw, str):
        text = raw
    else:
        text = ""
    return text if text else None


def _resolve_path(value: str | None) -> str | None:
    """Return the path as-is, or None if the file was not downloaded during export."""
    if value is None or _FILE_NOT_INCLUDED_MARKER in value:
        return None
    return value


@dataclass(frozen=True)
class ParsedMediaMetadata:
    media_type: str  # 'photo' | 'file' | 'video_file' | 'voice_message' | ...
    file_name: str | None
    relative_path: str | None  # relative to export root; None = not downloaded
    file_size_bytes: int | None
    mime_type: str | None
    raw_fields: dict[str, Any]  # verbatim media-related fields from source message


@dataclass(frozen=True)
class ParsedMessage:
    telegram_message_id: str
    sent_at: datetime  # UTC, timezone-aware
    from_user_id: str | None  # stored as-is, e.g. "user5748681414"
    text: str | None
    reply_to_telegram_message_id: str | None
    media: ParsedMediaMetadata | None
    raw_payload: dict[str, Any]  # full source message dict


@dataclass(frozen=True)
class ParsedChat:
    telegram_chat_id: str
    title: str | None
    chat_type: str  # always 'personal_chat' after parse_export filtering
    messages: list[ParsedMessage]


def _parse_media(msg: dict[str, Any]) -> ParsedMediaMetadata | None:
    """Detect and parse media metadata from a message dict.

    Priority: explicit media_type field > photo field > file field (no media_type).
    If the file was not downloaded during export, relative_path is None but
    media_type is still set — signals "media existed but is unavailable locally".
    """
    raw_media_type: str | None = msg.get("media_type")

    if raw_media_type is not None:
        media_type = raw_media_type
        raw_path: str | None = msg.get("file")
        file_size_raw: Any = msg.get("file_size")
    elif "photo" in msg:
        media_type = "photo"
        raw_path = msg.get("photo")
        file_size_raw = msg.get("photo_file_size")
    elif "file" in msg:
        media_type = "file"
        raw_path = msg.get("file")
        file_size_raw = msg.get("file_size")
    else:
        return None

    relative_path = _resolve_path(raw_path)

    file_name: str | None = msg.get("file_name")
    if file_name is None and relative_path is not None:
        file_name = Path(relative_path).name

    raw_fields: dict[str, Any] = {k: msg[k] for k in _MEDIA_RAW_KEYS if k in msg}

    return ParsedMediaMetadata(
        media_type=media_type,
        file_name=file_name,
        relative_path=relative_path,
        file_size_bytes=int(file_size_raw) if file_size_raw is not None else None,
        mime_type=msg.get("mime_type"),
        raw_fields=raw_fields,
    )


def parse_message(msg_dict: dict[str, Any]) -> ParsedMessage | None:
    """Parse one message dict. Returns None for messages that should be skipped.

    Skipped: service messages; voice_message and sticker with no text.
    """
    if msg_dict.get("type") == "service":
        return None

    text = _extract_text(msg_dict.get("text", ""))
    media = _parse_media(msg_dict)

    if media is not None and media.media_type in ("voice_message", "sticker") and text is None:
        return None

    reply_raw = msg_dict.get("reply_to_message_id")
    reply_id: str | None = str(reply_raw) if reply_raw is not None else None

    return ParsedMessage(
        telegram_message_id=str(msg_dict["id"]),
        sent_at=datetime.fromtimestamp(int(msg_dict["date_unixtime"]), tz=UTC),
        from_user_id=msg_dict.get("from_id"),
        text=text,
        reply_to_telegram_message_id=reply_id,
        media=media,
        raw_payload=msg_dict,
    )


def parse_export(json_path: Path) -> list[ParsedChat]:
    """Read a Telegram Desktop result.json and return parsed personal chats only."""
    with json_path.open(encoding="utf-8") as fh:
        data: Any = json.load(fh)

    result: list[ParsedChat] = []
    for chat_dict in data["chats"]["list"]:
        if chat_dict.get("type") != "personal_chat":
            continue

        messages: list[ParsedMessage] = []
        for msg_dict in chat_dict.get("messages", []):
            parsed = parse_message(msg_dict)
            if parsed is not None:
                messages.append(parsed)

        result.append(
            ParsedChat(
                telegram_chat_id=str(chat_dict["id"]),
                title=chat_dict.get("name"),
                chat_type=chat_dict["type"],
                messages=messages,
            )
        )

    return result
