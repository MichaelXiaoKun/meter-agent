"""HTTP tests for ``/api/conversations/{id}/share`` and public read.

``conftest`` prepends *data-processing-agent* before *orchestrator* on
``sys.path``, so :mod:`api` (which does ``from agent import …``) would import
the wrong *agent* unless we put *orchestrator* first for this module.
"""

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
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "share_api.db"))
    # api.py loads repo .env on import; keep DATABASE_URL present-but-empty so
    # python-dotenv does not repopulate a developer Postgres DSN.
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


def test_post_share_requires_auth(client_and_store):
    client, store = client_and_store
    uid = "u1"
    cid = store.create_conversation(uid, "Hi")
    store.append_messages(cid, [{"role": "user", "content": "test"}])
    r = client.post(
        f"/api/conversations/{cid}/share",
        json={"user_id": uid},
    )
    assert r.status_code in (401, 422)


def test_public_share_happy_path(client_and_store):
    client, store = client_and_store
    uid = "u1"
    cid = store.create_conversation(uid, "Titled")
    store.append_messages(
        cid,
        [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
    )
    r = client.post(
        f"/api/conversations/{cid}/share",
        json={"user_id": uid},
        headers={"Authorization": "Bearer fake.jwt"},
    )
    assert r.status_code == 200
    token = r.json()["token"]
    g = client.get(f"/api/public/shares/{token}")
    assert g.status_code == 200
    body = g.json()
    assert body["title"] == "Titled"
    assert len(body["messages"]) == 2


def test_public_share_revoked_404(client_and_store):
    client, store = client_and_store
    uid = "u1"
    cid = store.create_conversation(uid, "x")
    store.append_messages(cid, [{"role": "user", "content": "y"}])
    r = client.post(
        f"/api/conversations/{cid}/share",
        json={"user_id": uid},
        headers={"Authorization": "Bearer x"},
    )
    token = r.json()["token"]
    d = client.delete(
        f"/api/shares/{token}?user_id={uid}",
        headers={"Authorization": "Bearer x"},
    )
    assert d.status_code == 200
    g = client.get(f"/api/public/shares/{token}")
    assert g.status_code == 404
