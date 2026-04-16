"""
app.py — Streamlit chat UI for the bluebot orchestrator.

Run with:
    streamlit run app.py

Authentication:
    Users log in with their bluebot (Auth0) account.
    The resulting access token is used as the bluebot Bearer token.
    Set AUTH0_DOMAIN_{ENV}, AUTH0_CLIENT_ID_{ENV}, AUTH0_API_AUDIENCE_{ENV},
    AUTH0_REALM, and optionally BLUEBOT_ENV in Streamlit secrets / env vars.
"""

import base64
import json
import os
import re
import time
from pathlib import Path

import streamlit as st

# Promote Streamlit secrets into environment variables BEFORE importing any
# module that reads os.environ at import/call time (store, agent, auth).
# DATABASE_URL must be set before store._use_postgres() is evaluated.
for _secret_key in ("ANTHROPIC_API_KEY", "DATABASE_URL"):
    if _secret_key not in os.environ:
        try:
            _val = st.secrets.get(_secret_key, "")
        except Exception:
            _val = ""
        if _val:
            os.environ[_secret_key] = _val

import auth
from agent import run_turn
import store
from summarizer import update_title
from streamlit_cookies_controller import CookieController

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="bluebot Assistant",
    page_icon="💧",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Global styles
# ---------------------------------------------------------------------------

def _app_logo_b64() -> str:
    try:
        p = Path(__file__).parent.parent / "bluebot.jpg"
        return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode()
    except Exception:
        return ""

_LOGO_SRC = _app_logo_b64()

st.markdown(
    f"""
    <style>
    /* ── Hide Streamlit chrome ─────────────────────────────────────────── */
    footer {{ display: none !important; }}
    h1 a, h2 a, h3 a {{ display: none !important; }}

    /* ── Sidebar ───────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {{
        background: #f0f5ff !important;
        border-right: 1px solid #dce7f8;
    }}
    [data-testid="stSidebar"] .stButton > button {{
        border-radius: 8px !important;
        font-size: 0.85rem !important;
    }}
    /* "New conversation" button accent */
    [data-testid="stSidebar"] .stButton:first-of-type > button {{
        background: linear-gradient(135deg, #3a5f9a, #4a80c0) !important;
        color: white !important;
        border: none !important;
        font-weight: 600 !important;
    }}

    /* ── Chat messages ─────────────────────────────────────────────────── */
    [data-testid="stChatMessage"] {{
        border-radius: 12px !important;
        margin-bottom: 0.25rem !important;
    }}

    /* ── Chat input ────────────────────────────────────────────────────── */
    [data-testid="stChatInput"] textarea {{
        border-radius: 12px !important;
        border: 1.5px solid #c8d8ee !important;
    }}
    [data-testid="stChatInput"] textarea:focus {{
        border-color: #4a80c0 !important;
        box-shadow: 0 0 0 3px rgba(74,128,192,0.12) !important;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Cookie controller — must be instantiated early so cookies are readable
# before auth.login_gate() runs.  Each browser has its own isolated cookie
# store, so this gives every user an independent login that survives refresh.
# ---------------------------------------------------------------------------

_cookies = CookieController()

# ---------------------------------------------------------------------------
# Authentication — blocks rendering until the user is logged in
# ---------------------------------------------------------------------------

token = auth.login_gate(_cookies)  # returns bearer token or calls st.stop()

# Stable user identifier for conversation scoping.
_uid: str = st.session_state.get("auth_user", "") or ""

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

def _load_conversation(conv_id: str) -> None:
    st.session_state.conversation_id = conv_id
    st.session_state.messages = store.load_messages(conv_id)
    st.session_state.token_pct = 0.0
    st.session_state.token_count = 0
    st.session_state.compressing = False
    st.query_params["conv"] = conv_id


def _new_conversation() -> None:
    """Enter a pending 'new chat' state — no DB row is created until the first message."""
    st.session_state.conversation_id = None
    st.session_state.messages = []
    st.session_state.token_pct = 0.0
    st.session_state.token_count = 0
    st.session_state.compressing = False
    st.query_params.pop("conv", None)


if "conversation_id" not in st.session_state:
    # Restore from URL on page refresh, fall back to most-recent or pending.
    url_conv = st.query_params.get("conv")
    existing_ids = {c["id"] for c in store.list_conversations(_uid)}
    if url_conv and url_conv in existing_ids:
        _load_conversation(url_conv)
    else:
        convs = store.list_conversations(_uid)
        if convs:
            _load_conversation(convs[0]["id"])
        else:
            _new_conversation()

# Detect a turn that was interrupted by a page refresh.
# A plain user-text message at the tail of history (not a tool_result list) means
# the user submitted something but the agent never replied.
if "resume_pending" not in st.session_state:
    msgs = st.session_state.messages
    if (
        msgs
        and msgs[-1]["role"] == "user"
        and isinstance(msgs[-1]["content"], str)
        and msgs[-1]["content"]
    ):
        # Pull it out of in-memory history — the processing section will re-add
        # it and run the agent as if the user just typed it.
        st.session_state.resume_pending = msgs[-1]["content"]
        st.session_state.messages = msgs[:-1]
    else:
        st.session_state.resume_pending = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IMAGE_RE = re.compile(r'!\[(.*?)\]\((.*?\.png)\)')


def _show_image(path: str, caption: str | None = None) -> None:
    """Render a local PNG as an inline base64 data URI.

    Unlike st.image(), this bypasses Streamlit's media file handler so images
    survive server restarts, redeploys, and script reruns without 404 errors.
    """
    if not os.path.exists(path):
        return
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    cap_html = f"<p style='text-align:center;font-size:0.85rem;color:grey'>{caption}</p>" if caption else ""
    st.markdown(
        f'<img src="data:image/png;base64,{b64}" style="width:100%;border-radius:6px">{cap_html}',
        unsafe_allow_html=True,
    )


def _relative_date(ts: int) -> str:
    """Return a human-friendly date label (Today / Yesterday / Mon DD)."""
    today     = time.localtime()
    conv_day  = time.localtime(ts)
    today_ord = today.tm_year * 365 + today.tm_yday
    conv_ord  = conv_day.tm_year * 365 + conv_day.tm_yday
    delta     = today_ord - conv_ord
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return time.strftime("%b %d", conv_day)


def _render_message(role: str, content) -> None:
    """
    Render a single chat message.

    If the content contains Markdown image references to local PNG files they
    are rendered with st.image() instead of as raw markdown text.
    """
    with st.chat_message(role):
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block["text"]
                elif hasattr(block, "text"):
                    text += block.text

        if not text:
            return

        # Split on image references so we can interleave text and images.
        parts = _IMAGE_RE.split(text)
        i = 0
        while i < len(parts):
            chunk = parts[i]
            if chunk:
                st.markdown(chunk)
            i += 1
            if i + 1 < len(parts):
                _alt  = parts[i]
                _path = parts[i + 1]
                if os.path.exists(_path):
                    _show_image(_path, caption=_alt or None)
                i += 2


# ---------------------------------------------------------------------------
# Sidebar — conversation management + settings
# ---------------------------------------------------------------------------

with st.sidebar:
    # Sidebar header with logo
    _logo_img = (
        f"<img src='{_LOGO_SRC}' style='width:32px;height:32px;border-radius:8px;"
        "object-fit:cover;margin-right:0.6rem;vertical-align:middle;'>"
        if _LOGO_SRC else ""
    )
    st.markdown(
        f"<div style='display:flex;align-items:center;padding:0.5rem 0 0.75rem;'>"
        f"{_logo_img}"
        f"<span style='font-weight:700;font-size:1rem;color:#1a2a4a;'>bluebot</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    if st.button("＋  New conversation", use_container_width=True):
        _new_conversation()
        st.rerun()

    convs = store.list_conversations(_uid)
    for c in convs:
        label    = c["title"] if c["title"] else "New conversation"
        date_str = _relative_date(c["updated_at"])
        is_active = c["id"] == st.session_state.conversation_id
        col_btn, col_del = st.columns([9, 1])
        with col_btn:
            btn_label = f"**{label}**\n{date_str}" if is_active else f"{label}\n{date_str}"
            if st.button(btn_label, key=f"conv_{c['id']}", use_container_width=True):
                _load_conversation(c["id"])
                st.rerun()
        with col_del:
            if st.button("✕", key=f"del_{c['id']}", help="Delete conversation"):
                store.delete_conversation(c["id"], _uid)
                if is_active:
                    remaining = [r for r in convs if r["id"] != c["id"]]
                    if remaining:
                        _load_conversation(remaining[0]["id"])
                    else:
                        _new_conversation()
                st.rerun()

    # Context usage — only shown once the user has sent at least one message
    if st.session_state.token_count > 0:
        st.divider()
        st.caption("Context usage")
        st.progress(
            min(st.session_state.token_pct, 1.0),
            text=(
                f"{st.session_state.token_pct:.0%} of 200k tokens "
                f"({st.session_state.token_count:,})"
            ),
        )
        if st.session_state.compressing:
            st.warning("Compressing older messages to free context...", icon="⚠️")

    # Account section at the bottom of the sidebar
    st.divider()
    _user = st.session_state.get("auth_user", "")
    if _user:
        st.caption(f"Signed in as **{_user}**")
    if st.button("Sign out", use_container_width=True):
        auth.logout(_cookies)


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------

_logo_header = (
    f"<img src='{_LOGO_SRC}' style='width:38px;height:38px;border-radius:10px;"
    "object-fit:cover;margin-right:0.6rem;vertical-align:middle;"
    "box-shadow:0 2px 8px rgba(58,111,168,0.18);'>"
    if _LOGO_SRC else "💧 "
)
st.markdown(
    f"<div style='text-align:center;padding:0.6rem 0 0.1rem;'>"
    f"<span style='display:inline-flex;align-items:center;justify-content:center;"
    f"font-size:1.45rem;font-weight:700;color:#1a2a4a;letter-spacing:-0.3px;'>"
    f"{_logo_header}bluebot Assistant"
    f"</span></div>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Render existing conversation history  /  empty-state welcome card
# ---------------------------------------------------------------------------

if st.session_state.messages:
    # Plots are stored inside tool_result JSON but rendered after the assistant reply
    # that follows them. We carry them forward until the next assistant message.
    _queued_plots: list[str] = []
    for msg in st.session_state.messages:
        role    = msg["role"]
        content = msg["content"]
        if isinstance(content, list) and content and isinstance(content[0], dict):
            if content[0].get("type") == "tool_result":
                # Harvest any plot paths from analyze_flow_data results
                for block in content:
                    try:
                        result = json.loads(block.get("content", "{}"))
                        for p in result.get("plot_paths", []):
                            if os.path.exists(p) and p not in _queued_plots:
                                _queued_plots.append(p)
                    except (json.JSONDecodeError, AttributeError, TypeError):
                        pass
                continue  # never render tool_result rows as chat bubbles
        _render_message(role, content)
        # Flush queued plots after each assistant message
        if role == "assistant" and _queued_plots:
            for path in _queued_plots:
                _show_image(path)
            _queued_plots.clear()
else:
    st.markdown(
        """
        <div style="
            max-width: 560px;
            margin: 3.5rem auto 2rem auto;
            padding: 2.25rem 2.5rem 2rem;
            border-radius: 16px;
            border: 1px solid #dce7f8;
            background: linear-gradient(160deg, #f5f8ff 0%, #edf3fc 100%);
            box-shadow: 0 4px 20px rgba(58,111,168,0.08);
            text-align: center;
        ">
            <p style="font-size: 1.1rem; font-weight: 700; color: #1a2a4a; margin-bottom: 0.4rem;">
                What would you like to do?
            </p>
            <p style="color: #5a6a88; font-size: 0.92rem; margin-bottom: 1.75rem;">
                Status and flow: use your serial number as-is. Pipe setup uses the physical serial on the meter.
            </p>
            <div style="display:flex; flex-direction:column; gap:0.6rem;">
                <div style="background:white;border:1px solid #dce7f8;border-radius:10px;
                            padding:0.65rem 1rem;font-size:0.9rem;color:#374151;cursor:default;">
                    💬 &ldquo;Run a health check on device BB8100015261&rdquo;
                </div>
                <div style="background:white;border:1px solid #dce7f8;border-radius:10px;
                            padding:0.65rem 1rem;font-size:0.9rem;color:#374151;cursor:default;">
                    💬 &ldquo;Analyse the last 7 days of flow for device BB8100015261&rdquo;
                </div>
                <div style="background:white;border:1px solid #dce7f8;border-radius:10px;
                            padding:0.65rem 1rem;font-size:0.9rem;color:#374151;cursor:default;">
                    💬 &ldquo;Is device BB8100015261 online and transmitting?&rdquo;
                </div>
                <div style="background:white;border:1px solid #dce7f8;border-radius:10px;
                            padding:0.65rem 1rem;font-size:0.9rem;color:#374151;cursor:default;">
                    💬 &ldquo;Configure pipe for serial SN123456: PVC, Schedule 40, 2 inch nominal, angle 45º&rdquo;
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------

# Disable the input box while the agent is running.
_is_processing = bool(st.session_state.get("_agent_queued"))

user_input = st.chat_input(
    "Health, flow, or pipe setup (serial number)...",
    disabled=_is_processing,
)

# Three sources of input, in priority order:
#   1. _agent_queued  — user message saved + sidebar refreshed; run agent now
#   2. resume_pending — page refreshed mid-turn; auto-resume
#   3. user_input     — fresh message from the chat box
_queued = st.session_state.get("_agent_queued")
_resume = st.session_state.resume_pending

if _resume and not user_input and not _queued:
    st.info("↩️ Resuming your previous request...")

# ── Determine active_input ───────────────────────────────────────────────────
if _queued:
    # User message is already in messages (index -1) and in DB.
    # The history loop above already rendered it; skip setup and run agent.
    active_input     = _queued
    st.session_state._agent_queued = None
    checkpoint       = len(st.session_state.messages) - 1
    _skip_user_setup = True
else:
    active_input     = user_input or _resume
    _skip_user_setup = False

if active_input:
    if not token:
        st.session_state._agent_queued = None
        st.error("Please enter your bluebot Bearer token in ⚙️ Settings (sidebar).")
        st.stop()

    if not _skip_user_setup:
        # ── Setup: persist user message, queue agent, rerun so the chat input
        #    is disabled before the (slow) agent starts. ─────────────────────
        st.session_state.resume_pending = None
        is_resume = bool(_resume and not user_input)

        if not is_resume and st.session_state.conversation_id is None:
            conv_id = store.create_conversation(_uid)
            st.session_state.conversation_id = conv_id
            st.query_params["conv"] = conv_id

        st.session_state.messages.append({"role": "user", "content": active_input})

        if not is_resume:
            store.append_messages(
                st.session_state.conversation_id,
                [{"role": "user", "content": active_input}],
            )

        if len(st.session_state.messages) == 1:
            store.set_title(st.session_state.conversation_id, active_input[:60])

        # Queue the agent and rerun — on the next render the input is disabled
        # and the history loop shows the user message before the agent starts.
        st.session_state._agent_queued = active_input
        st.rerun()

    # ── Agent run ────────────────────────────────────────────────────────────
    status_placeholder    = st.empty()
    assistant_placeholder = st.empty()
    pending_plot_paths: list[str] = []
    _streamed_chunks: list[str]   = []

    def _on_event(event: dict) -> None:
        kind = event["type"]
        if kind == "token_usage":
            st.session_state.token_pct   = event["pct"]
            st.session_state.token_count = event["tokens"]
            st.session_state.compressing = False
        elif kind == "compressing":
            st.session_state.token_pct   = event["pct"]
            st.session_state.token_count = event["tokens"]
            st.session_state.compressing = True
            status_placeholder.info(
                f"Compressing conversation history ({event['pct']:.0%} context used)..."
            )
        elif kind == "thinking":
            _streamed_chunks.clear()
            assistant_placeholder.empty()
            status_placeholder.info("Thinking...")
        elif kind == "text_delta":
            status_placeholder.empty()
            _streamed_chunks.append(event["text"])
            with assistant_placeholder.container():
                with st.chat_message("assistant"):
                    st.markdown("".join(_streamed_chunks))
        elif kind == "tool_call":
            labels = {
                "resolve_time_range": "Resolving time range",
                "check_meter_status": "Checking meter status",
                "analyze_flow_data":  "Analyzing flow data",
                "configure_meter_pipe": "Configuring meter pipe",
                "set_transducer_angle_only": "Setting transducer angle (SSA only)",
            }
            status_placeholder.info(f"{labels.get(event['tool'], event['tool'])}...")
        elif kind == "tool_progress":
            status_placeholder.info(event.get("message") or event.get("tool", "…"))
        elif kind == "tool_result":
            status = "done" if event["success"] else "failed"
            status_placeholder.info(f"{event['tool']} {status}")
            for p in event.get("plot_paths", []):
                if os.path.exists(p) and p not in pending_plot_paths:
                    pending_plot_paths.append(p)

    try:
        _, history_replaced = run_turn(
            st.session_state.messages,
            token,
            on_event=_on_event,
        )
        status_placeholder.empty()
        st.session_state.compressing = False

        # Save agent messages — or full thread if history was summarized (rate limit).
        if history_replaced:
            store.replace_conversation_messages(
                st.session_state.conversation_id,
                st.session_state.messages,
            )
        else:
            store.append_messages(
                st.session_state.conversation_id,
                st.session_state.messages[checkpoint + 1:],
            )
        update_title(st.session_state.conversation_id, st.session_state.messages)

        # Rerun so the chat input is re-enabled (_is_processing becomes False)
        # and the history loop re-renders the full conversation cleanly.
        st.rerun()

    except Exception as exc:
        status_placeholder.empty()
        assistant_placeholder.empty()
        st.error(f"Error: {exc}")
        del st.session_state.messages[checkpoint + 1:]
        st.rerun()
