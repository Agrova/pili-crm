"""Catalog product search endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.catalog.repository import search_products

router = APIRouter(prefix="/api/v1/products", tags=["products"])


class ProductHit(BaseModel):
    product_id: int
    name: str
    supplier: str
    sku: str | None
    confidence: float


class ProductSearchResponse(BaseModel):
    query: str
    results: list[ProductHit]


@router.get("/search", response_model=ProductSearchResponse)
async def search(
    q: str = Query(..., min_length=1, description="search query"),
    limit: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
) -> ProductSearchResponse:
    hits = await search_products(db, q, limit=limit)
    return ProductSearchResponse(
        query=q,
        results=[
            ProductHit(
                product_id=h.product_id,
                name=h.name,
                supplier=h.supplier,
                sku=h.sku,
                confidence=h.confidence,
            )
            for h in hits
        ],
    )
