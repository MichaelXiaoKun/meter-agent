"""
Unit tests for pure helpers in ``scripts/run_golden_turns.py``.

The end-to-end LLM replay lives off-CI (it needs ``ANTHROPIC_API_KEY`` and
non-determinism means it would flake). But the assertion logic, fixture
loader, and stub installer are pure — regressing them means every fixture
silently starts passing or failing, which we cannot afford. These tests pin
that logic down without spending tokens.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "run_golden_turns.py"


def _load_runner():
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    spec = importlib.util.spec_from_file_location("run_golden_turns", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # ``@dataclass`` needs the module registered in ``sys.modules`` at class
    # definition time so ``dataclasses._is_type`` can look it up by name.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def runner():
    return _load_runner()


# ---------------------------------------------------------------------------
# _is_subset
# ---------------------------------------------------------------------------


def test_is_subset_handles_empty_needle(runner):
    assert runner._is_subset({}, {"serial_number": "BB1"}) is True


def test_is_subset_value_must_match_exactly(runner):
    assert runner._is_subset({"serial_number": "BB1"}, {"serial_number": "BB1", "x": 9}) is True
    assert runner._is_subset({"serial_number": "BB1"}, {"serial_number": "BB2"}) is False


def test_is_subset_missing_key_fails(runner):
    assert runner._is_subset({"email": "a@b"}, {"serial_number": "BB1"}) is False


# ---------------------------------------------------------------------------
# _reconstruct_inputs — positional → keyword mapping per tool
# ---------------------------------------------------------------------------


def test_reconstruct_check_meter_status_pulls_serial(runner):
    inputs = runner._reconstruct_inputs(
        "check_meter_status", args=("BB8100015261", "tok"), kwargs={}
    )
    assert inputs == {"serial_number": "BB8100015261"}


def test_reconstruct_analyze_flow_captures_window_and_optional_kwargs(runner):
    inputs = runner._reconstruct_inputs(
        "analyze_flow_data",
        args=("BB1", 100, 200, "tok"),
        kwargs={"network_type": "wifi", "meter_timezone": "America/Denver"},
    )
    assert inputs == {
        "serial_number": "BB1",
        "start": 100,
        "end": 200,
        "network_type": "wifi",
        "meter_timezone": "America/Denver",
    }


def test_reconstruct_configure_pipe_all_positional(runner):
    inputs = runner._reconstruct_inputs(
        "configure_meter_pipe",
        args=("BB1", "stainless_steel", "asme_schedule_40", "2_inch", 45, "tok"),
        kwargs={},
    )
    assert inputs == {
        "serial_number": "BB1",
        "pipe_material": "stainless_steel",
        "pipe_standard": "asme_schedule_40",
        "pipe_size": "2_inch",
        "transducer_angle": 45,
    }


# ---------------------------------------------------------------------------
# _check_fixture — the heart of the golden-vs-actual comparison
# ---------------------------------------------------------------------------


def _fixture() -> dict:
    return {
        "id": "t",
        "expected_tool_sequence": [
            {"tool": "resolve_time_range", "args_contains": {"description": "last 6 hours"}},
            {"tool": "analyze_flow_data", "args_contains": {"serial_number": "BB1"}},
        ],
        "forbidden_tools": ["configure_meter_pipe"],
        "response_must_not_contain": ["analyze_flow_data", "/Users/"],
        "response_must_contain": ["range"],
    }


def test_check_fixture_happy_path(runner):
    calls = [
        runner.ToolCallRecord("resolve_time_range", {"description": "last 6 hours"}),
        runner.ToolCallRecord("analyze_flow_data", {"serial_number": "BB1", "start": 1, "end": 2}),
    ]
    failures = runner._check_fixture(_fixture(), calls, "Showing range last 6 hours.")
    assert failures == []


def test_check_fixture_out_of_order_tool_fails(runner):
    # Given expected ``[resolve_time_range, analyze_flow_data]`` the cursor-based
    # matcher consumes the swapped-in ``resolve_time_range`` first, then cannot
    # find a *subsequent* ``analyze_flow_data`` — which is the right failure to
    # flag (the LLM would have analysed data *before* resolving the range).
    calls = [
        runner.ToolCallRecord("analyze_flow_data", {"serial_number": "BB1", "start": 1, "end": 2}),
        runner.ToolCallRecord("resolve_time_range", {"description": "last 6 hours"}),
    ]
    failures = runner._check_fixture(_fixture(), calls, "range")
    assert any("analyze_flow_data" in f and "not found" in f for f in failures)


def test_check_fixture_forbidden_tool_flagged(runner):
    calls = [
        runner.ToolCallRecord("resolve_time_range", {"description": "last 6 hours"}),
        runner.ToolCallRecord("analyze_flow_data", {"serial_number": "BB1"}),
        runner.ToolCallRecord("configure_meter_pipe", {"serial_number": "BB1"}),
    ]
    failures = runner._check_fixture(_fixture(), calls, "range")
    assert any("configure_meter_pipe" in f for f in failures)


def test_check_fixture_reply_guardrails(runner):
    calls = [
        runner.ToolCallRecord("resolve_time_range", {"description": "last 6 hours"}),
        runner.ToolCallRecord("analyze_flow_data", {"serial_number": "BB1"}),
    ]
    # Reply leaks tool name → fail; also missing the positive substring.
    failures = runner._check_fixture(_fixture(), calls, "ran analyze_flow_data at /Users/foo")
    assert any("analyze_flow_data" in f for f in failures)
    assert any("/Users/" in f for f in failures)
    assert any("range" in f and "missing" in f for f in failures)


def test_check_fixture_args_subset_mismatch(runner):
    fx = _fixture()
    calls = [
        runner.ToolCallRecord("resolve_time_range", {"description": "yesterday"}),
        runner.ToolCallRecord("analyze_flow_data", {"serial_number": "BB1"}),
    ]
    failures = runner._check_fixture(fx, calls, "range")
    assert any("unexpected args" in f for f in failures)


# ---------------------------------------------------------------------------
# _install_stubs — exercised against a dummy ``agent`` namespace so we don't
# import the heavyweight orchestrator.
# ---------------------------------------------------------------------------


def test_install_stubs_records_and_returns_canned_results(runner):
    # Build a minimal agent-shaped namespace with only the tool attributes
    # the installer cares about.
    dummy = SimpleNamespace(
        resolve_time_range=lambda *a, **k: {"unused": True},
        check_meter_status=lambda *a, **k: {"unused": True},
        get_meter_profile=lambda *a, **k: {"unused": True},
        list_meters_for_account=lambda *a, **k: {"unused": True},
        compare_meters=lambda *a, **k: {"unused": True},
        analyze_flow_data=lambda *a, **k: {"unused": True},
        configure_meter_pipe=lambda *a, **k: {"unused": True},
        set_transducer_angle_only=lambda *a, **k: {"unused": True},
    )
    fixture = {
        "expected_tool_sequence": [
            {
                "tool": "check_meter_status",
                "args_contains": {"serial_number": "BB1"},
                "mock_result": {"success": True, "online": True},
            }
        ]
    }
    calls: list = []
    restore = runner._install_stubs(dummy, fixture, calls)
    try:
        # Stubs must return the canned result on the matching call and a
        # "no canned result" envelope afterwards.
        first = dummy.check_meter_status("BB1", "tok")
        second = dummy.check_meter_status("BB1", "tok")
    finally:
        restore()

    assert first == {"success": True, "online": True}
    assert second == {
        "success": False,
        "error": "golden-replay: tool 'check_meter_status' called but no canned result left",
    }
    assert len(calls) == 2
    assert calls[0].tool == "check_meter_status"
    assert calls[0].inputs == {"serial_number": "BB1"}


def test_install_stubs_restores_originals(runner):
    sentinel_a = lambda *a, **k: "A"
    sentinel_b = lambda *a, **k: "B"
    dummy = SimpleNamespace(
        resolve_time_range=sentinel_a,
        check_meter_status=sentinel_b,
        get_meter_profile=sentinel_a,
        list_meters_for_account=sentinel_a,
        compare_meters=sentinel_a,
        analyze_flow_data=sentinel_a,
        configure_meter_pipe=sentinel_a,
        set_transducer_angle_only=sentinel_a,
    )
    restore = runner._install_stubs(dummy, {"expected_tool_sequence": []}, [])
    assert dummy.resolve_time_range is not sentinel_a
    restore()
    assert dummy.resolve_time_range is sentinel_a
    assert dummy.check_meter_status is sentinel_b
