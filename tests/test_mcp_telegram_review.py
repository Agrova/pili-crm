"""Tests for MCP tools implementing ADR-010 Phase 3 moderation queue.

- tests 1–7:  get_unreviewed_chats
- tests 8–18: link_chat_to_customer

The tests drive the crm-mcp tools directly (no MCP transport) against the
real test Postgres (docker-compose pili-crm-postgres-1). Fixtures wipe the
Telegram tables and any test-created customers before and after each test,
matching the pattern established in tests/test_ingestion_tg_import.py.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

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

from tools import get_unreviewed_chats, link_chat_to_customer  # noqa: E402

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
async def review_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text("SELECT 1 FROM communications_telegram_chat LIMIT 0")
            )
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"DB not available for integration tests: {exc}")
    yield engine
    await engine.dispose()


@pytest.fixture
async def clean_review(review_engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Wipe Telegram tables and any test-created customers before and after.

    Customer cleanup keys: name prefix 'TEST_MCP_' or telegram_id prefix 'tgtest_'.
    """

    async def _wipe() -> None:
        async with review_engine.begin() as conn:
            # ON DELETE CASCADE drops messages and then communications_link rows.
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


async def _insert_chat(
    engine: AsyncEngine,
    *,
    telegram_chat_id: str,
    title: str | None = "TEST chat",
    review_status: str | None = "unreviewed",
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO communications_telegram_chat"
                " (owner_account_id, telegram_chat_id, chat_type, title,"
                "  review_status)"
                " VALUES (1, :tg, 'personal_chat', :title,"
                "         CAST(:rs AS telegram_chat_review_status))"
                " RETURNING id"
            ),
            {"tg": telegram_chat_id, "title": title, "rs": review_status},
        )
        return int(row.scalar_one())


async def _insert_message(
    engine: AsyncEngine,
    *,
    chat_id: int,
    telegram_message_id: str,
    sent_at: datetime,
    text_body: str | None,
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO communications_telegram_message"
                " (chat_id, telegram_message_id, sent_at, text)"
                " VALUES (:c, :tmi, :sa, :t) RETURNING id"
            ),
            {"c": chat_id, "tmi": telegram_message_id, "sa": sent_at, "t": text_body},
        )
        return int(row.scalar_one())


def _dt(y: int, m: int, d: int, h: int = 12, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=UTC)


# ─── 1 ── empty DB ───────────────────────────────────────────────────────────


async def test_unreviewed_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as s:
        r = await get_unreviewed_chats.run(s)
    assert r["status"] == "ok"
    assert r["chats"] == []


# ─── 2 ── only 'unreviewed' status surfaces ──────────────────────────────────


async def test_unreviewed_returns_only_unreviewed(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    cu = await _insert_chat(clean_review, telegram_chat_id="c1", review_status="unreviewed")
    await _insert_chat(clean_review, telegram_chat_id="c2", review_status="linked")
    await _insert_chat(clean_review, telegram_chat_id="c3", review_status="ignored")
    await _insert_chat(clean_review, telegram_chat_id="c4", review_status="new_customer")
    await _insert_chat(clean_review, telegram_chat_id="c5", review_status=None)

    async with session_factory() as s:
        r = await get_unreviewed_chats.run(s)

    ids = [c["chat_id"] for c in r["chats"]]
    assert ids == [cu]


# ─── 3 ── sort DESC by last_message_at ───────────────────────────────────────


async def test_unreviewed_sort_order(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    a = await _insert_chat(clean_review, telegram_chat_id="old")
    b = await _insert_chat(clean_review, telegram_chat_id="new")
    c = await _insert_chat(clean_review, telegram_chat_id="mid")

    await _insert_message(
        clean_review, chat_id=a, telegram_message_id="1",
        sent_at=_dt(2023, 1, 1), text_body="A",
    )
    await _insert_message(
        clean_review, chat_id=b, telegram_message_id="1",
        sent_at=_dt(2026, 4, 1), text_body="B",
    )
    await _insert_message(
        clean_review, chat_id=c, telegram_message_id="1",
        sent_at=_dt(2024, 6, 1), text_body="C",
    )

    async with session_factory() as s:
        r = await get_unreviewed_chats.run(s)

    ids = [ch["chat_id"] for ch in r["chats"]]
    assert ids == [b, c, a]


# ─── 4 ── preview takes earliest/latest TEXT messages (skips media-only) ─────


async def test_unreviewed_preview_text(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _insert_chat(clean_review, telegram_chat_id="p")

    # Earliest overall is media-only (text=NULL) → must be skipped in preview_first.
    await _insert_message(
        clean_review, chat_id=chat, telegram_message_id="1",
        sent_at=_dt(2024, 1, 1), text_body=None,
    )
    # Earliest TEXT message.
    await _insert_message(
        clean_review, chat_id=chat, telegram_message_id="2",
        sent_at=_dt(2024, 2, 1), text_body="Привет, интересует пила",
    )
    await _insert_message(
        clean_review, chat_id=chat, telegram_message_id="3",
        sent_at=_dt(2024, 3, 1), text_body="middle",
    )
    # Latest TEXT message (id=5 @ 2024-04-15).
    await _insert_message(
        clean_review, chat_id=chat, telegram_message_id="5",
        sent_at=_dt(2024, 4, 15), text_body="Спасибо, всё отлично!",
    )
    # Latest overall is media-only (2024-05-01) → must be skipped in preview_last.
    await _insert_message(
        clean_review, chat_id=chat, telegram_message_id="4",
        sent_at=_dt(2024, 5, 1), text_body=None,
    )

    async with session_factory() as s:
        r = await get_unreviewed_chats.run(s)

    assert len(r["chats"]) == 1
    c = r["chats"][0]
    assert c["preview_first"] == "Привет, интересует пила"
    assert c["preview_last"] == "Спасибо, всё отлично!"
    assert c["message_count"] == 5


# ─── 5 ── previews are None when chat has no text messages ───────────────────


async def test_unreviewed_preview_none_when_all_media(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _insert_chat(clean_review, telegram_chat_id="m")
    await _insert_message(
        clean_review, chat_id=chat, telegram_message_id="1",
        sent_at=_dt(2024, 1, 1), text_body=None,
    )
    await _insert_message(
        clean_review, chat_id=chat, telegram_message_id="2",
        sent_at=_dt(2024, 2, 1), text_body="",
    )

    async with session_factory() as s:
        r = await get_unreviewed_chats.run(s)

    assert len(r["chats"]) == 1
    c = r["chats"][0]
    assert c["preview_first"] is None
    assert c["preview_last"] is None
    assert c["message_count"] == 2


# ─── 6 ── long text truncated to 100 chars ───────────────────────────────────


async def test_unreviewed_preview_truncated_to_100(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat = await _insert_chat(clean_review, telegram_chat_id="t")
    long_body = "абвгд" * 50  # 250 chars
    await _insert_message(
        clean_review, chat_id=chat, telegram_message_id="1",
        sent_at=_dt(2024, 1, 1), text_body=long_body,
    )

    async with session_factory() as s:
        r = await get_unreviewed_chats.run(s)

    c = r["chats"][0]
    assert c["preview_first"] is not None
    assert len(c["preview_first"]) == 100
    assert c["preview_last"] is not None
    assert len(c["preview_last"]) == 100


# ─── 7 ── limit respected ────────────────────────────────────────────────────


async def test_unreviewed_limit_respected(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for i in range(5):
        ch = await _insert_chat(clean_review, telegram_chat_id=f"lim_{i}")
        await _insert_message(
            clean_review, chat_id=ch, telegram_message_id="1",
            sent_at=_dt(2024, i + 1, 1), text_body=f"t{i}",
        )

    async with session_factory() as s:
        r = await get_unreviewed_chats.run(s, limit=2)

    assert len(r["chats"]) == 2


# ═════════════════════════════════════════════════════════════════════════════
# link_chat_to_customer — tests 8–18
# ═════════════════════════════════════════════════════════════════════════════


async def _insert_customer(
    engine: AsyncEngine,
    *,
    name: str,
    telegram_id: str | None,
    phone: str | None = None,
) -> int:
    async with engine.begin() as conn:
        row = await conn.execute(
            text(
                "INSERT INTO orders_customer (name, telegram_id, phone)"
                " VALUES (:n, :tg, :ph) RETURNING id"
            ),
            {"n": name, "tg": telegram_id, "ph": phone},
        )
        return int(row.scalar_one())


async def _get_chat_status(engine: AsyncEngine, chat_id: int) -> str | None:
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT review_status::text FROM communications_telegram_chat"
                " WHERE id = :cid"
            ),
            {"cid": chat_id},
        )
        return row.scalar_one_or_none()


async def _get_customer_telegram_id(
    engine: AsyncEngine, customer_id: int
) -> str | None:
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT telegram_id FROM orders_customer WHERE id = :cid"),
            {"cid": customer_id},
        )
        return row.scalar_one_or_none()


async def _count_links_for_chat(engine: AsyncEngine, chat_id: int) -> int:
    async with engine.connect() as conn:
        row = await conn.execute(
            text(
                "SELECT COUNT(*) FROM communications_link l"
                "  JOIN communications_telegram_message m"
                "    ON m.id = l.telegram_message_id"
                " WHERE m.chat_id = :cid"
            ),
            {"cid": chat_id},
        )
        return int(row.scalar_one())


async def _count_customers_by_name(engine: AsyncEngine, name: str) -> int:
    async with engine.connect() as conn:
        row = await conn.execute(
            text("SELECT COUNT(*) FROM orders_customer WHERE name = :n"),
            {"n": name},
        )
        return int(row.scalar_one())


async def _seed_chat_with_messages(
    engine: AsyncEngine,
    *,
    telegram_chat_id: str,
    title: str | None = "TEST chat",
    n_messages: int = 3,
) -> int:
    chat_id = await _insert_chat(
        engine, telegram_chat_id=telegram_chat_id, title=title
    )
    for i in range(n_messages):
        await _insert_message(
            engine,
            chat_id=chat_id,
            telegram_message_id=str(i + 1),
            sent_at=_dt(2024, 1, i + 1),
            text_body=f"msg {i + 1}",
        )
    return chat_id


# ─── 8 ── mode validation: zero modes ────────────────────────────────────────


async def test_link_mode_validation_zero(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = await _seed_chat_with_messages(clean_review, telegram_chat_id="tgtest_8")
    async with session_factory() as s:
        with pytest.raises(ValueError, match="Ровно один из"):
            await link_chat_to_customer.run(s, chat_id=chat_id)

    assert await _get_chat_status(clean_review, chat_id) == "unreviewed"


# ─── 9 ── mode validation: multiple modes ────────────────────────────────────


async def test_link_mode_validation_multiple(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = await _seed_chat_with_messages(clean_review, telegram_chat_id="tgtest_9")
    cust_id = await _insert_customer(
        clean_review, name="TEST_MCP_multi", telegram_id="tgtest_multi"
    )
    async with session_factory() as s:
        with pytest.raises(ValueError, match="Ровно один из"):
            await link_chat_to_customer.run(
                s, chat_id=chat_id, customer_id=cust_id, create_new=True
            )

    assert await _get_chat_status(clean_review, chat_id) == "unreviewed"


# ─── 10 ── link to existing customer ─────────────────────────────────────────


async def test_link_to_existing_customer(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = await _seed_chat_with_messages(
        clean_review, telegram_chat_id="tgtest_10", n_messages=3
    )
    cust_id = await _insert_customer(
        clean_review, name="TEST_MCP_existing", telegram_id="tgtest_existing"
    )

    async with session_factory() as s:
        r = await link_chat_to_customer.run(s, chat_id=chat_id, customer_id=cust_id)

    assert r["status"] == "ok"
    assert r["action"] == "linked"
    assert r["customer_id"] == cust_id
    assert r["customer_name"] == "TEST_MCP_existing"
    assert r["messages_linked"] == 3

    assert await _get_chat_status(clean_review, chat_id) == "linked"
    assert await _count_links_for_chat(clean_review, chat_id) == 3


# ─── 11 ── backfill empty telegram_id on link ────────────────────────────────


async def test_link_updates_empty_telegram_id(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = await _seed_chat_with_messages(clean_review, telegram_chat_id="tgtest_11")
    cust_id = await _insert_customer(
        clean_review,
        name="TEST_MCP_empty_tg",
        telegram_id=None,
        phone="+70000000011",  # satisfy ck_orders_customer_contact
    )

    async with session_factory() as s:
        r = await link_chat_to_customer.run(s, chat_id=chat_id, customer_id=cust_id)

    assert r["status"] == "ok"
    assert r["telegram_id_updated"] is True
    assert r["telegram_id_conflict"] is None
    assert await _get_customer_telegram_id(clean_review, cust_id) == "tgtest_11"


# ─── 12 ── preserve existing telegram_id on mismatch (no overwrite) ──────────


async def test_link_preserves_existing_telegram_id_mismatch(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = await _seed_chat_with_messages(clean_review, telegram_chat_id="tgtest_12")
    cust_id = await _insert_customer(
        clean_review, name="TEST_MCP_mismatch", telegram_id="tgtest_original"
    )

    async with session_factory() as s:
        r = await link_chat_to_customer.run(s, chat_id=chat_id, customer_id=cust_id)

    assert r["status"] == "ok"
    assert r["action"] == "linked"
    assert r["telegram_id_updated"] is False
    assert r["telegram_id_conflict"] is None  # collision field is for another customer
    assert (
        await _get_customer_telegram_id(clean_review, cust_id) == "tgtest_original"
    )


# ─── 13 ── create_new with chat title ────────────────────────────────────────


async def test_link_create_new_with_title(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = await _seed_chat_with_messages(
        clean_review, telegram_chat_id="tgtest_13", title="TEST_MCP_Иван Петров"
    )

    async with session_factory() as s:
        r = await link_chat_to_customer.run(s, chat_id=chat_id, create_new=True)

    assert r["status"] == "ok"
    assert r["action"] == "new_customer"
    assert r["customer_name"] == "TEST_MCP_Иван Петров"
    assert r["messages_linked"] == 3
    assert r["telegram_id_updated"] is True

    assert await _get_chat_status(clean_review, chat_id) == "new_customer"
    assert (
        await _get_customer_telegram_id(clean_review, int(r["customer_id"]))
        == "tgtest_13"
    )
    assert await _count_links_for_chat(clean_review, chat_id) == 3


# ─── 14 ── create_new fallback name when title is empty ──────────────────────


async def test_link_create_new_without_title(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = await _seed_chat_with_messages(
        clean_review, telegram_chat_id="tgtest_14", title=None
    )

    async with session_factory() as s:
        r = await link_chat_to_customer.run(s, chat_id=chat_id, create_new=True)

    assert r["status"] == "ok"
    assert r["action"] == "new_customer"
    assert r["customer_name"] == "Telegram user tgtest_14"

    # Stub name starts with "Telegram " so clean_review won't wipe it; do it manually.
    async with clean_review.begin() as conn:
        await conn.execute(
            text("DELETE FROM orders_customer WHERE id = :cid"),
            {"cid": int(r["customer_id"])},
        )


# ─── 15 ── ignore mode: status flipped, no links created ─────────────────────


async def test_link_ignore_mode(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = await _seed_chat_with_messages(clean_review, telegram_chat_id="tgtest_15")

    async with session_factory() as s:
        r = await link_chat_to_customer.run(s, chat_id=chat_id, ignore=True)

    assert r["status"] == "ok"
    assert r["action"] == "ignored"
    assert r["customer_id"] is None
    assert r["customer_name"] is None
    assert r["messages_linked"] == 0
    assert r["telegram_id_updated"] is False
    assert r["telegram_id_conflict"] is None

    assert await _get_chat_status(clean_review, chat_id) == "ignored"
    assert await _count_links_for_chat(clean_review, chat_id) == 0


# ─── 16 ── reject re-processing an already-reviewed chat ─────────────────────


async def test_link_rejects_already_reviewed_chat(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    chat_id = await _insert_chat(
        clean_review, telegram_chat_id="tgtest_16", review_status="linked"
    )

    async with session_factory() as s:
        with pytest.raises(ValueError, match="уже обработан"):
            await link_chat_to_customer.run(s, chat_id=chat_id, ignore=True)

    assert await _get_chat_status(clean_review, chat_id) == "linked"


# ─── 17 ── atomicity: mid-transaction error rolls back every write ───────────


async def test_link_atomicity_on_error(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Another customer already owns the telegram_id the new customer would claim.
    # create_new will UPDATE chat status first, then INSERT customer (which trips
    # the partial UNIQUE on orders_customer.telegram_id). Rollback must reverse
    # the status flip.
    await _insert_customer(
        clean_review, name="TEST_MCP_owner", telegram_id="tgtest_17"
    )
    chat_id = await _seed_chat_with_messages(clean_review, telegram_chat_id="tgtest_17")

    async with session_factory() as s:
        r = await link_chat_to_customer.run(s, chat_id=chat_id, create_new=True)

    assert r["status"] == "error"
    assert "БД" in r["error"]

    assert await _get_chat_status(clean_review, chat_id) == "unreviewed"
    assert await _count_links_for_chat(clean_review, chat_id) == 0
    # Chat title is the default "TEST chat"; no new customer should have been made.
    assert await _count_customers_by_name(clean_review, "TEST chat") == 0


# ─── 18 ── telegram_id collision on linking to existing customer ─────────────


async def test_link_existing_customer_telegram_id_collision(
    clean_review: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner_id = await _insert_customer(
        clean_review, name="TEST_MCP_A", telegram_id="tgtest_18"
    )
    target_id = await _insert_customer(
        clean_review,
        name="TEST_MCP_B",
        telegram_id=None,
        phone="+70000000018",
    )
    chat_id = await _seed_chat_with_messages(clean_review, telegram_chat_id="tgtest_18")

    async with session_factory() as s:
        r = await link_chat_to_customer.run(s, chat_id=chat_id, customer_id=target_id)

    assert r["status"] == "ok"
    assert r["action"] == "linked"
    assert r["customer_id"] == target_id
    assert r["messages_linked"] == 3
    assert r["telegram_id_updated"] is False
    assert r["telegram_id_conflict"] == {
        "conflicting_customer_id": owner_id,
        "conflicting_telegram_id": "tgtest_18",
    }

    # Target customer's telegram_id must NOT have been overwritten.
    assert await _get_customer_telegram_id(clean_review, target_id) is None
    # Owner's telegram_id untouched.
    assert await _get_customer_telegram_id(clean_review, owner_id) == "tgtest_18"
    # Links for the chat ARE created — operator has resolved the chat.
    assert await _get_chat_status(clean_review, chat_id) == "linked"
    assert await _count_links_for_chat(clean_review, chat_id) == 3
