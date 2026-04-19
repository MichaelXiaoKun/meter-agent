"""
Baseline Quality Evaluator
==========================

Pure, deterministic "should we trust a baseline comparison right now?" evaluator.

This module is the **refusal scaffolding** for the historical-baseline feature
described in the design proposal. It is intentionally wired in **before** any
baseline pipeline (daily rollups, cache, projection) exists so that the default
behavior of the system when asked a baseline question — without any data — is
``reliable=false`` with an explicit reason, not a silent success.

Design invariants (do not break without review):

1. **Silence is an option.** Every return value has an explicit ``state`` that a
   caller (and the LLM) can relay verbatim instead of narrating around.
2. **Provenance.** Every verdict carries the counts it is based on
   (``n_days_candidate``, ``n_days_used``, ``days_rejected``) so reviewers can
   audit why a baseline was / was not trusted.
3. **Bright-line refusals.** Thresholds are explicit constants overridable via
   environment variables — no implicit heuristics buried elsewhere.
4. **No side effects.** No network, no filesystem, no pandas. Inputs are plain
   primitives / TypedDicts so the module is unit-testable in isolation.

The module does **not** compute a baseline. It only evaluates whether one, if
supplied, would be trustworthy, and why.
"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, TypedDict


# ---------------------------------------------------------------------------
# Shape contracts (kept here so downstream modules can import them)
# ---------------------------------------------------------------------------


class DailyRollup(TypedDict, total=False):
    """
    Minimal per-day rollup expected from the (future) ``daily_rollup`` processor.

    Only ``local_date``, ``tz``, and ``volume_gallons`` are required for baseline
    quality. Additional fields (``n_samples``, ``coverage_ratio``, etc.) are
    consulted when present to reject low-quality reference days.
    """

    local_date: str              # 'YYYY-MM-DD' in the meter's local TZ
    tz: str                      # IANA name e.g. 'America/New_York'
    volume_gallons: float
    n_samples: int
    coverage_ratio: float        # 0.0-1.0 share of the day covered by telemetry
    n_gaps: int
    low_quality_ratio: float     # 0.0-1.0 share of readings flagged low-quality
    weekday: int                 # 0=Monday, 6=Sunday


# ---------------------------------------------------------------------------
# Configuration (environment-overridable thresholds)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineQualityConfig:
    """Thresholds that decide refusal / reliability. Tune via env vars."""

    min_reference_days: int = 5
    min_same_weekday_days: int = 3
    max_reference_day_gap_ratio: float = 0.15     # drop day if > 15% missing coverage
    max_reference_day_lowq_ratio: float = 0.20    # drop day if > 20% low-quality reads
    mad_outlier_z: float = 3.5                    # drop daily totals > z MADs from median
    min_partial_day_fraction_for_projection: float = 0.20
    max_today_missing_bucket_ratio: float = 0.25
    min_post_change_days: int = 7                 # regime change must be this old
    cusum_shift_threshold: float = 2.0            # CUSUM sensitivity (in std units)

    @classmethod
    def from_env(cls) -> "BaselineQualityConfig":
        def _f(name: str, default: float) -> float:
            raw = os.environ.get(name)
            if raw is None or str(raw).strip() == "":
                return float(default)
            try:
                return float(raw)
            except ValueError:
                return float(default)

        def _i(name: str, default: int) -> int:
            raw = os.environ.get(name)
            if raw is None or str(raw).strip() == "":
                return int(default)
            try:
                return int(raw)
            except ValueError:
                return int(default)

        return cls(
            min_reference_days=_i(
                "BLUEBOT_BASELINE_MIN_DAYS", cls.min_reference_days
            ),
            min_same_weekday_days=_i(
                "BLUEBOT_BASELINE_MIN_WEEKDAY_DAYS", cls.min_same_weekday_days
            ),
            max_reference_day_gap_ratio=_f(
                "BLUEBOT_BASELINE_MAX_DAY_GAP_RATIO", cls.max_reference_day_gap_ratio
            ),
            max_reference_day_lowq_ratio=_f(
                "BLUEBOT_BASELINE_MAX_DAY_LOWQ_RATIO", cls.max_reference_day_lowq_ratio
            ),
            mad_outlier_z=_f("BLUEBOT_BASELINE_MAD_Z", cls.mad_outlier_z),
            min_partial_day_fraction_for_projection=_f(
                "BLUEBOT_BASELINE_MIN_DAY_FRACTION",
                cls.min_partial_day_fraction_for_projection,
            ),
            max_today_missing_bucket_ratio=_f(
                "BLUEBOT_BASELINE_MAX_TODAY_MISSING",
                cls.max_today_missing_bucket_ratio,
            ),
            min_post_change_days=_i(
                "BLUEBOT_BASELINE_MIN_POST_CHANGE_DAYS", cls.min_post_change_days
            ),
            cusum_shift_threshold=_f(
                "BLUEBOT_BASELINE_CUSUM_Z", cls.cusum_shift_threshold
            ),
        )


# ---------------------------------------------------------------------------
# States (exhaustive enum as plain strings, because we serialise to JSON)
# ---------------------------------------------------------------------------

STATE_NOT_REQUESTED = "not_requested"                     # no baseline was asked for
STATE_NO_HISTORY = "no_history"                           # nothing supplied at all
STATE_INSUFFICIENT_CLEAN_DAYS = "insufficient_clean_days"  # too few usable days after filtering
STATE_REGIME_CHANGE_TOO_RECENT = "regime_change_too_recent"
STATE_PARTIAL_TODAY_UNSUITABLE = "partial_today_unsuitable"
STATE_RELIABLE = "reliable"

_ALL_STATES = frozenset(
    {
        STATE_NOT_REQUESTED,
        STATE_NO_HISTORY,
        STATE_INSUFFICIENT_CLEAN_DAYS,
        STATE_REGIME_CHANGE_TOO_RECENT,
        STATE_PARTIAL_TODAY_UNSUITABLE,
        STATE_RELIABLE,
    }
)


@dataclass
class RejectedDay:
    local_date: str
    reason: str


@dataclass
class BaselineQualityResult:
    """Fully JSON-serialisable verdict (see :func:`to_dict`)."""

    state: str = STATE_NOT_REQUESTED
    reliable: bool = False
    reasons_refused: List[str] = field(default_factory=list)
    n_days_candidate: int = 0
    n_days_used: int = 0
    n_days_rejected: int = 0
    days_rejected: List[RejectedDay] = field(default_factory=list)
    n_same_weekday_days_used: Optional[int] = None
    change_point_detected: bool = False
    change_point_date: Optional[str] = None
    post_change_days: Optional[int] = None
    fraction_of_day_elapsed: Optional[float] = None
    today_missing_bucket_ratio: Optional[float] = None
    seasonal_drift_suspected: bool = False
    recommendations: List[str] = field(default_factory=list)
    config_used: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["days_rejected"] = [asdict(r) for r in self.days_rejected]
        return d


# ---------------------------------------------------------------------------
# Pure helpers (no pandas / numpy; easy to unit test)
# ---------------------------------------------------------------------------


def _median(values: Sequence[float]) -> float:
    vals = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    n = len(vals)
    if n == 0:
        return float("nan")
    if n % 2 == 1:
        return vals[n // 2]
    return 0.5 * (vals[n // 2 - 1] + vals[n // 2])


def _mad(values: Sequence[float], med: float) -> float:
    """Median absolute deviation (robust scale). 0 if degenerate."""
    devs = [abs(float(v) - med) for v in values if v is not None and math.isfinite(float(v))]
    if not devs:
        return 0.0
    return _median(devs)


def _mad_outlier_mask(values: Sequence[float], z: float) -> List[bool]:
    """
    ``True`` for values > ``z`` robust standard deviations from the median.

    Uses the standard 1.4826 MAD→σ consistency factor. Returns all-False when the
    MAD is zero (avoids divide-by-zero / false positives on constant series).
    """
    med = _median(values)
    mad = _mad(values, med)
    if mad <= 0 or not math.isfinite(mad):
        return [False] * len(values)
    scale = 1.4826 * mad
    return [
        (math.isfinite(float(v)) and abs(float(v) - med) / scale > z)
        for v in values
    ]


def _std(values: Sequence[float], mean: float) -> float:
    """Population standard deviation; 0 for degenerate input."""
    finite = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if len(finite) < 2:
        return 0.0
    var = sum((x - mean) ** 2 for x in finite) / len(finite)
    return math.sqrt(var)


def _simple_cusum_change_point(
    values: Sequence[float], shift_z: float
) -> Optional[int]:
    """
    Minimal CUSUM two-sided change-point detector on a 1-D series.

    Returns the index of the most recent suspected change point, or ``None``.

    Scale is MAD-based (robust) when available, falling back to population std.
    The fallback matters when more than half the series sits at one level —
    a legitimate regime change — which collapses MAD to zero. The outlier
    rejection pass that runs *before* this already handles single-day spikes,
    so std-based CUSUM is safe here.

    Reference point is the pre-change *mean* of the first few stable samples
    rather than the overall median, so a late shift still produces a large
    deviation (otherwise the post-change majority would pull the reference
    into the new regime and hide the change).
    """
    n = len(values)
    if n < 6:
        return None
    finite = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if len(finite) < 6:
        return None

    baseline_window = max(3, n // 3)
    ref = sum(finite[:baseline_window]) / baseline_window
    med = _median(finite)
    mad = _mad(finite, med)
    scale = 1.4826 * mad if mad > 0 else _std(finite, sum(finite) / len(finite))
    if scale <= 0 or not math.isfinite(scale):
        return None
    target = shift_z * scale

    # Return on the FIRST threshold crossing (not the last). For a persistent
    # shift, successive samples keep the CUSUM above the threshold — latching
    # on the most recent sample would wrongly report ``post_change_days=0``.
    s_pos = 0.0
    s_neg = 0.0
    for i, v in enumerate(values):
        if not math.isfinite(float(v)):
            continue
        d = float(v) - ref
        s_pos = max(0.0, s_pos + d - 0.5 * target)
        s_neg = min(0.0, s_neg + d + 0.5 * target)
        if s_pos > target or s_neg < -target:
            return i
    return None


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------


def evaluate_baseline_quality(
    reference_rollups: Optional[Sequence[DailyRollup]] = None,
    today_partial: Optional[DailyRollup] = None,
    *,
    target_weekday: Optional[int] = None,
    fraction_of_day_elapsed: Optional[float] = None,
    today_missing_bucket_ratio: Optional[float] = None,
    config: Optional[BaselineQualityConfig] = None,
) -> BaselineQualityResult:
    """
    Decide whether a baseline comparison built from ``reference_rollups`` would
    be reliable against ``today_partial``, and explain refusals explicitly.

    Arguments are all **optional** so this function can be used as a stub
    before the rest of the baseline pipeline exists. When no reference data is
    supplied, the result is ``STATE_NOT_REQUESTED`` — *not* an error.

    Parameters
    ----------
    reference_rollups
        Prior-day rollups (any TZ-aligned day list). ``None`` ⇒ baseline not
        requested; empty list ⇒ ``STATE_NO_HISTORY``.
    today_partial
        Optional rollup of today so far (same ``tz`` / ``local_date`` as the
        comparison target).
    target_weekday
        Optional 0-6 code for the day being compared. When provided, we also
        require at least ``config.min_same_weekday_days`` clean reference days
        with matching weekday.
    fraction_of_day_elapsed
        0.0-1.0 share of the local day already observed in ``today_partial``.
    today_missing_bucket_ratio
        0.0-1.0 share of today's expected hour-buckets that are missing data.
    config
        Overrides; defaults to :meth:`BaselineQualityConfig.from_env`.

    Returns
    -------
    :class:`BaselineQualityResult`
        Use :meth:`BaselineQualityResult.to_dict` before JSON serialisation.
    """
    cfg = config or BaselineQualityConfig.from_env()
    result = BaselineQualityResult(config_used=asdict(cfg))
    result.fraction_of_day_elapsed = fraction_of_day_elapsed
    result.today_missing_bucket_ratio = today_missing_bucket_ratio

    # ---- gate 0: nothing was asked for ------------------------------------
    if reference_rollups is None:
        result.state = STATE_NOT_REQUESTED
        return result

    rollups: List[DailyRollup] = [r for r in reference_rollups if isinstance(r, dict)]
    result.n_days_candidate = len(rollups)

    if not rollups:
        result.state = STATE_NO_HISTORY
        result.reasons_refused.append("No reference days supplied.")
        result.recommendations.append(
            "Fetch at least "
            f"{cfg.min_reference_days} historical days before requesting a baseline."
        )
        return result

    # ---- gate 1: drop reference days with poor coverage / quality ---------
    # Runs before regime-change detection so missing-telemetry days can't be
    # mistaken for a shift.
    clean: List[DailyRollup] = []
    for r in rollups:
        date_str = str(r.get("local_date", "") or "unknown")
        cov = _maybe_float(r.get("coverage_ratio"))
        lowq = _maybe_float(r.get("low_quality_ratio"))
        vol = _maybe_float(r.get("volume_gallons"))
        if cov is not None and cov < (1.0 - cfg.max_reference_day_gap_ratio):
            result.days_rejected.append(
                RejectedDay(date_str, f"coverage_ratio={cov:.2f} below threshold")
            )
            continue
        if lowq is not None and lowq > cfg.max_reference_day_lowq_ratio:
            result.days_rejected.append(
                RejectedDay(date_str, f"low_quality_ratio={lowq:.2f} above threshold")
            )
            continue
        if vol is None or not math.isfinite(vol):
            result.days_rejected.append(RejectedDay(date_str, "missing volume_gallons"))
            continue
        clean.append(r)

    # ---- gate 2: regime-change detection (CUSUM on daily totals) ---------
    # Runs BEFORE MAD outlier rejection: otherwise the minority pre-change days
    # look like outliers and get removed, hiding the shift from the detector.
    if len(clean) >= 6:
        idx = _simple_cusum_change_point(
            [float(r["volume_gallons"]) for r in clean],
            cfg.cusum_shift_threshold,
        )
        if idx is not None:
            post_change_days = len(clean) - idx - 1
            result.change_point_detected = True
            result.change_point_date = str(
                clean[idx].get("local_date", "") or "unknown"
            )
            result.post_change_days = post_change_days
            if post_change_days < cfg.min_post_change_days:
                result.n_days_used = len(clean)
                result.n_days_rejected = len(result.days_rejected)
                result.state = STATE_REGIME_CHANGE_TOO_RECENT
                result.reasons_refused.append(
                    "Recent change-point at "
                    f"{result.change_point_date}; only {post_change_days} days "
                    f"since (< {cfg.min_post_change_days})."
                )
                result.recommendations.append(
                    "Use only post-change days, or wait until at least "
                    f"{cfg.min_post_change_days} new days have accumulated."
                )
                return result
            # Change is old enough — truncate to post-change days.
            for dropped in clean[: idx + 1]:
                result.days_rejected.append(
                    RejectedDay(
                        str(dropped.get("local_date", "") or "unknown"),
                        f"pre-change-point at {result.change_point_date}",
                    )
                )
            clean = clean[idx + 1 :]
            result.recommendations.append(
                "Baseline truncated to post-change-point days "
                f"(change at {result.change_point_date})."
            )

    # ---- gate 3: robust outlier rejection on the (post-truncation) series -
    totals = [float(r["volume_gallons"]) for r in clean]
    if len(totals) >= 4:
        mask = _mad_outlier_mask(totals, cfg.mad_outlier_z)
        kept: List[DailyRollup] = []
        for r, is_outlier in zip(clean, mask):
            if is_outlier:
                result.days_rejected.append(
                    RejectedDay(
                        str(r.get("local_date", "") or "unknown"),
                        "daily total is a robust outlier (MAD filter)",
                    )
                )
            else:
                kept.append(r)
        clean = kept

    result.n_days_used = len(clean)
    result.n_days_rejected = len(result.days_rejected)

    # ---- gate 4: not enough clean days -----------------------------------
    if result.n_days_used < cfg.min_reference_days:
        result.state = STATE_INSUFFICIENT_CLEAN_DAYS
        result.reasons_refused.append(
            f"Only {result.n_days_used} clean reference days "
            f"(need ≥ {cfg.min_reference_days})."
        )
        result.recommendations.append(
            "Wait for more history, lower BLUEBOT_BASELINE_MIN_DAYS explicitly, "
            "or relax the per-day coverage/quality thresholds."
        )
        return result

    if target_weekday is not None:
        same = sum(1 for r in clean if _maybe_int(r.get("weekday")) == target_weekday)
        result.n_same_weekday_days_used = same
        if same < cfg.min_same_weekday_days:
            result.state = STATE_INSUFFICIENT_CLEAN_DAYS
            result.reasons_refused.append(
                f"Only {same} matching-weekday days "
                f"(need ≥ {cfg.min_same_weekday_days})."
            )
            result.recommendations.append(
                "Disable weekday matching, or extend the reference window."
            )
            return result

    # ---- gate 5: partial-today suitability (only relevant if projecting) --
    if today_partial is not None:
        if (
            fraction_of_day_elapsed is not None
            and fraction_of_day_elapsed < cfg.min_partial_day_fraction_for_projection
        ):
            result.state = STATE_PARTIAL_TODAY_UNSUITABLE
            result.reasons_refused.append(
                f"Only {fraction_of_day_elapsed:.0%} of the local day elapsed "
                f"(need ≥ {cfg.min_partial_day_fraction_for_projection:.0%} "
                "for a point projection)."
            )
            result.recommendations.append(
                "Report a band-only projection, or retry later in the day."
            )
            return result
        if (
            today_missing_bucket_ratio is not None
            and today_missing_bucket_ratio > cfg.max_today_missing_bucket_ratio
        ):
            result.state = STATE_PARTIAL_TODAY_UNSUITABLE
            result.reasons_refused.append(
                f"Today is missing {today_missing_bucket_ratio:.0%} of expected "
                f"buckets (> {cfg.max_today_missing_bucket_ratio:.0%}); "
                "the projection would extrapolate across gaps."
            )
            result.recommendations.append(
                "Suppress projection until the telemetry stream catches up."
            )
            return result

    # ---- everything else: the baseline is trustworthy ---------------------
    result.state = STATE_RELIABLE
    result.reliable = True
    return result


# ---------------------------------------------------------------------------
# Tiny helpers
# ---------------------------------------------------------------------------


def _maybe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _maybe_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def not_requested_stub() -> Dict[str, Any]:
    """
    Convenience: a pre-built JSON verdict for "baseline not yet computed".

    Use this in the analysis pipeline until the baseline feature is wired, so
    the output schema is already stable.
    """
    return evaluate_baseline_quality(reference_rollups=None).to_dict()
