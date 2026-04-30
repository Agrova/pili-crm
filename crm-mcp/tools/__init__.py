"""MCP tool registry for crm-mcp."""

from __future__ import annotations

from tools import (
    add_to_stock,
    apply_identity_update,
    apply_pending_analysis,
    create_customer,
    create_order,
    find_customer,
    get_unreviewed_chats,
    link_chat_to_customer,
    list_customers,
    list_pending_identity_updates,
    match_shipment,
    pending_orders,
    search_products,
    update_customer,
    update_order,
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
    list_pending_identity_updates,
    apply_identity_update,
    update_customer,
    update_order,
    apply_pending_analysis,
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
    "list_pending_identity_updates",
    "apply_identity_update",
    "update_customer",
    "update_order",
    "apply_pending_analysis",
]
