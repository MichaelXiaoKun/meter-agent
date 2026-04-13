"""
main.py — CLI entry point for the meter status agent.

Usage:
    python main.py --device BB8100015261

Bearer token:
    Set the BLUEBOT_TOKEN environment variable, or pass --token explicitly.
"""

import argparse
import os
import sys

from data_client import fetch_meter_status
from agent import analyze
from report import format_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Meter status report agent")
    parser.add_argument("--device", required=True, help="Device ID (e.g. BB8100015261)")
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
        print("Error: Bearer token required. Use --token or set BLUEBOT_TOKEN.", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching status for device {args.device}...", file=sys.stderr)
    status = fetch_meter_status(args.device, token)
    print(f"Online: {status.get('online')}  |  Last seen: {status.get('last_message_at')}", file=sys.stderr)

    print("Running analysis...", file=sys.stderr)
    analysis = analyze(status, args.device)
    report = format_report(analysis, args.device)

    if args.output == "file":
        filename = f"status_{args.device}.md"
        with open(filename, "w") as f:
            f.write(report)
        print(f"Report saved to {filename}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
