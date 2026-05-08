"""Shared ``processors`` namespace compatibility.

Both the orchestrator and data-processing agent expose a top-level
``processors`` package on the test/runtime path.  If the orchestrator package is
imported first, extend its package search path so later data-agent processor
imports still resolve.
"""

from __future__ import annotations

from pathlib import Path

_DATA_PROCESSORS = Path(__file__).resolve().parents[2] / "data-processing-agent" / "processors"
if _DATA_PROCESSORS.is_dir():
    data_processors_path = str(_DATA_PROCESSORS)
    if data_processors_path not in __path__:
        __path__.insert(0, data_processors_path)
