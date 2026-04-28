"""
Daily Rollup Processor
======================

Builds local-timezone daily rollups in the shape consumed by
:mod:`processors.baseline_quality` (``DailyRollup``).

Why this lives in its own module
--------------------------------

``processors.long_range_summary._bucket_rollups`` already produces 24-hour
buckets, but its boundaries are aligned to *UTC* multiples of 86 400 s, and it
emits flow stats (``min`` / ``median`` / ``max`` / ``mean``) rather than the
integrated daily volume the baseline-quality refusal pipeline needs.

This module returns rollups aligned to the **meter-local IANA timezone** so
"yesterday" / "same weekday last week" semantics line up with the times the
``verified_facts`` block already speaks. Each rollup carries:

* ``local_date`` (``YYYY-MM-DD`` in ``tz``)
* ``tz`` (echoed back so downstream consumers don't re-derive it)
* ``volume_gallons`` (trapezoidal integration of ``flow_rate`` over the day,
  using the same convention as :func:`processors.flow_metrics.compute_total_volume`)
* ``n_samples`` (rows after dropping NaN flow rates)
* ``coverage_ratio`` (samples kept / expected; ``nominal_interval_seconds``
  follows the same rule the rest of the pipeline already uses)
* ``low_quality_ratio`` (share of valid quality readings ≤ 60)
* ``n_gaps`` (count of inter-arrival pauses larger than the network-aware
  healthy cap, computed on the day's samples in isolation)
* ``weekday`` (``0=Mon..6=Sun``) — used by the baseline ``target_weekday`` gate

The function is **pure** — no network, no filesystem. It does use numpy and
pandas so it can stream large dataframes efficiently, but every input is a
plain dataframe and every output is JSON-serialisable.
"""

from __future__ import annotations

from datetime import date as _date
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from processors.continuity import detect_gaps
from processors.flow_metrics import compute_total_volume


__all__ = [
    "build_daily_rollups",
    "build_today_partial_rollup",
]


def _safe_tz(tz: Optional[str]) -> str:
    """Echo a non-empty IANA name; fall back to ``UTC`` so we never crash on day grouping."""
    if isinstance(tz, str) and tz.strip():
        return tz.strip()
    return "UTC"


def _local_dates(timestamps: np.ndarray, tz: str) -> pd.Series:
    """Return a Series of ``YYYY-MM-DD`` strings keyed in the requested IANA zone."""
    if len(timestamps) == 0:
        return pd.Series([], dtype="object")
    # ``pd.to_datetime`` with ``unit='s'`` produces UTC timestamps; convert to
    # the meter-local zone before truncating to the calendar date.
    utc = pd.to_datetime(timestamps, unit="s", utc=True)
    local = utc.tz_convert(tz)
    return pd.Series(local.strftime("%Y-%m-%d"))


def _weekday_for_date(local_date: str) -> Optional[int]:
    try:
        y, m, d = (int(x) for x in local_date.split("-", 2))
        return _date(y, m, d).weekday()
    except (ValueError, AttributeError):
        return None


def _coverage_ratio(
    *,
    n_samples: int,
    span_seconds: float,
    nominal_interval_seconds: float,
) -> Optional[float]:
    """``n_samples / expected`` where expected uses the same rule as long-range buckets.

    Returns ``None`` when the span is degenerate (single sample, zero-width day);
    callers treat that the same as "unknown" rather than 0.
    """
    if span_seconds <= 0 or nominal_interval_seconds <= 0:
        return None
    expected = max(1.0, span_seconds / float(nominal_interval_seconds))
    return float(n_samples) / float(expected)


def _build_one_rollup(
    *,
    local_date: str,
    tz: str,
    timestamps: np.ndarray,
    flow_rate: np.ndarray,
    quality: np.ndarray,
    nominal_interval_seconds: float,
    healthy_gap_cap_seconds: float,
    span_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute a single :class:`DailyRollup`-shaped dict.

    ``span_seconds`` overrides the natural last - first span — used by
    :func:`build_today_partial_rollup` so coverage is a fraction of the *full*
    local day rather than just the span of samples we have so far.
    """
    n_total = int(len(timestamps))
    finite_flow_mask = np.isfinite(flow_rate)
    n_samples = int(np.sum(finite_flow_mask))

    volume_info = compute_total_volume(timestamps, flow_rate)
    volume_gallons = float(volume_info.get("total_volume_gallons", 0.0))

    if span_seconds is None:
        if n_total >= 2:
            span_seconds = float(timestamps[-1] - timestamps[0])
        else:
            span_seconds = 0.0

    coverage_ratio = _coverage_ratio(
        n_samples=n_samples,
        span_seconds=float(span_seconds or 0.0),
        nominal_interval_seconds=nominal_interval_seconds,
    )

    valid_quality = quality[np.isfinite(quality)]
    low_quality_ratio: Optional[float]
    if len(valid_quality) == 0:
        low_quality_ratio = None
    else:
        low_quality_ratio = float(np.sum(valid_quality <= 60.0) / len(valid_quality))

    gaps = (
        detect_gaps(timestamps.astype(float), None)
        if n_total >= 2
        else []
    )
    n_gaps = int(
        sum(
            1
            for g in gaps
            if float(g.get("duration_seconds") or 0.0) > healthy_gap_cap_seconds
        )
    )

    weekday = _weekday_for_date(local_date)

    rollup: Dict[str, Any] = {
        "local_date": local_date,
        "tz": tz,
        "volume_gallons": volume_gallons,
        "n_samples": n_samples,
        "coverage_ratio": coverage_ratio,
        "low_quality_ratio": low_quality_ratio,
        "n_gaps": n_gaps,
    }
    if weekday is not None:
        rollup["weekday"] = weekday
    return rollup


def build_daily_rollups(
    df: pd.DataFrame,
    *,
    tz: str = "UTC",
    nominal_interval_seconds: float = 60.0,
    healthy_gap_cap_seconds: float = 60.0,
) -> List[Dict[str, Any]]:
    """Group a flow dataframe by **meter-local** calendar day.

    Parameters
    ----------
    df
        Must contain ``timestamp`` (Unix seconds, integer-coercible) and
        ``flow_rate`` (gal/min). ``quality`` is optional; missing → all NaN.
    tz
        IANA name of the meter's local zone (typically ``profile.deviceTimeZone``).
    nominal_interval_seconds
        Used to compute ``coverage_ratio``. Match the value the rest of the
        pipeline already uses (``min(max(median, P75), max_healthy_inter_arrival)``)
        so coverage is comparable across the bundle.
    healthy_gap_cap_seconds
        Inter-arrival pauses longer than this count toward ``n_gaps``. Set to
        the same network-aware cap (Wi-Fi ≈ 7.5 s, LoRaWAN ≈ 90 s) used elsewhere.

    Returns
    -------
    list[dict]
        One entry per local calendar day touched by ``df``, sorted ascending
        by ``local_date``. Empty list when the dataframe is empty.
    """
    if df is None or len(df) == 0 or "timestamp" not in df.columns:
        return []

    tz_name = _safe_tz(tz)
    work = df[["timestamp", "flow_rate"]].copy()
    if "quality" in df.columns:
        work["quality"] = df["quality"]
    else:
        work["quality"] = np.nan

    work["timestamp"] = pd.to_numeric(work["timestamp"], errors="coerce")
    work = work.dropna(subset=["timestamp"]).sort_values("timestamp")
    if work.empty:
        return []

    ts_arr = work["timestamp"].to_numpy(dtype=float)
    work["_local_date"] = _local_dates(ts_arr, tz_name).values

    rollups: List[Dict[str, Any]] = []
    for local_date, part in work.groupby("_local_date", sort=True):
        rollups.append(
            _build_one_rollup(
                local_date=str(local_date),
                tz=tz_name,
                timestamps=part["timestamp"].to_numpy(dtype=float),
                flow_rate=part["flow_rate"].to_numpy(dtype=float),
                quality=part["quality"].to_numpy(dtype=float),
                nominal_interval_seconds=nominal_interval_seconds,
                healthy_gap_cap_seconds=healthy_gap_cap_seconds,
            )
        )
    return rollups


def build_today_partial_rollup(
    df: pd.DataFrame,
    *,
    target_local_date: str,
    tz: str = "UTC",
    nominal_interval_seconds: float = 60.0,
    healthy_gap_cap_seconds: float = 60.0,
    fraction_of_day_elapsed: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build a rollup describing **today so far** — the partial day the LLM is
    being asked to compare against the historical baseline.

    Coverage is computed against the *elapsed* portion of the local day, not
    the full 86 400 s span, so a meter that has only reported for 6 hours of a
    24 h day reads as "covered for 6 hours" rather than "25 % covered".

    Parameters
    ----------
    df
        Same shape as :func:`build_daily_rollups`; rows whose local date does
        not match ``target_local_date`` are ignored.
    target_local_date
        ``YYYY-MM-DD`` in ``tz`` — typically the local date of the analysis
        end timestamp.
    fraction_of_day_elapsed
        Optional 0.0–1.0 hint. When supplied we use it directly to size the
        coverage denominator (``86 400 × fraction``). When ``None``, we fall
        back to ``last_sample - midnight_local``.

    Returns
    -------
    dict | None
        ``None`` when no samples land on the target local date.
    """
    if df is None or len(df) == 0 or "timestamp" not in df.columns:
        return None

    tz_name = _safe_tz(tz)
    work = df[["timestamp", "flow_rate"]].copy()
    if "quality" in df.columns:
        work["quality"] = df["quality"]
    else:
        work["quality"] = np.nan

    work["timestamp"] = pd.to_numeric(work["timestamp"], errors="coerce")
    work = work.dropna(subset=["timestamp"]).sort_values("timestamp")
    if work.empty:
        return None

    ts_arr = work["timestamp"].to_numpy(dtype=float)
    work["_local_date"] = _local_dates(ts_arr, tz_name).values
    today = work[work["_local_date"] == str(target_local_date)]
    if today.empty:
        return None

    today_ts = today["timestamp"].to_numpy(dtype=float)

    span_seconds: Optional[float]
    if fraction_of_day_elapsed is not None:
        try:
            frac = float(fraction_of_day_elapsed)
        except (TypeError, ValueError):
            frac = 0.0
        frac = max(0.0, min(1.0, frac))
        span_seconds = 86400.0 * frac
    else:
        # ``last_sample - midnight_local`` — derive midnight from the local-
        # tz-aware timestamp of the first sample we kept on this day.
        first_local = pd.to_datetime(
            today_ts[0], unit="s", utc=True
        ).tz_convert(tz_name)
        midnight_local = first_local.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        midnight_unix = midnight_local.timestamp()
        last_sample_unix = float(today_ts[-1])
        span_seconds = max(0.0, last_sample_unix - midnight_unix)

    return _build_one_rollup(
        local_date=str(target_local_date),
        tz=tz_name,
        timestamps=today_ts,
        flow_rate=today["flow_rate"].to_numpy(dtype=float),
        quality=today["quality"].to_numpy(dtype=float),
        nominal_interval_seconds=nominal_interval_seconds,
        healthy_gap_cap_seconds=healthy_gap_cap_seconds,
        span_seconds=span_seconds,
    )


def fraction_of_day_elapsed(
    *,
    end_timestamp: float,
    tz: str,
) -> float:
    """``end_timestamp - midnight_local(end_timestamp)`` as a fraction of 86 400 s.

    Helper exposed so callers can pass the same fraction to both this module
    (for coverage sizing) and :mod:`processors.baseline_quality` (for the
    ``fraction_of_day_elapsed`` projection gate). Clamped to [0, 1].
    """
    tz_name = _safe_tz(tz)
    end_local = pd.to_datetime(float(end_timestamp), unit="s", utc=True).tz_convert(
        tz_name
    )
    midnight_local = end_local.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = float(end_local.timestamp() - midnight_local.timestamp())
    return max(0.0, min(1.0, elapsed / 86400.0))


def today_missing_bucket_ratio(
    df: pd.DataFrame,
    *,
    target_local_date: str,
    tz: str,
    bucket_seconds: int = 3600,
    fraction_of_day_elapsed: Optional[float] = None,
) -> Optional[float]:
    """Share of the *elapsed* hour-buckets on ``target_local_date`` that have zero samples.

    Used by :func:`processors.baseline_quality.evaluate_baseline_quality`
    (the ``today_missing_bucket_ratio`` argument) to suppress projections when
    today's telemetry has too many holes. Returns ``None`` when no samples land
    on the target day or when ``fraction_of_day_elapsed`` is non-positive.
    """
    if df is None or len(df) == 0 or "timestamp" not in df.columns:
        return None
    if fraction_of_day_elapsed is None or fraction_of_day_elapsed <= 0:
        return None

    tz_name = _safe_tz(tz)
    work = df[["timestamp"]].copy()
    work["timestamp"] = pd.to_numeric(work["timestamp"], errors="coerce")
    work = work.dropna(subset=["timestamp"])
    if work.empty:
        return None

    ts_arr = work["timestamp"].to_numpy(dtype=float)
    work["_local_date"] = _local_dates(ts_arr, tz_name).values
    today = work[work["_local_date"] == str(target_local_date)]
    if today.empty:
        return 1.0

    first_local = pd.to_datetime(
        float(today["timestamp"].iloc[0]), unit="s", utc=True
    ).tz_convert(tz_name)
    midnight_unix = first_local.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()

    elapsed_seconds = 86400.0 * float(fraction_of_day_elapsed)
    expected_buckets = max(1, int(elapsed_seconds // bucket_seconds))

    today_ts = today["timestamp"].to_numpy(dtype=float)
    bucket_indices = ((today_ts - midnight_unix) // bucket_seconds).astype(int)
    occupied = {
        int(b)
        for b in bucket_indices
        if 0 <= int(b) < expected_buckets
    }
    missing = expected_buckets - len(occupied)
    return float(missing) / float(expected_buckets)


# ---------------------------------------------------------------------------
# Re-export for convenience: callers writing
# ``from processors.daily_rollup import DailyRollup`` should still work even
# though the canonical TypedDict lives in ``baseline_quality`` (so the refusal
# scaffolding stays self-contained).
# ---------------------------------------------------------------------------
try:  # pragma: no cover - import-time alias only
    from processors.baseline_quality import DailyRollup  # noqa: F401
except ImportError:  # pragma: no cover
    DailyRollup = Dict[str, Any]  # type: ignore[assignment,misc]
