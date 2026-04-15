"""
set_transducer_angle.py — Orchestrator tool for SSA-only updates (no pipe catalog).

Runs pipe-configuration-agent in angle-only mode (same subprocess pattern as configure_meter_pipe).
"""

from __future__ import annotations

import os
import sys

from tools.pipe_subprocess import run_pipe_configuration_agent, subprocess_error_message

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
        "For full pipe + angle configuration, use configure_meter_pipe instead."
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


def set_transducer_angle_only(serial_number: str, transducer_angle: str, token: str) -> dict:
    """
    Run pipe-configuration-agent --angle-only and return its report.

    Returns:
        {"success": bool, "report": str | None, "error": str | None}
    """
    env = {**os.environ, "BLUEBOT_TOKEN": token}
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
