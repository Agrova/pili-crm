"""Tool: list Telegram chats awaiting operator review (ADR-010 Phase 3)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "get_unreviewed_chats"
DESCRIPTION = (
    "Возвращает очередь импортированных Telegram-чатов со статусом 'unreviewed' "
    "для разбора оператором. Для каждого чата: счётчик сообщений, даты первого "
    "и последнего сообщения, превью первого и последнего ТЕКСТОВЫХ сообщений "
    "(≤100 символов, медиа без текста пропускается). Сортировка: "
    "last_message_at DESC. Read-only, выполняется сразу без подтверждения."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "default": 50,
            "description": "Максимум чатов в ответе. По умолчанию 50.",
        },
    },
}

_SQL = text(
    """
    SELECT
        c.id                              AS chat_id,
        c.telegram_chat_id                AS telegram_chat_id,
        c.title                           AS title,
        COALESCE(stats.message_count, 0)  AS message_count,
        stats.first_message_at            AS first_message_at,
        stats.last_message_at             AS last_message_at,
        pf.preview_first                  AS preview_first,
        pl.preview_last                   AS preview_last
    FROM communications_telegram_chat c
    LEFT JOIN LATERAL (
        SELECT
            COUNT(*)::int  AS message_count,
            MIN(sent_at)   AS first_message_at,
            MAX(sent_at)   AS last_message_at
        FROM communications_telegram_message m
        WHERE m.chat_id = c.id
    ) stats ON TRUE
    LEFT JOIN LATERAL (
        SELECT LEFT(m.text, 100) AS preview_first
        FROM communications_telegram_message m
        WHERE m.chat_id = c.id
          AND m.text IS NOT NULL
          AND m.text <> ''
        ORDER BY m.sent_at ASC, m.id ASC
        LIMIT 1
    ) pf ON TRUE
    LEFT JOIN LATERAL (
        SELECT LEFT(m.text, 100) AS preview_last
        FROM communications_telegram_message m
        WHERE m.chat_id = c.id
          AND m.text IS NOT NULL
          AND m.text <> ''
        ORDER BY m.sent_at DESC, m.id DESC
        LIMIT 1
    ) pl ON TRUE
    WHERE c.review_status = 'unreviewed'
    ORDER BY stats.last_message_at DESC NULLS LAST, c.id DESC
    LIMIT :limit
    """
)


async def run(session: AsyncSession, limit: int = 50) -> dict[str, Any]:
    if limit is None or int(limit) < 1:
        return {"status": "error", "error": "limit должен быть ≥ 1", "chats": []}

    rows = (
        await session.execute(_SQL, {"limit": int(limit)})
    ).mappings().all()

    chats = [
        {
            "chat_id": int(r["chat_id"]),
            "telegram_chat_id": r["telegram_chat_id"],
            "title": r["title"],
            "message_count": int(r["message_count"] or 0),
            "first_message_at": r["first_message_at"],
            "last_message_at": r["last_message_at"],
            "preview_first": r["preview_first"],
            "preview_last": r["preview_last"],
        }
        for r in rows
    ]
    return {"status": "ok", "chats": chats}


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") == "error":
        return f"Ошибка: {result.get('error', 'неизвестная ошибка')}"
    chats = result.get("chats", [])
    if not chats:
        return "Очередь модерации пуста — нет чатов со статусом 'unreviewed'."
    lines = [f"Чатов к разбору: {len(chats)}"]
    for c in chats:
        title = c.get("title") or f"(без названия) tg={c['telegram_chat_id']}"
        last = c.get("last_message_at") or "—"
        preview = c.get("preview_last") or c.get("preview_first") or "(без текста)"
        lines.append(
            f"  • [id={c['chat_id']}] {title} — {c['message_count']} сообщ., "
            f"последнее {last} — «{preview}»"
        )
    return "\n".join(lines)
