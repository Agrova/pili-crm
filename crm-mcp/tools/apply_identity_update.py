"""Tool: apply / reject one identity-quarantine record (ADR-011 X1).

Write-tool. Operator-driven decision on a single
``analysis_extracted_identity`` row:

- ``overwrite`` — write ``value`` into the matching ``orders_customer``
  column. For ``email`` we wrap the UPDATE in a SAVEPOINT so a UNIQUE
  collision rolls back **only** that statement and the quarantine row
  stays ``pending``.
- ``reject`` — mark the row ``status='rejected'``; ``orders_customer`` is
  not touched.
- ``add_as_secondary`` — not yet implemented (no ``customer_contacts``
  table). Returns ``not_yet_implemented`` without DB writes.

All pre-validation failures return a structured ``{"status":"error",...}``
dict — no exceptions in normal flow. Bare Python errors (wrong arg type,
etc.) propagate as-is.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.analysis.identity_service import OPERATOR_OVERWRITE_COLUMNS
from app.analysis.models import AnalysisExtractedIdentity
from app.orders.service import set_customer_identity_field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("crm-mcp.apply_identity_update")

NAME = "apply_identity_update"
DESCRIPTION = (
    "Применяет или отклоняет одну запись карантина identity "
    "(analysis_extracted_identity, ADR-011). "
    "action='overwrite' — записать value в соответствующую колонку клиента "
    "(для email — с SAVEPOINT, при UNIQUE collision запись остаётся pending). "
    "action='reject' — пометить запись rejected, клиент не меняется. "
    "action='add_as_secondary' — пока не реализовано. "
    "Требует подтверждения оператора (write-tool)."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "extracted_id": {
            "type": "integer",
            "minimum": 1,
            "description": "id записи в analysis_extracted_identity.",
        },
        "action": {
            "type": "string",
            "enum": ["overwrite", "add_as_secondary", "reject"],
            "description": "Что сделать с записью карантина.",
        },
    },
    "required": ["extracted_id", "action"],
}

_VALID_ACTIONS = ("overwrite", "add_as_secondary", "reject")


async def run(
    session: AsyncSession,
    extracted_id: int,
    action: str,
) -> dict[str, Any]:
    eid = int(extracted_id)

    # ── Pre-validation: action whitelist ────────────────────────────────────
    if action not in _VALID_ACTIONS:
        return {
            "status": "error",
            "error": "unknown_action",
            "received": action,
            "message": "Допустимые значения: overwrite, add_as_secondary, reject",
        }

    # ── add_as_secondary short-circuit (no DB writes, no row lookup) ────────
    if action == "add_as_secondary":
        return {
            "status": "error",
            "error": "not_yet_implemented",
            "message": (
                "add_as_secondary будет реализован вместе с таблицей "
                "customer_contacts"
            ),
        }

    # ── Pre-validation: row exists ──────────────────────────────────────────
    record = await session.get(AnalysisExtractedIdentity, eid)
    if record is None:
        return {
            "status": "error",
            "error": "record_not_found",
            "extracted_id": eid,
            "message": "Запись карантина не найдена",
        }

    # ── Pre-validation: status is pending ───────────────────────────────────
    if record.status != "pending":
        return {
            "status": "error",
            "error": "already_processed",
            "extracted_id": eid,
            "current_status": record.status,
            "message": "Запись уже обработана ранее",
        }

    # ── reject branch ───────────────────────────────────────────────────────
    if action == "reject":
        try:
            now = datetime.now(tz=UTC)
            record.status = "rejected"
            record.applied_action = None  # reject is not an "apply" action
            record.applied_by = "operator"
            record.applied_at = now
            await session.commit()
        except SQLAlchemyError as exc:
            await session.rollback()
            logger.exception("apply_identity_update reject failed for id=%s", eid)
            return {
                "status": "error",
                "error": "db_error",
                "details": f"{type(exc).__name__}: {exc}",
                "message": "Ошибка БД, повторите попытку",
            }
        return {
            "status": "ok",
            "extracted_id": eid,
            "action": "reject",
        }

    # ── overwrite branch ────────────────────────────────────────────────────
    # Pre-validation continues: customer_id must be set, contact_type must map.
    if record.customer_id is None:
        return {
            "status": "error",
            "error": "unlinked_chat_quarantine",
            "extracted_id": eid,
            "chat_id": int(record.chat_id),
            "message": (
                "Чат не привязан к клиенту. Сначала вызовите "
                "link_chat_to_customer"
            ),
        }

    column = OPERATOR_OVERWRITE_COLUMNS.get(record.contact_type)
    if column is None:
        return {
            "status": "error",
            "error": "no_target_column",
            "contact_type": record.contact_type,
            "message": (
                f"Для contact_type={record.contact_type!r} нет соответствующей "
                f"колонки в OrdersCustomer"
            ),
        }

    customer_id = int(record.customer_id)
    new_value = record.value

    # Snapshot old value so the response shows what was overwritten.
    old_value_row = (
        await session.execute(
            text(f"SELECT {column} FROM orders_customer WHERE id = :cid"),  # noqa: S608
            {"cid": customer_id},
        )
    ).scalar_one_or_none()

    try:
        if record.contact_type == "email":
            try:
                async with session.begin_nested():
                    await set_customer_identity_field(
                        session, customer_id, column, new_value
                    )
            except IntegrityError:
                # SAVEPOINT already rolled back by the context manager.
                # The quarantine row stays pending — we do NOT commit any
                # status change. Look up who currently owns the email so the
                # operator can resolve the conflict; if that lookup itself
                # fails, degrade gracefully and report unknown owner.
                conflicting: int | None = None
                try:
                    raw = (
                        await session.execute(
                            text(
                                "SELECT id FROM orders_customer "
                                "WHERE email = :v AND id <> :cid LIMIT 1"
                            ),
                            {"v": new_value, "cid": customer_id},
                        )
                    ).scalar_one_or_none()
                    conflicting = int(raw) if raw is not None else None
                except SQLAlchemyError:
                    logger.exception(
                        "email collision: conflicting_customer_id lookup failed "
                        "for extracted_id=%s",
                        eid,
                    )
                    conflicting = None
                return {
                    "status": "error",
                    "error": "email_unique_collision",
                    "conflicting_customer_id": conflicting,
                    "message": (
                        f"Email уже занят клиентом {conflicting}"
                        if conflicting is not None
                        else "Email уже занят другим клиентом (id неизвестен)"
                    ),
                }
        else:
            await set_customer_identity_field(
                session, customer_id, column, new_value
            )

        now = datetime.now(tz=UTC)
        record.status = "applied"
        record.applied_action = "overwrite"
        record.applied_by = "operator"
        record.applied_at = now
        await session.commit()

    except SQLAlchemyError as exc:
        await session.rollback()
        logger.exception("apply_identity_update overwrite failed for id=%s", eid)
        return {
            "status": "error",
            "error": "db_error",
            "details": f"{type(exc).__name__}: {exc}",
            "message": "Ошибка БД, повторите попытку",
        }

    return {
        "status": "ok",
        "extracted_id": eid,
        "action": "overwrite",
        "applied_to_column": column,
        "old_value": old_value_row,
        "new_value": new_value,
    }


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        return f"Ошибка: {result.get('message') or result.get('error')}"
    action = result.get("action")
    eid = result.get("extracted_id")
    if action == "reject":
        return f"✅ Запись id={eid} отклонена (rejected)."
    col = result.get("applied_to_column")
    old = result.get("old_value")
    new = result.get("new_value")
    old_repr = "—" if old is None else f"«{old}»"
    return (
        f"✅ Запись id={eid} применена: {col} {old_repr} → «{new}»."
    )
