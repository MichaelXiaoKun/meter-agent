"""
app.py — Streamlit chat UI for the bluebot orchestrator agent.

Run with:
    cd orchestrator
    streamlit run ui/app.py

The page opens automatically in your default browser.
Set BLUEBOT_TOKEN and ANTHROPIC_API_KEY in your environment, or enter the
bluebot token in the sidebar at runtime.
"""

import os
import re
import sys

import streamlit as st

# Resolve the orchestrator root so imports work regardless of cwd.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent import run_turn
import store
from summarizer import update_title

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="FlowIQ",
    page_icon="💧",
    layout="centered",
)

# ---------------------------------------------------------------------------
# Helper functions  (defined before any rendering code that calls them)
# ---------------------------------------------------------------------------

def _extract_plot_paths(text: str) -> list[str]:
    """Pull absolute PNG paths out of Markdown image syntax."""
    return re.findall(r'!\[.*?\]\((.*?\.png)\)', text)


def _strip_images(text: str) -> str:
    """Remove Markdown image tags so st.markdown doesn't try to load local paths."""
    return re.sub(r'!\[.*?\]\(.*?\.png\)', '', text).strip()


def _rebuild_display(messages: list[dict]) -> list[dict]:
    """
    Reconstruct a display list from a plain-dict messages list loaded from the store.

    Skips tool-result messages (user role with list content).
    """
    display = []
    for msg in messages:
        role    = msg["role"]
        content = msg["content"]
        if role == "user":
            if isinstance(content, str):
                display.append({"role": "user", "text": content, "plots": []})
            # list content = tool results → skip
        elif role == "assistant" and isinstance(content, list):
            texts = [
                b["text"] for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if texts:
                text = "\n".join(texts)
                display.append({
                    "role":  "assistant",
                    "text":  text,
                    "plots": _extract_plot_paths(text),
                })
    return display


def _tool_status_line(event: dict) -> str:
    labels = {
        "resolve_time_range": "Resolving time range",
        "check_meter_status": "Checking meter status",
        "analyze_flow_data":  "Analysing flow data",
        "configure_meter_pipe": "Configuring meter pipe",
        "set_transducer_angle_only": "Setting transducer angle (SSA only)",
        "sweep_transducer_angles": "Sweeping transducer angles",
        "set_zero_point": "Preparing set-zero-point review",
    }
    tool  = event["tool"]
    inp   = event.get("input", {})
    label = labels.get(tool, tool)

    if tool == "resolve_time_range":
        detail = f"\"{inp.get('description', '')}\""
    elif tool == "check_meter_status":
        detail = inp.get("serial_number", "")
    elif tool == "analyze_flow_data":
        detail = inp.get("serial_number", "")
    elif tool == "configure_meter_pipe":
        detail = inp.get("serial_number", "")
    elif tool in ("set_transducer_angle_only", "sweep_transducer_angles", "set_zero_point"):
        detail = inp.get("serial_number", "")
    else:
        detail = ""

    return f"{label}{': ' + detail if detail else ''}"


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "conversation_id" not in st.session_state:
    # None means "not yet created" — created lazily on first message.
    st.session_state.conversation_id = None

if "messages" not in st.session_state:
    st.session_state.messages = []

if "display" not in st.session_state:
    st.session_state.display = []

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    # --- Conversation management ---
    st.header("Conversations")

    if st.button("+ New conversation", use_container_width=True):
        st.session_state.conversation_id = None
        st.session_state.messages = []
        st.session_state.display  = []
        st.rerun()

    st.divider()

    convs = store.list_conversations("")
    for conv in convs:
        title     = (conv["title"] or "(untitled)")[:38]
        is_active = conv["id"] == st.session_state.conversation_id
        label     = f"▶  {title}" if is_active else f"    {title}"

        col_btn, col_del = st.columns([5, 1])
        with col_btn:
            if st.button(label, key=f"conv_{conv['id']}", use_container_width=True):
                if not is_active:
                    st.session_state.conversation_id = conv["id"]
                    st.session_state.messages = store.load_messages(conv["id"])
                    st.session_state.display  = _rebuild_display(st.session_state.messages)
                    st.rerun()
        with col_del:
            if st.button("✕", key=f"del_{conv['id']}"):
                store.delete_conversation(conv["id"], "")
                if is_active:
                    st.session_state.conversation_id = None
                    st.session_state.messages = []
                    st.session_state.display  = []
                st.rerun()

    # --- Configuration ---
    st.divider()
    st.header("Configuration")

    token = st.text_input(
        "bluebot Token",
        type="password",
        value=os.environ.get("BLUEBOT_TOKEN", ""),
        help="Your bluebot API bearer token.",
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        st.warning("ANTHROPIC_API_KEY is not set in your environment.")

    st.divider()
    st.caption("Powered by Claude · bluebot API")

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("💧 FlowIQ")
st.caption(
    "Health and flow: use the serial number as the user gave it. Pipe / angle tools use the physical serial on the meter."
)

# ---------------------------------------------------------------------------
# Render conversation history
# ---------------------------------------------------------------------------

for entry in st.session_state.display:
    with st.chat_message(entry["role"]):
        st.markdown(_strip_images(entry["text"]))
        for path in entry.get("plots", []):
            if os.path.exists(path):
                st.image(path, use_container_width=True)

# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------

if prompt := st.chat_input("Health, flow, or pipe setup (serial number)..."):

    if not token:
        st.error("Enter your bluebot token in the sidebar to continue.")
        st.stop()

    # Create the conversation on the first message of a new session.
    if st.session_state.conversation_id is None:
        st.session_state.conversation_id = store.create_conversation("", title=prompt[:60])

    # Show user message immediately.
    with st.chat_message("user"):
        st.markdown(prompt)

    st.session_state.display.append({"role": "user", "text": prompt, "plots": []})
    st.session_state.messages.append({"role": "user", "content": prompt})

    checkpoint = len(st.session_state.messages) - 1  # index of the user message we just appended

    # Run agent turn and stream status updates.
    with st.chat_message("assistant"):
        with st.status("Thinking...", expanded=True) as status:

            def on_event(event: dict) -> None:
                if event["type"] == "thinking":
                    status.update(label="Thinking...", state="running")

                elif event["type"] == "tool_call":
                    line = _tool_status_line(event)
                    status.update(label=line, state="running")
                    status.write(f"⟳  {line}")

                elif event["type"] == "tool_progress":
                    msg = event.get("message") or event.get("tool", "…")
                    status.update(label=msg, state="running")
                    status.write(f"⟳  {msg}")

                elif event["type"] == "tool_result":
                    icon = "✓" if event["success"] else "✗"
                    status.write(f"{icon}  {event['tool']} {'complete' if event['success'] else 'failed'}")
                    status.update(label="Thinking...", state="running")

            try:
                reply, history_replaced = run_turn(
                    st.session_state.messages, token, on_event=on_event
                )
                if history_replaced:
                    store.replace_conversation_messages(
                        st.session_state.conversation_id,
                        st.session_state.messages,
                    )
                else:
                    store.append_messages(
                        st.session_state.conversation_id,
                        st.session_state.messages[checkpoint:],
                    )
                update_title(st.session_state.conversation_id, st.session_state.messages)
                status.update(label="Done", state="complete", expanded=False)

            except Exception as exc:
                status.update(label="Error", state="error", expanded=True)
                st.error(str(exc))
                # Roll back so the conversation stays consistent.
                del st.session_state.messages[checkpoint:]
                st.session_state.display.pop()
                st.stop()

        # Render reply — text first, then plots inline.
        plots      = _extract_plot_paths(reply)
        clean_text = _strip_images(reply)
        st.markdown(clean_text)

        for path in plots:
            if os.path.exists(path):
                st.image(path, use_container_width=True)

    st.session_state.display.append({
        "role":  "assistant",
        "text":  reply,
        "plots": plots,
    })
