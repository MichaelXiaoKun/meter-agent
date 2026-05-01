"""
Tests for ``shared.observability`` — JSONL event sink + turn context +
``timed`` helper.

We drive the file sink (``BLUEBOT_EVENT_LOG_PATH``) and read the lines back
to verify shape. ``_reset_for_tests`` wipes the module-level handle so each
test starts from a clean slate.
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from shared import observability as obs


@pytest.fixture
def event_log(tmp_path, monkeypatch):
    """Route observability events to a per-test JSONL file; yields a reader."""
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("BLUEBOT_EVENT_LOG_PATH", str(path))
    monkeypatch.delenv("BLUEBOT_EVENT_LOG_STDERR", raising=False)
    obs._reset_for_tests()

    def _read() -> list[dict]:
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    yield _read
    obs._reset_for_tests()


# ---------------------------------------------------------------------------
# emit_event basics
# ---------------------------------------------------------------------------


class TestEmitEvent:
    def test_event_has_ts_and_event_name(self, event_log):
        obs.emit_event("hello", foo=1)
        rows = event_log()
        assert len(rows) == 1
        r = rows[0]
        assert r["event"] == "hello"
        assert r["foo"] == 1
        assert isinstance(r["ts"], (int, float))

    def test_turn_id_auto_injected_when_context_active(self, event_log):
        with obs.turn_context("abc123"):
            obs.emit_event("e1")
        obs.emit_event("e2")  # outside context
        rows = event_log()
        assert rows[0]["turn_id"] == "abc123"
        assert "turn_id" not in rows[1]

    def test_reserved_fields_win_over_caller_args(self, event_log):
        # Caller passed ``event=foo`` — our reserved field must win.
        obs.emit_event("real_event", event="tampered")
        rows = event_log()
        assert rows[0]["event"] == "real_event"

    def test_unserialisable_arg_does_not_crash(self, event_log):
        class Weird:
            def __repr__(self):
                raise RuntimeError("boom")

        # ``default=str`` will still try repr → raises. We fall back to a
        # minimal error record rather than letting the call site crash.
        obs.emit_event("oops", weird=Weird())
        rows = event_log()
        assert rows[0]["event"] == "oops"
        # Either the record has the error sentinel OR the default=str
        # coercion succeeded. Both are acceptable; what matters is we
        # wrote exactly one line and didn't raise.

    def test_disabled_when_no_path_and_no_stderr(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BLUEBOT_EVENT_LOG_PATH", raising=False)
        monkeypatch.delenv("BLUEBOT_EVENT_LOG_STDERR", raising=False)
        obs._reset_for_tests()
        obs.emit_event("silent")  # must not raise
        # No file to read, no stderr — all good.
        assert not (tmp_path / "events.jsonl").exists()


# ---------------------------------------------------------------------------
# turn_context behaviour
# ---------------------------------------------------------------------------


class TestTurnContext:
    def test_generates_id_when_none_given(self, event_log):
        with obs.turn_context() as tid:
            assert tid
            obs.emit_event("x")
        rows = event_log()
        assert rows[0]["turn_id"] == tid

    def test_nested_contexts_restore_parent(self, event_log):
        with obs.turn_context("outer"):
            obs.emit_event("a")
            with obs.turn_context("inner"):
                obs.emit_event("b")
            obs.emit_event("c")
        rows = event_log()
        assert [r["turn_id"] for r in rows] == ["outer", "inner", "outer"]

    def test_threadpool_workers_use_explicit_turn_id(self, event_log):
        """CPython 3.13 ThreadPoolExecutor workers do not inherit ContextVar."""

        def worker(tid: str) -> None:
            obs.emit_event("from_thread", turn_id=tid)

        with obs.turn_context("parent"):
            parent_tid = obs.current_turn_id()
            assert parent_tid
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(worker, [parent_tid] * 3))

        rows = event_log()
        assert len(rows) == 3
        assert all(r["turn_id"] == parent_tid for r in rows)
        assert all(r["event"] == "from_thread" for r in rows)


# ---------------------------------------------------------------------------
# timed helper
# ---------------------------------------------------------------------------


class TestTimed:
    def test_emits_start_and_end_with_latency(self, event_log):
        with obs.timed("work", foo="bar") as end:
            end["result_size"] = 42
        rows = event_log()
        assert [r["event"] for r in rows] == ["work_start", "work_end"]
        assert rows[0]["foo"] == "bar"
        assert rows[1]["foo"] == "bar"
        assert rows[1]["result_size"] == 42
        assert rows[1]["latency_ms"] >= 0

    def test_exception_still_emits_end_with_error(self, event_log):
        with pytest.raises(RuntimeError):
            with obs.timed("work", tool="x"):
                raise RuntimeError("nope")
        rows = event_log()
        assert rows[-1]["event"] == "work_end"
        assert "RuntimeError" in rows[-1]["error"]
        assert rows[-1]["tool"] == "x"

    def test_thread_safe_concurrent_writes(self, event_log):
        # 50 threads each emit 10 events — we should get 500 valid JSON lines.
        def worker(n):
            for i in range(10):
                obs.emit_event("tick", worker=n, i=i)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = event_log()
        assert len(rows) == 500
        # Every row parses (test harness already parsed them); every row has
        # both fields intact — proves no interleaved partial lines.
        for r in rows:
            assert r["event"] == "tick"
            assert "worker" in r and "i" in r
