"""
Tests for frequency-domain probes.
"""

from __future__ import annotations

import numpy as np
import pytest

from processors.frequency_domain import compute_dominant_frequencies


def test_detects_dominant_period_after_resampling():
    ts = np.arange(0, 7200, 10, dtype=float)
    period = 300.0
    values = 4.0 + np.sin(2 * np.pi * ts / period)

    freqs = compute_dominant_frequencies(ts, values, top_k=3)

    assert freqs
    assert freqs[0]["period_seconds"] == pytest.approx(period, rel=0.12)
    assert freqs[0]["amplitude"] > 0


def test_constant_series_returns_empty():
    ts = np.arange(0, 3600, 10, dtype=float)
    values = np.ones_like(ts)

    assert compute_dominant_frequencies(ts, values) == []


def test_short_series_returns_empty():
    assert compute_dominant_frequencies([0, 1, 2], [1, 2, 3]) == []
