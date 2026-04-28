"""
Tests for deterministic data-agent template mode.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import agent_template
import interface
from processors.plots import pop_figures
from processors.verified_facts import build_verified_facts


def _df() -> pd.DataFrame:
    ts = np.arange(1_700_000_000, 1_700_000_600, 10, dtype=float)
    return pd.DataFrame(
        {
            "timestamp": ts,
            "flow_rate": np.linspace(0, 6, len(ts)),
            "quality": np.full(len(ts), 90.0),
        }
    )


def test_analyze_template_renders_markdown_and_standard_plots():
    df = _df()
    facts = build_verified_facts(df)

    body = agent_template.analyze_template(df, "BB1", verified_facts=facts)
    pending = pop_figures()

    assert "# Flow analysis summary" in body
    assert "Rows analyzed" in body
    assert "Continuity and quality" in body
    assert len(pending) >= 2


def test_interface_template_mode_skips_llm(monkeypatch):
    df = _df()
    monkeypatch.setenv("BLUEBOT_DATA_AGENT_MODE", "template")
    monkeypatch.setattr(interface, "fetch_flow_data_range", lambda *args, **kwargs: df)

    def explode(*_args, **_kwargs):
        raise AssertionError("LLM analyzer should not be called in template mode")

    monkeypatch.setattr(interface, "analyze_llm", explode)

    out = interface.run("BB1", 1_700_000_000, 1_700_000_600, token="tok")
    pop_figures()

    assert out["success"] is True
    assert "# Flow analysis summary" in out["report"]
    assert out["analysis_bundle"]["verified_facts"]["n_rows"] == len(df)
