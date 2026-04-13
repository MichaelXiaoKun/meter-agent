"""
Descriptive Statistics Processor

All computations use numpy with explicit ddof=1 (sample statistics).
No values are inferred or estimated — only what the data contains.
"""

from typing import Any, Dict, Optional

import numpy as np


def compute_descriptive_stats(values: np.ndarray) -> Dict[str, Any]:
    """
    Compute a full suite of descriptive statistics for a 1-D numeric array.

    Uses sample standard deviation (ddof=1) throughout.
    NaN values are excluded from all calculations and counted separately.

    Returns:
        count:          Total number of data points (including NaNs)
        valid_count:    Number of non-NaN data points
        null_count:     Number of NaN data points
        mean:           Arithmetic mean
        median:         50th percentile (middle value)
        std:            Sample standard deviation (ddof=1)
        variance:       Sample variance (ddof=1)
        min:            Minimum value
        max:            Maximum value
        range:          max - min
        p25:            25th percentile (Q1)
        p75:            75th percentile (Q3)
        p95:            95th percentile
        iqr:            Interquartile range (Q3 - Q1)
        cv:             Coefficient of variation (std / mean), None if mean == 0
    """
    clean = values[~np.isnan(values)]

    if len(clean) == 0:
        raise ValueError("No valid (non-NaN) values to compute statistics on.")

    p25 = float(np.percentile(clean, 25))
    p75 = float(np.percentile(clean, 75))
    mean = float(np.mean(clean))
    std = float(np.std(clean, ddof=1)) if len(clean) > 1 else 0.0

    return {
        "count": int(len(values)),
        "valid_count": int(len(clean)),
        "null_count": int(len(values) - len(clean)),
        "mean": mean,
        "median": float(np.median(clean)),
        "std": std,
        "variance": float(np.var(clean, ddof=1)) if len(clean) > 1 else 0.0,
        "min": float(np.min(clean)),
        "max": float(np.max(clean)),
        "range": float(np.max(clean) - np.min(clean)),
        "p25": p25,
        "p75": p75,
        "p95": float(np.percentile(clean, 95)),
        "iqr": p75 - p25,
        "cv": (std / mean) if mean != 0 else None,
    }
