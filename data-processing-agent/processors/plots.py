"""
plots.py — Plot generation processor for flow rate time series.

Each function saves a PNG under the plots directory (see PLOTS_DIR) and registers
the figure so the caller can invoke plt.show() for interactive display in standalone mode.

PLOTS_DIR matches the orchestrator FastAPI default (and BLUEBOT PLOTS_DIR env) so
saved files are visible to GET /api/plots/{filename}.

Public API:
    generate_plot(plot_type, timestamps, values, quality, serial_number, start) -> dict
    pop_figures() -> list[tuple[Figure, str]]   # (figure, absolute_path) pairs
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

# data-processing-agent/ (parent of processors/)
_PKG_ROOT = Path(__file__).resolve().parent.parent
# Repo root (contains orchestrator/ and data-processing-agent/) — same as orchestrator/api.py parent.parent
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_env_plots = os.environ.get("PLOTS_DIR")
if _env_plots:
    p = Path(_env_plots).expanduser()
    if p.is_absolute():
        _PLOTS_DIR = str(p.resolve())
    else:
        # Relative to repo root (not cwd), so subprocess matches FastAPI when cwd differs.
        _PLOTS_DIR = str((_REPO_ROOT / p).resolve())
else:
    _PLOTS_DIR = str((_PKG_ROOT / "plots").resolve())

# Accumulates (figure, path) pairs produced during one analyze() call.
# Cleared by pop_figures() after the caller has consumed them.
_pending: list[tuple] = []


def pop_figures() -> list[tuple]:
    """Return accumulated (figure, path) pairs and clear the list."""
    result = _pending.copy()
    _pending.clear()
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _save(fig, serial_number: str, start: float, plot_type: str) -> str:
    os.makedirs(_PLOTS_DIR, exist_ok=True)
    filename = f"{serial_number}_{int(start)}_{plot_type}.png"
    path = os.path.join(_PLOTS_DIR, filename)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    return os.path.abspath(path)


def _to_datetimes(timestamps: np.ndarray) -> list:
    return [datetime.fromtimestamp(float(t), tz=timezone.utc) for t in timestamps]


def _format_xaxis(ax, timestamps: np.ndarray) -> None:
    span_hours = (timestamps[-1] - timestamps[0]) / 3600
    if span_hours <= 6:
        fmt = "%H:%M"
    elif span_hours <= 48:
        fmt = "%d %b %H:%M"
    else:
        fmt = "%d %b"
    ax.xaxis.set_major_formatter(mdates.DateFormatter(fmt, tz=timezone.utc))
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right")


# ---------------------------------------------------------------------------
# Plot type: time_series
# ---------------------------------------------------------------------------

def _time_series(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
    start: float,
) -> dict:
    fig, ax = plt.subplots(figsize=(12, 4))
    dts = _to_datetimes(timestamps)

    ax.plot(dts, values, color="#2563eb", linewidth=0.8, label="Flow rate")

    low_q_mask = (~np.isnan(quality)) & (quality <= 60)
    if low_q_mask.any():
        low_dts = [dts[i] for i in np.where(low_q_mask)[0]]
        ax.scatter(
            low_dts, values[low_q_mask],
            color="#dc2626", s=14, zorder=5,
            label=f"Low quality (≤60): {low_q_mask.sum()} pts",
        )

    ax.set_title(f"Flow Rate — {serial_number}", fontsize=11)
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Flow Rate (gal/min)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    _format_xaxis(ax, timestamps)
    fig.tight_layout()

    path = _save(fig, serial_number, start, "time_series")
    _pending.append((fig, path))
    return {
        "path": path,
        "title": "Flow Rate Time Series",
        "low_quality_points_highlighted": int(low_q_mask.sum()),
    }


# ---------------------------------------------------------------------------
# Plot type: flow_duration_curve
# ---------------------------------------------------------------------------

def _flow_duration_curve(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
    start: float,
) -> dict:
    clean = values[~np.isnan(values)]
    if len(clean) == 0:
        return {"error": "No valid flow values to plot."}

    sorted_vals = np.sort(clean)[::-1]
    exceedance = np.linspace(0, 100, len(sorted_vals))

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(exceedance, sorted_vals, color="#2563eb", linewidth=1.5)

    for pct, label in [(10, "Q10"), (50, "Q50"), (90, "Q90")]:
        ax.axvline(x=pct, color="#94a3b8", linestyle="--", linewidth=0.8)
        ax.text(
            pct + 0.8, 0.96, label,
            transform=ax.get_xaxis_transform(),
            fontsize=7, color="#64748b", va="top",
        )

    ax.set_title(f"Flow Duration Curve — {serial_number}", fontsize=11)
    ax.set_xlabel("Exceedance Probability (%)")
    ax.set_ylabel("Flow Rate (gal/min)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    path = _save(fig, serial_number, start, "flow_duration_curve")
    _pending.append((fig, path))
    return {"path": path, "title": "Flow Duration Curve"}


# ---------------------------------------------------------------------------
# Plot type: peaks_annotated
# ---------------------------------------------------------------------------

def _peaks_annotated(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
    start: float,
) -> dict:
    from scipy.signal import find_peaks

    fig, ax = plt.subplots(figsize=(12, 4))
    dts = _to_datetimes(timestamps)

    ax.plot(dts, values, color="#2563eb", linewidth=0.8)

    peak_indices = np.array([], dtype=int)
    std = float(np.nanstd(values))
    if std > 0:
        peak_indices, _ = find_peaks(values, prominence=std)

    if len(peak_indices):
        peak_dts = [dts[i] for i in peak_indices]
        peak_vals = values[peak_indices]
        ax.scatter(
            peak_dts, peak_vals,
            color="#f59e0b", s=40, zorder=5,
            label=f"{len(peak_indices)} peaks",
        )
        # Annotate the top 5 peaks by value
        top_idx = peak_indices[np.argsort(peak_vals)[-min(5, len(peak_indices)):]]
        for i in top_idx:
            ax.annotate(
                f"{values[i]:.1f}",
                xy=(dts[i], values[i]),
                xytext=(0, 8), textcoords="offset points",
                fontsize=7, ha="center", color="#92400e",
            )
        ax.legend(fontsize=8)
    else:
        ax.text(
            0.5, 0.95, "No significant peaks detected",
            transform=ax.transAxes, ha="center", va="top",
            fontsize=9, color="#64748b",
        )

    ax.set_title(f"Flow Rate with Peak Annotations — {serial_number}", fontsize=11)
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Flow Rate (gal/min)")
    ax.grid(True, alpha=0.3)
    _format_xaxis(ax, timestamps)
    fig.tight_layout()

    path = _save(fig, serial_number, start, "peaks_annotated")
    _pending.append((fig, path))
    return {"path": path, "title": "Peaks Annotated", "peak_count": len(peak_indices)}


# ---------------------------------------------------------------------------
# Plot type: signal_quality
# ---------------------------------------------------------------------------

def _signal_quality(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
    start: float,
) -> dict:
    valid_mask = ~np.isnan(quality)
    if not valid_mask.any():
        return {"error": "No quality scores available to plot."}

    dts     = _to_datetimes(timestamps)
    q_dts   = [dts[i] for i in np.where(valid_mask)[0]]
    q_vals  = quality[valid_mask]

    fig, ax = plt.subplots(figsize=(12, 3))

    ax.plot(q_dts, q_vals, color="#7c3aed", linewidth=0.9, label="Signal quality")
    ax.axhline(y=60, color="#dc2626", linewidth=1.0, linestyle="--", label="Threshold (60)")

    # Shade the low-quality region
    ax.fill_between(
        q_dts, q_vals, 60,
        where=[v <= 60 for v in q_vals],
        alpha=0.25, color="#dc2626", label="Low quality zone",
    )

    low_count = int((q_vals <= 60).sum())
    ax.set_title(
        f"Signal Quality — {serial_number}  "
        f"({low_count} low-quality pts ≤60 of {len(q_vals)})",
        fontsize=11,
    )
    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Quality score")
    ax.set_ylim(0, 100)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    _format_xaxis(ax, timestamps[valid_mask])
    fig.tight_layout()

    path = _save(fig, serial_number, start, "signal_quality")
    _pending.append((fig, path))
    return {
        "path": path,
        "title": "Signal Quality",
        "low_quality_count": low_count,
        "total_count": int(len(q_vals)),
    }


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

_HANDLERS = {
    "time_series":         _time_series,
    "flow_duration_curve": _flow_duration_curve,
    "peaks_annotated":     _peaks_annotated,
    "signal_quality":      _signal_quality,
}


def generate_plot(
    plot_type: str,
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
    start: float,
) -> dict:
    """
    Generate and save a chart as a PNG file.

    Registers the figure for plt.show() via pop_figures().
    Returns {"path": str, ...} on success or {"error": str} on failure.
    """
    handler = _HANDLERS.get(plot_type)
    if handler is None:
        return {
            "error": (
                f"Unknown plot_type '{plot_type}'. "
                f"Valid options: {list(_HANDLERS)}"
            )
        }
    return handler(timestamps, values, quality, serial_number, start)
