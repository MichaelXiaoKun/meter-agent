"""Lightweight recent-flow snapshot helpers for Meter Context Packet."""

from __future__ import annotations

import importlib.util
import math
import os
import time
from pathlib import Path
from typing import Any, Callable


_DATA_AGENT_DIR = Path(__file__).resolve().parents[2] / "data-processing-agent"
_DATA_CLIENT_PATH = _DATA_AGENT_DIR / "data_client.py"
_FLOW_DATA_CLIENT: Any | None = None


def _normalize_network_type(network_type: str | None) -> str:
    value = (network_type or "").strip().lower()
    if value in {"wifi", "wi-fi"}:
        return "wifi"
    if value in {"lorawan", "lora", "lo-ra", "lo-ra-wan"}:
        return "lorawan"
    return "unknown"


def recent_flow_window_seconds(network_type: str | None) -> int:
    network = _normalize_network_type(network_type)
    if network == "wifi":
        return 5 * 60
    if network == "lorawan":
        return 60 * 60
    return 15 * 60


def _timeout_seconds() -> float:
    raw = os.environ.get("BLUEBOT_RECENT_FLOW_SNAPSHOT_TIMEOUT_SECONDS", "4")
    try:
        value = float(raw)
    except ValueError:
        return 4.0
    return max(0.5, min(value, 15.0))


def _healthy_inter_arrival_seconds(network_type: str | None) -> float:
    network = _normalize_network_type(network_type)
    if network == "wifi":
        return 5.0
    return 60.0


def _gap_threshold_seconds(network_type: str | None) -> float:
    return _healthy_inter_arrival_seconds(network_type) * 1.5


def _freshness_threshold_seconds(network_type: str | None) -> float:
    return max(_healthy_inter_arrival_seconds(network_type) * 3.0, 30.0)


def _round(value: float | None, digits: int = 3) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _as_int(value: Any) -> int | None:
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _load_data_client() -> Any:
    global _FLOW_DATA_CLIENT
    if _FLOW_DATA_CLIENT is not None:
        return _FLOW_DATA_CLIENT
    spec = importlib.util.spec_from_file_location(
        "bluebot_recent_flow_data_client",
        _DATA_CLIENT_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load flow data client at {_DATA_CLIENT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _FLOW_DATA_CLIENT = module
    return module


def _default_fetch_flow_data_range(*args: Any, **kwargs: Any) -> Any:
    return _load_data_client().fetch_flow_records_range(*args, **kwargs)


def _records_from_dataframe(df: Any) -> list[dict[str, Any]]:
    if isinstance(df, list):
        return [r for r in df if isinstance(r, dict)]
    if df is None or not hasattr(df, "to_dict"):
        return []
    records = df.to_dict("records")
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def _quality_summary(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    low_quality_count = sum(1 for value in values if value <= 60.0)
    return {
        "count": len(values),
        "latest": _round(values[-1], 2),
        "mean": _round(sum(values) / len(values), 2),
        "min": _round(min(values), 2),
        "max": _round(max(values), 2),
        "low_quality_count": low_quality_count,
    }


def _error_state(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "timed_out"
    return "unavailable"


def _base_payload(
    *,
    serial_number: str,
    network_type: str | None,
    now_s: int,
    window_seconds: int,
    state: str,
) -> dict[str, Any]:
    start_s = max(0, now_s - window_seconds)
    return {
        "state": state,
        "serial_number": serial_number,
        "source": "high_res_flow",
        "network_type": _normalize_network_type(network_type),
        "window_seconds": window_seconds,
        "window_start_unix": start_s,
        "window_end_unix": now_s,
        "window_label": f"last_{int(window_seconds / 60)}m",
    }


def build_recent_flow_snapshot(
    serial_number: str,
    token: str,
    *,
    network_type: str | None = None,
    now: int | float | None = None,
    timeout_seconds: float | None = None,
    fetch_flow_data_range: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Fetch and summarize a small high-res flow window.

    This intentionally does not run the full data-processing agent. It returns a
    compact fact packet and fails open so the chat turn can continue.
    """

    normalized_network = _normalize_network_type(network_type)
    window_seconds = recent_flow_window_seconds(normalized_network)
    now_s = int(now if now is not None else time.time())
    base = _base_payload(
        serial_number=serial_number,
        network_type=normalized_network,
        now_s=now_s,
        window_seconds=window_seconds,
        state="not_checked",
    )
    if not token:
        return {
            **base,
            "state": "unavailable",
            "reason": "Bearer token required for recent flow snapshot.",
        }

    timeout = timeout_seconds if timeout_seconds is not None else _timeout_seconds()
    fetch = fetch_flow_data_range or _default_fetch_flow_data_range

    try:
        result = fetch(
            serial_number,
            base["window_start_unix"],
            base["window_end_unix"],
            token=token,
            verbose=False,
            return_metadata=True,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - covered by state classifier tests
        state = _error_state(exc)
        return {
            **base,
            "state": state,
            "timeout_seconds": timeout,
            "reason": (
                "Recent flow snapshot timed out."
                if state == "timed_out"
                else f"{type(exc).__name__}: {exc}"
            ),
        }

    if isinstance(result, tuple) and len(result) == 2:
        df, metadata = result
    else:
        df, metadata = result, {}
    records = _records_from_dataframe(df)
    if not records:
        return {
            **base,
            "state": "empty",
            "sample_count": 0,
            "valid_flow_count": 0,
            "reason": "No high-res flow samples returned for the recent window.",
            **(metadata if isinstance(metadata, dict) else {}),
        }

    rows: list[dict[str, Any]] = []
    for record in records:
        ts = _as_int(record.get("timestamp"))
        if ts is None:
            continue
        rows.append(
            {
                "timestamp": ts,
                "flow_rate": _as_float(record.get("flow_rate")),
                "quality": _as_float(record.get("quality")),
            }
        )
    rows.sort(key=lambda row: row["timestamp"])
    if not rows:
        return {
            **base,
            "state": "empty",
            "sample_count": 0,
            "valid_flow_count": 0,
            "reason": "No usable timestamps returned for the recent window.",
            **(metadata if isinstance(metadata, dict) else {}),
        }

    timestamps = [int(row["timestamp"]) for row in rows]
    flow_values = [float(row["flow_rate"]) for row in rows if row["flow_rate"] is not None]
    quality_values = [float(row["quality"]) for row in rows if row["quality"] is not None]
    gaps = [
        float(curr - prev)
        for prev, curr in zip(timestamps, timestamps[1:])
        if curr >= prev
    ]
    gap_threshold = _gap_threshold_seconds(normalized_network)
    largest_gap = max(gaps) if gaps else 0.0
    gap_count = sum(1 for gap in gaps if gap > gap_threshold)
    latest = rows[-1]
    latest_ts = int(latest["timestamp"])
    latest_age = max(0, now_s - latest_ts)
    latest_sample_fresh = latest_age <= _freshness_threshold_seconds(normalized_network)

    if not flow_values:
        snapshot_quality = "no_valid_flow"
    elif not latest_sample_fresh:
        snapshot_quality = "stale"
    elif gap_count > 0:
        snapshot_quality = "gappy"
    else:
        snapshot_quality = "usable"

    payload: dict[str, Any] = {
        **base,
        "state": "checked",
        "sample_count": len(rows),
        "valid_flow_count": len(flow_values),
        "latest_sample_unix": latest_ts,
        "latest_sample_age_seconds": latest_age,
        "latest_sample_fresh": latest_sample_fresh,
        "latest_flow_rate": _round(_as_float(latest.get("flow_rate")), 4),
        "mean_flow_rate": _round(
            sum(flow_values) / len(flow_values) if flow_values else None,
            4,
        ),
        "min_flow_rate": _round(min(flow_values) if flow_values else None, 4),
        "max_flow_rate": _round(max(flow_values) if flow_values else None, 4),
        "largest_gap_seconds": _round(largest_gap, 2),
        "gap_count": gap_count,
        "gap_threshold_seconds": _round(gap_threshold, 2),
        "snapshot_quality": snapshot_quality,
    }
    quality = _quality_summary(quality_values)
    if quality:
        payload["signal_quality"] = quality
    if isinstance(metadata, dict):
        payload.update(metadata)
    return {k: v for k, v in payload.items() if v is not None}
