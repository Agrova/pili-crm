"""Read and write queries over catalog."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import CatalogProduct, CatalogProductListing, CatalogSupplier


@dataclass(frozen=True)
class ProductSearchResult:
    product_id: int
    name: str
    supplier: str
    sku: str | None
    confidence: float


async def search_products(
    session: AsyncSession, query: str, limit: int = 5
) -> list[ProductSearchResult]:
    """Fuzzy product search.

    Ranks by pg_trgm similarity when the extension is available, then by
    ILIKE containment as a secondary signal. Returns up to `limit` rows with a
    confidence score in [0, 1].
    """
    q = (query or "").strip()
    if not q:
        return []

    similarity = func.similarity(CatalogProduct.name, q).label("sim")
    like_hit = CatalogProduct.name.ilike(f"%{q}%")

    stmt = (
        select(
            CatalogProduct.id,
            CatalogProduct.name,
            CatalogSupplier.name.label("supplier_name"),
            CatalogProduct.sku,
            similarity,
        )
        .outerjoin(
            CatalogProductListing,
            (CatalogProductListing.product_id == CatalogProduct.id)
            & (CatalogProductListing.is_primary.is_(True)),
        )
        .outerjoin(CatalogSupplier, CatalogSupplier.id == CatalogProductListing.source_id)
        .where((similarity > 0.15) | like_hit)
        .order_by(similarity.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    results: list[ProductSearchResult] = []
    q_lower = q.lower()
    for row in rows:
        sim = float(row.sim or 0.0)
        name_lower = row.name.lower()
        if name_lower == q_lower:
            confidence = 1.0
        elif q_lower in name_lower or name_lower in q_lower:
            confidence = max(sim, 0.7)
        else:
            confidence = sim
        results.append(
            ProductSearchResult(
                product_id=row.id,
                name=row.name,
                supplier=row.supplier_name,
                sku=row.sku,
                confidence=round(confidence, 3),
            )
        )
    return results


async def _get_or_create_supplier(
    session: AsyncSession, name: str
) -> CatalogSupplier:
    """Return existing supplier by name or create it."""
    existing = (
        await session.execute(
            select(CatalogSupplier).where(CatalogSupplier.name == name)
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    supplier = CatalogSupplier(name=name)
    session.add(supplier)
    await session.flush()
    return supplier


async def find_or_create_product(
    session: AsyncSession,
    name: str,
    supplier_name: str | None = None,
) -> CatalogProduct:
    """Return an existing product (exact name match, case-insensitive) or create one.

    When creating, uses supplier_name if provided, otherwise the 'Unknown' supplier.
    Does NOT commit — the caller is responsible.
    """
    name = name.strip()

    # Exact name match first
    exact = (
        await session.execute(
            select(CatalogProduct).where(
                func.lower(CatalogProduct.name) == name.lower()
            )
        )
    ).scalar_one_or_none()
    if exact:
        return exact

    # Single ILIKE substring match
    ilike_rows = (
        await session.execute(
            select(CatalogProduct)
            .where(CatalogProduct.name.ilike(f"%{name}%"))
            .limit(2)
        )
    ).scalars().all()
    if len(ilike_rows) == 1:
        return ilike_rows[0]

    # Not found or ambiguous — create new product (listing created by caller in Package 3)
    product = CatalogProduct(name=name)
    session.add(product)
    await session.flush()
    return product
