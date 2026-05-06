"""Read and write queries over orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypedDict

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog.models import CatalogProduct
from app.finance.models import FinanceEntryType, FinanceLedgerEntry
from app.orders.models import (
    PENDING_ITEM_STATUSES,
    OrdersCustomer,
    OrdersOrder,
    OrdersOrderItem,
    OrdersOrderItemStatus,
    OrdersOrderStatus,
)

# ── Read dataclasses ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PendingOrderItem:
    item_id: int
    order_id: int
    product_id: int
    product_name: str
    quantity: Decimal
    unit_price: Decimal | None
    item_status: str
    order_status: str
    customer_id: int
    customer_name: str
    customer_phone: str | None
    customer_telegram: str | None


@dataclass(frozen=True)
class CustomerMatch:
    id: int
    name: str
    telegram_id: str | None
    telegram_link: str | None
    phone: str | None
    email: str | None
    pending_orders_count: int
    total_debt: Decimal
    confidence: float


# ── Write dataclasses ───────────────────────────────────────────────────────

@dataclass
class OrderItemInput:
    product_name: str
    price: Decimal
    quantity: Decimal = field(default_factory=lambda: Decimal("1"))


@dataclass(frozen=True)
class CreatedOrderItem:
    item_id: int
    product_id: int
    product_name: str
    quantity: Decimal
    unit_price: Decimal


@dataclass(frozen=True)
class OrderCreationResult:
    order_id: int
    order_display: str
    customer_name: str
    telegram_link: str | None
    items: list[CreatedOrderItem]
    total: Decimal
    paid: Decimal
    debt: Decimal
    status: str


# ── Helpers ─────────────────────────────────────────────────────────────────

def _tg_link(telegram_id: str | None) -> str | None:
    """Return https://t.me/handle if telegram_id starts with @, else None."""
    if telegram_id and telegram_id.startswith("@"):
        return f"https://t.me/{telegram_id[1:]}"
    return None


_FIND_CUSTOMERS_SQL = text(
    """
    SELECT
        c.id,
        c.name,
        c.telegram_id,
        c.phone,
        c.email,
        similarity(c.name, :q) AS name_sim,
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
    LEFT JOIN orders_order o      ON o.customer_id = c.id
    LEFT JOIN orders_order_item oi ON oi.order_id  = o.id
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


# ── Read queries ─────────────────────────────────────────────────────────────

async def get_pending_items_for_product(
    session: AsyncSession, product_id: int
) -> list[PendingOrderItem]:
    """Pending items awaiting shipment for a given product.

    Ordered by order_id ASC (earliest order gets priority when matching).
    """
    stmt = (
        select(
            OrdersOrderItem.id.label("item_id"),
            OrdersOrderItem.order_id,
            OrdersOrderItem.product_id,
            CatalogProduct.name.label("product_name"),
            OrdersOrderItem.quantity,
            OrdersOrderItem.unit_price,
            OrdersOrderItem.status.label("item_status"),
            OrdersOrder.status.label("order_status"),
            OrdersCustomer.id.label("customer_id"),
            OrdersCustomer.name.label("customer_name"),
            OrdersCustomer.phone,
            OrdersCustomer.telegram_id,
        )
        .join(OrdersOrder, OrdersOrderItem.order_id == OrdersOrder.id)
        .join(OrdersCustomer, OrdersOrder.customer_id == OrdersCustomer.id)
        .join(CatalogProduct, OrdersOrderItem.product_id == CatalogProduct.id)
        .where(
            OrdersOrderItem.product_id == product_id,
            OrdersOrderItem.status.in_(PENDING_ITEM_STATUSES),
        )
        .order_by(OrdersOrderItem.order_id.asc(), OrdersOrderItem.id.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        PendingOrderItem(
            item_id=r.item_id,
            order_id=r.order_id,
            product_id=r.product_id,
            product_name=r.product_name,
            quantity=r.quantity,
            unit_price=r.unit_price,
            item_status=str(r.item_status),
            order_status=str(r.order_status),
            customer_id=r.customer_id,
            customer_name=r.customer_name,
            customer_phone=r.phone,
            customer_telegram=r.telegram_id,
        )
        for r in rows
    ]


async def get_orders_with_pending_items(
    session: AsyncSession,
) -> list[OrdersOrder]:
    """Orders that have at least one pending item, with items + customer loaded."""
    stmt = (
        select(OrdersOrder)
        .join(OrdersOrder.items)
        .where(OrdersOrderItem.status.in_(PENDING_ITEM_STATUSES))
        .options(
            selectinload(OrdersOrder.items).selectinload(OrdersOrderItem.order),
            selectinload(OrdersOrder.customer),
        )
        .order_by(OrdersOrder.id.asc())
        .distinct()
    )
    return list((await session.execute(stmt)).scalars().unique())


async def get_active_orders(session: AsyncSession) -> list[OrdersOrder]:
    stmt = (
        select(OrdersOrder)
        .where(OrdersOrder.status != OrdersOrderStatus.delivered)
        .options(
            selectinload(OrdersOrder.items),
            selectinload(OrdersOrder.customer),
        )
        .order_by(OrdersOrder.id.asc())
    )
    return list((await session.execute(stmt)).scalars())


async def get_customers(session: AsyncSession) -> list[OrdersCustomer]:
    stmt = select(OrdersCustomer).order_by(OrdersCustomer.id.asc())
    return list((await session.execute(stmt)).scalars())


async def get_customer_debt_summary(
    session: AsyncSession,
) -> dict[int, tuple[int, Decimal]]:
    """Map customer_id → (order_count, total_pending_amount)."""
    stmt = (
        select(
            OrdersOrder.customer_id,
            OrdersOrder.id,
            OrdersOrderItem.unit_price,
            OrdersOrderItem.quantity,
            OrdersOrderItem.status,
        )
        .join(OrdersOrderItem, OrdersOrder.id == OrdersOrderItem.order_id)
    )
    rows = (await session.execute(stmt)).all()

    class _Bucket(TypedDict):
        orders: set[int]
        amount: Decimal

    acc: dict[int, _Bucket] = {}
    for cid, oid, price, qty, status in rows:
        bucket = acc.setdefault(cid, {"orders": set(), "amount": Decimal("0")})
        bucket["orders"].add(int(oid))
        if status in PENDING_ITEM_STATUSES and price is not None:
            bucket["amount"] = bucket["amount"] + Decimal(price) * Decimal(qty)
    return {
        cid: (len(v["orders"]), v["amount"])
        for cid, v in acc.items()
    }


async def find_customers(
    session: AsyncSession, query: str, limit: int = 10
) -> list[CustomerMatch]:
    """Fuzzy search over customers by name, telegram_id, phone, or email.

    Returns candidates sorted by confidence desc.
    """
    q = (query or "").strip()
    if not q:
        return []

    rows = (
        await session.execute(
            _FIND_CUSTOMERS_SQL,
            {"q": q, "pat": f"%{q}%", "limit": limit},
        )
    ).mappings().all()

    results: list[CustomerMatch] = []
    q_lower = q.lower()
    q_handle = q_lower.lstrip("@")

    for row in rows:
        name_lower = row["name"].lower()
        tg = row["telegram_id"] or ""
        tg_handle = tg.lower().lstrip("@")
        ph = row["phone"] or ""
        em = (row["email"] or "").lower()

        if name_lower == q_lower:
            confidence = 1.0
        elif q_handle and tg_handle == q_handle:
            confidence = 0.95
        elif ph == q:
            confidence = 0.90
        elif q_lower in name_lower or name_lower in q_lower:
            confidence = 0.85
        elif q_handle and q_handle in tg_handle:
            confidence = 0.80
        elif q_lower in em:
            confidence = 0.75
        else:
            confidence = max(float(row["name_sim"] or 0.0), 0.1)

        results.append(
            CustomerMatch(
                id=row["id"],
                name=row["name"],
                telegram_id=tg or None,
                telegram_link=_tg_link(tg or None),
                phone=row["phone"],
                email=row["email"],
                pending_orders_count=int(row["pending_count"] or 0),
                total_debt=Decimal(str(row["total_debt"] or 0)),
                confidence=round(confidence, 3),
            )
        )

    return sorted(results, key=lambda x: x.confidence, reverse=True)


# ── Write operations ─────────────────────────────────────────────────────────

async def create_customer(
    session: AsyncSession,
    name: str,
    telegram_id: str | None = None,
    phone: str | None = None,
    email: str | None = None,
) -> OrdersCustomer:
    """Create a new customer.

    DB constraint requires at least one of telegram_id / phone / email.
    If none supplied a placeholder telegram_id is generated.
    """
    name = name.strip()
    if not name:
        raise ValueError("Customer name must not be empty")
    if not any([telegram_id, phone, email]):
        ts = int(datetime.now(tz=UTC).timestamp())
        telegram_id = f"@auto_{ts}"

    customer = OrdersCustomer(
        name=name,
        telegram_id=telegram_id,
        phone=phone,
        email=email,
    )
    session.add(customer)
    await session.flush()
    return customer


async def create_order(
    session: AsyncSession,
    customer_id: int,
    items: list[OrderItemInput],
    paid_amount: Decimal = Decimal("0"),
) -> OrderCreationResult:
    """Create an order with items, optional payment, and ledger entry.

    Does NOT commit — the caller is responsible for committing or rolling back.
    """
    if not items:
        raise ValueError("Order must have at least one item")

    customer = await session.get(OrdersCustomer, customer_id)
    if customer is None:
        raise ValueError(f"Customer {customer_id} not found")

    # Create order
    order = OrdersOrder(
        customer_id=customer_id,
        status=OrdersOrderStatus.confirmed,
        currency="RUB",
    )
    session.add(order)
    await session.flush()

    # Create items, resolving/creating products
    from app.catalog.repository import find_or_create_product  # local import avoids cycle

    total = Decimal("0")
    created_items: list[CreatedOrderItem] = []

    for inp in items:
        product = await find_or_create_product(session, inp.product_name)
        order_item = OrdersOrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=inp.quantity,
            unit_price=inp.price,
            status=OrdersOrderItemStatus.pending,
        )
        session.add(order_item)
        await session.flush()

        line_total = inp.price * inp.quantity
        total += line_total
        created_items.append(
            CreatedOrderItem(
                item_id=order_item.id,
                product_id=product.id,
                product_name=product.name,
                quantity=inp.quantity,
                unit_price=inp.price,
            )
        )

    order.total_price = total
    await session.flush()

    # Ledger entry for payment
    if paid_amount > Decimal("0"):
        entry = FinanceLedgerEntry(
            entry_at=datetime.now(tz=UTC),
            entry_type=FinanceEntryType.income,
            amount=paid_amount,
            currency="RUB",
            description=f"Оплата заказа З-{order.id:03d}",
            related_module="orders",
            related_entity="orders_order",
            related_id=order.id,
        )
        session.add(entry)
        await session.flush()

    debt = max(total - paid_amount, Decimal("0"))

    return OrderCreationResult(
        order_id=order.id,
        order_display=f"З-{order.id:03d}",
        customer_name=customer.name,
        telegram_link=_tg_link(customer.telegram_id),
        items=created_items,
        total=total,
        paid=paid_amount,
        debt=debt,
        status=str(order.status),
    )
