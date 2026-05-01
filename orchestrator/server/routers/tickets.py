"""Native admin ticket route handlers."""

from __future__ import annotations

from server.app import (
    create_ticket,
    create_ticket_event,
    list_tickets,
    update_ticket,
)

__all__ = [
    "create_ticket",
    "create_ticket_event",
    "list_tickets",
    "update_ticket",
]
