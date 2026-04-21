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

import asyncio
import json
import logging
import os
import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse


# ---------------------------------------------------------------------------
# Disable Nagle's algorithm on accepted TCP sockets (mobile streaming fix)
# ---------------------------------------------------------------------------
# The orchestrator's chat endpoint emits SSE ``text_delta`` events as fast
# as Anthropic streams tokens — typically dozens per second, each only
# 30-100 bytes long after JSON encoding. Without ``TCP_NODELAY``, the
# kernel's Nagle algorithm coalesces consecutive small writes whenever an
# ACK is in flight, holding them for up to ~200 ms or until the next ACK
# arrives. On loopback (desktop dev) ACKs are essentially free so the
# effect is invisible; over Wi-Fi to a phone the round-trip is large
# enough that Nagle batches roughly every 200 ms of typing into a single
# burst, which makes the assistant reply look like it "pops in" instead
# of typing in real time.
#
# Patching ``socket.socket.accept`` here flips ``TCP_NODELAY`` on every
# socket uvicorn (or any other server in this process) accepts. We also
# set it on the listening socket so platforms that inherit the option
# (some BSDs / macOS) get it for free even before the patch fires. This
# is a one-shot global tweak that runs at module import — well before
# uvicorn binds — so accepted connections never carry the buffering
# default.
def _enable_tcp_nodelay_on_accept() -> None:
    _orig_accept = socket.socket.accept

    def _accept_no_delay(self):  # type: ignore[no-untyped-def]
        conn, addr = _orig_accept(self)
        try:
            if conn.family in (socket.AF_INET, socket.AF_INET6):
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            # Some sockets (e.g. UDS) reject TCP options — that's fine,
            # they aren't subject to Nagle anyway.
            pass
        return conn, addr

    socket.socket.accept = _accept_no_delay  # type: ignore[assignment]


_enable_tcp_nodelay_on_accept()

import store
from agent import get_rate_limit_config_for_api, run_turn
from plots_paths import resolved_plots_dir
from turn_gate import acquire_run_turn_slot, configured_max_slots, release_run_turn_slot
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

logger = logging.getLogger(__name__)
logger.info(
    "ORCHESTRATOR_MAX_CONCURRENT_TURNS=%s (max parallel chat turns per process)",
    configured_max_slots(),
)


def _sse_error_message(exc: BaseException) -> str:
    """Short, user-facing text for SSE `error` events (full traceback still logged)."""
    raw = str(exc)
    if "rate_limit" in raw.lower() or "429" in raw or type(exc).__name__ == "RateLimitError":
        return (
            "Claude API rate limit reached (input tokens per minute for your organization). "
            "Wait a minute and retry, start a **new chat** for very long threads, or ask a shorter "
            "follow-up so the model sees less history."
        )
    return raw


@asynccontextmanager
async def _lifespan(app: FastAPI):
    store._ensure_ready()
    logger.info("Rate limit budgeting: %s", get_rate_limit_config_for_api())
    yield

app = FastAPI(title="bluebot Orchestrator API", lifespan=_lifespan)


@app.get("/api/config")
def orchestrator_config():
    """
    Public tuning values for the UI (e.g. TPM bar) — no secrets.
    """
    return get_rate_limit_config_for_api()

_CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _CORS_ORIGINS],
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
    client_timezone: str | None = None  # IANA, e.g. America/New_York (browser local zone)
    # Optional UUID from the client so SSE events can be correlated and stale streams ignored.
    client_turn_id: str | None = None
    # Optional per-turn model override from the UI's picker. Validated against
    # the server allowlist in run_turn → resolve_orchestrator_model; unknown
    # values silently fall back to the server default.
    model: str | None = None

class UpdateTitleRequest(BaseModel):
    title: str


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@app.post("/api/auth/login")
async def login(body: LoginRequest):
    """Proxy Auth0 ROPC login — keeps client_id/audience server-side."""
    cfg = _auth0_config()
    env = _env("BLUEBOT_ENV", "PROD").upper()
    if not cfg["domain"] or not cfg["client_id"] or not cfg["audience"]:
        missing = []
        if not cfg["domain"]:
            missing.append(f"AUTH0_DOMAIN_{env}")
        if not cfg["client_id"]:
            missing.append(f"AUTH0_CLIENT_ID_{env}")
        if not cfg["audience"]:
            missing.append(f"AUTH0_API_AUDIENCE_{env}")
        raise HTTPException(
            500,
            "Auth0 is not configured on the server. Set these in your host environment "
            f"(e.g. Railway Variables): {', '.join(missing)}. "
            f"BLUEBOT_ENV is {env!r} — variable names must use that suffix.",
        )

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

_PLOTS_DIR = resolved_plots_dir()
logger.info("PLOTS_DIR=%s (exists=%s)", _PLOTS_DIR, _PLOTS_DIR.is_dir())
_LOGO_PATH = Path(os.environ.get(
    "LOGO_PATH",
    str(Path(__file__).parent.parent / "bluebot.jpg"),
))


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
    path = (_PLOTS_DIR / filename).resolve()
    try:
        path.relative_to(_PLOTS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename") from None
    if not path.is_file() or path.suffix.lower() != ".png":
        logger.warning(
            "Plot not found: %s (PLOTS_DIR=%s). "
            "Common causes: Railway scaled to >1 replica (plots are local disk per instance), "
            "redeploy cleared ephemeral files, or the browser requested a filename from markdown "
            "that does not match saved files.",
            path,
            _PLOTS_DIR,
        )
        raise HTTPException(404, "Plot not found")
    return FileResponse(path, media_type="image/png")


# ---------------------------------------------------------------------------
# Streaming chat (SSE) — POST initiates, EventSource subscribes
# ---------------------------------------------------------------------------
# We split the chat turn into two HTTP requests for one critical reason:
# **iOS WebKit (and therefore iOS Safari, iOS Chrome, iOS Edge — all of
# which use WKWebView underneath) buffers ``fetch().body.getReader()``
# reads internally and only releases bytes to JS when its internal buffer
# fills or the connection closes**. We tried every reasonable mitigation
# (TCP_NODELAY on accept, large initial primer, per-event SSE comment
# padding, X-Accel-Buffering disable on the Vite proxy, ping=2 keep-
# alives) and on iOS Chrome the entire reply still arrives in one chunk
# at the end of the stream.
#
# The native ``EventSource`` API does not have this problem because
# WebKit's SSE implementation parses events as the bytes arrive and fires
# JS callbacks per event boundary — it does not go through the fetch
# buffer at all. ``EventSource`` only supports ``GET`` though, so we
# can't keep our existing single-POST design.
#
# Solution: POST creates a "stream session" (queue + worker thread) and
# returns its UUID; the browser then opens an ``EventSource`` against
# that UUID to subscribe. The UUID is single-use and short-lived; only
# the original POSTer knows it.
# ---------------------------------------------------------------------------

_active_conversations: set[str] = set()
_streams: dict[str, dict] = {}
_streams_lock = threading.Lock()
# Stream sessions live at most this long after completion (or if no
# consumer ever attaches, e.g. browser tab crashed between the POST
# response and subscription). Workers always run to completion; stale
# sessions are reaped by :func:`_gc_streams`.
_STREAM_TTL_SEC = 600


def _gc_streams() -> None:
    """Best-effort cleanup of stream sessions past their TTL.

    A session is dropped when ``now - created > _STREAM_TTL_SEC`` *and*
    either nobody ever subscribed, or the worker has finished. A phone
    polling ~every 200 ms refreshes naturally through ``events`` access,
    so TTL is measured from session creation, not from last poll.
    """
    now = time.monotonic()
    with _streams_lock:
        stale = [
            sid
            for sid, s in _streams.items()
            if now - s["created"] > _STREAM_TTL_SEC
        ]
        for sid in stale:
            _streams.pop(sid, None)


def _rewrite_plot_paths(event: dict) -> dict:
    """Replace absolute filesystem paths with /api/plots/ URLs."""
    if "plot_paths" in event:
        event["plot_paths"] = [
            f"/api/plots/{Path(p).name}" for p in event["plot_paths"]
        ]
    return event


@app.get("/api/conversations/{conv_id}/status")
def conversation_status(conv_id: str):
    """Check whether the server is actively processing this conversation."""
    return {"processing": conv_id in _active_conversations}


@app.post("/api/conversations/{conv_id}/chat")
async def chat_init(
    conv_id: str,
    body: ChatRequest,
    authorization: str = Header(...),
    x_anthropic_key: str | None = Header(default=None, alias="X-Anthropic-Key"),
):
    """Persist the user's message, kick off the worker thread, and return a
    one-shot ``stream_id`` the browser can subscribe to via ``EventSource``.

    The actual SSE event stream lives at ``GET /api/streams/{stream_id}``
    (see :func:`chat_stream`).
    """
    _gc_streams()
    token = _bearer_token(authorization)
    user_anthropic_key = (x_anthropic_key or "").strip() or None
    messages = store.load_messages(conv_id)

    user_msg = {"role": "user", "content": body.message}
    messages.append(user_msg)
    # Messages only appended after this point belong to this turn (append vs replace on compress).
    n_messages_after_user = len(messages)
    store.append_messages(conv_id, [user_msg])

    if len(messages) == 1:
        store.set_title(conv_id, body.message[:60])

    # One logical turn per POST: every event gets the same ``turn_id`` and
    # a monotonic ``seq`` so the client can ignore stale / out-of-order
    # events (abort, double fire, reconnect edge cases).
    #
    # IMPORTANT: we echo the client's ``client_turn_id`` *verbatim*.
    # Previously we parsed it as a UUID and generated a fresh one on
    # ``ValueError`` — but iOS Safari < 15.4 has no ``crypto.randomUUID``
    # and the JS fallback generated an id shaped like ``turn-1234-abc``
    # which isn't a valid UUID. The server then minted its own random
    # UUID, every SSE event carried that UUID, the client's
    # ``sseExpectedTurnIdRef`` kept the original string, and
    # ``shouldApplySseEvent`` silently dropped every event. The UI sat
    # on "Preparing reply…" forever because state never advanced.
    #
    # The turn_id is just a nonce for client-side dedup across
    # re-renders / aborts. It doesn't need to be a UUID — it only needs
    # to round-trip unchanged.
    def _turn_id_for_request() -> str:
        raw = (body.client_turn_id or "").strip()
        return raw or str(uuid.uuid4())

    turn_id = _turn_id_for_request()

    # ------------------------------------------------------------------
    # Shared session storage: event log + Condition for wakeup
    # ------------------------------------------------------------------
    # Events are appended to ``events`` (list of dicts) in monotonic
    # ``seq`` order. Consumers (EventSource or long-poll) read by
    # ``cursor`` index. The ``cond`` Condition is notified whenever new
    # events land so long-polling pollers can unblock immediately. A
    # single append-only log replaces the old per-session queue, which
    # means the session supports *either* streaming or polling (or even
    # both simultaneously — e.g. EventSource + a debug tab). This is
    # what makes the mobile polling fallback work without a second
    # worker thread.
    session_cond = threading.Condition()
    stream_id = str(uuid.uuid4())

    with _streams_lock:
        _streams[stream_id] = {
            "events": [],  # list[dict]; appended in seq order
            "done": False,  # True once worker drains
            "cond": session_cond,
            "created": time.monotonic(),
            "sse_consumed": False,  # SSE is single-shot; polling is not
            "turn_id": turn_id,
            "conv_id": conv_id,
        }

    def _emit_event(event: dict) -> None:
        with session_cond:
            with _streams_lock:
                sess = _streams.get(stream_id)
                if sess is None:
                    return
                seq = len(sess["events"]) + 1
                sess["events"].append({**event, "turn_id": turn_id, "seq": seq})
            session_cond.notify_all()

    def _mark_done() -> None:
        with session_cond:
            with _streams_lock:
                sess = _streams.get(stream_id)
                if sess is not None:
                    sess["done"] = True
            session_cond.notify_all()

    def _run():
        try:
            acquire_run_turn_slot(
                on_wait=lambda: _emit_event(
                    {
                        "type": "queued",
                        "message": (
                            "Waiting for a free slot — another chat turn is using the model. "
                            f"(limit {configured_max_slots()} concurrent turn(s) per server.)"
                        ),
                    }
                )
            )
            _active_conversations.add(conv_id)
            try:
                _, history_replaced = run_turn(
                    messages,
                    token,
                    on_event=_emit_event,
                    client_timezone=body.client_timezone,
                    anthropic_api_key=user_anthropic_key,
                    model=body.model,
                )
                if history_replaced:
                    # In-place summarization (e.g. 429) — DB must match compressed thread.
                    store.replace_conversation_messages(conv_id, messages)
                else:
                    new_tail = messages[n_messages_after_user:]
                    if not new_tail:
                        logger.warning(
                            "chat turn produced no new messages after user (conv=%s)",
                            conv_id,
                        )
                    store.append_messages(conv_id, new_tail)
                update_title(conv_id, messages, anthropic_api_key=user_anthropic_key)
                _emit_event({"type": "done"})
            except Exception as exc:
                logger.exception("run_turn failed for conv %s", conv_id)
                _emit_event({"type": "error", "error": _sse_error_message(exc)})
            finally:
                _active_conversations.discard(conv_id)
        finally:
            release_run_turn_slot()
            _mark_done()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    return {"stream_id": stream_id, "turn_id": turn_id}


@app.get("/api/streams/{stream_id}")
async def chat_stream(stream_id: str, request: Request):
    """SSE stream for a chat session previously started via POST.

    Designed for ``EventSource`` consumption on desktop browsers. Reads
    from the shared event log; a separate long-poll endpoint
    (``/api/streams/{id}/poll``) consumes the *same* log and is the
    fallback used by mobile browsers where EventSource has proven
    unreliable under iOS + Vite proxy buffering (no amount of padding or
    ``TCP_NODELAY`` tuning made it fully reliable).

    The SSE subscription is single-shot per session — React StrictMode's
    double render in dev would otherwise open two connections that split
    events between them.
    """
    with _streams_lock:
        meta = _streams.get(stream_id)
        if meta is None:
            raise HTTPException(404, detail="stream not found or expired")
        if meta["sse_consumed"]:
            raise HTTPException(409, detail="stream already consumed")
        meta["sse_consumed"] = True

    cond: threading.Condition = meta["cond"]

    def _snapshot(cursor: int) -> tuple[list[dict], bool, int]:
        """Return ``(new_events, done, total_len)`` for this cursor."""
        with _streams_lock:
            sess = _streams.get(stream_id)
            if sess is None:
                return [], True, cursor
            events = sess["events"]
            total = len(events)
            return (events[cursor:] if cursor < total else []), bool(sess["done"]), total

    async def _stream() -> AsyncGenerator[dict, None]:
        # Per-event SSE comment padding: each text_delta becomes a
        # >=12 KB SSE frame so iOS's Wi-Fi receive path doesn't coalesce
        # the small payload with later events. ``data`` + ``comment`` in
        # the same dict are encoded by sse-starlette into a single SSE
        # frame / ASGI send / TCP write, which was the specific thing
        # that made desktop per-token typing reliable over the Vite
        # proxy. (Mobile uses the polling fallback below instead.)
        _PRIMER = " " * 16384
        _PER_EVENT_PAD = " " * 12288
        cursor = 0
        yield {"comment": _PRIMER}
        while True:
            if await request.is_disconnected():
                return
            new_events, done_flag, total_len = _snapshot(cursor)
            for ev in new_events:
                yield {
                    "data": json.dumps(_rewrite_plot_paths(dict(ev))),
                    "comment": _PER_EVENT_PAD,
                }
            cursor += len(new_events)
            if done_flag and cursor >= total_len:
                return
            # Wait briefly for more events; 0.25 s keeps disconnect
            # detection responsive. ``cond.wait`` runs in a thread so we
            # don't block the event loop.
            def _wait() -> None:
                with cond:
                    cond.wait(timeout=0.25)
            await asyncio.to_thread(_wait)

    return EventSourceResponse(
        _stream(),
        ping=2,
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache, no-store, no-transform",
        },
    )


# ---------------------------------------------------------------------------
# Mobile polling fallback
# ---------------------------------------------------------------------------
# iOS WebKit's fetch streaming + Vite dev proxy + consumer Wi-Fi is a
# perfect storm of buffering that we could not fully tame even with
# ``EventSource`` + 16 KB primer + 12 KB per-event padding. The pragmatic
# fallback is plain old HTTP long-polling: each poll is a short-lived
# JSON request, which every mobile browser handles correctly without
# any streaming semantics. The frontend polls ~every 200 ms, and the
# server long-polls for up to ~1 s per request so idle sessions don't
# burn CPU.
# ---------------------------------------------------------------------------

_POLL_WAIT_MAX_MS = 2000


@app.get("/api/streams/{stream_id}/poll")
async def chat_stream_poll(
    stream_id: str,
    cursor: int = 0,
    wait_ms: int = 1000,
):
    """Return all events with ``seq > cursor``, optionally blocking briefly.

    Response shape::

        {"events": [<event>, ...], "done": true, "next_cursor": 42}

    ``next_cursor`` should be passed as ``cursor`` on the next request.
    ``done`` is ``true`` once the worker has emitted its terminal event
    *and* ``next_cursor`` equals the log length; at that point the
    client can stop polling.
    """
    wait_ms = max(0, min(int(wait_ms), _POLL_WAIT_MAX_MS))

    with _streams_lock:
        meta = _streams.get(stream_id)
        if meta is None:
            raise HTTPException(404, detail="stream not found or expired")

    cond: threading.Condition = meta["cond"]

    def _snapshot() -> tuple[list[dict], bool, int]:
        with _streams_lock:
            sess = _streams.get(stream_id)
            if sess is None:
                return [], True, cursor
            events = sess["events"]
            done = bool(sess["done"])
            tail = events[cursor:] if cursor < len(events) else []
            return tail, done, len(events)

    events_out, done, length = _snapshot()
    if not events_out and not done and wait_ms > 0:
        # Long-poll: block briefly until new events arrive or the worker
        # finishes. We use ``asyncio.to_thread`` so we don't hold the
        # event loop while waiting on the condition variable.
        def _wait_for_events() -> None:
            deadline = time.monotonic() + wait_ms / 1000.0
            with cond:
                while True:
                    with _streams_lock:
                        sess = _streams.get(stream_id)
                        if sess is None:
                            return
                        if len(sess["events"]) > cursor or sess["done"]:
                            return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    cond.wait(timeout=min(remaining, 0.25))

        await asyncio.to_thread(_wait_for_events)
        events_out, done, length = _snapshot()

    # ``Cache-Control: no-store`` is mandatory here: iOS Safari has been
    # observed to serve *repeat* poll responses from its in-memory cache
    # even when the query string (``cursor=N``) differs, if the path
    # matches a recent 200 OK and no explicit no-store was sent. That
    # silently breaks polling because the client keeps seeing the old
    # empty-events reply and never processes the events the server
    # actually emitted in between.
    body = {
        "events": [_rewrite_plot_paths(dict(ev)) for ev in events_out],
        "done": done and length == cursor + len(events_out),
        "next_cursor": cursor + len(events_out),
    }
    return JSONResponse(
        body,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


# ---------------------------------------------------------------------------
# Production SPA (built Vite app) — same origin as /api (Railway, Docker)
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(
    os.environ.get(
        "FRONTEND_DIST",
        str(Path(__file__).resolve().parent.parent / "frontend" / "dist"),
    )
)

# Allow the Web Speech API / getUserMedia mic pipeline on the SPA document.
# Some platforms default to a restrictive Permissions-Policy; without
# ``microphone=(self)`` the composer mic can fail on production (Railway)
# even over HTTPS.
_SPA_DOCUMENT_HEADERS = {"Permissions-Policy": "microphone=(self)"}


def _mount_production_spa() -> None:
    """Serve React static files when dist/ exists (omit for API-only / local Vite dev)."""
    index = _FRONTEND_DIST / "index.html"
    if not index.is_file():
        return

    assets_dir = _FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="ui_assets")

    @app.get("/")
    def _spa_index():
        return FileResponse(index, headers=_SPA_DOCUMENT_HEADERS)

    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str):
        # Registered after all /api routes — unmatched /api/* returns JSON 404
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, detail="Not Found")
        candidate = _FRONTEND_DIST / full_path
        if candidate.is_file():
            # Only attach document policy headers to HTML entrypoints — not to
            # ``.js`` / ``.svg`` subresources (policy applies to the document
            # from the main navigation response, i.e. ``/`` or SPA fallback).
            if candidate.suffix.lower() in (".html", ".htm"):
                return FileResponse(candidate, headers=_SPA_DOCUMENT_HEADERS)
            return FileResponse(candidate)
        return FileResponse(index, headers=_SPA_DOCUMENT_HEADERS)


_mount_production_spa()
