"""Procurement domain services — hook on purchase status transitions."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog.services import record_listing_price_from_purchase
from app.procurement.models import ProcurementPurchase, ProcurementPurchaseStatus

logger = logging.getLogger(__name__)


async def on_purchase_delivered(purchase_id: int, session: AsyncSession) -> None:
    """Hook: called after a purchase transitions to status='delivered'.

    Records a catalog_listing_price entry for every purchase item that has a
    unit_cost.  Idempotent: uses delivered_at as a sentinel — if already set,
    returns immediately without duplicating records.

    Must be called within the same transaction as the status update so that
    the whole operation rolls back on failure.
    """
    result = await session.execute(
        select(ProcurementPurchase)
        .where(ProcurementPurchase.id == purchase_id)
        .options(selectinload(ProcurementPurchase.items))
    )
    purchase = result.scalar_one()

    assert purchase.status == ProcurementPurchaseStatus.delivered, (
        f"on_purchase_delivered called for purchase {purchase_id} "
        f"with status={purchase.status!r}"
    )

    # Idempotency guard.
    if purchase.delivered_at is not None:
        return

    purchase.delivered_at = datetime.now(tz=UTC)

    for item in purchase.items:
        if item.unit_cost is None:
            logger.warning(
                "purchase %d item %d: unit_cost is NULL — skipping listing price",
                purchase_id,
                item.id,
            )
            continue
        if purchase.currency is None:
            logger.warning(
                "purchase %d: currency is NULL — skipping listing price for item %d",
                purchase_id,
                item.id,
            )
            continue

        await record_listing_price_from_purchase(
            session=session,
            product_id=item.product_id,
            source_id=purchase.supplier_id,
            unit_cost=item.unit_cost,
            currency=purchase.currency,
            observed_at=purchase.delivered_at,
            purchase_id=purchase_id,
        )
