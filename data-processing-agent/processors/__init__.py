"""
Processors package — mathematically rigorous, deterministic functions.
The agent may only source numbers from these modules.
"""
"""Shared ``processors`` namespace compatibility.

Both the data-processing agent and orchestrator expose a top-level
``processors`` package on the test/runtime path.  If the data-processing
package is imported first, extend its package search path so later orchestrator
processor imports still resolve.
"""

from pathlib import Path

_ORCHESTRATOR_PROCESSORS = Path(__file__).resolve().parents[2] / "orchestrator" / "processors"
if _ORCHESTRATOR_PROCESSORS.is_dir():
    orchestrator_processors_path = str(_ORCHESTRATOR_PROCESSORS)
    if orchestrator_processors_path not in __path__:
        __path__.append(orchestrator_processors_path)
