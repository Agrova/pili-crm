"""Tool: create a confirmed order with items and optional payment."""

from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

NAME = "create_order"
DESCRIPTION = (
    "Создаёт подтверждённый заказ с позициями и опциональной оплатой. "
    "Автоматически создаёт запись в finance_ledger_entry при оплате. "
    "Если товар не найден в каталоге — создаёт его автоматически. "
    "Используй ТОЛЬКО после двух подтверждений оператора."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "customer_id": {
            "type": "integer",
            "description": "ID клиента из find_customer или create_customer.",
        },
        "items": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "minLength": 1},
                    "price": {"type": "number", "exclusiveMinimum": 0},
                    "quantity": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "default": 1,
                    },
                },
                "required": ["product_name", "price"],
            },
        },
        "paid_amount": {
            "type": "number",
            "minimum": 0,
            "default": 0,
            "description": "Оплаченная сумма в рублях (0 = без предоплаты).",
        },
    },
    "required": ["customer_id", "items"],
}

# ── SQL ─────────────────────────────────────────────────────────────────────

_GET_CUSTOMER_SQL = text(
    "SELECT id, name, telegram_id FROM orders_customer WHERE id = :cid"
)
_FIND_PRODUCT_SQL = text(
    "SELECT id, name FROM catalog_product WHERE lower(name) = lower(:name) LIMIT 1"
)
_FIND_PRODUCT_ILIKE_SQL = text(
    "SELECT id, name FROM catalog_product WHERE name ILIKE :pat LIMIT 2"
)
_CREATE_PRODUCT_SQL = text(
    """
    INSERT INTO catalog_product (name)
    VALUES (:name)
    RETURNING id, name
    """
)
_INSERT_ORDER_SQL = text(
    """
    INSERT INTO orders_order (customer_id, status, currency)
    VALUES (:cid, 'confirmed'::orders_order_status, 'RUB')
    RETURNING id
    """
)
_INSERT_ITEM_SQL = text(
    """
    INSERT INTO orders_order_item
        (order_id, product_id, quantity, unit_price, status)
    VALUES
        (:oid, :pid, :qty, :price, 'pending'::orders_order_item_status)
    RETURNING id
    """
)
_UPDATE_TOTAL_SQL = text(
    "UPDATE orders_order SET total_price = :total WHERE id = :oid"
)
_INSERT_LEDGER_SQL = text(
    """
    INSERT INTO finance_ledger_entry
        (entry_at, entry_type, amount, currency,
         description, related_module, related_entity, related_id)
    VALUES
        (NOW(), 'income'::finance_entry_type, :amount, 'RUB',
         :desc, 'orders', 'orders_order', :oid)
    RETURNING id
    """
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _tg_link(telegram_id: str | None) -> str | None:
    if telegram_id and telegram_id.startswith("@"):
        return f"https://t.me/{telegram_id[1:]}"
    return None


async def _find_or_create_product(
    session: AsyncSession, name: str
) -> dict[str, Any]:
    """Return {id, name} for an existing or newly created product."""
    # Exact match
    row = (
        await session.execute(_FIND_PRODUCT_SQL, {"name": name})
    ).mappings().one_or_none()
    if row:
        return dict(row)

    # Single ILIKE match
    ilike = (
        await session.execute(_FIND_PRODUCT_ILIKE_SQL, {"pat": f"%{name}%"})
    ).mappings().all()
    if len(ilike) == 1:
        return dict(ilike[0])

    row = (
        await session.execute(_CREATE_PRODUCT_SQL, {"name": name})
    ).mappings().one()
    print(f"[create_order] auto-created product: {row['name']}", file=sys.stderr)
    return dict(row)


# ── Main ─────────────────────────────────────────────────────────────────────

async def run(
    session: AsyncSession,
    customer_id: int,
    items: list[dict[str, Any]],
    paid_amount: float = 0.0,
) -> dict[str, Any]:
    # Validate customer
    cust = (
        await session.execute(_GET_CUSTOMER_SQL, {"cid": customer_id})
    ).mappings().one_or_none()
    if not cust:
        return {
            "status": "error",
            "error": f"Клиент с id={customer_id} не найден.",
        }

    if not items:
        return {"status": "error", "error": "Список позиций пуст."}

    # Parse paid_amount
    try:
        paid = Decimal(str(paid_amount))
    except InvalidOperation:
        return {"status": "error", "error": f"Некорректная сумма оплаты: {paid_amount}"}

    try:
        # Create order
        order_row = (
            await session.execute(_INSERT_ORDER_SQL, {"cid": customer_id})
        ).mappings().one()
        order_id = order_row["id"]

        # Create items
        total = Decimal("0")
        created_items: list[dict[str, Any]] = []

        for inp in items:
            pname = (inp.get("product_name") or "").strip()
            if not pname:
                raise ValueError("product_name не может быть пустым")
            try:
                price = Decimal(str(inp["price"]))
                qty = Decimal(str(inp.get("quantity", 1)))
            except (InvalidOperation, KeyError) as e:
                raise ValueError(f"Некорректная цена/количество для «{pname}»: {e}") from e

            product = await _find_or_create_product(session, pname)

            item_row = (
                await session.execute(
                    _INSERT_ITEM_SQL,
                    {
                        "oid": order_id,
                        "pid": product["id"],
                        "qty": str(qty),
                        "price": str(price),
                    },
                )
            ).mappings().one()

            line = price * qty
            total += line
            created_items.append(
                {
                    "item_id": item_row["id"],
                    "product_id": product["id"],
                    "product_name": product["name"],
                    "quantity": float(qty),
                    "unit_price": float(price),
                    "line_total": float(line),
                }
            )

        # Update total
        await session.execute(_UPDATE_TOTAL_SQL, {"total": str(total), "oid": order_id})

        # Ledger entry for payment
        ledger_id: int | None = None
        if paid > Decimal("0"):
            ledger_row = (
                await session.execute(
                    _INSERT_LEDGER_SQL,
                    {
                        "amount": str(paid),
                        "desc": f"Оплата заказа З-{order_id:03d}",
                        "oid": order_id,
                    },
                )
            ).mappings().one()
            ledger_id = ledger_row["id"]

        await session.commit()

    except Exception as exc:
        await session.rollback()
        return {"status": "error", "error": str(exc)}

    debt = max(total - paid, Decimal("0"))
    tg = cust["telegram_id"]

    return {
        "status": "ok",
        "order_id": order_id,
        "order_display": f"З-{order_id:03d}",
        "customer_name": cust["name"],
        "telegram_link": _tg_link(tg),
        "items": created_items,
        "total": float(total),
        "paid": float(paid),
        "debt": float(debt),
        "ledger_entry_id": ledger_id,
        "order_status": "confirmed",
    }


def format_text(result: dict[str, Any]) -> str:
    if result.get("status") != "ok":
        return f"Ошибка: {result.get('error', 'неизвестная ошибка')}"

    tg = result.get("telegram_link") or "—"
    lines = [
        f"✅ Заказ {result['order_display']} создан — "
        f"{result['customer_name']} ({tg})",
        f"   Статус: {result['order_status']}",
        "",
        "   Позиции:",
    ]
    for it in result["items"]:
        lines.append(
            f"   • {it['product_name']} — "
            f"{it['unit_price']:,.0f} ₽ × {it['quantity']:g} = "
            f"{it['line_total']:,.0f} ₽"
        )

    total = result["total"]
    paid = result["paid"]
    debt = result["debt"]
    lines.append(f"\n   Итого:   {total:,.0f} ₽")
    lines.append(f"   Оплата:  {paid:,.0f} ₽")
    lines.append(f"   Долг:    {debt:,.0f} ₽")

    if result.get("ledger_entry_id"):
        lines.append(
            f"   Ledger:  запись #{result['ledger_entry_id']} создана."
        )

    return "\n".join(lines)
