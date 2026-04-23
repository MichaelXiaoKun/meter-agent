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

import json
import sys
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
def flow_analysis_mod():
    """The ``tools.flow_analysis`` module (stubs installed before first import)."""
    _install_processors_time_range_stub()
    _ensure_subprocess_env_module()
    import tools.flow_analysis as fa  # noqa: WPS433
    return fa


@pytest.fixture(scope="module")
def analyze_flow_data(flow_analysis_mod):
    return flow_analysis_mod.analyze_flow_data


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


class TestAnalyzeFlowDataTimestampCoercion:
    """
    The LLM / API may pass JSON tool arguments as strings; ``datetime.fromtimestamp`` and
    the subprocess CLI need ints.
    """

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        monkeypatch.delenv("DISPLAY_TZ", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def test_string_start_end_reaches_subprocess_as_int_strings(
        self, analyze_flow_data
    ):
        import tools.flow_analysis as fa  # noqa: WPS433

        def fake_run(cmd, **_kwargs):
            assert cmd[4] == "--start"
            assert cmd[5] == "1700000000"
            assert cmd[6] == "--end"
            assert cmd[7] == "1700003600"
            return _fake_completed()

        with patch.object(fa.subprocess, "run", side_effect=fake_run):
            result = analyze_flow_data(
                serial_number="BB-TEST",
                start="1700000000",  # type: ignore[arg-type]
                end="1700003600",  # type: ignore[arg-type]
                token="dummy-token",
            )
        assert result["success"] is True

    def test_inverted_range_does_not_call_subprocess(self, analyze_flow_data):
        import tools.flow_analysis as fa  # noqa: WPS433

        with patch.object(fa.subprocess, "run") as p:
            result = analyze_flow_data(
                serial_number="BB-TEST",
                start=1_800_000_000,
                end=1_700_000_000,
                token="dummy-token",
            )
        p.assert_not_called()
        assert result["success"] is False
        assert "start" in result.get("error", "")

    def test_bool_timestamp_is_rejected(self, analyze_flow_data):
        import tools.flow_analysis as fa  # noqa: WPS433

        with patch.object(fa.subprocess, "run") as p:
            result = analyze_flow_data(
                serial_number="BB-TEST",
                start=True,  # type: ignore[arg-type]
                end=1_700_003_600,
                token="dummy-token",
            )
        p.assert_not_called()
        assert result["success"] is False
        assert "boolean" in (result.get("error") or "")


# ---------------------------------------------------------------------------
# _coerce_unix_seconds — all input shapes
# ---------------------------------------------------------------------------


class TestCoerceUnixSeconds:
    def test_int_unchanged(self, flow_analysis_mod):
        c = flow_analysis_mod._coerce_unix_seconds
        assert c("start", 1_700_000_000) == 1_700_000_000

    def test_float_truncates_toward_zero(self, flow_analysis_mod):
        c = flow_analysis_mod._coerce_unix_seconds
        assert c("end", 1_700_000_000.9) == 1_700_000_000
        assert c("end", -1.2) == -1

    def test_string_whitespace(self, flow_analysis_mod):
        c = flow_analysis_mod._coerce_unix_seconds
        assert c("start", "  1700000000  ") == 1_700_000_000
        assert c("start", "1.5e3") == 1500

    def test_bool_rejected(self, flow_analysis_mod):
        c = flow_analysis_mod._coerce_unix_seconds
        with pytest.raises(TypeError, match="boolean"):
            c("start", True)
        with pytest.raises(TypeError, match="boolean"):
            c("end", False)

    def test_nonfinite_float_rejected(self, flow_analysis_mod):
        c = flow_analysis_mod._coerce_unix_seconds
        with pytest.raises(ValueError, match="finite"):
            c("start", float("inf"))
        with pytest.raises(ValueError, match="finite"):
            c("start", float("-inf"))

    def test_string_nan_rejected(self, flow_analysis_mod):
        c = flow_analysis_mod._coerce_unix_seconds
        with pytest.raises((ValueError, OverflowError)):
            c("start", "nan")

    def test_empty_string_rejected(self, flow_analysis_mod):
        c = flow_analysis_mod._coerce_unix_seconds
        for bad in ("", "   "):
            with pytest.raises(ValueError, match="empty"):
                c("end", bad)

    def test_invalid_string_rejected(self, flow_analysis_mod):
        c = flow_analysis_mod._coerce_unix_seconds
        with pytest.raises(ValueError):
            c("start", "not-a-timestamp")

    def test_wild_type_rejected(self, flow_analysis_mod):
        c = flow_analysis_mod._coerce_unix_seconds
        with pytest.raises(TypeError, match="list"):
            c("start", [])
        with pytest.raises(TypeError, match="dict"):
            c("end", {})


# ---------------------------------------------------------------------------
# Report truncation & plot path helpers
# ---------------------------------------------------------------------------


class TestMaybeTruncateReport:
    def test_zero_limit_never_truncates(self, flow_analysis_mod, monkeypatch):
        """``BLUEBOT_FLOW_REPORT_MAX_CHARS <= 0`` disables the cap (see _flow_report_max_chars)."""
        monkeypatch.setenv("BLUEBOT_FLOW_REPORT_MAX_CHARS", "0")
        long = "A" * 100_000
        out, t = flow_analysis_mod._maybe_truncate_report(long)
        assert t is False
        assert out == long

    def test_short_text_never_truncates(self, flow_analysis_mod, monkeypatch):
        monkeypatch.setenv("BLUEBOT_FLOW_REPORT_MAX_CHARS", "5000")
        text = "short"
        out, t = flow_analysis_mod._maybe_truncate_report(text)
        assert t is False
        assert out == text

    def test_truncation_inserts_note(self, flow_analysis_mod, monkeypatch):
        monkeypatch.setenv("BLUEBOT_FLOW_REPORT_MAX_CHARS", "20")
        long = "A" * 200
        out, t = flow_analysis_mod._maybe_truncate_report(long)
        assert t is True
        assert "…*" in out or "truncat" in out.lower()


class TestPlotSummaries:
    def test_typed_plot_title(self, flow_analysis_mod):
        # Matches ``{serial}_{start}_{plot_type}`` from data-processing-agent plots.
        r = flow_analysis_mod._plot_summaries(
            ["/tmp/BBX_1700000000_time_series.png"],
            "America/Denver",
        )
        assert len(r) == 1
        assert r[0]["plot_type"] == "time_series"
        assert "Flow rate" in r[0]["title"]
        assert r[0]["plot_timezone"] == "America/Denver"

    def test_non_png_skipped(self, flow_analysis_mod):
        assert flow_analysis_mod._plot_summaries(["/a/b.jpg"], "UTC") == []

    def test_short_stem_uses_generic_title(self, flow_analysis_mod):
        r = flow_analysis_mod._plot_summaries(["/a/x.png"], "UTC")
        assert r[0]["plot_type"] == "unknown"
        assert r[0]["title"] == "Analysis plot"


class TestCollectPlotPathsAndAnalysisJson:
    def test_stderr_plot_paths_wins(self, flow_analysis_mod):
        stderr = flow_analysis_mod._PLOT_PATHS_MARKER + json.dumps(
            ["/abs/plot1.png", "/x/../unsafe.png", "nope.txt"]
        )
        paths = flow_analysis_mod._collect_plot_paths("ignored", stderr, "/agent")
        assert paths == ["/abs/plot1.png"]

    def test_markdown_fallback_with_real_file(self, flow_analysis_mod, tmp_path):
        agent = tmp_path / "agent"
        (agent / "plots").mkdir(parents=True)
        p = agent / "plots" / "chart.png"
        p.write_text("x")
        report = "See ![c](chart.png) here"
        paths = flow_analysis_mod._collect_plot_paths(report, "", str(agent))
        assert len(paths) == 1
        assert paths[0].endswith("chart.png")

    def test_collect_analysis_json_happy(self, flow_analysis_mod):
        line = flow_analysis_mod._ANALYSIS_JSON_MARKER + json.dumps(
            {"path": "/analyses/m.json"}
        )
        assert flow_analysis_mod._collect_analysis_json_path(line) == "/analyses/m.json"

    def test_collect_analysis_json_rejects_path_traversal(self, flow_analysis_mod):
        line = flow_analysis_mod._ANALYSIS_JSON_MARKER + json.dumps(
            {"path": "/a/../b.json"}
        )
        assert flow_analysis_mod._collect_analysis_json_path(line) is None


# ---------------------------------------------------------------------------
# End-to-end success / failure (patched subprocess)
# ---------------------------------------------------------------------------


class TestAnalyzeFlowDataIntegration:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        monkeypatch.delenv("DISPLAY_TZ", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def test_equal_start_end_still_runs(self, flow_analysis_mod):
        t = 1_700_000_000

        def fake_run(cmd, **_kwargs):
            assert cmd[5] == str(t) and cmd[7] == str(t)
            return _fake_completed()

        with patch.object(flow_analysis_mod.subprocess, "run", side_effect=fake_run):
            r = flow_analysis_mod.analyze_flow_data(
                serial_number="S",
                start=t,
                end=t,
                token="tok",
            )
        assert r["success"] is True

    def test_success_includes_plots_and_analysis_path(self, flow_analysis_mod):
        stderr = (
            flow_analysis_mod._ANALYSIS_JSON_MARKER
            + json.dumps({"path": "/tmp/analysis.json"})
            + "\n"
        )
        stderr += flow_analysis_mod._PLOT_PATHS_MARKER + json.dumps(
            ["/tmp/BBX_1700000000_time_series.png"]
        )

        def fake_run(_cmd, **_kwargs):
            return _fake_completed(
                "# Report",
                stderr=stderr,
            )

        with patch.object(flow_analysis_mod.subprocess, "run", side_effect=fake_run):
            r = flow_analysis_mod.analyze_flow_data(
                serial_number="BBX",
                start=1,
                end=2,
                token="tok",
            )
        assert r["success"] is True
        assert r["analysis_json_path"] == "/tmp/analysis.json"
        assert r["plot_paths"] == ["/tmp/BBX_1700000000_time_series.png"]
        assert len(r["plot_summaries"]) == 1
        assert r["display_range"] == "[1,2]@UTC"

    def test_failure_empty_stderr_uses_exit_code_message(
        self, flow_analysis_mod
    ):
        def fake_run(_cmd, **_kwargs):
            return SimpleNamespace(
                returncode=2, stdout="", stderr="   \n  "
            )

        with patch.object(flow_analysis_mod.subprocess, "run", side_effect=fake_run):
            r = flow_analysis_mod.analyze_flow_data(
                serial_number="S",
                start=1_700_000_000,
                end=1_700_000_100,
                token="tok",
            )
        assert r["success"] is False
        assert "code 2" in (r.get("error") or "")

    def test_coercion_error_returns_no_display_range(
        self, flow_analysis_mod
    ):
        with patch.object(
            flow_analysis_mod.subprocess, "run"
        ) as p:
            r = flow_analysis_mod.analyze_flow_data(
                serial_number="S",
                start="nope",  # type: ignore[arg-type]
                end=1,
                token="tok",
            )
        p.assert_not_called()
        assert r["success"] is False
        assert r["display_range"] == ""
        assert r["plot_paths"] == []

    def test_invalid_network_type_not_in_env(
        self, flow_analysis_mod
    ):
        cap: dict = {}

        def fake_run(_cmd, **subprocess_kwargs):
            cap.update(subprocess_kwargs.get("env") or {})
            return _fake_completed()

        with patch.object(flow_analysis_mod.subprocess, "run", side_effect=fake_run):
            flow_analysis_mod.analyze_flow_data(
                serial_number="BB",
                start=1,
                end=2,
                token="t",
                network_type="cellular-5g",
            )
        assert "BLUEBOT_METER_NETWORK_TYPE" not in cap

    def test_network_type_case_insensitive(self, flow_analysis_mod):
        cap: dict = {}

        def fake_run(_cmd, **subprocess_kwargs):
            cap.update(subprocess_kwargs.get("env") or {})
            return _fake_completed()

        with patch.object(flow_analysis_mod.subprocess, "run", side_effect=fake_run):
            flow_analysis_mod.analyze_flow_data(
                serial_number="BB",
                start=1,
                end=2,
                token="t",
                network_type="WIFI",
            )
        assert cap.get("BLUEBOT_METER_NETWORK_TYPE") == "wifi"


class TestAnalyzeFlowInputsErrorPayload:
    def test_missing_start(self, flow_analysis_mod):
        r = flow_analysis_mod.analyze_flow_inputs_error_payload(
            {"serial_number": "BB1", "end": 100},
            display_timezone=None,
        )
        assert r is not None
        assert r["success"] is False
        assert "start" in r["error"].lower()

    def test_complete_returns_none(self, flow_analysis_mod):
        assert (
            flow_analysis_mod.analyze_flow_inputs_error_payload(
                {"serial_number": "BB1", "start": 1, "end": 2},
                display_timezone=None,
            )
            is None
        )

    def test_non_dict(self, flow_analysis_mod):
        r = flow_analysis_mod.analyze_flow_inputs_error_payload(None)
        assert r is not None
        assert r["success"] is False
