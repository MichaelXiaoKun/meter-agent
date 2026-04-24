"""Smoke tests for ``scripts/analyze_events.py`` (stdlib only)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "analyze_events.py"


def test_stats_counts_events(tmp_path: Path) -> None:
    p = tmp_path / "e.jsonl"
    p.write_text(
        '{"event":"turn_start","ts":1}\n'
        '{"event":"turn_start","ts":2}\n'
        '{"event":"api_call_end","ts":3}\n',
        encoding="utf-8",
    )
    r = subprocess.run(
        [sys.executable, str(_SCRIPT), str(p), "--stats"],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert data["total_lines"] == 3
    assert data["by_event"]["turn_start"] == 2
    assert data["by_event"]["api_call_end"] == 1


def test_filter_contains_and_tail(tmp_path: Path) -> None:
    p = tmp_path / "e.jsonl"
    lines = [
        json.dumps({"event": f"turn_{i}", "n": i}, ensure_ascii=False) for i in range(5)
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT),
            str(p),
            "--contains",
            "turn",
            "--tail",
            "2",
            "--compact",
        ],
        cwd=str(_ROOT),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    out = [json.loads(s) for s in r.stdout.strip().splitlines() if s]
    assert len(out) == 2
    assert out[-1]["n"] == 4
