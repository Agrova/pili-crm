"""MCP tool registry for crm-mcp."""

from __future__ import annotations

from tools import (
    add_to_stock,
    create_customer,
    create_order,
    find_customer,
    get_unreviewed_chats,
    link_chat_to_customer,
    list_customers,
    match_shipment,
    pending_orders,
    search_products,
    update_order_item_status,
)

TOOLS = (
    match_shipment,
    pending_orders,
    list_customers,
    search_products,
    add_to_stock,
    update_order_item_status,
    find_customer,
    create_customer,
    create_order,
    get_unreviewed_chats,
    link_chat_to_customer,
)

__all__ = [
    "TOOLS",
    "match_shipment",
    "pending_orders",
    "list_customers",
    "search_products",
    "add_to_stock",
    "update_order_item_status",
    "find_customer",
    "create_customer",
    "create_order",
    "get_unreviewed_chats",
    "link_chat_to_customer",
]
