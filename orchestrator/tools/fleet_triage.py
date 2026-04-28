"""
fleet_triage.py — Account-level fleet health triage.
"""

from __future__ import annotations

from typing import Any, Dict, List

from tools.fleet_health import rank_fleet_by_health
from tools.meters_by_email import list_meters_for_account


_MAX_METERS = 50


TOOL_DEFINITION: dict[str, Any] = {
    "name": "triage_fleet_for_account",
    "description": (
        "List meters for an account email, then rank up to 50 of them by current "
        "composite health score. Use when the user asks which meters on an "
        "account need attention, how the fleet is looking, or for account-level "
        "triage. Returns a compact table with serial, health score, signal, last "
        "seen age, and top concern."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "email": {
                "type": "string",
                "description": "Bluebot account email address, verbatim from the user's message.",
            }
        },
        "required": ["email"],
    },
}


def _serial_from_meter(row: dict) -> str | None:
    for key in ("serialNumber", "serial_number", "serial"):
        raw = row.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _compact_row(row: dict) -> dict:
    return {
        "serial": row.get("serial_number"),
        "label": row.get("label"),
        "health_score": row.get("health_score"),
        "health_verdict": row.get("health_verdict"),
        "status": row.get("communication_status"),
        "online": row.get("online"),
        "signal": {
            "score": row.get("signal_score"),
            "level": row.get("signal_level"),
        },
        "last_seen_age_seconds": row.get("seconds_since"),
        "network_type": row.get("network_type"),
        "deviceTimeZone": row.get("deviceTimeZone"),
        "top_concern": row.get("top_concern"),
        "profile_error": row.get("profile_error"),
        "status_error": row.get("status_error"),
    }


def triage_fleet_for_account(
    email: str,
    token: str,
    *,
    anthropic_api_key: str | None = None,
) -> Dict[str, Any]:
    cleaned_email = (email or "").strip()
    if not cleaned_email:
        return {
            "success": False,
            "email": cleaned_email,
            "meters": [],
            "failed_serials": None,
            "total_count": 0,
            "returned_count": 0,
            "truncated": False,
            "notice": None,
            "error": "Email is required.",
        }
    if not token:
        return {
            "success": False,
            "email": cleaned_email,
            "meters": [],
            "failed_serials": None,
            "total_count": 0,
            "returned_count": 0,
            "truncated": False,
            "notice": None,
            "error": "Bearer token required.",
        }

    listing = list_meters_for_account(cleaned_email, token, limit=_MAX_METERS)
    if not listing.get("success"):
        return {
            "success": False,
            "email": cleaned_email,
            "meters": [],
            "failed_serials": None,
            "total_count": listing.get("total_count", 0),
            "returned_count": listing.get("returned_count", 0),
            "truncated": bool(listing.get("truncated")),
            "notice": listing.get("notice"),
            "error": listing.get("error") or "Unable to list meters for account.",
            "error_stage": listing.get("error_stage"),
            "error_code": listing.get("error_code"),
        }

    source_meters = listing.get("meters") if isinstance(listing.get("meters"), list) else []
    serials: List[str] = []
    for row in source_meters:
        if not isinstance(row, dict):
            continue
        serial = _serial_from_meter(row)
        if serial:
            serials.append(serial)

    if not serials:
        return {
            "success": True,
            "email": cleaned_email,
            "meters": [],
            "failed_serials": None,
            "total_count": listing.get("total_count", 0),
            "returned_count": 0,
            "truncated": bool(listing.get("truncated")),
            "notice": listing.get("notice") or f"No meters found on {cleaned_email}'s account.",
            "error": None,
        }

    ranked = rank_fleet_by_health(
        serials,
        token,
        anthropic_api_key=anthropic_api_key,
    )
    rows = [
        _compact_row(row)
        for row in (ranked.get("meters") if isinstance(ranked.get("meters"), list) else [])
        if isinstance(row, dict)
    ]
    return {
        "success": bool(ranked.get("success")),
        "email": cleaned_email,
        "meters": rows,
        "failed_serials": ranked.get("failed_serials"),
        "total_count": listing.get("total_count", len(serials)),
        "returned_count": len(rows),
        "truncated": bool(listing.get("truncated") or ranked.get("truncated")),
        "notice": listing.get("notice"),
        "error": ranked.get("error"),
    }
