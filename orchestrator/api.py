"""
api.py — FastAPI server exposing the orchestrator over HTTP + SSE.

Run with:
    cd orchestrator
    uvicorn api:app --port 8000 --reload

Auth0 config is read from environment variables using the same pattern as
the Streamlit app:  AUTH0_DOMAIN_{ENV}, AUTH0_CLIENT_ID_{ENV},
AUTH0_API_AUDIENCE_{ENV}, AUTH0_REALM, BLUEBOT_ENV (default PROD).

The bluebot Bearer token is passed via the Authorization header.
A user_id query parameter scopes conversations (no server-side auth).
"""

import json
import os
import queue
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import store
from agent import run_turn
from summarizer import update_title

# ---------------------------------------------------------------------------
# Load .streamlit/secrets.toml into env (same values the Streamlit app uses)
# ---------------------------------------------------------------------------

_SECRETS_PATH = Path(__file__).parent / ".streamlit" / "secrets.toml"

def _load_secrets_to_env() -> None:
    """Read key = "value" lines from secrets.toml into os.environ as fallbacks."""
    if not _SECRETS_PATH.exists():
        return
    for line in _SECRETS_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and val and key not in os.environ:
            os.environ[key] = val

_load_secrets_to_env()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    store._ensure_ready()
    yield

app = FastAPI(title="bluebot Orchestrator API", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _bearer_token(authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing Bearer token")
    return authorization[7:]


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _auth0_config() -> dict:
    env = _env("BLUEBOT_ENV", "PROD").upper()
    return {
        "domain":    _env(f"AUTH0_DOMAIN_{env}"),
        "audience":  _env(f"AUTH0_API_AUDIENCE_{env}"),
        "client_id": _env(f"AUTH0_CLIENT_ID_{env}"),
        "realm":     _env("AUTH0_REALM", "Username-Password-Authentication"),
    }


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str

class CreateConversationRequest(BaseModel):
    user_id: str
    title: str = ""

class ChatRequest(BaseModel):
    message: str

class UpdateTitleRequest(BaseModel):
    title: str


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
async def login(body: LoginRequest):
    """Proxy Auth0 ROPC login — keeps client_id/audience server-side."""
    cfg = _auth0_config()
    if not cfg["domain"] or not cfg["client_id"]:
        raise HTTPException(500, "Auth0 is not configured on the server")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{cfg['domain']}/oauth/token",
                json={
                    "client_id":  cfg["client_id"],
                    "grant_type": "http://auth0.com/oauth/grant-type/password-realm",
                    "username":   body.username,
                    "password":   body.password,
                    "audience":   cfg["audience"],
                    "realm":      cfg["realm"],
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "access_token": data["access_token"],
                "user": body.username,
            }
    except httpx.HTTPStatusError as e:
        try:
            msg = e.response.json().get("error_description", str(e))
        except Exception:
            msg = str(e)
        raise HTTPException(401, msg)
    except Exception as e:
        raise HTTPException(502, str(e))


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------

@app.get("/api/conversations")
def list_conversations(user_id: str = Query(...)):
    return store.list_conversations(user_id)


@app.post("/api/conversations")
def create_conversation(body: CreateConversationRequest):
    conv_id = store.create_conversation(body.user_id, body.title)
    return {"id": conv_id}


@app.get("/api/conversations/{conv_id}/messages")
def get_messages(conv_id: str):
    return store.load_messages(conv_id)


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str, user_id: str = Query(...)):
    store.delete_conversation(conv_id, user_id)
    return {"ok": True}


@app.patch("/api/conversations/{conv_id}")
def patch_conversation(conv_id: str, body: UpdateTitleRequest):
    store.set_title(conv_id, body.title)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

_PLOTS_DIR = Path(__file__).parent.parent / "data-processing-agent" / "plots"
_LOGO_PATH = Path(__file__).parent.parent / "bluebot.jpg"


@app.get("/api/logo")
def get_logo():
    """Serve the bluebot logo."""
    if not _LOGO_PATH.exists():
        raise HTTPException(404, "Logo not found")
    return FileResponse(_LOGO_PATH, media_type="image/jpeg")


@app.get("/api/plots/{filename}")
def get_plot(filename: str):
    """Serve a generated plot PNG by filename."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    path = _PLOTS_DIR / filename
    if not path.exists() or not path.suffix == ".png":
        raise HTTPException(404, "Plot not found")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# Streaming chat (SSE)
# ---------------------------------------------------------------------------

_SENTINEL = object()
_active_conversations: set[str] = set()


@app.get("/api/conversations/{conv_id}/status")
def conversation_status(conv_id: str):
    """Check whether the server is actively processing this conversation."""
    return {"processing": conv_id in _active_conversations}


@app.post("/api/conversations/{conv_id}/chat")
async def chat(
    conv_id: str,
    body: ChatRequest,
    request: Request,
    authorization: str = Header(...),
):
    token = _bearer_token(authorization)
    messages = store.load_messages(conv_id)

    user_msg = {"role": "user", "content": body.message}
    messages.append(user_msg)
    store.append_messages(conv_id, [user_msg])

    if len(messages) == 1:
        store.set_title(conv_id, body.message[:60])

    eq: queue.Queue = queue.Queue()

    def _on_event(event: dict):
        eq.put(event)

    def _run():
        _active_conversations.add(conv_id)
        try:
            run_turn(messages, token, on_event=_on_event)
            checkpoint = next(
                (i for i in range(len(messages) - 1, -1, -1)
                 if messages[i]["role"] == "user" and messages[i]["content"] == body.message),
                0,
            )
            store.append_messages(conv_id, messages[checkpoint + 1:])
            update_title(conv_id, messages)
            eq.put({"type": "done"})
        except Exception as exc:
            eq.put({"type": "error", "error": str(exc)})
        finally:
            _active_conversations.discard(conv_id)
            eq.put(_SENTINEL)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    def _rewrite_plot_paths(event: dict) -> dict:
        """Replace absolute filesystem paths with /api/plots/ URLs."""
        if "plot_paths" in event:
            event["plot_paths"] = [
                f"/api/plots/{Path(p).name}" for p in event["plot_paths"]
            ]
        return event

    async def _stream() -> AsyncGenerator[dict, None]:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = eq.get(timeout=0.25)
            except queue.Empty:
                continue
            if event is _SENTINEL:
                break
            yield {"event": event["type"], "data": json.dumps(_rewrite_plot_paths(event))}

    return EventSourceResponse(_stream())
