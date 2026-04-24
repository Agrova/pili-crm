"""ADR-012 Task 1: multi-account Telegram — service layer tests.

Public functions in `app.communications.service` return Pydantic schemas
(not ORM objects). Timestamp-mutating functions commit, so each test that
writes new rows cleans up explicitly in a finally block.
"""

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

from app.communications import service
from app.communications.schemas import TelegramAccountRead
from app.config import settings


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Fresh async session. Individual tests manage their own cleanup."""
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _delete_account(session: AsyncSession, phone: str) -> None:
    await session.execute(
        text(
            "DELETE FROM communications_telegram_account WHERE phone_number = :p"
        ),
        {"p": phone},
    )
    await session.commit()


async def test_create_account(session: AsyncSession) -> None:
    phone = "+12025550100"
    try:
        read = await service.create_account(
            session,
            phone=phone,
            display_name="Test account",
            notes="Service-layer test",
        )
        assert isinstance(read, TelegramAccountRead)
        assert read.phone_number == phone
        assert read.display_name == "Test account"
        assert read.notes == "Service-layer test"
        assert read.id > 0
        assert read.created_at is not None
        assert read.first_import_at is None
        assert read.last_import_at is None
    finally:
        await _delete_account(session, phone)


async def test_get_account_by_phone(session: AsyncSession) -> None:
    # Seed account from the migration
    found = await service.get_account_by_phone(session, "+77471057849")
    assert found is not None
    assert found.display_name == "Казахстан (+77471057849)"

    missing = await service.get_account_by_phone(session, "+19999999999")
    assert missing is None


async def test_update_timestamps_first_call(session: AsyncSession) -> None:
    phone = "+12025550101"
    try:
        created = await service.create_account(
            session,
            phone=phone,
            display_name="Timestamps test",
        )
        assert created.first_import_at is None
        assert created.last_import_at is None

        await service.update_account_timestamps(
            session,
            account_id=created.id,
            telegram_user_id="987654321",
        )

        after = await service.get_account_by_phone(session, phone)
        assert after is not None
        assert after.first_import_at is not None
        assert after.last_import_at is not None
        assert after.telegram_user_id == "987654321"
    finally:
        await _delete_account(session, phone)


async def test_list_chats_by_customer_across_accounts(session: AsyncSession) -> None:
    """A customer linked to chats in two accounts — both are returned,
    each enriched with the owner's display_name and phone."""
    phone2 = "+12025550102"
    seed_account_id = 1  # Kazakhstan seed from migration
    account2_id: int | None = None
    customer_id: int | None = None
    chat1_id: int | None = None
    chat2_id: int | None = None
    msg1_id: int | None = None
    msg2_id: int | None = None
    try:
        # Second account
        read2 = await service.create_account(
            session,
            phone=phone2,
            display_name="Second test (+12025550102)",
        )
        account2_id = read2.id

        # Create customer (no FK dependency on telegram data)
        customer_id = (
            await session.execute(
                text(
                    "INSERT INTO orders_customer (name, telegram_id) "
                    "VALUES ('ADR-012 test customer', '999000111') "
                    "RETURNING id"
                )
            )
        ).scalar_one()

        chat1_id = (
            await session.execute(
                text(
                    "INSERT INTO communications_telegram_chat "
                    "(owner_account_id, telegram_chat_id, chat_type, title) "
                    "VALUES (:acc, 'svc-cross-1', 'private', 'Chat KZ') "
                    "RETURNING id"
                ),
                {"acc": seed_account_id},
            )
        ).scalar_one()
        chat2_id = (
            await session.execute(
                text(
                    "INSERT INTO communications_telegram_chat "
                    "(owner_account_id, telegram_chat_id, chat_type, title) "
                    "VALUES (:acc, 'svc-cross-2', 'private', 'Chat RU') "
                    "RETURNING id"
                ),
                {"acc": account2_id},
            )
        ).scalar_one()

        # One message per chat (links are at message level)
        msg1_id = (
            await session.execute(
                text(
                    "INSERT INTO communications_telegram_message "
                    "(chat_id, telegram_message_id, sent_at, text) "
                    "VALUES (:chat, 'm-1', now(), 'hi') "
                    "RETURNING id"
                ),
                {"chat": chat1_id},
            )
        ).scalar_one()
        msg2_id = (
            await session.execute(
                text(
                    "INSERT INTO communications_telegram_message "
                    "(chat_id, telegram_message_id, sent_at, text) "
                    "VALUES (:chat, 'm-2', now(), 'hi 2') "
                    "RETURNING id"
                ),
                {"chat": chat2_id},
            )
        ).scalar_one()

        # Links: both messages → same customer
        for msg in (msg1_id, msg2_id):
            await session.execute(
                text(
                    "INSERT INTO communications_link "
                    "(telegram_message_id, target_module, target_entity, "
                    "target_id, link_confidence) "
                    "VALUES (:m, 'orders', 'orders_customer', :c, 'manual')"
                ),
                {"m": msg, "c": customer_id},
            )
        await session.commit()

        result = await service.list_chats_by_customer(session, customer_id)
        assert len(result) == 2
        by_chat = {r["chat_id"]: r for r in result}
        assert chat1_id in by_chat
        assert chat2_id in by_chat
        assert by_chat[chat1_id]["owner_account_display_name"] == (
            "Казахстан (+77471057849)"
        )
        assert by_chat[chat1_id]["owner_account_phone"] == "+77471057849"
        assert by_chat[chat2_id]["owner_account_display_name"] == (
            "Second test (+12025550102)"
        )
        assert by_chat[chat2_id]["owner_account_phone"] == phone2
    finally:
        # Clean up children before the account
        if customer_id is not None:
            await session.execute(
                text(
                    "DELETE FROM communications_link "
                    "WHERE target_module = 'orders' "
                    "AND target_entity = 'orders_customer' "
                    "AND target_id = :c"
                ),
                {"c": customer_id},
            )
        for chat in (chat1_id, chat2_id):
            if chat is not None:
                await session.execute(
                    text(
                        "DELETE FROM communications_telegram_message "
                        "WHERE chat_id = :c"
                    ),
                    {"c": chat},
                )
                await session.execute(
                    text(
                        "DELETE FROM communications_telegram_chat WHERE id = :c"
                    ),
                    {"c": chat},
                )
        if customer_id is not None:
            await session.execute(
                text("DELETE FROM orders_customer WHERE id = :c"),
                {"c": customer_id},
            )
        await session.commit()
        await _delete_account(session, phone2)
