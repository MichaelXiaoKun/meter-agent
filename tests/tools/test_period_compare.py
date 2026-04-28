"""
Tests for ``tools.period_compare``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _install_processors_time_range_stub() -> None:
    proc = sys.modules.get("processors")
    if proc is None:
        proc = ModuleType("processors")
        proc.__path__ = []
        sys.modules["processors"] = proc
    tr = ModuleType("processors.time_range")

    def _display_tz_name_for_user(name):
        return name if isinstance(name, str) and name.strip() else None

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


@pytest.fixture
def pc():
    previous_processors = sys.modules.get("processors")
    previous_time_range = sys.modules.get("processors.time_range")
    _install_processors_time_range_stub()
    _ensure_subprocess_env_module()
    sys.modules.pop("tools.flow_analysis", None)
    sys.modules.pop("tools.period_compare", None)
    from tools import period_compare as mod  # noqa: WPS433

    yield mod

    sys.modules.pop("tools.period_compare", None)
    sys.modules.pop("tools.flow_analysis", None)
    if previous_time_range is None:
        sys.modules.pop("processors.time_range", None)
    else:
        sys.modules["processors.time_range"] = previous_time_range
    if previous_processors is None:
        sys.modules.pop("processors", None)
    else:
        sys.modules["processors"] = previous_processors


def _bundle(tmp_path: Path, name: str, facts: dict) -> str:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"verified_facts": facts}), encoding="utf-8")
    return str(path)


def _facts(
    *,
    volume: float,
    mean: float,
    peak_count: int,
    gap_count: int,
    low_quality: int,
    total_quality: int,
) -> dict:
    return {
        "flow_volume": {"total_volume_gallons": volume},
        "flow_rate_descriptive": {"mean": mean},
        "peak_count": peak_count,
        "gap_event_count": gap_count,
        "signal_quality": {
            "flagged_count": low_quality,
            "total_count": total_quality,
        },
    }


def test_compare_periods_calls_both_windows_and_computes_deltas(pc, tmp_path, monkeypatch):
    path_a = _bundle(
        tmp_path,
        "a",
        _facts(
            volume=100.0,
            mean=5.0,
            peak_count=2,
            gap_count=4,
            low_quality=10,
            total_quality=100,
        ),
    )
    path_b = _bundle(
        tmp_path,
        "b",
        _facts(
            volume=150.0,
            mean=8.0,
            peak_count=5,
            gap_count=1,
            low_quality=20,
            total_quality=100,
        ),
    )
    calls: list[tuple] = []

    def fake_analyze(serial, start, end, token, **kwargs):
        calls.append((serial, start, end, token, kwargs))
        return {
            "success": True,
            "display_range": f"{start}-{end}",
            "plot_timezone": kwargs.get("meter_timezone") or "UTC",
            "analysis_json_path": path_a if start == 0 else path_b,
            "report_path": f"/tmp/{start}.md",
            "error": None,
        }

    monkeypatch.setattr(pc, "analyze_flow_data", fake_analyze)

    out = pc.compare_periods(
        "BB1",
        {"start": 0, "end": 7200},
        {"start": 10_000, "end": 13_600},
        "tok",
        network_type="wifi",
        meter_timezone="America/Denver",
    )

    assert out["success"] is True
    assert [(c[1], c[2]) for c in calls] == [(0, 7200), (10_000, 13_600)]
    assert all(c[4]["network_type"] == "wifi" for c in calls)
    assert all(c[4]["meter_timezone"] == "America/Denver" for c in calls)
    assert out["deltas"]["volume_delta_gallons"] == pytest.approx(50.0)
    assert out["deltas"]["volume_delta_pct"] == pytest.approx(50.0)
    assert out["deltas"]["mean_flow_delta"] == pytest.approx(3.0)
    assert out["deltas"]["peak_count_delta"] == 3
    assert out["deltas"]["gap_rate_delta"] == pytest.approx(-1.0)
    assert out["deltas"]["low_quality_ratio_delta"] == pytest.approx(0.1)


def test_compare_periods_returns_failure_when_bundle_missing(pc, monkeypatch):
    def fake_analyze(*_args, **_kwargs):
        return {
            "success": True,
            "display_range": "range",
            "analysis_json_path": None,
            "error": None,
        }

    monkeypatch.setattr(pc, "analyze_flow_data", fake_analyze)

    out = pc.compare_periods(
        "BB1",
        {"start": 0, "end": 1},
        {"start": 2, "end": 3},
        "tok",
    )

    assert out["success"] is False
    assert out["deltas"] is None
    assert out["periods"]["period_a"]["error"] == "analysis_json_path missing from flow analysis result"


def test_compare_periods_validates_input_before_analyzing(pc, monkeypatch):
    called = False

    def fake_analyze(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"success": True}

    monkeypatch.setattr(pc, "analyze_flow_data", fake_analyze)

    out = pc.compare_periods(
        "BB1",
        {"start": 10, "end": 1},
        {"start": 2, "end": 3},
        "tok",
    )

    assert out["success"] is False
    assert "period_a.start" in out["error"]
    assert called is False
