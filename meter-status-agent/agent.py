"""
Meter Status Agent

An LLM agent that interprets the raw status payload from a bluebot flow meter,
calls deterministic processor tools to derive facts, then synthesises a
structured status report.

Contract:
  - The LLM may ONLY call tools defined in TOOLS below.
  - Every fact in the final report must originate from a processor return value.
  - The LLM never computes or infers values itself.
"""

import json
import os
from typing import Any, Dict

import anthropic

from processors.staleness import compute_staleness
from processors.signal import interpret_signal_quality
from processors.pipe_config import interpret_pipe_config

TOOLS = [
    {
        "name": "compute_staleness",
        "description": (
            "Compute how long ago the meter last reported and classify its communication status. "
            "Returns elapsed time (seconds, minutes, hours, days) and a status label: "
            "'fresh' (< 1h), 'stale' (< 24h), 'absent' (< 7d), or 'lost' (≥ 7d)."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "interpret_signal_quality",
        "description": (
            "Interpret the meter's current ultrasonic signal quality score (0–100). "
            "Quality reflects acoustic signal strength through the pipe wall. "
            "Score ≤ 60 is unreliable — caused by air bubbles/drained pipe or "
            "coupling pads not properly seated on the pipe wall. "
            "Returns level ('good'/'degraded'/'poor'), reliability flag, interpretation, "
            "and whether action is needed."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "interpret_pipe_config",
        "description": (
            "Interpret the pipe geometry: compute inner diameter from outer diameter and wall thickness, "
            "and summarise the inferred nominal pipe size and standard. "
            "Returns all dimensions in mm and inches, nominal size label, pipe standard, and match diff."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


def _dispatch_tool(name: str, status: Dict[str, Any]) -> Any:
    """Route a tool call to the correct processor function."""
    if name == "compute_staleness":
        return compute_staleness(status["last_message_at"])

    elif name == "interpret_signal_quality":
        return interpret_signal_quality(status["signal_quality"])

    elif name == "interpret_pipe_config":
        return interpret_pipe_config(
            status["pipe_outer_diameter"],
            status["pipe_wall_thickness"],
            status.get("inferred_nominal_size"),
        )

    else:
        raise ValueError(f"Unknown tool: {name}")


def analyze(status: Dict[str, Any], serial_number: str) -> str:
    """
    Run the agentic analysis loop on a raw meter status payload.

    Args:
        status:         Raw dict returned by the bluebot status API
        serial_number:  Meter serial number for context

    Returns:
        Markdown-formatted status report string.
    """
    system_prompt = (
        "You are a field engineer assistant specialising in ultrasonic flow meters. "
        "You are given the current status of a bluebot ultrasonic flow meter. "
        "Ultrasonic meters measure flow by sending sound pulses through the pipe wall — "
        "signal quality reflects how cleanly this signal is received. "
        "Low signal quality (≤ 60) is caused by: air bubbles or drained pipe (intermittent), "
        "or coupling pads not properly seated between the transducer and pipe wall (persistent). "
        "You have processor tools to derive facts from the raw status. "
        "You MUST call all three tools and use only their outputs in your report — "
        "never derive numbers yourself. "
        "Write a concise Markdown status report covering: connectivity, signal quality, "
        "pipe configuration, and an overall health assessment with recommended actions if needed."
    )

    user_message = (
        f"Generate a status report for meter `{serial_number}`.\n\n"
        f"**Raw status payload:**\n```json\n{json.dumps(status, indent=2)}\n```\n\n"
        "Call all processor tools, then write the report."
    )

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model=os.environ.get("BLUEBOT_METER_STATUS_MODEL", "claude-haiku-4-5"),
            max_tokens=2048,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "Analysis complete (no text output)."

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = _dispatch_tool(block.name, status)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

        else:
            break

    return "Analysis ended unexpectedly."
