"""
agent.py — Conversational orchestrator agent.

Maintains full conversation history across turns and delegates to sub-agents
via tools: resolve_time_range, check_meter_status, analyze_flow_data, configure_meter_pipe,
set_transducer_angle_only.

Optional per-turn tool subset: ``ORCHESTRATOR_INTENT_ROUTER`` = off | rules (default) | haiku
— see ``_resolve_routed_tools`` and SSE event type ``intent_route``.

Usage (from an outer chat loop):
    messages = []
    reply, replaced = run_turn(messages, token="...")   # modifies messages in place
    # If replaced is True, persist messages with replace (not append) — see store.replace_conversation_messages.
"""

import json
import os
import re
import threading
import time

import anthropic

from tpm_window import (
    record_input_tokens,
    record_input_tokens_from_usage,
    sliding_input_tokens_sum,
    wait_for_sliding_tpm_headroom,
)

from processors.time_range import TOOL_DEFINITION as _TIME_RANGE_DEF, resolve_time_range
from tools.meter_status import TOOL_DEFINITION as _METER_STATUS_DEF, check_meter_status
from tools.meter_profile import (
    TOOL_DEFINITION as _METER_PROFILE_DEF,
    get_meter_profile,
)
from tools.meters_by_email import (
    TOOL_DEFINITION as _METERS_BY_EMAIL_DEF,
    list_meters_for_account,
)
from tools.flow_analysis import TOOL_DEFINITION as _FLOW_ANALYSIS_DEF, analyze_flow_data
from tools.pipe_configuration import (
    TOOL_DEFINITION as _PIPE_CONFIGURATION_DEF,
    configure_meter_pipe,
)
from tools.set_transducer_angle import (
    TOOL_DEFINITION as _SET_TRANSDUCER_ANGLE_DEF,
    set_transducer_angle_only,
)

TOOLS = [
    _TIME_RANGE_DEF,
    _METER_STATUS_DEF,
    _METER_PROFILE_DEF,
    _METERS_BY_EMAIL_DEF,
    _FLOW_ANALYSIS_DEF,
    _PIPE_CONFIGURATION_DEF,
    _SET_TRANSDUCER_ANGLE_DEF,
]

# ---------------------------------------------------------------------------
# Intent routing: optional cheap pass (rules or Haiku) to expose only a tool
# subset to the main orchestrator — cuts spurious analyze_flow_data / config calls.
# ORCHESTRATOR_INTENT_ROUTER:  off  — full TOOLS (legacy behaviour)
#                         rules  — keyword / regex heuristics (default)
#                          haiku — Haiku JSON classify, then rules on failure
# ---------------------------------------------------------------------------

_INTENT_LABELS = frozenset({"status", "flow", "config", "general"})

# Tool *names* (must match each TOOL_DEFINITION["name"]).
_BASE_READ_TOOLS: frozenset[str] = frozenset(
    {
        "resolve_time_range",
        "check_meter_status",
        "get_meter_profile",
        "list_meters_for_account",
    }
)

_TOOL_NAMES_BY_INTENT: dict[str, frozenset[str]] = {
    # Read-only / account discovery; no flow analysis, no pipe writes.
    "status": _BASE_READ_TOOLS,
    "general": _BASE_READ_TOOLS,
    # Historical flow + plots (expensive subprocess).
    "flow": _BASE_READ_TOOLS | frozenset({"analyze_flow_data"}),
    # Pipe / angle changes (mutations).
    "config": _BASE_READ_TOOLS
    | frozenset(
        {
            "configure_meter_pipe",
            "set_transducer_angle_only",
        }
    ),
}


def _intent_router_mode() -> str:
    raw = (os.environ.get("ORCHESTRATOR_INTENT_ROUTER") or "rules").strip().lower()
    if raw in ("0", "false", "no", "off", "none", "disabled"):
        return "off"
    if raw in ("haiku", "model", "llm"):
        return "haiku"
    return "rules"


def _last_user_text_for_routing(messages: list) -> str:
    """Most recent user message text (this turn's user utterance is usually last)."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str):
            return content.strip()[:12_000]
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif hasattr(block, "text"):
                    parts.append(str(getattr(block, "text", "") or ""))
            return " ".join(parts).strip()[:12_000]
    return ""


def _route_intent_rules(user_text: str) -> str:
    if not (user_text or "").strip():
        return "general"
    t = user_text.lower()
    # Order: more specific "what kind of work" first.
    if re.search(
        r"\b(flow|rate|trend|chart|graph|plot|time series|historical|analy[sz]e|"
        r"last \d+|past \d+|yesterday|today|this week|this month|"
        r"demand|duration curve|how much (water|flow)|usage over|peaks?|data for)\b",
        t,
    ):
        return "flow"
    if re.search(
        r"\b(config|pipe|material|diameter|transducer|angle|install|"
        r"pvc|hdpe|copper|npt|bspt|bs en|astm|sch \d+|schedule)\b",
        t,
    ):
        return "config"
    if re.search(
        r"\b(online|offline|status|signal|quality|battery|wifi|lora|lorawan|"
        r"health|is my meter|meter is|list meters?|serial)\b",
        t,
    ) or re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", t, re.I):
        return "status"
    return "general"


def _parse_haiku_intent_json(text: str) -> str | None:
    for m in re.finditer(r"\{[^{}]*\}", text):
        try:
            o = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        v = str(o.get("intent", "")).lower().strip()
        if v in _INTENT_LABELS:
            return v
    return None


def _route_intent_haiku(
    client: anthropic.Anthropic,
    user_text: str,
) -> str:
    if not (user_text or "").strip():
        return "general"
    system = (
        "You classify one user message for a bluebot ultrasonic flow meter assistant. "
        "Reply with ONLY valid JSON, no markdown: {\"intent\":\"<one word>\"}.\n"
        "intent must be exactly one of: status, flow, config, general.\n"
        "- status: online/offline, signal, battery, health, list meters by email, device metadata\n"
        "- flow: flow history, rates over time, charts, plots, time ranges, analysis\n"
        "- config: pipe material, size, standard, transducer angle, physical install\n"
        "- general: how-to, unclear, or small talk\n"
    )
    try:
        est = min(4000, max(400, len(user_text) // 2 + 400))
        wait_for_sliding_tpm_headroom(est, _TPM_INPUT_GUIDE_TOKENS)
        resp = client.messages.create(
            model=_CHEAP_MODEL,
            max_tokens=120,
            system=system,
            messages=[{"role": "user", "content": user_text[:8000]}],
        )
        record_input_tokens_from_usage(getattr(resp, "usage", None))
        raw = resp.content[0].text if resp.content else ""
    except Exception:
        return _route_intent_rules(user_text)
    parsed = _parse_haiku_intent_json(raw)
    if parsed:
        return parsed
    return _route_intent_rules(user_text)


def _tools_for_intent_label(label: str) -> list:
    """Return a non-empty subset of TOOLS; fallback to full list if something is off."""
    names = _TOOL_NAMES_BY_INTENT.get(label) or _TOOL_NAMES_BY_INTENT["general"]
    out = [t for t in TOOLS if t.get("name") in names]
    return out if out else list(TOOLS)


def _resolve_routed_tools(
    client: anthropic.Anthropic,
    messages: list,
    *,
    emit,
) -> tuple[list, str, str]:
    """
    Returns (tools_for_api, intent_label, source) where source is
    off | rules | haiku.
    """
    mode = _intent_router_mode()
    if mode == "off":
        return (list(TOOLS), "full", "off")
    user_text = _last_user_text_for_routing(messages)
    if mode == "haiku":
        label = _route_intent_haiku(client, user_text)
        src = "haiku"
    else:
        label = _route_intent_rules(user_text)
        src = "rules"
    tools = _tools_for_intent_label(label)
    if emit:
        emit(
            {
                "type": "intent_route",
                "intent": label,
                "source": src,
                "tools": [t.get("name") for t in tools if t.get("name") is not None],
            }
        )
    return (tools, label, src)


# Cheapest tier (Haiku). Override main chat with ORCHESTRATOR_MODEL (e.g. claude-sonnet-4-6).
_CHEAP_MODEL = "claude-haiku-4-5"
_DEFAULT_ORCHESTRATOR_MODEL = _CHEAP_MODEL
_MODEL = (os.environ.get("ORCHESTRATOR_MODEL") or _DEFAULT_ORCHESTRATOR_MODEL).strip() or _DEFAULT_ORCHESTRATOR_MODEL
_MODEL_CONTEXT_WINDOW = 200_000   # tokens

# ---------------------------------------------------------------------------
# Per-turn model selection (from the UI). We keep a tight allowlist so the
# frontend cannot pass an arbitrary string to Anthropic. The list is also
# exposed via /api/config so the UI can populate its picker.
#
# ORCHESTRATOR_ALLOWED_MODELS (comma-separated IDs) extends / overrides the
# built-in defaults if an operator wants to expose a different mix.
# ---------------------------------------------------------------------------


_MODEL_CATALOG: dict[str, dict[str, object]] = {
    "claude-haiku-4-5": {
        "label": "Haiku 4.5",
        "tier": "fast",
        "description": "Fast + cheap; great default for routine analysis.",
        "tpm_input_guide_tokens": 50_000,
    },
    "claude-sonnet-4-5": {
        "label": "Sonnet 4.5",
        "tier": "balanced",
        "description": "Balanced quality / cost; better multi-step reasoning.",
        "tpm_input_guide_tokens": 30_000,
    },
    "claude-opus-4-5": {
        "label": "Opus 4.5",
        "tier": "max",
        "description": "Highest quality, slowest + most expensive.",
        "tpm_input_guide_tokens": 30_000,
    },
}


def _configured_allowed_models() -> list[str]:
    raw = (os.environ.get("ORCHESTRATOR_ALLOWED_MODELS") or "").strip()
    if not raw:
        return list(_MODEL_CATALOG.keys())
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return [p for p in parts if p] or list(_MODEL_CATALOG.keys())


def list_available_models() -> list[dict[str, object]]:
    """Public metadata for /api/config — drives the UI's model picker."""
    out: list[dict[str, object]] = []
    default_id = _MODEL
    for mid in _configured_allowed_models():
        meta = _MODEL_CATALOG.get(mid) or {
            "label": mid,
            "tier": "custom",
            "description": "",
            "tpm_input_guide_tokens": 30_000,
        }
        out.append(
            {
                "id": mid,
                "label": meta["label"],
                "tier": meta["tier"],
                "description": meta["description"],
                "tpm_input_guide_tokens": meta["tpm_input_guide_tokens"],
                "is_default": mid == default_id,
            }
        )
    return out


def resolve_orchestrator_model(requested: str | None) -> str:
    """Return *requested* if in the allowlist, else the server default.

    Silently falling back prevents a misconfigured client from breaking chat;
    the UI learns the real model from the token-budget ``/api/config`` call.
    """
    if not requested:
        return _MODEL
    r = str(requested).strip()
    if not r:
        return _MODEL
    allowed = set(_configured_allowed_models())
    if r in allowed:
        return r
    return _MODEL
# Compress earlier so long threads (big tool payloads) stay smaller — helps TPM rate limits too.
_COMPRESS_THRESHOLD   = 0.40      # compress when input hits this fraction of context window
_COMPRESS_KEEP_RECENT = 5         # number of recent messages to leave untouched


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return float(raw)


def _default_tpm_input_guide_for_active_model() -> int:
    """
    Tier-1 ITPM from Anthropic docs: Haiku 4.5 = 50k, Sonnet 4 / Opus 4 = 30k.
    Override with ORCHESTRATOR_TPM_GUIDE_TOKENS when your org tier differs.
    """
    m = (os.environ.get("ORCHESTRATOR_MODEL") or _DEFAULT_ORCHESTRATOR_MODEL).strip().lower()
    if "haiku" in m:
        return 50_000
    return 30_000


def _resolve_tpm_input_guide_tokens() -> int:
    explicit = _env_int("ORCHESTRATOR_TPM_GUIDE_TOKENS")
    if explicit is not None:
        return explicit
    return _default_tpm_input_guide_for_active_model()


def _resolve_max_input_tokens_target(tpm_guide: int) -> int:
    explicit = _env_int("ORCHESTRATOR_MAX_INPUT_TOKENS_TARGET")
    if explicit is not None:
        return explicit
    frac = _env_float("ORCHESTRATOR_TPM_HEADROOM_FRACTION", 0.5)
    return int(tpm_guide * frac)


_TPM_INPUT_GUIDE_TOKENS = _resolve_tpm_input_guide_tokens()
_MAX_INPUT_TOKENS_TARGET = _resolve_max_input_tokens_target(_TPM_INPUT_GUIDE_TOKENS)


def _resolve_anthropic_api_key(override: str | None) -> str:
    """Prefer per-request key (browser), else server env."""
    k = (override or "").strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not k:
        raise RuntimeError(
            "Missing Anthropic API key. Add your key under **Claude API key** in the sidebar, "
            "or set ANTHROPIC_API_KEY on the server."
        )
    return k


def _estimate_stream_turn_tpm_cost(token_count: int) -> int:
    """
    Billable input for one orchestrator iteration: messages.count_tokens + messages.stream
    both charge ~the same input size; only stream is recorded in the sliding window.
    """
    f = _env_float("ORCHESTRATOR_TPM_NEXT_CALL_FACTOR", 2.05)
    return max(1, int(token_count * f))


def get_rate_limit_config_for_api() -> dict[str, object]:
    """Public knobs for /api/config and logging (matches run_turn budgeting)."""
    return {
        "tpm_input_guide_tokens": _TPM_INPUT_GUIDE_TOKENS,
        "max_input_tokens_target": _MAX_INPUT_TOKENS_TARGET,
        "model_context_window": _MODEL_CONTEXT_WINDOW,
        "tpm_headroom_fraction": _env_float("ORCHESTRATOR_TPM_HEADROOM_FRACTION", 0.5),
        "tpm_sliding_input_tokens_60s": sliding_input_tokens_sum(),
        "tpm_window_seconds": 60,
        "anthropic_server_configured": bool(
            (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        ),
        # Model selection: UI uses ``available_models`` to populate the
        # picker and falls back to ``default_model`` when the user has not
        # made a choice yet (or their stored choice is no longer allowed).
        "default_model": _MODEL,
        "available_models": list_available_models(),
    }

_SYSTEM_PROMPT = """\
You are a conversational assistant for bluebot ultrasonic flow meter analysis.
You help field engineers and operators check meter health, analyse flow data, and configure
pipe parameters by delegating to specialist sub-agents through tool calls.

Available tools:
  resolve_time_range     — convert natural language time expressions to Unix timestamps
  check_meter_status     — fetch current meter health (online state, signal quality, pipe config)
  get_meter_profile      — management-API device metadata + Wi-Fi vs LoRaWAN classification (by serial number)
  list_meters_for_account — list every meter attached to a Bluebot user account (by account email)
  analyze_flow_data      — analyse historical flow rate data over a time range
  configure_meter_pipe        — full pipe material/standard/size + transducer angle (management + MQTT)
  set_transducer_angle_only   — transducer angle only: MQTT **ssa** publish (no pipe catalog / spm)

Rules:
  1. **Serial number** for tools:
     - For **check_meter_status** and **analyze_flow_data**, pass the user's **serial_number**
       (e.g. BB8100015261) and call the tool. Do not ask for extra confirmation
       or terminology lectures before calling. If the API returns an error, explain it then.
     - For **configure_meter_pipe** and **set_transducer_angle_only**, use **serial_number** for
       management/MQTT as required by those tools.
  2. **Time ranges:** The API sends the user’s local IANA timezone (e.g. America/Denver) when
     the browser provides it. Ambiguous phrases ("today", "yesterday", "this morning", dates
     without an offset) are interpreted in that local timezone unless the user explicitly names
     a different one in their message (e.g. "in UTC", "Eastern time", "Tokyo").
     Always call resolve_time_range before analyze_flow_data when the user gives a time range
     in words. Translate the time expression to English before passing it as the description
     argument (e.g. "dernières 6 heures" → "last 6 hours", "最近6時間" → "last 6 hours").
  3. After calling resolve_time_range, always show the user the display_range string
     from the tool result (and you may mention resolved_label if helpful) and ask them
     to confirm before proceeding.
     Only call analyze_flow_data once the user confirms. If they correct the timezone,
     call resolve_time_range again with the adjusted description before proceeding.
  4. If resolve_time_range returns an error, relay it to the user and ask them to rephrase.
  5. If a sub-agent tool returns success=false, explain the error clearly and suggest a remedy.
  6. Ground every factual claim in your reply on tool results — never invent numbers.
  7. Do not convert Unix timestamps (range_start, range_end, or tool start/end integers)
     to wall-clock times yourself — LLMs often get this wrong. For human-readable times,
     use only display_range (and optionally resolved_label) from resolve_time_range, or
     display_range from analyze_flow_data. If you must cite raw seconds, give the integers
     without timezone interpretation.
  8. Keep replies concise: highlight key findings and let the user ask for detail.
  9. For configure_meter_pipe, collect serial_number, pipe_material, pipe_standard, pipe_size,
     and transducer_angle before calling. If any are missing, ask concise follow-ups first.
     Relay tool errors verbatim when helpful; do not guess MQTT or catalog outcomes.
  10. When the user wants **only** a transducer angle change (no pipe material/standard/size),
     use **set_transducer_angle_only** with serial_number and transducer_angle.
     Use **configure_meter_pipe** when they need pipe dimensions or a full pipe + angle push.
  11. Use **get_meter_profile** when the user asks about the meter's model, label, organization,
     network type, or whether it is Wi-Fi vs LoRaWAN. Also call it **before analyze_flow_data**
     whenever possible and pass through two fields from its result:
       a. ``network_type`` → the analyze_flow_data ``network_type`` input — tunes gap detection
          and coverage to the meter's physics (``wifi`` ≈ 2 s cadence, ``lorawan`` ≈ 12–60 s
          bursty cadence; ``unknown`` keeps the conservative 60 s cap).
       b. ``profile.deviceTimeZone`` → the analyze_flow_data ``meter_timezone`` input — renders
          the plot x-axes in the meter's local clock so they match the verified-facts wall times.
     Cite the classification reason verbatim when relevant.
  12. Use **list_meters_for_account** when the user asks questions keyed by an **email address**
     rather than a serial number — for example: "what meters does alice@acme.com have?",
     "list the devices on bob@example.com's account", "how many meters are registered to this email?".
     The user must supply the email verbatim in their message; do not guess or assume one.
     Stay email-centric in your reply: report the meter list back against the email the user gave,
     and do not introduce account ids or organization concepts the user did not ask about.
     After the list returns, offer to run check_meter_status / get_meter_profile / analyze_flow_data
     on a specific serial number of interest. Error handling:
       a. If the tool returns ``success: false``, relay the ``error`` field verbatim — it is already
          phrased for end users and tells you (via ``error_stage``) whether the problem was looking
          up the account, its ownership, or its meters.
       b. If ``success: true`` but ``meters`` is empty, use the ``notice`` field verbatim.
       c. If ``truncated`` is true, tell the user how many meters were returned vs the real total
          and ask them to narrow down (e.g. by a specific serial number of interest).
  13. **User-facing language (no implementation leakage).** Replies to the user must read like
     product answers, not engineering notes. Specifically:
       a. Never mention internal tool, function, module, environment-variable, or file names
          (e.g. ``analyze_flow_data``, ``resolve_time_range``, ``get_meter_profile``,
          ``verified_facts_precomputed``, ``baseline_quality``, ``BLUEBOT_*``, ``processors/``,
          ``sub-agent``, ``subprocess``, "the API", "the JSON bundle", "analysis_*.json").
          Talk about *capabilities* ("the meter analysis", "the time-range resolver") instead.
       b. Never disclose absolute filesystem paths or server paths (``/Users/...``,
          ``data-processing-agent/analyses/...``, Unix timestamp integers without context, etc.).
          Artefacts like plots are surfaced through the UI attachments the tools return; do not
          paste their raw paths into prose.
       c. When a capability is missing or a tool returns ``success=false``, refuse briefly in
          user terms and offer a concrete alternative — e.g. "I can't filter to business hours
          automatically yet. Want me to analyze a specific block like *Tue 8 AM – 5 PM Denver*
          instead?" — without explaining *why* the system can't do it (no references to
          missing filters, schemas, JSON files, or code).
       d. Do not speculate about what internal data *might* contain; only report what the tool
          results actually say.
"""


def _count_tokens(
    client: anthropic.Anthropic,
    messages: list,
    *,
    model: str | None = None,
    tools: list | None = None,
) -> int:
    """Return the input token count for the current conversation state.

    Per-turn *model* overrides the server default so the estimate matches
    the model that will actually handle this turn. Different Claude tiers
    can tokenise slightly differently, but more importantly this keeps the
    UI's ``token_usage`` events honest when the user switches model.
    *tools* should match the list passed to ``messages.stream`` (intent routing
    may pass a subset of ``TOOLS``).
    """
    tool_list = tools if tools is not None else TOOLS
    response = client.messages.count_tokens(
        model=model or _MODEL,
        system=_SYSTEM_PROMPT,
        tools=tool_list,
        messages=messages,
    )
    return response.input_tokens


def _compress_history(
    client: anthropic.Anthropic,
    messages: list,
    *,
    keep_recent: int | None = None,
) -> list:
    """
    Summarize older messages to reduce token usage.

    Keeps the last *keep_recent* messages verbatim (default _COMPRESS_KEEP_RECENT) and replaces
    everything before them with a single compressed summary message.
    Returns the messages list unchanged if summarization fails.
    """
    k = keep_recent if keep_recent is not None else _COMPRESS_KEEP_RECENT
    if len(messages) <= k:
        return messages

    older = messages[:-k]
    recent = messages[-k:]

    lines = []
    for msg in older:
        role    = msg["role"]
        content = msg["content"]
        if isinstance(content, str):
            lines.append(f"{role.capitalize()}: {content[:500]}")
        elif isinstance(content, list):
            for block in content:
                text = None
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block["text"]
                elif hasattr(block, "text"):
                    text = block.text
                if text:
                    lines.append(f"{role.capitalize()}: {text[:500]}")
                    break

    transcript = "\n".join(lines)

    try:
        # Haiku summarization call — rough input size from transcript + prompt overhead.
        summarize_est = min(
            _TPM_INPUT_GUIDE_TOKENS // 2,
            max(800, len(transcript) // 4 + 1500),
        )
        wait_for_sliding_tpm_headroom(summarize_est, _TPM_INPUT_GUIDE_TOKENS)
        summary_resp = client.messages.create(
            model=_CHEAP_MODEL,
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": (
                    "You are summarizing an earlier part of a conversation for context compression. "
                    "Write a concise factual summary (under 400 words) covering: devices discussed, "
                    "time ranges analyzed, key findings, and any decisions made. "
                    "Start with 'Earlier in this conversation:'\n\n"
                    f"{transcript}"
                ),
            }],
        )
        record_input_tokens_from_usage(getattr(summary_resp, "usage", None))
        summary_text = summary_resp.content[0].text.strip()
    except Exception:
        return messages

    summary_message = {
        "role": "user",
        "content": f"[Context summary — older messages compressed]\n{summary_text}",
    }
    return [summary_message] + recent


def _try_compress_history_inplace(
    client: anthropic.Anthropic,
    messages: list,
    *,
    keep_recent: int | None = None,
) -> bool:
    """
    Replace *messages* with a shorter summary + tail if compression succeeds and saves rows.
    Returns True if *messages* was mutated.
    """
    before = len(messages)
    compressed = _compress_history(client, messages, keep_recent=keep_recent)
    if compressed is messages or len(compressed) >= before:
        return False
    messages.clear()
    messages.extend(compressed)
    return True


def _collapse_entire_thread_to_summary(
    client: anthropic.Anthropic,
    messages: list,
) -> bool:
    """
    Last resort: replace the whole thread with one short user message (Haiku summary).
    Used when layered compression still leaves input above the TPM budget.
    """
    if not messages:
        return False
    preview = json.dumps(messages, default=str)
    if len(preview) > 120_000:
        preview = preview[:120_000] + "\n…[truncated for summarization]"
    try:
        collapse_est = min(
            int(_TPM_INPUT_GUIDE_TOKENS * 0.65),
            max(900, len(preview) // 4 + 2048),
        )
        wait_for_sliding_tpm_headroom(collapse_est, _TPM_INPUT_GUIDE_TOKENS)
        summary_resp = client.messages.create(
            model=_CHEAP_MODEL,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Summarize this entire conversation JSON for context compression. "
                        "Output under 600 words. Cover devices, serial numbers, time ranges, "
                        "tool outcomes, and open questions. Start with 'Thread summary:'.\n\n"
                        f"{preview}"
                    ),
                }
            ],
        )
        record_input_tokens_from_usage(getattr(summary_resp, "usage", None))
        summary_text = summary_resp.content[0].text.strip()
    except Exception:
        return False
    messages.clear()
    messages.append(
        {
            "role": "user",
            "content": f"[Full thread compressed — TPM budget]\n{summary_text}",
        }
    )
    return True


def _sleep_after_rate_limit(exc: anthropic.RateLimitError, attempt_index: int) -> float:
    """
    TPM is a rolling *per-minute* budget. After 429, wait before retrying so usage can drop.

    Uses Retry-After when present; otherwise scales with attempt (capped).
    """
    seconds: float
    try:
        raw = exc.response.headers.get("retry-after") if exc.response else None
        if raw is not None and str(raw).strip() != "":
            seconds = float(raw)
            if seconds > 0:
                time.sleep(seconds)
                return seconds
    except (TypeError, ValueError):
        pass
    seconds = min(10.0 * float(attempt_index), 65.0)
    time.sleep(seconds)
    return seconds


def _compress_until_under_input_budget(
    client: anthropic.Anthropic,
    messages: list,
    max_input_tokens: int,
    *,
    model: str | None = None,
    tools: list | None = None,
) -> bool:
    """
    Repeatedly summarize older turns until _count_tokens <= max_input_tokens or no progress.
    Returns True if *messages* was modified.
    """
    changed = False
    for _ in range(24):
        ntok = _count_tokens(client, messages, model=model, tools=tools)
        record_input_tokens(ntok)
        if ntok <= max_input_tokens:
            return changed
        round_progress = False
        for kr in (5, 4, 3, 2, 1):
            if len(messages) <= kr:
                continue
            if _try_compress_history_inplace(client, messages, keep_recent=kr):
                changed = True
                round_progress = True
                after = _count_tokens(client, messages, model=model, tools=tools)
                record_input_tokens(after)
                if after <= max_input_tokens:
                    return changed
        if not round_progress:
            break

    final_ntok = _count_tokens(client, messages, model=model, tools=tools)
    record_input_tokens(final_ntok)
    if final_ntok > max_input_tokens:
        if _collapse_entire_thread_to_summary(client, messages):
            changed = True
    return changed


def _run_analyze_flow_with_progress(
    inputs: dict,
    token: str,
    *,
    client_timezone: str | None,
    emit,
    anthropic_api_key: str | None = None,
) -> str:
    """
    Run analyze_flow_data in a worker thread so the main thread can emit SSE heartbeats.
    The subprocess can run for a long time with no other events otherwise.
    """
    result_holder: list = []
    exc_holder: list[BaseException] = []

    def worker() -> None:
        try:
            result_holder.append(
                analyze_flow_data(
                    inputs["serial_number"],
                    inputs["start"],
                    inputs["end"],
                    token,
                    display_timezone=client_timezone,
                    anthropic_api_key=anthropic_api_key,
                    network_type=inputs.get("network_type"),
                    meter_timezone=inputs.get("meter_timezone"),
                )
            )
        except BaseException as e:
            exc_holder.append(e)

    thread = threading.Thread(target=worker, daemon=True, name="analyze_flow_data")
    thread.start()

    elapsed_chunks = 0
    while True:
        thread.join(timeout=4.0)
        if not thread.is_alive():
            break
        elapsed_chunks += 1
        emit(
            {
                "type": "tool_progress",
                "tool": "analyze_flow_data",
                "message": (
                    f"Still analyzing flow data… (~{elapsed_chunks * 4}s elapsed)"
                ),
            }
        )

    if exc_holder:
        return json.dumps({"error": str(exc_holder[0])}, default=str)
    if not result_holder:
        return json.dumps({"error": "analyze_flow_data produced no result"}, default=str)
    return json.dumps(result_holder[0], default=str)


def _sse_tool_succeeded(result_dict: dict) -> bool:
    """
    Derive UX success for SSE ``tool_result`` events.
    Prefer an explicit ``success`` bool when present (all current tools set it);
    otherwise treat only missing/empty ``error`` as ok (legacy payload).
    """
    s = result_dict.get("success")
    if isinstance(s, bool):
        return s
    err = result_dict.get("error")
    return err is None or err == ""


def _dispatch(
    name: str,
    inputs: dict,
    token: str,
    *,
    client_timezone: str | None = None,
    anthropic_api_key: str | None = None,
) -> str:
    """Route a tool call to the correct function and return the result as JSON."""
    if name == "resolve_time_range":
        result = resolve_time_range(
            inputs["description"],
            user_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
        )

    elif name == "check_meter_status":
        result = check_meter_status(
            inputs["serial_number"],
            token,
            anthropic_api_key=anthropic_api_key,
        )

    elif name == "get_meter_profile":
        result = get_meter_profile(
            inputs["serial_number"],
            token,
        )

    elif name == "list_meters_for_account":
        result = list_meters_for_account(
            inputs["email"],
            token,
            limit=inputs.get("limit"),
        )

    elif name == "analyze_flow_data":
        result = analyze_flow_data(
            inputs["serial_number"],
            inputs["start"],
            inputs["end"],
            token,
            display_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
            network_type=inputs.get("network_type"),
            meter_timezone=inputs.get("meter_timezone"),
        )

    elif name == "configure_meter_pipe":
        result = configure_meter_pipe(
            inputs["serial_number"],
            inputs["pipe_material"],
            inputs["pipe_standard"],
            inputs["pipe_size"],
            inputs["transducer_angle"],
            token,
            anthropic_api_key=anthropic_api_key,
        )

    elif name == "set_transducer_angle_only":
        result = set_transducer_angle_only(
            inputs["serial_number"],
            inputs["transducer_angle"],
            token,
            anthropic_api_key=anthropic_api_key,
        )

    else:
        result = {"error": f"Unknown tool: {name}"}

    return json.dumps(result, default=str)


def run_turn(
    messages: list,
    token: str,
    on_event=None,
    *,
    client_timezone: str | None = None,
    anthropic_api_key: str | None = None,
    model: str | None = None,
) -> str:
    """
    Process one conversational turn.

    Appends the assistant's response (and any intermediate tool exchanges) to
    `messages` in place so that the next call retains full context.

    Args:
        messages:   Full conversation history (list of role/content dicts).
                    Modified in place — pass the same list on every turn.
        token:      bluebot Bearer token forwarded to sub-agent tool calls.
        anthropic_api_key: Optional. When set (e.g. from web UI), used instead of ANTHROPIC_API_KEY.
        model:      Optional Claude model ID (e.g. "claude-sonnet-4-5"). Must be in the
                    server's allowlist (see ``resolve_orchestrator_model``) — invalid or
                    missing values fall back to the server default.
        client_timezone: Optional IANA zone from the browser (e.g. America/New_York).
                    Used for resolve_time_range and analyze_flow_data display_range when set.
        on_event:   Optional callable(event: dict) for progress updates.
                    Fired before and after each tool call with:
                      {"type": "token_usage",  "tokens": int, "pct": float}  — before each API call
                      {"type": "compressing",  "tokens": int, "pct": float}  — when compression fires
                      {"type": "intent_route",  "intent": str, "source": str, "tools": list}
                          — when ORCHESTRATOR_INTENT_ROUTER is not off (rules/haiku pass)
                      {"type": "tool_call",    "tool": str, "input": dict}
                      {"type": "tool_result",  "tool": str, "success": bool}
                      {"type": "tool_progress", "tool": str, "message": str}  — long tools (flow analysis)
                      {"type": "thinking"}  — while waiting for the LLM

    Returns:
        (assistant_reply_text, history_replaced): *history_replaced* is True if messages were
        replaced with a summary (e.g. after a rate limit) — callers must persist with replace, not append.
    """
    def _emit(event: dict):
        if on_event:
            on_event(event)

    _anthropic_key = _resolve_anthropic_api_key(anthropic_api_key)
    # Resolve once; subsequent iterations / rate-limit retries all use the
    # same model so a user-picked Opus turn doesn't silently downgrade to
    # the server default halfway through.
    active_model = resolve_orchestrator_model(model)
    client = anthropic.Anthropic(api_key=_anthropic_key)
    history_replaced = False
    active_tools, _, _ = _resolve_routed_tools(client, messages, emit=_emit)

    while True:
        token_count = _count_tokens(
            client, messages, model=active_model, tools=active_tools
        )
        pct = token_count / _MODEL_CONTEXT_WINDOW
        _emit({"type": "token_usage", "tokens": token_count, "pct": pct, "model": active_model})

        # Stay under ORCHESTRATOR_MAX_INPUT_TOKENS_TARGET (default: headroom fraction × TPM guide).
        if token_count > _MAX_INPUT_TOKENS_TARGET:
            _emit(
                {
                    "type": "compressing",
                    "tokens": token_count,
                    "pct": pct,
                    "target_max_input": _MAX_INPUT_TOKENS_TARGET,
                    "tpm_guide": _TPM_INPUT_GUIDE_TOKENS,
                    "reason": "input_budget",
                }
            )
            if _compress_until_under_input_budget(
                client, messages, _MAX_INPUT_TOKENS_TARGET,
                model=active_model, tools=active_tools,
            ):
                history_replaced = True
            token_count = _count_tokens(
                client, messages, model=active_model, tools=active_tools
            )
            pct = token_count / _MODEL_CONTEXT_WINDOW
            _emit({"type": "token_usage", "tokens": token_count, "pct": pct, "model": active_model})
            if token_count > _MAX_INPUT_TOKENS_TARGET:
                raise RuntimeError(
                    f"Could not compress context below {_MAX_INPUT_TOKENS_TARGET} input tokens "
                    f"(still at {token_count}). Shorten the thread or wait and retry."
                )

        if pct >= _COMPRESS_THRESHOLD:
            _emit({"type": "compressing", "tokens": token_count, "pct": pct})
            api_messages = _compress_history(client, messages)
        else:
            api_messages = messages

        wait_for_sliding_tpm_headroom(
            _estimate_stream_turn_tpm_cost(token_count),
            _TPM_INPUT_GUIDE_TOKENS,
        )
        _emit({"type": "thinking"})
        response = None
        stream_attempt = 0
        while stream_attempt < 8:
            stream_attempt += 1
            try:
                with client.messages.stream(
                    model=active_model,
                    max_tokens=4096,
                    system=_SYSTEM_PROMPT,
                    tools=active_tools,
                    messages=api_messages,
                ) as stream:
                    for text_delta in stream.text_stream:
                        _emit({"type": "text_delta", "text": text_delta})
                    response = stream.get_final_message()
                if getattr(response, "usage", None):
                    record_input_tokens_from_usage(response.usage)
                else:
                    record_input_tokens(token_count)
                break
            except anthropic.RateLimitError as exc:
                _emit(
                    {
                        "type": "compressing",
                        "tokens": token_count,
                        "pct": pct,
                        "target_max_input": _MAX_INPUT_TOKENS_TARGET,
                        "reason": "rate_limit",
                    }
                )
                if _compress_until_under_input_budget(
                    client, messages, _MAX_INPUT_TOKENS_TARGET,
                    model=active_model, tools=active_tools,
                ):
                    history_replaced = True
                token_count = _count_tokens(
                    client, messages, model=active_model, tools=active_tools
                )
                pct = token_count / _MODEL_CONTEXT_WINDOW
                _emit({"type": "token_usage", "tokens": token_count, "pct": pct, "model": active_model})
                if token_count > _MAX_INPUT_TOKENS_TARGET:
                    raise RuntimeError(
                        f"Context still above {_MAX_INPUT_TOKENS_TARGET} tokens after compression "
                        f"({token_count} tokens). Wait one minute for rate limits to reset or start a new chat."
                    )
                if pct >= _COMPRESS_THRESHOLD:
                    _emit({"type": "compressing", "tokens": token_count, "pct": pct})
                    api_messages = _compress_history(client, messages)
                else:
                    api_messages = messages
                waited = _sleep_after_rate_limit(exc, stream_attempt)
                _emit(
                    {
                        "type": "thinking",
                        "rate_limit_wait_seconds": waited,
                        "attempt": stream_attempt,
                    }
                )
                wait_for_sliding_tpm_headroom(
                    _estimate_stream_turn_tpm_cost(token_count),
                    _TPM_INPUT_GUIDE_TOKENS,
                )

        if response is None:
            raise RuntimeError(
                "Claude API rate limit (tokens per minute) persisted after compressing context, "
                f"waiting, and {stream_attempt} stream attempts. Wait ~1 minute and send again, "
                "or start a new chat."
            )

        if response.stop_reason == "end_turn":
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text, history_replaced
            return "(No response)", history_replaced

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    _emit({"type": "tool_call", "tool": block.name, "input": block.input})
                    if block.name == "analyze_flow_data":
                        result_json = _run_analyze_flow_with_progress(
                            block.input,
                            token,
                            client_timezone=client_timezone,
                            emit=_emit,
                            anthropic_api_key=_anthropic_key,
                        )
                    else:
                        result_json = _dispatch(
                            block.name,
                            block.input,
                            token,
                            client_timezone=client_timezone,
                            anthropic_api_key=_anthropic_key,
                        )
                    result_dict = json.loads(result_json)
                    ok = _sse_tool_succeeded(result_dict)
                    event: dict = {
                        "type": "tool_result",
                        "tool": block.name,
                        "success": ok,
                    }
                    if not ok:
                        emsg = result_dict.get("error")
                        if emsg not in (None, ""):
                            event["message"] = str(emsg)[:500]
                    if block.name == "analyze_flow_data":
                        event["plot_paths"] = result_dict.get("plot_paths", [])
                        ptz = result_dict.get("plot_timezone")
                        if ptz:
                            event["plot_timezone"] = ptz
                        sums = result_dict.get("plot_summaries")
                        if sums:
                            event["plot_summaries"] = sums
                        aj = result_dict.get("analysis_json_path")
                        if aj:
                            event["analysis_json_path"] = aj
                    _emit(event)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_json,
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            break

    return "(Unexpected stop reason)", history_replaced
