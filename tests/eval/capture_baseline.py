#!/usr/bin/env python3
"""
Capture a lightweight eval baseline from orchestrator observability JSONL.

The output is intentionally small and deterministic: one record per turn with
the routed intent, available tools, executed tools, failures, and API stop
reasons. It is a trace snapshot for future replay/scoring work, not a full
conversation export.

Examples:
    python tests/eval/capture_baseline.py /tmp/bluebot_events.jsonl
    python tests/eval/capture_baseline.py events.jsonl --output tests/eval/baseline/local.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _turn_record(turn_id: str) -> dict[str, Any]:
    return {
        "turn_id": turn_id,
        "intent": None,
        "intent_source": None,
        "prompt_version": None,
        "tool_names_available": [],
        "tool_calls": [],
        "tool_failures": 0,
        "api_calls": [],
        "outcome": None,
    }


def capture_baseline(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    turns: OrderedDict[str, dict[str, Any]] = OrderedDict()
    orphan_ix = 0

    for rec in records:
        turn_id = str(rec.get("turn_id") or "")
        if not turn_id:
            orphan_ix += 1
            turn_id = f"orphan-{orphan_ix}"
        turn = turns.setdefault(turn_id, _turn_record(turn_id))
        event = rec.get("event")

        if event == "turn_start":
            turn["intent"] = rec.get("intent")
            turn["intent_source"] = rec.get("intent_source")
            turn["prompt_version"] = rec.get("prompt_version")
            names = rec.get("tool_names")
            turn["tool_names_available"] = names if isinstance(names, list) else []
        elif event == "tool_call_end":
            tool = rec.get("tool")
            if tool:
                turn["tool_calls"].append(
                    {
                        "tool": tool,
                        "success": bool(rec.get("success")),
                        "cached": bool(rec.get("cached")),
                        "round": rec.get("round"),
                    }
                )
            if rec.get("success") is False:
                turn["tool_failures"] += 1
        elif event == "api_call_end":
            turn["api_calls"].append(
                {
                    "model": rec.get("model"),
                    "attempt": rec.get("attempt"),
                    "stop_reason": rec.get("stop_reason"),
                    "input_tokens": rec.get("input_tokens"),
                    "output_tokens": rec.get("output_tokens"),
                }
            )
        elif event == "turn_end":
            turn["outcome"] = rec.get("outcome")

    return {
        "schema_version": 1,
        "turn_count": len(turns),
        "turns": list(turns.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture eval baseline from Bluebot event JSONL.")
    parser.add_argument("event_log", type=Path, help="Path to BLUEBOT_EVENT_LOG_PATH JSONL output")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("tests/eval/baseline/latest.json"),
        help="Baseline JSON path to write",
    )
    args = parser.parse_args(argv)

    if not args.event_log.exists():
        print(f"event log not found: {args.event_log}", file=sys.stderr)
        return 2

    baseline = capture_baseline(_read_jsonl(args.event_log))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
