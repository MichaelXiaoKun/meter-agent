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


def test_report_filter_refusal_does_not_emit_default_zero_metrics():
    facts = {
        "n_rows": 10,
        "baseline_quality": {"state": "not_requested", "reliable": False},
        "filter_applied": {
            "state": "invalid_spec",
            "applied": False,
            "n_rows_input": 10,
            "n_rows_kept": 0,
            "fraction_kept": None,
            "predicate_used": {"weekdays": [0]},
            "validation_errors": [
                "timezone is required when weekdays / hour_ranges / exclude_dates are provided"
            ],
            "reasons_refused": ["Filter spec failed validation; see validation_errors."],
        },
    }

    report = format_report("Analysis body", "BB1", 1, 2, verified_facts=facts)

    assert "Local-time filter" in report
    assert "Filter spec failed validation" in report
    assert "Gap events" not in report
    assert "Zero-flow periods" not in report
