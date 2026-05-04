"""Native admin ticket routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from .. import app as app_runtime

router = APIRouter(tags=["admin-tickets"])


def _ticket_status_filter(raw: str = "") -> list[str] | None:
    statuses = [part.strip() for part in (raw or "").split(",") if part.strip()]
    return statuses or None


@router.get("/api/tickets")
def list_tickets(
    user_id: str = Query(...),
    conversation_id: str = Query(default=""),
    serial_number: str = Query(default=""),
    status: str = Query(default=""),
):
    try:
        return app_runtime.store.list_tickets(
            user_id,
            conversation_id=conversation_id or None,
            serial_number=serial_number or None,
            status=_ticket_status_filter(status),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/tickets")
def create_ticket(body: app_runtime.CreateTicketRequest, authorization: str = Header(...)):
    app_runtime._bearer_token(authorization)
    try:
        return app_runtime.store.create_ticket(
            user_id=body.user_id,
            conversation_id=body.conversation_id,
            serial_number=body.serial_number,
            title=body.title,
            description=body.description,
            success_criteria=body.success_criteria,
            status=body.status,
            priority=body.priority,
            owner_type=body.owner_type,
            owner_id=body.owner_id,
            created_by_turn_id=body.created_by_turn_id,
            due_at=body.due_at,
            metadata=body.metadata,
            actor_type="human",
            actor_id=body.user_id,
            event_note="Ticket created from the admin workspace.",
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.patch("/api/tickets/{ticket_id}")
def update_ticket(
    ticket_id: str,
    body: app_runtime.UpdateTicketRequest,
    authorization: str = Header(...),
):
    app_runtime._bearer_token(authorization)
    updates = body.model_dump(
        exclude={"user_id", "note", "evidence"},
        exclude_none=True,
    )
    try:
        return app_runtime.store.update_ticket(
            ticket_id=ticket_id,
            user_id=body.user_id,
            updates=updates,
            actor_type="human",
            actor_id=body.user_id,
            note=body.note,
            evidence=body.evidence,
        )
    except LookupError as e:
        raise HTTPException(404, str(e) or "Ticket not found") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/api/tickets/{ticket_id}/events")
def create_ticket_event(
    ticket_id: str,
    body: app_runtime.CreateTicketEventRequest,
    authorization: str = Header(...),
):
    app_runtime._bearer_token(authorization)
    try:
        return app_runtime.store.append_ticket_event(
            ticket_id=ticket_id,
            user_id=body.user_id,
            event_type=body.event_type,
            actor_type=body.actor_type,
            actor_id=body.actor_id or body.user_id,
            note=body.note,
            turn_id=body.turn_id,
            evidence=body.evidence,
        )
    except LookupError as e:
        raise HTTPException(404, str(e) or "Ticket not found") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
