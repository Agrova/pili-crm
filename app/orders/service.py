"""Order business logic: status derivation from item statuses."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.orders.models import (
    ITEM_STATUS_WEIGHT,
    ITEM_TO_ORDER_STATUS_MAP,
    OrdersOrder,
    OrdersOrderItem,
    OrdersOrderStatus,
)


def derive_order_status(item_statuses: list[str]) -> str:
    """Pure function: derive order status from a list of item statuses.

    Takes the minimum-weight active (non-cancelled) item status and maps it
    to the corresponding order-level status.

    Raises ValueError if item_statuses is empty.
    """
    if not item_statuses:
        raise ValueError("Cannot derive order status: no item statuses provided")

    active = [s for s in item_statuses if s != "cancelled"]
    if not active:
        return "cancelled"

    earliest = min(active, key=lambda s: ITEM_STATUS_WEIGHT.get(s, 999))
    return ITEM_TO_ORDER_STATUS_MAP[earliest]


async def update_order_status_from_items(
    order_id: int, session: AsyncSession
) -> str:
    """Load item statuses from DB, derive order status, persist, return new status."""
    statuses = list(
        (
            await session.execute(
                select(OrdersOrderItem.status).where(
                    OrdersOrderItem.order_id == order_id
                )
            )
        ).scalars()
    )

    if not statuses:
        return "draft"

    new_status = derive_order_status([str(s) for s in statuses])

    order = await session.get(OrdersOrder, order_id)
    if order is not None:
        order.status = OrdersOrderStatus(new_status)

    return new_status
