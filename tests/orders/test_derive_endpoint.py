"""Tests for POST /api/v1/orders/{order_id}/derive-status."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.api.deps import get_db
from app.config import settings
from app.main import app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as c:
            r = await c.get("/api/v1/customers")
            if r.status_code != 200 or len(r.json()) == 0:
                pytest.skip("DB not seeded — run scripts/seed_mvp.py")
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


async def _first_order_id(client: httpx.AsyncClient) -> int:
    r = await client.get("/api/v1/orders/pending")
    assert r.status_code == 200
    orders = r.json()
    assert orders, "No pending orders found — run scripts/seed_mvp.py"
    return orders[0]["order_id"]


async def test_derive_status_endpoint(client: httpx.AsyncClient) -> None:
    order_id = await _first_order_id(client)
    r = await client.post(f"/api/v1/orders/{order_id}/derive-status")
    assert r.status_code == 200
    data = r.json()
    assert data["order_id"] == order_id
    assert "old_status" in data
    assert "new_status" in data
    assert data["new_status"]  # non-empty string


async def test_derive_status_not_found(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/v1/orders/999999999/derive-status")
    assert r.status_code == 404
