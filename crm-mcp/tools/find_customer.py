"""Tool: fuzzy customer search by name, telegram handle, phone, or email."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "find_customer"
DESCRIPTION = (
    "Ищет клиента по имени, @telegram-хэндлу, телефону или email. "
    "Возвращает кандидатов с оценкой уверенности и ссылкой на Telegram-чат. "
    "Используй перед созданием заказа, чтобы убедиться что клиент существует."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Строка поиска: имя, @handle, телефон или email. "
                "Пример: «Антон», «@tochin», «+79161234567»."
            ),
        }
    },
    "required": ["query"],
}

_SEARCH_SQL = text(
    """
    SELECT
        c.id,
        c.name,
        c.telegram_id,
        c.phone,
        c.email,
        similarity(c.name, :q)  AS name_sim,
        count(DISTINCT o.id)
            FILTER (WHERE o.status NOT IN ('delivered', 'cancelled'))
            AS pending_count,
        coalesce(sum(
            CASE
                WHEN o.status  NOT IN ('delivered', 'cancelled')
                 AND oi.status NOT IN ('delivered', 'cancelled')
                 AND oi.unit_price IS NOT NULL
                THEN oi.unit_price * oi.quantity
                ELSE 0
            END
        ), 0) AS total_debt
    FROM orders_customer c
    LEFT JOIN orders_order o       ON o.customer_id = c.id
    LEFT JOIN orders_order_item oi ON oi.order_id   = o.id
    WHERE
        c.name        ILIKE :pat
     OR c.telegram_id ILIKE :pat
     OR c.phone              = :q
     OR c.email       ILIKE :pat
    GROUP BY c.id
    ORDER BY name_sim DESC, c.name ASC
    LIMIT 10
    """
)


def _tg_link(telegram_id: str | None) -> str | None:
    if telegram_id and telegram_id.startswith("@"):
        return f"https://t.me/{telegram_id[1:]}"
    return None


def _confidence(q: str, row: Any) -> float:
    q_lower = q.lower()
    q_handle = q_lower.lstrip("@")
    name_lower = row["name"].lower()
    tg = (row["telegram_id"] or "").lower().lstrip("@")
    ph = row["phone"] or ""
    em = (row["email"] or "").lower()

    if name_lower == q_lower:
        return 1.0
    if q_handle and tg == q_handle:
        return 0.95
    if ph == q:
        return 0.90
    if q_lower in name_lower or name_lower in q_lower:
        return 0.85
    if q_handle and q_handle in tg:
        return 0.80
    if q_lower in em:
        return 0.75
    return max(float(row["name_sim"] or 0.0), 0.1)


async def run(session: AsyncSession, query: str) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        return {"found": False, "candidates": [], "message": "Пустой запрос."}

    rows = (
        await session.execute(_SEARCH_SQL, {"q": q, "pat": f"%{q}%"})
    ).mappings().all()

    if not rows:
        return {
            "found": False,
            "candidates": [],
            "message": f"Клиент «{q}» не найден.",
        }

    candidates = sorted(
        [
            {
                "id": row["id"],
                "name": row["name"],
                "telegram_id": row["telegram_id"],
                "telegram_link": _tg_link(row["telegram_id"]),
                "phone": row["phone"],
                "email": row["email"],
                "pending_orders_count": int(row["pending_count"] or 0),
                "total_debt": float(row["total_debt"] or 0),
                "confidence": round(_confidence(q, row), 3),
            }
            for row in rows
        ],
        key=lambda x: x["confidence"],
        reverse=True,
    )

    return {"found": True, "candidates": candidates}


def format_text(result: dict[str, Any]) -> str:
    if not result.get("found"):
        return f"❌ {result.get('message', 'Клиент не найден.')}"

    lines: list[str] = [f"Найдено кандидатов: {len(result['candidates'])}"]
    for c in result["candidates"]:
        tg = c.get("telegram_link") or c.get("telegram_id") or "—"
        debt = c.get("total_debt", 0)
        debt_s = f"{debt:,.0f} ₽" if debt else "нет"
        lines.append(
            f"  • [{c['confidence']:.0%}] {c['name']} "
            f"(id={c['id']}) — {tg}, "
            f"активных заказов: {c['pending_orders_count']}, долг: {debt_s}"
        )
    return "\n".join(lines)
