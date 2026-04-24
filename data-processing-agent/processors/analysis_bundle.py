"""
Machine-readable bundle for audits (same facts as the verified appendix).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def build_analysis_bundle(
    serial_number: str,
    start: int,
    end: int,
    verified_facts: Dict[str, Any],
    plot_paths: List[str],
    *,
    plot_captions: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Machine-readable audit bundle.

    ``plot_captions`` is a path → structured-caption dict produced by
    ``processors.plot_captions``. Including it in the bundle keeps the
    non-vision "what the chart shows" evidence available for downstream
    replays / evals even after the Markdown report has been truncated.
    """
    bundle: Dict[str, Any] = {
        "serial_number": serial_number,
        "range": {"start_unix": start, "end_unix": end},
        "generated_at_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_points": verified_facts.get("n_rows"),
        "verified_facts": verified_facts,
        "plot_paths": plot_paths,
    }
    if plot_captions:
        bundle["plot_captions"] = plot_captions
    return bundle
