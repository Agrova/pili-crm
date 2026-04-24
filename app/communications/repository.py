"""Repository layer for the communications module (ADR-012).

ORM-level CRUD for `TelegramAccount` and cross-account chat lookup. Higher-level
policy (Pydantic validation, session commit) lives in `service.py`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.communications.models import (
    CommunicationsLink,
    CommunicationsLinkTargetModule,
    CommunicationsTelegramAccount,
    CommunicationsTelegramChat,
    CommunicationsTelegramMessage,
)
from app.communications.schemas import TelegramAccountCreate


async def get_account_by_id(
    session: AsyncSession, account_id: int
) -> CommunicationsTelegramAccount | None:
    return await session.get(CommunicationsTelegramAccount, account_id)


async def get_account_by_phone(
    session: AsyncSession, phone: str
) -> CommunicationsTelegramAccount | None:
    result = await session.execute(
        select(CommunicationsTelegramAccount).where(
            CommunicationsTelegramAccount.phone_number == phone
        )
    )
    return result.scalar_one_or_none()


async def list_accounts(
    session: AsyncSession,
) -> list[CommunicationsTelegramAccount]:
    result = await session.execute(
        select(CommunicationsTelegramAccount).order_by(
            CommunicationsTelegramAccount.id
        )
    )
    return list(result.scalars().all())


async def create_account(
    session: AsyncSession, data: TelegramAccountCreate
) -> CommunicationsTelegramAccount:
    account = CommunicationsTelegramAccount(
        phone_number=data.phone_number,
        display_name=data.display_name,
        notes=data.notes,
        telegram_user_id=data.telegram_user_id,
    )
    session.add(account)
    await session.flush()
    await session.refresh(account)
    return account


async def update_account_timestamps(
    session: AsyncSession,
    account_id: int,
    first_import_at: datetime | None = None,
    last_import_at: datetime | None = None,
    telegram_user_id: str | None = None,
) -> CommunicationsTelegramAccount:
    account = await session.get(CommunicationsTelegramAccount, account_id)
    if account is None:
        raise ValueError(f"TelegramAccount id={account_id} not found")
    if first_import_at is not None and account.first_import_at is None:
        account.first_import_at = first_import_at
    if last_import_at is not None:
        account.last_import_at = last_import_at
    if telegram_user_id is not None and account.telegram_user_id is None:
        account.telegram_user_id = telegram_user_id
    await session.flush()
    await session.refresh(account)
    return account


async def list_chats_by_customer(
    session: AsyncSession, customer_id: int
) -> list[CommunicationsTelegramChat]:
    """All Telegram chats tied to a customer via communications_link.

    Links are stored at the message level (ADR-003 / ADR-010): join
    communications_link → telegram_message → telegram_chat, deduplicated by
    chat id. Only links with target_module=orders and target_entity=
    orders_customer count.
    """
    stmt = (
        select(CommunicationsTelegramChat)
        .join(
            CommunicationsTelegramMessage,
            CommunicationsTelegramMessage.chat_id == CommunicationsTelegramChat.id,
        )
        .join(
            CommunicationsLink,
            CommunicationsLink.telegram_message_id == CommunicationsTelegramMessage.id,
        )
        .where(
            CommunicationsLink.target_module == CommunicationsLinkTargetModule.orders,
            CommunicationsLink.target_entity == "orders_customer",
            CommunicationsLink.target_id == customer_id,
        )
        .options(selectinload(CommunicationsTelegramChat.owner_account))
        .distinct()
        .order_by(CommunicationsTelegramChat.id)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
