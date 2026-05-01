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
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", ".."))

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
from tools.fleet_health import (
    TOOL_DEFINITION as _FLEET_HEALTH_DEF,
    rank_fleet_by_health,
)
from tools.fleet_triage import (
    TOOL_DEFINITION as _FLEET_TRIAGE_DEF,
    triage_fleet_for_account,
)
from tools.period_compare import (
    TOOL_DEFINITION as _PERIOD_COMPARE_DEF,
    compare_periods,
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
from tools.sweep_transducer_angles import (
    TOOL_DEFINITION as _SWEEP_TRANSDUCER_ANGLES_DEF,
    prepare_sweep_confirmation_inputs,
    sweep_transducer_angles,
)
from tools.set_zero_point import (
    TOOL_DEFINITION as _SET_ZERO_POINT_DEF,
    prepare_zero_point_confirmation_inputs,
    set_zero_point,
)
from tools.batch_flow_analysis import (
    TOOL_DEFINITION as _BATCH_FLOW_ANALYSIS_DEF,
    batch_analyze_flow,
)
from tools.tickets import (
    CREATE_TICKET_TOOL_DEFINITION as _CREATE_TICKET_DEF,
    LIST_TICKETS_TOOL_DEFINITION as _LIST_TICKETS_DEF,
    UPDATE_TICKET_TOOL_DEFINITION as _UPDATE_TICKET_DEF,
    create_ticket,
    list_tickets,
    update_ticket,
)
from message_sanitize import messages_for_anthropic_api  # still used for _rough_input_token_fallback
from observability import current_turn_id, emit_event, turn_context, timed
from prompts import load_system_prompt
from store import record_tool_evidence

def TOOLS() -> list:
    """Return the full list of tool definitions from the meter registry."""
    from meter_tools import METER_REGISTRY
    return METER_REGISTRY.definitions()

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
        "rank_fleet_by_health",
        "triage_fleet_for_account",
        "list_tickets",
        "create_ticket",
        "update_ticket",
    }
)

_TOOL_NAMES_BY_INTENT: dict[str, frozenset[str]] = {
    # Read-only / account discovery; no flow analysis, no pipe writes.
    "status": _BASE_READ_TOOLS,
    "general": _BASE_READ_TOOLS,
    # Historical flow + plots (expensive subprocess).
    "flow": _BASE_READ_TOOLS
    | frozenset({"analyze_flow_data", "batch_analyze_flow", "compare_periods"}),
    # Pipe / angle changes (mutations).
    "config": _BASE_READ_TOOLS
    | frozenset(
        {
            "configure_meter_pipe",
            "set_transducer_angle_only",
            "sweep_transducer_angles",
            "set_zero_point",
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


_SERIAL_RE = re.compile(r"\bBB[A-Z0-9-]{1,}\b", re.IGNORECASE)


def _last_user_text(messages: list) -> str:
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if isinstance(m, dict) and m.get("role") == "user":
            return _plain_text_from_user_message(m)
    return ""


def _extract_first_serial(text: str) -> str | None:
    match = _SERIAL_RE.search(text or "")
    return match.group(0).upper() if match else None


def _looks_like_angle_diagnostic_request(text: str) -> bool:
    """Heuristic validator preflight for questions that need an angle experiment."""
    t = (text or "").lower()
    if not t:
        return False
    has_angle_subject = bool(
        re.search(
            r"\b(angle|transducer|install|installation|mount|mounted|alignment|signal|quality)\b",
            t,
        )
        or any(word in text for word in ("角度", "安装", "信号", "探头", "换能器"))
    )
    has_diagnostic_intent = bool(
        re.search(
            r"\b(is it|could it|whether|why|diagnos|judge|tell|prove|verify|"
            r"best|better|optimi[sz]e|issue|problem|cause)\b",
            t,
        )
        or any(word in text for word in ("是不是", "是否", "判断", "验证", "原因", "问题", "更好", "最佳"))
    )
    asks_direct_write = bool(
        re.search(r"\b(set|change|apply|configure|update|make it|switch)\b", t)
        or any(word in text for word in ("设置", "改成", "应用", "配置"))
    )
    return has_angle_subject and has_diagnostic_intent and not asks_direct_write


def _pipe_correctness_asserted(text: str) -> bool:
    t = (text or "").lower()
    return bool(
        re.search(
            r"\b(pipe|pipe config|pipe configuration|pipe parameter|pipe parameters|"
            r"material|diameter|size|standard)\b.{0,40}\b(correct|right|verified|confirmed|ok|okay|good)\b",
            t,
        )
        or re.search(
            r"\b(correct|right|verified|confirmed|ok|okay|good)\b.{0,40}\b(pipe|pipe config|pipe parameters)\b",
            t,
        )
        or any(
            phrase in (text or "")
            for phrase in (
                "管道参数是对的",
                "管道参数对",
                "管道参数正确",
                "管道参数没问题",
                "管道配置是对的",
                "管道配置正确",
                "管道配置没问题",
                "管径是对的",
                "管径正确",
                "管材是对的",
                "材质是对的",
            )
        )
    )


def _angle_experiment_signal_threshold() -> float:
    raw = os.environ.get("BLUEBOT_ANGLE_EXPERIMENT_SIGNAL_SCORE_MAX", "20")
    try:
        return float(raw)
    except ValueError:
        return 20.0


def _signal_score_from_status_result(status_result: dict) -> float | None:
    status = status_result.get("status_data") if isinstance(status_result, dict) else None
    signal = status.get("signal") if isinstance(status, dict) else None
    if not isinstance(signal, dict):
        return None
    score = signal.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    if isinstance(score, str):
        try:
            return float(score)
        except ValueError:
            return None
    return None


def _signal_is_low_enough_for_angle_experiment(status_result: dict) -> tuple[bool, str]:
    status = status_result.get("status_data") if isinstance(status_result, dict) else None
    signal = status.get("signal") if isinstance(status, dict) else None
    if not isinstance(signal, dict):
        return False, "No current signal-quality reading was available."
    score = _signal_score_from_status_result(status_result)
    threshold = _angle_experiment_signal_threshold()
    if score is not None:
        if score <= threshold:
            return True, f"Current signal score is {score:g}, at or below the {threshold:g} experiment threshold."
        return False, f"Current signal score is {score:g}, which is not low enough for an angle sweep."
    level = str(signal.get("level") or signal.get("label") or "").strip().lower()
    if level in {"0", "zero", "none", "no signal", "poor", "bad", "low", "very low", "critical"}:
        return True, f"Current signal level is {level}."
    return False, "Current signal quality is not clearly low enough for an angle sweep."


def _pipe_config_present(status_result: dict) -> bool:
    status = status_result.get("status_data") if isinstance(status_result, dict) else None
    pipe = status.get("pipe_config") if isinstance(status, dict) else None
    return isinstance(pipe, dict) and bool(pipe)


def _route_intent_rules(user_text: str) -> str:
    if not (user_text or "").strip():
        return "general"
    t = user_text.lower()
    # Order: more specific "what kind of work" first.
    if re.search(
        r"\b(flow|rate|trend|chart|graph|plot|time series|historical|analy[sz]e|"
        r"last \d+|past \d+|yesterday|today|this week|this month|"
        r"demand|duration curve|how much (water|flow)|usage over|peaks?|events?|"
        r"threshold|above|below|frequency|frequencies|periodic|periodicity|fft|psd|"
        r"data for)\b",
        t,
    ):
        return "flow"
    if re.search(
        r"\b(config|pipe|material|diameter|transducer|angle|install|"
        r"zero point|zero-point|set zero|reset zero|"
        r"pvc|hdpe|copper|npt|bspt|bs en|astm|sch \d+|schedule)\b",
        t,
    ) or any(word in user_text for word in ("零点", "归零", "校零")):
        return "config"
    if re.search(
        r"\b(online|offline|status|signal|quality|battery|wifi|lora|lorawan|"
        r"health|healthiest|triage|fleet|need attention|needs attention|"
        r"is my meter|meter is|list meters?|serial)\b",
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
    """Return a non-empty subset of tools for the given intent; fallback to full list if something is off."""
    from meter_tools import METER_REGISTRY
    names = _TOOL_NAMES_BY_INTENT.get(label) or _TOOL_NAMES_BY_INTENT["general"]
    out = METER_REGISTRY.definitions(names=list(names))
    return out if out else METER_REGISTRY.definitions()


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
        from meter_tools import METER_REGISTRY
        return (METER_REGISTRY.definitions(), "full", "off")
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
_MAX_PARALLEL_TOOL_WORKERS = 6


def _is_dedupable_read(tool_name: str) -> bool:
    """Return True if the tool result can be cached/deduplicated within a turn."""
    from meter_tools import METER_REGISTRY
    tool = METER_REGISTRY.get(tool_name)
    return tool is not None and tool.is_dedupable_read


def _is_write(tool_name: str) -> bool:
    """Return True if the tool performs a mutation (write)."""
    from meter_tools import METER_REGISTRY
    tool = METER_REGISTRY.get(tool_name)
    return tool is not None and tool.is_write


def _is_serial_only(tool_name: str) -> bool:
    """Return True if the tool must run serially, not in parallel."""
    from meter_tools import METER_REGISTRY
    tool = METER_REGISTRY.get(tool_name)
    return tool is not None and tool.is_serial_only


def _is_heartbeat_progress(tool_name: str) -> bool:
    """Return True if the tool emits progress events (SSE heartbeats)."""
    from meter_tools import METER_REGISTRY
    tool = METER_REGISTRY.get(tool_name)
    return tool is not None and tool.is_heartbeat_progress


def _per_turn_tool_dedupe_key(tool_name: str, inp_d: dict) -> str | None:
    """Return a stable cache key for a read-only tool call, else ``None``.

    Canonicalises the whole args dict via ``sort_keys=True`` so ``{"a": 1,
    "b": 2}`` and ``{"b": 2, "a": 1}`` hit the same entry. Write tools and
    unknown tools always return ``None`` so they bypass the cache.
    """
    if not _is_dedupable_read(tool_name):
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
    if not _is_write(tool_name):
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
    target = int(tpm_guide * frac)
    next_call_factor = max(1.0, _env_float("ORCHESTRATOR_TPM_NEXT_CALL_FACTOR", 2.05))
    # The next stream charges roughly count_tokens + stream input. Keep the
    # default target below the per-minute guide so an empty 60s window can run.
    streamable_target = int(tpm_guide / next_call_factor)
    return max(1, min(target, streamable_target))


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


def _wait_for_tpm_headroom_with_progress(
    estimated_next_input_tokens: int,
    *,
    emit,
) -> None:
    """Block for TPM headroom while streaming visible wait status to the UI."""
    last_bucket = -1

    def on_wait(snapshot: dict[str, int | float]) -> None:
        nonlocal last_bucket
        waited = float(snapshot.get("waited_seconds") or 0.0)
        bucket = int(waited // 5)
        # Emit immediately, then roughly every five seconds while blocked.
        if bucket == last_bucket and waited > 0:
            return
        last_bucket = bucket
        current = int(snapshot.get("current_tokens") or 0)
        estimated = int(snapshot.get("estimated_next_tokens") or estimated_next_input_tokens)
        cap = int(snapshot.get("tpm_cap") or _TPM_INPUT_GUIDE_TOKENS)
        overflow = int(snapshot.get("overflow_tokens") or 0)
        emit(
            {
                "type": "rate_limit_wait",
                "message": (
                    "Waiting for input-token headroom: "
                    f"{current:,} used in the last 60s + {estimated:,} next "
                    f"exceeds the {cap:,}/min budget."
                ),
                "current_tokens": current,
                "estimated_next_tokens": estimated,
                "tpm_limit": int(snapshot.get("tpm_limit") or _TPM_INPUT_GUIDE_TOKENS),
                "tpm_cap": cap,
                "overflow_tokens": overflow,
                "waited_seconds": waited,
            }
        )

    wait_for_sliding_tpm_headroom(
        estimated_next_input_tokens,
        _TPM_INPUT_GUIDE_TOKENS,
        on_wait=on_wait,
    )


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
    may pass a subset of available tools).
    """
    tool_list = tools if tools is not None else TOOLS()
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
                    baseline_window=inputs.get("baseline_window"),
                    filters=inputs.get("filters"),
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


def _run_dispatch_with_heartbeat_progress(
    tool_name: str,
    inputs: dict,
    token: str,
    *,
    client_timezone: str | None,
    emit,
    conversation_id: str | None = None,
    anthropic_api_key: str | None = None,
    heartbeat_seconds: float = 4.0,
) -> str:
    """Run a silent long read tool while emitting periodic status heartbeats."""
    result_holder: list[str] = []
    exc_holder: list[BaseException] = []

    def worker() -> None:
        try:
            result_holder.append(
                _dispatch_with_optional_conversation_id(
                    tool_name,
                    inputs,
                    token,
                    client_timezone=client_timezone,
                    anthropic_api_key=anthropic_api_key,
                    conversation_id=conversation_id,
                )
            )
        except BaseException as e:
            exc_holder.append(e)

    thread = threading.Thread(target=worker, daemon=True, name=tool_name)
    thread.start()

    def label() -> str:
        if tool_name == "triage_fleet_for_account":
            email = str(inputs.get("email") or "").strip()
            return f"Fleet triage for {email}" if email else "Fleet triage"
        if tool_name == "rank_fleet_by_health":
            serials = inputs.get("serial_numbers")
            count = len(serials) if isinstance(serials, list) else 0
            return f"Fleet health ranking for {count} meter(s)" if count else "Fleet health ranking"
        return tool_name.replace("_", " ")

    title = label()
    emit(
        {
            "type": "tool_progress",
            "tool": tool_name,
            "message": f"{title}: started — reading profiles and status…",
        }
    )
    elapsed_chunks = 0
    interval = max(0.01, float(heartbeat_seconds))
    while True:
        thread.join(timeout=interval)
        if not thread.is_alive():
            break
        elapsed_chunks += 1
        sec = int(round(elapsed_chunks * interval))
        emit(
            {
                "type": "tool_progress",
                "tool": tool_name,
                "message": f"{title}: still checking meters… ({sec}s)",
            }
        )

    if exc_holder:
        return json.dumps(
            {
                "success": False,
                "error": f"{type(exc_holder[0]).__name__}: {exc_holder[0]}",
            },
            default=str,
        )
    if not result_holder:
        return json.dumps(
            {"success": False, "error": f"{tool_name} produced no result"},
            default=str,
        )
    return result_holder[0]


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
    if tool_name == "compare_periods":
        return {
            "success": result_dict.get("success"),
            "serial_number": result_dict.get("serial_number"),
            "periods": result_dict.get("periods"),
            "deltas": result_dict.get("deltas"),
            "error": result_dict.get("error"),
        }
    if tool_name == "rank_fleet_by_health":
        return {
            "success": result_dict.get("success"),
            "meters": result_dict.get("meters"),
            "failed_serials": result_dict.get("failed_serials"),
            "truncated": result_dict.get("truncated"),
            "error": result_dict.get("error"),
        }
    if tool_name == "triage_fleet_for_account":
        return {
            "success": result_dict.get("success"),
            "email": result_dict.get("email"),
            "meters": result_dict.get("meters"),
            "failed_serials": result_dict.get("failed_serials"),
            "total_count": result_dict.get("total_count"),
            "returned_count": result_dict.get("returned_count"),
            "truncated": result_dict.get("truncated"),
            "notice": result_dict.get("notice"),
            "error": result_dict.get("error"),
        }
    if tool_name == "sweep_transducer_angles":
        return {
            "success": result_dict.get("success"),
            "serial_number": result_dict.get("serial_number"),
            "network_type": result_dict.get("network_type"),
            "resolved_angles": result_dict.get("resolved_angles"),
            "ranking": result_dict.get("ranking"),
            "best_angle": result_dict.get("best_angle"),
            "final_angle": result_dict.get("final_angle"),
            "final_action": result_dict.get("final_action"),
            "notice": result_dict.get("notice"),
            "error": result_dict.get("error"),
        }
    if tool_name == "set_zero_point":
        return {
            "success": result_dict.get("success"),
            "command": result_dict.get("command"),
            "mqtt_payload": result_dict.get("mqtt_payload"),
            "error": result_dict.get("error"),
            "report_excerpt": _compact_report_excerpt(result_dict.get("report")),
        }
    return result_dict


def _compact_tool_result_json_for_history(tool_name: str, result_dict: dict) -> str:
    return json.dumps(
        _compact_tool_result_for_history(tool_name, result_dict),
        default=str,
    )


def _record_tool_evidence_safe(
    *,
    conversation_id: str,
    tool_name: str,
    inp: dict,
    result_dict: dict,
    ok: bool,
    tool_use_id: str | None = None,
) -> dict | None:
    """Best-effort evidence ledger write; validation should never break chat."""
    try:
        return record_tool_evidence(
            conversation_id=conversation_id,
            turn_id=current_turn_id(),
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            input_payload=inp,
            raw_result=result_dict,
            compact_result=_compact_tool_result_for_history(tool_name, result_dict),
            success=ok,
        )
    except Exception as exc:
        emit_event(
            "tool_evidence_record_failed",
            tool=tool_name,
            conversation_id=conversation_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None


def _record_sweep_angle_evidence(
    *,
    conversation_id: str,
    inp: dict,
    result_dict: dict,
) -> None:
    """Record one evidence row per angle in a confirmed sweep result."""
    if not isinstance(result_dict.get("results"), list):
        return
    serial = str(result_dict.get("serial_number") or inp.get("serial_number") or "")
    for row in result_dict.get("results") or []:
        if not isinstance(row, dict):
            continue
        angle = str(row.get("angle") or "")
        payload = {
            "success": bool(row.get("write_success")) and bool(row.get("status_success")),
            "serial_number": serial,
            "angle": angle,
            "network_type": result_dict.get("network_type"),
            "sweep_final_policy": result_dict.get("final_action"),
            "measurement": row,
        }
        _record_tool_evidence_safe(
            conversation_id=conversation_id,
            tool_name="sweep_transducer_angles.angle_result",
            inp={"serial_number": serial, "transducer_angle": angle},
            result_dict=payload,
            ok=bool(payload["success"]),
            tool_use_id=f"{current_turn_id() or 'turn'}:{angle}",
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

    if tool_name == "rank_fleet_by_health":
        sns = inp.get("serial_numbers")
        if isinstance(sns, list) and sns:
            clean = [s.strip() for s in sns if isinstance(s, str) and s.strip()]
            if clean:
                return _clip_activity(f"Ranked fleet health for {', '.join(clean)}", 260)
        return _clip_activity("Ranked fleet health", 200)

    if tool_name == "triage_fleet_for_account" and email:
        return _clip_activity(f"Triaged fleet health for account {email}", 260)

    if tool_name == "list_tickets":
        count = result.get("count")
        if isinstance(count, int):
            return _clip_activity(f"Listed {count} ticket(s)", 180)
        return _clip_activity("Listed tickets", 160)

    if tool_name == "create_ticket":
        ticket = result.get("ticket")
        title = ticket.get("title") if isinstance(ticket, dict) else inp.get("title")
        if isinstance(title, str) and title.strip():
            return _clip_activity(f"Created ticket: {title.strip()}", 220)
        return _clip_activity("Created ticket", 160)

    if tool_name == "update_ticket":
        ticket = result.get("ticket")
        status = ticket.get("status") if isinstance(ticket, dict) else inp.get("status")
        if isinstance(status, str) and status.strip():
            return _clip_activity(f"Updated ticket status to {status.strip()}", 200)
        return _clip_activity("Updated ticket", 160)

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

    if tool_name == "compare_periods" and sn:
        return _clip_activity(f"Compared two flow periods for meter {sn}", 240)

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

    if tool_name == "sweep_transducer_angles" and sn:
        count = len(result.get("resolved_angles") or inp.get("transducer_angles") or [])
        best = result.get("best_angle")
        final = result.get("final_angle")
        if best and final:
            return _clip_activity(
                f"Swept {count} transducer angles for meter {sn}; best {best}, final {final}",
                260,
            )
        return _clip_activity(f"Swept {count} transducer angles for meter {sn}", 220)

    if tool_name == "set_zero_point" and sn:
        return _clip_activity(f"Put meter {sn} into set-zero-point state", 220)

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
    elif tool_name in {
        "analyze_flow_data",
        "configure_meter_pipe",
        "set_transducer_angle_only",
        "sweep_transducer_angles",
        "set_zero_point",
    }:
        if inp.get("network_type"):
            ctx["network_type"] = inp.get("network_type")
        if inp.get("meter_timezone"):
            ctx["timezone"] = inp.get("meter_timezone")
        if result.get("network_type"):
            ctx["network_type"] = result.get("network_type")

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
            else "transducer_angle_sweep"
            if tool_name == "sweep_transducer_angles"
            else "set_zero_point"
            if tool_name == "set_zero_point"
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
    current_values: dict | None = None,
) -> tuple[dict, dict]:
    if current_values is None:
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


def _angle_experiment_fields(serial: str, prepared_inputs: dict) -> dict:
    angles = prepared_inputs.get("transducer_angles")
    angle_list = ", ".join(str(a) for a in angles) if isinstance(angles, list) else "allowed angles"
    return {
        "workflow_type": "diagnostic_experiment",
        "experiment_goal": "Determine whether transducer angle is affecting signal quality.",
        "hypothesis": (
            "Given low current signal quality and pipe parameters treated as correct, if one tested "
            "angle has a reliably stronger signal score, angle alignment is likely contributing "
            f"to the meter's signal behavior for {serial}."
        ),
        "measurement_plan": (
            f"Set {serial} through {angle_list}, check meter status after each angle, "
            "compare signal score, level, and reliability, then set the best reliable angle."
        ),
        "success_criteria": (
            "At least one angle returns a reliable numeric signal score and the best angle can be "
            "verified after it is applied, with pipe parameters already treated as correct."
        ),
        "final_policy": "Set the best measured angle at the end when a reliable score exists.",
        "risk": (
            "This diagnostic experiment sends transducer-angle changes to the physical meter. "
            "It will set the best measured angle if a reliable score is available."
        ),
    }


def _experiment_confirmation_prompt(workflow: dict) -> str:
    serial = str(workflow.get("serial_number") or "the meter")
    proposed = workflow.get("proposed_values") if isinstance(workflow.get("proposed_values"), dict) else {}
    angles = proposed.get("transducer_angles")
    angle_list = ", ".join(str(a) for a in angles) if isinstance(angles, list) else "the allowed angles"
    estimate = proposed.get("estimated_duration_seconds")
    estimate_s = f" It should take about {int(estimate)} seconds." if isinstance(estimate, (int, float)) else ""
    return (
        f"I do not have enough evidence to safely judge whether angle alignment is the cause yet. "
        f"I can run a diagnostic angle sweep for meter {serial}: test {angle_list}, compare signal "
        f"quality after each setting, and set the best reliable angle at the end.{estimate_s} "
        "Please review and confirm before I send any changes to the meter."
    )


def _maybe_prepare_angle_experiment_from_validation(
    *,
    messages: list,
    conversation_id: str,
    user_scope: str,
    token: str,
    emit,
) -> tuple[str, dict | None]:
    """Return (assistant_message, workflow) when validation should request an experiment."""
    user_text = _last_user_text(messages)
    serial = _extract_first_serial(user_text)
    if not serial or not _looks_like_angle_diagnostic_request(user_text):
        return "", None

    emit(
        {
            "type": "validation_start",
            "message": "Checking current signal quality before preparing an angle experiment.",
        }
    )
    status_inp = {"serial_number": serial}
    emit({"type": "tool_call", "tool": "check_meter_status", "input": status_inp})
    status_json = _dispatch(
        "check_meter_status",
        status_inp,
        token,
        client_timezone=None,
        anthropic_api_key=None,
        conversation_id=conversation_id,
    )
    status_result = json.loads(status_json)
    status_ok = _sse_tool_succeeded(status_result)
    _record_tool_evidence_safe(
        conversation_id=conversation_id,
        tool_name="check_meter_status",
        inp=status_inp,
        result_dict=status_result,
        ok=status_ok,
        tool_use_id=f"{current_turn_id() or 'turn'}:angle-precheck",
    )
    _emit_tool_result_event(
        emit=emit,
        tool_name="check_meter_status",
        inp=status_inp,
        result_dict=status_result,
        ok=status_ok,
    )
    if not status_ok:
        msg = (
            "I cannot prepare the angle experiment yet because I could not read the meter's "
            f"current signal quality. {status_result.get('error') or ''}".strip()
        )
        emit(
            {
                "type": "validation_result",
                "verdict": "blocked",
                "message": "Current signal-quality evidence is required before an angle sweep.",
            }
        )
        return msg, None

    signal_ok, signal_reason = _signal_is_low_enough_for_angle_experiment(status_result)
    if not signal_ok:
        emit(
            {
                "type": "validation_result",
                "verdict": "blocked",
                "message": signal_reason,
            }
        )
        return (
            f"I would not start an angle sweep from the current evidence. {signal_reason} "
            "Angle sweeps are best reserved for signal quality that is zero or very low, after the pipe parameters are known to be correct.",
            None,
        )

    pipe_asserted = _pipe_correctness_asserted(user_text)
    if not pipe_asserted:
        pipe_seen = _pipe_config_present(status_result)
        pipe_hint = (
            "I can see configured pipe values on the meter, but I still need you to confirm they match the actual installation."
            if pipe_seen
            else "I also could not confirm pipe configuration from the current status."
        )
        emit(
            {
                "type": "validation_result",
                "verdict": "needs_clarification",
                "message": "Pipe parameters must be confirmed before using angle changes as a diagnostic.",
            }
        )
        return (
            f"The current signal is low enough to consider an angle experiment. {pipe_hint} "
            "Please confirm the pipe material, standard, and size are correct before I prepare the sweep.",
            None,
        )

    emit(
        {
            "type": "validation_result",
            "verdict": "needs_experiment",
            "message": "Signal quality is low and pipe parameters are treated as correct, so a controlled angle sweep is the next diagnostic step.",
            "next_action": "sweep_transducer_angles",
        }
    )
    emit(
        {
            "type": "tool_progress",
            "tool": "sweep_transducer_angles",
            "message": "Preparing angle sweep experiment…",
        }
    )
    prepared = prepare_sweep_confirmation_inputs(
        {
            "serial_number": serial,
            "apply_best_after_sweep": True,
        },
        token,
        profile_lookup=get_meter_profile,
    )
    if not prepared.get("success"):
        msg = (
            "I cannot prepare the angle experiment yet. "
            f"{prepared.get('error') or 'The meter profile could not be read.'}"
        )
        return msg, None

    inp = dict(prepared.get("inputs") or {})
    inp["apply_best_after_sweep"] = True
    current_values = (
        prepared.get("current_values")
        if isinstance(prepared.get("current_values"), dict)
        else None
    )
    workflow, meter_context = _confirmation_required_payload(
        conversation_id=conversation_id,
        user_scope=user_scope,
        tool_name="sweep_transducer_angles",
        inp=inp,
        token=token,
        current_values=current_values,
    )
    workflow.update(_angle_experiment_fields(serial, inp))
    workflow["message"] = "Review and confirm before the diagnostic experiment changes the meter."
    result_dict = {
        "success": True,
        "requires_confirmation": True,
        "confirmation_required": True,
        "action_id": workflow.get("action_id"),
        "config_workflow": workflow,
        "message": workflow["message"],
    }
    _record_tool_evidence_safe(
        conversation_id=conversation_id,
        tool_name="sweep_transducer_angles",
        inp=inp,
        result_dict=result_dict,
        ok=True,
        tool_use_id=str(workflow.get("action_id") or ""),
    )
    emit(
        {
            "type": "config_confirmation_required",
            "tool": "sweep_transducer_angles",
            "input": inp,
            "config_workflow": workflow,
            "meter_context": meter_context,
        }
    )
    return _experiment_confirmation_prompt(workflow), workflow


def _emit_preflight_evidence(
    *,
    prepared: dict,
    conversation_id: str | None,
    tool_use_id: str | None,
    emit,
) -> None:
    if not isinstance(prepared.get("evidence_results"), list):
        return
    for ix, item in enumerate(prepared.get("evidence_results") or []):
        if not isinstance(item, dict):
            continue
        tool_name = str(item.get("tool_name") or "").strip()
        inp = item.get("input") if isinstance(item.get("input"), dict) else {}
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if not tool_name:
            continue
        ok = _sse_tool_succeeded(result)
        if emit:
            emit({"type": "tool_call", "tool": tool_name, "input": inp, "preflight": True})
            _emit_tool_result_event(
                emit=emit,
                tool_name=tool_name,
                inp=inp,
                result_dict=result,
                ok=ok,
            )
        if conversation_id:
            _record_tool_evidence_safe(
                conversation_id=conversation_id,
                tool_name=tool_name,
                inp=inp,
                result_dict=result,
                ok=ok,
                tool_use_id=f"{tool_use_id or current_turn_id() or 'turn'}:preflight:{ix}",
            )


def _prepare_write_confirmation_inputs(
    tool_name: str,
    inp: dict,
    token: str,
    *,
    client_timezone: str | None = None,
    anthropic_api_key: str | None = None,
    conversation_id: str | None = None,
    emit=None,
    tool_use_id: str | None = None,
) -> dict:
    if tool_name == "sweep_transducer_angles":
        return prepare_sweep_confirmation_inputs(
            inp,
            token,
            profile_lookup=get_meter_profile,
        )
    if tool_name == "set_zero_point":
        if emit:
            emit(
                {
                    "type": "validation_start",
                    "message": "Checking current status and recent flow before set-zero-point.",
                }
            )
        prepared = prepare_zero_point_confirmation_inputs(
            inp,
            token,
            profile_lookup=get_meter_profile,
            status_lookup=check_meter_status,
            flow_analysis_lookup=analyze_flow_data,
            display_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
        )
        _emit_preflight_evidence(
            prepared=prepared,
            conversation_id=conversation_id,
            tool_use_id=tool_use_id,
            emit=emit,
        )
        verdict = prepared.get("preflight") if isinstance(prepared.get("preflight"), dict) else {}
        if emit and verdict:
            emit(
                {
                    "type": "validation_result",
                    "verdict": "needs_confirmation" if prepared.get("success") else "blocked",
                    "message": verdict.get("summary") or prepared.get("error"),
                    "next_action": "set_zero_point" if prepared.get("success") else None,
                }
            )
        return prepared
    return {"success": True, "inputs": inp, "current_values": None}


def _confirmation_prompt(workflow: dict) -> str:
    serial = str(workflow.get("serial_number") or "the meter")
    proposed = workflow.get("proposed_values") if isinstance(workflow.get("proposed_values"), dict) else {}
    label = ""
    current = workflow.get("current_values") if isinstance(workflow.get("current_values"), dict) else {}
    if isinstance(current, dict) and current.get("label"):
        label = f" ({current.get('label')})"
    if workflow.get("tool") == "sweep_transducer_angles":
        angles = proposed.get("transducer_angles")
        angle_list = ", ".join(str(a) for a in angles) if isinstance(angles, list) else ""
        estimate = proposed.get("estimated_duration_seconds")
        estimate_s = f" Estimated runtime: about {int(estimate)} seconds." if isinstance(estimate, (int, float)) else ""
        if proposed.get("apply_best_after_sweep") is True:
            final = "I will set the best measured angle at the end if a reliable score is available."
        else:
            final = "I will leave the meter at the last successfully tested angle."
        change = f"transducer angle sweep across {angle_list or 'the allowed angles'}.{estimate_s} {final}"
    elif workflow.get("tool") == "set_transducer_angle_only":
        change = f"transducer angle to {proposed.get('transducer_angle')}"
    elif workflow.get("tool") == "set_zero_point":
        preflight = str(workflow.get("preflight_summary") or "").strip()
        change = "set-zero-point command (`{\"szv\":\"null\"}`)"
        if preflight:
            change = f"{change}. Preflight: {preflight}"
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


def _sweep_progress_message(event: dict) -> str:
    phase = str(event.get("phase") or "")
    angle = str(event.get("angle") or "").strip()
    index = event.get("index")
    total = event.get("total")
    prefix = ""
    if isinstance(index, int) and isinstance(total, int) and total > 0:
        prefix = f"{index}/{total}: "
    if phase == "set_angle":
        return f"Angle sweep: {prefix}setting transducer angle {angle}."
    if phase == "check_status":
        return f"Angle sweep: {prefix}checking signal quality after {angle}."
    if phase == "set_best":
        return f"Angle sweep: setting best measured angle {angle}."
    if phase == "verify_best":
        return f"Angle sweep: verifying best measured angle {angle}."
    return "Angle sweep: working."


def _sweep_summary_message(serial: str, result: dict) -> str:
    angles = result.get("resolved_angles")
    count = len(angles) if isinstance(angles, list) else 0
    best = result.get("best_angle")
    final = result.get("final_angle")
    action = result.get("final_action")
    if action == "set_best_after_sweep" and best:
        final_s = f"I set the best measured angle, {best}, as the final setting."
    elif action == "best_not_set_no_reliable_score":
        final_s = "No reliable numeric signal score was available, so I did not choose a best angle."
    elif final:
        final_s = f"I left the meter at the last successfully tested angle, {final}."
    else:
        final_s = "No final angle was confirmed from the sweep."
    ranking = result.get("ranking") if isinstance(result.get("ranking"), list) else []
    if best and ranking:
        top = ranking[0]
        score = top.get("signal_score") if isinstance(top, dict) else None
        score_s = f" with signal score {score:g}" if isinstance(score, (int, float)) else ""
        best_s = f" Best snapshot: {best}{score_s}."
    else:
        best_s = ""
    return (
        f"Confirmed. I swept {count} transducer angle(s) for meter {serial}. "
        f"{final_s}{best_s}"
    )


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
    ticket = result_dict.get("ticket")
    if isinstance(ticket, dict):
        event["ticket"] = ticket
    tickets = result_dict.get("tickets")
    if isinstance(tickets, list):
        event["tickets"] = tickets
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
    elif tool_name in {"rank_fleet_by_health", "triage_fleet_for_account"}:
        meters = result_dict.get("meters")
        if isinstance(meters, list):
            event["meters"] = meters
    elif tool_name == "sweep_transducer_angles":
        event["sweep_result"] = {
            "results": result_dict.get("results") if isinstance(result_dict.get("results"), list) else [],
            "ranking": result_dict.get("ranking") if isinstance(result_dict.get("ranking"), list) else [],
            "best_angle": result_dict.get("best_angle"),
            "final_angle": result_dict.get("final_angle"),
            "final_action": result_dict.get("final_action"),
            "notice": result_dict.get("notice"),
        }
    emit(event)
    return event


def _dispatch(
    name: str,
    inputs: dict,
    token: str,
    *,
    client_timezone: str | None = None,
    anthropic_api_key: str | None = None,
    conversation_id: str | None = None,
) -> str:
    """Route a tool call to the correct function and return the result as JSON."""
    from meter_tools import METER_REGISTRY
    result = METER_REGISTRY.dispatch(
        name,
        inputs,
        token=token,
        client_timezone=client_timezone,
        anthropic_api_key=anthropic_api_key,
        conversation_id=conversation_id,
    )
    if not isinstance(result, dict):
        result = {"error": f"Tool {name!r} returned non-dict result"}
    return json.dumps(result, default=str)


def _dispatch_with_optional_conversation_id(
    name: str,
    inputs: dict,
    token: str,
    *,
    client_timezone: str | None,
    anthropic_api_key: str | None,
    conversation_id: str | None,
) -> str:
    """Call ``_dispatch`` while tolerating older test doubles.

    Some tests monkeypatch ``_dispatch`` with a narrower keyword-only
    signature. The real dispatcher accepts ``conversation_id`` for workflow
    tools; retrying without it keeps those focused tests decoupled from this
    context plumbing.
    """
    try:
        return _dispatch(
            name,
            inputs,
            token,
            client_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
            conversation_id=conversation_id,
        )
    except TypeError as exc:
        if "conversation_id" not in str(exc):
            raise
        return _dispatch(
            name,
            inputs,
            token,
            client_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
        )


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
        and not any(_is_serial_only(tc.name) for tc in tool_calls)
    )

    # Write tools are guarded by an action-time confirmation card. If the model
    # proposes any write in this batch, stop at the first write and do not run
    # additional read tools from the same tool_use response.
    for write_index, tc in enumerate(tool_calls):
        if not _is_write(tc.name):
            continue
        inp_d = _coerce_tool_input(tc.input)
        emit({"type": "tool_call", "tool": tc.name, "input": inp_d})
        counters["tool_calls"] += 1
        prepared = _prepare_write_confirmation_inputs(
            tc.name,
            inp_d,
            token,
            client_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
            conversation_id=conversation_id,
            emit=emit,
            tool_use_id=tc.id,
        )
        if not prepared.get("success"):
            result_dict = {
                "success": False,
                "error": prepared.get("error") or "Could not prepare configuration confirmation.",
            }
            counters["tool_failures"] += 1
            _record_tool_evidence_safe(
                conversation_id=conversation_id,
                tool_name=tc.name,
                inp=inp_d,
                result_dict=result_dict,
                ok=False,
                tool_use_id=tc.id,
            )
            _emit_tool_result_event(
                emit=emit,
                tool_name=tc.name,
                inp=inp_d,
                result_dict=result_dict,
                ok=False,
            )
            hidden_results: list[dict] = []
            for j, other in enumerate(tool_calls):
                if j == write_index:
                    content = _compact_tool_result_json_for_history(tc.name, result_dict)
                else:
                    content = json.dumps(
                        {
                            "success": True,
                            "skipped_due_to_configuration_error": True,
                            "message": (
                                "Skipped because a configuration change could not be prepared. "
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
            return hidden_results, None
        inp_d = dict(prepared.get("inputs") or inp_d)
        current_values = (
            prepared.get("current_values")
            if isinstance(prepared.get("current_values"), dict)
            else None
        )
        workflow, meter_context = _confirmation_required_payload(
            conversation_id=conversation_id,
            user_scope=user_scope,
            tool_name=tc.name,
            inp=inp_d,
            token=token,
            current_values=current_values,
        )
        workflow_updates = (
            prepared.get("workflow_updates")
            if isinstance(prepared.get("workflow_updates"), dict)
            else None
        )
        if workflow_updates:
            workflow.update(workflow_updates)
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
        pending_result = json.loads(result_json)
        _record_tool_evidence_safe(
            conversation_id=conversation_id,
            tool_name=tc.name,
            inp=inp_d,
            result_dict=pending_result,
            ok=True,
            tool_use_id=tc.id,
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
            elif _is_heartbeat_progress(tc.name):
                result_json = _run_dispatch_with_heartbeat_progress(
                    tc.name, inp_d, token,
                    client_timezone=client_timezone,
                    conversation_id=conversation_id,
                    emit=emit,
                    anthropic_api_key=anthropic_api_key,
                )
            else:
                result_json = _dispatch_with_optional_conversation_id(
                    tc.name, inp_d, token,
                    client_timezone=client_timezone,
                    anthropic_api_key=anthropic_api_key,
                    conversation_id=conversation_id,
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

        if _is_write(tc.name) and ok:
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
        _record_tool_evidence_safe(
            conversation_id=conversation_id,
            tool_name=tc.name,
            inp=inp_d,
            result_dict=result_dict,
            ok=ok,
            tool_use_id=tc.id,
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
    if tool_name == "sweep_transducer_angles":
        result = sweep_transducer_angles(
            inp["serial_number"],
            inp.get("transducer_angles"),
            token,
            apply_best_after_sweep=bool(inp.get("apply_best_after_sweep")),
            anthropic_api_key=anthropic_api_key,
            on_progress=lambda ev: emit(
                {
                    "type": "tool_progress",
                    "tool": tool_name,
                    "message": _sweep_progress_message(ev),
                }
            ),
            profile_lookup=get_meter_profile,
            set_angle_func=set_transducer_angle_only,
            check_status_func=check_meter_status,
        )
        result_json = json.dumps(result, default=str)
    else:
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
    _record_tool_evidence_safe(
        conversation_id=action.conversation_id,
        tool_name=tool_name,
        inp=inp,
        result_dict=result,
        ok=ok,
        tool_use_id=action.action_id,
    )
    if tool_name == "sweep_transducer_angles":
        _record_sweep_angle_evidence(
            conversation_id=action.conversation_id,
            inp=inp,
            result_dict=result,
        )
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

    if tool_name == "sweep_transducer_angles":
        msg = _sweep_summary_message(serial, result)
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
        _record_tool_evidence_safe(
            conversation_id=action.conversation_id,
            tool_name="check_meter_status",
            inp=status_inp,
            result_dict=status_result,
            ok=status_ok,
            tool_use_id=f"{action.action_id}:verification",
        )
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
    elif tool_name == "set_zero_point":
        msg = f"Confirmed. I put meter {serial} into set-zero-point state."
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
                      {"type": "rate_limit_wait", ...}  — waiting for input TPM headroom
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
      if not (confirmed_action_id or cancelled_action_id or superseded_action_id):
        experiment_reply, experiment_workflow = _maybe_prepare_angle_experiment_from_validation(
            messages=messages,
            conversation_id=conversation_id,
            user_scope=effective_user_scope,
            token=token,
            emit=_emit,
        )
        if experiment_reply:
            messages.append(
                {"role": "assistant", "content": [{"type": "text", "text": experiment_reply}]}
            )
            outcome = (
                "diagnostic_experiment_confirmation_required"
                if experiment_workflow
                else "diagnostic_experiment_blocked"
            )
            return _finish(outcome, experiment_reply, history_replaced)

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

        _wait_for_tpm_headroom_with_progress(
            _estimate_stream_turn_tpm_cost(token_count),
            emit=_emit,
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
                _wait_for_tpm_headroom_with_progress(
                    _estimate_stream_turn_tpm_cost(token_count),
                    emit=_emit,
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
