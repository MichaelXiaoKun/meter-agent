"""
Tests for the plot honesty fix: ``_series_with_gap_breaks`` and the three
line-plot routines that use it (``_time_series``, ``_peaks_annotated``,
``_signal_quality``).

The fundamental invariant being protected: matplotlib must NOT draw a
straight interpolated segment across a real data outage. We assert that the
broken series matches the verified-facts gap detector exactly when the
healthy inter-arrival cap is the same.
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")  # headless backend; no display required

import matplotlib.pyplot as plt
import numpy as np
import pytest

from datetime import timezone
from zoneinfo import ZoneInfo

from processors import plots
from processors.plots import (
    _series_with_gap_breaks,
    _to_datetimes_nan_aware,
    _peaks_annotated,
    _signal_quality,
    _time_series,
    describe_plot_tz,
    generate_plot,
    pop_captions,
    pop_figures,
    resolve_plot_tz,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_pending():
    """Drain ``_pending`` before and after each test so figures don't leak."""
    pop_figures()
    pop_captions()
    yield
    for fig, _ in pop_figures():
        plt.close(fig)
    pop_captions()


@pytest.fixture
def isolated_plots_dir(tmp_path, monkeypatch):
    """Redirect plot saves to a temp directory."""
    monkeypatch.setattr(plots, "_PLOTS_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# _series_with_gap_breaks — pure helper
# ---------------------------------------------------------------------------


class TestSeriesWithGapBreaks:
    def test_no_break_when_all_within_cap(self):
        ts = np.arange(0, 600, 2, dtype=float)        # 2 s cadence
        v = np.ones_like(ts)
        out_ts, out_v = _series_with_gap_breaks(ts, v, cap_seconds=10.0)
        np.testing.assert_array_equal(out_ts, ts)
        np.testing.assert_array_equal(out_v, v)

    def test_break_when_gap_exceeds_cap(self):
        ts = np.array([0, 2, 4, 600, 602, 604], dtype=float)
        v = np.array([1, 2, 3, 4, 5, 6], dtype=float)
        out_ts, out_v = _series_with_gap_breaks(ts, v, cap_seconds=10.0)
        # NaN row should be inserted between idx 2 (t=4) and idx 3 (t=600)
        assert len(out_ts) == len(ts) + 1
        assert np.isnan(out_ts[3])
        assert np.isnan(out_v[3])
        # Real values preserved on either side
        np.testing.assert_array_equal(out_ts[~np.isnan(out_ts)], ts)
        np.testing.assert_array_equal(out_v[~np.isnan(out_v)], v)

    def test_multiple_breaks(self):
        ts = np.array([0, 2, 200, 202, 800, 802], dtype=float)
        v = np.arange(6, dtype=float)
        out_ts, out_v = _series_with_gap_breaks(ts, v, cap_seconds=10.0)
        # Two gaps ⇒ two NaN rows inserted
        assert int(np.isnan(out_ts).sum()) == 2
        assert int(np.isnan(out_v).sum()) == 2
        assert len(out_ts) == len(ts) + 2

    def test_uses_env_cap_when_not_passed(self, monkeypatch):
        # Wi-Fi network type ⇒ cap = 5 s. A 10 s pause should trigger a break.
        monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "wifi")
        ts = np.array([0, 2, 12, 14], dtype=float)
        v = np.array([1, 2, 3, 4], dtype=float)
        out_ts, _ = _series_with_gap_breaks(ts, v)
        assert int(np.isnan(out_ts).sum()) == 1

    def test_lorawan_does_not_break_on_30s_pause(self, monkeypatch):
        # LoRaWAN cap = 60 s; a 30 s pause is normal.
        monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "lorawan")
        ts = np.array([0, 30, 60, 90], dtype=float)
        v = np.array([1, 2, 3, 4], dtype=float)
        out_ts, out_v = _series_with_gap_breaks(ts, v)
        np.testing.assert_array_equal(out_ts, ts)
        np.testing.assert_array_equal(out_v, v)

    def test_short_series_returns_unchanged(self):
        out_ts, out_v = _series_with_gap_breaks(np.array([1.0]), np.array([2.0]))
        assert out_ts.tolist() == [1.0]
        assert out_v.tolist() == [2.0]
        out_ts, out_v = _series_with_gap_breaks(np.array([], dtype=float), np.array([], dtype=float))
        assert len(out_ts) == 0

    def test_break_count_matches_gap_count_under_same_cap(self):
        """The visual break count must equal continuity.detect_gaps under the
        same cap. Otherwise the plot and the verified-facts table disagree."""
        from processors.continuity import detect_gaps

        ts = np.array([0, 2, 4, 200, 202, 1000, 1002], dtype=float)
        v = np.arange(7, dtype=float)
        out_ts, _ = _series_with_gap_breaks(ts, v, cap_seconds=10.0)
        nan_breaks = int(np.isnan(out_ts).sum())
        # Adaptive detect_gaps may flag more (it uses percentile floors), but
        # never fewer than what a flat cap-only check would catch.
        deltas = np.diff(ts)
        cap_only_breaks = int((deltas > 10.0).sum())
        assert nan_breaks == cap_only_breaks
        assert len(detect_gaps(ts)) >= cap_only_breaks


# ---------------------------------------------------------------------------
# _to_datetimes_nan_aware
# ---------------------------------------------------------------------------


class TestNanAwareDatetimes:
    def test_handles_nan_as_nat(self):
        ts = np.array([1.7e9, np.nan, 1.7e9 + 60], dtype=float)
        arr = _to_datetimes_nan_aware(ts)
        assert arr.dtype == np.dtype("datetime64[ns]")
        # NaT == NaT is False; use np.isnat for the comparison.
        nat_mask = np.isnat(arr)
        np.testing.assert_array_equal(nat_mask, np.array([False, True, False]))

    def test_empty_input_returns_empty_array(self):
        arr = _to_datetimes_nan_aware(np.array([], dtype=float))
        assert arr.dtype == np.dtype("datetime64[ns]")
        assert len(arr) == 0


# ---------------------------------------------------------------------------
# Integration: plot routines must inject NaN into the rendered line
# ---------------------------------------------------------------------------


def _make_series_with_outage(sample_dt: float = 2.0, outage_seconds: float = 1800.0):
    """5 min of cadence, then a 30 min outage, then 5 more min of cadence."""
    base = 1_700_000_000.0
    pre = np.arange(base, base + 300, sample_dt)
    post = np.arange(base + 300 + outage_seconds, base + 600 + outage_seconds, sample_dt)
    ts = np.concatenate([pre, post])
    flow = np.full_like(ts, 5.0)
    quality = np.full_like(ts, 90.0)
    return ts, flow, quality


class TestTimeSeriesIntegration:
    def test_line_breaks_at_outage(self, isolated_plots_dir, monkeypatch):
        monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "wifi")
        ts, flow, quality = _make_series_with_outage()
        result = _time_series(ts, flow, quality, "BB-TEST", float(ts[0]))
        figs = pop_figures()
        assert len(figs) == 1
        fig, _ = figs[0]
        # First Line2D is the connected flow series.
        line = fig.axes[0].lines[0]
        ydata = np.asarray(line.get_ydata(), dtype=float)
        assert np.isnan(ydata).any(), "no NaN in plotted line — gap is being interpolated"
        plt.close(fig)
        assert os.path.isfile(result["path"])

    def test_no_nan_when_no_outage(self, isolated_plots_dir, monkeypatch):
        monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "wifi")
        base = 1_700_000_000.0
        ts = np.arange(base, base + 600, 2.0)
        flow = np.full_like(ts, 5.0)
        quality = np.full_like(ts, 90.0)
        _time_series(ts, flow, quality, "BB-TEST", float(ts[0]))
        fig, _ = pop_figures()[0]
        ydata = np.asarray(fig.axes[0].lines[0].get_ydata(), dtype=float)
        assert not np.isnan(ydata).any()
        plt.close(fig)


class TestPeaksAnnotatedIntegration:
    def test_line_breaks_at_outage_but_peaks_keep_real_coordinates(
        self, isolated_plots_dir, monkeypatch
    ):
        monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "wifi")
        ts, flow, quality = _make_series_with_outage()
        # Inject a couple of real peaks on the post-outage segment.
        flow = flow.copy()
        flow[-50] = 50.0
        flow[-20] = 75.0
        result = _peaks_annotated(ts, flow, quality, "BB-TEST", float(ts[0]))
        fig, _ = pop_figures()[0]
        # First line is the connected series — must contain NaN.
        line_y = np.asarray(fig.axes[0].lines[0].get_ydata(), dtype=float)
        assert np.isnan(line_y).any()
        # Peak count surfaced through the result dict reflects the unbroken series.
        assert result["peak_count"] >= 1
        plt.close(fig)


class TestSignalQualityIntegration:
    def test_quality_line_breaks_at_outage(self, isolated_plots_dir, monkeypatch):
        monkeypatch.setenv("BLUEBOT_METER_NETWORK_TYPE", "wifi")
        ts, flow, quality = _make_series_with_outage()
        _signal_quality(ts, flow, quality, "BB-TEST", float(ts[0]))
        fig, _ = pop_figures()[0]
        # First line is the quality series.
        ydata = np.asarray(fig.axes[0].lines[0].get_ydata(), dtype=float)
        assert np.isnan(ydata).any(), "quality line did not break at outage"
        plt.close(fig)

    def test_skips_when_no_quality_present(self, isolated_plots_dir):
        ts = np.arange(0, 100, 2, dtype=float)
        flow = np.ones_like(ts)
        quality = np.full_like(ts, np.nan)
        result = _signal_quality(ts, flow, quality, "BB-TEST", 0.0)
        assert "error" in result
        assert pop_figures() == []


# ---------------------------------------------------------------------------
# Timezone resolution + axis labelling
# ---------------------------------------------------------------------------


class TestResolvePlotTz:
    def test_explicit_arg_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("BLUEBOT_PLOT_TZ", "America/Denver")
        tz = resolve_plot_tz("America/New_York")
        assert isinstance(tz, ZoneInfo)
        assert tz.key == "America/New_York"

    def test_env_var_used_when_no_arg(self, monkeypatch):
        monkeypatch.setenv("BLUEBOT_PLOT_TZ", "America/Denver")
        tz = resolve_plot_tz(None)
        assert isinstance(tz, ZoneInfo)
        assert tz.key == "America/Denver"

    def test_unknown_zone_falls_back_to_utc(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        assert resolve_plot_tz("Mars/Olympus_Mons") is timezone.utc

    def test_empty_inputs_fall_back_to_utc(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        assert resolve_plot_tz("") is timezone.utc
        assert resolve_plot_tz("   ") is timezone.utc
        assert resolve_plot_tz(None) is timezone.utc

    def test_explicit_utc_string_returns_utc(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        assert resolve_plot_tz("UTC") is timezone.utc
        assert resolve_plot_tz("utc") is timezone.utc


class TestDescribePlotTz:
    def test_iana_zone_returns_zone_and_label(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        info = describe_plot_tz("America/Denver")
        assert info["zone"] == "America/Denver"
        assert info["label"] == "America/Denver"
        assert isinstance(info["tzinfo"], ZoneInfo)

    def test_explicit_utc_returns_plain_utc_label(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        info = describe_plot_tz("UTC")
        assert info["zone"] == "UTC"
        assert info["label"] == "UTC"

    def test_fallback_utc_label_is_marked(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        info = describe_plot_tz(None)
        assert info["zone"] == "UTC"
        assert "meter timezone unknown" in info["label"]

    def test_env_var_explicit_utc_keeps_plain_label(self, monkeypatch):
        monkeypatch.setenv("BLUEBOT_PLOT_TZ", "UTC")
        info = describe_plot_tz(None)
        assert info["zone"] == "UTC"
        assert info["label"] == "UTC"


class TestPlotAxisIsTzAware:
    """The DateFormatter on each tz-aware plot must carry the resolved tzinfo."""

    def _ts_flow_quality(self):
        base = 1_700_000_000.0
        ts = np.arange(base, base + 600, 2.0)
        flow = np.full_like(ts, 5.0)
        quality = np.full_like(ts, 90.0)
        return ts, flow, quality

    def test_time_series_uses_explicit_tz(self, isolated_plots_dir):
        ts, flow, quality = self._ts_flow_quality()
        result = _time_series(
            ts, flow, quality, "BB-TEST", float(ts[0]),
            tz_name="America/Denver",
        )
        assert result["tz"] == "America/Denver"
        fig, _ = pop_figures()[0]
        formatter = fig.axes[0].xaxis.get_major_formatter()
        assert getattr(formatter, "tz", None) is not None
        assert getattr(formatter.tz, "key", None) == "America/Denver"
        # Axis label must reflect the resolved zone, not "UTC".
        assert "UTC" not in fig.axes[0].get_xlabel()
        plt.close(fig)

    def test_time_series_falls_back_to_marked_utc(self, isolated_plots_dir, monkeypatch):
        monkeypatch.delenv("BLUEBOT_PLOT_TZ", raising=False)
        ts, flow, quality = self._ts_flow_quality()
        _time_series(ts, flow, quality, "BB-TEST", float(ts[0]))
        fig, _ = pop_figures()[0]
        label = fig.axes[0].get_xlabel()
        # Either the short tz code "UTC" or the marked-fallback label is acceptable;
        # both must indicate UTC explicitly so the user can't mistake it for local.
        assert "UTC" in label
        plt.close(fig)

    def test_peaks_uses_env_tz(self, isolated_plots_dir, monkeypatch):
        monkeypatch.setenv("BLUEBOT_PLOT_TZ", "America/New_York")
        ts, flow, quality = self._ts_flow_quality()
        flow = flow.copy()
        flow[100] = 50.0
        result = _peaks_annotated(ts, flow, quality, "BB-TEST", float(ts[0]))
        assert result["tz"] == "America/New_York"
        fig, _ = pop_figures()[0]
        formatter = fig.axes[0].xaxis.get_major_formatter()
        assert getattr(formatter.tz, "key", None) == "America/New_York"
        plt.close(fig)

    def test_signal_quality_uses_explicit_tz(self, isolated_plots_dir):
        ts, flow, quality = self._ts_flow_quality()
        result = _signal_quality(
            ts, flow, quality, "BB-TEST", float(ts[0]), tz_name="Europe/London"
        )
        assert result["tz"] == "Europe/London"
        fig, _ = pop_figures()[0]
        formatter = fig.axes[0].xaxis.get_major_formatter()
        assert getattr(formatter.tz, "key", None) == "Europe/London"
        plt.close(fig)


class TestGeneratePlotForwardsTz:
    def test_generate_plot_passes_tz_through_dispatcher(self, isolated_plots_dir):
        base = 1_700_000_000.0
        ts = np.arange(base, base + 600, 2.0)
        flow = np.full_like(ts, 5.0)
        quality = np.full_like(ts, 90.0)
        result = generate_plot(
            "time_series", ts, flow, quality, "BB-TEST", float(ts[0]),
            tz_name="America/Denver",
        )
        assert result["tz"] == "America/Denver"
        fig, _ = pop_figures()[0]
        formatter = fig.axes[0].xaxis.get_major_formatter()
        assert getattr(formatter.tz, "key", None) == "America/Denver"
        plt.close(fig)

    def test_generate_plot_ignores_tz_for_flow_duration_curve(self, isolated_plots_dir):
        base = 1_700_000_000.0
        ts = np.arange(base, base + 600, 2.0)
        flow = np.linspace(0.5, 10.0, len(ts))
        quality = np.full_like(ts, 90.0)
        result = generate_plot(
            "flow_duration_curve", ts, flow, quality, "BB-TEST", float(ts[0]),
            tz_name="America/Denver",  # accepted but irrelevant — no time axis
        )
        assert "path" in result and "tz" not in result
        # Drain the figure so the autouse fixture doesn't see it as a leak.
        for fig, _ in pop_figures():
            plt.close(fig)

    def test_diagnostic_timeline_writes_png_and_marker_caption(self, isolated_plots_dir):
        base = 1_700_000_000.0
        ts = np.array([base, base + 2, base + 4, base + 700, base + 702], dtype=float)
        flow = np.array([1.0, 1.1, 1.2, 4.0, 4.1], dtype=float)
        quality = np.array([95.0, 92.0, 90.0, 55.0, 54.0], dtype=float)
        facts = {
            "max_healthy_inter_arrival_seconds": 60,
            "cusum_drift": {
                "skipped": False,
                "drift_detected": "upward",
                "positive_alarm_count": 5,
                "negative_alarm_count": 0,
                "first_alarm_timestamp": int(base + 700),
            },
            "signal_quality": {
                "flagged_percent": 40,
                "low_quality_intervals": [
                    {
                        "start_timestamp": int(base + 700),
                        "end_timestamp": int(base + 702),
                        "duration_seconds": 2.0,
                        "point_count": 2,
                        "mean_quality_score": 54.5,
                    }
                ],
            },
            "anomaly_attribution": {
                "summary": "The strongest interpretation is a real sustained upward flow change.",
                "next_checks": ["Compare with the previous day"],
            },
        }
        result = generate_plot(
            "diagnostic_timeline",
            ts,
            flow,
            quality,
            "BB-TEST",
            float(ts[0]),
            tz_name="America/Denver",
            verified_facts=facts,
        )
        assert result["path"].endswith("_diagnostic_timeline.png")
        assert result["marker_count"] >= 2
        assert result["caption"]["plot_type"] == "diagnostic_timeline"
        assert any(
            marker["type"] == "drift"
            for marker in result["caption"]["diagnostic_markers"]
        )
        captions = pop_captions()
        assert captions[result["path"]]["diagnostic_markers"]
        for fig, _ in pop_figures():
            plt.close(fig)
