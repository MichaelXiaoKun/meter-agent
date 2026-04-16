"""
agent.py — Conversational orchestrator agent.

Maintains full conversation history across turns and delegates to sub-agents
via tools: resolve_time_range, check_meter_status, analyze_flow_data, configure_meter_pipe,
set_transducer_angle_only.

Usage (from an outer chat loop):
    messages = []
    reply, replaced = run_turn(messages, token="...")   # modifies messages in place
    # If replaced is True, persist messages with replace (not append) — see store.replace_conversation_messages.
"""

import json
import os
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
    _FLOW_ANALYSIS_DEF,
    _PIPE_CONFIGURATION_DEF,
    _SET_TRANSDUCER_ANGLE_DEF,
]

# Cheapest tier (Haiku). Override main chat with ORCHESTRATOR_MODEL (e.g. claude-sonnet-4-6).
_CHEAP_MODEL = "claude-haiku-4-5"
_DEFAULT_ORCHESTRATOR_MODEL = _CHEAP_MODEL
_MODEL = (os.environ.get("ORCHESTRATOR_MODEL") or _DEFAULT_ORCHESTRATOR_MODEL).strip() or _DEFAULT_ORCHESTRATOR_MODEL
_MODEL_CONTEXT_WINDOW = 200_000   # tokens
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


def get_rate_limit_config_for_api() -> dict[str, float | int]:
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
    }

_SYSTEM_PROMPT = """\
You are a conversational assistant for bluebot ultrasonic flow meter analysis.
You help field engineers and operators check meter health, analyse flow data, and configure
pipe parameters by delegating to specialist sub-agents through tool calls.

Available tools:
  resolve_time_range     — convert natural language time expressions to Unix timestamps
  check_meter_status     — fetch current meter health (online state, signal quality, pipe config)
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
"""


def _count_tokens(client: anthropic.Anthropic, messages: list) -> int:
    """Return the input token count for the current conversation state."""
    response = client.messages.count_tokens(
        model=_MODEL,
        system=_SYSTEM_PROMPT,
        tools=TOOLS,
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
) -> bool:
    """
    Repeatedly summarize older turns until _count_tokens <= max_input_tokens or no progress.
    Returns True if *messages* was modified.
    """
    changed = False
    for _ in range(24):
        ntok = _count_tokens(client, messages)
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
                after = _count_tokens(client, messages)
                record_input_tokens(after)
                if after <= max_input_tokens:
                    return changed
        if not round_progress:
            break

    final_ntok = _count_tokens(client, messages)
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

    elif name == "analyze_flow_data":
        result = analyze_flow_data(
            inputs["serial_number"],
            inputs["start"],
            inputs["end"],
            token,
            display_timezone=client_timezone,
            anthropic_api_key=anthropic_api_key,
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
        client_timezone: Optional IANA zone from the browser (e.g. America/New_York).
                    Used for resolve_time_range and analyze_flow_data display_range when set.
        on_event:   Optional callable(event: dict) for progress updates.
                    Fired before and after each tool call with:
                      {"type": "token_usage",  "tokens": int, "pct": float}  — before each API call
                      {"type": "compressing",  "tokens": int, "pct": float}  — when compression fires
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
    client = anthropic.Anthropic(api_key=_anthropic_key)
    history_replaced = False

    while True:
        token_count = _count_tokens(client, messages)
        pct = token_count / _MODEL_CONTEXT_WINDOW
        _emit({"type": "token_usage", "tokens": token_count, "pct": pct})

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
                client, messages, _MAX_INPUT_TOKENS_TARGET
            ):
                history_replaced = True
            token_count = _count_tokens(client, messages)
            pct = token_count / _MODEL_CONTEXT_WINDOW
            _emit({"type": "token_usage", "tokens": token_count, "pct": pct})
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
                    model=_MODEL,
                    max_tokens=4096,
                    system=_SYSTEM_PROMPT,
                    tools=TOOLS,
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
                    client, messages, _MAX_INPUT_TOKENS_TARGET
                ):
                    history_replaced = True
                token_count = _count_tokens(client, messages)
                pct = token_count / _MODEL_CONTEXT_WINDOW
                _emit({"type": "token_usage", "tokens": token_count, "pct": pct})
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
                    event: dict = {
                        "type": "tool_result",
                        "tool": block.name,
                        "success": result_dict.get("error") is None,
                    }
                    if block.name == "analyze_flow_data":
                        event["plot_paths"] = result_dict.get("plot_paths", [])
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
