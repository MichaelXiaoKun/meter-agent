"""
Wiring tests for threshold ``event_predicates`` on ``analyze_flow_data``.
"""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from tools.plot_tz import validate_iana


def _install_processors_time_range_stub() -> None:
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
    resolve_event_predicates,
)


class TestResolveEventPredicates:
    def test_missing_or_empty_returns_none(self):
        assert resolve_event_predicates(None, primary_start=1, primary_end=2) is None
        assert resolve_event_predicates([], primary_start=1, primary_end=2) is None

    def test_non_list_returns_none(self):
        assert resolve_event_predicates({"predicate": "flow > 10"}, primary_start=1, primary_end=2) is None

    def test_json_list_passes_through_as_canonical_copy(self):
        spec = [{"name": "high", "predicate": "flow > 10", "min_duration_seconds": 60}]
        out = resolve_event_predicates(spec, primary_start=1, primary_end=2)
        assert out == spec
        assert out is not spec

    def test_non_json_value_returns_none(self):
        assert resolve_event_predicates([{"bad": {"set"}}], primary_start=1, primary_end=2) is None


class TestToolDefinitionSchema:
    def test_event_predicates_is_optional(self):
        assert "event_predicates" not in TOOL_DEFINITION["input_schema"]["required"]

    def test_event_predicates_shape_documents_predicate(self):
        prop = TOOL_DEFINITION["input_schema"]["properties"]["event_predicates"]
        assert prop["type"] == "array"
        item = prop["items"]
        assert set(item["required"]) == {"name", "predicate", "min_duration_seconds"}


class TestSubprocessEventPredicateEnv:
    def setup_method(self):
        clear_result_cache()

    def teardown_method(self):
        clear_result_cache()

    def _captured_run(self, captured: dict):
        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env") or {}
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

        return fake_run

    def test_event_predicates_json_lands_in_env(self):
        captured: dict = {}
        specs = [{"name": "high", "predicate": "flow > 10", "min_duration_seconds": 300}]
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
                event_predicates=specs,
            )

        assert json.loads(captured["env"]["BLUEBOT_EVENT_PREDICATES_JSON"]) == specs

    def test_malformed_event_predicates_value_is_dropped(self):
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
                event_predicates={"predicate": "flow > 10"},
            )

        assert "cmd" in captured
        assert "BLUEBOT_EVENT_PREDICATES_JSON" not in captured["env"]

    def test_cache_key_distinguishes_event_predicates(self):
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
                event_predicates=[
                    {"name": "high", "predicate": "flow > 10", "min_duration_seconds": 60}
                ],
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
                event_predicates=[
                    {"name": "low", "predicate": "flow < 1", "min_duration_seconds": 60}
                ],
            )

        assert "cmd" in captured1
        assert "cmd" in captured2
        assert (
            captured1["env"]["BLUEBOT_EVENT_PREDICATES_JSON"]
            != captured2["env"]["BLUEBOT_EVENT_PREDICATES_JSON"]
        )
