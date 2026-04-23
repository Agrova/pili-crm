"""Customers list endpoint."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.orders.repository import get_customer_debt_summary, get_customers

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])


class CustomerOut(BaseModel):
    id: int
    name: str
    email: str | None
    phone: str | None
    telegram_id: str | None
    order_count: int
    pending_amount: Decimal


@router.get("", response_model=list[CustomerOut])
async def list_customers(
    db: AsyncSession = Depends(get_db),
) -> list[CustomerOut]:
    customers = await get_customers(db)
    summary = await get_customer_debt_summary(db)
    out: list[CustomerOut] = []
    for c in customers:
        count, amount = summary.get(c.id, (0, Decimal("0")))
        out.append(
            CustomerOut(
                id=c.id,
                name=c.name,
                email=c.email,
                phone=c.phone,
                telegram_id=c.telegram_id,
                order_count=count,
                pending_amount=amount,
            )
        )
    return out
