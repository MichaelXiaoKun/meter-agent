"""
Regression tests for the orchestrator's user-facing language guardrails.

The orchestrator's system prompt includes **rule 12**, which forbids replies
that leak internal tool names, module/file paths, env vars, or codebase jargon
("sub-agent", "subprocess", "JSON bundle", etc.). We extract the prompt via a
text scan — no imports — so the test stays green even when optional deps
(anthropic, tpm_window, httpx) aren't installed in the current interpreter.

If someone later trims rule 12 or renames / removes it, this test fails loudly
with a pointer at exactly what's missing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_AGENT_PATH = (
    Path(__file__).resolve().parents[2] / "orchestrator" / "agent.py"
)


@pytest.fixture(scope="module")
def system_prompt() -> str:
    """Return the ``_SYSTEM_PROMPT`` string literal from ``orchestrator/agent.py``."""
    src = _AGENT_PATH.read_text(encoding="utf-8")
    # Capture everything between ``_SYSTEM_PROMPT = """\`` and the closing ``"""``.
    match = re.search(
        r'_SYSTEM_PROMPT\s*=\s*"""\\?\n(?P<body>.*?)^"""',
        src,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match, "Could not locate _SYSTEM_PROMPT triple-quoted literal in agent.py"
    return match.group("body")


# ---------------------------------------------------------------------------
# Structural sanity — the rule we care about must exist and be labelled 12.
# ---------------------------------------------------------------------------


def test_system_prompt_contains_numbered_rules(system_prompt):
    # Rules 1..12 must all appear as list markers in the prompt.
    for n in range(1, 13):
        assert re.search(
            rf"^\s*{n}\.\s", system_prompt, flags=re.MULTILINE
        ), f"rule {n}. is missing from the orchestrator system prompt"


def test_rule_twelve_header_mentions_user_facing_language(system_prompt):
    # The rule is allowed to evolve wording, but it must still be titled around
    # "user-facing language" / "implementation leakage" so auditors find it.
    rule_12 = _extract_rule(system_prompt, 12)
    assert rule_12, "rule 12 not found"
    assert re.search(
        r"user[- ]facing language|implementation leakage|no implementation leakage",
        rule_12,
        flags=re.IGNORECASE,
    ), "rule 12 header no longer mentions user-facing language / implementation leakage"


# ---------------------------------------------------------------------------
# Content guardrails — the specific forbidden categories in rule 12.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        "analyze_flow_data",        # specific tool name must be called out as forbidden
        "resolve_time_range",
        "get_meter_profile",
        "verified_facts_precomputed",
        "baseline_quality",
        "BLUEBOT_",                 # env-var prefix ban
        "sub-agent",
        "subprocess",
    ],
)
def test_rule_twelve_bans_internal_names(system_prompt, needle):
    rule_12 = _extract_rule(system_prompt, 12)
    assert needle in rule_12, (
        f"rule 12 no longer names {needle!r} as forbidden jargon; "
        "the LLM may regress to leaking it."
    )


def test_rule_twelve_bans_absolute_paths(system_prompt):
    rule_12 = _extract_rule(system_prompt, 12)
    # Any of these path-y phrases proves we still forbid leaking filesystem paths.
    assert re.search(
        r"absolute filesystem paths|/Users/|data-processing-agent/analyses|analysis_\*\.json",
        rule_12,
    ), "rule 12 no longer bans leaking absolute filesystem paths / bundle filenames"


def test_rule_twelve_requires_alternative_on_refusal(system_prompt):
    rule_12 = _extract_rule(system_prompt, 12)
    # "refuse … offer a concrete alternative" — the graceful-refusal contract.
    assert re.search(r"alternative", rule_12, flags=re.IGNORECASE), (
        "rule 12 no longer requires offering a concrete alternative on refusal"
    )
    assert re.search(
        r"without explaining\s+\*?why\*?|do not explain why",
        rule_12,
        flags=re.IGNORECASE,
    ), "rule 12 no longer tells the model to refuse without narrating the cause"


def test_rule_twelve_bans_speculation(system_prompt):
    rule_12 = _extract_rule(system_prompt, 12)
    assert re.search(r"speculat", rule_12, flags=re.IGNORECASE), (
        "rule 12 no longer forbids speculating about internal data shapes"
    )


# ---------------------------------------------------------------------------
# Meta-check on the prompt as a whole — no raw absolute paths / secrets.
# ---------------------------------------------------------------------------


def test_prompt_contains_no_raw_absolute_paths(system_prompt):
    # The prompt itself should never ship a developer's home path.
    assert "/Users/" not in system_prompt or "``/Users/" in system_prompt, (
        "the system prompt appears to embed a real absolute path; only quoted "
        "examples inside rule 12 are allowed"
    )


def test_prompt_still_mentions_every_tool(system_prompt):
    # The "Available tools" block is where tool names legitimately appear;
    # it must stay in sync with ``TOOLS`` in agent.py.
    for tool in [
        "resolve_time_range",
        "check_meter_status",
        "get_meter_profile",
        "analyze_flow_data",
        "configure_meter_pipe",
        "set_transducer_angle_only",
    ]:
        assert tool in system_prompt, f"tool {tool!r} dropped from the orchestrator prompt"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_rule(prompt: str, n: int) -> str:
    """Return the body of rule ``n.`` up to (but not including) the next rule marker."""
    # Match "<n>." at the start of a (possibly-indented) line, then consume until
    # the next "<n+1>." marker or the end of the prompt.
    pattern = rf"^\s*{n}\.\s(?P<body>.*?)(?=^\s*{n + 1}\.\s|\Z)"
    match = re.search(pattern, prompt, flags=re.DOTALL | re.MULTILINE)
    return match.group("body") if match else ""
