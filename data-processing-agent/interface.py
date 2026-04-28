"""
Leaf Agent Interface

This is the canonical entry point for orchestrator integration.
Import and call `run()` directly — no CLI, no subprocess, no HTTP needed.

Example (from an orchestrator):
    from data_processing_agent.interface import run

    result = run(
        serial_number="BB8100015261",
        start=1775588400,
        end=1775590200,
        token="...",
    )
    if result["success"]:
        print(result["report"])
    else:
        print(result["error"])
"""

import os
import traceback
from typing import Optional

from data_client import fetch_flow_data_range
from agent import analyze
from report import format_report
from processors.analysis_bundle import build_analysis_bundle
from processors.daily_rollup import (
    build_daily_rollups,
    build_today_partial_rollup,
    fraction_of_day_elapsed as _fraction_of_day_elapsed,
    today_missing_bucket_ratio as _today_missing_bucket_ratio,
)
from processors.plots import pop_figures
from processors.sampling_physics import max_healthy_inter_arrival_seconds
from processors.verified_facts import build_verified_facts
from processors.mask_by_local_time import apply_filter


def _resolve_meter_tz() -> str:
    """Same precedence as ``main._resolve_meter_tz`` — kept here so that
    programmatic callers (golden runner, etc.) get identical behaviour."""
    for var in ("BLUEBOT_PLOT_TZ", "DISPLAY_TZ"):
        raw = os.environ.get(var)
        if raw and raw.strip():
            return raw.strip()
    return "UTC"


def _maybe_baseline_inputs(
    *,
    df,
    primary_end: int,
    baseline_window: Optional[dict],
    token: Optional[str],
    serial_number: str,
    filters: Optional[dict] = None,
) -> dict:
    """Translate a ``baseline_window`` dict into ``build_verified_facts`` kwargs.

    Mirror of the CLI helper in ``main.py``. Empty dict when the caller did
    not opt in to a baseline (legacy behaviour preserved).
    """
    if not isinstance(baseline_window, dict):
        return {}
    bs = baseline_window.get("start")
    be = baseline_window.get("end")
    try:
        bs_i = int(bs) if bs is not None else None
        be_i = int(be) if be is not None else None
    except (TypeError, ValueError):
        return {}
    if bs_i is None or be_i is None or be_i < bs_i:
        return {}

    tz = _resolve_meter_tz()
    cap = max_healthy_inter_arrival_seconds()
    nominal_interval_seconds = float(cap)

    baseline_df, _ = fetch_flow_data_range(
        serial_number,
        bs_i,
        be_i,
        token=token,
        verbose=False,
        return_metadata=True,
    )
    if filters is not None:
        baseline_filtered_df, baseline_filter_result = apply_filter(baseline_df, filters)
        baseline_df = (
            baseline_filtered_df
            if baseline_filter_result.applied
            else baseline_df.iloc[0:0].copy()
        )
    reference_rollups = build_daily_rollups(
        baseline_df,
        tz=tz,
        nominal_interval_seconds=nominal_interval_seconds,
        healthy_gap_cap_seconds=cap,
    )

    import pandas as _pd

    end_local = _pd.to_datetime(int(primary_end), unit="s", utc=True).tz_convert(tz)
    today_local_date = end_local.strftime("%Y-%m-%d")
    target_weekday = int(end_local.weekday())
    fraction = _fraction_of_day_elapsed(end_timestamp=float(primary_end), tz=tz)
    today_partial = build_today_partial_rollup(
        df,
        target_local_date=today_local_date,
        tz=tz,
        nominal_interval_seconds=nominal_interval_seconds,
        healthy_gap_cap_seconds=cap,
        fraction_of_day_elapsed=fraction,
    )
    today_missing_bucket = _today_missing_bucket_ratio(
        df,
        target_local_date=today_local_date,
        tz=tz,
        fraction_of_day_elapsed=fraction,
    )

    return {
        "reference_rollups": reference_rollups,
        "today_partial": today_partial,
        "target_weekday": target_weekday,
        "fraction_of_day_elapsed": fraction,
        "today_missing_bucket_ratio": today_missing_bucket,
    }


def _filter_refusal_analysis(verified_facts: dict) -> str:
    fa = verified_facts.get("filter_applied") if isinstance(verified_facts, dict) else None
    if not isinstance(fa, dict):
        return "The requested local-time filter could not be applied."
    lines = [
        "## Requested local-time filter was not applied",
        "",
        f"- State: `{fa.get('state') or 'unknown'}`",
    ]
    for reason in fa.get("reasons_refused") or []:
        lines.append(f"- Refusal: {reason}")
    for err in fa.get("validation_errors") or []:
        lines.append(f"- Validation: {err}")
    return "\n".join(lines)


def run(
    serial_number: str,
    start: int,
    end: int,
    token: Optional[str] = None,
    *,
    baseline_window: Optional[dict] = None,
    filters: Optional[dict] = None,
) -> dict:
    """
    Fetch, process, and analyse flow rate data for a meter over a time range.

    This is the single callable contract exposed to orchestrators.
    All errors are caught and returned in the result dict — this function
    never raises, so orchestrators can call it safely without try/except.

    Args:
        serial_number:  Meter serial number (e.g. "BB8100015261")
        start:          Range start as Unix timestamp (seconds, inclusive)
        end:            Range end as Unix timestamp (seconds, inclusive)
        token:          bluebot Bearer token. Falls back to BLUEBOT_TOKEN env var.
        baseline_window: Optional ``{"start": int, "end": int}`` reference
                        window. When supplied, the agent fetches that range,
                        builds local-tz daily rollups, and runs
                        :func:`processors.baseline_quality.evaluate_baseline_quality`
                        — populating ``analysis_bundle["verified_facts"]
                        ["baseline_quality"]`` with a real verdict. When
                        the verdict is ``reliable``, ``today_vs_baseline``
                        is also added.
        filters:        Optional local-time filter spec. When supplied and
                        valid, downstream metrics, plots, and analysis run on
                        the filtered subset. Invalid or empty filters
                        short-circuit with ``filter_applied`` refusal details.

    Returns:
        {
            "success":        bool,
            "serial_number":  str,
            "start":          int,
            "end":            int,
            "data_points":    int | None,   # number of rows fetched
            "report":         str | None,   # full Markdown report
            "plot_paths":     list | None,
            "analysis_bundle": dict | None, # machine-readable verified_facts + plots (JSON-serialisable)
            "error":          str | None,   # populated only on failure
        }
    """
    base = {"serial_number": serial_number, "start": start, "end": end}

    try:
        df = fetch_flow_data_range(serial_number, start, end, token=token, verbose=False)
        if filters is not None and not filters:
            filters = None
        analysis_df = df
        filter_result = None
        if filters is not None:
            filtered_df, filter_result = apply_filter(df, filters)
            if filter_result.applied:
                analysis_df = filtered_df

        filter_refused = filter_result is not None and not filter_result.applied
        baseline_kwargs = {}
        if not filter_refused:
            baseline_kwargs = _maybe_baseline_inputs(
                df=analysis_df,
                primary_end=end,
                baseline_window=baseline_window,
                token=token,
                serial_number=serial_number,
                filters=filters,
            )
        verified_facts = build_verified_facts(df, filters=filters, **baseline_kwargs)
        if filter_refused:
            analysis = _filter_refusal_analysis(verified_facts)
            plot_paths = []
        else:
            analysis = analyze(analysis_df, serial_number, verified_facts=verified_facts)
            plot_paths = [path for _, path in pop_figures()]
        report = format_report(
            analysis, serial_number, start, end, verified_facts=verified_facts
        )
        analysis_bundle = build_analysis_bundle(
            serial_number, start, end, verified_facts, plot_paths
        )

        return {
            **base,
            "success": True,
            "data_points": verified_facts.get("n_rows", len(analysis_df)),
            "report": report,
            "plot_paths": plot_paths,
            "analysis_bundle": analysis_bundle,
            "error": None,
        }

    except Exception as exc:
        return {
            **base,
            "success": False,
            "data_points": None,
            "report": None,
            "plot_paths": None,
            "analysis_bundle": None,
            "error": f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        }
