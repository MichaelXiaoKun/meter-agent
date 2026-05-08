"""
Zero-point command run.

Deterministic: device by serial -> MQTT ``szv`` only.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from processors.device_and_catalog import resolve_device_context_by_serial
from processors.mqtt_pipe import apply_zero_point_over_mqtt


def run_zero_point(*, serial_number: str, token: str) -> str:
    """
    Execute zero-point pipeline and return Markdown (body only; caller may add header).

    Safety checks are enforced by the orchestrator before this CLI is called.
    """
    ctx: Dict[str, Any] = resolve_device_context_by_serial(token, serial_number)
    lines = [
        "## Set zero point",
        "",
        "### Device context",
        f"```json\n{json.dumps(ctx, indent=2, default=str)}\n```",
        "",
    ]
    if ctx.get("error"):
        lines.append(f"**Stopped:** {ctx['error']}")
        return "\n".join(lines)

    mqtt = apply_zero_point_over_mqtt(ctx)
    lines.extend(
        [
            "### MQTT (szv only)",
            f"```json\n{json.dumps(mqtt, indent=2, default=str)}\n```",
            "",
        ]
    )
    if mqtt.get("error"):
        lines.append(f"**Stopped:** {mqtt['error']}")
        return "\n".join(lines)

    lines.append(
        "**Summary:** Published `szv` only to enter set-zero-point state. "
        f"Verification: {'ok' if mqtt.get('verification_ok') else 'inconclusive'} - "
        f"{mqtt.get('verification_note', '')}"
    )
    return "\n".join(lines)
