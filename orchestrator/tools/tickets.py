"""Native admin ticket tools for accountable follow-up work."""

from __future__ import annotations

from typing import Any

import store


_AGENT_OWNER = "bluebot-admin-agent"


LIST_TICKETS_TOOL_DEFINITION = {
    "name": "list_tickets",
    "description": (
        "List native admin tickets for the current authenticated user. Use this "
        "when the user asks what is open, assigned, waiting, or already tracked."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "conversation_id": {
                "type": "string",
                "description": "Optional conversation id to scope tickets. Usually omit it.",
            },
            "all_conversations": {
                "type": "boolean",
                "description": "Set true only when the user asks for tickets across all conversations.",
            },
            "serial_number": {
                "type": "string",
                "description": "Optional meter serial number to scope tickets.",
            },
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "waiting_on_human", "resolved", "cancelled"],
                "description": "Optional ticket status filter.",
            },
        },
    },
}


CREATE_TICKET_TOOL_DEFINITION = {
    "name": "create_ticket",
    "description": (
        "Create a durable native admin ticket/work item for accountable follow-up. "
        "Use only when the user asks to track work, when work cannot finish in this "
        "turn, or when high-risk diagnostics/configuration need follow-up."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short accountable work-item title.",
            },
            "success_criteria": {
                "type": "string",
                "description": "Concrete condition that proves this ticket is done.",
            },
            "description": {
                "type": "string",
                "description": "Optional extra context, compact and user-facing.",
            },
            "serial_number": {
                "type": "string",
                "description": "Meter serial number when this ticket is tied to a meter.",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high", "urgent"],
                "description": "Ticket priority. Default normal.",
            },
            "owner_type": {
                "type": "string",
                "enum": ["agent", "human", "unassigned"],
                "description": (
                    "Use agent for follow-up the assistant can re-check; human for field "
                    "or operator work; unassigned only when ownership is genuinely unclear."
                ),
            },
            "owner_id": {
                "type": "string",
                "description": "Optional named owner. Omit for default owner.",
            },
            "agent_checkable": {
                "type": "boolean",
                "description": "Set true when the assistant can verify the follow-up later.",
            },
            "due_at": {
                "type": "integer",
                "description": "Optional Unix seconds due date.",
            },
            "metadata": {
                "type": "object",
                "description": (
                    "Optional compact metadata. Include config_action_id here when linking "
                    "to a pending or completed configuration workflow."
                ),
            },
            "evidence": {
                "type": "object",
                "description": "Optional compact evidence that justified opening the ticket.",
            },
        },
        "required": ["title", "success_criteria"],
    },
}


UPDATE_TICKET_TOOL_DEFINITION = {
    "name": "update_ticket",
    "description": (
        "Update a native admin ticket. Do not resolve a ticket unless the user "
        "instructed it or there is evidence from a tool result, verified diagnostic "
        "fact, or linked configuration workflow."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ticket_id": {"type": "string", "description": "Ticket id to update."},
            "title": {"type": "string", "description": "Optional new title."},
            "description": {"type": "string", "description": "Optional new description."},
            "success_criteria": {
                "type": "string",
                "description": "Optional new success criteria.",
            },
            "status": {
                "type": "string",
                "enum": ["open", "in_progress", "waiting_on_human", "resolved", "cancelled"],
                "description": "Optional new ticket status.",
            },
            "priority": {
                "type": "string",
                "enum": ["low", "normal", "high", "urgent"],
                "description": "Optional new priority.",
            },
            "owner_type": {
                "type": "string",
                "enum": ["agent", "human", "unassigned"],
                "description": "Optional new owner type.",
            },
            "owner_id": {"type": "string", "description": "Optional new owner id."},
            "due_at": {"type": "integer", "description": "Optional Unix seconds due date."},
            "serial_number": {
                "type": "string",
                "description": "Optional meter serial number.",
            },
            "metadata": {"type": "object", "description": "Optional replacement metadata."},
            "note": {
                "type": "string",
                "description": "Timeline note explaining the update.",
            },
            "evidence": {
                "type": "object",
                "description": "Compact evidence for status changes, required to resolve.",
            },
        },
        "required": ["ticket_id"],
    },
}


def _conversation_owner(conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    return store.get_conversation_user_id(conversation_id)


def _failure(error: str) -> dict:
    return {"success": False, "error": error}


def list_tickets(
    *,
    conversation_id: str | None,
    serial_number: str | None = None,
    status: str | None = None,
    all_conversations: bool = False,
) -> dict:
    user_id = _conversation_owner(conversation_id)
    if not user_id:
        return _failure("Tickets require an authenticated admin conversation.")
    try:
        tickets = store.list_tickets(
            user_id,
            conversation_id=None if all_conversations else conversation_id,
            serial_number=serial_number,
            status=status,
        )
    except ValueError as exc:
        return _failure(str(exc))
    return {"success": True, "tickets": tickets, "count": len(tickets)}


def create_ticket(
    *,
    conversation_id: str | None,
    title: str,
    success_criteria: str,
    description: str = "",
    serial_number: str | None = None,
    priority: str = "normal",
    owner_type: str | None = None,
    owner_id: str | None = None,
    agent_checkable: bool = False,
    due_at: int | None = None,
    metadata: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
    turn_id: str | None = None,
) -> dict:
    user_id = _conversation_owner(conversation_id)
    if not user_id:
        return _failure("Tickets require an authenticated admin conversation.")
    if not owner_type:
        owner_type = "agent" if agent_checkable else "human"
    if not owner_id:
        if owner_type == "agent":
            owner_id = _AGENT_OWNER
        elif owner_type == "human":
            owner_id = user_id
        else:
            owner_id = ""
    try:
        ticket = store.create_ticket(
            user_id=user_id,
            conversation_id=conversation_id,
            serial_number=serial_number,
            title=title,
            description=description,
            success_criteria=success_criteria,
            priority=priority,
            owner_type=owner_type,
            owner_id=owner_id,
            due_at=due_at,
            metadata=metadata,
            evidence=evidence,
            created_by_turn_id=turn_id,
            actor_type="agent",
            actor_id=_AGENT_OWNER,
            event_note="Ticket created by the admin assistant.",
        )
    except ValueError as exc:
        return _failure(str(exc))
    return {"success": True, "ticket": ticket}


def update_ticket(
    *,
    conversation_id: str | None,
    ticket_id: str,
    note: str = "",
    evidence: dict[str, Any] | None = None,
    turn_id: str | None = None,
    **updates: Any,
) -> dict:
    user_id = _conversation_owner(conversation_id)
    if not user_id:
        return _failure("Tickets require an authenticated admin conversation.")
    clean_updates = {k: v for k, v in updates.items() if v is not None}
    try:
        ticket = store.update_ticket(
            ticket_id=ticket_id,
            user_id=user_id,
            updates=clean_updates,
            actor_type="agent",
            actor_id=_AGENT_OWNER,
            note=note,
            evidence=evidence,
            turn_id=turn_id,
        )
    except LookupError as exc:
        return _failure(str(exc) or "Ticket not found")
    except ValueError as exc:
        return _failure(str(exc))
    return {"success": True, "ticket": ticket}


# Convenience alias for modules that expect a unified TOOL_DEFINITIONS list
TOOL_DEFINITIONS = [LIST_TICKETS_TOOL_DEFINITION, CREATE_TICKET_TOOL_DEFINITION, UPDATE_TICKET_TOOL_DEFINITION]
