"""
Process-wide 60-second sliding window of estimated input tokens for the Anthropic API key.

Tracks usage from this orchestrator process only (count_tokens, messages.stream, messages.create).
Subprocesses (e.g. data-processing-agent) use the same API key but a different process — they do not
contribute unless you add reporting. Multi-replica deployments get one window per replica.

Thread-safe for concurrent chat threads in one uvicorn worker.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any

_WINDOW_SEC = 60.0

_lock = threading.Lock()
_samples: deque[tuple[float, int]] = deque()


def record_input_tokens(n: int) -> None:
    """Add *n* input tokens to the rolling window (one API request)."""
    if n <= 0:
        return
    with _lock:
        _prune_unlocked()
        _samples.append((time.time(), n))


def record_input_tokens_from_usage(usage: Any) -> None:
    """Extract input_tokens from an Anthropic Usage object (or dict) if present."""
    if usage is None:
        return
    n = getattr(usage, "input_tokens", None)
    if n is None and isinstance(usage, dict):
        n = usage.get("input_tokens")
    if isinstance(n, int) and n > 0:
        record_input_tokens(n)


def _prune_unlocked() -> None:
    cutoff = time.time() - _WINDOW_SEC
    while _samples and _samples[0][0] < cutoff:
        _samples.popleft()


def sliding_input_tokens_sum() -> int:
    """Sum of recorded input tokens in the last ~60 seconds (this process)."""
    with _lock:
        _prune_unlocked()
        return sum(t for _, t in _samples)


def _sliding_budget_fraction() -> float:
    """
    Allow sliding_sum + estimated_next up to tpm_limit * this fraction (per-minute input budget).

    Default 1.0 — one large turn can legitimately use ~2× measured input (count_tokens + stream).
    Set below 1 (e.g. 0.92) if the same API key has other traffic outside this process.
    """
    raw = os.environ.get("ORCHESTRATOR_TPM_SLIDING_BUDGET_FRACTION", "1.0")
    try:
        f = float(raw)
    except ValueError:
        return 1.0
    return min(1.0, max(0.5, f))


def wait_for_sliding_tpm_headroom(
    estimated_next_input_tokens: int,
    tpm_limit: int,
    *,
    max_wait_seconds: float | None = None,
    poll_seconds: float = 1.0,
) -> None:
    """
    Block until the rolling 60s input sum plus *estimated_next_input_tokens* fits under the TPM guide.

    Prevents bursting past Anthropic input-token-per-minute limits (429) when our sliding sum is
    already high. *estimated_next_input_tokens* should include every billable input for the upcoming
    operations (e.g. count_tokens + stream for the same payload ≈ 2× a single count).
    """
    if estimated_next_input_tokens <= 0 or tpm_limit <= 0:
        return
    cap = int(tpm_limit * _sliding_budget_fraction())
    if cap <= 0:
        return
    max_wait = max_wait_seconds
    if max_wait is None:
        raw = os.environ.get("ORCHESTRATOR_TPM_SLIDING_MAX_WAIT_SECONDS", "240")
        try:
            max_wait = float(raw)
        except ValueError:
            max_wait = 240.0
    deadline = time.time() + max(5.0, max_wait)
    while True:
        s = sliding_input_tokens_sum()
        if s + estimated_next_input_tokens <= cap:
            return
        if time.time() >= deadline:
            raise RuntimeError(
                f"Input-token rate limit: ~{s:,} input tokens in the last 60s (budget ~{cap:,} "
                f"vs {tpm_limit:,}/min guide); need ~{estimated_next_input_tokens:,} more for the "
                "next call. Wait a minute and retry, or reduce concurrent chats."
            )
        time.sleep(poll_seconds)
