"""
Data Client

Fetches flow rate time series data from the bluebot high-res API.
Requires a Bearer token via BLUEBOT_TOKEN env var or explicit argument.

Long ranges are automatically partitioned into 1-hour chunks so that
no single API request exceeds 3600 seconds of data.
"""

import os
from io import StringIO
from typing import List, Optional, Tuple

import httpx
import pandas as pd

# Override with BLUEBOT_FLOW_HIGH_RES_BASE if your tenant uses a different host/path.
_DEFAULT_FLOW_BASE = "https://prod.bluebot.com/flow/v2/high-res/data"


def _flow_base_url() -> str:
    return os.environ.get("BLUEBOT_FLOW_HIGH_RES_BASE", _DEFAULT_FLOW_BASE).rstrip("/")


# Required by bluebot flow API for admin/management-style queries.
_FLOW_HEADERS_EXTRA = {"x-admin-query": "true"}

CHUNK_SECONDS = 3600


def partition_range(start: int, end: int, chunk_seconds: int = CHUNK_SECONDS) -> List[Tuple[int, int]]:
    """
    Split [start, end] into contiguous chunks of at most chunk_seconds each.

    Example (1:20 AM → 3:40 AM):
        (1:20:00, 2:20:00), (2:20:01, 3:20:01), (3:20:02, 3:40:00)

    Args:
        start:         Range start as Unix timestamp (seconds, inclusive)
        end:           Range end as Unix timestamp (seconds, inclusive)
        chunk_seconds: Maximum span per chunk (default 3600)

    Returns:
        List of (chunk_start, chunk_end) tuples covering [start, end] exactly.
    """
    if end < start:
        raise ValueError(f"range_end ({end}) must be >= range_start ({start})")

    chunks = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + chunk_seconds, end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + 1
    return chunks


def fetch_flow_data(
    device_id: str,
    range_start: int,
    range_end: int,
    token: Optional[str] = None,
) -> pd.DataFrame:
    """
    Fetch flow rate time series from the bluebot API.

    Args:
        device_id:    Device identifier (e.g. BB8100015261)
        range_start:  Start of range as Unix timestamp (seconds)
        range_end:    End of range as Unix timestamp (seconds)
        token:        Bearer token. Falls back to BLUEBOT_TOKEN env var.

    Returns:
        DataFrame with columns: timestamp (int64), flow_rate (float64)
        Sorted ascending by timestamp, nulls preserved in flow_rate.
    """
    token = token or os.environ.get("BLUEBOT_TOKEN")
    if not token:
        raise ValueError(
            "Bearer token required. Pass --token or set the BLUEBOT_TOKEN environment variable."
        )

    base = _flow_base_url()
    url = f"{base}/{device_id}"
    params = {
        "range_start": range_start,
        "range_end": range_end,
        "fields": "quality,flow_amount,flow_rate",
        "format": "csv",
    }
    headers = {**_FLOW_HEADERS_EXTRA, "Authorization": f"Bearer {token}"}

    try:
        response = httpx.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        body = (e.response.text or "")[:500].strip()
        hint = {
            401: "Invalid or expired Bearer token.",
            403: "Token is not allowed to read this device.",
            404: (
                "No resource at this URL — often: wrong device_id, token cannot access this meter, "
                "or high-res flow data is not available for this device/path. "
                "Confirm the device ID and that ingestion is enabled."
            ),
        }.get(code, "Unexpected HTTP error from Bluebot flow API.")
        raise RuntimeError(
            f"Bluebot high-res API HTTP {code} for device {device_id!r}. {hint} "
            f"Request URL: {url} (range {range_start}–{range_end}). "
            f"Response: {body or '(empty body)'}"
        ) from e

    df = pd.read_csv(StringIO(response.text))

    # Normalize column names (lowercase, strip whitespace)
    df.columns = [c.strip().lower() for c in df.columns]

    # Normalise timestamp column name (API returns 'recorded_at')
    if "recorded_at" in df.columns:
        df = df.rename(columns={"recorded_at": "timestamp"})

    if "timestamp" not in df.columns:
        raise ValueError(f"Expected 'timestamp' or 'recorded_at' column, got: {list(df.columns)}")
    if "flow_rate" not in df.columns:
        raise ValueError(f"Expected 'flow_rate' column, got: {list(df.columns)}")

    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="raise").astype("int64") // 1000
    df["flow_rate"] = pd.to_numeric(df["flow_rate"], errors="coerce").astype("float64")

    df["quality"] = (
        pd.to_numeric(df["quality"], errors="coerce").astype("float64")
        if "quality" in df.columns
        else float("nan")
    )
    df["flow_amount"] = (
        pd.to_numeric(df["flow_amount"], errors="coerce").astype("float64")
        if "flow_amount" in df.columns
        else float("nan")
    )

    df = (
        df[["timestamp", "flow_rate", "flow_amount", "quality"]]
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    return df


def fetch_flow_data_range(
    device_id: str,
    range_start: int,
    range_end: int,
    token: Optional[str] = None,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Fetch flow rate data for an arbitrary time range, partitioned into
    hourly chunks (≤ 3600 seconds each) to stay within API limits.

    Args:
        device_id:    Device identifier
        range_start:  Start of range as Unix timestamp (seconds)
        range_end:    End of range as Unix timestamp (seconds)
        token:        Bearer token. Falls back to BLUEBOT_TOKEN env var.
        verbose:      Print chunk progress to stderr (default True)

    Returns:
        Combined DataFrame sorted by timestamp with duplicates removed.
    """
    token = token or os.environ.get("BLUEBOT_TOKEN")
    if not token:
        raise ValueError(
            "Bearer token required. Pass --token or set the BLUEBOT_TOKEN environment variable."
        )

    chunks = partition_range(range_start, range_end)
    frames = []

    for i, (chunk_start, chunk_end) in enumerate(chunks, 1):
        if verbose:
            print(
                f"  Chunk {i}/{len(chunks)}: {chunk_start} → {chunk_end} "
                f"({chunk_end - chunk_start}s)",
                file=__import__("sys").stderr,
            )
        df_chunk = fetch_flow_data(device_id, chunk_start, chunk_end, token)
        frames.append(df_chunk)

    combined = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset="timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )[["timestamp", "flow_rate", "flow_amount", "quality"]]
    return combined
