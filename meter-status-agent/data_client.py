"""
Data Client

Fetches the current status of a bluebot flow meter device.
Requires a Bearer token via BLUEBOT_TOKEN env var or explicit argument.
"""

import os
from typing import Any, Dict, Optional

import httpx

BASE_URL = "https://prod.bluebot.com/flow/v2/status"


def fetch_meter_status(
    device_id: str,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch the current status of a flow meter device.

    Args:
        device_id:  Device identifier (e.g. BB8100015261)
        token:      Bearer token. Falls back to BLUEBOT_TOKEN env var.

    Returns:
        Raw status dict as returned by the API.
    """
    token = token or os.environ.get("BLUEBOT_TOKEN")
    if not token:
        raise ValueError(
            "Bearer token required. Pass --token or set the BLUEBOT_TOKEN environment variable."
        )

    url = f"{BASE_URL}/{device_id}"
    headers = {"Authorization": f"Bearer {token}"}

    response = httpx.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    return response.json()
