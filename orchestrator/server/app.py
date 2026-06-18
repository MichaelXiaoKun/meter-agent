"""
api.py — FastAPI server exposing the orchestrator over HTTP + SSE.

Run with:
    cd orchestrator
    uvicorn api:app --port 8000 --reload --reload-dir . --reload-dir ../llm

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
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Literal

_SERVER_DIR = Path(__file__).resolve().parent
_ORCHESTRATOR_DIR = _SERVER_DIR.parent
_REPO_ROOT = _ORCHESTRATOR_DIR.parent
for _path in (_ORCHESTRATOR_DIR, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# Load .env from the repo root for local dev.
try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass

import httpx
from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request
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
from admin_chat.turn_loop import get_rate_limit_config_for_api, run_turn
from sales_chat.agent import run_sales_turn
from shared.message_sanitize import append_turn_activity_block
from shared.plots_paths import resolved_plots_dir
from shared.summarizer import update_title
from shared.turn_gate import acquire_run_turn_slot, configured_max_slots, release_run_turn_slot

# ---------------------------------------------------------------------------
# Load legacy .streamlit/secrets.toml values into env for older local setups.
# ---------------------------------------------------------------------------

_SECRETS_PATH = _ORCHESTRATOR_DIR / ".streamlit" / "secrets.toml"

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


class TurnCancelledByUser(RuntimeError):
    """Raised inside worker threads when a client intentionally cancels a turn."""


def _ensure_flow_tool_stderr_logging() -> None:
    """
    Uvicorn’s console often shows only ``INFO: 127.0.0.1 "GET ..."`` access lines.
    Bind ``tools.flow_analysis`` to stderr so ``analyze_flow_data`` failures are
    always visible when subprocess returncode != 0.
    """
    lg = logging.getLogger("tools.flow_analysis")
    if getattr(lg, "_bluebot_stderr_handler", None) is not None:
        return
    h = logging.StreamHandler(sys.stderr)
    h.setLevel(logging.DEBUG)
    h.setFormatter(
        logging.Formatter("%(levelname)s [tools.flow_analysis] %(message)s")
    )
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg._bluebot_stderr_handler = h  # type: ignore[attr-defined]
    # One short line (avoid terminal wrap/truncate looking like a typo).
    lg.info("bluebot flow_analysis: stderr log handler ready")


_ensure_flow_tool_stderr_logging()


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
    logger.info("Rate limit budgeting: %s", get_rate_limit_config_for_api(allow_live=False))
    yield


HostMode = Literal["combined", "admin", "sales"]


config_router = APIRouter(tags=["config"])


@config_router.get("/api/config")
def orchestrator_config():
    """
    Public tuning values for the UI (e.g. TPM bar) — no secrets.
    """
    return get_rate_limit_config_for_api()

_CORS_ORIGINS = os.environ.get(
    "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")


def _install_cors(fastapi_app: FastAPI) -> None:
    fastapi_app.add_middleware(
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


class ForgotPasswordRequest(BaseModel):
    email: str

class CreateConversationRequest(BaseModel):
    user_id: str
    title: str = ""

class ChatRequest(BaseModel):
    message: str
    client_timezone: str | None = None  # IANA, e.g. America/New_York (browser local zone)
    # Optional UUID from the client so SSE events can be correlated and stale streams ignored.
    client_turn_id: str | None = None
    # Optional pending configuration action id from the Meter Workspace confirm button.
    confirmed_action_id: str | None = None
    # Optional pending configuration action id to cancel from the inline confirmation card.
    cancelled_action_id: str | None = None
    # Optional pending configuration action id replaced by a new user request.
    superseded_action_id: str | None = None
    # Optional per-turn model override from the UI's picker. Validated against
    # the server allowlist in run_turn → resolve_orchestrator_model; unknown
    # values silently fall back to the server default.
    model: str | None = None

class SalesConversationRequest(BaseModel):
    title: str = ""

class SalesChatRequest(BaseModel):
    message: str
    client_turn_id: str | None = None

class UpdateTitleRequest(BaseModel):
    title: str


class CreateShareRequest(BaseModel):
    user_id: str


class CreateTicketRequest(BaseModel):
    user_id: str
    conversation_id: str | None = None
    serial_number: str | None = None
    title: str
    description: str = ""
    success_criteria: str
    status: str = "open"
    priority: str = "normal"
    owner_type: str = "unassigned"
    owner_id: str | None = None
    created_by_turn_id: str | None = None
    due_at: int | None = None
    metadata: dict[str, Any] | None = None


class UpdateTicketRequest(BaseModel):
    user_id: str
    title: str | None = None
    description: str | None = None
    success_criteria: str | None = None
    status: str | None = None
    priority: str | None = None
    owner_type: str | None = None
    owner_id: str | None = None
    due_at: int | None = None
    serial_number: str | None = None
    metadata: dict[str, Any] | None = None
    note: str = ""
    evidence: dict[str, Any] | None = None


class CreateTicketEventRequest(BaseModel):
    user_id: str
    event_type: str
    actor_type: str = "human"
    actor_id: str | None = None
    note: str = ""
    turn_id: str | None = None
    evidence: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

_PLOTS_DIR = resolved_plots_dir()
logger.info("PLOTS_DIR=%s (exists=%s)", _PLOTS_DIR, _PLOTS_DIR.is_dir())
_ANALYSES_DIR = Path(
    os.environ.get(
        "BLUEBOT_ANALYSES_DIR",
        str(_REPO_ROOT / "data-processing-agent" / "analyses"),
    )
).expanduser().resolve()
logger.info("ANALYSES_DIR=%s (exists=%s)", _ANALYSES_DIR, _ANALYSES_DIR.is_dir())
_LOGO_PATH = Path(os.environ.get(
    "LOGO_PATH",
    str(_REPO_ROOT / "bluebot.jpg"),
))

logo_router = APIRouter(tags=["artifacts"])


@logo_router.get("/api/logo")
def get_logo():
    """Serve the bluebot logo."""
    if not _LOGO_PATH.exists():
        raise HTTPException(404, "Logo not found")
    return FileResponse(_LOGO_PATH, media_type="image/jpeg")


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
_active_conversation_streams: dict[str, str] = {}
_cancelled_conversations: set[str] = set()
_cancel_events: dict[str, threading.Event] = {}  # Per-conversation cancel events
_streams: dict[str, dict] = {}
_streams_lock = threading.Lock()
# Stream sessions live at most this long after completion (or after an
# inactive worker loses its active conversation marker). Active unfinished
# sessions must stay resumable even when a large turn exceeds this wall time.
_STREAM_TTL_SEC = 600


def _gc_streams() -> None:
    """Best-effort cleanup of stream sessions past their TTL.

    A session is dropped when ``now - created > _STREAM_TTL_SEC`` *and* the
    worker has either finished or is no longer marked active. TTL is measured
    from session creation because polling/SSE reads are intentionally cheap
    snapshots, not lease-renewing ownership.
    """
    now = time.monotonic()
    with _streams_lock:
        stale = [
            sid
            for sid, s in _streams.items()
            if now - s["created"] > _STREAM_TTL_SEC
            and (
                bool(s.get("done"))
                or str(s.get("conv_id") or "") not in _active_conversations
            )
        ]
        for sid in stale:
            _streams.pop(sid, None)
        stale_set = set(stale)
        for conv_id, active_sid in list(_active_conversation_streams.items()):
            if active_sid in stale_set:
                _active_conversation_streams.pop(conv_id, None)


def _rewrite_plot_paths(event: dict) -> dict:
    """Replace absolute filesystem paths with /api/plots/ URLs."""
    if "plot_paths" in event:
        event["plot_paths"] = [
            f"/api/plots/{Path(p).name}" for p in event["plot_paths"]
        ]
    return event


def _rewrite_download_artifacts(event: dict) -> dict:
    """Replace absolute artifact paths with authenticated download URLs."""
    def _clean(item: object) -> dict | None:
        if not isinstance(item, dict):
            return None
        kind = item.get("kind")
        raw_name = item.get("filename") or item.get("path") or item.get("url")
        if kind != "csv" or not isinstance(raw_name, str):
            return None
        filename = Path(raw_name).name
        if not filename.endswith(".csv"):
            return None
        out = {
            "kind": "csv",
            "title": item.get("title") if isinstance(item.get("title"), str) else "Flow data CSV",
            "filename": filename,
            "url": f"/api/analysis-artifacts/{filename}",
        }
        row_count = item.get("row_count")
        if isinstance(row_count, int):
            out["row_count"] = row_count
        return out

    if "download_artifacts" in event:
        event["download_artifacts"] = [
            clean for a in event.get("download_artifacts") or []
            if (clean := _clean(a)) is not None
        ]
    meters = event.get("meters")
    if isinstance(meters, list):
        for meter in meters:
            if isinstance(meter, dict) and "download_artifacts" in meter:
                meter["download_artifacts"] = [
                    clean for a in meter.get("download_artifacts") or []
                    if (clean := _clean(a)) is not None
                ]
    return event


def _rewrite_artifact_urls(event: dict) -> dict:
    return _rewrite_download_artifacts(_rewrite_plot_paths(event))


_TURN_ACTIVITY_TYPES = frozenset(
    {
        "queued",
        "intent_route",
        "thinking",
        "token_usage",
        "compressing",
        "rate_limit_wait",
        "validation_start",
        "validation_result",
        "tool_call",
        "tool_progress",
        "tool_result",
        "meter_context",
        "config_confirmation_required",
        "config_confirmation_cancelled",
        "config_confirmation_superseded",
        "text_delta",
        "text_stream",
        "tool_round_limit",
        "done",
        "error",
    }
)
_TURN_ACTIVITY_PERSIST_KEYS = frozenset(
    {
        "type",
        "seq",
        "turn_id",
        "tool",
        "input",
        "success",
        "message",
        "tool_activity",
        "display_range",
        "report_truncated",
        "plot_timezone",
        "download_artifacts",
        "analysis_details",
        "meter_context",
        "diagnostic_summary",
        "config_workflow",
        "sweep_result",
        "ticket",
        "tickets",
        "verdict",
        "next_action",
        "validation_mode",
        "draft_model",
        "validator_model",
        "escalated",
        "validation_points_count",
        "unsupported_points_count",
        "tokens",
        "pct",
        "model",
        "intent",
        "source",
        "tools",
        "rate_limit_wait_seconds",
        "current_tokens",
        "estimated_next_tokens",
        "tpm_limit",
        "tpm_cap",
        "overflow_tokens",
        "waited_seconds",
        "attempt",
        "text",
        "limit",
        "deduped",
    }
)


def _slim_turn_events_for_history(raw: list[dict], tid: str) -> list[dict]:
    """Persist replayable turn-status events without storing token spam."""
    out: list[dict] = []
    for ev in raw:
        t = ev.get("type")
        if t not in _TURN_ACTIVITY_TYPES:
            continue
        if t == "text_delta":
            if not out or out[-1].get("type") != "text_stream":
                out.append(
                    {
                        "type": "text_stream",
                        "turn_id": tid,
                        "seq": ev.get("seq", 0),
                    }
                )
            continue
        s = {k: ev[k] for k in _TURN_ACTIVITY_PERSIST_KEYS if k in ev}
        s["type"] = t
        s.setdefault("turn_id", tid)
        s.setdefault("seq", ev.get("seq", 0))
        if t == "tool_result" and "plot_paths" in ev and ev.get("plot_paths"):
            s["plot_paths"] = [
                str(p).split("/")[-1] for p in (ev.get("plot_paths") or [])[:8]
            ]
        if t == "tool_result" and "download_artifacts" in ev and ev.get("download_artifacts"):
            s["download_artifacts"] = [
                a
                for a in (
                    _rewrite_download_artifacts(
                        {"download_artifacts": ev.get("download_artifacts") or []}
                    ).get("download_artifacts")
                    or []
                )[:8]
                if isinstance(a, dict)
            ]
        out.append(s)
    return out


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


# ---------------------------------------------------------------------------
# Router registration
# ---------------------------------------------------------------------------

# Router modules import the request models and stream-session state above, so
# registration intentionally happens late and before the SPA fallback.
from .routers.admin_chat import router as admin_chat_router
from .routers.artifacts import router as artifacts_router
from .routers.auth import router as auth_router
from .routers.conversations import router as conversations_router
from .routers.sales_chat import router as sales_chat_router
from .routers.shares import router as shares_router
from .routers.tickets import router as tickets_router


# ---------------------------------------------------------------------------
# Production SPA (built Vite app) — same origin as /api (Railway, Docker)
# ---------------------------------------------------------------------------

_FRONTEND_DIST = Path(
    os.environ.get(
        "FRONTEND_DIST",
        str(_REPO_ROOT / "frontend" / "dist"),
    )
)

# Allow the Web Speech API / getUserMedia mic pipeline on the SPA document.
# Some platforms default to a restrictive Permissions-Policy; without
# ``microphone=(self)`` the composer mic can fail on production (Railway)
# even over HTTPS.
_SPA_DOCUMENT_HEADERS = {"Permissions-Policy": "microphone=(self)"}


def _mount_production_spa(fastapi_app: FastAPI) -> None:
    """Serve React static files when dist/ exists (omit for API-only / local Vite dev)."""
    index = _FRONTEND_DIST / "index.html"
    if not index.is_file():
        return

    assets_dir = _FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        fastapi_app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="ui_assets")

    @fastapi_app.get("/", include_in_schema=False)
    def _spa_index():
        return FileResponse(index, headers=_SPA_DOCUMENT_HEADERS)

    @fastapi_app.get("/{full_path:path}", include_in_schema=False)
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


def _resolve_host_mode() -> HostMode:
    raw = os.environ.get("BLUEBOT_HOST_MODE", "combined").strip().lower()
    if raw not in ("combined", "admin", "sales"):
        raise RuntimeError(
            f"BLUEBOT_HOST_MODE must be combined|admin|sales, got {raw!r}"
        )
    return raw  # type: ignore[return-value]


def _default_serve_spa(mode: HostMode) -> bool:
    raw = os.environ.get("BLUEBOT_SERVE_SPA")
    if raw is not None:
        return raw.strip() == "1"
    return mode in ("combined", "admin")


def create_app(
    *,
    mode: HostMode | None = None,
    serve_spa: bool | None = None,
) -> FastAPI:
    resolved_mode = mode or _resolve_host_mode()
    if resolved_mode not in ("combined", "admin", "sales"):
        raise RuntimeError(
            f"BLUEBOT_HOST_MODE must be combined|admin|sales, got {resolved_mode!r}"
        )

    fastapi_app = FastAPI(title="bluebot Orchestrator API", lifespan=_lifespan)
    _install_cors(fastapi_app)

    fastapi_app.include_router(config_router)
    fastapi_app.include_router(shares_router)
    fastapi_app.include_router(logo_router)

    if resolved_mode in ("combined", "admin"):
        fastapi_app.include_router(auth_router)
        fastapi_app.include_router(conversations_router)
        fastapi_app.include_router(admin_chat_router)
        fastapi_app.include_router(tickets_router)
        fastapi_app.include_router(artifacts_router)

    if resolved_mode in ("combined", "sales"):
        fastapi_app.include_router(sales_chat_router)

    if serve_spa is None:
        serve_spa = _default_serve_spa(resolved_mode)  # type: ignore[arg-type]
    if serve_spa:
        _mount_production_spa(fastapi_app)

    return fastapi_app


app = create_app()
