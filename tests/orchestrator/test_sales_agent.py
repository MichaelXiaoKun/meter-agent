"""Public sales-agent tests."""

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
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "sales_agent.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    for name in ("api", "store", "agent", "sales_agent", "sales_tools"):
        sys.modules.pop(name, None)

    import api as api_mod  # noqa: WPS433
    import store  # noqa: WPS433

    importlib.reload(api_mod)
    importlib.reload(store)
    store._bootstrapped.clear()
    store._ensure_ready()
    return TestClient(api_mod.app), api_mod, store


def test_sales_tool_set_excludes_live_device_and_write_tools():
    import sales_agent

    forbidden = {
        "check_meter_status",
        "get_meter_profile",
        "list_meters_for_account",
        "analyze_flow_data",
        "configure_meter_pipe",
        "set_transducer_angle_only",
        "sweep_transducer_angles",
    }
    assert forbidden.isdisjoint(sales_agent.SALES_TOOL_NAMES)
    assert {t["name"] for t in sales_agent.TOOL_DEFINITIONS} == sales_agent.SALES_TOOL_NAMES


def test_sales_kb_retrieves_pipe_impact_guidance():
    import sales_tools

    result = sales_tools.search_sales_kb("will it damage my pipe or cause pressure drop")
    assert result["success"] is True
    ids = {row["id"] for row in result["results"]}
    assert "pipe-impact" in ids
    assert any(link["url"].startswith("https://") for link in result["relevant_links"])


def test_capture_lead_summary_persists_structured_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "lead_summary.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    for name in ("store", "sales_tools"):
        sys.modules.pop(name, None)
    import store
    import sales_tools

    importlib.reload(store)
    importlib.reload(sales_tools)
    store._bootstrapped.clear()
    store._ensure_ready()
    conv_id = store.create_sales_conversation()

    result = sales_tools.capture_lead_summary(
        conv_id,
        {
            "application": "irrigation monitoring",
            "pipe_material": "PVC",
            "pipe_size": "4 inch",
            "liquid": "water",
        },
    )

    assert result["success"] is True
    assert result["lead_summary"]["application"] == "irrigation monitoring"
    assert result["lead_summary"]["pipe_material"] == "PVC"
    assert "expected_flow_range" in result["missing_fields"]
    assert store.load_sales_lead_summary(conv_id)["pipe_size"] == "4 inch"


def test_recommend_product_line_uses_pipe_size_and_wifi_requirements():
    import sales_tools

    small_wifi = sales_tools.recommend_product_line(
        pipe_size="1 inch",
        has_reliable_wifi=True,
        needs_long_range=False,
        application="home water monitoring",
    )
    assert small_wifi["success"] is True
    assert small_wifi["recommendations"]
    assert small_wifi["recommendations"][0]["name"] in {
        "Bluebot Flagship",
        "Bluebot Mini",
    }
    assert "/shop/" in small_wifi["recommendations"][0]["source_url"]
    assert small_wifi["relevant_links"]

    large_no_wifi = sales_tools.recommend_product_line(
        pipe_size="3 inch",
        has_reliable_wifi=False,
        needs_long_range=True,
        application="irrigation monitoring",
    )
    assert large_no_wifi["success"] is True
    assert large_no_wifi["recommendations"][0]["name"] == "Bluebot ProLink Prime"
    assert large_no_wifi["recommendations"][0]["source_url"].endswith("/bluebot-prolink-prime/")


def test_public_sales_api_requires_no_auth_and_persists_lead_summary(tmp_path, monkeypatch):
    client, api_mod, store = _client_and_modules(tmp_path, monkeypatch)

    def fake_run_sales_turn(messages, *, conversation_id, on_event=None, **_kwargs):
        lead = {
            "application": "building water monitoring",
            "pipe_material": "copper",
            "liquid": "water",
        }
        store.update_sales_lead_summary(conversation_id, lead)
        if on_event:
            on_event({"type": "lead_summary", "lead_summary": lead})
            on_event({"type": "text_delta", "text": "Yes, clamp-on monitoring can be a fit."})
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Yes, clamp-on monitoring can be a fit.",
                    }
                ],
            }
        )
        return "Yes, clamp-on monitoring can be a fit."

    monkeypatch.setattr(api_mod, "run_sales_turn", fake_run_sales_turn)
    monkeypatch.setattr(api_mod, "acquire_run_turn_slot", lambda on_wait: None)
    monkeypatch.setattr(api_mod, "release_run_turn_slot", lambda: None)

    create = client.post("/api/public/sales/conversations", json={})
    assert create.status_code == 200
    conv_id = create.json()["id"]

    chat = client.post(
        f"/api/public/sales/conversations/{conv_id}/chat",
        json={"message": "Can this work for building water?", "client_turn_id": "sales-turn-1"},
    )
    assert chat.status_code == 200
    stream_id = chat.json()["stream_id"]

    done = False
    events = []
    cursor = 0
    for _ in range(50):
        poll = client.get(
            f"/api/public/sales/streams/{stream_id}/poll",
            params={"cursor": cursor, "wait_ms": 100},
        )
        assert poll.status_code == 200
        body = poll.json()
        events.extend(body["events"])
        cursor = body["next_cursor"]
        done = body["done"]
        if done:
            break
        time.sleep(0.02)

    assert done is True
    assert any(e["type"] == "lead_summary" for e in events)
    assert any(e.get("text") == "Yes, clamp-on monitoring can be a fit." for e in events)

    loaded = client.get(f"/api/public/sales/conversations/{conv_id}")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["lead_summary"]["pipe_material"] == "copper"
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][-1]["role"] == "assistant"


def test_public_sales_conversation_crud_history_without_auth(tmp_path, monkeypatch):
    client, _api_mod, _store = _client_and_modules(tmp_path, monkeypatch)

    first = client.post("/api/public/sales/conversations", json={"title": "First"})
    second = client.post("/api/public/sales/conversations", json={"title": "Second"})
    assert first.status_code == 200
    assert second.status_code == 200
    first_id = first.json()["id"]
    second_id = second.json()["id"]
    assert len(first_id) >= 16

    listed = client.get(
        "/api/public/sales/conversations",
        params={"ids": f"{second_id},{first_id}"},
    )
    assert listed.status_code == 200
    body = listed.json()
    assert [row["id"] for row in body] == [second_id, first_id]

    renamed = client.patch(
        f"/api/public/sales/conversations/{first_id}",
        json={"title": "Renamed sales thread"},
    )
    assert renamed.status_code == 200
    loaded = client.get(
        "/api/public/sales/conversations",
        params={"ids": first_id},
    ).json()
    assert loaded[0]["title"] == "Renamed sales thread"

    deleted = client.delete(f"/api/public/sales/conversations/{first_id}")
    assert deleted.status_code == 200
    missing = client.get(f"/api/public/sales/conversations/{first_id}")
    assert missing.status_code == 404


def test_public_sales_share_link_snapshot_and_revoke_without_auth(tmp_path, monkeypatch):
    client, _api_mod, store = _client_and_modules(tmp_path, monkeypatch)

    created = client.post("/api/public/sales/conversations", json={"title": "Share me"})
    assert created.status_code == 200
    conv_id = created.json()["id"]
    store.append_sales_messages(
        conv_id,
        [
            {"role": "user", "content": "Will this damage my pipe?"},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Bluebot clamps outside the pipe."}],
            },
        ],
    )

    shared = client.post(f"/api/public/sales/conversations/{conv_id}/share")
    assert shared.status_code == 200
    share_body = shared.json()
    assert share_body["token"]
    assert share_body["revoke_key"]

    public = client.get(f"/api/public/shares/{share_body['token']}")
    assert public.status_code == 200
    snapshot = public.json()
    assert snapshot["title"] == "Share me"
    assert snapshot["messages"][0]["content"] == "Will this damage my pipe?"

    denied_revoke = client.delete(
        f"/api/public/sales/shares/{share_body['token']}",
        params={"revoke_key": "wrong"},
    )
    assert denied_revoke.status_code == 404

    revoked = client.delete(
        f"/api/public/sales/shares/{share_body['token']}",
        params={"revoke_key": share_body["revoke_key"]},
    )
    assert revoked.status_code == 200
    missing = client.get(f"/api/public/shares/{share_body['token']}")
    assert missing.status_code == 404


def test_public_sales_cancel_endpoint_requires_no_auth(tmp_path, monkeypatch):
    client, _api_mod, _store = _client_and_modules(tmp_path, monkeypatch)

    created = client.post("/api/public/sales/conversations", json={"title": "Cancelable"})
    assert created.status_code == 200
    conv_id = created.json()["id"]

    cancelled = client.post(f"/api/public/sales/conversations/{conv_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True


def test_public_sales_status_endpoint_exposes_resumable_stream_without_auth(tmp_path, monkeypatch):
    client, api_mod, _store = _client_and_modules(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def slow_run_sales_turn(messages, *, conversation_id, on_event=None, **_kwargs):
        started.set()
        if on_event:
            on_event({"type": "thinking"})
        release.wait(timeout=2)
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Recovered after refresh."}],
            }
        )
        return "Recovered after refresh."

    monkeypatch.setattr(api_mod, "run_sales_turn", slow_run_sales_turn)
    monkeypatch.setattr(api_mod, "acquire_run_turn_slot", lambda on_wait: None)
    monkeypatch.setattr(api_mod, "release_run_turn_slot", lambda: None)

    created = client.post("/api/public/sales/conversations", json={"title": "Refresh"})
    assert created.status_code == 200
    conv_id = created.json()["id"]
    chat = client.post(
        f"/api/public/sales/conversations/{conv_id}/chat",
        json={"message": "Start a resumable turn", "client_turn_id": "refresh-turn"},
    )
    assert chat.status_code == 200
    assert started.wait(timeout=2)

    status = client.get(f"/api/public/sales/conversations/{conv_id}/status")
    assert status.status_code == 200
    body = status.json()
    assert body["processing"] is True
    stream_id = chat.json()["stream_id"]
    assert body["stream_id"] == stream_id
    assert body["turn_id"] == "refresh-turn"
    assert body["event_count"] >= 1

    release.set()
    cursor = 0
    for _ in range(50):
        poll = client.get(
            f"/api/public/sales/streams/{stream_id}/poll",
            params={"cursor": cursor, "wait_ms": 100},
        )
        assert poll.status_code == 200
        poll_body = poll.json()
        cursor = poll_body["next_cursor"]
        if poll_body["done"]:
            break
        time.sleep(0.02)
    else:
        raise AssertionError("sales stream did not finish")
