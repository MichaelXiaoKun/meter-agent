"""
Resolve device registration (serial → NUI/model) and pipe catalog rows
(material → standard → size) via management APIs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from management_client import (
    fetch_device_by_serial,
    fetch_materials,
    fetch_sizes,
    fetch_standards,
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _collect_labels(row: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for k in (
        "name",
        "material",
        "standard",
        "title",
        "label",
        "description",
        "nominalSize",
        "nominal_size",
        "size",
        "displayName",
        "display_name",
    ):
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def _best_match_row(query: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    q = _norm(query)
    if not q:
        return None

    best: Optional[Tuple[int, int, Dict[str, Any]]] = None
    for row in rows:
        labels = [_norm(x) for x in _collect_labels(row)]
        labels = [x for x in labels if x]
        if not labels:
            continue

        if q in labels:
            score = (0, 0, row)
        else:
            subs = [x for x in labels if q in x or x in q]
            if not subs:
                continue
            score = (1, min(len(x) for x in subs), row)

        if best is None or score[:2] < best[:2]:
            best = score
    return best[2] if best else None


def _material_query_value(material_row: Dict[str, Any]) -> str:
    for k in ("id", "materialId", "material_id", "code", "key", "name"):
        v = material_row.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return str(int(v)) if float(v).is_integer() else str(v)
        s = str(v).strip()
        if s:
            return s
    raise RuntimeError("Could not determine material query param from selected material row.")


def _standard_query_value(standard_row: Dict[str, Any]) -> str:
    for k in ("id", "standardId", "standard_id", "code", "key", "name"):
        v = standard_row.get(k)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            return str(int(v)) if float(v).is_integer() else str(v)
        s = str(v).strip()
        if s:
            return s
    raise RuntimeError("Could not determine standard query param from selected standard row.")


def _spm_index_from_standard_row(standard_row: Dict[str, Any]) -> str:
    """
    Value for MQTT `spm` / 50-W `smp.pm`: firmware pipe standard index.

    Must come from GET /management/v1/standard?material=… response **data** row field **`index`**
    only (not inferred from other keys).
    """
    v = standard_row.get("index")
    if v is None:
        raise RuntimeError(
            "Standard row from GET /management/v1/standard is missing required field `index` "
            f"(keys={list(standard_row.keys())})."
        )
    if isinstance(v, bool):
        raise RuntimeError("Standard `index` must be numeric or string, not boolean.")
    if isinstance(v, (int, float)):
        return str(int(v)) if float(v).is_integer() else str(v)
    s = str(v).strip()
    if not s:
        raise RuntimeError("Standard `index` is empty.")
    return s


def _size_mm(size_row: Dict[str, Any]) -> Tuple[float, float]:
    outer = size_row.get("outerDiameterMm")
    wall = size_row.get("wallThicknessMm")
    if outer is None:
        outer = size_row.get("outer_diameter_mm")
    if wall is None:
        wall = size_row.get("wall_thickness_mm")
    if outer is None or wall is None:
        raise RuntimeError(
            "Selected size row is missing outerDiameterMm / wallThicknessMm "
            f"(keys={list(size_row.keys())})."
        )
    return float(outer), float(wall)


def _is_50w_model(model: Optional[str]) -> bool:
    if not model:
        return False
    m = re.sub(r"[^a-z0-9]+", "", model.lower())
    return "50w" in m


def resolve_device_context_by_serial(token: str, serial_number: str) -> Dict[str, Any]:
    """
    Management GET /management/v1/device?serialNumber=… only — no pipe catalog.

    Returns NUI, model, and Wi-Fi vs LoRaWAN flags for **transducer angle only** flows
    (sufficient for ``resolve_transducer_angle`` and SSA-only MQTT).
    """
    serial_number = (serial_number or "").strip()
    if not serial_number:
        return {"error": "serial_number is required"}

    try:
        device = fetch_device_by_serial(token, serial_number)
    except Exception as exc:
        return {"error": f"Device lookup failed: {exc}"}

    nui = device.get("networkUniqueIdentifier") or device.get("network_unique_identifier")
    model = device.get("model")
    if not nui:
        return {"error": "Device row missing networkUniqueIdentifier."}

    is_lorawan = str(nui).strip() != str(serial_number).strip()

    return {
        "error": None,
        "serial_number": serial_number,
        "network_unique_identifier": str(nui),
        "model": model,
        "is_lorawan": bool(is_lorawan),
        "is_wifi": not bool(is_lorawan),
        "is_50w": _is_50w_model(str(model) if model is not None else ""),
    }


def resolve_device_and_pipe_specs(
    token: str,
    serial_number: str,
    pipe_material: str,
    pipe_standard: str,
    pipe_size: str,
) -> Dict[str, Any]:
    """
    Look up the device by serial, then resolve catalog selections.

    Returns a JSON-serialisable dict suitable for passing into the MQTT processor.
    """
    serial_number = (serial_number or "").strip()
    if not serial_number:
        return {"error": "serial_number is required"}

    try:
        device = fetch_device_by_serial(token, serial_number)
    except Exception as exc:
        return {"error": f"Device lookup failed: {exc}"}

    nui = device.get("networkUniqueIdentifier") or device.get("network_unique_identifier")
    model = device.get("model")
    if not nui:
        return {"error": "Device row missing networkUniqueIdentifier."}

    is_lorawan = str(nui).strip() != str(serial_number).strip()

    materials = fetch_materials(token)
    mat_row = _best_match_row(pipe_material, materials)
    if not mat_row:
        return {
            "error": (
                f"No material match for {pipe_material!r}. "
                f"Available sample: {[_collect_labels(r)[0] for r in materials[:8] if _collect_labels(r)]}"
            ),
            "device": {"serial_number": serial_number, "model": model, "network_unique_identifier": nui},
        }

    mat_param = _material_query_value(mat_row)
    standards = fetch_standards(token, mat_param)
    std_row = _best_match_row(pipe_standard, standards)
    if not std_row:
        return {
            "error": (
                f"No standard match for {pipe_standard!r} under material={mat_param!r}. "
                f"Available sample: {[_collect_labels(r)[0] for r in standards[:8] if _collect_labels(r)]}"
            ),
            "device": {"serial_number": serial_number, "model": model, "network_unique_identifier": nui},
            "matched_material": _collect_labels(mat_row)[0] if _collect_labels(mat_row) else mat_param,
        }

    std_param = _standard_query_value(std_row)
    try:
        std_index = _spm_index_from_standard_row(std_row)
    except Exception as exc:
        return {"error": str(exc)}

    sizes = fetch_sizes(token, std_param)
    size_row = _best_match_row(pipe_size, sizes)
    if not size_row:
        return {
            "error": (
                f"No nominal size match for {pipe_size!r} under standard={std_param!r}. "
                f"Available sample: {[_collect_labels(r)[0] for r in sizes[:12] if _collect_labels(r)]}"
            ),
            "device": {"serial_number": serial_number, "model": model, "network_unique_identifier": nui},
            "matched_material": _collect_labels(mat_row)[0] if _collect_labels(mat_row) else mat_param,
            "matched_standard": _collect_labels(std_row)[0] if _collect_labels(std_row) else std_param,
        }

    try:
        outer_mm, wall_mm = _size_mm(size_row)
    except Exception as exc:
        return {"error": str(exc)}

    return {
        "error": None,
        "serial_number": serial_number,
        "network_unique_identifier": str(nui),
        "model": model,
        "is_lorawan": bool(is_lorawan),
        "is_wifi": not bool(is_lorawan),
        "is_50w": _is_50w_model(str(model) if model is not None else ""),
        "standard_index": str(std_index),
        "outer_diameter_mm": float(outer_mm),
        "wall_thickness_mm": float(wall_mm),
        "matched_material": (_collect_labels(mat_row)[0] if _collect_labels(mat_row) else mat_param),
        "matched_standard": (_collect_labels(std_row)[0] if _collect_labels(std_row) else std_param),
        "matched_size": (_collect_labels(size_row)[0] if _collect_labels(size_row) else str(pipe_size)),
        "management_material_param": mat_param,
        "management_standard_param": std_param,
    }
