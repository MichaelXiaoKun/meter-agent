"""
Tests for ``processors.plot_captions`` — the structured, terse captions we
attach to each plot so text-only LLMs can still cite what the chart shows.

The captions are deliberately tiny scalar-only dicts; these tests lock in
the field names and the classifier buckets so they stay stable across
versions.
"""

from __future__ import annotations

import numpy as np
import pytest

from processors.plot_captions import (
    caption_flow_duration_curve,
    caption_peaks_annotated,
    caption_signal_quality,
    caption_time_series,
)


# ---------------------------------------------------------------------------
# time_series
# ---------------------------------------------------------------------------


def test_time_series_caption_flat_variability_and_no_gaps():
    ts = np.arange(0, 600, 2, dtype=float)
    flow = np.full_like(ts, 5.0)
    quality = np.full_like(ts, 90.0)
    cap = caption_time_series(ts, flow, quality, healthy_cap_seconds=10.0)
    assert cap["plot_type"] == "time_series"
    assert cap["slope_sign"] == "flat"
    assert cap["variability"] == "near_constant"
    assert cap["gap_markers"] == 0
    assert cap["low_quality_fraction"] == 0.0
    assert cap["n_points"] == ts.size


def test_time_series_caption_detects_gap_marker():
    ts = np.array([0, 2, 4, 600, 602, 604], dtype=float)
    flow = np.arange(6, dtype=float)
    cap = caption_time_series(ts, flow, None, healthy_cap_seconds=10.0)
    assert cap["gap_markers"] == 1
    assert cap["low_quality_fraction"] is None


def test_time_series_caption_detects_positive_slope():
    ts = np.arange(0, 600, 2, dtype=float)
    flow = np.linspace(1.0, 10.0, ts.size)
    quality = np.full_like(ts, 90.0)
    cap = caption_time_series(ts, flow, quality)
    assert cap["slope_sign"] == "positive"
    assert cap["variability"] in {"low", "moderate", "high"}


def test_time_series_caption_detects_low_quality_fraction():
    ts = np.arange(0, 200, 2, dtype=float)
    flow = np.ones_like(ts)
    quality = np.full_like(ts, 90.0)
    quality[:20] = 30.0  # 20 out of 100 samples (20 %) low quality
    cap = caption_time_series(ts, flow, quality)
    assert cap["low_quality_fraction"] == pytest.approx(0.20, abs=1e-3)


# ---------------------------------------------------------------------------
# flow_duration_curve
# ---------------------------------------------------------------------------


def test_fdc_caption_flat_shape_when_values_constant():
    flow = np.full(100, 5.0)
    cap = caption_flow_duration_curve(flow)
    assert cap["plot_type"] == "flow_duration_curve"
    assert cap["shape"] == "flat"
    assert cap["q10"] == pytest.approx(5.0, abs=1e-6)
    assert cap["q50"] == pytest.approx(5.0, abs=1e-6)
    assert cap["q90"] == pytest.approx(5.0, abs=1e-6)


def test_fdc_caption_flashy_when_q10_dominates_q50():
    # 90 % zeros, 10 % of 100 → q10 ~= 100, q50 ~= 0 → "intermittent_zero_dominated".
    flow = np.concatenate([np.zeros(90), np.full(10, 100.0)])
    cap = caption_flow_duration_curve(flow)
    assert cap["shape"] in {"flashy", "intermittent_zero_dominated"}


def test_fdc_caption_empty_shape():
    cap = caption_flow_duration_curve(np.array([np.nan, np.nan]))
    assert cap["shape"] == "empty"


# ---------------------------------------------------------------------------
# peaks_annotated
# ---------------------------------------------------------------------------


def test_peaks_caption_reports_counts():
    ts = np.arange(0, 200, 2, dtype=float)
    flow = np.ones_like(ts)
    flow[10] = 20.0
    flow[50] = 20.0
    cap = caption_peaks_annotated(ts, flow, peak_count=2)
    assert cap["plot_type"] == "peaks_annotated"
    assert cap["peak_count"] == 2
    assert cap["peak_density"] in {"none", "low", "moderate", "high", "insufficient_data"}
    assert cap["variability"] in {"near_constant", "low", "moderate", "high", "insufficient_data"}
    assert cap["n_points"] == ts.size


# ---------------------------------------------------------------------------
# signal_quality
# ---------------------------------------------------------------------------


def test_signal_quality_caption_clean_when_all_above_threshold():
    quality = np.full(100, 90.0)
    cap = caption_signal_quality(quality)
    assert cap["plot_type"] == "signal_quality"
    assert cap["state"] == "clean"
    assert cap["low_quality_fraction"] == 0.0


def test_signal_quality_caption_widespread_when_majority_low():
    quality = np.concatenate([np.full(60, 40.0), np.full(40, 90.0)])
    cap = caption_signal_quality(quality)
    assert cap["state"] == "widespread_low_quality"


def test_signal_quality_caption_no_valid_quality_when_all_nan():
    cap = caption_signal_quality(np.full(10, np.nan))
    assert cap["state"] == "no_valid_quality"
