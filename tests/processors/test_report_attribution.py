from __future__ import annotations

from report import format_report


def test_report_includes_diagnostic_interpretation_block():
    facts = {
        "n_rows": 1,
        "gap_event_count": 0,
        "largest_gap_duration_seconds": 0,
        "anomaly_attribution": {
            "primary_type": "real_flow_change",
            "severity": "medium",
            "confidence": "high",
            "summary": "The strongest interpretation is a real sustained upward flow change.",
            "primary_cause": "CUSUM found sustained upward drift with adequate data.",
            "evidence": [
                {
                    "code": "CUSUM_DRIFT",
                    "message": "CUSUM detected upward drift.",
                    "source": "cusum_drift",
                }
            ],
            "next_checks": ["Compare against the previous day"],
        },
    }

    report = format_report("Analysis body", "BB1", 1, 2, verified_facts=facts)

    assert "Diagnostic interpretation" in report
    assert "`real_flow_change`" in report
    assert "CUSUM detected upward drift" in report
