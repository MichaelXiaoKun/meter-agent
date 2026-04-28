"""
Unit tests for ``processors.baseline_compare``.

The pipeline contract is small but specific:

* Empty / unusable reference set → ``None`` (caller should be gated by
  ``baseline_quality.reliable``; this is just defensive).
* Reference distribution exposes median / P25 / P75 / robust-σ / IQR so the
  outer LLM can quote "typical day was X gal" without re-deriving statistics.
* The verdict respects ``BLUEBOT_BASELINE_COMPARE_LOW`` / ``..._HIGH``
  bounds and lands on ``typical`` / ``elevated`` / ``below_normal``.
* When ``today_partial`` is missing the verdict stays ``indeterminate`` and
  no projection block is emitted.
* The same-weekday subset takes over when at least 3 matching days are
  available — the threshold mirrors the baseline-quality gate.
* Degenerate distributions (all reference days identical → MAD = 0) yield a
  finite z-score (0.0) rather than ``inf``; the verdict still classifies on
  the ratio threshold.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List

import pytest

from processors.baseline_compare import compute_today_vs_baseline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _refs(
    n: int,
    *,
    base_volume: float = 2000.0,
    jitter: float = 0.0,
    weekday_offset: int = 0,
    seed: int = 0,
) -> List[Dict[str, Any]]:
    import random

    rng = random.Random(seed)
    out: List[Dict[str, Any]] = []
    for i in range(n):
        out.append(
            {
                "local_date": f"2026-04-{i + 1:02d}",
                "tz": "America/Denver",
                "volume_gallons": base_volume + (rng.uniform(-jitter, jitter) if jitter else 0.0),
                "weekday": (weekday_offset + i) % 7,
            }
        )
    return out


def _today(volume: float, weekday: int = 0, local_date: str = "2026-04-30") -> Dict[str, Any]:
    return {
        "local_date": local_date,
        "tz": "America/Denver",
        "volume_gallons": volume,
        "weekday": weekday,
    }


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_reference_returns_none(self):
        assert compute_today_vs_baseline([]) is None

    def test_reference_with_no_volumes_returns_none(self):
        bad_refs = [
            {"local_date": "2026-04-01", "tz": "UTC", "volume_gallons": None}
        ]
        assert compute_today_vs_baseline(bad_refs) is None

    def test_no_today_yields_indeterminate(self):
        out = compute_today_vs_baseline(_refs(7))
        assert out is not None
        assert out["verdict"] == "indeterminate"
        assert "projection" not in out

    def test_no_fraction_yields_indeterminate(self):
        out = compute_today_vs_baseline(
            _refs(7), today_partial=_today(volume=1000.0)
        )
        assert out is not None
        assert out["verdict"] == "indeterminate"
        assert "projection" not in out


# ---------------------------------------------------------------------------
# Reference distribution shape
# ---------------------------------------------------------------------------


class TestReferenceDistribution:
    def test_distribution_fields_populated(self):
        out = compute_today_vs_baseline(_refs(10, base_volume=1000.0, jitter=200.0, seed=42))
        assert out is not None
        dist = out["reference_distribution"]
        assert "median_volume_gallons" in dist
        assert "p25_volume_gallons" in dist
        assert "p75_volume_gallons" in dist
        assert "mad_volume_gallons" in dist  # robust σ
        assert "iqr_volume_gallons" in dist
        assert dist["p25_volume_gallons"] <= dist["median_volume_gallons"] <= dist["p75_volume_gallons"]

    def test_reference_period_metadata(self):
        refs = _refs(7)
        out = compute_today_vs_baseline(refs)
        assert out is not None
        assert out["reference_period"]["n_days"] == 7
        assert out["reference_period"]["first_local_date"] == "2026-04-01"
        assert out["reference_period"]["last_local_date"] == "2026-04-07"


# ---------------------------------------------------------------------------
# Verdict classification (typical / elevated / below_normal)
# ---------------------------------------------------------------------------


class TestVerdict:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        monkeypatch.delenv("BLUEBOT_BASELINE_COMPARE_LOW", raising=False)
        monkeypatch.delenv("BLUEBOT_BASELINE_COMPARE_HIGH", raising=False)

    def test_typical_when_projected_matches_median(self):
        # Reference median 2000 gal/day; today on track for 2000 → ratio 1.0 → typical.
        refs = _refs(10, base_volume=2000.0)
        out = compute_today_vs_baseline(
            refs,
            today_partial=_today(volume=500.0),  # 0.25 of day → projected 2000
            fraction_of_day_elapsed=0.25,
        )
        assert out is not None
        assert out["verdict"] == "typical"
        assert out["projection"]["ratio_projected_vs_expected"] == pytest.approx(1.0, rel=1e-6)

    def test_elevated_when_projected_above_high_threshold(self):
        refs = _refs(10, base_volume=2000.0)
        # 0.25 of day with 600 → projected 2400 → ratio 1.20.
        # Default HIGH = 1.30, so 1.20 should still be "typical".
        # Push to 800 → projected 3200 → ratio 1.60 ⇒ elevated.
        out = compute_today_vs_baseline(
            refs,
            today_partial=_today(volume=800.0),
            fraction_of_day_elapsed=0.25,
        )
        assert out is not None
        assert out["verdict"] == "elevated"

    def test_below_normal_when_projected_below_low_threshold(self):
        refs = _refs(10, base_volume=2000.0)
        out = compute_today_vs_baseline(
            refs,
            today_partial=_today(volume=200.0),  # 0.25 of day → projected 800 → ratio 0.40
            fraction_of_day_elapsed=0.25,
        )
        assert out is not None
        assert out["verdict"] == "below_normal"

    def test_env_override_widens_typical_band(self, monkeypatch):
        # With LOW=0.4 / HIGH=2.0 a previously elevated ratio (1.6) becomes typical.
        monkeypatch.setenv("BLUEBOT_BASELINE_COMPARE_LOW", "0.4")
        monkeypatch.setenv("BLUEBOT_BASELINE_COMPARE_HIGH", "2.0")
        refs = _refs(10, base_volume=2000.0)
        out = compute_today_vs_baseline(
            refs,
            today_partial=_today(volume=800.0),  # ratio 1.6
            fraction_of_day_elapsed=0.25,
        )
        assert out is not None
        assert out["verdict"] == "typical"

    def test_indeterminate_when_expected_is_zero(self):
        # All reference volumes 0 → median 0 → ratio undefined.
        refs = _refs(7, base_volume=0.0)
        out = compute_today_vs_baseline(
            refs,
            today_partial=_today(volume=100.0),
            fraction_of_day_elapsed=0.25,
        )
        assert out is not None
        assert out["verdict"] == "indeterminate"

    def test_zero_mad_yields_finite_z_score(self):
        # All reference days identical ⇒ MAD = 0. Z must not be ±inf.
        refs = _refs(7, base_volume=1500.0, jitter=0.0)
        out = compute_today_vs_baseline(
            refs,
            today_partial=_today(volume=375.0),  # projected 1500 → ratio 1.0
            fraction_of_day_elapsed=0.25,
        )
        assert out is not None
        z = out["projection"]["z_score_robust"]
        assert math.isfinite(z)


# ---------------------------------------------------------------------------
# Same-weekday subset
# ---------------------------------------------------------------------------


class TestSameWeekday:
    def test_same_weekday_subset_used_when_three_or_more_match(self):
        # Build 28 reference days (4 weeks). Mondays (weekday=0) are 3000 gal,
        # other days are 1000 gal — so the same-weekday median is 3000 while
        # the full-set median is 1000.
        refs: List[Dict[str, Any]] = []
        for i in range(28):
            refs.append(
                {
                    "local_date": f"2026-04-{i + 1:02d}",
                    "tz": "UTC",
                    "volume_gallons": 3000.0 if i % 7 == 0 else 1000.0,
                    "weekday": i % 7,
                }
            )
        # Today is a Monday → same-weekday subset has 4 Mondays at 3000, so
        # expected = 3000 (not the full-set median ~1000).
        out = compute_today_vs_baseline(
            refs,
            today_partial=_today(volume=750.0, weekday=0),
            target_weekday=0,
            fraction_of_day_elapsed=0.25,
        )
        assert out is not None
        assert out["reference_period"]["n_same_weekday_days"] == 4
        assert out["reference_distribution"]["median_volume_gallons"] == pytest.approx(3000.0)

    def test_falls_back_to_full_set_when_under_three_same_weekday(self):
        # Only 2 Mondays in the reference → use the full set instead.
        refs: List[Dict[str, Any]] = []
        for i in range(8):
            refs.append(
                {
                    "local_date": f"2026-04-{i + 1:02d}",
                    "tz": "UTC",
                    "volume_gallons": 1000.0,
                    "weekday": 0 if i < 2 else 1,
                }
            )
        out = compute_today_vs_baseline(
            refs,
            today_partial=_today(volume=250.0, weekday=0),
            target_weekday=0,
            fraction_of_day_elapsed=0.25,
        )
        assert out is not None
        assert out["reference_period"]["n_same_weekday_days"] == 2
        # Median falls back to full-set median (1000) — same value here, but
        # the important assertion is that we didn't crash on a 2-element set.
        assert out["reference_distribution"]["median_volume_gallons"] == pytest.approx(1000.0)
