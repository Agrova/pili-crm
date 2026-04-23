"""Tool: link an imported Telegram chat to a customer (ADR-010 Phase 3).

Write-tool. Requires operator confirmation (two-confirmations rule, Cowork
system prompt section 7). All DB changes — chat status, communications_link
rows, optional customer creation, optional telegram_id backfill — happen
atomically in a single transaction.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("crm-mcp.link_chat_to_customer")

NAME = "link_chat_to_customer"
DESCRIPTION = (
    "Привязывает импортированный Telegram-чат к клиенту в одном из трёх режимов: "
    "(1) customer_id — к существующему клиенту; "
    "(2) create_new=true — создать нового клиента из данных чата; "
    "(3) ignore=true — пометить чат как 'ignored'. "
    "Ровно ОДИН режим за вызов. Атомарная транзакция. Требует подтверждения оператора."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chat_id": {
            "type": "integer",
            "minimum": 1,
            "description": "id в communications_telegram_chat.",
        },
        "customer_id": {
            "type": "integer",
            "minimum": 1,
            "description": "id существующего клиента (режим linked).",
        },
        "create_new": {
            "type": "boolean",
            "default": False,
            "description": "Создать нового клиента по данным чата.",
        },
        "ignore": {
            "type": "boolean",
            "default": False,
            "description": "Пометить чат как 'ignored' без привязки.",
        },
    },
    "required": ["chat_id"],
}


_SELECT_CHAT_SQL = text(
    """
    SELECT
        id,
        telegram_chat_id,
        title,
        review_status::text AS review_status
    FROM communications_telegram_chat
    WHERE id = :chat_id
    """
)

_SELECT_CUSTOMER_SQL = text(
    """
    SELECT id, name, telegram_id
    FROM orders_customer
    WHERE id = :customer_id
    """
)

_SELECT_TG_COLLISION_SQL = text(
    """
    SELECT id
    FROM orders_customer
    WHERE telegram_id = :tg AND id <> :excl
    LIMIT 1
    """
)

_UPDATE_CHAT_STATUS_SQL = text(
    """
    UPDATE communications_telegram_chat
       SET review_status = CAST(:status AS telegram_chat_review_status),
           updated_at    = NOW()
     WHERE id = :chat_id
    """
)

_UPDATE_CUSTOMER_TG_SQL = text(
    """
    UPDATE orders_customer
       SET telegram_id = :tg,
           updated_at  = NOW()
     WHERE id = :customer_id
    """
)

_INSERT_NEW_CUSTOMER_SQL = text(
    """
    INSERT INTO orders_customer (name, telegram_id)
    VALUES (:name, :tg)
    RETURNING id, name
    """
)

_BULK_INSERT_LINKS_SQL = text(
    """
    INSERT INTO communications_link
        (telegram_message_id, target_module, target_entity, target_id, link_confidence)
    SELECT m.id, 'orders', 'orders_customer', :customer_id, 'manual'
      FROM communications_telegram_message m
     WHERE m.chat_id = :chat_id
    """
)


def _validate_modes(
    customer_id: int | None, create_new: bool, ignore: bool
) -> str:
    modes_set = (
        int(customer_id is not None) + int(bool(create_new)) + int(bool(ignore))
    )
    if modes_set != 1:
        raise ValueError(
            "Ровно один из customer_id, create_new, ignore должен быть задан. "
            f"Задано: {modes_set}."
        )
    if customer_id is not None:
        return "linked"
    if create_new:
        return "new_customer"
    return "ignored"


async def run(
    session: AsyncSession,
    chat_id: int,
    customer_id: int | None = None,
    create_new: bool = False,
    ignore: bool = False,
) -> dict[str, Any]:
    # ── Pre-validation (raises before touching the DB) ──────────────────────
    mode = _validate_modes(customer_id, create_new, ignore)

    chat_row = (
        await session.execute(_SELECT_CHAT_SQL, {"chat_id": int(chat_id)})
    ).mappings().first()
    if chat_row is None:
        raise ValueError(f"Чат id={chat_id} не найден.")

    current_status = chat_row["review_status"]
    if current_status is not None and current_status != "unreviewed":
        raise ValueError(
            f"Чат id={chat_id} уже обработан (review_status={current_status!r}). "
            "Повторная обработка не допускается."
        )

    tg_chat_id: str = chat_row["telegram_chat_id"]
    chat_title: str | None = chat_row["title"]

    target_customer: dict[str, Any] | None = None
    if mode == "linked":
        target_customer = dict(
            (
                await session.execute(
                    _SELECT_CUSTOMER_SQL, {"customer_id": int(customer_id)}  # type: ignore[arg-type]
                )
            ).mappings().first()
            or {}
        )
        if not target_customer:
            raise ValueError(f"Клиент id={customer_id} не найден.")

    # ── Write phase (single transaction, caller session) ────────────────────
    try:
        telegram_id_updated = False
        telegram_id_conflict: dict[str, Any] | None = None
        messages_linked = 0

        if mode == "linked":
            assert target_customer is not None
            cust_id = int(target_customer["id"])
            cust_name = str(target_customer["name"])
            cust_tg = target_customer.get("telegram_id")

            await session.execute(
                _UPDATE_CHAT_STATUS_SQL,
                {"status": "linked", "chat_id": int(chat_id)},
            )
            res = await session.execute(
                _BULK_INSERT_LINKS_SQL,
                {"customer_id": cust_id, "chat_id": int(chat_id)},
            )
            messages_linked = int(getattr(res, "rowcount", 0) or 0)

            if cust_tg is None:
                collision = (
                    await session.execute(
                        _SELECT_TG_COLLISION_SQL,
                        {"tg": tg_chat_id, "excl": cust_id},
                    )
                ).scalar_one_or_none()
                if collision is None:
                    await session.execute(
                        _UPDATE_CUSTOMER_TG_SQL,
                        {"tg": tg_chat_id, "customer_id": cust_id},
                    )
                    telegram_id_updated = True
                else:
                    logger.warning(
                        "telegram_id=%s уже занят клиентом id=%s — "
                        "не перезаписываю у клиента id=%s; привязка чата сохранена.",
                        tg_chat_id, collision, cust_id,
                    )
                    telegram_id_conflict = {
                        "conflicting_customer_id": int(collision),
                        "conflicting_telegram_id": tg_chat_id,
                    }
            elif cust_tg != tg_chat_id:
                logger.warning(
                    "Mismatch: customer.telegram_id=%r, chat.telegram_chat_id=%r — "
                    "не перезаписываю.",
                    cust_tg, tg_chat_id,
                )

            action, result_cust_id, result_cust_name = "linked", cust_id, cust_name

        elif mode == "new_customer":
            title_clean = (chat_title or "").strip()
            new_name = title_clean if title_clean else f"Telegram user {tg_chat_id}"

            # Update chat first, then INSERT customer. If the INSERT trips
            # uq_orders_customer_telegram_id (telegram_id already taken), the
            # rollback reverses the status flip and no partial state leaks.
            await session.execute(
                _UPDATE_CHAT_STATUS_SQL,
                {"status": "new_customer", "chat_id": int(chat_id)},
            )

            new_row = (
                await session.execute(
                    _INSERT_NEW_CUSTOMER_SQL,
                    {"name": new_name, "tg": tg_chat_id},
                )
            ).mappings().one()

            new_cust_id = int(new_row["id"])
            res = await session.execute(
                _BULK_INSERT_LINKS_SQL,
                {"customer_id": new_cust_id, "chat_id": int(chat_id)},
            )
            messages_linked = int(getattr(res, "rowcount", 0) or 0)
            telegram_id_updated = True
            action, result_cust_id, result_cust_name = (
                "new_customer", new_cust_id, str(new_row["name"])
            )

        else:  # mode == "ignored"
            await session.execute(
                _UPDATE_CHAT_STATUS_SQL,
                {"status": "ignored", "chat_id": int(chat_id)},
            )
            action, result_cust_id, result_cust_name = "ignored", None, None

        await session.commit()

    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("link_chat_to_customer failed for chat_id=%s", chat_id)
        return {
            "status": "error",
            "error": f"БД отказалась выполнить операцию: {type(exc).__name__}: {exc}",
            "chat_id": int(chat_id),
        }

    return {
        "status": "ok",
        "chat_id": int(chat_id),
        "action": action,
        "customer_id": result_cust_id,
        "customer_name": result_cust_name,
        "messages_linked": messages_linked,
        "telegram_id_updated": telegram_id_updated,
        "telegram_id_conflict": telegram_id_conflict,
    }


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        return f"Ошибка: {result.get('error', 'неизвестная ошибка')}"

    action = result.get("action")
    chat_id = result.get("chat_id")
    linked = result.get("messages_linked", 0)

    if action == "ignored":
        return f"✅ Чат id={chat_id} помечен как ignored."

    cust_id = result.get("customer_id")
    cust_name = result.get("customer_name")
    tg_updated = result.get("telegram_id_updated")
    conflict = result.get("telegram_id_conflict")

    head = (
        f"✅ Чат id={chat_id} → "
        f"{'создан новый клиент' if action == 'new_customer' else 'привязан к клиенту'} "
        f"{cust_name} (id={cust_id}). Сообщений связано: {linked}."
    )
    extras: list[str] = []
    if tg_updated:
        extras.append("telegram_id клиента заполнен из чата.")
    if conflict:
        extras.append(
            f"⚠ telegram_id={conflict['conflicting_telegram_id']} уже у клиента "
            f"id={conflict['conflicting_customer_id']} — НЕ перезаписан. "
            "Возможен дубликат, проверьте оператором."
        )
    return head + ("\n  " + "\n  ".join(extras) if extras else "")
