"""
Wiring tests for the ``baseline_window`` input on ``analyze_flow_data``.

These tests pin three contracts:

1. :func:`tools.flow_analysis.resolve_baseline_window` — semantic keys
   (``auto`` / ``trailing_7_days`` / ``trailing_28_days`` / ``prior_week``)
   resolve to concrete Unix bounds anchored to the primary window's start.
   Explicit ``{"start", "end"}`` objects pass through after coercion.

2. The TOOL_DEFINITION input schema accepts ``baseline_window`` as either a
   semantic-key string OR a ``{"start", "end"}`` object — the orchestrator
   system prompt's rule 16 depends on the model being allowed to send
   either shape.

3. End-to-end: when the orchestrator passes ``baseline_window``, the
   subprocess command line includes ``--baseline-start`` / ``--baseline-end``
   with the resolved integers. We patch ``subprocess.run`` so no real
   process is spawned and no flow data is fetched.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from tools.plot_tz import validate_iana


def _install_processors_time_range_stub() -> None:
    """Mirror of the helper in ``test_flow_analysis_tz.py``: stub
    ``processors.time_range`` so importing ``tools.flow_analysis`` survives
    the orchestrator-vs-data-processing-agent ``processors`` namespace
    collision on the shared test sys.path."""
    proc = sys.modules.get("processors")
    if proc is None:
        proc = ModuleType("processors")
        proc.__path__ = []
        sys.modules["processors"] = proc
    tr = ModuleType("processors.time_range")

    def _display_tz_name_for_user(name):
        return validate_iana(name)

    def _format_unix_range_display(start, end, tz_name=None):
        return f"[{start},{end}]@{tz_name or 'UTC'}"

    tr.display_tz_name_for_user = _display_tz_name_for_user
    tr.format_unix_range_display = _format_unix_range_display
    sys.modules["processors.time_range"] = tr


def _ensure_subprocess_env_module() -> None:
    if "subprocess_env" in sys.modules:
        return
    mod = ModuleType("subprocess_env")

    def _tool_subprocess_env(token, anthropic_api_key=None):
        env = {"BLUEBOT_TOKEN": token or ""}
        if anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = anthropic_api_key
        return env

    mod.tool_subprocess_env = _tool_subprocess_env
    sys.modules["subprocess_env"] = mod


_install_processors_time_range_stub()
_ensure_subprocess_env_module()
sys.modules.pop("tools.flow_analysis", None)

from tools.flow_analysis import (  # noqa: E402
    TOOL_DEFINITION,
    analyze_flow_data,
    clear_result_cache,
    resolve_baseline_window,
)


# ---------------------------------------------------------------------------
# resolve_baseline_window — pure function
# ---------------------------------------------------------------------------


class TestResolveBaselineWindow:
    def test_none_returns_none(self):
        assert (
            resolve_baseline_window(None, primary_start=1_700_000_000, primary_end=1_700_003_600)
            is None
        )

    def test_unknown_string_returns_none(self):
        assert (
            resolve_baseline_window(
                "fortnight", primary_start=1_700_000_000, primary_end=1_700_003_600
            )
            is None
        )

    def test_auto_resolves_to_trailing_28_days(self):
        primary_start = 1_700_000_000
        bounds = resolve_baseline_window(
            "auto", primary_start=primary_start, primary_end=primary_start + 3600
        )
        assert bounds is not None
        bs, be = bounds
        assert be == primary_start - 1
        assert bs == primary_start - 28 * 86400

    def test_trailing_7_days_resolves(self):
        primary_start = 1_700_000_000
        bs, be = resolve_baseline_window(
            "trailing_7_days",
            primary_start=primary_start,
            primary_end=primary_start + 3600,
        )
        assert be == primary_start - 1
        assert bs == primary_start - 7 * 86400

    def test_prior_week_is_alias_for_trailing_7_days(self):
        primary_start = 1_700_000_000
        a = resolve_baseline_window(
            "trailing_7_days",
            primary_start=primary_start,
            primary_end=primary_start + 3600,
        )
        b = resolve_baseline_window(
            "prior_week",
            primary_start=primary_start,
            primary_end=primary_start + 3600,
        )
        assert a == b

    def test_explicit_object_passes_through(self):
        bounds = resolve_baseline_window(
            {"start": 1_690_000_000, "end": 1_695_000_000},
            primary_start=1_700_000_000,
            primary_end=1_700_003_600,
        )
        assert bounds == (1_690_000_000, 1_695_000_000)

    def test_explicit_object_with_inverted_bounds_returns_none(self):
        bounds = resolve_baseline_window(
            {"start": 1_695_000_000, "end": 1_690_000_000},
            primary_start=1_700_000_000,
            primary_end=1_700_003_600,
        )
        assert bounds is None

    def test_string_key_is_case_insensitive(self):
        primary_start = 1_700_000_000
        bs, be = resolve_baseline_window(
            "TRAILING_28_DAYS",
            primary_start=primary_start,
            primary_end=primary_start + 3600,
        )
        assert bs == primary_start - 28 * 86400
        assert be == primary_start - 1


# ---------------------------------------------------------------------------
# TOOL_DEFINITION — schema shape
# ---------------------------------------------------------------------------


class TestToolDefinitionSchema:
    def test_baseline_window_is_optional(self):
        assert "baseline_window" not in TOOL_DEFINITION["input_schema"]["required"]

    def test_baseline_window_accepts_semantic_or_object(self):
        prop = TOOL_DEFINITION["input_schema"]["properties"]["baseline_window"]
        # oneOf carries the two allowed shapes.
        assert "oneOf" in prop
        shapes = prop["oneOf"]
        assert len(shapes) == 2
        kinds = {shape.get("type") for shape in shapes}
        assert kinds == {"string", "object"}

    def test_semantic_enum_lists_all_supported_keys(self):
        prop = TOOL_DEFINITION["input_schema"]["properties"]["baseline_window"]
        string_shape = next(
            shape for shape in prop["oneOf"] if shape.get("type") == "string"
        )
        assert set(string_shape["enum"]) == {
            "auto",
            "trailing_7_days",
            "trailing_28_days",
            "prior_week",
        }

    def test_object_shape_requires_start_and_end(self):
        prop = TOOL_DEFINITION["input_schema"]["properties"]["baseline_window"]
        object_shape = next(
            shape for shape in prop["oneOf"] if shape.get("type") == "object"
        )
        assert set(object_shape["required"]) == {"start", "end"}


# ---------------------------------------------------------------------------
# End-to-end: subprocess CLI receives the resolved bounds
# ---------------------------------------------------------------------------


class TestSubprocessCommandLine:
    """Patch ``subprocess.run`` so we can assert the command-line args
    without spawning a real process."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        clear_result_cache()
        yield
        clear_result_cache()

    def _captured_run(self, captured: dict):
        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env") or {}
            return SimpleNamespace(
                returncode=0,
                stdout="ok\n",
                stderr="",
            )

        return fake_run

    def test_no_baseline_window_omits_cli_args(self):
        captured: dict = {}
        with patch(
            "tools.flow_analysis.subprocess.run",
            side_effect=self._captured_run(captured),
        ):
            analyze_flow_data(
                "BB8100015261",
                1_700_000_000,
                1_700_003_600,
                token="dummy",
                meter_timezone="America/Denver",
            )
        cmd = captured["cmd"]
        assert "--baseline-start" not in cmd
        assert "--baseline-end" not in cmd

    def test_auto_keyword_passes_resolved_bounds(self):
        captured: dict = {}
        primary_start = 1_700_000_000
        with patch(
            "tools.flow_analysis.subprocess.run",
            side_effect=self._captured_run(captured),
        ):
            analyze_flow_data(
                "BB8100015261",
                primary_start,
                primary_start + 3600,
                token="dummy",
                meter_timezone="America/Denver",
                baseline_window="auto",
            )
        cmd = captured["cmd"]
        assert "--baseline-start" in cmd
        assert "--baseline-end" in cmd
        bs_idx = cmd.index("--baseline-start")
        be_idx = cmd.index("--baseline-end")
        assert int(cmd[bs_idx + 1]) == primary_start - 28 * 86400
        assert int(cmd[be_idx + 1]) == primary_start - 1

    def test_explicit_object_passes_through_to_cli(self):
        captured: dict = {}
        with patch(
            "tools.flow_analysis.subprocess.run",
            side_effect=self._captured_run(captured),
        ):
            analyze_flow_data(
                "BB8100015261",
                1_700_000_000,
                1_700_003_600,
                token="dummy",
                meter_timezone="America/Denver",
                baseline_window={"start": 1_690_000_000, "end": 1_695_000_000},
            )
        cmd = captured["cmd"]
        bs_idx = cmd.index("--baseline-start")
        be_idx = cmd.index("--baseline-end")
        assert int(cmd[bs_idx + 1]) == 1_690_000_000
        assert int(cmd[be_idx + 1]) == 1_695_000_000

    def test_invalid_keyword_silently_drops_baseline(self):
        # Defensive: a malformed value should not break the call, just skip
        # the baseline. The subprocess should still run with primary bounds.
        captured: dict = {}
        with patch(
            "tools.flow_analysis.subprocess.run",
            side_effect=self._captured_run(captured),
        ):
            analyze_flow_data(
                "BB8100015261",
                1_700_000_000,
                1_700_003_600,
                token="dummy",
                meter_timezone="America/Denver",
                baseline_window="not_a_real_key",
            )
        cmd = captured["cmd"]
        assert "--baseline-start" not in cmd
        assert "--baseline-end" not in cmd

    def test_cache_key_distinguishes_baseline_window(self):
        """A second call with a different baseline_window must NOT hit the
        cache from the first call — otherwise switching reference periods
        would silently return stale comparisons."""
        captured1: dict = {}
        captured2: dict = {}

        with patch(
            "tools.flow_analysis.subprocess.run",
            side_effect=self._captured_run(captured1),
        ):
            analyze_flow_data(
                "BB8100015261",
                1_700_000_000,
                1_700_003_600,
                token="dummy",
                meter_timezone="America/Denver",
                baseline_window="trailing_7_days",
            )

        with patch(
            "tools.flow_analysis.subprocess.run",
            side_effect=self._captured_run(captured2),
        ):
            analyze_flow_data(
                "BB8100015261",
                1_700_000_000,
                1_700_003_600,
                token="dummy",
                meter_timezone="America/Denver",
                baseline_window="trailing_28_days",
            )

        # Both calls must have hit subprocess.run (no cache reuse).
        assert "cmd" in captured1
        assert "cmd" in captured2
        # And the resolved baseline bounds in the two cmds differ.
        bs1 = int(captured1["cmd"][captured1["cmd"].index("--baseline-start") + 1])
        bs2 = int(captured2["cmd"][captured2["cmd"].index("--baseline-start") + 1])
        assert bs1 != bs2
