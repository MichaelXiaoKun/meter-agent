"""Ticket/work-item persistence facade."""

from __future__ import annotations

from persistence.store_impl import (
    append_ticket_event,
    create_ticket,
    get_ticket,
    list_ticket_events,
    list_tickets,
    update_ticket,
)

__all__ = [
    "append_ticket_event",
    "create_ticket",
    "get_ticket",
    "list_ticket_events",
    "list_tickets",
    "update_ticket",
]
