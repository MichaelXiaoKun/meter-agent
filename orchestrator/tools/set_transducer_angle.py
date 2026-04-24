"""
set_transducer_angle.py — Orchestrator tool for SSA-only updates (no pipe catalog).

Runs pipe-configuration-agent in angle-only mode (same subprocess pattern as configure_meter_pipe).
"""

from __future__ import annotations

import os
import sys

from subprocess_env import tool_subprocess_env
from tools.meter_profile import get_meter_profile
from tools.pipe_subprocess import run_pipe_configuration_agent, subprocess_error_message
from tools.transducer_angle_preflight import preflight_validate_transducer_angle

_AGENT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "pipe-configuration-agent")
)

_VENV_PYTHON = os.path.join(_AGENT_DIR, ".venv", "bin", "python")
_PYTHON = _VENV_PYTHON if os.path.exists(_VENV_PYTHON) else sys.executable

TOOL_DEFINITION = {
    "name": "set_transducer_angle_only",
    "description": (
        "Change **only** the meter transducer angle over MQTT (**ssa** payload). "
        "Uses the physical **serial_number** for management device lookup (NUI, Wi-Fi vs LoRaWAN). "
        "Does **not** change pipe material/standard/size (no spm/spd/spt or smp). "
        "Use when the user explicitly wants an angle-only update. "
        "For full pipe + angle configuration, use configure_meter_pipe instead. "
        "For **comparing** angles (user wants best signal quality across several settings), call this "
        "tool once per angle in sequence—each time followed by check_meter_status—not a single angle only. "
        "After a successful run, the orchestrator should confirm behaviour with check_meter_status "
        "on the same serial (signal quality / online state) before finishing the turn."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_number": {
                "type": "string",
                "description": "Physical meter serial (management serialNumber query).",
            },
            "transducer_angle": {
                "type": "string",
                "description": "Angle label, e.g. '45º' / '35°' / '25'.",
            },
        },
        "required": ["serial_number", "transducer_angle"],
    },
}


def set_transducer_angle_only(
    serial_number: str,
    transducer_angle: str,
    token: str,
    *,
    anthropic_api_key: str | None = None,
) -> dict:
    """
    Run pipe-configuration-agent --angle-only and return its report.

    Returns:
        {"success": bool, "report": str | None, "error": str | None}
    """
    # OpenAI/Gemini tool JSON often uses numbers (e.g. angle 45); subprocess argv must be str.
    serial_number = str(serial_number or "").strip()
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
            "--angle-only",
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
