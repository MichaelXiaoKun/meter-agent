"""
meter_profile.py — Orchestrator tool for querying device metadata from the
Bluebot management API and classifying the meter's network type.

Endpoint:
    GET {management_base}/management/v1/device?serialNumber=<serial>
    Headers: x-admin-query: true, Authorization: Bearer <token>

Response is a JSON array; we use the first element. Classification:

- ``networkUniqueIdentifier`` starts with ``FF`` (case-insensitive) → **LoRaWAN**
- ``networkUniqueIdentifier`` equals ``serialNumber`` (case-insensitive) → **Wi-Fi**
- otherwise → **unknown**
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

_DEFAULT_MANAGEMENT_BASE = "https://prod.bluebot.com"
_ADMIN_HEADERS = {"x-admin-query": "true"}

TOOL_DEFINITION = {
    "name": "get_meter_profile",
    "description": (
        "Look up a meter profile from the Bluebot management API by serial number. "
        "Returns metadata (label, model, organization, timezone, installed/commissioned flags) "
        "and a **network_type** classification (``lorawan`` when networkUniqueIdentifier starts with FF, "
        "``wifi`` when networkUniqueIdentifier equals the serial number, else ``unknown``). "
        "LoRaWAN meters typically report every ~12–60 s; Wi-Fi meters report every ~2 s — useful context "
        "when interpreting flow data gaps or sparse coverage."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "serial_number": {
                "type": "string",
                "description": (
                    "Serial number engraved on the meter (used as the ``serialNumber`` query parameter)."
                ),
            }
        },
        "required": ["serial_number"],
    },
}


def _management_base_url() -> str:
    return os.environ.get("BLUEBOT_MANAGEMENT_BASE", _DEFAULT_MANAGEMENT_BASE).rstrip("/")


def _headers(token: str) -> Dict[str, str]:
    return {**_ADMIN_HEADERS, "Authorization": f"Bearer {token}"}


def classify_network_type(serial_number: str, network_unique_identifier: Optional[str]) -> Dict[str, Any]:
    """
    Return ``{"network_type": str, "reason": str, "expected_cadence_hint": str}``.

    network_type is one of ``lorawan`` | ``wifi`` | ``unknown``.
    """
    nui = (network_unique_identifier or "").strip()
    serial = (serial_number or "").strip()

    if not nui:
        return {
            "network_type": "unknown",
            "reason": "networkUniqueIdentifier missing from device profile.",
            "expected_cadence_hint": None,
        }

    if nui.upper().startswith("FF"):
        return {
            "network_type": "lorawan",
            "reason": "networkUniqueIdentifier starts with FF.",
            "expected_cadence_hint": "typical inter-arrival 12–60 s (bursty)",
        }

    if serial and nui.upper() == serial.upper():
        return {
            "network_type": "wifi",
            "reason": "networkUniqueIdentifier equals serial number.",
            "expected_cadence_hint": "typical inter-arrival ~2 s",
        }

    return {
        "network_type": "unknown",
        "reason": (
            "networkUniqueIdentifier does not start with FF and does not match the serial number."
        ),
        "expected_cadence_hint": None,
    }


def _pick_profile_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Subset we surface to the agent (keeps tool result compact)."""
    keys = [
        "serialNumber",
        "label",
        "model",
        "category",
        "deviceType",
        "networkUniqueIdentifier",
        "commissioned",
        "installed",
        "installedOn",
        "active",
        "deviceTimeZone",
        "organizationId",
        "accountId",
    ]
    out: Dict[str, Any] = {k: row.get(k) for k in keys if k in row}
    org = row.get("organization") or {}
    if isinstance(org, dict):
        out["organization_name"] = org.get("name")
    groups: List[Dict[str, Any]] = []
    for link in row.get("deviceToDeviceGroups") or []:
        if not isinstance(link, dict):
            continue
        g = link.get("deviceGroup") or {}
        if isinstance(g, dict) and (g.get("name") or g.get("description")):
            groups.append(
                {
                    "name": g.get("name"),
                    "description": g.get("description"),
                    "parentGroupId": g.get("parentGroupId"),
                }
            )
    if groups:
        out["device_groups"] = groups
    return out


def get_meter_profile(serial_number: str, token: str) -> Dict[str, Any]:
    """
    Query the management device API and classify the meter's network type.

    Returns:
        {
            "success":       bool,
            "serial_number": str,
            "network_type":  "lorawan" | "wifi" | "unknown" | None,
            "classification": {
                "network_type": str,
                "reason": str,
                "expected_cadence_hint": str | None,
            } | None,
            "profile":       dict | None,   # compact subset of the management row
            "error":         str | None,
        }
    """
    base = {"serial_number": serial_number}
    if not token:
        return {
            **base,
            "success": False,
            "network_type": None,
            "classification": None,
            "profile": None,
            "error": "Bearer token required for management API.",
        }

    url = f"{_management_base_url()}/management/v1/device"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, headers=_headers(token), params={"serialNumber": serial_number})
            resp.raise_for_status()
            payload: Any = resp.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        body = (exc.response.text or "")[:300].strip()
        hint = {
            401: "Invalid or expired Bearer token.",
            403: "Token is not allowed to read management device records.",
            404: "No device found for the given serial number.",
        }.get(code, "Unexpected HTTP error from Bluebot management API.")
        return {
            **base,
            "success": False,
            "network_type": None,
            "classification": None,
            "profile": None,
            "error": (
                f"Management API HTTP {code} for serial {serial_number!r}. {hint} "
                f"Response: {body or '(empty body)'}"
            ),
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            **base,
            "success": False,
            "network_type": None,
            "classification": None,
            "profile": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    rows: List[Dict[str, Any]] = payload if isinstance(payload, list) else []
    if not rows:
        return {
            **base,
            "success": False,
            "network_type": None,
            "classification": None,
            "profile": None,
            "error": f"No device profile returned for serial {serial_number!r}.",
        }

    row = next((r for r in rows if isinstance(r, dict)), None)
    if row is None:
        return {
            **base,
            "success": False,
            "network_type": None,
            "classification": None,
            "profile": None,
            "error": "Management API returned no usable device object.",
        }

    classification = classify_network_type(serial_number, row.get("networkUniqueIdentifier"))
    return {
        **base,
        "success": True,
        "network_type": classification["network_type"],
        "classification": classification,
        "profile": _pick_profile_fields(row),
        "error": None,
    }
