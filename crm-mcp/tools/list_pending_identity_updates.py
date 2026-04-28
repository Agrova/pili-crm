"""Tool: list pending identity-quarantine rows for a customer (ADR-011 X1).

Read-only. Surfaces ``analysis_extracted_identity`` rows with
``status='pending'`` for one customer so Cowork can ask the operator:
"подтвердить / перезаписать / отклонить".

For each pending row we also report ``current_customer_value`` — the
matching column on ``orders_customer`` — so the operator sees what an
``overwrite`` would replace (especially important for ``name``: NOT NULL,
overwrite is destructive).
"""

from __future__ import annotations

from typing import Any

from app.analysis.identity_service import OPERATOR_OVERWRITE_COLUMNS
from app.analysis.models import AnalysisExtractedIdentity
from app.orders.service import get_customer_identity_columns
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "list_pending_identity_updates"
DESCRIPTION = (
    "Возвращает pending-записи карантина identity для клиента "
    "(analysis_extracted_identity, ADR-011). Для каждой записи: "
    "contact_type, value, confidence, цитата из чата, текущее значение "
    "соответствующей колонки клиента (current_customer_value). "
    "Сортировка: confidence (high → medium → low), затем extracted_at DESC. "
    "Read-only, выполняется сразу без подтверждения."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_id": {
            "type": "integer",
            "minimum": 1,
            "description": "id в orders_customer.",
        },
    },
    "required": ["customer_id"],
}

_CONFIDENCE_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


async def run(session: AsyncSession, customer_id: int) -> dict[str, Any]:
    cid = int(customer_id)

    try:
        current_columns = await get_customer_identity_columns(session, cid)
    except ValueError:
        return {
            "status": "error",
            "error": "customer_not_found",
            "customer_id": cid,
            "message": f"Клиент id={cid} не найден",
        }

    customer_name = current_columns["name"]

    rows = list(
        (
            await session.execute(
                select(AnalysisExtractedIdentity).where(
                    AnalysisExtractedIdentity.customer_id == cid,
                    AnalysisExtractedIdentity.status == "pending",
                )
            )
        )
        .scalars()
        .all()
    )

    rows.sort(
        key=lambda r: (
            _CONFIDENCE_RANK.get(r.confidence, 99),
            -(r.extracted_at.timestamp() if r.extracted_at is not None else 0),
        )
    )

    pending_updates = []
    for r in rows:
        column = OPERATOR_OVERWRITE_COLUMNS.get(r.contact_type)
        current_value = current_columns.get(column) if column is not None else None
        pending_updates.append(
            {
                "extracted_id": int(r.extracted_id),
                "chat_id": int(r.chat_id),
                "analyzer_version": r.analyzer_version,
                "extracted_at": (
                    r.extracted_at.isoformat() if r.extracted_at is not None else None
                ),
                "contact_type": r.contact_type,
                "value": r.value,
                "confidence": r.confidence,
                "context_quote": r.context_quote,
                "current_customer_value": current_value,
            }
        )

    return {
        "status": "ok",
        "customer_id": cid,
        "customer_name": customer_name,
        "pending_count": len(pending_updates),
        "pending_updates": pending_updates,
    }


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        return f"Ошибка: {result.get('message') or result.get('error')}"
    count = result.get("pending_count", 0)
    if count == 0:
        return (
            f"Карантин identity пуст для клиента "
            f"{result.get('customer_name')!r} (id={result.get('customer_id')})."
        )
    lines = [
        f"Pending identity updates для клиента {result.get('customer_name')!r} "
        f"(id={result.get('customer_id')}): {count}"
    ]
    for u in result.get("pending_updates", []):
        cur = u.get("current_customer_value")
        cur_repr = "—" if cur is None else f"«{cur}»"
        lines.append(
            f"  • [extracted_id={u['extracted_id']}] "
            f"{u['contact_type']}={u['value']!r} "
            f"(conf={u['confidence']}, current={cur_repr}, chat={u['chat_id']})"
        )
    return "\n".join(lines)
