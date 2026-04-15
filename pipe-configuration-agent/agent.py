"""
Pipe configuration agent

Uses three deterministic processor-backed tools:
  1) resolve_device_and_pipe_specs — management API lookups
  2) resolve_transducer_angle — Wi-Fi vs LoRaWAN angle maps → ssa_code
  3) apply_pipe_configuration_over_mqtt — MQTT publish + subscribe verification

The LLM must ground its final status report on tool outputs only.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import anthropic

from processors.device_and_catalog import resolve_device_and_pipe_specs
from processors.mqtt_pipe import apply_pipe_configuration_over_mqtt
from processors.transducer_angle import resolve_transducer_angle

TOOLS = [
    {
        "name": "resolve_device_and_pipe_specs",
        "description": (
            "Look up a meter by **serial number** in management. Determine Wi-Fi vs LoRaWAN "
            "(NUI != serial ⇒ LoRaWAN) and whether the model is 50-W (50W/50w/50-W), "
            "then resolve pipe material/standard/nominal size against management catalog "
            "endpoints. **standard_index** for MQTT is taken only from the matched row's "
            "**`index`** field on GET /management/v1/standard?material=…. "
            "Returns that value plus outerDiameterMm/wallThicknessMm and matched labels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "serial_number": {"type": "string"},
                "pipe_material": {"type": "string"},
                "pipe_standard": {"type": "string"},
                "pipe_size": {"type": "string"},
            },
            "required": ["serial_number", "pipe_material", "pipe_standard", "pipe_size"],
        },
    },
    {
        "name": "resolve_transducer_angle",
        "description": (
            "Convert the operator's transducer angle label (e.g. 45º, 35°) to the firmware "
            "**ssa** numeric string using Wi-Fi vs LoRaWAN code maps. Requires the successful "
            "JSON from resolve_device_and_pipe_specs (for is_lorawan). This is a separate "
            "processor from MQTT transport."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipe_resolution": {
                    "type": "object",
                    "description": "Full JSON from resolve_device_and_pipe_specs (error must be null).",
                },
                "transducer_angle": {
                    "type": "string",
                    "description": "Angle label, e.g. '45º' / '35°' / '25'.",
                },
            },
            "required": ["pipe_resolution", "transducer_angle"],
        },
    },
    {
        "name": "apply_pipe_configuration_over_mqtt",
        "description": (
            "Publish pipe configuration to meter/sub/<NUI> using the 50-W single-message "
            "format or the non–50-W spm/spd/spt sequence, wait between publishes, then publish "
            "**ssa** using **ssa_code** from resolve_transducer_angle. Subscribes to "
            "meter/pub/<NUI> and performs best-effort verification of pipe index and mm fields. "
            "By default connects with the assigned client_id to plain TCP MQTT at "
            "mqtt-prod.bluebot.com:1883 (override with BLUEBOT_MQTT_HOST / BLUEBOT_MQTT_PORT if needed)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipe_resolution": {
                    "type": "object",
                    "description": "The full JSON object returned by resolve_device_and_pipe_specs.",
                },
                "ssa_code": {
                    "type": "string",
                    "description": "Numeric string from resolve_transducer_angle (payload key ssa).",
                },
            },
            "required": ["pipe_resolution", "ssa_code"],
        },
    },
]


def _dispatch_tool(name: str, inputs: Dict[str, Any], token: str) -> Any:
    if name == "resolve_device_and_pipe_specs":
        return resolve_device_and_pipe_specs(
            token,
            str(inputs["serial_number"]),
            str(inputs["pipe_material"]),
            str(inputs["pipe_standard"]),
            str(inputs["pipe_size"]),
        )

    if name == "resolve_transducer_angle":
        return resolve_transducer_angle(
            dict(inputs["pipe_resolution"]),
            str(inputs["transducer_angle"]),
        )

    if name == "apply_pipe_configuration_over_mqtt":
        return apply_pipe_configuration_over_mqtt(
            dict(inputs["pipe_resolution"]),
            str(inputs["ssa_code"]),
        )

    raise ValueError(f"Unknown tool: {name}")


def analyze(
    *,
    serial_number: str,
    pipe_material: str,
    pipe_standard: str,
    pipe_size: str,
    transducer_angle: str,
    token: str,
) -> str:
    system_prompt = (
        "You are a field engineer assistant for bluebot ultrasonic flow meters. "
        "You help operators push a validated pipe configuration (material/standard/size) "
        "to a meter and confirm it via MQTT telemetry. "
        "You MUST call tools in order:\n"
        "  1) resolve_device_and_pipe_specs\n"
        "  2) resolve_transducer_angle — only if (1) returns error=null; pass the full (1) JSON as pipe_resolution\n"
        "  3) apply_pipe_configuration_over_mqtt — only if (1) and (2) return error=null; "
        "pass the same pipe_resolution and ssa_code from (2)\n"
        "Never invent management, angle, or MQTT outcomes; ground every factual claim on tool JSON. "
        "If any tool fails, explain clearly and do not call later tools. "
        "If tool (3) succeeds but verification_ok is false, treat that as a warning. "
        "Write a concise Markdown status report with sections: device identification, "
        "resolved catalog selection, transducer angle resolution, radio type (Wi-Fi vs LoRaWAN), "
        "model path (50-W vs other), MQTT actions, verification, and next steps."
    )

    user_message = (
        "Configure pipe parameters on the meter and report results.\n\n"
        f"- serial_number: `{serial_number}`\n"
        f"- pipe_material: `{pipe_material}`\n"
        f"- pipe_standard: `{pipe_standard}`\n"
        f"- pipe_size: `{pipe_size}`\n"
        f"- transducer_angle: `{transducer_angle}`\n\n"
        "Call the tools using these exact values (do not substitute identifiers the user did not provide)."
    )

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
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
                    result = _dispatch_tool(block.name, block.input, token)
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
