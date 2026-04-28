"""
main.py — CLI entry point for the meter status agent.

Usage:
    python main.py --serial BB8100015261

Bearer token:
    Set the BLUEBOT_TOKEN environment variable, or pass --token explicitly.

Stderr markers (for the orchestrator wrapper):
    ``__BLUEBOT_STATUS_JSON__<json>`` — structured per-meter facts derived by
    the deterministic processors. Emitted before the LLM analysis runs so it
    is available even if the LLM step fails. The human-readable Markdown
    report still goes to stdout as before.
"""

import argparse
import json
import os
import sys
from typing import Any, Dict

from data_client import fetch_meter_status
from agent import analyze
from report import format_report
from processors.staleness import compute_staleness
from processors.signal import interpret_signal_quality
from processors.pipe_config import interpret_pipe_config
from processors.health_score import compute_health_score


_STATUS_JSON_MARKER = "__BLUEBOT_STATUS_JSON__"


def _safe_processor(fn, *args):
    """Run a processor and return (result, error_message). Never raises."""
    try:
        return fn(*args), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _build_status_payload(status: Dict[str, Any], serial: str) -> Dict[str, Any]:
    """Call each processor independently so one bad field doesn't mask the rest."""
    staleness, staleness_err = _safe_processor(
        compute_staleness, status.get("last_message_at")
    )
    signal, signal_err = _safe_processor(
        interpret_signal_quality, status.get("signal_quality")
    )
    pipe, pipe_err = _safe_processor(
        interpret_pipe_config,
        status.get("pipe_outer_diameter"),
        status.get("pipe_wall_thickness"),
        status.get("inferred_nominal_size"),
    )
    errors = {
        k: v
        for k, v in (
            ("staleness", staleness_err),
            ("signal", signal_err),
            ("pipe_config", pipe_err),
        )
        if v
    }
    payload = {
        "serial_number": serial,
        "online": status.get("online"),
        "last_message_at": status.get("last_message_at"),
        "staleness": staleness,
        "signal": signal,
        "pipe_config": pipe,
        "errors": errors,
    }
    payload["health_score"] = compute_health_score(status=payload)
    return payload


def _emit_status_marker(status: Dict[str, Any], serial: str) -> None:
    """Emit the structured payload on stderr. Swallow any JSON error silently —
    the Markdown report is still the primary output."""
    try:
        payload = _build_status_payload(status, serial)
        print(
            _STATUS_JSON_MARKER + json.dumps(payload, default=str),
            file=sys.stderr,
        )
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Meter status report agent")
    parser.add_argument("--serial", required=True, dest="serial", help="Meter serial number (e.g. BB8100015261)")
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

    try:
        print(f"Fetching status for serial {args.serial}...", file=sys.stderr)
        status = fetch_meter_status(args.serial, token)
        print(
            f"Online: {status.get('online')}  |  Last seen: {status.get('last_message_at')}",
            file=sys.stderr,
        )

        _emit_status_marker(status, args.serial)

        print("Running analysis...", file=sys.stderr)
        analysis = analyze(status, args.serial)
        report = format_report(analysis, args.serial)

        if args.output == "file":
            filename = f"status_{args.serial}.md"
            with open(filename, "w") as f:
                f.write(report)
            print(f"Report saved to {filename}", file=sys.stderr)
        else:
            print(report)
    except Exception as e:
        # Orchestrator surfaces stderr to the UI / SSE — never dump a Python traceback here.
        msg = str(e).strip() or type(e).__name__
        print(msg, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
