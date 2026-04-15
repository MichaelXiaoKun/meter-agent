"""
Data Client

Fetches the current status of a bluebot flow meter device.
Requires a Bearer token via BLUEBOT_TOKEN env var or explicit argument.
"""

import os
from typing import Any, Dict, Optional

import httpx

_DEFAULT_STATUS_BASE = "https://prod.bluebot.com/flow/v2/status"


def _status_base_url() -> str:
    return os.environ.get("BLUEBOT_METER_STATUS_BASE", _DEFAULT_STATUS_BASE).rstrip("/")


# Required by bluebot management/status API for admin-style queries.
_STATUS_HEADERS_EXTRA = {"x-admin-query": "true"}


def fetch_meter_status(
    serial_number: str,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch the current status of a flow meter device.

    Args:
        serial_number:  Path segment for the status API — the meter serial number the user provides
                        (same value as orchestrator check_meter_status ``serial_number``).
        token:          Bearer token. Falls back to BLUEBOT_TOKEN env var.

    Returns:
        Raw status dict as returned by the API.
    """
    token = token or os.environ.get("BLUEBOT_TOKEN")
    if not token:
        raise ValueError(
            "Bearer token required. Pass --token or set the BLUEBOT_TOKEN environment variable."
        )

    base = _status_base_url()
    url = f"{base}/{serial_number}"
    headers = {**_STATUS_HEADERS_EXTRA, "Authorization": f"Bearer {token}"}

    try:
        response = httpx.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        body = (e.response.text or "")[:500].strip()
        hint = {
            401: "Invalid or expired Bearer token.",
            403: "Token is not allowed to read this meter.",
            404: (
                "Meter not found or not accessible with this token — verify the serial number and access."
            ),
        }.get(code, "Unexpected HTTP error from Bluebot status API.")
        raise RuntimeError(
            f"Bluebot status API HTTP {code} for serial {serial_number!r}. {hint} "
            f"URL: {url}. Response: {body or '(empty body)'}"
        ) from e

    return response.json()
