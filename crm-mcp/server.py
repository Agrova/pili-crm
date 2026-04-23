"""MCP stdio server for ПилиСтрогай CRM (read-only).

stdout is reserved for the MCP protocol — everything else goes to stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from db import dispose, get_session, setup_logging
from tools import TOOLS

logger = logging.getLogger("crm-mcp.server")

server: Server = Server("crm-pilistrogai")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name=mod.NAME,
            description=mod.DESCRIPTION,
            inputSchema=mod.INPUT_SCHEMA,
        )
        for mod in TOOLS
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict[str, Any] | None
) -> list[TextContent]:
    args = arguments or {}
    mod = next((t for t in TOOLS if name == t.NAME), None)
    if mod is None:
        return [TextContent(type="text", text=f"Неизвестный инструмент: {name}")]

    try:
        async with get_session() as session:
            result = await mod.run(session, **args)
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return [
            TextContent(
                type="text",
                text=(
                    f"Ошибка при обращении к БД ({type(exc).__name__}). "
                    "Проверьте, что Postgres запущен и DATABASE_URL корректен."
                ),
            )
        ]

    text = mod.format_text(result)
    payload = json.dumps(result, ensure_ascii=False, default=str)
    return [
        TextContent(type="text", text=text),
        TextContent(type="text", text=f"```json\n{payload}\n```"),
    ]


async def _run() -> None:
    setup_logging()
    logger.info("crm-mcp starting on stdio transport")
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        await dispose()
        logger.info("crm-mcp stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
