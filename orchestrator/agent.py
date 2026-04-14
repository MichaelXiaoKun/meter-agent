"""
agent.py — Conversational orchestrator agent.

Maintains full conversation history across turns and delegates to sub-agents
via three tools: resolve_time_range, check_meter_status, analyze_flow_data.

Usage (from an outer chat loop):
    messages = []
    reply = run_turn(messages, token="...")   # modifies messages in place
    reply = run_turn(messages, token="...")   # subsequent turns retain context
"""

import json
import anthropic

from processors.time_range import TOOL_DEFINITION as _TIME_RANGE_DEF, resolve_time_range
from tools.meter_status import TOOL_DEFINITION as _METER_STATUS_DEF, check_meter_status
from tools.flow_analysis import TOOL_DEFINITION as _FLOW_ANALYSIS_DEF, analyze_flow_data

TOOLS = [_TIME_RANGE_DEF, _METER_STATUS_DEF, _FLOW_ANALYSIS_DEF]

_MODEL = "claude-sonnet-4-6"
_MODEL_CONTEXT_WINDOW = 200_000   # tokens
_COMPRESS_THRESHOLD   = 0.75      # compress when input hits 75% of context window
_COMPRESS_KEEP_RECENT = 6         # number of recent messages to leave untouched

_SYSTEM_PROMPT = """\
You are a conversational assistant for bluebot ultrasonic flow meter analysis.
You help field engineers and operators check meter health and analyse flow data
by delegating to specialist sub-agents through tool calls.

Available tools:
  resolve_time_range  — convert natural language time expressions to Unix timestamps
  check_meter_status  — fetch current meter health (online state, signal quality, pipe config)
  analyze_flow_data   — analyse historical flow rate data over a time range

Rules:
  1. If the user has not provided a device ID, ask for it before calling any tool.
  2. Always call resolve_time_range before analyze_flow_data when the user gives a
     time range in words. Translate the time expression to English before passing
     it as the description argument (e.g. "dernières 6 heures" → "last 6 hours",
     "最近6時間" → "last 6 hours").
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


def _compress_history(client: anthropic.Anthropic, messages: list) -> list:
    """
    Summarize older messages to reduce token usage.

    Keeps the last _COMPRESS_KEEP_RECENT messages verbatim and replaces
    everything before them with a single compressed summary message.
    Returns the messages list unchanged if summarization fails.
    """
    if len(messages) <= _COMPRESS_KEEP_RECENT:
        return messages

    older  = messages[:-_COMPRESS_KEEP_RECENT]
    recent = messages[-_COMPRESS_KEEP_RECENT:]

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
        summary_resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
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
        summary_text = summary_resp.content[0].text.strip()
    except Exception:
        return messages

    summary_message = {
        "role": "user",
        "content": f"[Context summary — older messages compressed]\n{summary_text}",
    }
    return [summary_message] + recent


def _dispatch(name: str, inputs: dict, token: str) -> str:
    """Route a tool call to the correct function and return the result as JSON."""
    if name == "resolve_time_range":
        result = resolve_time_range(inputs["description"])

    elif name == "check_meter_status":
        result = check_meter_status(inputs["device_id"], token)

    elif name == "analyze_flow_data":
        result = analyze_flow_data(
            inputs["device_id"],
            inputs["start"],
            inputs["end"],
            token,
        )

    else:
        result = {"error": f"Unknown tool: {name}"}

    return json.dumps(result, default=str)


def run_turn(messages: list, token: str, on_event=None) -> str:
    """
    Process one conversational turn.

    Appends the assistant's response (and any intermediate tool exchanges) to
    `messages` in place so that the next call retains full context.

    Args:
        messages:   Full conversation history (list of role/content dicts).
                    Modified in place — pass the same list on every turn.
        token:      bluebot Bearer token forwarded to sub-agent tool calls.
        on_event:   Optional callable(event: dict) for progress updates.
                    Fired before and after each tool call with:
                      {"type": "token_usage",  "tokens": int, "pct": float}  — before each API call
                      {"type": "compressing",  "tokens": int, "pct": float}  — when compression fires
                      {"type": "tool_call",    "tool": str, "input": dict}
                      {"type": "tool_result",  "tool": str, "success": bool}
                      {"type": "thinking"}  — while waiting for the LLM

    Returns:
        The assistant's final text reply for this turn.
    """
    def _emit(event: dict):
        if on_event:
            on_event(event)

    client = anthropic.Anthropic()

    while True:
        token_count = _count_tokens(client, messages)
        pct = token_count / _MODEL_CONTEXT_WINDOW
        _emit({"type": "token_usage", "tokens": token_count, "pct": pct})
        if pct >= _COMPRESS_THRESHOLD:
            _emit({"type": "compressing", "tokens": token_count, "pct": pct})
            api_messages = _compress_history(client, messages)
        else:
            api_messages = messages

        _emit({"type": "thinking"})
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

        if response.stop_reason == "end_turn":
            messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "(No response)"

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    _emit({"type": "tool_call", "tool": block.name, "input": block.input})
                    result_json = _dispatch(block.name, block.input, token)
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

    return "(Unexpected stop reason)"
