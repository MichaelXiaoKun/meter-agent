"""
HTTP client for bluebot management APIs (material / standard / size / device).

Uses Bearer auth and x-admin-query: true, consistent with other meter_agent clients.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

_ADMIN_HEADERS = {"x-admin-query": "true"}

_DEFAULT_MANAGEMENT_BASE = "https://prod.bluebot.com"


def management_base_url() -> str:
    return os.environ.get("BLUEBOT_MANAGEMENT_BASE", _DEFAULT_MANAGEMENT_BASE).rstrip("/")


def _headers(token: str) -> Dict[str, str]:
    return {**_ADMIN_HEADERS, "Authorization": f"Bearer {token}"}


def _get_json(url: str, token: str, *, params: Optional[Dict[str, Any]] = None) -> Any:
    with httpx.Client(timeout=30) as client:
        r = client.get(url, headers=_headers(token), params=params or {})
        r.raise_for_status()
        return r.json()


def _data_list(payload: Any) -> List[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return data
    return []


def fetch_materials(token: str) -> List[Dict[str, Any]]:
    url = f"{management_base_url()}/management/v1/material"
    return [x for x in _data_list(_get_json(url, token)) if isinstance(x, dict)]


def fetch_standards(token: str, material: str) -> List[Dict[str, Any]]:
    """
    GET /management/v1/standard?material=<material>.

    Each row should include **`index`**: the firmware value published as MQTT `spm` (and 50-W `smp.pm`).
    """
    url = f"{management_base_url()}/management/v1/standard"
    return [
        x
        for x in _data_list(_get_json(url, token, params={"material": material}))
        if isinstance(x, dict)
    ]


def fetch_sizes(token: str, standard: str) -> List[Dict[str, Any]]:
    url = f"{management_base_url()}/management/v1/size"
    return [
        x
        for x in _data_list(_get_json(url, token, params={"standard": standard}))
        if isinstance(x, dict)
    ]


def fetch_device_by_serial(token: str, serial_number: str) -> Dict[str, Any]:
    url = f"{management_base_url()}/management/v1/device"
    payload = _get_json(url, token, params={"serialNumber": serial_number})
    rows = _data_list(payload)
    if not rows:
        raise RuntimeError(
            f"No device row returned for serialNumber={serial_number!r} "
            f"(expected response.data[0] from management API)."
        )
    first = rows[0]
    if not isinstance(first, dict):
        raise RuntimeError("management /device returned a non-object row.")
    return first
