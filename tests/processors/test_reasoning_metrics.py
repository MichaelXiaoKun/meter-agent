"""
Tests for ``processors.reasoning_metrics`` — the offline rollout-stability
utility used to validate the "same token budget, stronger next-step
consistency" claim.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from processors.reasoning_metrics import (
    _main,
    compare_digests,
    group_digests_by_subject,
    load_schema_from_bundle,
    score_bundle_dir,
    summarise_schema,
    walk_bundle_dir,
)


def _schema(
    *,
    regime: str,
    evidence: list[str] | None = None,
    hypotheses: list[str] | None = None,
    next_actions: list[str] | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "regime": regime,
        "evidence": [{"code": c, "severity": "medium"} for c in (evidence or [])],
        "hypotheses": [{"code": c, "confidence": 0.7, "because": []} for c in (hypotheses or [])],
        "next_checks": [
            {"priority": i + 1, "action": a, "for_hypothesis": "H", "expect": ""}
            for i, a in enumerate(next_actions or [])
        ],
        "conflict_policy": "trust",
        "context": {},
    }


# ---------------------------------------------------------------------------
# summarise_schema
# ---------------------------------------------------------------------------


class TestSummariseSchema:
    def test_extracts_codes_only_not_confidences(self):
        schema = _schema(
            regime="STEADY_FLOW",
            evidence=["E_GAP_LONG", "E_COVERAGE_SPARSE"],
            hypotheses=["H_COMMS_INSTABILITY"],
            next_actions=["check_uplink_rssi_and_packet_loss"],
        )
        d = summarise_schema(schema)
        assert d["regime"] == "STEADY_FLOW"
        assert d["evidence_codes"] == ["E_COVERAGE_SPARSE", "E_GAP_LONG"]
        assert d["hypothesis_codes"] == ["H_COMMS_INSTABILITY"]
        assert d["next_check_actions"] == ["check_uplink_rssi_and_packet_loss"]

    def test_handles_empty_schema(self):
        d = summarise_schema({})
        assert d["regime"] == "UNKNOWN"
        assert d["evidence_codes"] == []

    def test_handles_non_dict(self):
        d = summarise_schema("not a dict")  # type: ignore[arg-type]
        assert d["regime"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# compare_digests
# ---------------------------------------------------------------------------


class TestCompareDigests:
    def test_perfect_stability_over_identical_runs(self):
        d1 = summarise_schema(_schema(
            regime="STEADY_FLOW", evidence=["E_GAP_LONG"],
            hypotheses=["H_COMMS_INSTABILITY"],
            next_actions=["check_uplink_rssi_and_packet_loss"],
        ))
        metrics = compare_digests([d1, d1, d1])
        assert metrics["n_runs"] == 3
        assert metrics["regime_agreement"] == 1.0
        assert metrics["evidence_jaccard_mean"] == 1.0
        assert metrics["hypothesis_jaccard_mean"] == 1.0
        assert metrics["next_check_top1_agreement"] == 1.0
        assert metrics["mode_regime"] == "STEADY_FLOW"
        assert metrics["mode_top_action"] == "check_uplink_rssi_and_packet_loss"

    def test_regime_disagreement_drops_agreement(self):
        a = summarise_schema(_schema(regime="STEADY_FLOW"))
        b = summarise_schema(_schema(regime="INTERMITTENT_BURST"))
        metrics = compare_digests([a, a, b])
        assert metrics["regime_agreement"] == pytest.approx(2 / 3, abs=1e-3)
        assert metrics["mode_regime"] == "STEADY_FLOW"

    def test_jaccard_reports_partial_overlap(self):
        a = summarise_schema(_schema(regime="X", evidence=["E_GAP_LONG", "E_COVERAGE_SPARSE"]))
        b = summarise_schema(_schema(regime="X", evidence=["E_GAP_LONG", "E_QUALITY_DROP"]))
        metrics = compare_digests([a, b])
        # One shared out of three total ⇒ 1/3 = 0.3333.
        assert metrics["evidence_jaccard_mean"] == pytest.approx(1 / 3, abs=1e-3)

    def test_empty_digests_returns_neutral_metrics(self):
        metrics = compare_digests([])
        assert metrics["n_runs"] == 0
        assert metrics["regime_agreement"] == 1.0


# ---------------------------------------------------------------------------
# Bundle file integration
# ---------------------------------------------------------------------------


class TestBundleWalkers:
    def test_load_schema_from_bundle_roundtrip(self, tmp_path: Path):
        bundle = {
            "serial_number": "BB1",
            "range": {"start_unix": 0, "end_unix": 1},
            "verified_facts": {
                "n_rows": 1,
                "reasoning_schema": _schema(
                    regime="STEADY_FLOW",
                    evidence=["E_GAP_LONG"],
                    hypotheses=["H_COMMS_INSTABILITY"],
                ),
            },
            "plot_paths": [],
        }
        p = tmp_path / "analysis_BB1_0_1.json"
        p.write_text(json.dumps(bundle))
        schema = load_schema_from_bundle(str(p))
        assert schema is not None
        assert schema["regime"] == "STEADY_FLOW"

    def test_load_schema_handles_missing_file(self, tmp_path: Path):
        assert load_schema_from_bundle(str(tmp_path / "nope.json")) is None

    def test_load_schema_handles_bundle_without_schema(self, tmp_path: Path):
        bundle = {"verified_facts": {"n_rows": 0}, "plot_paths": []}
        p = tmp_path / "analysis_old.json"
        p.write_text(json.dumps(bundle))
        assert load_schema_from_bundle(str(p)) is None

    def test_walk_bundle_dir_collects_digests(self, tmp_path: Path):
        for i in range(3):
            bundle = {
                "verified_facts": {
                    "reasoning_schema": _schema(
                        regime="STEADY_FLOW",
                        evidence=["E_GAP_LONG"],
                        hypotheses=["H_COMMS_INSTABILITY"],
                        next_actions=["check_uplink_rssi_and_packet_loss"],
                    ),
                },
            }
            (tmp_path / f"analysis_BB_{i}.json").write_text(json.dumps(bundle))
        # An unrelated file must be ignored by the prefix filter.
        (tmp_path / "other.json").write_text("{}")
        digests = walk_bundle_dir(str(tmp_path))
        assert len(digests) == 3
        metrics = compare_digests(digests)
        assert metrics["regime_agreement"] == 1.0
        assert metrics["mode_top_action"] == "check_uplink_rssi_and_packet_loss"

    def test_walk_bundle_dir_missing_dir(self, tmp_path: Path):
        assert walk_bundle_dir(str(tmp_path / "missing")) == []


# ---------------------------------------------------------------------------
# Subject-grouped scoring — the actual "rollout metric"
# ---------------------------------------------------------------------------


def _write_bundle(
    path: Path,
    *,
    serial: str,
    start: int,
    end: int,
    schema: dict,
) -> None:
    bundle = {
        "serial_number": serial,
        "range": {"start_unix": start, "end_unix": end},
        "verified_facts": {"n_rows": 1, "reasoning_schema": schema},
        "plot_paths": [],
    }
    path.write_text(json.dumps(bundle))


class TestGroupingAndScoring:
    def test_group_digests_by_subject_separates_inputs(self, tmp_path: Path):
        s_a = _schema(regime="STEADY_FLOW", evidence=["E_GAP_LONG"])
        s_b = _schema(regime="INTERMITTENT_BURST", evidence=["E_TOF_NOISE_UP"])
        _write_bundle(tmp_path / "analysis_A1.json", serial="A", start=0, end=10, schema=s_a)
        _write_bundle(tmp_path / "analysis_A2.json", serial="A", start=0, end=10, schema=s_a)
        _write_bundle(tmp_path / "analysis_B1.json", serial="B", start=0, end=10, schema=s_b)
        grouped = group_digests_by_subject(str(tmp_path))
        assert set(grouped.keys()) == {("A", 0, 10), ("B", 0, 10)}
        assert len(grouped[("A", 0, 10)]) == 2
        assert len(grouped[("B", 0, 10)]) == 1

    def test_score_bundle_dir_reports_per_subject_and_aggregate(self, tmp_path: Path):
        good = _schema(
            regime="STEADY_FLOW",
            evidence=["E_GAP_LONG"],
            hypotheses=["H_COMMS_INSTABILITY"],
            next_actions=["check_uplink_rssi_and_packet_loss"],
        )
        wobbly = _schema(
            regime="INTERMITTENT_BURST",
            evidence=["E_GAP_LONG"],
            hypotheses=["H_COMMS_INSTABILITY"],
            next_actions=["check_uplink_rssi_and_packet_loss"],
        )
        _write_bundle(tmp_path / "analysis_A1.json", serial="A", start=0, end=10, schema=good)
        _write_bundle(tmp_path / "analysis_A2.json", serial="A", start=0, end=10, schema=good)
        _write_bundle(tmp_path / "analysis_A3.json", serial="A", start=0, end=10, schema=wobbly)
        _write_bundle(tmp_path / "analysis_B1.json", serial="B", start=0, end=10, schema=good)

        scorecard = score_bundle_dir(str(tmp_path))
        assert scorecard["n_subjects"] == 2

        by_key = {(s["serial_number"], s["start_unix"], s["end_unix"]): s for s in scorecard["subjects"]}
        sa = by_key[("A", 0, 10)]
        assert sa["n_runs"] == 3
        assert sa["regime_agreement"] == pytest.approx(2 / 3, abs=1e-3)
        assert sa["mode_regime"] == "STEADY_FLOW"

        sb = by_key[("B", 0, 10)]
        assert sb["n_runs"] == 1
        assert sb["regime_agreement"] == 1.0

        agg = scorecard["aggregate"]
        assert agg["n_subjects_multi_run"] == 1
        assert agg["regime_agreement_mean"] == pytest.approx(2 / 3, abs=1e-3)

    def test_score_bundle_dir_handles_missing_dir(self, tmp_path: Path):
        scorecard = score_bundle_dir(str(tmp_path / "nope"))
        assert scorecard["n_subjects"] == 0
        assert scorecard["aggregate"]["n_subjects_multi_run"] == 0


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_prints_scorecard_as_json(self, tmp_path: Path, capsys):
        _write_bundle(
            tmp_path / "analysis_A1.json",
            serial="A",
            start=0,
            end=10,
            schema=_schema(regime="STEADY_FLOW"),
        )
        rc = _main(["reasoning_metrics", str(tmp_path)])
        assert rc == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["n_subjects"] == 1
        assert payload["subjects"][0]["serial_number"] == "A"

    def test_cli_without_args_prints_usage(self, capsys):
        rc = _main(["reasoning_metrics"])
        assert rc == 2
        assert "Usage" in capsys.readouterr().err

    def test_cli_help_flag_is_not_an_error(self, capsys):
        rc = _main(["reasoning_metrics", "--help"])
        assert rc == 0
