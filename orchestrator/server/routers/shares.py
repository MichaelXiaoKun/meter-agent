"""Public share routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import app as app_runtime

router = APIRouter(tags=["public-shares"])


@router.get("/api/public/shares/{token}")
def get_public_share(token: str):
    """Read-only snapshot for anonymous visitors. No auth header required."""
    data = app_runtime.store.load_share(token)
    if data is None or data["revoked"]:
        raise HTTPException(404, "Share not found or revoked")
    return {"title": data["title"], "messages": data["messages"]}
