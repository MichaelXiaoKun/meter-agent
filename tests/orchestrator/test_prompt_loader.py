"""
Unit tests for :mod:`orchestrator.prompts` — the on-disk system-prompt loader.

These exercise the contract, not the prompt content:
  * the default version is ``v1`` and resolves to a non-empty UTF-8 string,
  * ``ORCHESTRATOR_PROMPT_VERSION`` overrides the default,
  * an explicit ``version`` argument overrides the env var,
  * unknown / malformed versions raise predictable errors,
  * :func:`available_versions` enumerates every shipped file.

The prompt text itself is guarded by ``test_system_prompt.py`` — keeping the two
concerns separate means prompt edits don't force loader test updates and vice
versa.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ORCHESTRATOR_DIR = Path(__file__).resolve().parents[2] / "orchestrator"
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

from prompts import (  # noqa: E402
    DEFAULT_PROMPT_VERSION,
    PromptNotFoundError,
    available_versions,
    load_system_prompt,
    prompt_path,
)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_version_is_v1():
    assert DEFAULT_PROMPT_VERSION == "v1"


def test_load_default_returns_nonempty_v1(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ORCHESTRATOR_PROMPT_VERSION", raising=False)
    text, version = load_system_prompt()
    assert version == "v1"
    assert isinstance(text, str)
    assert len(text) > 500, "v1 prompt should be substantial (hundreds of chars)"
    assert "You are a conversational assistant" in text


def test_prompt_path_points_at_markdown_file_in_package(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ORCHESTRATOR_PROMPT_VERSION", raising=False)
    p = prompt_path()
    assert p.name == "system_v1.md"
    assert p.parent.name == "prompts"
    assert p.exists(), f"default prompt file missing on disk: {p}"


# ---------------------------------------------------------------------------
# Version resolution (env > default, arg > env)
# ---------------------------------------------------------------------------


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORCHESTRATOR_PROMPT_VERSION", "v1")
    text, version = load_system_prompt()
    assert version == "v1"
    assert text


def test_explicit_arg_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORCHESTRATOR_PROMPT_VERSION", "nonexistent")
    text, version = load_system_prompt("v1")
    assert version == "v1"
    assert text


def test_whitespace_around_env_value_is_stripped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORCHESTRATOR_PROMPT_VERSION", "  v1  ")
    _, version = load_system_prompt()
    assert version == "v1"


def test_empty_env_var_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ORCHESTRATOR_PROMPT_VERSION", "")
    _, version = load_system_prompt()
    assert version == DEFAULT_PROMPT_VERSION


# ---------------------------------------------------------------------------
# Failure modes — we prefer loud over silent fallback.
# ---------------------------------------------------------------------------


def test_unknown_version_raises_prompt_not_found(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ORCHESTRATOR_PROMPT_VERSION", raising=False)
    with pytest.raises(PromptNotFoundError):
        load_system_prompt("v999")


def test_malformed_version_raises_value_error():
    # Slashes / spaces / path traversal must be rejected at the API boundary.
    with pytest.raises(ValueError):
        load_system_prompt("../etc/passwd")
    with pytest.raises(ValueError):
        load_system_prompt("has spaces")
    with pytest.raises(ValueError):
        load_system_prompt("bad/version")


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def test_available_versions_lists_v1():
    versions = available_versions()
    assert "v1" in versions
    assert sorted(versions) == versions, "available_versions must be sorted"


# ---------------------------------------------------------------------------
# Agent module wires the loader into ``_SYSTEM_PROMPT_VERSION``
# ---------------------------------------------------------------------------


def test_agent_module_exposes_resolved_prompt_version():
    # Load the orchestrator's ``agent.py`` by absolute file path — a bare
    # ``import agent`` resolves to the data-processing agent's module (it
    # wins on the pytest ``pythonpath``), which does not define the
    # prompt-version attributes. Putting ``orchestrator/`` at position 0
    # of ``sys.path`` also redirects the transitive ``import processors``
    # inside ``agent.py`` to the orchestrator's own processors package.
    import importlib.util
    import sys as _sys

    orch_dir = str(_ORCHESTRATOR_DIR)
    if _sys.path[:1] != [orch_dir]:
        try:
            _sys.path.remove(orch_dir)
        except ValueError:
            pass
        _sys.path.insert(0, orch_dir)
    for cached in ("processors", "processors.time_range", "agent"):
        _sys.modules.pop(cached, None)

    agent_path = _ORCHESTRATOR_DIR / "agent.py"
    spec = importlib.util.spec_from_file_location(
        "meter_orchestrator_agent_prompt_loader_tests", agent_path
    )
    assert spec is not None and spec.loader is not None
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)

    assert agent._SYSTEM_PROMPT, "agent._SYSTEM_PROMPT should be populated at import"
    assert agent._SYSTEM_PROMPT_VERSION in available_versions()
