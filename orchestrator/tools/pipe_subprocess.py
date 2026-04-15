"""
Helpers for running pipe-configuration-agent as a subprocess.

When BLUEBOT_MQTT_DEBUG is set, stderr is inherited so MQTT trace lines from the child
reach the same terminal / log stream as uvicorn (capture_output would hide them).
"""

from __future__ import annotations

import os
import subprocess
def mqtt_debug_inherit_stderr() -> bool:
    return os.environ.get("BLUEBOT_MQTT_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


def run_pipe_configuration_agent(argv: list[str], *, cwd: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    """
    Run the pipe agent; stdout is always captured for the chat report.

    If BLUEBOT_MQTT_DEBUG is enabled in ``env``, stderr is inherited (live trace);
    otherwise stderr is captured so errors can be returned in the tool result.
    """
    if mqtt_debug_inherit_stderr():
        return subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=None,
        )
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
    )


def subprocess_error_message(result: subprocess.CompletedProcess) -> str:
    err = (result.stderr or "").strip()
    return err or f"Process exited with code {result.returncode}"
