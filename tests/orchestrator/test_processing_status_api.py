"""Tests for resumable processing status metadata."""

from __future__ import annotations

import importlib
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

_root = Path(__file__).resolve().parents[2]
_orch = str(_root / "orchestrator")
if _orch in sys.path:
    sys.path.remove(_orch)
sys.path.insert(0, _orch)


def _client_and_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "processing_status.db"))
    # api.py loads the repo .env on import; keep DATABASE_URL present-but-empty
    # so python-dotenv does not repopulate a developer Postgres DSN.
    monkeypatch.setenv("DATABASE_URL", "")
    for name in ("api", "store", "agent"):
        sys.modules.pop(name, None)

    import api as api_mod  # noqa: WPS433
    import store  # noqa: WPS433

    importlib.reload(api_mod)
    importlib.reload(store)
    store._bootstrapped.clear()
    store._ensure_ready()
    return TestClient(api_mod.app), api_mod, store


def test_status_exposes_active_stream_metadata_while_queued(tmp_path, monkeypatch):
    client, api_mod, store = _client_and_modules(tmp_path, monkeypatch)
    slot_release = threading.Event()

    def fake_acquire_run_turn_slot(on_wait):
        on_wait()
        assert slot_release.wait(timeout=2)

    def fake_run_turn(messages, _token, **_kwargs):
        messages.append({"role": "assistant", "content": "done"})
        return "done", False

    monkeypatch.setattr(api_mod, "acquire_run_turn_slot", fake_acquire_run_turn_slot)
    monkeypatch.setattr(api_mod, "run_turn", fake_run_turn)
    monkeypatch.setattr(api_mod, "update_title", lambda *_args, **_kwargs: None)

    cid = store.create_conversation("u1", "status")
    response = client.post(
        f"/api/conversations/{cid}/chat",
        json={"message": "hello", "client_turn_id": "turn-1"},
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200
    stream_id = response.json()["stream_id"]

    status = {}
    for _ in range(50):
        status = client.get(f"/api/conversations/{cid}/status").json()
        if status.get("event_count", 0) >= 1:
            break
        time.sleep(0.02)

    assert status["processing"] is True
    assert status["stream_id"] == stream_id
    assert status["turn_id"] == "turn-1"
    assert status["event_count"] >= 1
    assert status["done"] is False

    slot_release.set()
    for _ in range(50):
        status = client.get(f"/api/conversations/{cid}/status").json()
        if status["processing"] is False:
            break
        time.sleep(0.02)

    assert status == {"processing": False}


def test_full_thread_compression_still_persists_streamed_reply(tmp_path, monkeypatch):
    client, api_mod, store = _client_and_modules(tmp_path, monkeypatch)

    def fake_run_turn(messages, _token, **kwargs):
        on_event = kwargs.get("on_event")
        messages.clear()
        messages.append(
            {
                "role": "user",
                "content": "[Full thread compressed — TPM budget]\nEarlier context.",
            }
        )
        if on_event:
            on_event({"type": "text_delta", "text": "Final answer after compression."})
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Final answer after compression."}],
            }
        )
        return "Final answer after compression.", True

    monkeypatch.setattr(api_mod, "run_turn", fake_run_turn)
    monkeypatch.setattr(api_mod, "update_title", lambda *_args, **_kwargs: None)

    cid = store.create_conversation("u1", "compression")
    store.append_messages(
        cid,
        [
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": [{"type": "text", "text": "Earlier answer"}]},
        ],
    )

    response = client.post(
        f"/api/conversations/{cid}/chat",
        json={"message": "Large fleet question", "client_turn_id": "turn-compress"},
        headers={"Authorization": "Bearer token"},
    )
    assert response.status_code == 200

    for _ in range(50):
        if client.get(f"/api/conversations/{cid}/status").json()["processing"] is False:
            break
        time.sleep(0.02)

    messages = store.load_messages(cid)
    assert messages[-2] == {"role": "user", "content": "Large fleet question"}
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"][0]["text"] == "Final answer after compression."

    summary, covers = store.get_api_context_info(cid)
    assert summary == "[Full thread compressed — TPM budget]\nEarlier context."
    assert covers == 3


def test_stream_gc_preserves_stale_active_unfinished_stream(tmp_path, monkeypatch):
    _client, api_mod, store = _client_and_modules(tmp_path, monkeypatch)
    cid = store.create_conversation("u1", "long running")
    sid = "stream-active"
    with api_mod._streams_lock:  # noqa: SLF001
        api_mod._streams[sid] = {  # noqa: SLF001
            "events": [],
            "done": False,
            "cond": threading.Condition(),
            "created": time.monotonic() - api_mod._STREAM_TTL_SEC - 5,  # noqa: SLF001
            "sse_consumed": True,
            "turn_id": "turn-active",
            "conv_id": cid,
        }
        api_mod._active_conversations.add(cid)  # noqa: SLF001
        api_mod._active_conversation_streams[cid] = sid  # noqa: SLF001

    try:
        api_mod._gc_streams()  # noqa: SLF001
        with api_mod._streams_lock:  # noqa: SLF001
            assert sid in api_mod._streams  # noqa: SLF001
            assert api_mod._active_conversation_streams[cid] == sid  # noqa: SLF001
    finally:
        with api_mod._streams_lock:  # noqa: SLF001
            api_mod._streams.pop(sid, None)  # noqa: SLF001
            api_mod._active_conversations.discard(cid)  # noqa: SLF001
            api_mod._active_conversation_streams.pop(cid, None)  # noqa: SLF001


def test_stream_gc_removes_stale_done_stream(tmp_path, monkeypatch):
    _client, api_mod, store = _client_and_modules(tmp_path, monkeypatch)
    cid = store.create_conversation("u1", "done")
    sid = "stream-done"
    with api_mod._streams_lock:  # noqa: SLF001
        api_mod._streams[sid] = {  # noqa: SLF001
            "events": [],
            "done": True,
            "cond": threading.Condition(),
            "created": time.monotonic() - api_mod._STREAM_TTL_SEC - 5,  # noqa: SLF001
            "sse_consumed": True,
            "turn_id": "turn-done",
            "conv_id": cid,
        }
        api_mod._active_conversation_streams[cid] = sid  # noqa: SLF001

    api_mod._gc_streams()  # noqa: SLF001

    with api_mod._streams_lock:  # noqa: SLF001
        assert sid not in api_mod._streams  # noqa: SLF001
        assert cid not in api_mod._active_conversation_streams  # noqa: SLF001
