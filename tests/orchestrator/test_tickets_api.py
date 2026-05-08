from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[2]
_orch = str(_root / "orchestrator")
if _orch in sys.path:
    sys.path.remove(_orch)
sys.path.insert(0, _orch)

import pytest
from fastapi.testclient import TestClient

import store as store_mod


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "tickets_api.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    store_mod._bootstrapped.clear()

    import importlib
    import sys as _s

    for name in ("api", "store", "agent"):
        _s.modules.pop(name, None)

    import api as api_mod
    import store

    importlib.reload(api_mod)
    importlib.reload(store)
    store._bootstrapped.clear()
    store._ensure_ready()
    return TestClient(api_mod.app), store


def test_ticket_mutations_require_auth(client_and_store):
    client, _store = client_and_store
    r = client.post(
        "/api/tickets",
        json={
            "user_id": "u1",
            "title": "Track signal",
            "success_criteria": "Signal verified",
        },
    )
    assert r.status_code in (401, 422)


def test_ticket_api_create_list_patch_event(client_and_store):
    client, store = client_and_store
    cid = store.create_conversation("u1", "tickets")

    created = client.post(
        "/api/tickets",
        headers={"Authorization": "Bearer fake"},
        json={
            "user_id": "u1",
            "conversation_id": cid,
            "serial_number": "BB1",
            "title": "Track signal",
            "success_criteria": "Signal verified",
            "priority": "high",
        },
    )
    assert created.status_code == 200
    ticket = created.json()
    assert ticket["priority"] == "high"

    listed = client.get(
        f"/api/tickets?user_id=u1&conversation_id={cid}&serial_number=BB1&status=open"
    )
    assert listed.status_code == 200
    assert [t["id"] for t in listed.json()] == [ticket["id"]]

    patched = client.patch(
        f"/api/tickets/{ticket['id']}",
        headers={"Authorization": "Bearer fake"},
        json={
            "user_id": "u1",
            "status": "in_progress",
            "owner_type": "human",
            "owner_id": "u1",
            "note": "Claimed.",
        },
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "in_progress"

    event = client.post(
        f"/api/tickets/{ticket['id']}/events",
        headers={"Authorization": "Bearer fake"},
        json={
            "user_id": "u1",
            "event_type": "note",
            "note": "Field tech notified.",
        },
    )
    assert event.status_code == 200
    assert event.json()["note"] == "Field tech notified."


def test_ticket_api_rejects_invalid_enum(client_and_store):
    client, _store = client_and_store
    r = client.post(
        "/api/tickets",
        headers={"Authorization": "Bearer fake"},
        json={
            "user_id": "u1",
            "title": "Bad priority",
            "success_criteria": "Done",
            "priority": "whenever",
        },
    )
    assert r.status_code == 400
    assert "priority must be one of" in r.text
