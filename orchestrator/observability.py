"""
observability.py — Minimal JSON-lines event emitter for the orchestrator.

Goal: give operators a queryable stream of every turn, tool call, and API
call — without pulling in a full tracing framework. One event per line,
stable keys, safe to tail / grep / pipe through ``jq``.

Configuration (env vars):
    BLUEBOT_EVENT_LOG_PATH   Absolute path. Events are appended here as JSONL.
                             Unset / empty → file sink disabled.
    BLUEBOT_EVENT_LOG_STDERR ``1`` / ``true`` → also mirror events to stderr
                             (useful during local dev). Default off.

Events are emitted as ``{"ts": ..., "event": ..., ...fields...}`` plus a
``turn_id`` whenever one is active (set via :func:`turn_context`).

Thread safety: writes are serialised behind a module-level lock so
concurrent tool workers (``compare_meters`` uses a ThreadPoolExecutor) can't
interleave half-lines.

**Context / threads:** In CPython 3.13, ``ThreadPoolExecutor`` workers do *not*
inherit ``ContextVar`` values from the submitting thread. Capture
``current_turn_id()`` in the parent and pass ``turn_id=...`` into
:func:`emit_event` from worker code (or plumb it into your task closure) so
events from thread pools stay correlated.

Redaction: arguments are emitted **as-is** in V1. When the PII-scrubbing
ticket lands, plug a redactor into :func:`_redact_fields` — the call sites
won't need to change.
"""

from __future__ import annotations

import contextvars
import json
import os
import sys
import threading
import time
import uuid
from typing import Any, Iterator, Optional

from contextlib import contextmanager


# ---------------------------------------------------------------------------
# Turn context
# ---------------------------------------------------------------------------


_turn_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "bluebot_turn_id", default=None
)


def current_turn_id() -> Optional[str]:
    """Return the active turn id, or None if we're outside a turn context."""
    return _turn_id_var.get()


@contextmanager
def turn_context(turn_id: Optional[str] = None) -> Iterator[str]:
    """Bind a turn id for the duration of the ``with`` block.

    If ``turn_id`` is not supplied, a short uuid is generated. Use at the top
    of :func:`run_turn`. For code running inside ``ThreadPoolExecutor``
    workers, also pass ``turn_id=`` into :func:`emit_event` — ContextVar is not
    always inherited by worker threads (see module docstring).
    """
    tid = turn_id or uuid.uuid4().hex[:12]
    token = _turn_id_var.set(tid)
    try:
        yield tid
    finally:
        _turn_id_var.reset(token)


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


_write_lock = threading.Lock()
_file_handle: Any = None
_file_path: Optional[str] = None
_stderr_enabled: bool = False
_initialised: bool = False


def _init_sinks() -> None:
    """Open file sink once; re-reads env on the first emit of the process."""
    global _file_handle, _file_path, _stderr_enabled, _initialised
    if _initialised:
        return
    path = (os.environ.get("BLUEBOT_EVENT_LOG_PATH") or "").strip()
    if path:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            _file_handle = open(path, "a", buffering=1, encoding="utf-8")
            _file_path = path
        except OSError:
            # Can't open the file — fall through to stderr-only / disabled.
            _file_handle = None
            _file_path = None
    raw = (os.environ.get("BLUEBOT_EVENT_LOG_STDERR") or "").strip().lower()
    _stderr_enabled = raw in ("1", "true", "yes", "on")
    _initialised = True


def _reset_for_tests() -> None:
    """Test hook only — re-read env on the next emit."""
    global _file_handle, _file_path, _stderr_enabled, _initialised
    with _write_lock:
        if _file_handle is not None:
            try:
                _file_handle.close()
            except Exception:
                pass
        _file_handle = None
        _file_path = None
        _stderr_enabled = False
        _initialised = False


# ---------------------------------------------------------------------------
# Redaction hook (future home for #6 PII-scrubbing)
# ---------------------------------------------------------------------------


def _redact_fields(fields: dict) -> dict:
    """Placeholder for the PII-scrubbing pass — returns fields unchanged today.

    Future: mask ``email``, truncate ``serial_number``, drop large payloads,
    etc. Keeping the hook in place means the emit call sites don't change
    when scrubbing lands.
    """
    return fields


def _json_default(obj: Any) -> str:
    """``json.dumps`` hook that never calls into user ``__repr__`` that may raise."""
    try:
        return str(obj)
    except Exception:
        return f"<{type(obj).__name__}>"


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------


def emit_event(event_name: str, **fields: Any) -> None:
    """Write one JSON-line event. Best-effort — never raises to the caller.

    Reserved fields injected automatically:
        ``ts``       — Unix epoch seconds, float
        ``event``    — the event name argument
        ``turn_id``  — active turn id from :func:`turn_context`, if any; or pass
                       ``turn_id="..."`` explicitly (required from thread-pool workers)
    Extra keys the caller passes are merged in. If a caller's key collides
    with a reserved name the reserved value wins.
    """
    _init_sinks()
    if _file_handle is None and not _stderr_enabled:
        return
    # Strip mistaken kwargs that would collide with the JSON envelope.
    fields = dict(fields)
    fields.pop("event", None)
    explicit_tid = fields.pop("turn_id", None)
    record: dict = {
        **_redact_fields(fields),
        "ts": round(time.time(), 6),
        "event": event_name,
    }
    tid = explicit_tid if explicit_tid is not None else current_turn_id()
    if tid is not None:
        record["turn_id"] = tid
    try:
        line = json.dumps(record, default=_json_default, ensure_ascii=False)
    except (TypeError, ValueError):
        # Last resort — drop the problematic payload so one bad arg can't kill
        # the whole stream.
        line = json.dumps(
            {
                "ts": record["ts"],
                "event": event_name,
                "turn_id": tid,
                "error": "unserialisable_fields",
            },
            ensure_ascii=False,
        )
    with _write_lock:
        if _file_handle is not None:
            try:
                _file_handle.write(line + "\n")
            except OSError:
                pass
        if _stderr_enabled:
            try:
                print(line, file=sys.stderr, flush=True)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Timing helper
# ---------------------------------------------------------------------------


@contextmanager
def timed(event: str, **fields: Any) -> Iterator[dict]:
    """Emit ``event + "_start"`` / ``event + "_end"`` pairs with ``latency_ms``.

    Yields a mutable dict the caller can mutate to attach extra end-event
    fields (e.g. output token counts). Example::

        with timed("tool_call", tool=name, args=args) as end:
            result = run_tool(...)
            end["success"] = result["success"]
            end["bytes_out"] = len(result_json)
    """
    start_fields = dict(fields)
    end_fields: dict = {}
    emit_event(event + "_start", **start_fields)
    t0 = time.perf_counter()
    error: Optional[str] = None
    try:
        yield end_fields
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        emit_fields = {**fields, **end_fields, "latency_ms": latency_ms}
        if error is not None:
            emit_fields["error"] = error
        emit_event(event + "_end", **emit_fields)
