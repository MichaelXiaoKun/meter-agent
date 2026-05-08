"""Authenticated admin conversation and share routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query

from .. import app as app_runtime

router = APIRouter(tags=["admin-conversations"])


@router.get("/api/conversations")
def list_conversations(user_id: str = Query(...)):
    return app_runtime.store.list_conversations(user_id)


@router.post("/api/conversations")
def create_conversation(body: app_runtime.CreateConversationRequest):
    conv_id = app_runtime.store.create_conversation(body.user_id, body.title)
    return {"id": conv_id}


@router.get("/api/conversations/{conv_id}/messages")
def get_messages(conv_id: str):
    return app_runtime.store.load_messages(conv_id)


@router.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str, user_id: str = Query(...)):
    app_runtime.store.delete_conversation(conv_id, user_id)
    return {"ok": True}


@router.patch("/api/conversations/{conv_id}")
def patch_conversation(conv_id: str, body: app_runtime.UpdateTitleRequest):
    app_runtime.store.set_title(conv_id, body.title)
    return {"ok": True}


@router.post("/api/conversations/{conv_id}/share")
def create_conversation_share(conv_id: str, body: app_runtime.CreateShareRequest, authorization: str = Header(...)):
    """
    Create a one-time public snapshot of the conversation. Requires a logged-in
    user (Bearer) who owns the conversation (``user_id`` in the body must match
    the conversation's owner; same scoping as other conv endpoints).
    """
    app_runtime._bearer_token(authorization)
    try:
        token = app_runtime.store.create_share(conv_id, body.user_id)
    except LookupError as e:
        raise HTTPException(404, str(e) or "Conversation not found or access denied") from e
    return {"token": token}


@router.delete("/api/shares/{token}")
def delete_share(
    token: str,
    user_id: str = Query(...),
    authorization: str = Header(...),
):
    """Revoke a share; only the owner (``user_id`` + Bearer) can revoke."""
    app_runtime._bearer_token(authorization)
    ok = app_runtime.store.revoke_share(token, user_id)
    if not ok:
        raise HTTPException(404, "Share not found or access denied")
    return {"ok": True}
