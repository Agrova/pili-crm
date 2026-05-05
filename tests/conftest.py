"""Shared fixtures for all tests."""

from __future__ import annotations

import os
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

# Force-load all ORM models so Base.metadata is fully populated at collection
# time. Without this, running a single test file in isolation may miss models
# from other modules (e.g. pricing) that are only imported transitively, causing
# "NoReferencedTableError" or incomplete CREATE TABLE in the test schema.
import app.analysis.models  # noqa: F401, E402
import app.catalog.models  # noqa: F401, E402
import app.communications.models  # noqa: F401, E402
import app.finance.models  # noqa: F401, E402
import app.orders.models  # noqa: F401, E402
import app.pricing.models  # noqa: F401, E402
import app.procurement.models  # noqa: F401, E402
import app.warehouse.models  # noqa: F401, E402


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

    # Two-layer protection: tests that bypass db_session fixture and use
    # settings.database_url or subprocess(alembic) directly should also
    # land on the test DB. See hotfix #3 (root cause of 2026-04-26 wipe).
    settings.database_url = test_url
    os.environ["DATABASE_URL"] = test_url


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Async DB session that rolls back all writes after each test.

    Isolation strategy: rollback (not truncate).
    - Why rollback: no DDL lock, faster than truncate, works with nested
      savepoints. Each test gets a clean slate without touching other tests.
    - Constraint: repository functions must use flush() not commit(); a commit
      would persist data outside the transaction and break rollback isolation.
    - Writing a new test: `async def test_foo(db_session: AsyncSession): ...`
      The fixture handles setup and teardown automatically.
    - Running one test: `python3 -m pytest tests/path/test_file.py::test_name -v`
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
