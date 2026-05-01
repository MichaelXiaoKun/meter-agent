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
import tempfile
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

for p in reversed(_AGENT_DIRS):
    sp = str(p)
    if p.exists() and sp not in sys.path:
        sys.path.insert(0, sp)


_DATA_AGENT_DIR = str(_ROOT / "data-processing-agent")
_DATA_AGENT_MODULES = {
    "adaptive_fetch",
    "agent",
    "agent_template",
    "data_client",
    "interface",
    "main",
    "processors",
    "report",
}
_DATA_AGENT_TOP_LEVEL_TESTS = {
    "test_adaptive_fetch.py",
    "test_data_client_parallel_fetch.py",
    "test_flow_csv_artifact.py",
}


def _prefer_data_agent_imports() -> None:
    """Reset shared top-level module names before data-agent tests collect."""
    if _DATA_AGENT_DIR in sys.path:
        sys.path.remove(_DATA_AGENT_DIR)
    sys.path.insert(0, _DATA_AGENT_DIR)
    for name in list(sys.modules):
        if name in _DATA_AGENT_MODULES or name.startswith("processors."):
            sys.modules.pop(name, None)


def pytest_collect_file(file_path, parent):  # noqa: D401 - pytest hook
    path = Path(str(file_path))
    if "processors" in path.parts and "tests" in path.parts:
        _prefer_data_agent_imports()
    elif path.name in _DATA_AGENT_TOP_LEVEL_TESTS:
        _prefer_data_agent_imports()


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
    os.environ["BLUEBOT_CONV_DB"] = str(
        Path(tempfile.gettempdir()) / f"bluebot_meter_agent_tests_{os.getpid()}.db"
    )


def pytest_unconfigure(config):
    saved = getattr(config, "_saved_env", {})
    if "BLUEBOT_CONV_DB" not in saved:
        os.environ.pop("BLUEBOT_CONV_DB", None)
    for k, v in saved.items():
        os.environ[k] = v
