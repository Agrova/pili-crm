"""Tool: apply a ready analysis to the customer linked to a chat.

Intended workflow: chat is analysed with --no-apply, then linked via
link_chat_to_customer. This tool applies the stored analysis so identity
and order data land in the CRM without re-running the LLM.
"""

from __future__ import annotations

import logging
from typing import Any

from app.analysis import repository as analysis_repo
from app.analysis.service import apply_analysis_to_customer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("crm-mcp.apply_pending_analysis")

NAME = "apply_pending_analysis"
DESCRIPTION = (
    "Применяет последний завершённый анализ чата к привязанному клиенту. "
    "Используется после link_chat_to_customer, когда чат был прогнан с --no-apply. "
    "Требует подтверждения оператора (write-tool)."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chat_id": {
            "type": "integer",
            "minimum": 1,
            "description": "id чата из communications_telegram_chat.",
        },
    },
    "required": ["chat_id"],
}

_SELECT_CHAT_SQL = text(
    "SELECT id, review_status::text AS review_status "
    "FROM communications_telegram_chat WHERE id = :chat_id"
)

_LINKED_STATUSES = frozenset({"linked", "new_customer"})


async def run(
    session: AsyncSession,
    chat_id: int,
) -> dict[str, Any]:
    cid = int(chat_id)

    chat = (
        await session.execute(_SELECT_CHAT_SQL, {"chat_id": cid})
    ).mappings().one_or_none()
    if chat is None:
        return {"status": "error", "error": "chat_not_found", "chat_id": cid}
    if chat["review_status"] not in _LINKED_STATUSES:
        return {
            "status": "error",
            "error": "chat_not_linked",
            "chat_id": cid,
            "review_status": chat["review_status"],
            "message": "Сначала привяжите чат через link_chat_to_customer",
        }

    analyses = await analysis_repo.list_analyses_for_chat(session, cid)
    if not analyses:
        return {
            "status": "error",
            "error": "no_analysis_found",
            "chat_id": cid,
            "message": "Для чата нет завершённого анализа. Запустите analysis/run.py",
        }

    analysis = analyses[0]

    try:
        result = await apply_analysis_to_customer(
            session, analysis_id=analysis.id, force=True
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.exception("apply_pending_analysis failed for chat_id=%s", cid)
        return {
            "status": "error",
            "error": "apply_failed",
            "details": f"{type(exc).__name__}: {exc}",
        }

    if result.ambiguous_customer_ids:
        return {
            "status": "error",
            "error": "ambiguous_customer",
            "ambiguous_customer_ids": result.ambiguous_customer_ids,
            "message": (
                "Чат привязан к нескольким клиентам — требуется ручное разрешение"
            ),
        }

    return {
        "status": "ok",
        "chat_id": cid,
        "analysis_id": result.analysis_id,
        "customer_id": result.customer_id,
        "identities_quarantined": result.identities_quarantined,
        "identities_auto_applied": result.identities_auto_applied,
        "identities_kept_pending": result.identities_kept_pending,
        "orders_created": result.orders_created,
        "orders_filtered_historical": result.orders_filtered_historical,
        "order_items_created": result.order_items_created,
        "pending_items_created": result.pending_items_created,
        "preferences_added": result.preferences_added,
        "incidents_added": result.incidents_added,
        "delivery_preferences_updated": result.delivery_preferences_updated,
        "rolled_back_count": result.rolled_back_count,
    }


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        err = result.get("error", "неизвестная ошибка")
        msg = result.get("message", "")
        if err == "chat_not_found":
            return f"Ошибка: чат id={result.get('chat_id')} не найден."
        if err == "chat_not_linked":
            return (
                f"Ошибка: чат id={result.get('chat_id')} не привязан к клиенту "
                f"(статус: {result.get('review_status')}). {msg}"
            ).rstrip()
        if err in ("no_analysis_found", "ambiguous_customer"):
            return f"Ошибка: {msg}" if msg else f"Ошибка: {err}"
        return f"Ошибка: {err}"
    cid = result.get("customer_id")
    aid = result.get("analysis_id")
    iq = result.get("identities_quarantined", 0)
    ia = result.get("identities_auto_applied", 0)
    ip = result.get("identities_kept_pending", 0)
    oc = result.get("orders_created", 0)
    oi = result.get("order_items_created", 0)
    ofh = result.get("orders_filtered_historical", 0)
    return (
        f"✅ Анализ чата id={result['chat_id']} применён к клиенту id={cid}.\n"
        f"   (analysis_id={aid})\n"
        f"   Идентификаторы: карантин={iq}, авто={ia}, ожидают={ip}\n"
        f"   Заказы: создано={oc}, позиций={oi}, отфильтровано как история={ofh}"
    )
