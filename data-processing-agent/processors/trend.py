"""
Trend Processor

Linear regression and rolling statistics for characterising directional
behaviour and local volatility in the time series.
"""

from typing import Any, Dict

import numpy as np
import pandas as pd
from scipy import stats


def compute_linear_trend(
    timestamps: np.ndarray,
    values: np.ndarray,
) -> Dict[str, Any]:
    """
    Fit a simple linear regression to the time series.

    Time axis is normalised to seconds-since-start to avoid floating-point
    precision issues in OLS. Uses scipy.stats.linregress (exact closed-form OLS).

    Returns:
        slope_per_second:         Rate of change per second
        slope_per_minute:         Rate of change per minute (more readable for flow)
        intercept:                Estimated value at t=0 (series start)
        r_squared:                Coefficient of determination (0–1)
        p_value:                  Two-sided p-value for the slope (H0: slope == 0)
        std_error:                Standard error of the slope estimate
        trend_direction:          "increasing" | "decreasing" | "flat"
        statistically_significant: True if p_value < 0.05
    """
    clean_mask = ~np.isnan(values)
    t = (timestamps[clean_mask].astype(float) - timestamps[clean_mask][0])
    v = values[clean_mask]

    if len(t) < 2:
        return {"error": "Insufficient data points for linear regression"}

    slope, intercept, r_value, p_value, std_err = stats.linregress(t, v)

    if abs(slope) < 1e-12:
        direction = "flat"
    elif slope > 0:
        direction = "increasing"
    else:
        direction = "decreasing"

    return {
        "slope_per_second": float(slope),
        "slope_per_minute": float(slope * 60),
        "intercept": float(intercept),
        "r_squared": float(r_value**2),
        "p_value": float(p_value),
        "std_error": float(std_err),
        "trend_direction": direction,
        "statistically_significant": bool(p_value < 0.05),
    }


def compute_rolling_statistics(
    timestamps: np.ndarray,
    values: np.ndarray,
    window_size: int = 10,
) -> Dict[str, Any]:
    """
    Compute rolling mean and rolling standard deviation over a sliding window.

    Uses pandas.Series.rolling with min_periods=1 so edge values are still
    computed (with smaller effective windows at the boundaries).

    Returns summary statistics rather than raw arrays, making results
    suitable for LLM consumption.

    Returns:
        window_size:          Points per window
        rolling_mean_min:     Minimum of the rolling mean series
        rolling_mean_max:     Maximum of the rolling mean series
        rolling_mean_start:   Smoothed value at the beginning of the series
        rolling_mean_end:     Smoothed value at the end of the series
        average_volatility:   Mean of the rolling std (avg local spread)
        peak_volatility:      Maximum of the rolling std (most volatile window)
    """
    series = pd.Series(values.astype(float))
    rolling = series.rolling(window=window_size, min_periods=1)
    r_mean = rolling.mean().values
    r_std = rolling.std(ddof=1).values

    return {
        "window_size": window_size,
        "rolling_mean_min": float(np.nanmin(r_mean)),
        "rolling_mean_max": float(np.nanmax(r_mean)),
        "rolling_mean_start": float(r_mean[0]),
        "rolling_mean_end": float(r_mean[-1]),
        "average_volatility": float(np.nanmean(r_std)),
        "peak_volatility": float(np.nanmax(r_std)),
    }
