"""
meter_compare.py — Orchestrator tool for comparing 2–10 bluebot meters
side-by-side on device metadata and current health status.

Under the hood this fans out to :func:`tools.meter_profile.get_meter_profile`
and :func:`tools.meter_status.check_meter_status` for every requested serial
(in parallel, with a small worker pool), then stitches the structured
per-meter data and a pre-computed ``differences`` block.

The value of this tool over N separate ``get_meter_profile`` /
``check_meter_status`` calls is the **pre-computed diff**: the caller (LLM) no
longer has to eyeball N dicts to find the odd one out — the tool reports
which fields disagree and who has what value.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from tools.meter_profile import get_meter_profile
from tools.meter_status import check_meter_status


_MIN_SERIALS = 2
_MAX_SERIALS = 10
_MAX_WORKERS = 5

# Ordered list of fields included in ``differences`` / ``uniform_fields``.
# Label, installedOn, organization_name, signal.score, seconds_since, and
# inner_diameter_mm are intentionally excluded from diffing — they are either
# expected-to-differ (label/org) or continuous values where strict equality
# is the wrong comparison; they still appear in ``per_meter`` for the LLM.
_DIFF_FIELDS: tuple[str, ...] = (
    "model",
    "network_type",
    "deviceTimeZone",
    "commissioned",
    "installed",
    "active",
    "category",
    "online",
    "communication_status",
    "signal_level",
    "signal_reliable",
    "pipe_nominal_size",
    "pipe_standard",
)


TOOL_DEFINITION = {
    "name": "compare_meters",
    "description": (
        "Compare 2–10 bluebot meters side-by-side on device metadata "
        "(model, network_type, deviceTimeZone, installed/commissioned flags) "
        "AND current health (online, communication_status, signal level, "
        "pipe nominal size/standard). Use when the user asks to diff a set "
        "of meters ('are these 3 configured the same?', 'which of these is "
        "the odd one out?', 'compare BB1 and BB2'). Returns per-meter fields "
        "plus a pre-computed ``differences`` block highlighting which fields "
        "disagree and who has what, and ``uniform_fields`` for fields that "
        "agree across all meters. Prefer this over calling get_meter_profile "
        "or check_meter_status separately for each serial."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": _MIN_SERIALS,
                "maxItems": _MAX_SERIALS,
                "description": (
                    "List of meter serial numbers to compare (verbatim from "
                    "the user's message). Duplicates are deduplicated while "
                    "preserving first-occurrence order."
                ),
            },
        },
        "required": ["serial_numbers"],
    },
}


# ---------------------------------------------------------------------------
# Per-meter fetch + flatten
# ---------------------------------------------------------------------------


def _flatten_meter(
    serial: str,
    profile_result: Dict[str, Any],
    status_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge profile + status into a single flat dict keyed for diffing.

    Fields that couldn't be fetched come back as ``None``. Per-source errors
    are kept on ``profile_error`` / ``status_error`` so the LLM can say
    "couldn't read BB3's status" without losing the profile side.
    """
    profile = profile_result.get("profile") or {}
    status_data = status_result.get("status_data") or {}
    staleness = (status_data.get("staleness") or {}) if status_data else {}
    signal = (status_data.get("signal") or {}) if status_data else {}
    pipe = (status_data.get("pipe_config") or {}) if status_data else {}
    health = (status_data.get("health_score") or {}) if status_data else {}

    return {
        "serial_number": serial,
        "profile_error": None if profile_result.get("success") else profile_result.get("error"),
        "status_error": None if status_result.get("success") else status_result.get("error"),
        # Profile fields
        "label": profile.get("label"),
        "model": profile.get("model"),
        "category": profile.get("category"),
        "network_type": profile_result.get("network_type"),
        "deviceTimeZone": profile.get("deviceTimeZone"),
        "commissioned": profile.get("commissioned"),
        "installed": profile.get("installed"),
        "installedOn": profile.get("installedOn"),
        "active": profile.get("active"),
        "organization_name": profile.get("organization_name"),
        # Status fields
        "online": status_data.get("online") if status_data else None,
        "last_message_at": status_data.get("last_message_at") if status_data else None,
        "communication_status": staleness.get("communication_status") or None,
        "seconds_since": staleness.get("seconds_since"),
        "signal_score": signal.get("score"),
        "signal_level": signal.get("level"),
        "signal_reliable": signal.get("reliable"),
        "health_score": health.get("score"),
        "health_verdict": health.get("verdict"),
        "health_score_components": health.get("components"),
        "pipe_nominal_size": pipe.get("nominal_size"),
        "pipe_standard": pipe.get("pipe_standard"),
        "pipe_inner_diameter_mm": pipe.get("inner_diameter_mm"),
    }


def _fetch_one(serial: str, token: str, anthropic_api_key: Optional[str]) -> Dict[str, Any]:
    """Fetch profile + status for one meter. Per-source failures are captured,
    never raised — a meter with e.g. a dead profile but live status still
    contributes what it can to the comparison."""
    profile_result = get_meter_profile(serial, token)
    status_result = check_meter_status(
        serial, token, anthropic_api_key=anthropic_api_key
    )
    return _flatten_meter(serial, profile_result, status_result)


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def _dedup_serials(serials: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for s in serials:
        if not isinstance(s, str):
            continue
        clean = s.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
    return out


def _field_groups(field: str, per_meter: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Group serials by the value they have for ``field``. Missing values are
    collected under the sentinel key ``"__missing__"`` so the LLM can tell
    "we don't know" apart from "definitely null/false/etc."."""
    groups: Dict[str, List[str]] = {}
    for m in per_meter:
        raw = m.get(field)
        key = "__missing__" if raw is None else str(raw)
        groups.setdefault(key, []).append(m["serial_number"])
    return groups


def _compute_differences(per_meter: List[Dict[str, Any]]) -> Dict[str, Any]:
    differences: Dict[str, Any] = {}
    uniform_fields: List[str] = []
    for field in _DIFF_FIELDS:
        groups = _field_groups(field, per_meter)
        # Skip fields that are missing from *every* meter — not informative.
        if set(groups.keys()) == {"__missing__"}:
            continue
        if len(groups) == 1:
            uniform_fields.append(field)
        else:
            differences[field] = {"uniform": False, "groups": groups}
    return {"differences": differences, "uniform_fields": uniform_fields}


def _build_summary(
    per_meter: List[Dict[str, Any]],
    differences: Dict[str, Any],
    uniform_fields: List[str],
    failed: List[Dict[str, str]],
) -> str:
    total = len(per_meter) + len(failed)
    ok = len(per_meter)
    parts = [f"{ok}/{total} meters fetched."]
    if differences:
        parts.append(f"Disagree on: {', '.join(sorted(differences.keys()))}.")
    else:
        parts.append("No field disagreements among the compared meters.")
    if uniform_fields:
        parts.append(f"Agree on: {', '.join(uniform_fields)}.")
    if failed:
        bad = ", ".join(f["serial_number"] for f in failed)
        parts.append(f"Unreachable: {bad}.")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compare_meters(
    serial_numbers: List[str],
    token: str,
    *,
    anthropic_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Compare profile + status fields across 2–10 bluebot meters.

    Returns a dict with a stable shape for both success and failure — the
    orchestrator never needs to handle raised exceptions.

    Shape::

        {
            "success":          bool,
            "serial_numbers":   list[str],         # deduped, order preserved
            "requested_count":  int,
            "successful_count": int,               # meters with at least
                                                   # profile OR status ok
            "failed_count":     int,
            "failures":         list[{"serial_number": str, "error": str}],
            "per_meter":        list[dict],        # flat per-meter dict (see
                                                   # ``_flatten_meter``) for
                                                   # every serial that had
                                                   # *any* data — failed rows
                                                   # are under ``failures``
            "differences":      {field: {"uniform": False, "groups": {...}}},
            "uniform_fields":   list[str],         # fields all meters agree on
            "summary":          str,
            "error":            str | None,        # envelope-level error
                                                   # (e.g. too few serials)
        }
    """
    cleaned = _dedup_serials(serial_numbers or [])
    base: Dict[str, Any] = {
        "success": False,
        "serial_numbers": cleaned,
        "requested_count": len(cleaned),
        "successful_count": 0,
        "failed_count": 0,
        "failures": [],
        "per_meter": [],
        "differences": {},
        "uniform_fields": [],
        "summary": "",
        "error": None,
    }

    if not token:
        return {**base, "error": "Bearer token required for the bluebot APIs."}
    if len(cleaned) < _MIN_SERIALS:
        return {
            **base,
            "error": (
                f"compare_meters needs at least {_MIN_SERIALS} distinct serial "
                f"numbers; got {len(cleaned)}."
            ),
        }
    if len(cleaned) > _MAX_SERIALS:
        return {
            **base,
            "error": (
                f"compare_meters accepts at most {_MAX_SERIALS} serials per call; "
                f"got {len(cleaned)}. Split into smaller batches."
            ),
        }

    # Fan out: each worker runs profile + status sequentially for one serial.
    results_by_serial: Dict[str, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(cleaned))) as pool:
        futures = {
            pool.submit(_fetch_one, sn, token, anthropic_api_key): sn
            for sn in cleaned
        }
        for fut in as_completed(futures):
            sn = futures[fut]
            try:
                results_by_serial[sn] = fut.result()
            except Exception as exc:
                results_by_serial[sn] = {
                    "serial_number": sn,
                    "profile_error": f"{type(exc).__name__}: {exc}",
                    "status_error": f"{type(exc).__name__}: {exc}",
                }

    per_meter: List[Dict[str, Any]] = []
    failures: List[Dict[str, str]] = []
    for sn in cleaned:
        row = results_by_serial.get(sn, {})
        profile_ok = row.get("profile_error") is None and row.get("model") is not None
        status_ok = row.get("status_error") is None and row.get("online") is not None
        if profile_ok or status_ok:
            per_meter.append(row)
        else:
            err = row.get("profile_error") or row.get("status_error") or "unknown error"
            failures.append({"serial_number": sn, "error": err})

    diff_block = _compute_differences(per_meter) if per_meter else {
        "differences": {},
        "uniform_fields": [],
    }
    summary = _build_summary(
        per_meter, diff_block["differences"], diff_block["uniform_fields"], failures
    )

    return {
        "success": True,
        "serial_numbers": cleaned,
        "requested_count": len(cleaned),
        "successful_count": len(per_meter),
        "failed_count": len(failures),
        "failures": failures,
        "per_meter": per_meter,
        "differences": diff_block["differences"],
        "uniform_fields": diff_block["uniform_fields"],
        "summary": summary,
        "error": None,
    }
