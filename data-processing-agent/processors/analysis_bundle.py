"""
Machine-readable bundle for audits (same facts as the verified appendix).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


def build_analysis_bundle(
    serial_number: str,
    start: int,
    end: int,
    verified_facts: Dict[str, Any],
    plot_paths: List[str],
) -> Dict[str, Any]:
    return {
        "serial_number": serial_number,
        "range": {"start_unix": start, "end_unix": end},
        "generated_at_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_points": verified_facts.get("n_rows"),
        "verified_facts": verified_facts,
        "plot_paths": plot_paths,
    }
