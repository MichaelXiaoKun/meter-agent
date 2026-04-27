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
import logging
import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import threading
import time

import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".."))

import httpx

from config_workflow import (
    PendingConfigAction,
    clear_pending_actions_for_tests,
    consume_pending_action,
    create_pending_action,
    get_pending_action,
    user_scope_from_token,
    validate_pending_action,
)
from llm import get_provider, LLMRateLimitError
from llm.registry import MODEL_CATALOG, get_cheap_model

from tpm_window import (
    record_input_tokens,
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
from tools.meter_compare import (
    TOOL_DEFINITION as _METER_COMPARE_DEF,
    compare_meters,
)
from tools.flow_analysis import (
    TOOL_DEFINITION as _FLOW_ANALYSIS_DEF,
    analyze_flow_data,
    analyze_flow_inputs_error_payload,
)
from tools.pipe_configuration import (
    TOOL_DEFINITION as _PIPE_CONFIGURATION_DEF,
    configure_meter_pipe,
)
from tools.set_transducer_angle import (
    TOOL_DEFINITION as _SET_TRANSDUCER_ANGLE_DEF,
    set_transducer_angle_only,
)
from tools.batch_flow_analysis import (
    TOOL_DEFINITION as _BATCH_FLOW_ANALYSIS_DEF,
    batch_analyze_flow,
)
from message_sanitize import messages_for_anthropic_api  # still used for _rough_input_token_fallback
from observability import emit_event, turn_context, timed
from prompts import load_system_prompt

TOOLS = [
    _TIME_RANGE_DEF,
    _METER_STATUS_DEF,
    _METER_PROFILE_DEF,
    _METERS_BY_EMAIL_DEF,
    _METER_COMPARE_DEF,
    _FLOW_ANALYSIS_DEF,
    _BATCH_FLOW_ANALYSIS_DEF,
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
        "compare_meters",
    }
)

_TOOL_NAMES_BY_INTENT: dict[str, frozenset[str]] = {
    # Read-only / account discovery; no flow analysis, no pipe writes.
    "status": _BASE_READ_TOOLS,
    "general": _BASE_READ_TOOLS,
    # Historical flow + plots (expensive subprocess).
    "flow": _BASE_READ_TOOLS | frozenset({"analyze_flow_data", "batch_analyze_flow"}),
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


# How many recent **user** messages to concatenate for intent routing. Follow-ups like
# "it's BB81…" (serial only) must inherit flow/config intent from earlier user lines;
# otherwise rules classify as ``general`` and ``analyze_flow_data`` is stripped from the
# tool list — models then correctly report the tool as unavailable.
_INTENT_ROUTE_USER_LOOKBACK = 4


def _plain_text_from_user_message(m: dict) -> str:
    """Extract searchable user text from one message (text blocks only)."""
    content = m.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif hasattr(block, "text"):
                parts.append(str(getattr(block, "text", "") or ""))
        return " ".join(parts).strip()
    return ""


def _recent_user_text_for_routing(messages: list) -> str:
    """
    Last N user utterances, oldest first, joined by newlines.

    Using only the final user line breaks multi-step flows (e.g. user asks for a 2-hour
    analysis, then replies with a serial): the last line has no flow keywords, intent
    becomes ``general``, and expensive tools are hidden from the provider.
    """
    blobs: list[str] = []
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") != "user":
            continue
        t = _plain_text_from_user_message(m)
        if t:
            blobs.append(t)
        if len(blobs) >= _INTENT_ROUTE_USER_LOOKBACK:
            break
    blobs.reverse()
    return "\n".join(blobs).strip()[:12_000]


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
    provider,
    cheap_model: str,
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
        resp = provider.complete(
            cheap_model,
            [{"role": "user", "content": user_text[:8000]}],
            system=system,
            tools=[],
            max_tokens=120,
        )
        record_input_tokens(resp.input_tokens)
        raw = resp.text
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
    provider,
    cheap_model: str,
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
    user_text = _recent_user_text_for_routing(messages)
    if mode == "haiku":
        label = _route_intent_haiku(provider, cheap_model, user_text)
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


# Default orchestrator model — override with ORCHESTRATOR_MODEL env var.
# Supported: any model ID in meter_agent/llm/registry.py MODEL_CATALOG.
_DEFAULT_ORCHESTRATOR_MODEL = "claude-haiku-4-5"
_MODEL = (os.environ.get("ORCHESTRATOR_MODEL") or _DEFAULT_ORCHESTRATOR_MODEL).strip() or _DEFAULT_ORCHESTRATOR_MODEL

# ---------------------------------------------------------------------------
# Per-turn model selection (from the UI). We keep a tight allowlist so the
# frontend cannot pass an arbitrary string to the provider. The list is also
# exposed via /api/config so the UI can populate its picker.
#
# ORCHESTRATOR_ALLOWED_MODELS (comma-separated IDs) extends / overrides the
# built-in defaults if an operator wants to expose a different mix.
# ---------------------------------------------------------------------------


def _configured_allowed_models() -> list[str]:
    raw = (os.environ.get("ORCHESTRATOR_ALLOWED_MODELS") or "").strip()
    if not raw:
        return list(MODEL_CATALOG.keys())
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return [p for p in parts if p] or list(MODEL_CATALOG.keys())


def list_available_models() -> list[dict[str, object]]:
    """Public metadata for /api/config — drives the UI's model picker."""
    out: list[dict[str, object]] = []
    default_id = _MODEL
    for mid in _configured_allowed_models():
        meta = MODEL_CATALOG.get(mid) or {
            "label": mid,
            "provider": "unknown",
            "tier": "custom",
            "description": "",
            "tpm_input_guide_tokens": 30_000,
        }
        out.append(
            {
                "id": mid,
                "label": meta["label"],
                "provider": meta.get("provider", "unknown"),
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


def _max_tool_rounds_per_turn() -> int:
    """
    Maximum LLM↔tool iterations for one user message (outer ``run_turn`` loop).

    Stops runaway tool loops. Override with ORCHESTRATOR_MAX_TOOL_ROUNDS (clamped 4…128).
    """
    explicit = _env_int("ORCHESTRATOR_MAX_TOOL_ROUNDS")
    if explicit is not None:
        return max(4, min(int(explicit), 128))
    return 32


# Tools whose result is a deterministic function of their inputs *within* one turn.
# Safe to cache and replay on duplicate calls in the same turn; a post-mutation
# reread is still sound because :func:`_invalidate_dedupe_for_write` drops any
# cached read tied to a serial the write tool just touched (see rule 11).
_DEDUPABLE_READ_TOOLS: frozenset[str] = frozenset(
    {
        "resolve_time_range",
        "check_meter_status",
        "get_meter_profile",
        "list_meters_for_account",
        "compare_meters",
        "analyze_flow_data",
    }
)

# Write tools are *never* cached: each call is an MQTT / management push and
# must reach the device even when the arguments look identical.
_WRITE_TOOLS: frozenset[str] = frozenset(
    {"configure_meter_pipe", "set_transducer_angle_only"}
)

# These tools always run serially even when multiple tool_use blocks arrive in
# one response: writes for rule-11 verify-after-write correctness; flow analysis
# because its SSE heartbeats are per-meter and interleaving them would confuse
# the UI timeline. Fan-out flow comparisons go through batch_analyze_flow instead.
_SERIAL_ONLY_TOOLS: frozenset[str] = _WRITE_TOOLS | frozenset({"analyze_flow_data"})
_MAX_PARALLEL_TOOL_WORKERS = 6


def _per_turn_tool_dedupe_key(tool_name: str, inp_d: dict) -> str | None:
    """Return a stable cache key for a read-only tool call, else ``None``.

    Canonicalises the whole args dict via ``sort_keys=True`` so ``{"a": 1,
    "b": 2}`` and ``{"b": 2, "a": 1}`` hit the same entry. Write tools and
    unknown tools always return ``None`` so they bypass the cache.
    """
    if tool_name not in _DEDUPABLE_READ_TOOLS:
        return None
    return json.dumps(
        {"tool": tool_name, "args": inp_d},
        sort_keys=True,
        default=str,
    )


def _invalidate_dedupe_for_write(
    dedupe_cache: dict[str, tuple[str, str | None]],
    tool_name: str,
    inp_d: dict,
) -> list[str]:
    """Drop cached reads that reference the serial this write just mutated.

    Returns the list of invalidated cache keys (for telemetry). Rule 11
    ("verify after configuration") assumes subsequent ``check_meter_status``
    / ``get_meter_profile`` calls re-hit the device, so we must not hand
    back a stale cached read after a write succeeds.
    """
    if tool_name not in _WRITE_TOOLS:
        return []
    serial = str(inp_d.get("serial_number") or "").strip() or None
    if serial is None:
        return []
    dropped: list[str] = [
        key for key, (_json, tagged) in dedupe_cache.items() if tagged == serial
    ]
    for key in dropped:
        dedupe_cache.pop(key, None)
    return dropped


def _default_tpm_input_guide_for_active_model() -> int:
    """
    Default input-TPM guide pulled from the model catalog.
    Override with ORCHESTRATOR_TPM_GUIDE_TOKENS when your org tier differs.
    """
    m = (os.environ.get("ORCHESTRATOR_MODEL") or _DEFAULT_ORCHESTRATOR_MODEL).strip()
    entry = MODEL_CATALOG.get(m, {})
    return int(entry.get("tpm_input_guide_tokens", 30_000))


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


def _resolve_api_key_override(override: str | None) -> str | None:
    """Return a non-empty key override string, or None (provider factory reads env vars)."""
    k = (override or "").strip()
    return k or None


def _estimate_stream_turn_tpm_cost(token_count: int) -> int:
    """
    Billable input for one orchestrator iteration: messages.count_tokens + messages.stream
    both charge ~the same input size; only stream is recorded in the sliding window.
    """
    f = _env_float("ORCHESTRATOR_TPM_NEXT_CALL_FACTOR", 2.05)
    return max(1, int(token_count * f))


def get_rate_limit_config_for_api() -> dict[str, object]:
    """Public knobs for /api/config and logging (matches run_turn budgeting)."""
    active_model_ctx = int(MODEL_CATALOG.get(_MODEL, {}).get("context_window", 200_000))
    return {
        "tpm_input_guide_tokens": _TPM_INPUT_GUIDE_TOKENS,
        "max_input_tokens_target": _MAX_INPUT_TOKENS_TARGET,
        "model_context_window": active_model_ctx,
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
        "max_tool_rounds_per_turn": _max_tool_rounds_per_turn(),
    }

_SYSTEM_PROMPT, _SYSTEM_PROMPT_VERSION = load_system_prompt()

_log = logging.getLogger(__name__)


def _rough_input_token_fallback(messages: list) -> int:
    """When ``count_tokens`` is unreachable, approximate from text length (+ system/tools fudge)."""
    safe = messages_for_anthropic_api(messages)
    char_n = 0
    for msg in safe:
        content = msg.get("content")
        if isinstance(content, str):
            char_n += len(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    char_n += len(str(block.get("text", "")))
                elif btype == "tool_result":
                    inner = block.get("content")
                    if isinstance(inner, str):
                        char_n += len(inner)
                    elif isinstance(inner, list):
                        for p in inner:
                            if isinstance(p, dict) and p.get("type") == "text":
                                char_n += len(str(p.get("text", "")))
    # ~4 chars per token; add conservative system/tool overhead.
    m = (os.environ.get("ORCHESTRATOR_MODEL") or _DEFAULT_ORCHESTRATOR_MODEL).strip()
    ctx = int(MODEL_CATALOG.get(m, {}).get("context_window", 200_000))
    return min(max(char_n // 4 + 12_000, 1), ctx)


def _count_tokens(
    provider,
    messages: list,
    *,
    model: str | None = None,
    tools: list | None = None,
) -> int:
    """Return the input token count for the current conversation state.

    Per-turn *model* overrides the server default so the estimate matches
    the model that will actually handle this turn.
    *tools* should match the list passed to the stream call (intent routing
    may pass a subset of ``TOOLS``).
    """
    tool_list = tools if tools is not None else TOOLS
    try:
        return provider.count_tokens(
            model or _MODEL,
            messages,
            system=_SYSTEM_PROMPT,
            tools=tool_list,
        )
    except Exception as exc:
        n = _rough_input_token_fallback(messages)
        _log.warning(
            "count_tokens failed (%s); using rough estimate %s input tokens for budgeting",
            exc.__class__.__name__,
            n,
        )
        return n


def _compress_history(
    provider,
    cheap_model: str,
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
                if text:
                    lines.append(f"{role.capitalize()}: {text[:500]}")
                    break

    transcript = "\n".join(lines)

    try:
        summarize_est = min(
            _TPM_INPUT_GUIDE_TOKENS // 2,
            max(800, len(transcript) // 4 + 1500),
        )
        wait_for_sliding_tpm_headroom(summarize_est, _TPM_INPUT_GUIDE_TOKENS)
        summary_resp = provider.complete(
            cheap_model,
            [{
                "role": "user",
                "content": (
                    "You are summarizing an earlier part of a conversation for context compression. "
                    "Write a concise factual summary (under 400 words) covering: devices discussed, "
                    "time ranges analyzed, key findings, and any decisions made. "
                    "Start with 'Earlier in this conversation:'\n\n"
                    f"{transcript}"
                ),
            }],
            system="",
            tools=[],
            max_tokens=512,
        )
        record_input_tokens(summary_resp.input_tokens)
        summary_text = summary_resp.text.strip()
    except Exception:
        return messages

    summary_message = {
        "role": "user",
        "content": f"[Context summary — older messages compressed]\n{summary_text}",
    }
    return [summary_message] + recent


def _try_compress_history_inplace(
    provider,
    cheap_model: str,
    messages: list,
    *,
    keep_recent: int | None = None,
) -> bool:
    """
    Replace *messages* with a shorter summary + tail if compression succeeds and saves rows.
    Returns True if *messages* was mutated.
    """
    before = len(messages)
    compressed = _compress_history(provider, cheap_model, messages, keep_recent=keep_recent)
    if compressed is messages or len(compressed) >= before:
        return False
    messages.clear()
    messages.extend(compressed)
    return True


def _collapse_entire_thread_to_summary(
    provider,
    cheap_model: str,
    messages: list,
) -> bool:
    """
    Last resort: replace the whole thread with one short user message (cheap model summary).
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
        summary_resp = provider.complete(
            cheap_model,
            [{
                "role": "user",
                "content": (
                    "Summarize this entire conversation JSON for context compression. "
                    "Output under 600 words. Cover devices, serial numbers, time ranges, "
                    "tool outcomes, and open questions. Start with 'Thread summary:'.\n\n"
                    f"{preview}"
                ),
            }],
            system="",
            tools=[],
            max_tokens=1024,
        )
        record_input_tokens(summary_resp.input_tokens)
        summary_text = summary_resp.text.strip()
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


def _sleep_after_rate_limit(exc: LLMRateLimitError, attempt_index: int) -> float:
    """
    After a rate-limit error, wait before retrying so usage can drop.
    Uses Retry-After when the provider populated it; otherwise scales with attempt.
    """
    if exc.retry_after and exc.retry_after > 0:
        time.sleep(exc.retry_after)
        return exc.retry_after
    seconds = min(10.0 * float(attempt_index), 65.0)
    time.sleep(seconds)
    return seconds


def _compress_until_under_input_budget(
    provider,
    cheap_model: str,
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
        ntok = _count_tokens(provider, messages, model=model, tools=tools)
        record_input_tokens(ntok)
        if ntok <= max_input_tokens:
            return changed
        round_progress = False
        for kr in (5, 4, 3, 2, 1):
            if len(messages) <= kr:
                continue
            if _try_compress_history_inplace(provider, cheap_model, messages, keep_recent=kr):
                changed = True
                round_progress = True
                after = _count_tokens(provider, messages, model=model, tools=tools)
                record_input_tokens(after)
                if after <= max_input_tokens:
                    return changed
        if not round_progress:
            break

    final_ntok = _count_tokens(provider, messages, model=model, tools=tools)
    record_input_tokens(final_ntok)
    if final_ntok > max_input_tokens:
        if _collapse_entire_thread_to_summary(provider, cheap_model, messages):
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
    bad = analyze_flow_inputs_error_payload(
        inputs, display_timezone=client_timezone
    )
    if bad is not None:
        return json.dumps(bad, default=str)

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
                    analysis_mode=inputs.get("analysis_mode"),
                )
            )
        except BaseException as e:
            exc_holder.append(e)

    thread = threading.Thread(target=worker, daemon=True, name="analyze_flow_data")
    thread.start()
    sn = str(inputs.get("serial_number") or "").strip()
    tag = f" [{sn}]" if sn else ""
    # Sub-agent (data-processing) is silent for seconds — surface simple status
    # lines; the web UI maps these to the tool row in the activity timeline.
    emit(
        {
            "type": "tool_progress",
            "tool": "analyze_flow_data",
            "message": (
                f"Flow-analysis agent{tag}: started — loading data and running the pipeline…"
            ),
        }
    )

    elapsed_chunks = 0
    while True:
        thread.join(timeout=4.0)
        if not thread.is_alive():
            break
        elapsed_chunks += 1
        sec = elapsed_chunks * 4
        if sec <= 8:
            line = f"Flow-analysis agent{tag}: computing stats, gaps, and quality… ({sec}s)"
        elif sec <= 24:
            line = f"Flow-analysis agent{tag}: building plots and report — still working… ({sec}s)"
        else:
            line = f"Flow-analysis agent{tag}: still running (large range or I/O)… {sec}s"
        emit(
            {
                "type": "tool_progress",
                "tool": "analyze_flow_data",
                "message": line,
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


def _clip_activity(text: str, max_len: int) -> str:
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _flow_report_excerpt_max_chars() -> int:
    raw = os.environ.get("ORCHESTRATOR_FLOW_REPORT_EXCERPT_CHARS", "1800")
    try:
        n = int(raw)
    except ValueError:
        return 1800
    return max(0, min(n, 6000))


def _compact_report_excerpt(report: object) -> str | None:
    if not isinstance(report, str):
        return None
    text = report.strip()
    if not text:
        return None
    limit = _flow_report_excerpt_max_chars()
    if limit <= 0:
        return None
    if len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n\n")
    if nl > limit * 0.55:
        cut = cut[:nl]
    return cut.rstrip() + "\n\n…*(report excerpt truncated; use report_path for the full artifact)*"


def _compact_analysis_metadata(meta: object) -> dict:
    if not isinstance(meta, dict):
        return {}
    keep = (
        "analysis_mode",
        "requested_analysis_mode",
        "mode_selection_reasons",
        "fetch",
        "report_path",
    )
    return {k: meta.get(k) for k in keep if meta.get(k) is not None}


def _compact_download_artifacts(artifacts: object) -> list[dict]:
    if not isinstance(artifacts, list):
        return []
    out: list[dict] = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        filename = item.get("filename")
        if kind != "csv" or not isinstance(filename, str) or not filename.endswith(".csv"):
            continue
        clean = {
            "kind": "csv",
            "title": item.get("title") if isinstance(item.get("title"), str) else "Flow data CSV",
            "filename": filename,
        }
        row_count = item.get("row_count")
        if isinstance(row_count, int):
            clean["row_count"] = row_count
        url = item.get("url")
        if isinstance(url, str) and url.startswith("/api/"):
            clean["url"] = url
        out.append(clean)
    return out


def _compact_flow_result_for_history(result: dict) -> dict:
    """Bound the analyze_flow_data payload that is appended to LLM history."""
    compact = {
        "success": result.get("success"),
        "error": result.get("error"),
        "display_range": result.get("display_range"),
        "plot_timezone": result.get("plot_timezone"),
        "analysis_mode": result.get("analysis_mode"),
        "report_truncated": result.get("report_truncated"),
        "analysis_json_path": result.get("analysis_json_path"),
        "report_path": result.get("report_path"),
        "reasoning_schema": result.get("reasoning_schema"),
        "analysis_details": result.get("analysis_details"),
        "analysis_metadata": _compact_analysis_metadata(result.get("analysis_metadata")),
    }
    plot_paths = result.get("plot_paths")
    if isinstance(plot_paths, list):
        compact["plot_paths"] = plot_paths[:12]
        if len(plot_paths) > 12:
            compact["plot_paths_omitted"] = len(plot_paths) - 12
    plot_summaries = result.get("plot_summaries")
    if isinstance(plot_summaries, list):
        compact["plot_summaries"] = plot_summaries[:12]
        if len(plot_summaries) > 12:
            compact["plot_summaries_omitted"] = len(plot_summaries) - 12
    excerpt = _compact_report_excerpt(result.get("report"))
    if excerpt:
        compact["report_excerpt"] = excerpt
    artifacts = _compact_download_artifacts(result.get("download_artifacts"))
    if artifacts:
        compact["download_artifacts"] = artifacts
    return {k: v for k, v in compact.items() if v is not None}


def _compact_tool_result_for_history(tool_name: str, result_dict: dict) -> dict:
    """
    Full tool results can be huge. SSE events and per-turn dedupe keep the full
    dict, but persisted LLM history receives a bounded shape.
    """
    if tool_name == "analyze_flow_data":
        return _compact_flow_result_for_history(result_dict)
    if tool_name == "batch_analyze_flow":
        compact = {
            "success": result_dict.get("success"),
            "error": result_dict.get("error"),
            "display_range": result_dict.get("display_range"),
            "failed_serials": result_dict.get("failed_serials"),
        }
        meters = []
        for m in result_dict.get("meters", []):
            if isinstance(m, dict):
                meters.append(
                    {
                        "serial_number": m.get("serial_number"),
                        **_compact_flow_result_for_history(m),
                    }
                )
        compact["meters"] = meters
        return {k: v for k, v in compact.items() if v is not None}
    return result_dict


def _compact_tool_result_json_for_history(tool_name: str, result_dict: dict) -> str:
    return json.dumps(
        _compact_tool_result_for_history(tool_name, result_dict),
        default=str,
    )


def _coerce_tool_input(inp: object) -> dict:
    if isinstance(inp, dict):
        return inp
    if isinstance(inp, Mapping):
        return dict(inp)
    return {}


def _tool_activity_line(
    tool_name: str, inp: dict, result: dict, *, ok: bool
) -> str | None:
    """
    One-line status for the web timeline when a tool succeeds.
    The client falls back to generic labels when this returns None.
    """
    if not ok:
        return None
    sn = ""
    if isinstance(inp.get("serial_number"), str):
        sn = inp["serial_number"].strip()
    email = ""
    if isinstance(inp.get("email"), str):
        email = inp["email"].strip()

    if tool_name == "resolve_time_range":
        dr = result.get("display_range")
        if isinstance(dr, str) and dr.strip():
            return _clip_activity(f"Resolved the time range {dr.strip()}", 240)
        return None

    if tool_name == "analyze_flow_data":
        if sn:
            core = f"Analyzed the flow data for meter with serial number {sn}"
        else:
            core = "Analyzed the flow data"
        dr = result.get("display_range")
        if isinstance(dr, str) and dr.strip():
            return _clip_activity(f"{core} — {dr.strip()}", 280)
        return _clip_activity(core, 200)

    if tool_name == "check_meter_status" and sn:
        return _clip_activity(f"Checked the meter {sn}", 200)

    if tool_name == "get_meter_profile" and sn:
        return _clip_activity(f"Read the meter profile for {sn}", 200)

    if tool_name == "list_meters_for_account" and email:
        return _clip_activity(f"Listed meters for account {email}", 240)

    if tool_name == "compare_meters":
        sns = inp.get("serial_numbers")
        if isinstance(sns, list) and sns:
            clean = [s.strip() for s in sns if isinstance(s, str) and s.strip()]
            if clean:
                return _clip_activity(f"Compared meters {', '.join(clean)}", 240)
        return _clip_activity("Compared meters", 200)

    if tool_name == "batch_analyze_flow":
        sns = inp.get("serial_numbers")
        if isinstance(sns, list) and sns:
            clean = [s.strip() for s in sns if isinstance(s, str) and s.strip()]
            if clean:
                dr = result.get("display_range")
                core = f"Analyzed flow for {', '.join(clean)}"
                if isinstance(dr, str) and dr.strip():
                    return _clip_activity(f"{core} — {dr.strip()}", 300)
                return _clip_activity(core, 240)
        return _clip_activity("Batch flow analysis", 200)

    if tool_name == "configure_meter_pipe" and sn:
        return _clip_activity(f"Configured the pipe for meter {sn}", 200)

    if tool_name == "set_transducer_angle_only" and sn:
        ang = inp.get("transducer_angle")
        ang_s = str(ang).strip() if ang is not None else ""
        if ang_s:
            return _clip_activity(
                f"Set the transducer angle to {ang_s} for meter {sn}", 220
            )
        return _clip_activity(f"Set the transducer angle for meter {sn}", 200)

    return None


def _compact_signal(signal: object) -> dict | None:
    if not isinstance(signal, dict):
        return None
    return {
        k: signal.get(k)
        for k in ("score", "level", "reliable", "snr", "rssi")
        if signal.get(k) is not None
    }


def _compact_pipe_config(pipe_config: object) -> dict | None:
    if not isinstance(pipe_config, dict):
        return None
    keep = (
        "material",
        "standard",
        "nominal_size",
        "pipe_size",
        "inner_diameter_mm",
        "outer_diameter_mm",
        "wall_thickness_mm",
        "transducer_angle",
    )
    return {k: pipe_config.get(k) for k in keep if pipe_config.get(k) is not None}


def _cusum_adequacy_explanation(cusum: dict) -> str:
    actual = cusum.get("actual_points")
    target = cusum.get("target_min")
    gap = cusum.get("gap_pct")
    actual_s = f"{int(actual):,}" if isinstance(actual, (int, float)) else None
    target_s = f"{int(target):,}" if isinstance(target, (int, float)) else None
    gap_s = f"{round(float(gap), 1)}%" if isinstance(gap, (int, float)) else None
    if cusum.get("skipped") is True or cusum.get("adequacy_ok") is False:
        reason = str(cusum.get("adequacy_reason") or "not enough usable data")
        bits = []
        if actual_s and target_s:
            bits.append(f"{actual_s} samples available, {target_s} required")
        if gap_s:
            bits.append(f"{gap_s} gaps")
        suffix = f": {', '.join(bits)}" if bits else ""
        return f"CUSUM was skipped because {reason}{suffix}."
    bits = []
    if actual_s and target_s:
        bits.append(f"{actual_s} samples available, {target_s} required")
    if gap_s:
        bits.append(f"{gap_s} gaps")
    suffix = f": {', '.join(bits)}" if bits else "."
    return f"Data is sufficient for drift detection{suffix}"


def _meter_context_from_result(tool_name: str, inp: dict, result: dict) -> dict | None:
    serial = str(result.get("serial_number") or inp.get("serial_number") or "").strip()
    ctx: dict = {"serial_number": serial} if serial else {}

    if tool_name == "get_meter_profile":
        profile = result.get("profile")
        if isinstance(profile, dict):
            ctx.update(
                {
                    "label": profile.get("label"),
                    "network_type": result.get("network_type"),
                    "timezone": profile.get("deviceTimeZone"),
                    "installed": profile.get("installed"),
                    "commissioned": profile.get("commissioned"),
                    "active": profile.get("active"),
                }
            )
        elif result.get("network_type"):
            ctx["network_type"] = result.get("network_type")
    elif tool_name == "check_meter_status":
        status = result.get("status_data")
        if isinstance(status, dict):
            ctx.update(
                {
                    "serial_number": status.get("serial_number") or serial,
                    "online": status.get("online"),
                    "last_message_at": status.get("last_message_at"),
                    "signal": _compact_signal(status.get("signal")),
                    "pipe_config": _compact_pipe_config(status.get("pipe_config")),
                }
            )
    elif tool_name in {"analyze_flow_data", "configure_meter_pipe", "set_transducer_angle_only"}:
        if inp.get("network_type"):
            ctx["network_type"] = inp.get("network_type")
        if inp.get("meter_timezone"):
            ctx["timezone"] = inp.get("meter_timezone")

    clean = {k: v for k, v in ctx.items() if v is not None and v != ""}
    return clean or None


def _diagnostic_summary_from_result(tool_name: str, result: dict, event: dict) -> dict | None:
    if tool_name == "check_meter_status":
        status = result.get("status_data")
        if not isinstance(status, dict):
            return None
        return {
            "kind": "status",
            "online": status.get("online"),
            "last_message_at": status.get("last_message_at"),
            "communication_status": (
                status.get("staleness", {}).get("communication_status")
                if isinstance(status.get("staleness"), dict)
                else None
            ),
            "signal": _compact_signal(status.get("signal")),
            "pipe_config": _compact_pipe_config(status.get("pipe_config")),
            "next_actions": [
                "Analyze recent flow" if status.get("online") is not False else "Check connectivity",
                "Review pipe setup",
            ],
        }

    if tool_name != "analyze_flow_data":
        return None

    details = event.get("analysis_details")
    cusum = details.get("cusum_drift") if isinstance(details, dict) else None
    attribution = details.get("attribution") if isinstance(details, dict) else None
    summary: dict = {
        "kind": "flow",
        "range": result.get("display_range") or event.get("display_range"),
        "plot_count": len(result.get("plot_paths") or []),
        "next_actions": ["Check current meter health", "Compare with a nearby window"],
    }
    plot_summaries = result.get("plot_summaries")
    if isinstance(plot_summaries, list):
        for item in plot_summaries:
            if not isinstance(item, dict):
                continue
            caption = item.get("caption")
            if item.get("plot_type") != "diagnostic_timeline" or not isinstance(caption, dict):
                continue
            markers = caption.get("diagnostic_markers")
            if isinstance(markers, list):
                summary["plot_explanation"] = {
                    "summary": caption.get("summary"),
                    "markers": markers,
                    "next_actions": caption.get("next_actions") if isinstance(caption.get("next_actions"), list) else [],
                }
            break
    if isinstance(attribution, dict):
        summary["attribution"] = attribution
        checks = attribution.get("next_checks")
        if isinstance(checks, list) and checks:
            summary["next_actions"] = [str(item) for item in checks if item][:4]
    if isinstance(cusum, dict):
        skipped = cusum.get("skipped") is True or cusum.get("adequacy_ok") is False
        summary["adequacy"] = {
            "ok": cusum.get("adequacy_ok"),
            "reason": cusum.get("adequacy_reason"),
            "actual_points": cusum.get("actual_points"),
            "target_min": cusum.get("target_min"),
            "gap_pct": cusum.get("gap_pct"),
            "explanation": _cusum_adequacy_explanation(cusum),
        }
        summary["drift"] = {
            "cusum_ran": not skipped,
            "direction": None if skipped else (cusum.get("drift_detected") or "none"),
            "skipped": skipped,
            "reason": cusum.get("adequacy_reason") if skipped else None,
        }
        summary["alarms"] = {
            "up": cusum.get("positive_alarm_count"),
            "down": cusum.get("negative_alarm_count"),
            "first_alarm_timestamp": cusum.get("first_alarm_timestamp"),
        }
        if skipped and not isinstance(attribution, dict):
            summary["next_actions"] = ["Widen the analysis window", "Check meter connectivity"]
        elif (
            not isinstance(attribution, dict)
            and cusum.get("drift_detected")
            and cusum.get("drift_detected") != "none"
        ):
            summary["next_actions"] = ["Check signal quality now", "Compare against the previous day"]
    return summary


def _current_values_for_config_confirmation(tool_name: str, inp: dict, token: str) -> dict | None:
    serial = str(inp.get("serial_number") or "").strip()
    if not serial:
        return None
    profile = get_meter_profile(serial, token)
    current: dict = {
        "serial_number": serial,
        "profile_success": profile.get("success"),
        "network_type": profile.get("network_type"),
        "transducer_angle_options": profile.get("transducer_angle_options"),
        "change_type": (
            "full_pipe_configuration"
            if tool_name == "configure_meter_pipe"
            else "transducer_angle_only"
        ),
    }
    prof = profile.get("profile")
    if isinstance(prof, dict):
        current.update(
            {
                "label": prof.get("label"),
                "timezone": prof.get("deviceTimeZone"),
                "model": prof.get("model"),
                "active": prof.get("active"),
            }
        )
    if not profile.get("success"):
        current["profile_error"] = profile.get("error")
    return current


def _confirmation_required_payload(
    *,
    conversation_id: str,
    user_scope: str,
    tool_name: str,
    inp: dict,
    token: str,
) -> tuple[dict, dict]:
    current_values = _current_values_for_config_confirmation(tool_name, inp, token)
    action = create_pending_action(
        conversation_id=conversation_id,
        user_scope=user_scope,
        tool_name=tool_name,
        inputs=inp,
        current_values=current_values,
    )
    workflow = action.as_workflow()
    workflow["message"] = "Review and confirm before any device configuration is sent."
    workflow["risk"] = "This will send configuration to the physical meter. No change has been made yet."
    meter_context = {
        "serial_number": str(inp.get("serial_number") or ""),
        "network_type": (current_values or {}).get("network_type"),
        "label": (current_values or {}).get("label"),
        "timezone": (current_values or {}).get("timezone"),
    }
    return workflow, {k: v for k, v in meter_context.items() if v}


def _confirmation_prompt(workflow: dict) -> str:
    serial = str(workflow.get("serial_number") or "the meter")
    proposed = workflow.get("proposed_values") if isinstance(workflow.get("proposed_values"), dict) else {}
    label = ""
    current = workflow.get("current_values") if isinstance(workflow.get("current_values"), dict) else {}
    if isinstance(current, dict) and current.get("label"):
        label = f" ({current.get('label')})"
    if workflow.get("tool") == "set_transducer_angle_only":
        change = f"transducer angle to {proposed.get('transducer_angle')}"
    else:
        parts = [
            proposed.get("pipe_material"),
            proposed.get("pipe_standard"),
            proposed.get("pipe_size"),
            f"angle {proposed.get('transducer_angle')}" if proposed.get("transducer_angle") else None,
        ]
        change = " / ".join(str(p) for p in parts if p)
    return (
        f"I prepared a configuration change for meter {serial}{label}: {change}.\n\n"
        "Choose Yes to apply it, No to cancel it, or type another value."
    )


def _status_line_from_status_result(result: dict) -> str:
    status = result.get("status_data")
    if not isinstance(status, dict):
        return "Status verification ran, but no structured status summary was returned."
    online = status.get("online")
    online_s = "online" if online is True else "offline" if online is False else "unknown online state"
    signal = status.get("signal")
    signal_s = ""
    if isinstance(signal, dict):
        level = signal.get("level")
        score = signal.get("score")
        if level is not None and score is not None:
            signal_s = f", signal {level} ({score})"
        elif level is not None:
            signal_s = f", signal {level}"
    last = status.get("last_message_at")
    last_s = f", last message {last}" if last else ""
    return f"Verification: meter is {online_s}{signal_s}{last_s}."


def _emit_tool_result_event(
    *,
    emit,
    tool_name: str,
    inp: dict,
    result_dict: dict,
    ok: bool,
    from_cache: bool = False,
    config_workflow: dict | None = None,
) -> dict:
    event: dict = {"type": "tool_result", "tool": tool_name, "success": ok}
    if from_cache:
        event["deduped"] = True
    dr = result_dict.get("display_range")
    if isinstance(dr, str) and dr.strip():
        event["display_range"] = dr.strip()
    if isinstance(result_dict.get("report_truncated"), bool):
        event["report_truncated"] = result_dict.get("report_truncated")
    details = result_dict.get("analysis_details")
    if isinstance(details, dict) and details:
        event["analysis_details"] = details
    metadata = result_dict.get("analysis_metadata")
    if isinstance(metadata, dict) and metadata:
        event["analysis_metadata"] = metadata
    if result_dict.get("analysis_mode"):
        event["analysis_mode"] = result_dict.get("analysis_mode")
    artifacts = result_dict.get("download_artifacts")
    if isinstance(artifacts, list) and artifacts:
        event["download_artifacts"] = artifacts
    activity = _tool_activity_line(tool_name, inp, result_dict, ok=ok)
    if activity:
        event["tool_activity"] = activity
    meter_context = _meter_context_from_result(tool_name, inp, result_dict)
    if meter_context:
        event["meter_context"] = meter_context
    diagnostic = _diagnostic_summary_from_result(tool_name, result_dict, event)
    if diagnostic:
        event["diagnostic_summary"] = diagnostic
    if config_workflow:
        event["config_workflow"] = config_workflow
    if not ok:
        emsg = result_dict.get("error")
        if emsg not in (None, ""):
            event["message"] = str(emsg)[:500]
    if tool_name == "analyze_flow_data":
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
        rp = result_dict.get("report_path")
        if rp:
            event["report_path"] = rp
    elif tool_name == "batch_analyze_flow":
        all_paths: list[str] = []
        all_summaries: list[dict] = []
        for m in result_dict.get("meters", []):
            all_paths.extend(m.get("plot_paths", []))
            all_summaries.extend(m.get("plot_summaries", []))
        if all_paths:
            event["plot_paths"] = all_paths
        if all_summaries:
            event["plot_summaries"] = all_summaries
        event["meters"] = result_dict.get("meters", [])
    emit(event)
    return event


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

    elif name == "compare_meters":
        result = compare_meters(
            inputs["serial_numbers"],
            token,
            anthropic_api_key=anthropic_api_key,
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
            analysis_mode=inputs.get("analysis_mode"),
        )

    elif name == "batch_analyze_flow":
        result = batch_analyze_flow(
            inputs["serial_numbers"],
            inputs["start"],
            inputs["end"],
            token,
            display_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
            network_type=inputs.get("network_type"),
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


def _dispatch_tool_batch(
    tool_calls: list,
    *,
    token: str,
    client_timezone: str | None,
    anthropic_api_key: str | None,
    conversation_id: str,
    user_scope: str,
    dedupe_cache: dict,
    counters: dict,
    emit,
    round_ix: int,
) -> list[dict]:
    """Dispatch a batch of tool calls; read-only non-flow tools run in parallel.

    Write tools and analyze_flow_data always serialize: writes for rule-11
    verify-after-write correctness, flow for clean per-meter SSE progress.
    Result ordering matches tool_calls (required by the Anthropic API).
    """
    use_parallel = (
        len(tool_calls) > 1
        and not any(tc.name in _SERIAL_ONLY_TOOLS for tc in tool_calls)
    )

    # Write tools are guarded by an action-time confirmation card. If the model
    # proposes any write in this batch, stop at the first write and do not run
    # additional read tools from the same tool_use response.
    for write_index, tc in enumerate(tool_calls):
        if tc.name not in _WRITE_TOOLS:
            continue
        inp_d = _coerce_tool_input(tc.input)
        emit({"type": "tool_call", "tool": tc.name, "input": inp_d})
        counters["tool_calls"] += 1
        workflow, meter_context = _confirmation_required_payload(
            conversation_id=conversation_id,
            user_scope=user_scope,
            tool_name=tc.name,
            inp=inp_d,
            token=token,
        )
        emit(
            {
                "type": "config_confirmation_required",
                "tool": tc.name,
                "input": inp_d,
                "config_workflow": workflow,
                "meter_context": meter_context,
            }
        )
        result_json = json.dumps(
            {
                "success": True,
                "requires_confirmation": True,
                "confirmation_required": True,
                "action_id": workflow.get("action_id"),
                "config_workflow": workflow,
                "message": "Configuration change is pending confirmation. No device changes were sent.",
            },
            default=str,
        )
        hidden_results: list[dict] = []
        for j, other in enumerate(tool_calls):
            if j == write_index:
                content = result_json
            else:
                content = json.dumps(
                    {
                        "success": True,
                        "skipped_due_to_confirmation": True,
                        "message": (
                            "Skipped because a configuration change is waiting for user confirmation. "
                            "No additional tools were run."
                        ),
                    },
                    default=str,
                )
            hidden_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": other.id,
                    "content": content,
                }
            )
        return hidden_results, workflow

    # Phase 1: cache check + emit tool_call events — always serial and ordered.
    call_infos: list[tuple] = []
    batch_owner_by_key: dict[str, int] = {}
    for tc in tool_calls:
        inp_d = _coerce_tool_input(tc.input)
        dedupe_key = _per_turn_tool_dedupe_key(tc.name, inp_d)
        duplicate_of = (
            batch_owner_by_key.get(dedupe_key)
            if dedupe_key
            else None
        )
        from_cache = bool(
            (dedupe_key and dedupe_key in dedupe_cache)
            or duplicate_of is not None
        )
        cached_json: str | None = (
            dedupe_cache[dedupe_key][0]
            if dedupe_key and dedupe_key in dedupe_cache
            else None
        )
        call_ev: dict = {"type": "tool_call", "tool": tc.name, "input": inp_d}
        if from_cache:
            call_ev["deduped"] = True
            emit_event(
                "tool_dedupe_hit",
                tool=tc.name,
                serial_number=inp_d.get("serial_number"),
                round=round_ix,
            )
        emit(call_ev)
        counters["tool_calls"] += 1
        idx = len(call_infos)
        if dedupe_key and duplicate_of is None:
            batch_owner_by_key[dedupe_key] = idx
        call_infos.append((tc, inp_d, dedupe_key, from_cache, cached_json, duplicate_of))

    # Phase 2: execute — each slot writes to its own index (GIL-safe).
    exec_results: list = [None] * len(call_infos)

    def _execute(idx: int, tc, inp_d: dict, from_cache: bool, cached_json: str | None) -> None:
        with timed("tool_call", tool=tc.name, args=inp_d, cached=from_cache, round=round_ix) as _end:
            if from_cache:
                result_json = cached_json
            elif tc.name == "analyze_flow_data":
                result_json = _run_analyze_flow_with_progress(
                    inp_d, token,
                    client_timezone=client_timezone,
                    emit=emit,
                    anthropic_api_key=anthropic_api_key,
                )
            else:
                result_json = _dispatch(
                    tc.name, inp_d, token,
                    client_timezone=client_timezone,
                    anthropic_api_key=anthropic_api_key,
                )
            result_dict = json.loads(result_json)
            _end["bytes_out"] = len(result_json)
            _end["success"] = _sse_tool_succeeded(result_dict)
            if not _end["success"]:
                _end["error"] = str(result_dict.get("error") or "")[:500]
        exec_results[idx] = (result_json, result_dict)

    has_batch_duplicates = any(info[5] is not None for info in call_infos)
    if use_parallel and not has_batch_duplicates:
        n = min(len(call_infos), _MAX_PARALLEL_TOOL_WORKERS)
        with ThreadPoolExecutor(max_workers=n) as pool:
            futs = [
                pool.submit(_execute, i, tc, inp_d, fc, cj)
                for i, (tc, inp_d, _, fc, cj, _) in enumerate(call_infos)
            ]
            for f in as_completed(futs):
                f.result()
    else:
        for i, (tc, inp_d, _, fc, cj, duplicate_of) in enumerate(call_infos):
            if duplicate_of is not None:
                exec_results[i] = exec_results[duplicate_of]
                continue
            _execute(i, tc, inp_d, fc, cj)

    # Phase 3: cache update, write-invalidation, emit tool_result — serial and ordered.
    tool_results: list[dict] = []
    for i, (tc, inp_d, dedupe_key, from_cache, _, _) in enumerate(call_infos):
        result_json, result_dict = exec_results[i]
        ok = _sse_tool_succeeded(result_dict)

        if not ok:
            counters["tool_failures"] += 1

        if dedupe_key and not from_cache and ok:
            serial_tag = str(inp_d.get("serial_number") or "").strip() or None
            dedupe_cache[dedupe_key] = (result_json, serial_tag)

        if tc.name in _WRITE_TOOLS and ok:
            dropped = _invalidate_dedupe_for_write(dedupe_cache, tc.name, inp_d)
            if dropped:
                emit_event(
                    "tool_dedupe_invalidate",
                    tool=tc.name,
                    serial_number=inp_d.get("serial_number"),
                    dropped=len(dropped),
                    round=round_ix,
                )

        workflow = result_dict.get("config_workflow")
        _emit_tool_result_event(
            emit=emit,
            tool_name=tc.name,
            inp=inp_d,
            result_dict=result_dict,
            ok=ok,
            from_cache=from_cache,
            config_workflow=workflow if isinstance(workflow, dict) else None,
        )
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": tc.id,
            "content": _compact_tool_result_json_for_history(tc.name, result_dict),
        })

    return tool_results, None


def _execute_confirmed_config_action(
    *,
    action: PendingConfigAction,
    messages: list,
    token: str,
    client_timezone: str | None,
    anthropic_api_key: str | None,
    emit,
) -> tuple[str, bool]:
    tool_name = action.tool_name
    inp = dict(action.inputs)
    workflow_base = action.as_workflow(status="confirmed")
    emit({"type": "tool_call", "tool": tool_name, "input": inp})
    result_json = _dispatch(
        tool_name,
        inp,
        token,
        client_timezone=client_timezone,
        anthropic_api_key=anthropic_api_key,
    )
    result = json.loads(result_json)
    ok = _sse_tool_succeeded(result)
    workflow_status = "executed" if ok else "failed"
    workflow = {**workflow_base, "status": workflow_status}
    _emit_tool_result_event(
        emit=emit,
        tool_name=tool_name,
        inp=inp,
        result_dict=result,
        ok=ok,
        config_workflow=workflow,
    )
    serial = str(inp.get("serial_number") or "").strip()
    if not ok:
        msg = (
            f"I could not apply the confirmed configuration for meter {serial or 'the meter'}. "
            f"{result.get('error') or 'The configuration tool reported a failure.'}"
        )
        messages.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
        return msg, False

    verification_line = ""
    verification: dict | None = None
    if serial:
        status_inp = {"serial_number": serial}
        emit({"type": "tool_call", "tool": "check_meter_status", "input": status_inp})
        status_json = _dispatch(
            "check_meter_status",
            status_inp,
            token,
            client_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
        )
        status_result = json.loads(status_json)
        status_ok = _sse_tool_succeeded(status_result)
        verification = {
            **workflow_base,
            "status": "verified" if status_ok else "verification_failed",
            "verification": status_result.get("status_data"),
        }
        _emit_tool_result_event(
            emit=emit,
            tool_name="check_meter_status",
            inp=status_inp,
            result_dict=status_result,
            ok=status_ok,
            config_workflow=verification,
        )
        verification_line = _status_line_from_status_result(status_result)

    if tool_name == "configure_meter_pipe":
        changed = (
            f"{inp.get('pipe_material')} {inp.get('pipe_standard')} {inp.get('pipe_size')}, "
            f"angle {inp.get('transducer_angle')}"
        )
        msg = f"Confirmed. I applied the pipe configuration for meter {serial}: {changed}."
    else:
        msg = (
            f"Confirmed. I set the transducer angle for meter {serial} "
            f"to {inp.get('transducer_angle')}."
        )
    if verification_line:
        msg = f"{msg}\n\n{verification_line}"
    messages.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
    return msg, False


def run_turn(
    messages: list,
    token: str,
    on_event=None,
    *,
    client_timezone: str | None = None,
    anthropic_api_key: str | None = None,
    model: str | None = None,
    conversation_id: str = "default",
    user_scope: str | None = None,
    confirmed_action_id: str | None = None,
    cancelled_action_id: str | None = None,
    superseded_action_id: str | None = None,
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
                      {"type": "tool_result",  "tool": str, "success": bool,
                       "tool_activity": str | None}  — optional human title when success
                      {"type": "tool_progress", "tool": str, "message": str}  — long tools (flow analysis)
                      {"type": "thinking"}  — while waiting for the LLM

    Returns:
        (assistant_reply_text, history_replaced): *history_replaced* is True if messages were
        replaced with a summary (e.g. after a rate limit) — callers must persist with replace, not append.
    """
    def _emit(event: dict):
        if on_event:
            on_event(event)

    effective_user_scope = user_scope or user_scope_from_token(token)
    api_key_override = _resolve_api_key_override(anthropic_api_key)
    # Resolve once; subsequent iterations / rate-limit retries all use the
    # same model so a user-picked model turn doesn't silently downgrade.
    active_model = resolve_orchestrator_model(model)
    cheap_model = get_cheap_model(active_model)
    model_ctx = int(MODEL_CATALOG.get(active_model, {}).get("context_window", 200_000))
    history_replaced = False
    provider = None
    if confirmed_action_id or cancelled_action_id:
        active_tools = []
        _intent_label = "config"
        _intent_src = "confirmed_action" if confirmed_action_id else "cancelled_action"
    else:
        provider = get_provider(active_model, api_key_override=api_key_override)
        active_tools, _intent_label, _intent_src = _resolve_routed_tools(
            provider, cheap_model, messages, emit=_emit
        )
    max_rounds = _max_tool_rounds_per_turn()
    # key -> (result_json, serial_number_tag | None). The tag lets
    # :func:`_invalidate_dedupe_for_write` drop cached reads for a serial that
    # a write tool has just mutated (rule 11 verify-after-configuration).
    dedupe_cache: dict[str, tuple[str, str | None]] = {}
    round_ix = 0
    # Mutable counters threaded through the telemetry ``turn_end`` event.
    _counters = {"tool_calls": 0, "tool_failures": 0, "api_calls": 0, "rounds": 0}

    _turn_ctx = turn_context()
    _turn_id = _turn_ctx.__enter__()
    emit_event(
        "turn_start",
        model=active_model,
        cheap_model=cheap_model,
        intent=_intent_label,
        intent_source=_intent_src,
        tool_names=[t["name"] for t in active_tools],
        history_len=len(messages),
        prompt_version=_SYSTEM_PROMPT_VERSION,
    )

    def _finish(outcome: str, reply: str, replaced: bool, **extra):
        _counters["rounds"] = round_ix
        emit_event("turn_end", outcome=outcome, **_counters, **extra)
        _turn_ctx.__exit__(None, None, None)
        return reply, replaced

    try:
      if confirmed_action_id:
        action = consume_pending_action(
            conversation_id,
            effective_user_scope,
            confirmed_action_id,
        )
        if action is None:
            msg = (
                "I could not find that pending configuration action, or it expired. "
                "Please review the configuration values again before applying them."
            )
            messages.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
            _emit(
                {
                    "type": "error",
                    "error": "Pending configuration action was not found or expired.",
                }
            )
            return _finish("config_confirmation_missing", msg, history_replaced)
        ok, err = validate_pending_action(
            action,
            tool_name=action.tool_name,
            inputs=dict(action.inputs),
        )
        if not ok:
            msg = err or "The pending configuration action could not be validated."
            messages.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
            _emit({"type": "error", "error": msg})
            return _finish("config_confirmation_invalid", msg, history_replaced)
        reply, replaced = _execute_confirmed_config_action(
            action=action,
            messages=messages,
            token=token,
            client_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
            emit=_emit,
        )
        return _finish("config_confirmed", reply, replaced)

      if cancelled_action_id:
        action = consume_pending_action(
            conversation_id,
            effective_user_scope,
            cancelled_action_id,
        )
        if action is None:
            msg = (
                "I could not find that pending configuration action, or it already expired. "
                "No device changes were sent."
            )
            messages.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
            _emit(
                {
                    "type": "config_confirmation_cancelled",
                    "config_workflow": {
                        "action_id": cancelled_action_id,
                        "status": "cancel_missing",
                    },
                    "message": msg,
                }
            )
            return _finish("config_cancel_missing", msg, history_replaced)
        workflow = action.as_workflow(status="cancelled")
        workflow["message"] = "Cancelled. No device changes were sent."
        msg = "Cancelled. No device changes were sent."
        messages.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
        _emit(
            {
                "type": "config_confirmation_cancelled",
                "tool": action.tool_name,
                "config_workflow": workflow,
                "message": msg,
            }
        )
        return _finish("config_cancelled", msg, history_replaced)

      if superseded_action_id:
        action = consume_pending_action(
            conversation_id,
            effective_user_scope,
            superseded_action_id,
        )
        if action is None:
            msg = (
                "I could not find the pending configuration action you wanted to replace, "
                "or it already expired. Please review the configuration values again."
            )
            messages.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
            _emit(
                {
                    "type": "config_confirmation_superseded",
                    "config_workflow": {
                        "action_id": superseded_action_id,
                        "status": "supersede_missing",
                    },
                    "message": msg,
                }
            )
            return _finish("config_supersede_missing", msg, history_replaced)
        workflow = action.as_workflow(status="superseded")
        workflow["message"] = "Replaced by your new request. No device change was sent."
        _emit(
            {
                "type": "config_confirmation_superseded",
                "tool": action.tool_name,
                "config_workflow": workflow,
                "message": workflow["message"],
            }
        )

      while True:
        round_ix += 1
        if round_ix > max_rounds:
            msg = (
                f"Stopped after {max_rounds} assistant steps (safety limit). "
                "Send a shorter request or continue in a new message."
            )
            messages.append({"role": "assistant", "content": [{"type": "text", "text": msg}]})
            _emit({"type": "tool_round_limit", "limit": max_rounds})
            return _finish("round_limit", msg, history_replaced, limit=max_rounds)

        token_count = _count_tokens(
            provider, messages, model=active_model, tools=active_tools
        )
        pct = token_count / model_ctx
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
                provider, cheap_model, messages, _MAX_INPUT_TOKENS_TARGET,
                model=active_model, tools=active_tools,
            ):
                history_replaced = True
            token_count = _count_tokens(
                provider, messages, model=active_model, tools=active_tools
            )
            pct = token_count / model_ctx
            _emit({"type": "token_usage", "tokens": token_count, "pct": pct, "model": active_model})
            if token_count > _MAX_INPUT_TOKENS_TARGET:
                raise RuntimeError(
                    f"Could not compress context below {_MAX_INPUT_TOKENS_TARGET} input tokens "
                    f"(still at {token_count}). Shorten the thread or wait and retry."
                )

        if pct >= _COMPRESS_THRESHOLD:
            _emit({"type": "compressing", "tokens": token_count, "pct": pct})
            api_messages = messages_for_anthropic_api(_compress_history(provider, cheap_model, messages))
        else:
            api_messages = messages_for_anthropic_api(messages)

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
                with timed(
                    "api_call",
                    model=active_model,
                    attempt=stream_attempt,
                    input_tokens_estimate=token_count,
                ) as _api_end:
                    response = provider.stream(
                        active_model,
                        api_messages,
                        system=_SYSTEM_PROMPT,
                        tools=active_tools,
                        max_tokens=4096,
                        on_text_delta=lambda delta: _emit({"type": "text_delta", "text": delta}),
                    )
                    _api_end["input_tokens"] = response.input_tokens
                    _api_end["output_tokens"] = getattr(response, "output_tokens", None)
                    _api_end["stop_reason"] = response.stop_reason
                    _counters["api_calls"] += 1
                record_input_tokens(response.input_tokens or token_count)
                break
            except LLMRateLimitError as exc:
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
                    provider, cheap_model, messages, _MAX_INPUT_TOKENS_TARGET,
                    model=active_model, tools=active_tools,
                ):
                    history_replaced = True
                token_count = _count_tokens(
                    provider, messages, model=active_model, tools=active_tools
                )
                pct = token_count / model_ctx
                _emit({"type": "token_usage", "tokens": token_count, "pct": pct, "model": active_model})
                if token_count > _MAX_INPUT_TOKENS_TARGET:
                    raise RuntimeError(
                        f"Context still above {_MAX_INPUT_TOKENS_TARGET} tokens after compression "
                        f"({token_count} tokens). Wait one minute for rate limits to reset or start a new chat."
                    )
                if pct >= _COMPRESS_THRESHOLD:
                    _emit({"type": "compressing", "tokens": token_count, "pct": pct})
                    api_messages = messages_for_anthropic_api(
                        _compress_history(provider, cheap_model, messages)
                    )
                else:
                    api_messages = messages_for_anthropic_api(messages)
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
                f"Rate limit persisted after compressing context, waiting, and "
                f"{stream_attempt} stream attempts. Wait ~1 minute and send again, "
                "or start a new chat."
            )

        if response.stop_reason == "end_turn":
            messages.append({"role": "assistant", "content": response.assistant_content})
            reply = response.text or "(No response)"
            return _finish(
                "ok", reply, history_replaced, reply_chars=len(reply)
            )

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.assistant_content})
            tool_results, pending_workflow = _dispatch_tool_batch(
                response.tool_calls,
                token=token,
                client_timezone=client_timezone,
                anthropic_api_key=anthropic_api_key,
                conversation_id=conversation_id,
                user_scope=effective_user_scope,
                dedupe_cache=dedupe_cache,
                counters=_counters,
                emit=_emit,
                round_ix=round_ix,
            )
            messages.append({"role": "user", "content": tool_results})
            if pending_workflow:
                reply = _confirmation_prompt(pending_workflow)
                messages.append({"role": "assistant", "content": [{"type": "text", "text": reply}]})
                return _finish("config_confirmation_required", reply, history_replaced)

        else:
            break

      return _finish("unexpected_stop", "(Unexpected stop reason)", history_replaced)
    except BaseException as exc:
        _counters["rounds"] = round_ix
        emit_event(
            "turn_end",
            outcome="error",
            error=f"{type(exc).__name__}: {exc}",
            **_counters,
        )
        _turn_ctx.__exit__(None, None, None)
        raise
