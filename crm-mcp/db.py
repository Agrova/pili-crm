"""DB plumbing for crm-mcp.

Independent of app/ — uses raw text() queries, no ORM models duplicated.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger("crm-mcp.db")

_DEFAULT_URL = "postgresql+asyncpg://pili:pili@localhost:5432/pili_crm"
DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT_URL)

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def _init() -> async_sessionmaker[AsyncSession]:
    global _engine, _factory
    if _factory is None:
        logger.info("Initializing DB engine → %s", _redact(DATABASE_URL))
        _engine = create_async_engine(DATABASE_URL, echo=False)
        _factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _factory


def _redact(url: str) -> str:
    # hide password in logs
    if "@" in url and "://" in url:
        head, tail = url.split("://", 1)
        creds, host = tail.split("@", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{head}://{user}:***@{host}"
    return url


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Async context manager yielding an AsyncSession.

    On DB errors, logs to stderr and re-raises. Callers are responsible for
    catching and returning user-friendly messages.
    """
    factory = _init()
    try:
        async with factory() as session:
            yield session
    except Exception:
        logger.exception("DB session error")
        raise


async def dispose() -> None:
    global _engine, _factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _factory = None


def setup_logging(level: int = logging.INFO) -> None:
    """Configure stderr-only logging. stdout is reserved for MCP protocol."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
