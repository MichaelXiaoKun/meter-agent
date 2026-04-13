"""
Signal Quality Processor

Interprets the current signal quality score of the ultrasonic meter.

Quality reflects how cleanly the ultrasonic sensor receives its signal through the pipe wall.
Low quality has two main causes:
  1. No water detected in the pipe — common when air bubbles are travelling through,
     or when the pipe section has been drained entirely.
  2. Ultrasonic coupling pads not properly seated between the transducer and the pipe wall,
     preventing clean acoustic signal transmission.

Sustained low quality → drainage or coupling installation issue.
Intermittent low quality → passing air bubbles.
"""

from typing import Any, Dict


def interpret_signal_quality(signal_quality: str | int | float) -> Dict[str, Any]:
    """
    Interpret the current ultrasonic signal quality score.

    Args:
        signal_quality:  Quality score (0–100), may arrive as a string from the API.

    Returns:
        score:          Numeric score (0–100)
        level:          "good" | "degraded" | "poor"
        reliable:       True if the signal is considered reliable (score > 60)
        interpretation: Plain-language description of what the score means
        action_needed:  True if the score warrants investigation
    """
    score = int(float(str(signal_quality)))

    if score > 80:
        level = "good"
        reliable = True
        interpretation = "Signal is strong. Measurements are reliable."
        action_needed = False
    elif score > 60:
        level = "degraded"
        reliable = True
        interpretation = (
            "Signal is weakened but still within acceptable range. "
            "Monitor for further degradation."
        )
        action_needed = False
    else:
        level = "poor"
        reliable = False
        interpretation = (
            "Signal quality is below the reliability threshold (≤ 60). "
            "Likely causes: air bubbles or drained pipe (intermittent), "
            "or coupling pads not properly seated against the pipe wall (persistent)."
        )
        action_needed = True

    return {
        "score": score,
        "level": level,
        "reliable": reliable,
        "interpretation": interpretation,
        "action_needed": action_needed,
    }
