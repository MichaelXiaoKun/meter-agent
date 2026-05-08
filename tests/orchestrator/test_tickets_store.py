from __future__ import annotations

import pytest

import store as store_mod


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "tickets_store.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store_mod._bootstrapped.clear()
    store_mod._ensure_ready()
    return store_mod


def test_create_list_update_ticket_and_events(fresh_db):
    s = fresh_db
    cid = s.create_conversation("alice", "triage")

    ticket = s.create_ticket(
        user_id="alice",
        conversation_id=cid,
        serial_number="BB1",
        title="Check low signal",
        description="Signal was low after install.",
        success_criteria="Signal is back in the acceptable range.",
        priority="high",
        owner_type="agent",
        owner_id="bluebot-admin-agent",
        metadata={"config_action_id": "abc123"},
    )

    assert ticket["id"].startswith("tkt_")
    assert ticket["status"] == "open"
    assert ticket["metadata"]["config_action_id"] == "abc123"

    rows = s.list_tickets("alice", conversation_id=cid, serial_number="BB1", status="open")
    assert [r["id"] for r in rows] == [ticket["id"]]

    updated = s.update_ticket(
        ticket_id=ticket["id"],
        user_id="alice",
        updates={"status": "resolved"},
        note="Signal verified after the change.",
        evidence={"signal_score": 83},
    )
    assert updated["status"] == "resolved"
    assert updated["closed_at"] is not None

    events = s.list_ticket_events(ticket["id"], "alice")
    assert [e["event_type"] for e in events] == ["created", "status:resolved"]
    assert events[-1]["evidence"]["signal_score"] == 83


def test_ticket_filters_are_user_scoped(fresh_db):
    s = fresh_db
    c1 = s.create_conversation("alice", "a")
    c2 = s.create_conversation("bob", "b")
    s.create_ticket(
        user_id="alice",
        conversation_id=c1,
        title="Alice task",
        success_criteria="Done",
    )
    s.create_ticket(
        user_id="bob",
        conversation_id=c2,
        title="Bob task",
        success_criteria="Done",
    )

    assert [t["title"] for t in s.list_tickets("alice")] == ["Alice task"]
    assert [t["title"] for t in s.list_tickets("bob")] == ["Bob task"]


def test_resolving_requires_note_or_evidence(fresh_db):
    s = fresh_db
    ticket = s.create_ticket(
        user_id="alice",
        title="Needs proof",
        success_criteria="Proof captured",
    )

    with pytest.raises(ValueError, match="requires a note or evidence"):
        s.update_ticket(
            ticket_id=ticket["id"],
            user_id="alice",
            updates={"status": "resolved"},
        )


def test_tool_evidence_records_raw_and_compact_results(fresh_db):
    s = fresh_db
    cid = s.create_conversation("alice", "evidence")

    row = s.record_tool_evidence(
        conversation_id=cid,
        turn_id="turn-1",
        tool_use_id="tool-1",
        tool_name="check_meter_status",
        input_payload={"serial_number": "BB1"},
        raw_result={"success": True, "status_data": {"signal": {"score": 82}}},
        compact_result={"success": True, "status_data": {"signal": {"score": 82}}},
        success=True,
    )

    assert row["id"].startswith("ev_")
    assert row["success"] is True
    assert row["input"]["serial_number"] == "BB1"
    assert row["raw_result"]["status_data"]["signal"]["score"] == 82
    assert len(row["result_sha256"]) == 64

    rows = s.list_tool_evidence(cid, turn_id="turn-1")
    assert [r["id"] for r in rows] == [row["id"]]
