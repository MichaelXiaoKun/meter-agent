"""
Pipe Configuration Processor

Interprets the pipe configuration fields returned by the status API:
outer diameter, wall thickness, inferred nominal size, and derived inner diameter.

All dimensional calculations are explicit arithmetic — no estimation.
"""

from typing import Any, Dict, Optional


def interpret_pipe_config(
    pipe_outer_diameter: str | float,
    pipe_wall_thickness: str | float,
    inferred_nominal_size: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compute pipe geometry and summarise the inferred nominal pipe size.

    Inner diameter is derived exactly as:
        inner_diameter_mm = outer_diameter_mm - 2 * wall_thickness_mm

    Args:
        pipe_outer_diameter:    Outer diameter in mm (may arrive as string from API)
        pipe_wall_thickness:    Wall thickness in mm (may arrive as string from API)
        inferred_nominal_size:  The inferred_nominal_size object from the API response (optional)

    Returns:
        outer_diameter_mm:      Outer diameter in mm
        wall_thickness_mm:      Wall thickness in mm
        inner_diameter_mm:      Derived inner diameter in mm (OD - 2*WT)
        inner_diameter_inches:  Derived inner diameter in inches
        nominal_size:           Nominal pipe size label (e.g. '3/4"') if inferred
        pipe_standard:          Pipe standard (e.g. 'CPVC') if inferred
        nominal_match_diff:     Difference between measured OD and standard OD (mm) if inferred
        config_summary:         One-line human-readable pipe description
    """
    od_mm = float(pipe_outer_diameter)
    wt_mm = float(pipe_wall_thickness)
    id_mm = round(od_mm - 2 * wt_mm, 4)
    id_inches = round(id_mm / 25.4, 4)

    result: Dict[str, Any] = {
        "outer_diameter_mm": round(od_mm, 4),
        "wall_thickness_mm": round(wt_mm, 4),
        "inner_diameter_mm": id_mm,
        "inner_diameter_inches": id_inches,
        "nominal_size": None,
        "pipe_standard": None,
        "nominal_match_diff": None,
        "config_summary": f"{od_mm:.2f}mm OD / {id_mm:.3f}mm ID / {wt_mm:.3f}mm wall",
    }

    if inferred_nominal_size:
        nominal = inferred_nominal_size.get("nominalSize")
        standard = inferred_nominal_size.get("standard")
        diff = inferred_nominal_size.get("diff")

        result["nominal_size"] = nominal
        result["pipe_standard"] = standard
        result["nominal_match_diff"] = float(diff) if diff is not None else None
        result["config_summary"] = (
            f"{standard} {nominal} — "
            f"{od_mm:.2f}mm OD / {id_mm:.3f}mm ID / {wt_mm:.3f}mm wall"
        )

    return result
