"""Tests for MCP identity-quarantine tools (ADR-011 X1).

- tests 1–4:  list_pending_identity_updates
- tests 5–13: apply_identity_update

Style mirrors tests/test_mcp_telegram_review.py: clean_review fixture wipes
Telegram tables (CASCADE drops quarantine rows that reference them) and any
customer with a TEST_MCP_-prefixed name or tgtest_-prefixed telegram_id.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
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

# Expose crm-mcp tools to the main test runner.
_CRM_MCP = Path(__file__).resolve().parent.parent / "crm-mcp"
if str(_CRM_MCP) not in sys.path:
    sys.path.insert(0, str(_CRM_MCP))

from tools import apply_identity_update, list_pending_identity_updates  # noqa: E402

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
async def review_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT 1 FROM analysis_extracted_identity LIMIT 0")
            )
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"DB not available for integration tests: {exc}")
    yield engine
    await engine.dispose()


@pytest.fixture
async def clean_review(review_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Wipe Telegram tables (cascades into quarantine) and TEST_MCP_ customers."""

    async def _wipe() -> None:
        async with review_engine.begin() as conn:
            await conn.execute(text("DELETE FROM communications_telegram_chat"))
            await conn.execute(
                text(
                    "DELETE FROM orders_customer"
                    " WHERE name LIKE 'TEST_MCP_%'"
                    "    OR telegram_id LIKE 'tgtest_%'"
                )
            )

    await _wipe()
    yield review_engine
    await _wipe()


@pytest.fixture
def session_factory(
    clean_review: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(clean_review, expire_on_commit=False)


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _insert_chat(engine: AsyncEngine, *, telegram_chat_id: str) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO communications_telegram_chat"
                " (owner_account_id, telegram_chat_id, chat_type, title,"
                "  review_status)"
                " VALUES (1, :tg, 'personal_chat', 'TEST chat',"
                "         CAST('unreviewed' AS telegram_chat_review_status))"
                " RETURNING id"
            ),
            {"tg": telegram_chat_id},
        )
        return int(row.scalar_one())


async def _insert_customer(
    engine: AsyncEngine,
    *,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    telegram_username: str | None = None,
    telegram_id: str | None = None,
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO orders_customer"
                " (name, phone, email, telegram_username, telegram_id)"
                " VALUES (:n, :p, :e, :tu, :tg) RETURNING id"
            ),
            {
                "n": name,
                "p": phone,
                "e": email,
                "tu": telegram_username,
                "tg": telegram_id,
            },
        )
        return int(row.scalar_one())


async def _insert_quarantine(
    engine: AsyncEngine,
    *,
    customer_id: int | None,
    chat_id: int,
    contact_type: str,
    value: str,
    confidence: str = "medium",
    status: str = "pending",
    extracted_at: datetime | None = None,
    analyzer_version: str = "test-v1",
    context_quote: str | None = None,
    applied_action: str | None = None,
) -> int:
    when = extracted_at or datetime(2026, 4, 29, 12, 0, tzinfo=UTC)
    # Satisfy ck_extracted_identity_pending_consistency for non-pending seeds.
    applied_at: datetime | None = None
    applied_by: str | None = None
    if status != "pending":
        applied_at = datetime(2026, 4, 29, 13, 0, tzinfo=UTC)
        applied_by = "operator"
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO analysis_extracted_identity"
                " (customer_id, chat_id, analyzer_version, extracted_at,"
                "  contact_type, value, confidence, context_quote, status,"
                "  applied_action, applied_by, applied_at)"
                " VALUES (:cid, :chid, :ver, :ext, :ct, :v, :cf, :cq, :st,"
                "         :aa, :ab, :at)"
                " RETURNING extracted_id"
            ),
            {
                "cid": customer_id,
                "chid": chat_id,
                "ver": analyzer_version,
                "ext": when,
                "ct": contact_type,
                "v": value,
                "cf": confidence,
                "cq": context_quote,
                "st": status,
                "aa": applied_action,
                "ab": applied_by,
                "at": applied_at,
            },
        )
        return int(row.scalar_one())


async def _get_quarantine(engine: AsyncEngine, eid: int) -> dict[str, Any] | None:
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT extracted_id, customer_id, chat_id, contact_type,"
                "  value, confidence, status, applied_action, applied_by,"
                "  applied_at"
                " FROM analysis_extracted_identity WHERE extracted_id = :eid"
            ),
            {"eid": eid},
        )
        m = row.mappings().first()
        return dict(m) if m else None


_ALLOWED_CUSTOMER_COLUMNS = {"name", "phone", "email", "telegram_username", "telegram_id"}


async def _get_customer_field(
    engine: AsyncEngine, customer_id: int, column: str
) -> str | None:
    if column not in _ALLOWED_CUSTOMER_COLUMNS:
        raise ValueError(f"Unexpected column in test helper: {column!r}")
    async with engine.connect() as conn:
        row = await conn.execute(
            text(f"SELECT {column} FROM orders_customer WHERE id = :cid"),  # noqa: S608
            {"cid": customer_id},
        )
        return row.scalar_one_or_none()


# ═════════════════════════════════════════════════════════════════════════════
# list_pending_identity_updates — tests 1–4
# ═════════════════════════════════════════════════════════════════════════════


# ─── 1 ── empty quarantine for an existing customer ─────────────────────────


async def test_list_pending_returns_empty_for_customer_with_no_quarantine(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cid = await _insert_customer(
        clean_review, name="TEST_MCP_alone", phone="+70000000001"
    )
    async with session_factory() as s:
        r = await list_pending_identity_updates.run(s, customer_id=cid)
    assert r["status"] == "ok"
    assert r["customer_id"] == cid
    assert r["customer_name"] == "TEST_MCP_alone"
    assert r["pending_count"] == 0
    assert r["pending_updates"] == []


# ─── 2 ── sort by confidence (high→medium→low), then extracted_at DESC ──────


async def test_list_pending_returns_quarantine_rows_sorted_by_confidence_then_date(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cid = await _insert_customer(
        clean_review, name="TEST_MCP_sort", phone="+70000000002"
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_sort")

    e_high_old = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="phone", value="+71",
        confidence="high",
        extracted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    e_high_new = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="email", value="x@y.test",
        confidence="high",
        extracted_at=datetime(2026, 4, 1, tzinfo=UTC),
    )
    e_med = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="city", value="Москва",
        confidence="medium",
        extracted_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    e_low = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="address", value="ул. Тестовая",
        confidence="low",
        extracted_at=datetime(2026, 2, 1, tzinfo=UTC),
    )

    async with session_factory() as s:
        r = await list_pending_identity_updates.run(s, customer_id=cid)

    assert r["pending_count"] == 4
    ids = [u["extracted_id"] for u in r["pending_updates"]]
    assert ids == [e_high_new, e_high_old, e_med, e_low]


# ─── 3 ── exclude applied/rejected ──────────────────────────────────────────


async def test_list_pending_excludes_applied_and_rejected(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cid = await _insert_customer(
        clean_review, name="TEST_MCP_filter", phone="+70000000003"
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_filter")

    pending_id = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="phone", value="+72", confidence="high",
    )
    await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="phone", value="+73", confidence="high",
        status="applied", applied_action="overwrite",
    )
    await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="phone", value="+74", confidence="high",
        status="rejected",
    )

    async with session_factory() as s:
        r = await list_pending_identity_updates.run(s, customer_id=cid)

    assert r["pending_count"] == 1
    assert r["pending_updates"][0]["extracted_id"] == pending_id


# ─── 4 ── nonexistent customer ──────────────────────────────────────────────


async def test_list_pending_returns_error_for_nonexistent_customer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        r = await list_pending_identity_updates.run(s, customer_id=999_999_999)
    assert r["status"] == "error"
    assert r["error"] == "customer_not_found"
    assert r["customer_id"] == 999_999_999


# ═════════════════════════════════════════════════════════════════════════════
# apply_identity_update — tests 5–13
# ═════════════════════════════════════════════════════════════════════════════


# ─── 5 ── overwrite phone ────────────────────────────────────────────────────


async def test_apply_overwrite_phone_writes_to_customer(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Customer has no phone yet — overwrite fills the empty column.
    cid = await _insert_customer(
        clean_review,
        name="TEST_MCP_phone",
        email="phone-fill@example.com",  # satisfy ck_orders_customer_contact
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_phone")
    eid = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="phone", value="+79039612273", confidence="high",
    )

    async with session_factory() as s:
        r = await apply_identity_update.run(s, extracted_id=eid, action="overwrite")

    assert r["status"] == "ok"
    assert r["action"] == "overwrite"
    assert r["applied_to_column"] == "phone"
    assert r["old_value"] is None
    assert r["new_value"] == "+79039612273"

    assert (
        await _get_customer_field(clean_review, cid, "phone") == "+79039612273"
    )
    q = await _get_quarantine(clean_review, eid)
    assert q is not None
    assert q["status"] == "applied"
    assert q["applied_action"] == "overwrite"
    assert q["applied_by"] == "operator"
    assert q["applied_at"] is not None


# ─── 6 ── name overwrite (NOT NULL — критичный кейс Kristina) ───────────────


async def test_apply_overwrite_name_writes_to_customer(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # NOT NULL column already filled with a placeholder name. Overwrite must
    # replace the existing value (destructive — Cowork must confirm).
    cid = await _insert_customer(
        clean_review,
        name="TEST_MCP_Telegram user 6544",
        telegram_id="tgtest_6544",
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_6544_chat")
    eid = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="name",
        value="TEST_MCP_Саргсян Кристина Степановна",
        confidence="high",
    )

    async with session_factory() as s:
        r = await apply_identity_update.run(s, extracted_id=eid, action="overwrite")

    assert r["status"] == "ok"
    assert r["applied_to_column"] == "name"
    assert r["old_value"] == "TEST_MCP_Telegram user 6544"
    assert r["new_value"] == "TEST_MCP_Саргсян Кристина Степановна"

    assert (
        await _get_customer_field(clean_review, cid, "name")
        == "TEST_MCP_Саргсян Кристина Степановна"
    )
    q = await _get_quarantine(clean_review, eid)
    assert q is not None
    assert q["status"] == "applied"
    assert q["applied_action"] == "overwrite"


# ─── 7 ── email collision: SAVEPOINT rollback, row stays pending ────────────


async def test_apply_overwrite_email_collision_rolls_back_savepoint(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Customer A already owns the contested email.
    a_id = await _insert_customer(
        clean_review,
        name="TEST_MCP_owner",
        email="collision@example.com",
        phone="+70000000077",
    )
    # Customer B has email=NULL; quarantine wants to overwrite with A's email.
    b_id = await _insert_customer(
        clean_review,
        name="TEST_MCP_target",
        phone="+70000000017",
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_email_coll")
    eid = await _insert_quarantine(
        clean_review, customer_id=b_id, chat_id=chat,
        contact_type="email", value="collision@example.com",
        confidence="high",
    )

    async with session_factory() as s:
        r = await apply_identity_update.run(s, extracted_id=eid, action="overwrite")

    # (a) structured error with conflicting_customer_id == A.id
    assert r["status"] == "error"
    assert r["error"] == "email_unique_collision"
    assert r["conflicting_customer_id"] == a_id

    # (b) Customer B's email NOT changed (still NULL).
    assert await _get_customer_field(clean_review, b_id, "email") is None
    # Customer A's email untouched.
    assert (
        await _get_customer_field(clean_review, a_id, "email")
        == "collision@example.com"
    )

    # (c) quarantine row still pending, no apply metadata stamped.
    q = await _get_quarantine(clean_review, eid)
    assert q is not None
    assert q["status"] == "pending"
    assert q["applied_action"] is None
    assert q["applied_by"] is None
    assert q["applied_at"] is None


# ─── 8 ── reject ─────────────────────────────────────────────────────────────


async def test_apply_reject_does_not_touch_customer(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cid = await _insert_customer(
        clean_review, name="TEST_MCP_reject", phone="+70000000008"
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_reject")
    eid = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="phone", value="+79991111111", confidence="high",
    )

    async with session_factory() as s:
        r = await apply_identity_update.run(s, extracted_id=eid, action="reject")

    assert r["status"] == "ok"
    assert r["action"] == "reject"

    # Customer.phone unchanged.
    assert (
        await _get_customer_field(clean_review, cid, "phone") == "+70000000008"
    )
    q = await _get_quarantine(clean_review, eid)
    assert q is not None
    assert q["status"] == "rejected"
    assert q["applied_action"] is None
    assert q["applied_by"] == "operator"
    assert q["applied_at"] is not None


# ─── 9 ── unknown action → structured error, no DB changes ──────────────────


async def test_apply_unknown_action_returns_error_without_changes(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cid = await _insert_customer(
        clean_review, name="TEST_MCP_unk", phone="+70000000009"
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_unk")
    eid = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="phone", value="+79991", confidence="high",
    )

    async with session_factory() as s:
        r = await apply_identity_update.run(
            s, extracted_id=eid, action="foobar"
        )

    assert r["status"] == "error"
    assert r["error"] == "unknown_action"
    assert r["received"] == "foobar"

    q = await _get_quarantine(clean_review, eid)
    assert q is not None
    assert q["status"] == "pending"
    assert (
        await _get_customer_field(clean_review, cid, "phone") == "+70000000009"
    )


# ─── 10 ── add_as_secondary → not_yet_implemented, no DB changes ────────────


async def test_apply_add_as_secondary_returns_not_implemented(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cid = await _insert_customer(
        clean_review, name="TEST_MCP_secd", phone="+70000000010"
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_secd")
    eid = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="phone", value="+79992", confidence="high",
    )

    async with session_factory() as s:
        r = await apply_identity_update.run(
            s, extracted_id=eid, action="add_as_secondary"
        )

    assert r["status"] == "error"
    assert r["error"] == "not_yet_implemented"

    q = await _get_quarantine(clean_review, eid)
    assert q is not None
    assert q["status"] == "pending"
    assert (
        await _get_customer_field(clean_review, cid, "phone") == "+70000000010"
    )


# ─── 11 ── apply on already-applied row → already_processed ─────────────────


async def test_apply_to_already_applied_record_raises(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cid = await _insert_customer(
        clean_review, name="TEST_MCP_done", phone="+70000000011"
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_done")
    eid = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="phone", value="+79993", confidence="high",
        status="applied", applied_action="overwrite",
    )

    async with session_factory() as s:
        r = await apply_identity_update.run(
            s, extracted_id=eid, action="overwrite"
        )

    assert r["status"] == "error"
    assert r["error"] == "already_processed"
    assert r["current_status"] == "applied"


# ─── 12 ── NULL customer_id → unlinked_chat_quarantine ──────────────────────


async def test_apply_to_quarantine_with_null_customer_raises(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Unreviewed-chat scenario: identity extracted before chat was linked.
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_orphan")
    eid = await _insert_quarantine(
        clean_review, customer_id=None, chat_id=chat,
        contact_type="phone", value="+79994", confidence="high",
    )

    async with session_factory() as s:
        r = await apply_identity_update.run(
            s, extracted_id=eid, action="overwrite"
        )

    assert r["status"] == "error"
    assert r["error"] == "unlinked_chat_quarantine"
    assert r["chat_id"] == chat
    assert r["extracted_id"] == eid

    q = await _get_quarantine(clean_review, eid)
    assert q is not None
    assert q["status"] == "pending"


# ─── 13 ── city → no_target_column, no DB changes ───────────────────────────


async def test_apply_overwrite_no_target_column_for_city(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cid = await _insert_customer(
        clean_review, name="TEST_MCP_city", phone="+70000000013"
    )
    chat = await _insert_chat(clean_review, telegram_chat_id="tgtest_city")
    eid = await _insert_quarantine(
        clean_review, customer_id=cid, chat_id=chat,
        contact_type="city", value="Москва", confidence="high",
    )

    async with session_factory() as s:
        r = await apply_identity_update.run(
            s, extracted_id=eid, action="overwrite"
        )

    assert r["status"] == "error"
    assert r["error"] == "no_target_column"
    assert r["contact_type"] == "city"

    q = await _get_quarantine(clean_review, eid)
    assert q is not None
    assert q["status"] == "pending"
