"""
Rollout metrics for the reasoning schema.

This module answers one question: *under a fixed token budget, does the
schema-anchored output actually make the agent's "next step" advice more
consistent than free-form narrative?*

It is intentionally deterministic and LLM-free so you can run it in CI or
offline over a folder of analysis bundles produced by
``processors.analysis_bundle.build_analysis_bundle``. It does NOT generate
new text; it aggregates existing schemas.

Two core primitives:

- ``summarise_schema(schema)``: one bundle → token-budget-independent digest
  (regime + evidence codes + hypothesis codes + next-check actions).
- ``compare_digests(digests)``: a set of digests (e.g. same meter / range
  across multiple runs) → stability metrics (Jaccard, exact-match rates,
  regime agreement).

Outputs are JSON-friendly so CI can diff them over time.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Schema digest — compact, comparable across runs
# ---------------------------------------------------------------------------


def summarise_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reduce a reasoning schema to the sets / labels that a human reviewer
    would consider "the same conclusion" across runs.

    Intentionally drops numeric confidences and severity levels: a run that
    flips ``H_COMMS_INSTABILITY`` from 0.70 to 0.72 should still count as
    consistent; a run that swaps the hypothesis for ``H_REAL_PROCESS_CHANGE``
    should not.
    """
    if not isinstance(schema, dict):
        return {
            "regime": "UNKNOWN",
            "evidence_codes": [],
            "hypothesis_codes": [],
            "next_check_actions": [],
        }
    evidence = schema.get("evidence") or []
    hypotheses = schema.get("hypotheses") or []
    next_checks = schema.get("next_checks") or []
    return {
        "regime": schema.get("regime") or "UNKNOWN",
        "evidence_codes": sorted({e.get("code") for e in evidence if isinstance(e, dict) and e.get("code")}),
        "hypothesis_codes": sorted({h.get("code") for h in hypotheses if isinstance(h, dict) and h.get("code")}),
        "next_check_actions": [
            nc.get("action")
            for nc in next_checks
            if isinstance(nc, dict) and nc.get("action")
        ],
    }


# ---------------------------------------------------------------------------
# Set-similarity helpers
# ---------------------------------------------------------------------------


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    if not union:
        return 1.0
    return round(len(sa & sb) / len(union), 4)


def _pairwise_mean(values: Sequence[float]) -> float:
    if not values:
        return 1.0
    return round(sum(values) / len(values), 4)


# ---------------------------------------------------------------------------
# Digest-set comparison — stability metrics
# ---------------------------------------------------------------------------


def compare_digests(digests: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregate stability metrics across N digests of the *same* analysis input.

    Returns:
        {
            "n_runs": int,
            "regime_agreement": fraction of runs whose regime matches the mode,
            "evidence_jaccard_mean": mean pairwise Jaccard over evidence codes,
            "hypothesis_jaccard_mean": mean pairwise Jaccard over hypothesis codes,
            "next_check_top1_agreement": fraction whose top action matches the mode,
            "mode_regime": most common regime label,
            "mode_top_action": most common first next-check action (or None),
        }
    """
    n = len(digests)
    if n == 0:
        return {
            "n_runs": 0,
            "regime_agreement": 1.0,
            "evidence_jaccard_mean": 1.0,
            "hypothesis_jaccard_mean": 1.0,
            "next_check_top1_agreement": 1.0,
            "mode_regime": None,
            "mode_top_action": None,
        }

    regimes = [d.get("regime") or "UNKNOWN" for d in digests]
    mode_regime = max(set(regimes), key=regimes.count)
    regime_agreement = round(regimes.count(mode_regime) / n, 4)

    def _pairwise(field: str) -> float:
        if n < 2:
            return 1.0
        scores: List[float] = []
        for i in range(n):
            for j in range(i + 1, n):
                scores.append(_jaccard(digests[i].get(field) or [], digests[j].get(field) or []))
        return _pairwise_mean(scores)

    evidence_j = _pairwise("evidence_codes")
    hypothesis_j = _pairwise("hypothesis_codes")

    top_actions = [
        (d.get("next_check_actions") or [None])[0] for d in digests
    ]
    non_null = [a for a in top_actions if a]
    if non_null:
        mode_top = max(set(non_null), key=non_null.count)
        top1_agreement = round(top_actions.count(mode_top) / n, 4)
    else:
        mode_top = None
        top1_agreement = 1.0 if all(a is None for a in top_actions) else 0.0

    return {
        "n_runs": n,
        "regime_agreement": regime_agreement,
        "evidence_jaccard_mean": evidence_j,
        "hypothesis_jaccard_mean": hypothesis_j,
        "next_check_top1_agreement": top1_agreement,
        "mode_regime": mode_regime,
        "mode_top_action": mode_top,
    }


# ---------------------------------------------------------------------------
# Bundle-file walkers (for offline CI / eval dashboards)
# ---------------------------------------------------------------------------


def _load_bundle(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_schema_from_bundle(path: str) -> Optional[Dict[str, Any]]:
    """
    Read an analysis bundle written by ``main.py`` and return the embedded
    ``reasoning_schema`` (or ``None`` when the file predates this feature).
    """
    data = _load_bundle(path)
    if data is None:
        return None
    facts = data.get("verified_facts")
    if isinstance(facts, dict):
        schema = facts.get("reasoning_schema")
        if isinstance(schema, dict):
            return schema
    return None


def _subject_key(bundle: Dict[str, Any]) -> Tuple[str, int, int]:
    """
    Stable identity of the analysis *input* so only like-with-like runs are
    compared. Missing fields fall back to sentinel values that keep
    heterogeneous bundles from silently collapsing into the same group.
    """
    serial = str(bundle.get("serial_number") or "UNKNOWN_SERIAL")
    rng = bundle.get("range") or {}
    try:
        start = int(rng.get("start_unix")) if rng.get("start_unix") is not None else -1
    except (TypeError, ValueError):
        start = -1
    try:
        end = int(rng.get("end_unix")) if rng.get("end_unix") is not None else -1
    except (TypeError, ValueError):
        end = -1
    return (serial, start, end)


def walk_bundle_dir(dir_path: str) -> List[Dict[str, Any]]:
    """
    Collect digests for every ``analysis_*.json`` in a directory.

    Useful as: ``compare_digests(walk_bundle_dir("analyses/"))`` to get a
    quick stability scorecard over a batch of runs — **but only when the
    directory contains runs of the same input**. For mixed directories, use
    :func:`score_bundle_dir` which groups by subject first.
    """
    digests: List[Dict[str, Any]] = []
    if not os.path.isdir(dir_path):
        return digests
    for name in sorted(os.listdir(dir_path)):
        if not name.startswith("analysis_") or not name.endswith(".json"):
            continue
        schema = load_schema_from_bundle(os.path.join(dir_path, name))
        if schema is None:
            continue
        digests.append(summarise_schema(schema))
    return digests


def group_digests_by_subject(
    dir_path: str,
) -> Dict[Tuple[str, int, int], List[Dict[str, Any]]]:
    """
    Walk a directory of analysis bundles and bucket their digests by
    (serial_number, start_unix, end_unix).

    Stability is only meaningful within a subject: a directory that mixes
    five meters should produce five scorecards, not one averaged soup.
    """
    grouped: Dict[Tuple[str, int, int], List[Dict[str, Any]]] = {}
    if not os.path.isdir(dir_path):
        return grouped
    for name in sorted(os.listdir(dir_path)):
        if not name.startswith("analysis_") or not name.endswith(".json"):
            continue
        bundle = _load_bundle(os.path.join(dir_path, name))
        if bundle is None:
            continue
        facts = bundle.get("verified_facts")
        if not isinstance(facts, dict):
            continue
        schema = facts.get("reasoning_schema")
        if not isinstance(schema, dict):
            continue
        key = _subject_key(bundle)
        grouped.setdefault(key, []).append(summarise_schema(schema))
    return grouped


def score_bundle_dir(dir_path: str) -> Dict[str, Any]:
    """
    Produce a JSON-serialisable stability scorecard for a directory of
    analysis bundles, grouped by subject.

    The top-level shape is designed to be friendly to CI diffs:

        {
          "dir": "...",
          "n_subjects": int,
          "subjects": [
            {
              "serial_number": "...",
              "start_unix": int,
              "end_unix": int,
              "n_runs": int,
              "regime_agreement": float,
              "evidence_jaccard_mean": float,
              "hypothesis_jaccard_mean": float,
              "next_check_top1_agreement": float,
              "mode_regime": str|None,
              "mode_top_action": str|None,
            },
            ...
          ],
          "aggregate": {  # subject-weighted, excludes single-run subjects
            "n_subjects_multi_run": int,
            "regime_agreement_mean": float,
            "evidence_jaccard_mean": float,
            "hypothesis_jaccard_mean": float,
            "next_check_top1_agreement_mean": float,
          }
        }
    """
    grouped = group_digests_by_subject(dir_path)
    subjects: List[Dict[str, Any]] = []
    multi_run_scores: List[Dict[str, Any]] = []
    for (serial, start, end), digests in sorted(grouped.items()):
        score = compare_digests(digests)
        subject_entry = {
            "serial_number": serial,
            "start_unix": start,
            "end_unix": end,
            **score,
        }
        subjects.append(subject_entry)
        if score["n_runs"] >= 2:
            multi_run_scores.append(score)

    def _mean(field: str) -> float:
        if not multi_run_scores:
            return 1.0
        return round(
            sum(float(s[field]) for s in multi_run_scores) / len(multi_run_scores),
            4,
        )

    aggregate = {
        "n_subjects_multi_run": len(multi_run_scores),
        "regime_agreement_mean": _mean("regime_agreement"),
        "evidence_jaccard_mean": _mean("evidence_jaccard_mean"),
        "hypothesis_jaccard_mean": _mean("hypothesis_jaccard_mean"),
        "next_check_top1_agreement_mean": _mean("next_check_top1_agreement"),
    }

    return {
        "dir": os.path.abspath(dir_path) if os.path.isdir(dir_path) else dir_path,
        "n_subjects": len(subjects),
        "subjects": subjects,
        "aggregate": aggregate,
    }


# ---------------------------------------------------------------------------
# CLI — keeps CI / shell usage dead simple
# ---------------------------------------------------------------------------


def _main(argv: Sequence[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        print(
            "Usage: python -m processors.reasoning_metrics <bundle_dir>\n"
            "\n"
            "Emits a JSON stability scorecard to stdout. Only analyses of the\n"
            "same (serial_number, start_unix, end_unix) are compared to each\n"
            "other; mixed directories produce one scorecard per subject.",
            file=sys.stderr,
        )
        return 2 if len(argv) < 2 else 0
    scorecard = score_bundle_dir(argv[1])
    json.dump(scorecard, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(_main(sys.argv))
