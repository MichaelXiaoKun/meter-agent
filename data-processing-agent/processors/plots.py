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
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

from processors.plot_captions import (
    caption_flow_duration_curve,
    caption_peaks_annotated,
    caption_signal_quality,
    caption_time_series,
)
from processors.plot_diagnostics import build_diagnostic_markers, diagnostic_caption
from processors.sampling_physics import max_healthy_inter_arrival_seconds

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

# Captions keyed by absolute path. Populated in parallel with ``_pending`` so
# callers that only care about figures can keep using the 2-tuple unchanged,
# while the CLI / orchestrator can call ``pop_captions()`` for the side-car
# metadata that helps text-only LLMs "read" the chart.
_pending_captions: dict[str, dict] = {}


def pop_figures() -> list[tuple]:
    """Return accumulated (figure, path) pairs and clear the list."""
    result = _pending.copy()
    _pending.clear()
    return result


def pop_captions() -> dict[str, dict]:
    """Return captions-by-path produced in this analyze() call and clear."""
    out = dict(_pending_captions)
    _pending_captions.clear()
    return out


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


def _to_datetimes_nan_aware(timestamps: np.ndarray) -> np.ndarray:
    """
    Convert unix-second timestamps to a numpy ``datetime64[ns]`` array, mapping
    ``NaN`` to ``NaT``. Matplotlib breaks lines at ``NaT`` the same way it
    does at ``NaN`` y-values, which is exactly what ``_series_with_gap_breaks``
    relies on to avoid drawing interpolated segments through real outages.

    We deliberately return a *tz-naive* datetime64 array (logically UTC) instead
    of a tz-aware ``DatetimeIndex``; matplotlib's date converter chokes on
    ``NaT`` values inside tz-aware ``Timestamp`` arrays but handles them cleanly
    in the naive datetime64 path.
    """
    ts = np.asarray(timestamps, dtype=float)
    out = np.empty(len(ts), dtype="datetime64[ns]")
    if len(ts) == 0:
        return out
    nan_mask = np.isnan(ts)
    if (~nan_mask).any():
        ns = (ts[~nan_mask] * 1e9).astype("int64")
        out[~nan_mask] = ns.astype("datetime64[ns]")
    out[nan_mask] = np.datetime64("NaT")
    return out


def _series_with_gap_breaks(
    timestamps: np.ndarray,
    values: np.ndarray,
    *,
    cap_seconds: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Return ``(timestamps, values)`` with a ``NaN`` row inserted between any two
    consecutive samples whose spacing exceeds ``cap_seconds``.

    Matplotlib's line plotter renders a single connected polyline through
    everything you hand it, so a 5-hour outage shows up as a smooth diagonal
    segment connecting the last pre-gap reading to the first post-gap reading.
    That actively contradicts the verified-facts bundle, which always knows
    the gap is there. Inserting a ``NaN`` row breaks the line at the exact
    place data was missing, so the plot stops lying.

    The cap defaults to ``max_healthy_inter_arrival_seconds()`` so the same
    network-aware rule that drives gap detection drives the visual break — a
    Wi-Fi meter breaks on > 5 s pauses; a LoRaWAN meter only on > 60 s.
    """
    ts = np.asarray(timestamps, dtype=float)
    vals = np.asarray(values, dtype=float)
    if len(ts) < 2:
        return ts, vals

    cap = max_healthy_inter_arrival_seconds() if cap_seconds is None else float(cap_seconds)
    deltas = np.diff(ts)
    breaks = np.where(deltas > cap)[0]
    if len(breaks) == 0:
        return ts, vals

    out_ts = np.insert(ts, breaks + 1, np.nan)
    out_v = np.insert(vals, breaks + 1, np.nan)
    return out_ts, out_v


def resolve_plot_tz(tz_name: str | None = None) -> tzinfo:
    """
    Resolve the timezone the plots should render in.

    Precedence (first valid IANA name wins):

    1. Explicit ``tz_name`` argument (call site override).
    2. ``BLUEBOT_PLOT_TZ`` env var (set by the orchestrator from the meter's
       ``deviceTimeZone`` or the user's browser timezone).
    3. UTC fallback. Returned as :class:`datetime.timezone.utc`, never ``None``.

    Unknown / malformed names silently fall back to UTC; callers that need to
    distinguish "explicit UTC" from "fallback UTC" should use
    :func:`describe_plot_tz`.
    """
    candidates = [tz_name, os.environ.get("BLUEBOT_PLOT_TZ")]
    for name in candidates:
        if not name:
            continue
        n = str(name).strip()
        if not n:
            continue
        if n.upper() == "UTC":
            return timezone.utc
        try:
            return ZoneInfo(n)
        except ZoneInfoNotFoundError:
            continue
    return timezone.utc


def describe_plot_tz(tz_name: str | None = None) -> dict:
    """Return the resolved zone name + a short axis label for a reference timestamp.

    Used both by tests and by the axis-label code so the fallback story is the
    same everywhere: explicit IANA → short zone code at the reference instant
    (e.g. ``"MDT"``); UTC fallback → ``"UTC — meter timezone unknown"`` so the
    label can never be silently misread as local time.
    """
    tz = resolve_plot_tz(tz_name)
    if tz is timezone.utc:
        explicit_utc = bool(
            (tz_name and tz_name.strip().upper() == "UTC")
            or (os.environ.get("BLUEBOT_PLOT_TZ", "").strip().upper() == "UTC")
        )
        label = "UTC" if explicit_utc else "UTC — meter timezone unknown"
        return {"zone": "UTC", "label": label, "tzinfo": tz}
    iana = str(getattr(tz, "key", tz_name or ""))
    return {"zone": iana, "label": iana, "tzinfo": tz}


def _xaxis_label(tz: tzinfo, ref_unix_seconds: float, fallback_label: str) -> str:
    """Build the ``Time (XYZ)`` x-axis label, preferring a short zone code."""
    try:
        short = datetime.fromtimestamp(float(ref_unix_seconds), tz=tz).strftime("%Z")
    except (OverflowError, OSError, ValueError):
        short = ""
    short = short.strip()
    return f"Time ({short})" if short else f"Time ({fallback_label})"


def _format_xaxis(ax, timestamps: np.ndarray, tz: tzinfo | None = None) -> None:
    span_hours = (timestamps[-1] - timestamps[0]) / 3600
    if span_hours <= 6:
        fmt = "%H:%M"
    elif span_hours <= 48:
        fmt = "%d %b %H:%M"
    else:
        fmt = "%d %b"
    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(fmt, tz=tz if tz is not None else timezone.utc)
    )
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
    tz_name: str | None = None,
) -> dict:
    tz_info = describe_plot_tz(tz_name)
    tz = tz_info["tzinfo"]

    fig, ax = plt.subplots(figsize=(12, 4))
    dts = _to_datetimes(timestamps)

    # Connected line is drawn over a NaN-broken copy so real outages render as
    # gaps instead of interpolated diagonals; per-sample scatter overlays still
    # use the unbroken arrays so individual markers stay at their true times.
    line_ts, line_v = _series_with_gap_breaks(timestamps, values)
    ax.plot(
        _to_datetimes_nan_aware(line_ts), line_v,
        color="#2563eb", linewidth=0.8, label="Flow rate",
    )

    low_q_mask = (~np.isnan(quality)) & (quality <= 60)
    if low_q_mask.any():
        low_dts = [dts[i] for i in np.where(low_q_mask)[0]]
        ax.scatter(
            low_dts, values[low_q_mask],
            color="#dc2626", s=14, zorder=5,
            label=f"Low quality (≤60): {low_q_mask.sum()} pts",
        )

    ax.set_title(f"Flow Rate — {serial_number}", fontsize=11)
    ax.set_xlabel(_xaxis_label(tz, float(timestamps[0]), tz_info["label"]))
    ax.set_ylabel("Flow Rate (gal/min)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    _format_xaxis(ax, timestamps, tz=tz)
    fig.tight_layout()

    path = _save(fig, serial_number, start, "time_series")
    _pending.append((fig, path))
    caption = caption_time_series(
        timestamps,
        values,
        quality,
        healthy_cap_seconds=max_healthy_inter_arrival_seconds(),
    )
    _pending_captions[path] = caption
    return {
        "path": path,
        "title": "Flow Rate Time Series",
        "low_quality_points_highlighted": int(low_q_mask.sum()),
        "tz": tz_info["zone"],
        "caption": caption,
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
    caption = caption_flow_duration_curve(values)
    _pending_captions[path] = caption
    return {
        "path": path,
        "title": "Flow Duration Curve",
        "caption": caption,
    }


# ---------------------------------------------------------------------------
# Plot type: peaks_annotated
# ---------------------------------------------------------------------------

def _peaks_annotated(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
    start: float,
    tz_name: str | None = None,
) -> dict:
    from scipy.signal import find_peaks

    tz_info = describe_plot_tz(tz_name)
    tz = tz_info["tzinfo"]

    fig, ax = plt.subplots(figsize=(12, 4))
    dts = _to_datetimes(timestamps)

    # Same gap-break treatment as _time_series: peak markers stay on the
    # unbroken series so they keep their true coordinates.
    line_ts, line_v = _series_with_gap_breaks(timestamps, values)
    ax.plot(
        _to_datetimes_nan_aware(line_ts), line_v,
        color="#2563eb", linewidth=0.8,
    )

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
    ax.set_xlabel(_xaxis_label(tz, float(timestamps[0]), tz_info["label"]))
    ax.set_ylabel("Flow Rate (gal/min)")
    ax.grid(True, alpha=0.3)
    _format_xaxis(ax, timestamps, tz=tz)
    fig.tight_layout()

    path = _save(fig, serial_number, start, "peaks_annotated")
    _pending.append((fig, path))
    caption = caption_peaks_annotated(
        timestamps, values, peak_count=int(len(peak_indices))
    )
    _pending_captions[path] = caption
    return {
        "path": path,
        "title": "Peaks Annotated",
        "peak_count": len(peak_indices),
        "tz": tz_info["zone"],
        "caption": caption,
    }


# ---------------------------------------------------------------------------
# Plot type: signal_quality
# ---------------------------------------------------------------------------

def _signal_quality(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
    start: float,
    tz_name: str | None = None,
) -> dict:
    valid_mask = ~np.isnan(quality)
    if not valid_mask.any():
        return {"error": "No quality scores available to plot."}

    tz_info = describe_plot_tz(tz_name)
    tz = tz_info["tzinfo"]

    q_ts_raw = timestamps[valid_mask].astype(float)
    q_vals_raw = quality[valid_mask].astype(float)

    # Break the quality line at gaps the same way we break flow_rate, so a
    # transmission outage doesn't render as a smooth quality signal.
    q_ts_broken, q_vals_broken = _series_with_gap_breaks(q_ts_raw, q_vals_raw)
    q_dts = _to_datetimes_nan_aware(q_ts_broken)

    fig, ax = plt.subplots(figsize=(12, 3))

    ax.plot(q_dts, q_vals_broken, color="#7c3aed", linewidth=0.9, label="Signal quality")
    ax.axhline(y=60, color="#dc2626", linewidth=1.0, linestyle="--", label="Threshold (60)")

    # Shade the low-quality region (NaN values exclude themselves naturally).
    low_mask = np.where(np.isnan(q_vals_broken), False, q_vals_broken <= 60)
    ax.fill_between(
        q_dts, q_vals_broken, 60,
        where=low_mask,
        alpha=0.25, color="#dc2626", label="Low quality zone",
    )

    q_vals = q_vals_raw  # for the title's low-count summary, use real samples only

    low_count = int((q_vals <= 60).sum())
    ax.set_title(
        f"Signal Quality — {serial_number}  "
        f"({low_count} low-quality pts ≤60 of {len(q_vals)})",
        fontsize=11,
    )
    ax.set_xlabel(_xaxis_label(tz, float(q_ts_raw[0]), tz_info["label"]))
    ax.set_ylabel("Quality score")
    ax.set_ylim(0, 100)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    _format_xaxis(ax, timestamps[valid_mask], tz=tz)
    fig.tight_layout()

    path = _save(fig, serial_number, start, "signal_quality")
    _pending.append((fig, path))
    caption = caption_signal_quality(quality)
    _pending_captions[path] = caption
    return {
        "path": path,
        "title": "Signal Quality",
        "low_quality_count": low_count,
        "total_count": int(len(q_vals)),
        "tz": tz_info["zone"],
        "caption": caption,
    }


# ---------------------------------------------------------------------------
# Plot type: diagnostic_timeline
# ---------------------------------------------------------------------------

def _dedup_legend(ax) -> None:
    handles, labels = ax.get_legend_handles_labels()
    seen = set()
    kept_handles = []
    kept_labels = []
    for handle, label in zip(handles, labels):
        if not label or label in seen:
            continue
        seen.add(label)
        kept_handles.append(handle)
        kept_labels.append(label)
    if kept_handles:
        ax.legend(kept_handles, kept_labels, fontsize=8, loc="upper left")


def _diagnostic_timeline(
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
    start: float,
    tz_name: str | None = None,
    verified_facts: dict | None = None,
) -> dict:
    if len(timestamps) == 0:
        return {"error": "No flow values to plot."}

    tz_info = describe_plot_tz(tz_name)
    tz = tz_info["tzinfo"]
    markers = build_diagnostic_markers(timestamps, values, quality, verified_facts)

    fig, ax = plt.subplots(figsize=(12, 4.6))
    line_ts, line_v = _series_with_gap_breaks(timestamps, values)
    ax.plot(
        _to_datetimes_nan_aware(line_ts),
        line_v,
        color="#2563eb",
        linewidth=0.85,
        label="Flow rate",
    )

    valid_values = values[~np.isnan(values)]
    y_min = float(np.nanmin(valid_values)) if len(valid_values) else 0.0
    y_max = float(np.nanmax(valid_values)) if len(valid_values) else 1.0
    if y_min == y_max:
        y_max = y_min + 1.0
    y_span = max(y_max - y_min, 1.0)
    text_y = y_max + y_span * 0.04

    for marker in markers:
        kind = marker.get("type")
        severity = str(marker.get("severity") or "low")
        alpha = {"high": 0.22, "medium": 0.16, "low": 0.10}.get(severity, 0.10)

        if kind == "gap":
            s = marker.get("start")
            e = marker.get("end")
            if s is not None and e is not None:
                ax.axvspan(
                    datetime.fromtimestamp(float(s), tz=timezone.utc),
                    datetime.fromtimestamp(float(e), tz=timezone.utc),
                    color="#64748b",
                    alpha=alpha,
                    label="Missing data",
                )
        elif kind == "low_quality":
            s = marker.get("start")
            e = marker.get("end")
            if s is not None and e is not None:
                ax.axvspan(
                    datetime.fromtimestamp(float(s), tz=timezone.utc),
                    datetime.fromtimestamp(float(e), tz=timezone.utc),
                    color="#dc2626",
                    alpha=alpha,
                    label="Low signal quality",
                )
        elif kind == "drift":
            ts = marker.get("timestamp")
            if ts is not None:
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                ax.axvline(dt, color="#f97316", linewidth=1.5, linestyle="--", label="Drift alarm")
                ax.annotate(
                    str(marker.get("label") or "Drift alarm"),
                    xy=(dt, text_y),
                    xytext=(4, 0),
                    textcoords="offset points",
                    fontsize=8,
                    color="#9a3412",
                    va="bottom",
                )
        elif kind in {"flatline", "baseline"}:
            s = marker.get("start")
            e = marker.get("end")
            if s is not None and e is not None:
                color = "#8b5cf6" if kind == "flatline" else "#0891b2"
                label = "Near-constant flow" if kind == "flatline" else "Possible baseline rise"
                ax.axvspan(
                    datetime.fromtimestamp(float(s), tz=timezone.utc),
                    datetime.fromtimestamp(float(e), tz=timezone.utc),
                    color=color,
                    alpha=0.08 if kind == "flatline" else 0.10,
                    label=label,
                )
                if kind == "baseline":
                    quiet = (verified_facts or {}).get("quiet_flow_baseline")
                    if isinstance(quiet, dict) and quiet.get("quiet_flow_median") is not None:
                        try:
                            median = float(quiet["quiet_flow_median"])
                            ax.axhline(
                                median,
                                color=color,
                                linewidth=1.0,
                                linestyle=":",
                                label="Quiet-flow baseline",
                            )
                        except (TypeError, ValueError):
                            pass

    if not markers:
        ax.text(
            0.5,
            0.92,
            "No diagnostic markers in this window",
            transform=ax.transAxes,
            ha="center",
            va="top",
            fontsize=9,
            color="#64748b",
        )

    ax.set_title(f"Diagnostic Timeline — {serial_number}", fontsize=11)
    ax.set_xlabel(_xaxis_label(tz, float(timestamps[0]), tz_info["label"]))
    ax.set_ylabel("Flow Rate (gal/min)")
    ax.set_ylim(y_min - y_span * 0.05, y_max + y_span * 0.16)
    ax.grid(True, alpha=0.28)
    _format_xaxis(ax, timestamps, tz=tz)
    _dedup_legend(ax)
    fig.tight_layout()

    path = _save(fig, serial_number, start, "diagnostic_timeline")
    _pending.append((fig, path))
    caption = diagnostic_caption(markers, verified_facts)
    _pending_captions[path] = caption
    return {
        "path": path,
        "title": "Diagnostic Timeline",
        "marker_count": len(markers),
        "tz": tz_info["zone"],
        "caption": caption,
    }


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------

_HANDLERS = {
    "time_series":         _time_series,
    "flow_duration_curve": _flow_duration_curve,
    "peaks_annotated":     _peaks_annotated,
    "signal_quality":      _signal_quality,
    "diagnostic_timeline": _diagnostic_timeline,
}


_TZ_AWARE_PLOT_TYPES = frozenset(
    {"time_series", "peaks_annotated", "signal_quality", "diagnostic_timeline"}
)


def generate_plot(
    plot_type: str,
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
    start: float,
    tz_name: str | None = None,
    verified_facts: dict | None = None,
) -> dict:
    """
    Generate and save a chart as a PNG file.

    ``tz_name`` is the IANA timezone for time-axis rendering on the
    time-series, peaks, and signal-quality plots; falls back to the
    ``BLUEBOT_PLOT_TZ`` env var and finally to UTC. The flow-duration curve
    has no time axis and ignores ``tz_name``.

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
    if plot_type == "diagnostic_timeline":
        return handler(
            timestamps,
            values,
            quality,
            serial_number,
            start,
            tz_name=tz_name,
            verified_facts=verified_facts,
        )
    if plot_type in _TZ_AWARE_PLOT_TYPES:
        return handler(timestamps, values, quality, serial_number, start, tz_name=tz_name)
    return handler(timestamps, values, quality, serial_number, start)
