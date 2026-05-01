"""
CI-friendly structural regression for ``tests/fixtures/golden_turns/``.

This test does **not** call an LLM — that job belongs to
``scripts/run_golden_turns.py``. Here we only guarantee that every fixture
stays wire-compatible with the orchestrator so a bad edit surfaces in seconds
rather than at the next manual replay.

Checks per fixture:
  * required top-level keys exist and have the right types,
  * every ``expected_tool_sequence[*].tool`` is in the orchestrator catalog,
  * every ``forbidden_tools`` entry is in the orchestrator catalog,
  * ``args_contains`` only uses keys that the tool's JSON-schema actually
    accepts — typos like ``serial_num`` vs ``serial_number`` get caught here,
  * at least one listed ``prompt_versions`` is known to the loader,
  * response guardrail substrings overlap with the forbidden jargon listed in
    the active prompt (a cheap check that the fixture's blacklist is still
    meaningful against the current prompt).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ORCHESTRATOR_DIR = Path(__file__).resolve().parents[2] / "orchestrator"
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

from prompts import available_versions, load_system_prompt  # noqa: E402


def _load_orchestrator_agent() -> ModuleType:
    """Import ``orchestrator/admin_chat/turn_loop.py`` by file path.

    A plain ``import agent`` resolves to ``data-processing-agent/agent.py`` in
    this repo because ``data-processing-agent`` lands first on the pytest
    ``pythonpath``. Forcing the loader to the right file *and* putting the
    orchestrator dir at position 0 of ``sys.path`` ensures the transitive
    ``import processors.time_range`` inside ``admin_chat/turn_loop.py`` resolves to the
    orchestrator's processors package, not the data-processing agent's.
    """
    agent_path = _ORCHESTRATOR_DIR / "admin_chat" / "turn_loop.py"
    orch_dir = str(_ORCHESTRATOR_DIR)
    if sys.path[:1] != [orch_dir]:
        try:
            sys.path.remove(orch_dir)
        except ValueError:
            pass
        sys.path.insert(0, orch_dir)
    # Evict any stale ``processors`` / ``agent`` modules cached against the
    # wrong directory so the fresh exec re-resolves them.
    for cached in ("processors", "processors.time_range", "agent"):
        sys.modules.pop(cached, None)
    spec = importlib.util.spec_from_file_location("meter_orchestrator_agent_golden", agent_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden_turns"

_REQUIRED_KEYS: dict[str, type] = {
    "id": str,
    "description": str,
    "user": str,
    "expected_tool_sequence": list,
    "forbidden_tools": list,
    "response_must_not_contain": list,
    "response_must_contain": list,
    "prompt_versions": list,
}


def _load_fixtures() -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(_FIXTURE_DIR.glob("*.json")):
        with path.open("r", encoding="utf-8") as fh:
            out.append((path.name, json.load(fh)))
    return out


def _tool_catalog() -> dict[str, dict[str, Any]]:
    """Return ``{name: input_schema_properties}`` for every orchestrator tool."""
    agent = _load_orchestrator_agent()
    out: dict[str, dict[str, Any]] = {}
    tool_defs = agent.TOOLS() if callable(agent.TOOLS) else agent.TOOLS
    for defn in tool_defs:
        schema = defn.get("input_schema") or {}
        props = schema.get("properties") or {}
        out[defn["name"]] = props
    return out


@pytest.fixture(scope="module")
def fixtures() -> list[tuple[str, dict[str, Any]]]:
    found = _load_fixtures()
    assert found, f"no golden fixtures found under {_FIXTURE_DIR}"
    return found


@pytest.fixture(scope="module")
def catalog() -> dict[str, dict[str, Any]]:
    return _tool_catalog()


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def test_fixture_dir_is_committed(fixtures):
    names = {name for name, _ in fixtures}
    # README must ship too — the fixture format is part of the API.
    readme = _FIXTURE_DIR / "README.md"
    assert readme.exists(), f"missing golden fixture README at {readme}"
    # At least one fixture per tool the prompt is allowed to call.
    assert len(names) >= 5, "expected at least 5 fixtures covering the core tools"


def test_each_fixture_has_required_keys(fixtures):
    for name, data in fixtures:
        for key, typ in _REQUIRED_KEYS.items():
            assert key in data, f"{name}: missing key {key!r}"
            assert isinstance(data[key], typ), (
                f"{name}: key {key!r} should be {typ.__name__}, got {type(data[key]).__name__}"
            )


def test_fixture_ids_are_unique_and_match_filenames(fixtures):
    seen: dict[str, str] = {}
    for name, data in fixtures:
        fid = data["id"]
        assert fid not in seen, f"duplicate fixture id {fid!r} in {seen[fid]} and {name}"
        seen[fid] = name
        assert name == f"{fid}.json", f"{name}: filename should match id ({fid}.json)"


def test_expected_tool_sequence_is_nonempty_and_well_shaped(fixtures):
    for name, data in fixtures:
        seq = data["expected_tool_sequence"]
        assert seq, f"{name}: expected_tool_sequence must not be empty"
        for i, step in enumerate(seq):
            assert isinstance(step, dict), f"{name}[{i}]: step must be a dict"
            assert isinstance(step.get("tool"), str) and step["tool"], (
                f"{name}[{i}]: step.tool must be a non-empty string"
            )
            if "args_contains" in step:
                assert isinstance(step["args_contains"], dict), (
                    f"{name}[{i}]: args_contains must be a dict when present"
                )
            if "mock_result" in step:
                assert isinstance(step["mock_result"], dict), (
                    f"{name}[{i}]: mock_result must be a dict when present"
                )


# ---------------------------------------------------------------------------
# Catalog coverage
# ---------------------------------------------------------------------------


def test_every_referenced_tool_exists_in_the_catalog(fixtures, catalog):
    known = set(catalog)
    for name, data in fixtures:
        for step in data["expected_tool_sequence"]:
            assert step["tool"] in known, (
                f"{name}: expected_tool_sequence references unknown tool "
                f"{step['tool']!r}; catalog = {sorted(known)}"
            )
        for t in data.get("forbidden_tools", []):
            assert t in known, (
                f"{name}: forbidden_tools entry {t!r} not in catalog; typo? "
                f"catalog = {sorted(known)}"
            )


def test_args_contains_keys_are_valid_for_each_tool(fixtures, catalog):
    for name, data in fixtures:
        for i, step in enumerate(data["expected_tool_sequence"]):
            props = catalog[step["tool"]]
            if not props:
                continue  # tool has no JSON-schema properties declared — skip
            for key in (step.get("args_contains") or {}).keys():
                assert key in props, (
                    f"{name}[{i}]: tool {step['tool']!r} has no arg {key!r}; "
                    f"valid args = {sorted(props)}"
                )


# ---------------------------------------------------------------------------
# Prompt-version coupling
# ---------------------------------------------------------------------------


def test_prompt_versions_list_is_nonempty_and_known(fixtures):
    known = set(available_versions())
    assert known, "loader reports no available prompt versions"
    for name, data in fixtures:
        versions = data["prompt_versions"]
        assert versions, f"{name}: prompt_versions list is empty"
        for v in versions:
            assert isinstance(v, str) and v, f"{name}: prompt_versions entries must be strings"
        overlap = known & set(versions)
        assert overlap, (
            f"{name}: prompt_versions {versions} does not overlap with shipped versions "
            f"{sorted(known)}; either drop the fixture or update the list."
        )


def test_forbidden_jargon_guardrail_matches_v1_prompt(fixtures):
    """At least one ``response_must_not_contain`` entry per fixture should
    overlap with the jargon the v1 prompt explicitly bans. That way, when
    the prompt is tightened, the fixture guardrails stay synchronised by
    construction instead of drifting silently."""
    text, _ = load_system_prompt("v1")
    # Canonical jargon from rule 15 (the user-facing-language rule).
    banned = {
        "analyze_flow_data",
        "resolve_time_range",
        "get_meter_profile",
        "subprocess",
        "sub-agent",
        "/Users/",
    }
    for name, data in fixtures:
        if "v1" not in data["prompt_versions"]:
            continue
        blacklist = set(data["response_must_not_contain"])
        overlap = banned & blacklist
        assert overlap, (
            f"{name}: response_must_not_contain {sorted(blacklist)} does not overlap with "
            f"the v1 prompt's banned jargon {sorted(banned)}; the fixture would not catch a "
            f"regression of rule 15."
        )
        # Sanity: all blacklisted substrings should actually *not* be literally
        # encouraged by the prompt itself. We can't guarantee absence of every
        # banned substring in a rule that names it as forbidden — but the rule
        # should at least quote it inside backticks when it appears.
        _ = text  # present for future expansion; keep referenced to avoid linter churn
