"""Тесты миграции и seed для communications_message_template."""
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_table_exists(db_session):
    """Таблица создана."""
    result = await db_session.execute(text(
        "SELECT to_regclass('communications_message_template')"
    ))
    assert result.scalar() is not None


@pytest.mark.asyncio
async def test_index_exists(db_session):
    """Индекс (code, language) создан."""
    result = await db_session.execute(text("""
        SELECT indexname FROM pg_indexes
        WHERE tablename = 'communications_message_template'
          AND indexname = 'ix_communications_message_template_code_language'
    """))
    assert result.scalar() is not None


@pytest.mark.asyncio
async def test_seed_quote_to_client(db_session):
    """Seed quote_to_client/ru существует и активен."""
    result = await db_session.execute(text("""
        SELECT code, language, is_active
        FROM communications_message_template
        WHERE code = 'quote_to_client' AND language = 'ru'
    """))
    row = result.mappings().first()
    assert row is not None
    assert row["is_active"] is True
    assert row["language"] == "ru"
