"""Shared fixtures for all tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Async DB session that rolls back all writes after each test.

    Repository functions must use flush() (not commit()) for rollback to work.
    """
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM orders_customer"))
        ).scalar()
        if not count:
            await engine.dispose()
            pytest.skip("DB not seeded — run python3 -m scripts.seed_mvp")
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()
