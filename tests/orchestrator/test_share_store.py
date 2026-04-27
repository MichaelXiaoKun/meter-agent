"""Tests for read-only conversation snapshots in ``store`` (``shares`` table)."""

from __future__ import annotations

import pytest

import store as store_mod


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated SQLite DB and reset store bootstrap state."""
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "share_test.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store_mod._bootstrapped.clear()
    store_mod._ensure_ready()
    return store_mod


def test_create_load_revoke_share(fresh_db):
    s = fresh_db
    uid = "alice"
    cid = s.create_conversation(uid, "Water report")
    s.append_messages(
        cid,
        [
            {"role": "user", "content": "Flow rate?"},
            {"role": "assistant", "content": "Here is the plot."},
        ],
    )

    token = s.create_share(cid, uid)
    assert len(token) == 32
    assert all(c in "0123456789abcdef" for c in token)

    data = s.load_share(token)
    assert data is not None
    assert data["title"] == "Water report"
    assert not data["revoked"]
    assert len(data["messages"]) == 2
    assert data["messages"][0]["role"] == "user"

    assert s.revoke_share(token, uid)
    data2 = s.load_share(token)
    assert data2 is not None
    assert data2["revoked"]


def test_create_share_wrong_user(fresh_db):
    s = fresh_db
    cid = s.create_conversation("owner", "")
    with pytest.raises(LookupError):
        s.create_share(cid, "other")


def test_revoke_wrong_user(fresh_db):
    s = fresh_db
    cid = s.create_conversation("owner", "t")
    s.append_messages(cid, [{"role": "user", "content": "x"}])
    token = s.create_share(cid, "owner")
    assert not s.revoke_share(token, "intruder")
    data = s.load_share(token)
    assert data is not None
    assert not data["revoked"]


def test_load_share_invalid_token(fresh_db):
    s = fresh_db
    assert s.load_share("") is None
    assert s.load_share("not-a-real-token-ever-12345") is None
