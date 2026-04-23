"""Tests for create_customer repository function."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.orders.repository import create_customer, find_customers


async def test_create_minimal(db_session: AsyncSession) -> None:
    """Create customer with only a name; placeholder telegram_id generated."""
    cust = await create_customer(db_session, name="Тест Тестович")
    assert cust.id is not None
    assert cust.name == "Тест Тестович"
    # At least one contact must exist (DB constraint satisfied via placeholder)
    assert cust.telegram_id is not None or cust.phone is not None or cust.email is not None


async def test_create_with_telegram(db_session: AsyncSession) -> None:
    cust = await create_customer(
        db_session, name="Сергей Деревянный", telegram_id="@sergey_wood_test"
    )
    assert cust.id is not None
    assert cust.telegram_id == "@sergey_wood_test"


async def test_create_with_phone(db_session: AsyncSession) -> None:
    cust = await create_customer(
        db_session, name="Иван Рубанков", phone="+79161234501"
    )
    assert cust.id is not None
    assert cust.phone == "+79161234501"


async def test_telegram_link_generation(db_session: AsyncSession) -> None:
    """@handle should produce https://t.me/handle link in find_customers."""
    await create_customer(
        db_session, name="Линк Тест", telegram_id="@link_test_handle"
    )
    # After flush, customer is in DB (within this transaction)
    results = await find_customers(db_session, "@link_test_handle")
    assert results, "Just-created customer should be findable"
    assert results[0].telegram_link == "https://t.me/link_test_handle"


async def test_auto_placeholder_when_no_contact(db_session: AsyncSession) -> None:
    """When no contact supplied, an @auto_... placeholder is created."""
    cust = await create_customer(db_session, name="Без Контакта")
    assert cust.telegram_id is not None
    assert cust.telegram_id.startswith("@auto_")


async def test_create_empty_name_raises(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="empty"):
        await create_customer(db_session, name="")
