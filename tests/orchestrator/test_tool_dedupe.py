"""
Per-turn tool result cache — covers three cases:

1. Read-only tool called twice in one turn ⇒ second call served from cache.
2. Write tool called twice ⇒ both hit the device (never dedup'd).
3. Write tool on serial X invalidates a prior cached read on serial X.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

_ORCH_PATH = Path(__file__).resolve().parents[2] / "orchestrator" / "agent.py"
_ORCH_DIR = str(_ORCH_PATH.parent)


def _load_agent():
    sys.path.insert(0, _ORCH_DIR)
    for name in list(sys.modules):
        if name == "processors" or name.startswith("processors."):
            sys.modules.pop(name, None)
    name = "meter_orchestrator_agent_dedupe_tests"
    spec = importlib.util.spec_from_file_location(name, _ORCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _ScriptedProvider:
    """Yield a predefined sequence of LLMResponse objects on successive .stream() calls."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._i = 0

    def count_tokens(self, model, messages, system, tools):
        return 100

    def stream(self, model, messages, system, tools, max_tokens, on_text_delta):
        if self._i >= len(self._responses):
            raise AssertionError(
                f"Fake provider ran out of scripted responses (called {self._i + 1}x)"
            )
        r = self._responses[self._i]
        self._i += 1
        return r


def _tool_use_response(orch, calls: list[tuple[str, str, dict]]):
    """Build an LLMResponse that triggers one or more tool_use blocks."""
    from llm.base import LLMResponse, ToolCall

    assistant_content = []
    tool_calls = []
    for tc_id, name, inp in calls:
        assistant_content.append(
            {"type": "tool_use", "id": tc_id, "name": name, "input": inp}
        )
        tool_calls.append(ToolCall(id=tc_id, name=name, input=inp))
    return LLMResponse(
        text="",
        stop_reason="tool_use",
        tool_calls=tool_calls,
        assistant_content=assistant_content,
        input_tokens=100,
        output_tokens=10,
    )


def _end_turn_response():
    from llm.base import LLMResponse

    return LLMResponse(
        text="done",
        stop_reason="end_turn",
        assistant_content=[{"type": "text", "text": "done"}],
        input_tokens=100,
        output_tokens=2,
    )


@pytest.fixture
def event_log_path(tmp_path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("BLUEBOT_EVENT_LOG_PATH", str(path))
    monkeypatch.delenv("BLUEBOT_EVENT_LOG_STDERR", raising=False)
    import observability as obs  # pyright: ignore[reportMissingImports]

    obs._reset_for_tests()
    yield path
    obs._reset_for_tests()


def _read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(s)
        for s in path.read_text(encoding="utf-8").splitlines()
        if s.strip()
    ]


def _stub_common(monkeypatch, orch):
    monkeypatch.setattr(orch, "get_cheap_model", lambda m: "claude-haiku-4-5")
    monkeypatch.setattr(orch, "wait_for_sliding_tpm_headroom", lambda *a, **k: None)
    monkeypatch.setenv("ORCHESTRATOR_INTENT_ROUTER", "off")


def test_read_only_tool_is_deduped_within_one_turn(
    event_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _load_agent()
    _stub_common(monkeypatch, orch)

    dispatch_calls: list[tuple[str, dict]] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append((name, dict(inp)))
        return json.dumps({"success": True, "serial_number": inp.get("serial_number")})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)

    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    ("t1", "check_meter_status", {"serial_number": "BB1"}),
                    ("t2", "check_meter_status", {"serial_number": "BB1"}),
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    reply, _ = orch.run_turn(
        [{"role": "user", "content": "status?"}],
        token="tok",
        model=orch._MODEL,
    )
    assert reply == "done"
    assert len(dispatch_calls) == 1, dispatch_calls

    events = _read_events(event_log_path)
    hits = [e for e in events if e.get("event") == "tool_dedupe_hit"]
    assert len(hits) == 1
    assert hits[0]["tool"] == "check_meter_status"
    assert hits[0]["serial_number"] == "BB1"


def test_fleet_health_tool_emits_heartbeat_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = _load_agent()
    progress_events: list[dict] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        assert name == "rank_fleet_by_health"
        time.sleep(0.03)
        return json.dumps(
            {
                "success": True,
                "meters": [{"serial_number": "BB1", "health_score": 92}],
                "failed_serials": None,
                "error": None,
            }
        )

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)

    result_json = orch._run_dispatch_with_heartbeat_progress(  # noqa: SLF001
        "rank_fleet_by_health",
        {"serial_numbers": ["BB1"]},
        "token",
        client_timezone=None,
        anthropic_api_key=None,
        emit=progress_events.append,
        heartbeat_seconds=0.01,
    )

    assert json.loads(result_json)["success"] is True
    messages = [
        ev["message"]
        for ev in progress_events
        if ev.get("type") == "tool_progress"
        and ev.get("tool") == "rank_fleet_by_health"
    ]
    assert messages[0].startswith("Fleet health ranking for 1 meter(s): started")
    assert any("still checking meters" in msg for msg in messages[1:])


def test_write_tools_require_confirmation_before_dispatch(
    event_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _load_agent()
    _stub_common(monkeypatch, orch)

    dispatch_calls: list[tuple[str, dict]] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append((name, dict(inp)))
        return json.dumps({"success": True})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)
    monkeypatch.setattr(
        orch,
        "get_meter_profile",
        lambda serial, token: {
            "success": True,
            "network_type": "wifi",
            "profile": {"label": "Test meter"},
            "transducer_angle_options": ["30", "45"],
        },
    )

    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "w1",
                        "set_transducer_angle_only",
                        {"serial_number": "BB1", "transducer_angle": 30},
                    ),
                    (
                        "w2",
                        "set_transducer_angle_only",
                        {"serial_number": "BB1", "transducer_angle": 30},
                    ),
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "set angle"}],
        token="tok",
        model=orch._MODEL,
        conversation_id="conv",
    )
    assert len(dispatch_calls) == 0

    events = _read_events(event_log_path)
    assert not any(e.get("event") == "tool_dedupe_hit" for e in events)


def test_unconfirmed_write_does_not_invalidate_cached_read_for_same_serial(
    event_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _load_agent()
    _stub_common(monkeypatch, orch)

    dispatch_calls: list[tuple[str, dict]] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append((name, dict(inp)))
        return json.dumps({"success": True, "serial_number": inp.get("serial_number")})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)
    monkeypatch.setattr(
        orch,
        "get_meter_profile",
        lambda serial, token: {
            "success": True,
            "network_type": "wifi",
            "profile": {"label": "Test meter"},
            "transducer_angle_options": ["30", "45"],
        },
    )

    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch, [("r1", "check_meter_status", {"serial_number": "BB1"})]
            ),
            _tool_use_response(
                orch,
                [
                    (
                        "w1",
                        "set_transducer_angle_only",
                        {"serial_number": "BB1", "transducer_angle": 30},
                    )
                ],
            ),
            _tool_use_response(
                orch, [("r2", "check_meter_status", {"serial_number": "BB1"})]
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "status, set, verify"}],
        token="tok",
        model=orch._MODEL,
    )

    names = [name for (name, _args) in dispatch_calls]
    assert names == [
        "check_meter_status",
    ]

    events = _read_events(event_log_path)
    assert not any(e.get("event") == "tool_dedupe_invalidate" for e in events)
    # Pending confirmation now short-circuits the turn, so no later read runs
    # and there is no dedupe hit to report.
    assert not any(e.get("event") == "tool_dedupe_hit" for e in events)


def test_dedupe_key_is_canonical_regardless_of_arg_order(
    event_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _load_agent()
    _stub_common(monkeypatch, orch)

    dispatch_calls: list[tuple[str, dict]] = []

    def fake_dispatch(name, inp, token, *, client_timezone, anthropic_api_key):
        dispatch_calls.append((name, dict(inp)))
        return json.dumps({"success": True})

    monkeypatch.setattr(orch, "_dispatch", fake_dispatch)

    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "g1",
                        "get_meter_profile",
                        {"serial_number": "BB1", "include_history": True},
                    ),
                    (
                        "g2",
                        "get_meter_profile",
                        {"include_history": True, "serial_number": "BB1"},
                    ),
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    orch.run_turn(
        [{"role": "user", "content": "get profile"}],
        token="tok",
        model=orch._MODEL,
    )
    assert len(dispatch_calls) == 1

    events = _read_events(event_log_path)
    assert any(e.get("event") == "tool_dedupe_hit" for e in events)


def test_flow_tool_history_payload_is_compacted(monkeypatch: pytest.MonkeyPatch) -> None:
    orch = _load_agent()
    monkeypatch.setenv("ORCHESTRATOR_FLOW_REPORT_EXCERPT_CHARS", "200")
    result = {
        "success": True,
        "report": "A" * 10_000 + "SECRET_TAIL",
        "display_range": "Jan 1 -> Jan 2",
        "analysis_mode": "summary",
        "analysis_json_path": "/tmp/analysis.json",
        "report_path": "/tmp/report.md",
        "reasoning_schema": {"regime": "STEADY_FLOW"},
        "analysis_details": {"rollup_highlights": {"six_hour_problem_window_count": 1}},
        "download_artifacts": [
            {
                "kind": "csv",
                "title": "Flow data CSV",
                "filename": "flow_data_BB1_1_2.csv",
                "path": "/private/flow_data_BB1_1_2.csv",
                "row_count": 42,
            }
        ],
        "analysis_metadata": {
            "analysis_mode": "summary",
            "requested_analysis_mode": "auto",
            "mode_selection_reasons": ["range_exceeds_threshold"],
            "fetch": {"chunk_count": 24, "fetch_workers": 8},
            "report_path": "/tmp/report.md",
            "mode_selection": {"large": "omitted"},
        },
    }

    compact = orch._compact_tool_result_for_history("analyze_flow_data", result)
    encoded = json.dumps(compact)

    assert "report" not in compact
    assert compact["report_excerpt"].startswith("A")
    assert "SECRET_TAIL" not in encoded
    assert compact["reasoning_schema"] == {"regime": "STEADY_FLOW"}
    assert compact["analysis_json_path"] == "/tmp/analysis.json"
    assert "mode_selection" not in compact["analysis_metadata"]
    assert compact["download_artifacts"] == [
        {
            "kind": "csv",
            "title": "Flow data CSV",
            "filename": "flow_data_BB1_1_2.csv",
            "row_count": 42,
        }
    ]
    assert "/private/" not in encoded


def test_run_turn_persists_compact_flow_tool_result(
    event_log_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _load_agent()
    _stub_common(monkeypatch, orch)
    monkeypatch.setenv("ORCHESTRATOR_FLOW_REPORT_EXCERPT_CHARS", "200")

    full_result = {
        "success": True,
        "report": "LONG_REPORT_START\n" + ("x" * 10_000) + "\nSECRET_TAIL",
        "display_range": "Jan 1 -> Jan 2",
        "plot_paths": ["/tmp/p.png"],
        "plot_summaries": [{"plot_type": "diagnostic_timeline"}],
        "analysis_mode": "summary",
        "analysis_json_path": "/tmp/analysis.json",
        "report_path": "/tmp/report.md",
        "reasoning_schema": {"regime": "STEADY_FLOW"},
        "analysis_details": {"rollup_highlights": {"six_hour_problem_window_count": 1}},
        "analysis_metadata": {"analysis_mode": "summary", "fetch": {"chunk_count": 24}},
        "download_artifacts": [
            {
                "kind": "csv",
                "title": "Flow data CSV",
                "filename": "flow_data_BB1_1_2.csv",
                "path": "/private/flow_data_BB1_1_2.csv",
                "row_count": 42,
            }
        ],
        "error": None,
    }

    monkeypatch.setattr(
        orch,
        "_run_analyze_flow_with_progress",
        lambda *a, **k: json.dumps(full_result),
    )
    provider = _ScriptedProvider(
        [
            _tool_use_response(
                orch,
                [
                    (
                        "f1",
                        "analyze_flow_data",
                        {"serial_number": "BB1", "start": 0, "end": 10},
                    )
                ],
            ),
            _end_turn_response(),
        ]
    )
    monkeypatch.setattr(orch, "get_provider", lambda *a, **k: provider)

    messages = [{"role": "user", "content": "analyze flow"}]
    reply, _ = orch.run_turn(messages, token="tok", model=orch._MODEL)

    assert reply == "done"
    tool_result_messages = [
        m for m in messages
        if m.get("role") == "user" and isinstance(m.get("content"), list)
    ]
    assert tool_result_messages
    content = tool_result_messages[-1]["content"][0]["content"]
    payload = json.loads(content)
    encoded_history = json.dumps(messages)

    assert "report" not in payload
    assert payload["report_excerpt"].startswith("LONG_REPORT_START")
    assert payload["analysis_json_path"] == "/tmp/analysis.json"
    assert payload["reasoning_schema"] == {"regime": "STEADY_FLOW"}
    assert payload["download_artifacts"][0]["filename"] == "flow_data_BB1_1_2.csv"
    assert "path" not in payload["download_artifacts"][0]
    assert "SECRET_TAIL" not in encoded_history
    assert "/private/" not in encoded_history
