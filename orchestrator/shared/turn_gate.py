"""
Global limiter for concurrent orchestrator run_turn executions (per API process).

Uses a semaphore so at most ORCHESTRATOR_MAX_CONCURRENT_TURNS chats run Claude at once.
Additional POSTs block until a slot frees — reducing Anthropic TPM/RPM bursts.

Set ORCHESTRATOR_MAX_CONCURRENT_TURNS=1 to serialize all turns (strictest).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


def _max_slots() -> int:
    raw = os.environ.get("ORCHESTRATOR_MAX_CONCURRENT_TURNS", "2")
    try:
        n = int(raw)
    except ValueError:
        return 2
    return max(1, n)


_MAX = _max_slots()
_sem = threading.Semaphore(_MAX)


def configured_max_slots() -> int:
    """Slots allowed at once (for logs / diagnostics)."""
    return _MAX


def acquire_run_turn_slot(*, on_wait: Callable[[], None] | None = None) -> None:
    """
    Block until this request may start run_turn.

    If the caller would block, invokes on_wait() once (e.g. emit SSE) before blocking.
    """
    if _sem.acquire(blocking=False):
        return
    if callable(on_wait):
        try:
            on_wait()
        except Exception:
            logger.exception("turn_gate on_wait callback failed")
    _sem.acquire()


def release_run_turn_slot() -> None:
    _sem.release()
