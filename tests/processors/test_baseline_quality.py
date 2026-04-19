"""
Unit tests for ``processors.baseline_quality``.

Every refusal state, the CUSUM change-point ordering, the MAD outlier filter,
and the env-variable precedence should stay green. These tests encode the
smoke scenarios we validated when building the module so regressions are loud.
"""

from __future__ import annotations

import math
import random
from typing import List

from processors.baseline_quality import (
    BaselineQualityConfig,
    STATE_INSUFFICIENT_CLEAN_DAYS,
    STATE_NO_HISTORY,
    STATE_NOT_REQUESTED,
    STATE_PARTIAL_TODAY_UNSUITABLE,
    STATE_REGIME_CHANGE_TOO_RECENT,
    STATE_RELIABLE,
    evaluate_baseline_quality,
    not_requested_stub,
    _mad_outlier_mask,
    _median,
    _simple_cusum_change_point,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rollups(
    n: int,
    *,
    vol: float = 2000.0,
    jitter: float = 0.0,
    coverage: float = 0.99,
    lowq: float = 0.02,
    start_day: int = 1,
    weekday_offset: int = 0,
    seed: int | None = None,
) -> List[dict]:
    rng = random.Random(seed)
    rows: List[dict] = []
    for i in range(n):
        d = start_day + i
        rows.append(
            {
                "local_date": f"2026-04-{d:02d}",
                "tz": "America/New_York",
                "volume_gallons": vol + (rng.uniform(-jitter, jitter) if jitter else 0.0),
                "coverage_ratio": coverage,
                "low_quality_ratio": lowq,
                "weekday": (weekday_offset + d - 1) % 7,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Public-API state coverage
# ---------------------------------------------------------------------------


def test_not_requested_stub_matches_public_call():
    direct = evaluate_baseline_quality(reference_rollups=None).to_dict()
    assert not_requested_stub() == direct
    assert direct["state"] == STATE_NOT_REQUESTED
    assert direct["reliable"] is False
    assert direct["reasons_refused"] == []


def test_empty_reference_returns_no_history():
    res = evaluate_baseline_quality(reference_rollups=[]).to_dict()
    assert res["state"] == STATE_NO_HISTORY
    assert res["reliable"] is False
    assert res["reasons_refused"] and "No reference days" in res["reasons_refused"][0]


def test_too_few_clean_days_is_insufficient():
    res = evaluate_baseline_quality(reference_rollups=_rollups(3)).to_dict()
    assert res["state"] == STATE_INSUFFICIENT_CLEAN_DAYS
    assert res["reliable"] is False
    assert "Only 3 clean reference days" in res["reasons_refused"][0]


def test_stable_series_is_reliable():
    res = evaluate_baseline_quality(
        reference_rollups=_rollups(14, jitter=25, seed=0)
    ).to_dict()
    assert res["state"] == STATE_RELIABLE
    assert res["reliable"] is True
    assert res["n_days_used"] == 14
    assert res["n_days_rejected"] == 0
    assert res["change_point_detected"] is False


def test_low_coverage_day_is_rejected():
    rollups = _rollups(10) + [
        {
            "local_date": "2026-04-11",
            "tz": "America/New_York",
            "volume_gallons": 2000.0,
            "coverage_ratio": 0.50,           # below default 0.85 threshold
            "low_quality_ratio": 0.02,
            "weekday": 3,
        }
    ]
    res = evaluate_baseline_quality(reference_rollups=rollups).to_dict()
    assert res["n_days_used"] == 10
    assert res["n_days_rejected"] == 1
    reason_msgs = {rd["reason"] for rd in res["days_rejected"]}
    assert any("coverage_ratio" in m for m in reason_msgs)


def test_high_low_quality_day_is_rejected():
    rollups = _rollups(10) + [
        {
            "local_date": "2026-04-11",
            "tz": "America/New_York",
            "volume_gallons": 2000.0,
            "coverage_ratio": 0.99,
            "low_quality_ratio": 0.50,        # way above 0.20 default
            "weekday": 3,
        }
    ]
    res = evaluate_baseline_quality(reference_rollups=rollups).to_dict()
    assert res["n_days_rejected"] == 1
    assert any("low_quality_ratio" in rd["reason"] for rd in res["days_rejected"])


def test_missing_volume_is_rejected():
    rollups = _rollups(10) + [
        {
            "local_date": "2026-04-11",
            "tz": "America/New_York",
            "volume_gallons": None,
            "coverage_ratio": 0.99,
            "low_quality_ratio": 0.02,
            "weekday": 3,
        }
    ]
    res = evaluate_baseline_quality(reference_rollups=rollups).to_dict()
    assert any(rd["reason"] == "missing volume_gallons" for rd in res["days_rejected"])


# ---------------------------------------------------------------------------
# Regime-change / CUSUM behaviour
# ---------------------------------------------------------------------------


def _regime_rollups(
    n_pre: int, n_post: int, *, pre_vol: float = 1000.0, post_vol: float = 4000.0
) -> List[dict]:
    pre = _rollups(n_pre, vol=pre_vol, jitter=50, seed=0)
    post = _rollups(
        n_post,
        vol=post_vol,
        jitter=50,
        start_day=n_pre + 1,
        weekday_offset=n_pre,
        seed=1,
    )
    return pre + post


def test_old_regime_change_is_truncated_and_reliable():
    rollups = _regime_rollups(n_pre=5, n_post=11)       # 11 post-change ≥ default 7
    res = evaluate_baseline_quality(reference_rollups=rollups).to_dict()
    assert res["change_point_detected"] is True
    assert res["post_change_days"] == 10               # idx points at last pre; len - idx - 1
    assert res["state"] == STATE_RELIABLE
    assert res["reliable"] is True
    # All 5 pre-change days should have been rejected with the pre-change tag.
    pre_dates = {f"2026-04-{d:02d}" for d in range(1, 6)}
    rejected_pre = {rd["local_date"] for rd in res["days_rejected"]}
    assert pre_dates.issubset(rejected_pre)
    assert any("pre-change-point" in rd["reason"] for rd in res["days_rejected"])


def test_recent_regime_change_is_refused():
    rollups = _regime_rollups(n_pre=12, n_post=3)       # 3 post-change < default 7
    res = evaluate_baseline_quality(reference_rollups=rollups).to_dict()
    assert res["state"] == STATE_REGIME_CHANGE_TOO_RECENT
    assert res["reliable"] is False
    assert res["change_point_detected"] is True
    assert res["post_change_days"] is not None
    assert res["post_change_days"] < 7
    assert "Recent change-point" in res["reasons_refused"][0]


def test_cusum_returns_first_crossing_not_last():
    # Values jump from ~1000 to ~4000 at index 10. The CUSUM must flag in the
    # post-change region, NOT the final sample (which would imply the detector
    # "latched" on the most recent sample instead of the earliest crossing).
    # With a small minority of pre-change samples the MAD-based scale is tight
    # and the crossing happens right at the shift.
    rng = random.Random(0)
    values = (
        [1000.0 + rng.uniform(-30, 30) for _ in range(5)]
        + [4000.0 + rng.uniform(-30, 30) for _ in range(11)]
    )
    idx = _simple_cusum_change_point(values, shift_z=2.0)
    assert idx is not None
    assert idx < len(values) - 1, f"CUSUM latched on trailing sample: idx={idx}"
    # Accept the first few post-change samples (may take 1–2 accumulations).
    assert 5 <= idx <= 7, f"expected crossing near index 5, got {idx}"


def test_cusum_returns_none_on_stable_series():
    values = [2000.0 + (i % 5) for i in range(20)]
    assert _simple_cusum_change_point(values, shift_z=2.0) is None


# ---------------------------------------------------------------------------
# MAD outlier mask
# ---------------------------------------------------------------------------


def test_mad_outlier_mask_flags_single_spike():
    values = [100.0, 102.0, 98.0, 101.0, 99.0, 5000.0, 100.0, 97.0, 103.0]
    mask = _mad_outlier_mask(values, z=3.5)
    assert mask[5] is True
    assert sum(mask) == 1


def test_mad_outlier_mask_returns_all_false_on_constant_series():
    values = [42.0] * 8
    assert _mad_outlier_mask(values, z=3.5) == [False] * 8


def test_median_handles_empty_and_even_lengths():
    assert math.isnan(_median([]))
    assert _median([1.0, 3.0]) == 2.0
    assert _median([1.0, 2.0, 3.0]) == 2.0


# ---------------------------------------------------------------------------
# Same-weekday gating
# ---------------------------------------------------------------------------


def test_same_weekday_gate_insufficient():
    # 14 days all weekday=0 (Monday); asking for weekday=4 (Friday) ⇒ 0 matches.
    rollups = [
        {
            "local_date": f"2026-04-{d:02d}",
            "tz": "America/New_York",
            "volume_gallons": 2000.0,
            "coverage_ratio": 0.99,
            "low_quality_ratio": 0.02,
            "weekday": 0,
        }
        for d in range(1, 15)
    ]
    res = evaluate_baseline_quality(
        reference_rollups=rollups, target_weekday=4
    ).to_dict()
    assert res["state"] == STATE_INSUFFICIENT_CLEAN_DAYS
    assert res["n_same_weekday_days_used"] == 0


def test_same_weekday_gate_sufficient():
    rollups = _rollups(14, jitter=10, seed=0)
    target = rollups[0]["weekday"]
    # Force at least 3 matching days by duplicating weekday values.
    for i in range(3):
        rollups[i]["weekday"] = target
    res = evaluate_baseline_quality(
        reference_rollups=rollups, target_weekday=target
    ).to_dict()
    assert res["state"] == STATE_RELIABLE
    assert (res["n_same_weekday_days_used"] or 0) >= 3


# ---------------------------------------------------------------------------
# Projection-suitability guards
# ---------------------------------------------------------------------------


def test_partial_today_too_early_for_projection():
    rollups = _rollups(14, jitter=10, seed=0)
    res = evaluate_baseline_quality(
        reference_rollups=rollups,
        today_partial={
            "local_date": "2026-04-16",
            "tz": "America/New_York",
            "volume_gallons": 50.0,
        },
        fraction_of_day_elapsed=0.10,
    ).to_dict()
    assert res["state"] == STATE_PARTIAL_TODAY_UNSUITABLE
    assert "10%" in res["reasons_refused"][0]


def test_partial_today_missing_buckets_for_projection():
    rollups = _rollups(14, jitter=10, seed=0)
    res = evaluate_baseline_quality(
        reference_rollups=rollups,
        today_partial={
            "local_date": "2026-04-16",
            "tz": "America/New_York",
            "volume_gallons": 500.0,
        },
        fraction_of_day_elapsed=0.50,
        today_missing_bucket_ratio=0.40,
    ).to_dict()
    assert res["state"] == STATE_PARTIAL_TODAY_UNSUITABLE
    assert "40%" in res["reasons_refused"][0]


def test_projection_ok_when_all_guards_pass():
    rollups = _rollups(14, jitter=10, seed=0)
    target = rollups[0]["weekday"]
    for i in range(4):
        rollups[i]["weekday"] = target
    res = evaluate_baseline_quality(
        reference_rollups=rollups,
        today_partial={
            "local_date": "2026-04-16",
            "tz": "America/New_York",
            "volume_gallons": 500.0,
        },
        fraction_of_day_elapsed=0.50,
        today_missing_bucket_ratio=0.05,
        target_weekday=target,
    ).to_dict()
    assert res["state"] == STATE_RELIABLE
    assert res["reliable"] is True


# ---------------------------------------------------------------------------
# Config & environment overrides
# ---------------------------------------------------------------------------


def test_config_from_env_overrides(monkeypatch):
    monkeypatch.setenv("BLUEBOT_BASELINE_MIN_DAYS", "2")
    monkeypatch.setenv("BLUEBOT_BASELINE_MIN_POST_CHANGE_DAYS", "3")
    cfg = BaselineQualityConfig.from_env()
    assert cfg.min_reference_days == 2
    assert cfg.min_post_change_days == 3


def test_config_from_env_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("BLUEBOT_BASELINE_MIN_DAYS", "not-a-number")
    cfg = BaselineQualityConfig.from_env()
    assert cfg.min_reference_days == BaselineQualityConfig().min_reference_days


def test_custom_config_can_lower_minimums():
    rollups = _rollups(3, jitter=5, seed=0)
    cfg = BaselineQualityConfig(min_reference_days=2, min_same_weekday_days=1)
    res = evaluate_baseline_quality(reference_rollups=rollups, config=cfg).to_dict()
    assert res["state"] == STATE_RELIABLE
    assert res["n_days_used"] == 3


# ---------------------------------------------------------------------------
# Dataclass wire format
# ---------------------------------------------------------------------------


def test_rejected_day_serialises_as_plain_dict():
    rollups = _rollups(10) + [
        {
            "local_date": "2026-04-11",
            "tz": "America/New_York",
            "volume_gallons": 2000.0,
            "coverage_ratio": 0.50,
            "low_quality_ratio": 0.02,
            "weekday": 3,
        }
    ]
    res = evaluate_baseline_quality(reference_rollups=rollups).to_dict()
    assert isinstance(res["days_rejected"], list)
    for rd in res["days_rejected"]:
        assert isinstance(rd, dict)
        assert set(rd.keys()) == {"local_date", "reason"}


def test_result_config_used_is_dict_not_dataclass():
    res = evaluate_baseline_quality(reference_rollups=[]).to_dict()
    assert isinstance(res["config_used"], dict)
    assert "min_reference_days" in res["config_used"]
