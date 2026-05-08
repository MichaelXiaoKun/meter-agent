"""
CLI entry point for the pipe configuration agent.

Full pipe + angle (inner LLM + tools):
    python main.py \\
      --serial BB8100015261 \\
      --material "PVC" \\
      --standard "Schedule 40" \\
      --size '2"' \\
      --angle "45º"

Transducer angle only (deterministic SSA publish, no catalog):
    python main.py --serial BB8100015261 --angle-only --angle "35º"

Set zero point (deterministic SZV publish, no catalog):
    python main.py --serial BB8100015261 --zero-point

Bearer token:
    Set BLUEBOT_TOKEN, or pass --token explicitly.
"""

from __future__ import annotations

import argparse
import os
import sys

from agent import analyze
from angle_only import run_transducer_angle_only
from report import format_angle_only_report, format_report, format_zero_point_report
from zero_point import run_zero_point


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipe configuration agent")
    parser.add_argument("--serial", required=True, help="Device serial number (user-facing)")
    parser.add_argument(
        "--angle-only",
        action="store_true",
        help="Only resolve device + transducer angle and publish MQTT ssa (no pipe catalog)",
    )
    parser.add_argument(
        "--zero-point",
        action="store_true",
        help="Only resolve device and publish MQTT szv to enter set-zero-point state.",
    )
    parser.add_argument(
        "--material",
        default=None,
        help="Pipe material (full mode only; matched against management catalog)",
    )
    parser.add_argument(
        "--standard",
        default=None,
        help="Pipe standard (full mode only)",
    )
    parser.add_argument(
        "--size",
        default=None,
        help="Nominal pipe size (full mode only)",
    )
    parser.add_argument(
        "--angle",
        default=None,
        help="Transducer angle label (e.g. 45º / 35° / 25). Mapping depends on Wi-Fi vs LoRaWAN.",
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
        print("Error: Bearer token required. Use --token or set BLUEBOT_TOKEN.", file=sys.stderr)
        sys.exit(1)

    if args.angle_only and args.zero_point:
        print("Error: choose either --angle-only or --zero-point, not both.", file=sys.stderr)
        sys.exit(2)

    if args.zero_point:
        print(f"Running set-zero-point update for serial {args.serial}...", file=sys.stderr)
        body = run_zero_point(
            serial_number=args.serial,
            token=token,
        )
        report = format_zero_point_report(body, args.serial)
        out_prefix = f"zero_point_{args.serial}"
    elif args.angle_only:
        if not args.angle:
            print("Error: --angle-only requires --angle.", file=sys.stderr)
            sys.exit(2)
        print(f"Running SSA-only transducer update for serial {args.serial}...", file=sys.stderr)
        body = run_transducer_angle_only(
            serial_number=args.serial,
            transducer_angle=args.angle,
            token=token,
        )
        report = format_angle_only_report(body, args.serial)
        out_prefix = f"angle_only_{args.serial}"
    else:
        if not args.material or not args.standard or not args.size or not args.angle:
            print(
                "Error: full pipe mode requires --material, --standard, --size, and --angle "
                "(or use --angle-only / --zero-point for narrow commands).",
                file=sys.stderr,
            )
            sys.exit(2)
        print(f"Running pipe configuration agent for serial {args.serial}...", file=sys.stderr)
        analysis = analyze(
            serial_number=args.serial,
            pipe_material=args.material,
            pipe_standard=args.standard,
            pipe_size=args.size,
            transducer_angle=args.angle,
            token=token,
        )
        report = format_report(analysis, args.serial)
        out_prefix = f"pipe_config_{args.serial}"

    if args.output == "file":
        filename = f"{out_prefix}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report saved to {filename}", file=sys.stderr)
    else:
        print(report)


if __name__ == "__main__":
    main()
