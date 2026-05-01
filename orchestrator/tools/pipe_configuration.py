"""
pipe_configuration.py — Orchestrator tool wrapper for the pipe-configuration-agent.

Runs the agent as a subprocess using its own virtual environment if present,
otherwise falls back to the current Python interpreter.
"""

from __future__ import annotations

import os
import sys

from shared.subprocess_env import tool_subprocess_env
from tools.meter_profile import get_meter_profile
from tools.pipe_subprocess import run_pipe_configuration_agent, subprocess_error_message
from tools.transducer_angle_preflight import preflight_validate_transducer_angle

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "pipe-configuration-agent")
)

_VENV_PYTHON = os.path.join(_AGENT_DIR, ".venv", "bin", "python")
_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

TOOL_DEFINITION = {
    "name": "configure_meter_pipe",
    "description": (
        "Full pipe setup: material/standard/nominal size via management APIs, then MQTT "
        "(spm/spd/spt or smp) plus transducer **ssa**. "
        "Requires the physical **serial_number** engraved on the meter (for LoRaWAN this is "
        "usually not the same as the networkUniqueIdentifier text users see in portals). "
        "This tool resolves NUI/model from management. "
        "MQTT defaults to mqtt-prod.bluebot.com:1883 with "
        "client_id lens_<uuid>. Use when the user asks to set or change pipe parameters "
        "on a meter. After success, confirm with check_meter_status on the same serial "
        "(connectivity and signal quality) before treating the change as complete."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_number": {
                "type": "string",
                "description": (
                    "Physical meter serial number for management lookup (engraved ID / asset tag)."
                ),
            },
            "pipe_material": {"type": "string"},
            "pipe_standard": {"type": "string"},
            "pipe_size": {"type": "string"},
            "transducer_angle": {
                "type": "string",
                "description": "Transducer angle label, e.g. '45º' / '35°' / '25'",
            },
        },
        "required": ["serial_number", "pipe_material", "pipe_standard", "pipe_size", "transducer_angle"],
    },
}


def configure_meter_pipe(
    serial_number: str,
    pipe_material: str,
    pipe_standard: str,
    pipe_size: str,
    transducer_angle: str,
    token: str,
    *,
    anthropic_api_key: str | None = None,
) -> dict:
    """
    Run the pipe-configuration-agent and return its report.

    Returns:
        {"success": bool, "report": str | None, "error": str | None}
    """
    # OpenAI/Gemini may return numeric or mixed types; subprocess argv must be str.
    serial_number = str(serial_number or "").strip()
    pipe_material = str(pipe_material if pipe_material is not None else "").strip()
    pipe_standard = str(pipe_standard if pipe_standard is not None else "").strip()
    pipe_size = str(pipe_size if pipe_size is not None else "").strip()
    transducer_angle = str(transducer_angle if transducer_angle is not None else "").strip()

    prof = get_meter_profile(serial_number, token)
    if not prof.get("success"):
        return {
            "success": False,
            "report": None,
            "error": prof.get("error")
            or "Could not load the device profile to validate the transducer angle before sending.",
        }
    v_err = preflight_validate_transducer_angle(transducer_angle, prof.get("network_type"))
    if v_err:
        return {"success": False, "report": None, "error": v_err}

    env = tool_subprocess_env(token, anthropic_api_key)
    result = run_pipe_configuration_agent(
        [
            _PYTHON,
            "main.py",
            "--serial",
            serial_number,
            "--material",
            pipe_material,
            "--standard",
            pipe_standard,
            "--size",
            pipe_size,
            "--angle",
            transducer_angle,
        ],
        cwd=_AGENT_DIR,
        env=env,
    )
    if result.returncode == 0:
        return {"success": True, "report": (result.stdout or "").strip(), "error": None}
    return {
        "success": False,
        "report": None,
        "error": subprocess_error_message(result),
    }
