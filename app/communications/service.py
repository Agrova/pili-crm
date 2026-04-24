"""Public service layer for the communications module (ADR-012).

All public functions return Pydantic schemas (or plain dicts for enriched
views), never ORM objects. Session lifecycle — commit/rollback — is the
responsibility of the caller, except for the account-mutation helpers
(`create_account`, `update_account_timestamps`) which `commit()` before
returning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.exceptions import MultipleCustomersForChatError
from app.communications import repository
from app.communications.models import (
    CommunicationsLink,
    CommunicationsLinkTargetModule,
    CommunicationsTelegramMessage,
)
from app.communications.schemas import (
    TelegramAccountCreate,
    TelegramAccountRead,
)


async def get_account_by_phone(
    session: AsyncSession, phone: str
) -> TelegramAccountRead | None:
    account = await repository.get_account_by_phone(session, phone)
    return TelegramAccountRead.model_validate(account) if account else None


async def list_accounts(session: AsyncSession) -> list[TelegramAccountRead]:
    accounts = await repository.list_accounts(session)
    return [TelegramAccountRead.model_validate(a) for a in accounts]


async def create_account(
    session: AsyncSession,
    phone: str,
    display_name: str,
    notes: str | None = None,
    telegram_user_id: str | None = None,
) -> TelegramAccountRead:
    data = TelegramAccountCreate(
        phone_number=phone,
        display_name=display_name,
        notes=notes,
        telegram_user_id=telegram_user_id,
    )
    account = await repository.create_account(session, data)
    await session.commit()
    await session.refresh(account)
    return TelegramAccountRead.model_validate(account)


async def update_account_timestamps(
    session: AsyncSession,
    account_id: int,
    telegram_user_id: str | None = None,
) -> None:
    """Record an import run on the account.

    `last_import_at` is always bumped to now. `first_import_at` is filled only
    when currently NULL — i.e. preserved across subsequent runs.
    `telegram_user_id` is filled only when currently NULL — repository enforces
    this, the service just forwards.
    """
    now = datetime.now(UTC)
    await repository.update_account_timestamps(
        session,
        account_id=account_id,
        first_import_at=now,
        last_import_at=now,
        telegram_user_id=telegram_user_id,
    )
    await session.commit()


async def get_customer_for_chat(
    session: AsyncSession, chat_id: int
) -> int | None:
    """Return the customer id a chat is linked to, or ``None`` if no link.

    Links live on messages (ADR-003 / ADR-010): the join chases
    ``communications_link`` rows with
    ``target_module='orders'`` / ``target_entity='orders_customer'`` back
    through ``communications_telegram_message`` to the chat.

    Raises ``MultipleCustomersForChatError`` when ≥2 distinct customer ids
    link to the same chat — callers in ``app/analysis/service.py`` catch
    this and surface the ids via ``AnalysisApplicationResult``.
    """
    stmt = (
        select(CommunicationsLink.target_id)
        .join(
            CommunicationsTelegramMessage,
            CommunicationsTelegramMessage.id == CommunicationsLink.telegram_message_id,
        )
        .where(
            CommunicationsTelegramMessage.chat_id == chat_id,
            CommunicationsLink.target_module == CommunicationsLinkTargetModule.orders,
            CommunicationsLink.target_entity == "orders_customer",
        )
        .distinct()
    )
    ids = [int(row) for row in (await session.execute(stmt)).scalars()]
    if not ids:
        return None
    if len(ids) > 1:
        raise MultipleCustomersForChatError(chat_id=chat_id, customer_ids=ids)
    return ids[0]


async def list_chats_by_customer(
    session: AsyncSession, customer_id: int
) -> list[dict[str, Any]]:
    """Every Telegram chat a customer appears in, across all accounts.

    Returned dicts carry the owner account's display name and phone plus a
    `message_count` — exactly the shape needed for the `existing_channels`
    field of `link_chat_to_customer` (ADR-012 §8).
    """
    chats = await repository.list_chats_by_customer(session, customer_id)
    out: list[dict[str, Any]] = []
    for chat in chats:
        message_count = await repository.count_messages_in_chat(session, chat.id)
        out.append(
            {
                "chat_id": chat.id,
                "telegram_chat_id": chat.telegram_chat_id,
                "title": chat.title,
                "owner_account_id": chat.owner_account_id,
                "owner_account_display_name": chat.owner_account.display_name,
                "owner_account_phone": chat.owner_account.phone_number,
                "message_count": message_count,
            }
        )
    return out
