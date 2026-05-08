"""Tool-result compaction and event shaping helpers for admin chat."""

from __future__ import annotations

from admin_chat.turn_loop import (
    _clip_activity,
    _compact_analysis_metadata,
    _compact_download_artifacts,
    _compact_flow_result_for_history,
    _compact_report_excerpt,
    _compact_tool_result_for_history,
    _compact_tool_result_json_for_history,
    _diagnostic_summary_from_result,
    _emit_preflight_evidence,
    _emit_tool_result_event,
    _flow_report_excerpt_max_chars,
    _meter_context_from_result,
    _record_sweep_angle_evidence,
    _record_tool_evidence_safe,
    _sse_tool_succeeded,
    _tool_activity_line,
)

__all__ = [
    "_clip_activity",
    "_compact_analysis_metadata",
    "_compact_download_artifacts",
    "_compact_flow_result_for_history",
    "_compact_report_excerpt",
    "_compact_tool_result_for_history",
    "_compact_tool_result_json_for_history",
    "_diagnostic_summary_from_result",
    "_emit_preflight_evidence",
    "_emit_tool_result_event",
    "_flow_report_excerpt_max_chars",
    "_meter_context_from_result",
    "_record_sweep_angle_evidence",
    "_record_tool_evidence_safe",
    "_sse_tool_succeeded",
    "_tool_activity_line",
]
