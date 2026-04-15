"""
Transducer angle–only run (no pipe catalog, no spm/spd/spt/smp).

Deterministic: device by serial → angle code → MQTT **ssa** only.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from processors.device_and_catalog import resolve_device_context_by_serial
from processors.mqtt_pipe import apply_ssa_only_over_mqtt
from processors.transducer_angle import resolve_transducer_angle


def run_transducer_angle_only(*, serial_number: str, transducer_angle: str, token: str) -> str:
    """
    Execute angle-only pipeline and return Markdown (body only; caller may add header).

    Raises nothing; errors are embedded in the returned Markdown.
    """
    ctx: Dict[str, Any] = resolve_device_context_by_serial(token, serial_number)
    lines = [
        "## Transducer angle (SSA only)",
        "",
        "### Device context",
        f"```json\n{json.dumps(ctx, indent=2, default=str)}\n```",
        "",
    ]
    if ctx.get("error"):
        lines.append(f"**Stopped:** {ctx['error']}")
        return "\n".join(lines)

    ang = resolve_transducer_angle(ctx, transducer_angle)
    lines.extend(
        [
            "### Angle resolution",
            f"```json\n{json.dumps(ang, indent=2, default=str)}\n```",
            "",
        ]
    )
    if ang.get("error"):
        lines.append(f"**Stopped:** {ang['error']}")
        return "\n".join(lines)

    mqtt = apply_ssa_only_over_mqtt(ctx, str(ang["ssa_code"]))
    lines.extend(
        [
            "### MQTT (ssa only)",
            f"```json\n{json.dumps(mqtt, indent=2, default=str)}\n```",
            "",
        ]
    )
    if mqtt.get("error"):
        lines.append(f"**Stopped:** {mqtt['error']}")
        return "\n".join(lines)

    lines.append(
        "**Summary:** Published `ssa` only (no pipe field messages). "
        f"Verification: {'ok' if mqtt.get('verification_ok') else 'inconclusive'} — "
        f"{mqtt.get('verification_note', '')}"
    )
    return "\n".join(lines)
