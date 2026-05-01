#!/usr/bin/env python3
"""
Query JSONL event files produced by ``orchestrator/shared/observability.py``.

No third-party dependencies — stdin, file, or ``-`` for pipe.

Examples::

    # Summary counts by ``event`` field
    python scripts/analyze_events.py /tmp/bluebot_events.jsonl --stats

    # Last 20 events whose name contains "turn"
    python scripts/analyze_events.py /path/to.jsonl --contains turn --tail 20

    # Filter by turn id, pretty-print
    python scripts/analyze_events.py events.jsonl --turn-id a1b2c3d4e5f6

    # Stream from another process: tail -f file | python scripts/analyze_events.py - --max 5

The script prints one JSON object per line (JSONL) to stdout so you can pipe to ``jq``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any, Iterable, Iterator, TextIO


def _iter_lines(f: TextIO) -> Iterator[str]:
    for line in f:
        s = line.strip()
        if s:
            yield s


def _parse_line(line: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _read_all_records(path: str) -> list[dict[str, Any]]:
    if path == "-":
        return _read_records_from(_iter_lines(sys.stdin))
    with open(path, "r", encoding="utf-8") as f:
        return _read_records_from(_iter_lines(f))


def _read_records_from(lines: Iterable[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in lines:
        rec = _parse_line(line)
        if rec is not None:
            out.append(rec)
    return out


def _apply_filters(
    records: list[dict[str, Any]],
    *,
    contains: str | None,
    turn_id: str | None,
) -> list[dict[str, Any]]:
    out = records
    if contains:
        c = contains.lower()
        out = [r for r in out if c in str(r.get("event", "")).lower()]
    if turn_id:
        out = [r for r in out if r.get("turn_id") == turn_id]
    return out


def _print_stats(records: list[dict[str, Any]]) -> None:
    counts = Counter(str(r.get("event", "")) for r in records)
    total = len(records)
    summary = {
        "total_lines": total,
        "by_event": dict(sorted(counts.items(), key=lambda x: (-x[1], x[0]))),
    }
    json.dump(summary, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def _print_records(records: list[dict[str, Any]], *, one_line: bool) -> None:
    for r in records:
        if one_line:
            json.dump(r, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(r, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Query Bluebot orchestrator JSONL event logs.")
    p.add_argument(
        "path",
        help="Path to a .jsonl file, or '-' for stdin",
    )
    p.add_argument(
        "--stats",
        action="store_true",
        help="Print counts grouped by the 'event' field and exit",
    )
    p.add_argument(
        "--contains",
        metavar="SUBSTR",
        help="Case-insensitive substring match on the 'event' field",
    )
    p.add_argument(
        "--turn-id",
        dest="turn_id",
        metavar="ID",
        help="Exact match on turn_id (when set by turn_context / run_turn)",
    )
    p.add_argument(
        "--tail",
        type=int,
        metavar="N",
        help="After filtering, keep only the last N records",
    )
    p.add_argument(
        "--head",
        type=int,
        metavar="N",
        help="After filtering, keep only the first N records",
    )
    p.add_argument(
        "--max",
        type=int,
        dest="max_out",
        metavar="N",
        help="Max records to print (after head/tail); default: no limit",
    )
    p.add_argument(
        "--compact",
        action="store_true",
        help="One-line JSON per record (default: pretty-printed)",
    )
    args = p.parse_args(argv)

    records = _read_all_records(args.path)
    filtered = _apply_filters(
        records, contains=args.contains, turn_id=args.turn_id
    )
    if args.head is not None:
        filtered = filtered[: max(0, args.head)]
    if args.tail is not None:
        n = max(0, args.tail)
        filtered = filtered[-n:] if n else []
    if args.max_out is not None:
        filtered = filtered[: max(0, args.max_out)]

    if args.stats:
        _print_stats(filtered)
        return 0

    _print_records(filtered, one_line=args.compact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
