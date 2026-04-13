"""
Report Formatter

Wraps the agent's analysis in a standardised report header.
"""

from datetime import datetime, timezone


def format_report(analysis: str, device_id: str) -> str:
    generated_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    header = (
        "=" * 80 + "\n"
        "METER STATUS REPORT\n"
        f"Device:     {device_id}\n"
        f"Generated:  {generated_str}\n"
        + "=" * 80 + "\n\n"
    )

    return header + analysis
