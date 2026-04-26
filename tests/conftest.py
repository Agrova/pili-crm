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


def pytest_configure(config: pytest.Config) -> None:
    """Safety guard: refuse to run tests on prod DB."""
    test_url = settings.test_database_url

    if not test_url:
        pytest.exit(
            "TEST_DATABASE_URL is not set. Run scripts/setup_test_db.sh first.",
            returncode=2,
        )
    if test_url == settings.database_url:
        pytest.exit(
            f"REFUSING TO RUN: TEST_DATABASE_URL == DATABASE_URL ({test_url}). "
            "Tests would wipe production data.",
            returncode=2,
        )
    if "test" not in test_url.lower():
        pytest.exit(
            f"REFUSING TO RUN: TEST_DATABASE_URL ({test_url}) does not contain 'test'. "
            "Safety guard against typos.",
            returncode=2,
        )


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Async DB session that rolls back all writes after each test.

    Repository functions must use flush() (not commit()) for rollback to work.
    """
    assert settings.test_database_url  # guarded by pytest_configure above
    engine = create_async_engine(settings.test_database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM orders_customer"))
        ).scalar()
        if not count:
            await engine.dispose()
            pytest.skip("DB not seeded — run scripts/setup_test_db.sh")
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()
