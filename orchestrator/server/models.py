"""Request models exposed by the FastAPI server.

For this compatibility refactor, the canonical classes still live in
``server.app`` so route behavior and imports stay unchanged.
"""

from __future__ import annotations

from server.app import (
    ChatRequest,
    CreateConversationRequest,
    CreateShareRequest,
    CreateTicketEventRequest,
    CreateTicketRequest,
    ForgotPasswordRequest,
    LoginRequest,
    SalesChatRequest,
    SalesConversationRequest,
    UpdateTicketRequest,
    UpdateTitleRequest,
)

__all__ = [
    "ChatRequest",
    "CreateConversationRequest",
    "CreateShareRequest",
    "CreateTicketEventRequest",
    "CreateTicketRequest",
    "ForgotPasswordRequest",
    "LoginRequest",
    "SalesChatRequest",
    "SalesConversationRequest",
    "UpdateTicketRequest",
    "UpdateTitleRequest",
]
