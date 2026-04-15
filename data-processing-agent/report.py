"""
Report Formatter

Wraps the agent's analysis in a standardised report header.
"""

from datetime import datetime, timezone


def format_report(analysis: str, serial_number: str, start: int, end: int) -> str:
    """
    Attach a header block to the agent's analysis text.

    Args:
        analysis:       Markdown analysis string returned by agent.analyze()
        serial_number:  Meter serial number
        start:          Range start as Unix timestamp (seconds)
        end:            Range end as Unix timestamp (seconds)

    Returns:
        Full report string ready for console output or file write.
    """
    fmt = "%Y-%m-%d %H:%M:%S UTC"
    start_str = datetime.fromtimestamp(start, tz=timezone.utc).strftime(fmt)
    end_str = datetime.fromtimestamp(end, tz=timezone.utc).strftime(fmt)
    generated_str = datetime.now(tz=timezone.utc).strftime(fmt)

    header = (
        "=" * 80 + "\n"
        "FLOW RATE ANALYSIS REPORT\n"
        f"Serial:     {serial_number}\n"
        f"Period:     {start_str}  →  {end_str}\n"
        f"Generated:  {generated_str}\n"
        + "=" * 80 + "\n\n"
    )

    return header + analysis
