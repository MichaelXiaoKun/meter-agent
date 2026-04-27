from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).with_name("capture_baseline.py")
_SPEC = importlib.util.spec_from_file_location("capture_baseline", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
capture_baseline_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(capture_baseline_mod)
capture_baseline = capture_baseline_mod.capture_baseline


def test_capture_baseline_groups_turn_events() -> None:
    baseline = capture_baseline(
        [
            {
                "event": "turn_start",
                "turn_id": "t1",
                "intent": "flow",
                "intent_source": "rules",
                "prompt_version": "v1",
                "tool_names": ["resolve_time_range", "analyze_flow_data"],
            },
            {
                "event": "api_call_end",
                "turn_id": "t1",
                "model": "claude",
                "attempt": 1,
                "stop_reason": "tool_use",
                "input_tokens": 100,
                "output_tokens": 20,
            },
            {
                "event": "tool_call_end",
                "turn_id": "t1",
                "tool": "analyze_flow_data",
                "success": True,
                "cached": False,
                "round": 1,
            },
            {"event": "turn_end", "turn_id": "t1", "outcome": "end_turn"},
        ]
    )

    assert baseline["turn_count"] == 1
    turn = baseline["turns"][0]
    assert turn["intent"] == "flow"
    assert turn["tool_calls"] == [
        {"tool": "analyze_flow_data", "success": True, "cached": False, "round": 1}
    ]
    assert turn["api_calls"][0]["stop_reason"] == "tool_use"
    assert turn["outcome"] == "end_turn"
