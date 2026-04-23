"""Orders endpoints: pending list + status derivation."""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.orders.models import PENDING_ITEM_STATUSES, OrdersOrder
from app.orders.repository import get_orders_with_pending_items
from app.orders.service import update_order_status_from_items

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/orders", tags=["orders"])


class PendingItemOut(BaseModel):
    item_id: int
    product_id: int
    quantity: Decimal
    unit_price: Decimal | None
    status: str


class PendingOrderOut(BaseModel):
    order_id: int
    status: str
    customer_id: int
    customer_name: str
    customer_phone: str | None
    customer_telegram: str | None
    items: list[PendingItemOut]
    total_pending_amount: Decimal


@router.get("/pending", response_model=list[PendingOrderOut])
async def pending_orders(
    db: AsyncSession = Depends(get_db),
) -> list[PendingOrderOut]:
    orders = await get_orders_with_pending_items(db)
    out: list[PendingOrderOut] = []
    for o in orders:
        pending_items = [i for i in o.items if i.status in PENDING_ITEM_STATUSES]
        if not pending_items:
            continue
        amount = sum(
            ((i.unit_price or Decimal("0")) * i.quantity for i in pending_items),
            start=Decimal("0"),
        )
        out.append(
            PendingOrderOut(
                order_id=o.id,
                status=str(o.status),
                customer_id=o.customer.id,
                customer_name=o.customer.name,
                customer_phone=o.customer.phone,
                customer_telegram=o.customer.telegram_id,
                items=[
                    PendingItemOut(
                        item_id=i.id,
                        product_id=i.product_id,
                        quantity=i.quantity,
                        unit_price=i.unit_price,
                        status=str(i.status),
                    )
                    for i in pending_items
                ],
                total_pending_amount=amount,
            )
        )
    return out


class DeriveStatusOut(BaseModel):
    order_id: int
    old_status: str
    new_status: str


@router.post("/{order_id}/derive-status", response_model=DeriveStatusOut)
async def derive_order_status_endpoint(
    order_id: int,
    db: AsyncSession = Depends(get_db),
) -> DeriveStatusOut:
    """DEPRECATED: order status derivation is now handled by a PostgreSQL trigger (ADR-006).
    This endpoint is kept as a fallback only. Do not call it from new code.
    """
    logger.warning(
        "DEPRECATED: /derive-status called for order %s, use DB trigger instead",
        order_id,
    )
    order = (
        await db.execute(select(OrdersOrder).where(OrdersOrder.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")

    old_status = str(order.status)
    new_status = await update_order_status_from_items(order_id, db)
    await db.commit()

    return DeriveStatusOut(
        order_id=order_id,
        old_status=old_status,
        new_status=new_status,
    )
