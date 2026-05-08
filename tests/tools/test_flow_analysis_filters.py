"""
Wiring tests for local-time ``filters`` on ``analyze_flow_data``.

The data-processing subprocess owns semantic validation and refusal states.
The orchestrator wrapper only canonicalises JSON-ish objects, passes them via
``BLUEBOT_FILTERS_JSON``, and keeps the result cache keyed by that payload.
"""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

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
    if "shared.subprocess_env" in sys.modules:
        return
    mod = ModuleType("shared.subprocess_env")

    def _tool_subprocess_env(token, anthropic_api_key=None):
        env = {"BLUEBOT_TOKEN": token or ""}
        if anthropic_api_key:
            env["ANTHROPIC_API_KEY"] = anthropic_api_key
        return env

    mod.tool_subprocess_env = _tool_subprocess_env
    sys.modules["shared.subprocess_env"] = mod


_install_processors_time_range_stub()
_ensure_subprocess_env_module()
sys.modules.pop("tools.flow_analysis", None)

from tools.flow_analysis import (  # noqa: E402
    TOOL_DEFINITION,
    analyze_flow_data,
    clear_result_cache,
    resolve_filters,
)


class TestResolveFilters:
    def test_missing_or_empty_returns_none(self):
        assert resolve_filters(None, primary_start=1, primary_end=2) is None
        assert resolve_filters({}, primary_start=1, primary_end=2) is None

    def test_non_object_returns_none(self):
        assert resolve_filters("weekdays", primary_start=1, primary_end=2) is None
        assert resolve_filters([("timezone", "UTC")], primary_start=1, primary_end=2) is None

    def test_json_object_passes_through_as_canonical_copy(self):
        spec = {
            "weekdays": [0, 1, 2, 3, 4],
            "timezone": "America/Denver",
            "hour_ranges": [{"end_hour": 17, "start_hour": 8}],
        }
        out = resolve_filters(spec, primary_start=1, primary_end=2)
        assert out == spec
        assert out is not spec

    def test_non_json_object_returns_none(self):
        assert (
            resolve_filters({"timezone": {"UTC"}}, primary_start=1, primary_end=2)
            is None
        )


class TestToolDefinitionSchema:
    def test_filters_is_optional(self):
        assert "filters" not in TOOL_DEFINITION["input_schema"]["required"]

    def test_filters_shape_documents_local_rules(self):
        prop = TOOL_DEFINITION["input_schema"]["properties"]["filters"]
        assert prop["type"] == "object"
        assert {"timezone", "weekdays", "hour_ranges", "exclude_dates", "include_sub_ranges"} <= set(
            prop["properties"]
        )
        hour_range = prop["properties"]["hour_ranges"]["items"]
        assert set(hour_range["required"]) == {"start_hour", "end_hour"}


class TestSubprocessFiltersEnv:
    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        clear_result_cache()
        yield
        clear_result_cache()

    def _captured_run(self, captured: dict):
        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = list(cmd)
            captured["env"] = kwargs.get("env") or {}
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

        return fake_run

    def test_filters_json_lands_in_env(self):
        captured: dict = {}
        filters = {
            "timezone": "America/Denver",
            "weekdays": [0, 1, 2, 3, 4],
            "hour_ranges": [{"start_hour": 8, "end_hour": 17}],
        }
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
                filters=filters,
            )

        assert json.loads(captured["env"]["BLUEBOT_FILTERS_JSON"]) == filters

    def test_malformed_filters_value_is_dropped(self):
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
                filters="business hours",
            )

        assert "cmd" in captured
        assert "BLUEBOT_FILTERS_JSON" not in captured["env"]

    def test_cache_key_distinguishes_filters(self):
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
                filters={"timezone": "America/Denver", "weekdays": [0]},
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
                filters={"timezone": "America/Denver", "weekdays": [1]},
            )

        assert "cmd" in captured1
        assert "cmd" in captured2
        assert captured1["env"]["BLUEBOT_FILTERS_JSON"] != captured2["env"]["BLUEBOT_FILTERS_JSON"]
