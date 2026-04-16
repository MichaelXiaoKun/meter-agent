"""
Flow Analysis Agent

An LLM agent that decides which mathematical processors to run on a flow
rate time series, executes them deterministically, then synthesises a
structured analytical report.

Contract:
  - The LLM may ONLY call tools defined in TOOLS below.
  - Every number in the final report must originate from a processor return value.
  - The LLM never computes statistics itself.
"""

import json
import os
from typing import Any, Dict

import numpy as np
import pandas as pd
import anthropic

from processors.descriptive import compute_descriptive_stats
from processors.continuity import detect_gaps, detect_zero_flow_periods
from processors.flow_metrics import compute_total_volume, detect_peaks, compute_flow_duration_curve
from processors.trend import compute_linear_trend, compute_rolling_statistics
from processors.quality import detect_low_quality_readings
from processors.quiet_baseline import summarize_quiet_flow_baseline
from processors.plots import generate_plot, pop_figures

TOOLS = [
    {
        "name": "compute_descriptive_stats",
        "description": (
            "Compute rigorous descriptive statistics over the full flow rate series: "
            "count, valid_count, null_count, mean, median, std (sample, ddof=1), variance, "
            "min, max, range, p25, p75, p95, IQR, coefficient of variation."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "detect_gaps",
        "description": (
            "Detect time gaps where consecutive readings exceed the expected sampling interval. "
            "Returns a list of gaps with start/end timestamps, duration, and estimated missing point count."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expected_interval_seconds": {
                    "type": "number",
                    "description": (
                        "Nominal sampling period in seconds. "
                        "If omitted, it is auto-detected from the median time delta."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "detect_zero_flow_periods",
        "description": (
            "Detect continuous periods where flow rate is at or below zero. "
            "Returns start/end timestamps, duration, and point count for each period."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "min_duration_seconds": {
                    "type": "number",
                    "description": "Minimum period length to report (seconds). Default: 60.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "compute_total_volume",
        "description": (
            "Estimate total flow volume over the window using trapezoidal numerical integration. "
            "Assumes flow_rate in gal/min. Returns total_volume_gallons and time_span_minutes."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "detect_peaks",
        "description": (
            "Detect significant flow rate peaks using scipy.signal.find_peaks with "
            "prominence-based filtering (threshold = std * prominence_multiplier). "
            "Returns timestamp, value, z-score, and prominence for each peak."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prominence_multiplier": {
                    "type": "number",
                    "description": (
                        "Scales the prominence threshold relative to the series std. "
                        "Higher = fewer, larger peaks only. Default: 1.0."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "compute_flow_duration_curve",
        "description": (
            "Compute the flow duration curve (FDC): Qx = flow rate exceeded x% of the time. "
            "Returns Q10, Q25, Q50, Q75, Q90, Q95, Q99 — standard hydrological analysis."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "compute_linear_trend",
        "description": (
            "Fit a linear regression (OLS) to the time series. "
            "Returns slope (per second and per minute), intercept, R², p-value, "
            "standard error, trend direction, and statistical significance flag."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "compute_rolling_statistics",
        "description": (
            "Compute rolling mean and rolling std over a sliding window. "
            "Returns smoothed range, start/end smoothed values, average volatility, and peak volatility."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "window_size": {
                    "type": "integer",
                    "description": "Number of data points per window. Default: 10.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "detect_low_quality_readings",
        "description": (
            "Flag readings where the ultrasonic signal quality score is at or below a threshold (default 60). "
            "Quality reflects how cleanly the ultrasonic sensor received its measurement signal — "
            "a low score means the sensor struggled, making the flow rate reading less reliable. "
            "Returns aggregate stats, first/last low-quality times, longest stretch summary, "
            "and merged low-quality intervals (contiguous runs), not per-sample rows — use these for the report."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "threshold": {
                    "type": "number",
                    "description": "Quality score at or below which a reading is flagged. Default: 60.",
                }
            },
            "required": [],
        },
    },
    {
        "name": "summarize_quiet_flow_baseline",
        "description": (
            "Quiet-flow baseline: among readings with good ultrasonic quality (default: quality > 60), "
            "take the bottom flow-rate percentile (default: 10th percentile) as a 'quiet' cutoff, "
            "then summarise flow_rate statistics for that quiet subset (median, mean, IQR, counts). "
            "Useful for screening offset or residual flow when the process is most still — not proof of a leak. "
            "Call when has_quality_scores is true in the data overview."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "quality_threshold": {
                    "type": "number",
                    "description": "Include only readings with quality strictly above this. Default: 60.",
                },
                "quiet_percentile": {
                    "type": "number",
                    "description": (
                        "Percentile of flow_rate (among good-quality points) defining the quiet band. "
                        "Default: 10 (bottom decile)."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "generate_plot",
        "description": (
            "Generate and save a chart of the flow rate data as a PNG file. "
            "Returns the absolute file path to embed in the report as a Markdown image. "
            "Always call with plot_type='time_series' for every analysis. "
            "Also call with 'peaks_annotated' when peaks are present, "
            "and 'flow_duration_curve' for a complete analysis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plot_type": {
                    "type": "string",
                    "enum": ["time_series", "flow_duration_curve", "peaks_annotated", "signal_quality"],
                    "description": (
                        "time_series: flow rate over time with low-quality readings highlighted in red. "
                        "flow_duration_curve: exceedance probability chart (Q10/Q50/Q90 marked). "
                        "peaks_annotated: time series with detected peaks labelled. "
                        "signal_quality: quality score over time with threshold line at 60 and low-quality zone shaded."
                    ),
                }
            },
            "required": ["plot_type"],
        },
    },
]


def _auto_detect_interval(timestamps: np.ndarray) -> float:
    """Estimate the nominal sampling interval from the median consecutive delta."""
    if len(timestamps) < 2:
        return 1.0
    return float(np.median(np.diff(timestamps.astype(float))))


def _dispatch_tool(
    name: str,
    inputs: Dict[str, Any],
    timestamps: np.ndarray,
    values: np.ndarray,
    quality: np.ndarray,
    serial_number: str,
) -> Any:
    """Route a tool call to the correct processor function."""
    if name == "compute_descriptive_stats":
        return compute_descriptive_stats(values)

    elif name == "detect_gaps":
        interval = inputs.get("expected_interval_seconds") or _auto_detect_interval(timestamps)
        return detect_gaps(timestamps, interval)

    elif name == "detect_zero_flow_periods":
        return detect_zero_flow_periods(
            timestamps, values, inputs.get("min_duration_seconds", 60.0)
        )

    elif name == "compute_total_volume":
        return compute_total_volume(timestamps, values)

    elif name == "detect_peaks":
        return detect_peaks(timestamps, values, inputs.get("prominence_multiplier", 1.0))

    elif name == "compute_flow_duration_curve":
        return compute_flow_duration_curve(values)

    elif name == "compute_linear_trend":
        return compute_linear_trend(timestamps, values)

    elif name == "compute_rolling_statistics":
        return compute_rolling_statistics(timestamps, values, inputs.get("window_size", 10))

    elif name == "detect_low_quality_readings":
        return detect_low_quality_readings(
            timestamps, values, quality, inputs.get("threshold", 60.0)
        )

    elif name == "summarize_quiet_flow_baseline":
        return summarize_quiet_flow_baseline(
            timestamps,
            values,
            quality,
            quality_threshold=float(inputs.get("quality_threshold", 60.0)),
            quiet_percentile=float(inputs.get("quiet_percentile", 10.0)),
        )

    elif name == "generate_plot":
        return generate_plot(
            inputs["plot_type"],
            timestamps, values, quality,
            serial_number=serial_number,
            start=timestamps[0] if len(timestamps) else 0,
        )

    else:
        raise ValueError(f"Unknown tool: {name}")


def analyze(df: pd.DataFrame, serial_number: str) -> str:
    """
    Run the agentic analysis loop on a flow rate DataFrame.

    The agent selects and calls processor tools, then writes a structured
    natural-language report grounded entirely in the tool outputs.

    Args:
        df:              DataFrame with columns: timestamp (int64), flow_rate (float64)
        serial_number:   Meter serial number for context

    Returns:
        Markdown-formatted analytical report string.
    """
    timestamps   = df["timestamp"].values.astype(float)
    values       = df["flow_rate"].values.astype(float)
    quality      = df["quality"].values.astype(float)      if "quality"      in df.columns else np.full(len(df), np.nan)
    flow_amount  = df["flow_amount"].values.astype(float)  if "flow_amount"  in df.columns else np.full(len(df), np.nan)

    data_overview = {
        "serial_number": serial_number,
        "total_points": int(len(df)),
        "start_timestamp": int(timestamps[0]) if len(timestamps) else None,
        "end_timestamp": int(timestamps[-1]) if len(timestamps) else None,
        "time_span_seconds": float(timestamps[-1] - timestamps[0]) if len(timestamps) > 1 else 0,
        "null_count": int(np.isnan(values).sum()),
        "has_quality_scores": bool(~np.isnan(quality).all()),
        "has_flow_amount": bool(~np.isnan(flow_amount).all()),
        "sample_values_head": [
            round(v, 4) for v in values[:5].tolist() if not np.isnan(v)
        ],
    }

    system_prompt = (
        "You are a precise time series analyst specialising in ultrasonic flow meter data. "
        "The devices use ultrasonic measurement: a quality score accompanies each reading and "
        "reflects how cleanly the sensor received its signal. "
        "A quality score at or below 60 means the sensor struggled — "
        "the corresponding flow rate is less reliable and should be flagged in the report. "
        "Low quality has two main causes: "
        "(1) the meter could not detect water inside the pipe — common when air bubbles are travelling "
        "through the pipe, or when the pipe has been drained; "
        "(2) the ultrasonic coupling pads are not properly seated between the meter transducer and the pipe wall, "
        "preventing a clean acoustic signal. "
        "When reporting low-quality events, consider the context: sustained low quality over a period "
        "suggests a drainage or installation issue, while intermittent spikes suggest passing air bubbles. "
        "You have access to a set of mathematical processor tools. "
        "You MUST use only these tools to obtain every number in your report — "
        "never compute or estimate statistics yourself. "
        "When quality scores are present (has_quality_scores=true), call summarize_quiet_flow_baseline "
        "once to characterise the quietest flow band (screening for residual flow / offset; not diagnostic proof). "
        "After calling all relevant tools, always call generate_plot with "
        "plot_type='time_series'. Always also call it with plot_type='signal_quality' "
        "when quality scores are present (has_quality_scores=true). "
        "Also call it with 'peaks_annotated' when peaks are present, "
        "and 'flow_duration_curve' for a complete analysis. "
        "Embed each returned path in the report as a Markdown image using the "
        "exact path from the tool result: ![Title](path). "
        "Then write a structured Markdown report that presents the findings "
        "clearly, referencing the tool outputs. "
        "Keep the final report concise: short sections, bullets where possible, "
        "no filler or repeated restatements; expand detail only when anomalies or "
        "low-quality periods require explanation."
    )

    user_message = (
        f"Analyse the flow rate time series for meter `{serial_number}`.\n\n"
        f"**Data overview:**\n```json\n{json.dumps(data_overview, indent=2)}\n```\n\n"
        "Run the processor tools needed to characterise this dataset (not every tool if irrelevant), "
        "then produce a concise analytical report covering: headline stats, data quality, "
        "quiet-flow baseline if quality data exists, flow behaviour and trends, and a brief summary."
    )

    max_output_tokens = int(os.environ.get("BLUEBOT_ANALYSIS_MAX_OUTPUT_TOKENS", "3072"))

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model=os.environ.get("BLUEBOT_ANALYSIS_MODEL", "claude-haiku-4-5"),
            max_tokens=max_output_tokens,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return "Analysis complete (no text output)."

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = _dispatch_tool(block.name, block.input, timestamps, values, quality, serial_number)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        }
                    )

            messages.append({"role": "user", "content": tool_results})

        else:
            break

    return "Analysis ended unexpectedly."
