"""
main.py — CLI entry point for the flow rate analysis agent.

Usage:
    python main.py --serial BB8100015261 --start 1775588400 --end 1775590200

Bearer token:
    Set the BLUEBOT_TOKEN environment variable, or pass --token explicitly.

Output:
    Prints the Markdown report to stdout by default.
    Use --output file to save to a .md file instead.
"""

import argparse
import json
import os
import re
import sys
import time

# Headless servers (Docker / Railway) have no display; force non-GUI backend first.
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data_client import fetch_flow_data_range
from agent import analyze
from report import format_report
from processors.analysis_bundle import build_analysis_bundle
from processors.anomaly_attribution import slim_anomaly_attribution_for_prompt
from processors.long_range_summary import (
    format_long_range_summary_markdown,
    build_long_range_summary,
    resolve_analysis_mode,
)
from processors.plots import generate_plot, pop_captions, pop_figures
from processors.verified_facts import build_verified_facts

_ANALYSIS_JSON_MARKER = "__BLUEBOT_ANALYSIS_JSON__"
_PLOT_CAPTIONS_MARKER = "__BLUEBOT_PLOT_CAPTIONS__"
_REASONING_SCHEMA_MARKER = "__BLUEBOT_REASONING_SCHEMA__"
_ANALYSIS_DETAILS_MARKER = "__BLUEBOT_ANALYSIS_DETAILS__"
_ANALYSIS_METADATA_MARKER = "__BLUEBOT_ANALYSIS_METADATA__"
_DOWNLOAD_ARTIFACTS_MARKER = "__BLUEBOT_DOWNLOAD_ARTIFACTS__"


def _safe_filename_part(value: object) -> str:
    s = str(value or "").strip()
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", s)
    return s.strip("._") or "unknown"


def _write_flow_csv_artifact(df, analyses_dir: str, serial_number: str, start: int, end: int) -> dict:
    """Write the exact normalized flow rows used for analysis, oldest first."""
    os.makedirs(analyses_dir, exist_ok=True)
    filename = (
        f"flow_data_{_safe_filename_part(serial_number)}_"
        f"{int(start)}_{int(end)}.csv"
    )
    path = os.path.abspath(os.path.join(analyses_dir, filename))
    cols = ["timestamp", "flow_rate", "flow_amount", "quality"]
    csv_df = (
        df.sort_values("timestamp")
        .drop_duplicates(subset="timestamp")
        .reindex(columns=cols)
    )
    csv_df.to_csv(path, index=False)
    return {
        "kind": "csv",
        "title": "Flow data CSV",
        "filename": filename,
        "path": path,
        "row_count": int(len(csv_df)),
    }


def _analysis_details_from_verified_facts(verified_facts: dict) -> dict:
    """Small UI-safe processor summary for the orchestrator activity timeline."""
    details: dict = {}
    cusum = verified_facts.get("cusum_drift") if isinstance(verified_facts, dict) else None
    if isinstance(cusum, dict):
        adequacy = cusum.get("adequacy") if isinstance(cusum.get("adequacy"), dict) else {}
        details["cusum_drift"] = {
            "skipped": bool(cusum.get("skipped")),
            "drift_detected": cusum.get("drift_detected"),
            "positive_alarm_count": cusum.get("positive_alarm_count"),
            "negative_alarm_count": cusum.get("negative_alarm_count"),
            "first_alarm_timestamp": cusum.get("first_alarm_timestamp"),
            "adequacy_ok": adequacy.get("ok"),
            "adequacy_reason": adequacy.get("reason"),
            "actual_points": adequacy.get("actual_points"),
            "target_min": adequacy.get("target_min"),
            "gap_pct": adequacy.get("gap_pct"),
        }
    attribution = (
        verified_facts.get("anomaly_attribution")
        if isinstance(verified_facts, dict)
        else None
    )
    if isinstance(attribution, dict):
        details["attribution"] = slim_anomaly_attribution_for_prompt(attribution)
    return details


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Flow rate time series analysis agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--serial", required=True, dest="serial", help="Meter serial number (e.g. BB8100015261)"
    )
    parser.add_argument(
        "--start", required=True, type=int, help="Range start as Unix timestamp (seconds)"
    )
    parser.add_argument(
        "--end", required=True, type=int, help="Range end as Unix timestamp (seconds)"
    )
    parser.add_argument(
        "--token", default=None, help="Bearer token (default: reads BLUEBOT_TOKEN env var)"
    )
    parser.add_argument(
        "--output",
        choices=["console", "file"],
        default="console",
        help="Output destination (default: console)",
    )
    parser.add_argument(
        "--analysis-mode",
        choices=["auto", "detailed", "summary"],
        default="auto",
        help=(
            "Analysis mode. auto uses deterministic summary for long / large windows; "
            "detailed always runs the internal LLM analysis loop."
        ),
    )
    args = parser.parse_args()

    token = args.token or os.environ.get("BLUEBOT_TOKEN")
    if not token:
        print(
            "Error: Bearer token required. Use --token or set BLUEBOT_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Fetching data for serial {args.serial}...", file=sys.stderr)
    fetch_t0 = time.monotonic()
    df, fetch_metadata = fetch_flow_data_range(
        args.serial,
        args.start,
        args.end,
        token,
        verbose=True,
        return_metadata=True,
    )
    fetch_metadata.setdefault(
        "fetch_elapsed_seconds",
        round(float(time.monotonic() - fetch_t0), 3),
    )
    print(f"Fetched {len(df)} data points total.", file=sys.stderr)

    print("Running analysis...", file=sys.stderr)
    verified_facts = build_verified_facts(df)
    mode_selection = resolve_analysis_mode(
        args.analysis_mode,
        start=args.start,
        end=args.end,
        row_count=len(df),
    )

    long_range_summary = None
    if mode_selection["resolved_mode"] == "summary":
        if len(df):
            quality = (
                df["quality"].values.astype(float)
                if "quality" in df.columns
                else [float("nan")] * len(df)
            )
            generate_plot(
                "diagnostic_timeline",
                df["timestamp"].values.astype(float),
                df["flow_rate"].values.astype(float),
                quality,
                serial_number=args.serial,
                start=args.start,
                verified_facts=verified_facts,
            )
        pending = pop_figures()
        plot_paths = [path for _, path in pending]
        plot_captions = pop_captions()
        long_range_summary = build_long_range_summary(df, verified_facts)
        analysis = format_long_range_summary_markdown(
            serial_number=args.serial,
            summary=long_range_summary,
            verified_facts=verified_facts,
            mode_selection=mode_selection,
            plot_paths=plot_paths,
        )
        report = format_report(
            analysis, args.serial, args.start, args.end, verified_facts=verified_facts
        )
    else:
        analysis = analyze(df, args.serial, verified_facts=verified_facts)
        report = format_report(
            analysis, args.serial, args.start, args.end, verified_facts=verified_facts
        )

        if len(df):
            quality = (
                df["quality"].values.astype(float)
                if "quality" in df.columns
                else [float("nan")] * len(df)
            )
            generate_plot(
                "diagnostic_timeline",
                df["timestamp"].values.astype(float),
                df["flow_rate"].values.astype(float),
                quality,
                serial_number=args.serial,
                start=args.start,
                verified_facts=verified_facts,
            )

        pending = pop_figures()
        plot_paths = [path for _, path in pending]
        plot_captions = pop_captions()

    _here = os.path.dirname(os.path.abspath(__file__))
    # Analysis bundles live under <agent>/analyses/ (gitignored) alongside plots/.
    # Override with BLUEBOT_ANALYSES_DIR when a persistent volume is needed.
    analyses_dir = os.environ.get("BLUEBOT_ANALYSES_DIR") or os.path.join(_here, "analyses")
    os.makedirs(analyses_dir, exist_ok=True)
    report_path = os.path.join(
        analyses_dir, f"report_{args.serial}_{args.start}_{args.end}.md"
    )
    download_artifacts = [
        _write_flow_csv_artifact(df, analyses_dir, args.serial, args.start, args.end)
    ]
    analysis_metadata = {
        "analysis_mode": mode_selection["resolved_mode"],
        "requested_analysis_mode": mode_selection["requested_mode"],
        "mode_selection_reasons": mode_selection["reasons"],
        "mode_selection": mode_selection,
        "fetch": fetch_metadata,
        "report_path": os.path.abspath(report_path),
        "download_artifacts": download_artifacts,
    }
    bundle = build_analysis_bundle(
        args.serial,
        args.start,
        args.end,
        verified_facts,
        plot_paths,
        plot_captions=plot_captions,
        analysis_mode=mode_selection["resolved_mode"],
        long_range_summary=long_range_summary,
        analysis_metadata=analysis_metadata,
        download_artifacts=download_artifacts,
    )
    aj_path = os.path.join(
        analyses_dir, f"analysis_{args.serial}_{args.start}_{args.end}.json"
    )
    with open(aj_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, default=str)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(_ANALYSIS_JSON_MARKER + json.dumps({"path": os.path.abspath(aj_path)}), file=sys.stderr)
    analysis_details = _analysis_details_from_verified_facts(verified_facts)
    if long_range_summary:
        analysis_details["rollup_highlights"] = long_range_summary.get("rollup_highlights")
        analysis_details["anomaly_windows"] = long_range_summary.get("anomaly_windows", [])[:12]
    if analysis_details:
        print(
            _ANALYSIS_DETAILS_MARKER + json.dumps(analysis_details, default=str),
            file=sys.stderr,
        )
    print(
        _ANALYSIS_METADATA_MARKER + json.dumps(analysis_metadata, default=str),
        file=sys.stderr,
    )
    print(
        _DOWNLOAD_ARTIFACTS_MARKER + json.dumps(download_artifacts, default=str),
        file=sys.stderr,
    )

    if args.output == "file":
        filename = f"report_{args.serial}_{args.start}_{args.end}.md"
        with open(filename, "w") as f:
            f.write(report)
        print(f"Report saved to {filename}", file=sys.stderr)
    else:
        print(report)

    # Reasoning schema: small, deterministic anchor block the orchestrator can
    # surface to the outer LLM directly, so it does not have to re-derive the
    # same evidence → hypothesis → next-step reasoning from Markdown prose.
    reasoning_schema = verified_facts.get("reasoning_schema") if isinstance(verified_facts, dict) else None
    if reasoning_schema:
        print(_REASONING_SCHEMA_MARKER + json.dumps(reasoning_schema, default=str), file=sys.stderr)

    if pending:
        paths = plot_paths
        # Orchestrator parses this line for authoritative paths (not markdown).
        print("__BLUEBOT_PLOT_PATHS__" + json.dumps(paths), file=sys.stderr)
        if plot_captions:
            # Same path-keyed dict as the bundle so the orchestrator can zip
            # captions back with plot_paths without guessing.
            print(
                _PLOT_CAPTIONS_MARKER + json.dumps(plot_captions, default=str),
                file=sys.stderr,
            )
        for path in paths:
            print(f"Plot saved: {path}", file=sys.stderr)
        # Only open interactive windows when running in a real terminal.
        # When invoked as a subprocess (e.g. from the orchestrator), stdout is
        # captured and isatty() returns False — plt.show() is skipped.
        if sys.stdout.isatty():
            plt.show()


if __name__ == "__main__":
    main()
