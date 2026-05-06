"""Tool: verify a draft order — confirm or reject."""

from __future__ import annotations

import logging
from typing import Any

from app.orders.models import OrdersOrder, OrdersOrderStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("crm-mcp.verify_draft_order")

NAME = "verify_draft_order"
DESCRIPTION = (
    "Верифицирует черновой заказ: action='confirm' переводит в статус confirmed; "
    "action='reject' удаляет заказ и все его позиции. "
    "Требует подтверждения оператора (write-tool)."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "order_id": {
            "type": "integer",
            "minimum": 1,
            "description": "id заказа (числовой, не З-XXX)",
        },
        "action": {
            "type": "string",
            "enum": ["confirm", "reject"],
            "description": '"confirm" → статус confirmed; "reject" → удалить заказ',
        },
    },
    "required": ["order_id", "action"],
}


async def run(
    session: AsyncSession,
    order_id: int,
    action: str,
) -> dict[str, Any]:
    oid = int(order_id)
    display_id = f"З-{oid:03d}"

    order = await session.get(OrdersOrder, oid)
    if order is None:
        return {"status": "error", "error": "order_not_found", "order_id": oid}

    if order.status != OrdersOrderStatus.draft:
        return {
            "status": "error",
            "error": "not_a_draft",
            "order_id": oid,
            "display_id": display_id,
            "current_status": order.status.value,
        }

    try:
        if action == "confirm":
            order.status = OrdersOrderStatus.confirmed
            await session.commit()
            return {
                "status": "ok",
                "action": "confirmed",
                "order_id": oid,
                "display_id": display_id,
            }
        else:  # reject
            # Raw DELETE lets the DB-level CASCADE remove order items.
            # session.delete(order) would try to null-out order_item.order_id
            # first (no passive_deletes on the ORM relationship), which fails
            # on the NOT NULL constraint.
            await session.execute(
                text("DELETE FROM orders_order WHERE id = :oid"), {"oid": oid}
            )
            await session.commit()
            return {
                "status": "ok",
                "action": "rejected",
                "order_id": oid,
                "display_id": display_id,
            }
    except Exception as exc:
        await session.rollback()
        logger.exception("verify_draft_order failed for order_id=%s action=%s", oid, action)
        return {
            "status": "error",
            "error": "db_error",
            "order_id": oid,
            "details": f"{type(exc).__name__}: {exc}",
        }


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        err = result.get("error")
        oid = result.get("order_id")
        if err == "order_not_found":
            return f"Ошибка: заказ id={oid} не найден."
        if err == "not_a_draft":
            return (
                f"Ошибка: З-{oid:03d} не является черновиком "
                f"(текущий статус: {result.get('current_status')})."
            )
        return f"Ошибка: {result.get('error', 'неизвестная')}"

    action = result.get("action")
    did = result.get("display_id")
    if action == "confirmed":
        return f"✅ {did} подтверждён (статус: confirmed)."
    return f"🗑 {did} отклонён и удалён."
