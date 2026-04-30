"""Tool: update fields on an existing customer."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("crm-mcp.update_customer")

NAME = "update_customer"
DESCRIPTION = (
    "Обновляет данные существующего клиента: имя, телефон, Telegram, email. "
    "Передавай только изменяемые поля. Требует подтверждения оператора (write-tool)."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_id": {
            "type": "integer",
            "minimum": 1,
            "description": "id клиента из find_customer или list_customers.",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "description": "Новое полное имя клиента.",
        },
        "phone": {
            "type": "string",
            "description": "Новый телефон клиента.",
        },
        "telegram_id": {
            "type": "string",
            "description": "Новый @handle или display name в Telegram.",
        },
        "email": {
            "type": "string",
            "description": "Новый email-адрес.",
        },
    },
    "required": ["customer_id"],
}

_SELECT_CUSTOMER_SQL = text(
    "SELECT id, name, telegram_id, phone, email "
    "FROM orders_customer WHERE id = :cid"
)
_SELECT_TG_CONFLICT_SQL = text(
    "SELECT id FROM orders_customer "
    "WHERE telegram_id = :tg AND id != :cid LIMIT 1"
)
_SELECT_EMAIL_CONFLICT_SQL = text(
    "SELECT id FROM orders_customer "
    "WHERE email = :email AND id != :cid LIMIT 1"
)


async def run(
    session: AsyncSession,
    customer_id: int,
    name: str | None = None,
    phone: str | None = None,
    telegram_id: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    cid = int(customer_id)

    existing = (
        await session.execute(_SELECT_CUSTOMER_SQL, {"cid": cid})
    ).mappings().one_or_none()
    if existing is None:
        return {"status": "error", "error": "customer_not_found", "customer_id": cid}

    # Normalise: empty string → None
    name = (name or "").strip() or None
    phone = (phone or "").strip() or None
    telegram_id = (telegram_id or "").strip() or None
    email = (email or "").strip() or None

    updated_fields: list[str] = []
    params: dict[str, Any] = {"cid": cid}

    if name is not None:
        updated_fields.append("name")
        params["name"] = name
    if phone is not None:
        updated_fields.append("phone")
        params["phone"] = phone
    if telegram_id is not None:
        updated_fields.append("telegram_id")
        params["telegram_id"] = telegram_id
    if email is not None:
        updated_fields.append("email")
        params["email"] = email

    if not updated_fields:
        return {"status": "error", "error": "no_fields_to_update"}

    # Check telegram_id collision before hitting DB constraint
    if telegram_id is not None:
        conflict_row = (
            await session.execute(
                _SELECT_TG_CONFLICT_SQL, {"tg": telegram_id, "cid": cid}
            )
        ).scalar_one_or_none()
        if conflict_row is not None:
            return {
                "status": "error",
                "error": "telegram_id_conflict",
                "conflicting_customer_id": int(conflict_row),
            }

    set_clause = ", ".join(f"{f} = :{f}" for f in updated_fields)
    update_sql = text(
        f"UPDATE orders_customer SET {set_clause} WHERE id = :cid"  # noqa: S608
    )

    try:
        if "email" in updated_fields:
            try:
                async with session.begin_nested():
                    await session.execute(update_sql, params)
            except IntegrityError:
                conflicting: int | None = None
                try:
                    raw = (
                        await session.execute(
                            _SELECT_EMAIL_CONFLICT_SQL,
                            {"email": email, "cid": cid},
                        )
                    ).scalar_one_or_none()
                    conflicting = int(raw) if raw is not None else None
                except SQLAlchemyError:
                    logger.exception(
                        "email collision: conflicting_customer_id lookup failed "
                        "for customer_id=%s",
                        cid,
                    )
                return {
                    "status": "error",
                    "error": "email_unique_collision",
                    "conflicting_customer_id": conflicting,
                }
        else:
            await session.execute(update_sql, params)

        row = (
            await session.execute(_SELECT_CUSTOMER_SQL, {"cid": cid})
        ).mappings().one()
        await session.commit()

    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("update_customer failed for customer_id=%s", cid)
        return {
            "status": "error",
            "error": "db_error",
            "details": f"{type(exc).__name__}: {exc}",
        }

    return {
        "status": "ok",
        "customer_id": row["id"],
        "name": row["name"],
        "telegram_id": row["telegram_id"],
        "phone": row["phone"],
        "email": row["email"],
        "updated_fields": updated_fields,
    }


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        err = result.get("error", "неизвестная ошибка")
        if err == "customer_not_found":
            return f"Ошибка: клиент id={result.get('customer_id')} не найден."
        if err == "no_fields_to_update":
            return "Ошибка: не передано ни одного поля для обновления."
        if err == "telegram_id_conflict":
            return (
                f"Ошибка: telegram_id уже занят клиентом "
                f"id={result.get('conflicting_customer_id')}."
            )
        if err == "email_unique_collision":
            return (
                f"Ошибка: email уже занят клиентом "
                f"id={result.get('conflicting_customer_id')}."
            )
        return f"Ошибка: {err}"
    cid = result["customer_id"]
    fields = ", ".join(result.get("updated_fields", []))
    return f"✅ Клиент id={cid} обновлён: обновлены поля {fields}."
