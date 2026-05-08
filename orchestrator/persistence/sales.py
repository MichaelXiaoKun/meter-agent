"""Public sales-conversation persistence facade."""

from __future__ import annotations

from persistence.store_impl import (
    append_sales_messages,
    create_sales_conversation,
    create_sales_share,
    delete_sales_conversation,
    list_sales_conversations,
    load_sales_lead_summary,
    load_sales_messages,
    revoke_sales_share,
    sales_conversation_exists,
    set_sales_title,
    update_sales_lead_summary,
)

__all__ = [
    "append_sales_messages",
    "create_sales_conversation",
    "create_sales_share",
    "delete_sales_conversation",
    "list_sales_conversations",
    "load_sales_lead_summary",
    "load_sales_messages",
    "revoke_sales_share",
    "sales_conversation_exists",
    "set_sales_title",
    "update_sales_lead_summary",
]
