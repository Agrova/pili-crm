"""Tool: fetch a message template by code and language."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "get_message_template"
DESCRIPTION = (
    "Возвращает шаблон сообщения клиенту по коду и языку. "
    "Read-only, подтверждения не требует. "
    "Используется артефактом-калькулятором для генерации текста заказа. "
    "Подстановку переменных делает вызывающая сторона."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "Код шаблона, например 'quote_to_client'.",
        },
        "language": {
            "type": "string",
            "default": "ru",
            "description": "Язык шаблона (ISO 639-1). По умолчанию 'ru'.",
        },
    },
    "required": ["code"],
}

_FETCH_SQL = text(
    """
    SELECT id, code, body_template, language, is_active, updated_at
    FROM communications_message_template
    WHERE code = :code
      AND language = :language
      AND is_active = TRUE
    LIMIT 1
    """
)


async def run(
    session: AsyncSession, code: str, language: str = "ru"
) -> dict[str, Any]:
    code = (code or "").strip()
    language = (language or "ru").strip()

    if not code:
        return {
            "found": False,
            "error": "template_not_found",
            "message": "Параметр code не может быть пустым.",
        }

    row = (
        await session.execute(_FETCH_SQL, {"code": code, "language": language})
    ).mappings().first()

    if row is None:
        return {
            "found": False,
            "error": "template_not_found",
            "message": (
                f"Шаблон '{code}' (язык: {language}) не найден "
                f"или неактивен."
            ),
        }

    return {
        "found": True,
        "code": row["code"],
        "language": row["language"],
        "body_template": row["body_template"],
        "updated_at": row["updated_at"].isoformat(),
    }


def format_text(result: dict[str, Any]) -> str:
    if not result.get("found"):
        return f"❌ {result.get('message', 'Шаблон не найден.')}"
    return (
        f"✅ Шаблон '{result['code']}' ({result['language']}), "
        f"обновлён: {result['updated_at']}\n\n"
        f"{result['body_template']}"
    )
