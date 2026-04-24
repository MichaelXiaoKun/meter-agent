"""
Load pipe-configuration-agent transducer angle tables for orchestrator preflight.

Avoids duplicating Wi-Fi vs LoRaWAN maps; uses importlib so ``processors`` name
does not clash with orchestrator's ``processors`` package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_module: Any | None = None


def _catalog():
    global _module
    if _module is None:
        path = (
            Path(__file__).resolve().parents[2]
            / "pipe-configuration-agent"
            / "processors"
            / "transducer_angle.py"
        )
        spec = importlib.util.spec_from_file_location(
            "meter_agent_pipe_transducer_angle_catalog",
            path,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load transducer angle catalog from {path}")
        _module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_module)
    return _module


def allowed_labels_for_network_type(network_type: str | None) -> list[str]:
    nt = (network_type or "").strip().lower()
    if nt == "lorawan":
        return list(_catalog().allowed_angle_labels(is_lorawan=True))
    if nt == "wifi":
        return list(_catalog().allowed_angle_labels(is_lorawan=False))
    return list(_catalog().allowed_angle_labels(is_lorawan=None))


def preflight_validate_transducer_angle(angle: str, network_type: str | None) -> str | None:
    nt = (network_type or "").strip().lower()
    if nt == "lorawan":
        is_lora = True
    elif nt == "wifi":
        is_lora = False
    else:
        is_lora = None
    return _catalog().preflight_validate_angle(angle, is_lorawan=is_lora)
