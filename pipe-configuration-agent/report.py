"""
Report header for pipe configuration runs.
"""

from datetime import datetime, timezone


def format_report(analysis: str, serial_number: str) -> str:
    generated_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    header = (
        "=" * 80 + "\n"
        "PIPE CONFIGURATION REPORT\n"
        f"Serial:     {serial_number}\n"
        f"Generated:  {generated_str}\n"
        + "=" * 80 + "\n\n"
    )

    return header + analysis


def format_angle_only_report(body: str, serial_number: str) -> str:
    generated_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    header = (
        "=" * 80 + "\n"
        "TRANSDUCER ANGLE (SSA ONLY)\n"
        f"Serial:     {serial_number}\n"
        f"Generated:  {generated_str}\n"
        + "=" * 80 + "\n\n"
    )

    return header + body
