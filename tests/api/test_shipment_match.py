"""End-to-end tests for POST /api/v1/shipment/match against the seeded DB.

Requires Postgres running (docker compose up postgres) and seed applied
(python scripts/seed_mvp.py). Skipped otherwise.
"""

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
    # Fresh engine per test to avoid cross-loop pool reuse under pytest-asyncio.
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


async def test_match_exact(client):
    r = await client.post(
        "/api/v1/shipment/match",
        json={"items": ["Veritas Shooting Board"]},
    )
    assert r.status_code == 200
    data = r.json()
    assert len(data["matched"]) == 1
    m = data["matched"][0]
    assert m["input_item"] == "Veritas Shooting Board"
    assert m["confidence"] >= 0.95
    assert m["order_id"] > 0
    assert m["customer_name"]


async def test_match_case_insensitive(client):
    r = await client.post(
        "/api/v1/shipment/match",
        json={"items": ["veritas shooting board"]},
    )
    data = r.json()
    assert len(data["matched"]) == 1
    assert data["matched"][0]["confidence"] >= 0.95


async def test_match_not_in_catalog(client):
    r = await client.post(
        "/api/v1/shipment/match",
        json={"items": ["Абсолютно несуществующий товар QWERTY12345"]},
    )
    data = r.json()
    assert len(data["unmatched"]) == 1
    assert data["unmatched"][0]["input_item"].startswith("Абсолютно")


async def test_match_ambiguous_partial_query(client):
    r = await client.post(
        "/api/v1/shipment/match",
        json={"items": ["стамеска"]},
    )
    data = r.json()
    total = len(data["matched"]) + len(data["ambiguous"]) + len(data["unmatched"])
    assert total == 1
    if data["ambiguous"]:
        cands = data["ambiguous"][0]["candidates"]
        assert all(0.0 <= c["confidence"] < 0.95 for c in cands)


async def test_match_priority_earliest_order(client):
    r = await client.post(
        "/api/v1/shipment/match",
        json={"items": ["Wonder Dog"]},
    )
    data = r.json()
    if data["matched"]:
        m = data["matched"][0]
        r2 = await client.get("/api/v1/orders/pending")
        pending_ids = [o["order_id"] for o in r2.json()]
        assert m["order_id"] in pending_ids


async def test_batch_shape(client):
    r = await client.post(
        "/api/v1/shipment/match",
        json={
            "items": [
                "Veritas Shooting Board",
                "Wonder Dog",
                "Абракадабра XYZ",
            ]
        },
    )
    data = r.json()
    assert "matched" in data and "ambiguous" in data and "unmatched" in data
    total = (
        len(data["matched"]) + len(data["ambiguous"]) + len(data["unmatched"])
    )
    assert total == 3
