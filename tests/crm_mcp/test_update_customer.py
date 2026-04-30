"""Tests for MCP tool: update_customer."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings

_CRM_MCP = Path(__file__).resolve().parent.parent.parent / "crm-mcp"
if str(_CRM_MCP) not in sys.path:
    sys.path.insert(0, str(_CRM_MCP))

from tools import update_customer  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with eng.connect() as conn:
            await conn.execute(text("SELECT 1 FROM orders_customer LIMIT 0"))
    except Exception as exc:
        await eng.dispose()
        pytest.skip(f"DB not available: {exc}")
    yield eng
    await eng.dispose()


@pytest.fixture
async def clean(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    async def _wipe() -> None:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM orders_customer"
                    " WHERE name LIKE 'TEST_UC_%'"
                    "    OR telegram_id LIKE 'tguc_%'"
                )
            )

    await _wipe()
    yield engine
    await _wipe()


@pytest.fixture
def session_factory(clean: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(clean, expire_on_commit=False)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _insert_customer(
    engine: AsyncEngine,
    *,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    telegram_id: str | None = None,
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO orders_customer (name, phone, email, telegram_id)"
                " VALUES (:n, :p, :e, :tg) RETURNING id"
            ),
            {"n": name, "p": phone, "e": email, "tg": telegram_id},
        )
        return int(row.scalar_one())


async def _get_customer(engine: AsyncEngine, cid: int) -> dict[str, Any] | None:
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT id, name, telegram_id, phone, email"
                " FROM orders_customer WHERE id = :cid"
            ),
            {"cid": cid},
        )
        m = row.mappings().first()
        return dict(m) if m else None


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_customer_not_found(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        result = await update_customer.run(session, customer_id=9_999_999)
    assert result["status"] == "error"
    assert result["error"] == "customer_not_found"
    assert result["customer_id"] == 9_999_999


async def test_no_fields_to_update(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    cid = await _insert_customer(clean, name="TEST_UC_NoUpdate", telegram_id="tguc_noupdate")
    async with session_factory() as session:
        result = await update_customer.run(session, customer_id=cid)
    assert result["status"] == "error"
    assert result["error"] == "no_fields_to_update"


async def test_telegram_id_conflict(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    owner_id = await _insert_customer(
        clean, name="TEST_UC_TgOwner", telegram_id="tguc_conflict_owner"
    )
    other_id = await _insert_customer(clean, name="TEST_UC_TgOther", telegram_id="tguc_other")

    async with session_factory() as session:
        result = await update_customer.run(
            session,
            customer_id=other_id,
            telegram_id="tguc_conflict_owner",
        )

    assert result["status"] == "error"
    assert result["error"] == "telegram_id_conflict"
    assert result["conflicting_customer_id"] == owner_id

    # Row must not be changed
    row = await _get_customer(clean, other_id)
    assert row is not None
    assert row["telegram_id"] == "tguc_other"


async def test_email_unique_collision(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    email = "tguc_collision@example.com"
    owner_id = await _insert_customer(
        clean,
        name="TEST_UC_EmailOwner",
        telegram_id="tguc_email_owner",
        email=email,
    )
    other_id = await _insert_customer(
        clean, name="TEST_UC_EmailOther", telegram_id="tguc_email_other"
    )

    async with session_factory() as session:
        result = await update_customer.run(
            session, customer_id=other_id, email=email
        )

    assert result["status"] == "error"
    assert result["error"] == "email_unique_collision"
    assert result["conflicting_customer_id"] == owner_id

    # SAVEPOINT rolled back — email of other_id must still be NULL
    row = await _get_customer(clean, other_id)
    assert row is not None
    assert row["email"] is None


async def test_happy_path_name_and_phone(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    cid = await _insert_customer(
        clean, name="TEST_UC_Original", telegram_id="tguc_happy"
    )

    async with session_factory() as session:
        result = await update_customer.run(
            session,
            customer_id=cid,
            name="TEST_UC_Updated",
            phone="+79001234567",
        )

    assert result["status"] == "ok"
    assert result["customer_id"] == cid
    assert result["name"] == "TEST_UC_Updated"
    assert result["phone"] == "+79001234567"
    assert set(result["updated_fields"]) == {"name", "phone"}

    # Verify actual DB state
    row = await _get_customer(clean, cid)
    assert row is not None
    assert row["name"] == "TEST_UC_Updated"
    assert row["phone"] == "+79001234567"
    assert row["telegram_id"] == "tguc_happy"  # unchanged


async def test_empty_string_normalised_to_none(
    clean: AsyncEngine, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    cid = await _insert_customer(
        clean, name="TEST_UC_EmptyStr", telegram_id="tguc_emptystr"
    )
    async with session_factory() as session:
        # All fields are empty strings → treated as None → no_fields_to_update
        result = await update_customer.run(
            session, customer_id=cid, name="", phone="", telegram_id="", email=""
        )
    assert result["status"] == "error"
    assert result["error"] == "no_fields_to_update"
