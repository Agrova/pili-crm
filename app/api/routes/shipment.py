"""POST /api/v1/shipment/match — match arrived shipment items to pending orders."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.catalog.repository import ProductSearchResult, search_products
from app.orders.repository import get_pending_items_for_product

router = APIRouter(prefix="/api/v1/shipment", tags=["shipment"])

EXACT_CONFIDENCE = 0.95


class ShipmentMatchRequest(BaseModel):
    items: list[str] = Field(..., min_length=1)


class MatchedItem(BaseModel):
    input_item: str
    product_name: str
    supplier: str
    confidence: float
    order_id: int
    order_status: str
    item_status: str
    customer_name: str
    customer_phone: str | None
    customer_telegram: str | None
    quantity: Decimal
    unit_price: Decimal | None


class AmbiguousCandidate(BaseModel):
    product_name: str
    supplier: str
    confidence: float
    order_id: int | None
    customer_name: str | None


class AmbiguousItem(BaseModel):
    input_item: str
    candidates: list[AmbiguousCandidate]


class UnmatchedItem(BaseModel):
    input_item: str
    reason: str


class ShipmentMatchResponse(BaseModel):
    matched: list[MatchedItem]
    ambiguous: list[AmbiguousItem]
    unmatched: list[UnmatchedItem]


async def _match_one(
    db: AsyncSession, query: str
) -> tuple[MatchedItem | None, AmbiguousItem | None, UnmatchedItem | None]:
    candidates = await search_products(db, query, limit=5)
    if not candidates:
        return None, None, UnmatchedItem(
            input_item=query, reason="Товар не найден в каталоге"
        )

    top = candidates[0]
    is_exact = top.confidence >= EXACT_CONFIDENCE or top.name.lower() == query.lower()

    if is_exact:
        pending = await get_pending_items_for_product(db, top.product_id)
        if pending:
            first = pending[0]
            return (
                MatchedItem(
                    input_item=query,
                    product_name=top.name,
                    supplier=top.supplier,
                    confidence=top.confidence,
                    order_id=first.order_id,
                    order_status=first.order_status,
                    item_status=first.item_status,
                    customer_name=first.customer_name,
                    customer_phone=first.customer_phone,
                    customer_telegram=first.customer_telegram,
                    quantity=first.quantity,
                    unit_price=first.unit_price,
                ),
                None,
                None,
            )
        return None, None, UnmatchedItem(
            input_item=query,
            reason=f"«{top.name}» найден в каталоге, но нет ожидающих заказов",
        )

    enriched: list[AmbiguousCandidate] = []
    for c in candidates:
        pending = await get_pending_items_for_product(db, c.product_id)
        if pending:
            p = pending[0]
            enriched.append(
                AmbiguousCandidate(
                    product_name=c.name,
                    supplier=c.supplier,
                    confidence=c.confidence,
                    order_id=p.order_id,
                    customer_name=p.customer_name,
                )
            )
        else:
            enriched.append(
                AmbiguousCandidate(
                    product_name=c.name,
                    supplier=c.supplier,
                    confidence=c.confidence,
                    order_id=None,
                    customer_name=None,
                )
            )

    with_orders = [c for c in enriched if c.order_id is not None]
    if with_orders:
        return None, AmbiguousItem(input_item=query, candidates=with_orders), None
    return None, None, UnmatchedItem(
        input_item=query,
        reason="Есть похожие товары в каталоге, но ни один не в ожидающих заказах",
    )


@router.post("/match", response_model=ShipmentMatchResponse)
async def match_shipment(
    body: ShipmentMatchRequest,
    db: AsyncSession = Depends(get_db),
) -> ShipmentMatchResponse:
    matched: list[MatchedItem] = []
    ambiguous: list[AmbiguousItem] = []
    unmatched: list[UnmatchedItem] = []
    for raw in body.items:
        query = raw.strip()
        if not query:
            unmatched.append(
                UnmatchedItem(input_item=raw, reason="Пустой ввод")
            )
            continue
        m, a, u = await _match_one(db, query)
        if m:
            matched.append(m)
        elif a:
            ambiguous.append(a)
        elif u:
            unmatched.append(u)

    _ = ProductSearchResult  # keep import used
    return ShipmentMatchResponse(
        matched=matched, ambiguous=ambiguous, unmatched=unmatched
    )
