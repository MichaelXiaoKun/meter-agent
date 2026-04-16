"""
Shared env for subprocess-based tools (meter-status, flow analysis, pipe config).

Injects BLUEBOT_TOKEN and optionally overrides ANTHROPIC_API_KEY when the user
supplies a key from the web UI.
"""

from __future__ import annotations

import os


def tool_subprocess_env(
    bluebot_token: str,
    anthropic_api_key: str | None = None,
) -> dict[str, str]:
    env: dict[str, str] = {**os.environ, "BLUEBOT_TOKEN": bluebot_token}
    ak = (anthropic_api_key or "").strip()
    if ak:
        env["ANTHROPIC_API_KEY"] = ak
    return env
