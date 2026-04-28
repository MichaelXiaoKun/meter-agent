#!/usr/bin/env python3
"""
Manual golden-turn replay — run every fixture in ``tests/fixtures/golden_turns``
through the real LLM and check that the orchestrator's tool-call sequence and
final reply still match what the fixture expects.

Why this is not a pytest test:
  * It hits the Anthropic API, so it needs ``ANTHROPIC_API_KEY`` and costs $.
  * Results are non-deterministic at the single-run level; we accept that and
    treat the script as a pre-merge checklist item, not a CI gate.

What it does:
  1. Load every fixture JSON (or the subset matching ``--fixture``).
  2. For each fixture:
     a. Monkey-patch every tool function on the orchestrator's ``agent`` module
        to a stub that returns the fixture's ``mock_result`` for the matching
        step without touching the network.
     b. Call ``run_turn`` with the fixture's user message and the real LLM.
     c. Compare user-visible ``tool_call`` events vs ``expected_tool_sequence``
        (order, ``args_contains``), check ``forbidden_tools`` was never touched,
        and verify ``response_must_not_contain`` / ``response_must_contain`` on
        the assistant reply. This intentionally ignores internal helper reads
        used to prepare confirmation cards.
  3. Print a pretty PASS/FAIL table and exit non-zero on any failure.

Usage::

    # Default: all fixtures, prompt v1, claude-haiku-4-5.
    python scripts/run_golden_turns.py

    # Single fixture, a different prompt version.
    python scripts/run_golden_turns.py --fixture single_meter_status \\
        --prompt-version v1

    # Override the model.
    BLUEBOT_MODEL=claude-sonnet-4-5 python scripts/run_golden_turns.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORCH_DIR = ROOT / "orchestrator"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "golden_turns"


# ---------------------------------------------------------------------------
# Orchestrator loader — mirrors the pattern used by
# ``tests/orchestrator/test_run_turn_observability.py``.
# ---------------------------------------------------------------------------


def _load_agent(prompt_version: str) -> ModuleType:
    orch = str(ORCH_DIR)
    if sys.path[:1] != [orch]:
        try:
            sys.path.remove(orch)
        except ValueError:
            pass
        sys.path.insert(0, orch)
    for cached in ("processors", "processors.time_range", "agent"):
        sys.modules.pop(cached, None)
    os.environ["ORCHESTRATOR_PROMPT_VERSION"] = prompt_version
    agent_path = ORCH_DIR / "agent.py"
    spec = importlib.util.spec_from_file_location("meter_orchestrator_agent_golden_runner", agent_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if mod._SYSTEM_PROMPT_VERSION != prompt_version:  # pragma: no cover — paranoia
        raise RuntimeError(
            f"loader returned prompt version {mod._SYSTEM_PROMPT_VERSION!r}, expected {prompt_version!r}"
        )
    return mod


# ---------------------------------------------------------------------------
# Fixture + runner data model
# ---------------------------------------------------------------------------


@dataclass
class ToolCallRecord:
    tool: str
    inputs: dict[str, Any]


@dataclass
class FixtureResult:
    fixture: str
    prompt_version: str
    passed: bool
    reply: str = ""
    calls: list[ToolCallRecord] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# Tool stubbing
# ---------------------------------------------------------------------------


# Map from orchestrator tool name → attribute path on the agent module. These
# are the names the ``_dispatch`` function uses at call time; patching them
# intercepts every real network / subprocess invocation.
_TOOL_ATTRS: dict[str, str] = {
    "resolve_time_range": "resolve_time_range",
    "check_meter_status": "check_meter_status",
    "get_meter_profile": "get_meter_profile",
    "list_meters_for_account": "list_meters_for_account",
    "compare_meters": "compare_meters",
    "rank_fleet_by_health": "rank_fleet_by_health",
    "triage_fleet_for_account": "triage_fleet_for_account",
    "compare_periods": "compare_periods",
    "analyze_flow_data": "analyze_flow_data",
    "batch_analyze_flow": "batch_analyze_flow",
    "configure_meter_pipe": "configure_meter_pipe",
    "set_transducer_angle_only": "set_transducer_angle_only",
}


def _install_stubs(agent: ModuleType, fixture: dict[str, Any], calls: list[ToolCallRecord]):
    """Replace every tool function on ``agent`` with a recorder + canned-reply stub."""
    sequence = list(fixture["expected_tool_sequence"])
    # index per tool name → which canned result to hand back next
    next_idx: dict[str, int] = {t: 0 for t in _TOOL_ATTRS}
    originals: dict[str, Any] = {}

    def _make_stub(tool_name: str):
        def _stub(*args, **kwargs):
            inputs = _reconstruct_inputs(tool_name, args, kwargs)
            calls.append(ToolCallRecord(tool=tool_name, inputs=dict(inputs)))
            # Try to find the next expected step for this tool that we haven't
            # consumed yet. Fall back to a generic success envelope so the
            # conversation can still progress.
            idx = next_idx[tool_name]
            for i in range(idx, len(sequence)):
                if sequence[i]["tool"] == tool_name:
                    next_idx[tool_name] = i + 1
                    return dict(sequence[i].get("mock_result") or {"success": True})
            return {
                "success": False,
                "error": f"golden-replay: tool {tool_name!r} called but no canned result left",
            }

        return _stub

    for name, attr in _TOOL_ATTRS.items():
        if hasattr(agent, attr):
            originals[attr] = getattr(agent, attr)
            setattr(agent, attr, _make_stub(name))

    def _restore():
        for attr, fn in originals.items():
            setattr(agent, attr, fn)

    return _restore


def _reconstruct_inputs(tool: str, args: tuple, kwargs: dict) -> dict[str, Any]:
    """Best-effort: reconstruct a dict of inputs from ``args`` + ``kwargs``.

    ``_dispatch`` passes tool arguments positionally in a known order; we map
    them back so the recorded ``inputs`` reads like the JSON body the LLM
    originally sent. If a future tool changes its positional signature this
    mapping will need to grow, but it's cheaper than JSON-re-encoding the
    LLM's ``tool_use`` block.
    """
    positional_schema: dict[str, list[str]] = {
        "resolve_time_range": ["description"],
        "check_meter_status": ["serial_number"],
        "get_meter_profile": ["serial_number"],
        "list_meters_for_account": ["email"],
        "compare_meters": ["serial_numbers"],
        "rank_fleet_by_health": ["serial_numbers"],
        "triage_fleet_for_account": ["email"],
        "compare_periods": ["serial_number", "period_a", "period_b"],
        "analyze_flow_data": ["serial_number", "start", "end"],
        "batch_analyze_flow": ["serial_numbers", "start", "end"],
        "configure_meter_pipe": [
            "serial_number",
            "pipe_material",
            "pipe_standard",
            "pipe_size",
            "transducer_angle",
        ],
        "set_transducer_angle_only": ["serial_number", "transducer_angle"],
    }
    names = positional_schema.get(tool, [])
    inputs: dict[str, Any] = {}
    # Positional args — first ``len(names)`` map to named inputs; the rest are
    # orchestrator-supplied (token, etc.) and we drop them.
    for i, value in enumerate(args[: len(names)]):
        inputs[names[i]] = value
    # Keyword args are already named; keep the ones we care about.
    for key in (
        "network_type",
        "meter_timezone",
        "limit",
        "display_timezone",
        "client_timezone",
        "analysis_mode",
        "baseline_window",
        "filters",
        "event_predicates",
    ):
        if key in kwargs:
            inputs[key] = kwargs[key]
    return inputs


# ---------------------------------------------------------------------------
# Assertion logic
# ---------------------------------------------------------------------------


_LOOSE_CONFIG_FIELDS = {
    "pipe_material",
    "pipe_standard",
    "pipe_size",
    "transducer_angle",
}


def _config_token(value: object) -> str:
    s = str(value if value is not None else "").strip().lower()
    s = s.replace("º", "°")
    s = s.replace('"', " inch ")
    s = re.sub(r"\bdegrees?\b|°", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def _numeric_token(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", str(value or ""))
    if match is None:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _arg_value_matches(key: str, expected: Any, actual: Any) -> bool:
    if actual == expected:
        return True
    if key == "transducer_angle":
        e_num = _numeric_token(expected)
        a_num = _numeric_token(actual)
        if e_num is not None and a_num is not None:
            return abs(e_num - a_num) < 1e-9
    if key in _LOOSE_CONFIG_FIELDS:
        return _config_token(expected) == _config_token(actual)
    return False


def _is_subset(needle: dict[str, Any], hay: dict[str, Any]) -> bool:
    for k, v in needle.items():
        if k not in hay:
            return False
        if not _arg_value_matches(k, v, hay[k]):
            return False
    return True


def _check_fixture(
    fixture: dict[str, Any], calls: list[ToolCallRecord], reply: str
) -> list[str]:
    failures: list[str] = []

    # 1. Ordered subset match against expected_tool_sequence.
    expected = fixture["expected_tool_sequence"]
    cursor = 0
    for step in expected:
        found = False
        while cursor < len(calls):
            cand = calls[cursor]
            cursor += 1
            if cand.tool != step["tool"]:
                continue
            if not _is_subset(step.get("args_contains") or {}, cand.inputs):
                failures.append(
                    f"tool {cand.tool!r} called with unexpected args; "
                    f"expected superset of {step.get('args_contains')}, got {cand.inputs}"
                )
            found = True
            break
        if not found:
            failures.append(
                f"expected tool {step['tool']!r} (args⊇ {step.get('args_contains')}) not found in call sequence"
            )
            break  # stop at first missing step — downstream assertions would cascade

    # 2. Forbidden tools must not appear.
    forbidden = set(fixture.get("forbidden_tools") or [])
    for call in calls:
        if call.tool in forbidden:
            failures.append(f"forbidden tool {call.tool!r} was invoked with inputs {call.inputs}")

    # 3. Reply substring guardrails.
    for bad in fixture.get("response_must_not_contain") or []:
        if bad in reply:
            failures.append(f"reply leaked forbidden substring {bad!r}")
    for good in fixture.get("response_must_contain") or []:
        if good not in reply:
            failures.append(f"reply missing required substring {good!r}")

    return failures


# ---------------------------------------------------------------------------
# Runner main
# ---------------------------------------------------------------------------


def _load_fixtures(selector: str | None) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        if selector and path.stem != selector:
            continue
        with path.open("r", encoding="utf-8") as fh:
            out.append((path.name, json.load(fh)))
    if not out:
        raise SystemExit(
            f"no fixtures matched selector={selector!r} under {FIXTURE_DIR}"
        )
    return out


def _run_one(agent: ModuleType, fixture: dict[str, Any], prompt_version: str) -> FixtureResult:
    function_calls: list[ToolCallRecord] = []
    event_calls: list[ToolCallRecord] = []
    restore = _install_stubs(agent, fixture, function_calls)
    t0 = time.perf_counter()
    try:
        messages: list = [{"role": "user", "content": fixture["user"]}]

        def _record_event(event: dict) -> None:
            if event.get("type") == "tool_call":
                tool = event.get("tool")
                inp = event.get("input")
                if isinstance(tool, str) and isinstance(inp, dict):
                    event_calls.append(ToolCallRecord(tool=tool, inputs=dict(inp)))

        reply, _replaced = agent.run_turn(
            messages,
            token="golden-replay-token",  # stubs never hit the network
            model=agent._MODEL,
            on_event=_record_event,
        )
    except Exception as exc:  # pragma: no cover — surface as a failure row
        restore()
        return FixtureResult(
            fixture=fixture["id"],
            prompt_version=prompt_version,
            passed=False,
            reply="",
            calls=event_calls or function_calls,
            failures=[f"{type(exc).__name__}: {exc}"],
            elapsed_s=time.perf_counter() - t0,
        )
    restore()
    calls = event_calls or function_calls
    failures = _check_fixture(fixture, calls, reply or "")
    return FixtureResult(
        fixture=fixture["id"],
        prompt_version=prompt_version,
        passed=not failures,
        reply=reply or "",
        calls=calls,
        failures=failures,
        elapsed_s=time.perf_counter() - t0,
    )


def _print_table(results: list[FixtureResult], show_detail: bool) -> None:
    width = max((len(r.fixture) for r in results), default=20)
    header = f"{'fixture':<{width}}  prompt   status   elapsed   calls  notes"
    print(header)
    print("-" * len(header))
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        notes = "" if r.passed else r.failures[0]
        print(
            f"{r.fixture:<{width}}  {r.prompt_version:<7}  {status:<7}  "
            f"{r.elapsed_s:6.2f}s  {len(r.calls):>5}  {notes}"
        )
    if show_detail:
        for r in results:
            if r.passed:
                continue
            print()
            print(f"=== {r.fixture} ({r.prompt_version}) ===")
            for i, c in enumerate(r.calls):
                print(f"  call[{i}] {c.tool} {c.inputs}")
            for f in r.failures:
                print(f"  fail: {f}")
            print(f"  reply: {r.reply[:400]}{'…' if len(r.reply) > 400 else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--fixture",
        default=None,
        help="Restrict to one fixture by id (filename without .json).",
    )
    ap.add_argument(
        "--prompt-version",
        default=os.environ.get("ORCHESTRATOR_PROMPT_VERSION", "v1"),
        help="Prompt version to run against (default: v1 or $ORCHESTRATOR_PROMPT_VERSION).",
    )
    ap.add_argument(
        "--show-detail",
        action="store_true",
        help="Print full call sequences and reply snippets for failing fixtures.",
    )
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "error: ANTHROPIC_API_KEY is not set — this runner must hit the real LLM.",
            file=sys.stderr,
        )
        return 2

    fixtures = _load_fixtures(args.fixture)
    fixtures = [
        (name, data)
        for name, data in fixtures
        if args.prompt_version in (data.get("prompt_versions") or [])
    ]
    if not fixtures:
        print(
            f"no fixtures opt into prompt_version={args.prompt_version!r}; nothing to run."
        )
        return 0

    agent = _load_agent(args.prompt_version)

    results: list[FixtureResult] = []
    for name, data in fixtures:
        print(f"[{data['id']}] running…", file=sys.stderr, flush=True)
        results.append(_run_one(agent, data, args.prompt_version))

    _print_table(results, args.show_detail)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print()
    print(f"{passed}/{total} fixtures passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
