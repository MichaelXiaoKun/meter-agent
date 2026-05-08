"""
Shared env for subprocess-based tools (meter-status, flow analysis, pipe config).

Injects BLUEBOT_TOKEN and forwards all provider API keys so sub-agents can
use whichever provider their BLUEBOT_*_MODEL env var points to.

When the user supplies a per-request key via X-LLM-Key, it is passed as
*llm_api_key_override* and written into the appropriate env var based on the
model's provider (or as ANTHROPIC_API_KEY for backward compatibility).
"""

from __future__ import annotations

import os


def tool_subprocess_env(
    bluebot_token: str,
    anthropic_api_key: str | None = None,
) -> dict[str, str]:
    env: dict[str, str] = {**os.environ, "BLUEBOT_TOKEN": bluebot_token}

    # Forward the per-request key override.  We write it to all three
    # provider key vars so whichever provider the sub-agent model resolves to
    # will pick it up — only the matching one is actually used.
    ak = (anthropic_api_key or "").strip()
    if ak:
        env["ANTHROPIC_API_KEY"] = ak
        env["OPENAI_API_KEY"] = ak
        env["GEMINI_API_KEY"] = ak

    return env
