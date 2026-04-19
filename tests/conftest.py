"""
Shared pytest fixtures / sys.path setup for the meter_agent test suite.

Each sub-agent / orchestrator is an independent Python package rooted at its
own directory (no shared ``setup.py`` / ``pyproject.toml``). Adding those
directories to ``sys.path`` here lets tests do ordinary imports like::

    from processors.baseline_quality import evaluate_baseline_quality
    from tools.meter_profile import classify_network_type

…without any test-specific import gymnastics.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# Order matters only when symbol names collide; we keep each sub-agent first on
# its own test files (``rootdir``/sys.path invalidation is handled per module
# where needed via ``monkeypatch.syspath_prepend``).
_AGENT_DIRS = [
    _ROOT / "data-processing-agent",
    _ROOT / "orchestrator",
    _ROOT / "meter-status-agent",
    _ROOT / "pipe-configuration-agent",
]

for p in _AGENT_DIRS:
    sp = str(p)
    if p.exists() and sp not in sys.path:
        sys.path.insert(0, sp)


def pytest_collection_modifyitems(config, items):
    """Mark integration tests so they can be skipped with ``-m 'not integration'``."""
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker("integration")


# ---------------------------------------------------------------------------
# Environment hygiene — never let a developer's local .env leak into tests.
# ---------------------------------------------------------------------------


_BLUEBOT_ENV_PREFIXES = ("BLUEBOT_", "ANTHROPIC_API_KEY", "AUTH0_", "DATABASE_URL")


def pytest_configure(config):
    # Snapshot and clear Bluebot / Anthropic / Auth0 / DB env vars so tests
    # start from a clean slate regardless of shell state. Restored in
    # pytest_unconfigure for interactive reruns.
    config._saved_env = {
        k: v
        for k, v in os.environ.items()
        if k.startswith(_BLUEBOT_ENV_PREFIXES) or k == "DATABASE_URL"
    }
    for k in list(config._saved_env):
        os.environ.pop(k, None)


def pytest_unconfigure(config):
    saved = getattr(config, "_saved_env", {})
    for k, v in saved.items():
        os.environ[k] = v
