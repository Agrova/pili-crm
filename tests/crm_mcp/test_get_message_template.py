"""Тесты MCP-tool get_message_template."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CRM_MCP = Path(__file__).resolve().parent.parent.parent / "crm-mcp"
if str(_CRM_MCP) not in sys.path:
    sys.path.insert(0, str(_CRM_MCP))

from tools.get_message_template import run  # noqa: E402


@pytest.mark.asyncio
async def test_get_existing_template(db_session):
    """Возвращает quote_to_client/ru."""
    result = await run(db_session, code="quote_to_client", language="ru")
    assert result["found"] is True
    assert result["code"] == "quote_to_client"
    assert "{customer_name}" in result["body_template"]
    assert "{items_block}" in result["body_template"]
    assert "{total_rub}" in result["body_template"]


@pytest.mark.asyncio
async def test_get_nonexistent_template(db_session):
    """Несуществующий код → template_not_found."""
    result = await run(db_session, code="non_existent_code_xyz")
    assert result["found"] is False
    assert result["error"] == "template_not_found"


@pytest.mark.asyncio
async def test_get_default_language_is_ru(db_session):
    """Дефолтный язык — ru."""
    result = await run(db_session, code="quote_to_client")
    assert result["found"] is True
    assert result["language"] == "ru"


@pytest.mark.asyncio
async def test_get_english_not_found(db_session):
    """Английский seed не существует → template_not_found."""
    result = await run(db_session, code="quote_to_client", language="en")
    assert result["found"] is False
    assert result["error"] == "template_not_found"


@pytest.mark.asyncio
async def test_empty_code_returns_error(db_session):
    """Пустой code → ошибка без падения."""
    result = await run(db_session, code="")
    assert result["found"] is False
    assert result["error"] == "template_not_found"
