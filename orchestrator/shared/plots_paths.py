"""
Shared resolution for the flow-analysis plots directory.

Used by api.py (GET /api/plots) and store.py (cleanup on conversation delete).
Must match data-processing-agent/processors/plots.py PLOTS_DIR logic.
"""

from __future__ import annotations

import os
from pathlib import Path


def resolved_plots_dir() -> Path:
    """
    Canonical plots directory.

    Relative PLOTS_DIR is resolved from the meter_agent repo root (parent of orchestrator/),
    not from process cwd — uvicorn often runs with cwd orchestrator/ while subprocess
    cwd is data-processing-agent/, which previously split reads vs writes.
    """
    repo_root = Path(__file__).resolve().parents[2]
    raw = os.environ.get("PLOTS_DIR")
    if not raw:
        return (repo_root / "data-processing-agent" / "plots").resolve()
    p = Path(raw).expanduser()
    return p.resolve() if p.is_absolute() else (repo_root / p).resolve()
