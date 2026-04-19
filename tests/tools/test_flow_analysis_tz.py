"""
Tests for the orchestrator's plot-timezone resolver
(``tools/plot_tz.py``) and its integration in ``analyze_flow_data``.

The pure helpers — ``validate_iana`` and ``resolve_plot_tz_name`` — live in
their own module so they have no dependencies on the orchestrator's
``processors`` namespace (which would collide with
``data-processing-agent/processors`` on a shared ``sys.path``). That makes
them trivially unit-testable here.

The end-to-end propagation test patches ``subprocess.run`` so we can assert
that ``BLUEBOT_PLOT_TZ`` lands in the env passed to the data-processing-agent
subprocess, without spawning a real process or hitting the data API.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Pure helpers — import directly from the dependency-free module.
# ---------------------------------------------------------------------------

from tools.plot_tz import resolve_plot_tz_name, validate_iana


# ---------------------------------------------------------------------------
# validate_iana
# ---------------------------------------------------------------------------


class TestValidateIana:
    def test_known_zone_returns_input(self):
        assert validate_iana("America/Denver") == "America/Denver"

    def test_explicit_utc_normalises(self):
        assert validate_iana("UTC") == "UTC"
        assert validate_iana("utc") == "UTC"

    def test_unknown_zone_returns_none(self):
        assert validate_iana("Mars/Olympus_Mons") is None

    def test_blank_inputs_return_none(self):
        assert validate_iana("") is None
        assert validate_iana("   ") is None
        assert validate_iana(None) is None


# ---------------------------------------------------------------------------
# resolve_plot_tz_name — precedence chain
# ---------------------------------------------------------------------------


class TestResolvePlotTzName:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        monkeypatch.delenv("DISPLAY_TZ", raising=False)

    def test_meter_tz_wins(self):
        out = resolve_plot_tz_name(
            meter_timezone="America/Denver",
            display_timezone="America/New_York",
        )
        assert out == "America/Denver"

    def test_display_tz_used_when_meter_missing(self):
        out = resolve_plot_tz_name(
            meter_timezone=None,
            display_timezone="America/New_York",
        )
        assert out == "America/New_York"

    def test_invalid_meter_tz_falls_through_to_display(self):
        out = resolve_plot_tz_name(
            meter_timezone="Mars/Olympus_Mons",
            display_timezone="America/New_York",
        )
        assert out == "America/New_York"

    def test_env_used_when_caller_passes_nothing(self, monkeypatch):
        monkeypatch.setenv("BLUEBOT_PLOT_TZ", "America/Denver")
        out = resolve_plot_tz_name(meter_timezone=None, display_timezone=None)
        assert out == "America/Denver"

    def test_display_tz_env_used_as_secondary_fallback(self, monkeypatch):
        monkeypatch.setenv("DISPLAY_TZ", "America/Chicago")
        out = resolve_plot_tz_name(meter_timezone=None, display_timezone=None)
        assert out == "America/Chicago"

    def test_plot_tz_env_beats_display_tz_env(self, monkeypatch):
        monkeypatch.setenv("BLUEBOT_PLOT_TZ", "Europe/London")
        monkeypatch.setenv("DISPLAY_TZ", "America/Chicago")
        out = resolve_plot_tz_name(meter_timezone=None, display_timezone=None)
        assert out == "Europe/London"

    def test_final_fallback_is_utc(self):
        out = resolve_plot_tz_name(meter_timezone=None, display_timezone=None)
        assert out == "UTC"

    def test_explicit_utc_returned_verbatim(self):
        out = resolve_plot_tz_name(meter_timezone="UTC", display_timezone=None)
        assert out == "UTC"


# ---------------------------------------------------------------------------
# analyze_flow_data — env propagation to the subprocess
# ---------------------------------------------------------------------------


def _install_processors_time_range_stub() -> None:
    """
    Stub out ``processors.time_range`` so importing ``tools.flow_analysis``
    doesn't trip over the orchestrator-vs-data-processing-agent ``processors``
    namespace collision in the test sys.path.

    Real runtime imports the orchestrator's own implementation; the helpers
    we stub here are not on the code path we're asserting against (we only
    care about ``BLUEBOT_PLOT_TZ`` ending up in the subprocess env).
    """
    if "processors.time_range" in sys.modules:
        return
    proc = sys.modules.get("processors")
    if proc is None:
        proc = ModuleType("processors")
        proc.__path__ = []  # mark as a package
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
    """``tools/flow_analysis.py`` imports ``subprocess_env``; the real one lives
    next to ``orchestrator/api.py``. Stub a minimal one for tests."""
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


@pytest.fixture(scope="module")
def analyze_flow_data():
    _install_processors_time_range_stub()
    _ensure_subprocess_env_module()
    from tools.flow_analysis import analyze_flow_data as fn  # noqa: WPS433
    return fn


def _fake_completed(stdout: str = "# report\n", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


class TestAnalyzeFlowDataExportsPlotTz:
    """Patch ``subprocess.run`` and assert ``BLUEBOT_PLOT_TZ`` is exported."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        monkeypatch.delenv("DISPLAY_TZ", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def _run(self, analyze_flow_data, **kwargs):
        captured: dict = {}

        def fake_run(*_args, **subprocess_kwargs):
            captured.update(subprocess_kwargs.get("env") or {})
            return _fake_completed()

        import tools.flow_analysis as fa  # noqa: WPS433

        with patch.object(fa.subprocess, "run", side_effect=fake_run):
            result = analyze_flow_data(
                serial_number="BB-TEST",
                start=1_700_000_000,
                end=1_700_003_600,
                token="dummy-token",
                **kwargs,
            )
        return captured, result

    def test_meter_timezone_exported(self, analyze_flow_data):
        env, result = self._run(analyze_flow_data, meter_timezone="America/Denver")
        assert env.get("BLUEBOT_PLOT_TZ") == "America/Denver"
        assert result["plot_timezone"] == "America/Denver"

    def test_display_timezone_used_when_meter_missing(self, analyze_flow_data):
        env, result = self._run(analyze_flow_data, display_timezone="America/New_York")
        assert env.get("BLUEBOT_PLOT_TZ") == "America/New_York"
        assert result["plot_timezone"] == "America/New_York"

    def test_meter_timezone_beats_display_timezone(self, analyze_flow_data):
        env, result = self._run(
            analyze_flow_data,
            meter_timezone="America/Denver",
            display_timezone="America/New_York",
        )
        assert env.get("BLUEBOT_PLOT_TZ") == "America/Denver"
        assert result["plot_timezone"] == "America/Denver"

    def test_invalid_browser_zone_falls_through_to_utc(self, analyze_flow_data):
        env, result = self._run(analyze_flow_data, display_timezone="Mars/Olympus_Mons")
        assert env.get("BLUEBOT_PLOT_TZ") == "UTC"
        assert result["plot_timezone"] == "UTC"

    def test_network_type_still_exported(self, analyze_flow_data):
        env, _ = self._run(
            analyze_flow_data,
            meter_timezone="America/Denver",
            network_type="lorawan",
        )
        assert env.get("BLUEBOT_METER_NETWORK_TYPE") == "lorawan"
        assert env.get("BLUEBOT_PLOT_TZ") == "America/Denver"

    def test_failure_path_still_returns_plot_timezone(self, analyze_flow_data):
        import tools.flow_analysis as fa  # noqa: WPS433

        def fake_run(*_args, **_kwargs):
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")

        with patch.object(fa.subprocess, "run", side_effect=fake_run):
            result = analyze_flow_data(
                serial_number="BB-TEST",
                start=1_700_000_000,
                end=1_700_003_600,
                token="dummy-token",
                meter_timezone="America/Denver",
            )
        assert result["success"] is False
        assert result["plot_timezone"] == "America/Denver"
