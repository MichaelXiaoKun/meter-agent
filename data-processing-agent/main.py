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
import sys

# Headless servers (Docker / Railway) have no display; force non-GUI backend first.
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from data_client import fetch_flow_data_range
from agent import analyze
from report import format_report
from processors.analysis_bundle import build_analysis_bundle
from processors.plots import pop_captions, pop_figures
from processors.verified_facts import build_verified_facts

_ANALYSIS_JSON_MARKER = "__BLUEBOT_ANALYSIS_JSON__"
_PLOT_CAPTIONS_MARKER = "__BLUEBOT_PLOT_CAPTIONS__"
_REASONING_SCHEMA_MARKER = "__BLUEBOT_REASONING_SCHEMA__"


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
    args = parser.parse_args()

    token = args.token or os.environ.get("BLUEBOT_TOKEN")
    if not token:
        print(
            "Error: Bearer token required. Use --token or set BLUEBOT_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Fetching data for serial {args.serial}...", file=sys.stderr)
    df = fetch_flow_data_range(args.serial, args.start, args.end, token, verbose=True)
    print(f"Fetched {len(df)} data points total.", file=sys.stderr)

    print("Running analysis...", file=sys.stderr)
    verified_facts = build_verified_facts(df)
    analysis = analyze(df, args.serial, verified_facts=verified_facts)
    report = format_report(
        analysis, args.serial, args.start, args.end, verified_facts=verified_facts
    )

    pending = pop_figures()
    plot_paths = [path for _, path in pending]
    plot_captions = pop_captions()

    _here = os.path.dirname(os.path.abspath(__file__))
    bundle = build_analysis_bundle(
        args.serial,
        args.start,
        args.end,
        verified_facts,
        plot_paths,
        plot_captions=plot_captions,
    )
    # Analysis bundles live under <agent>/analyses/ (gitignored) alongside plots/.
    # Override with BLUEBOT_ANALYSES_DIR when a persistent volume is needed.
    analyses_dir = os.environ.get("BLUEBOT_ANALYSES_DIR") or os.path.join(_here, "analyses")
    os.makedirs(analyses_dir, exist_ok=True)
    aj_path = os.path.join(
        analyses_dir, f"analysis_{args.serial}_{args.start}_{args.end}.json"
    )
    with open(aj_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, default=str)
    print(_ANALYSIS_JSON_MARKER + json.dumps({"path": os.path.abspath(aj_path)}), file=sys.stderr)

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
