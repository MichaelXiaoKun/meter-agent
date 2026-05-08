"""
Regression tests for the orchestrator's user-facing language guardrails.

The live prompt lives in ``orchestrator/prompts/system_v<N>.md`` and is selected
at process start by ``ORCHESTRATOR_PROMPT_VERSION`` (default ``v1``). These
tests pin the default version so a bad edit to ``system_v1.md`` fails loudly —
adding a new version (``v2``) does not retire the guardrails on ``v1``.

Rule 15 (after the numbered re-order: rule 13 covers ``list_meters_for_account``
and rule 14 covers ``compare_meters``) forbids replies that leak internal tool
names, module/file paths, env vars, or codebase jargon ("sub-agent",
"subprocess", "JSON bundle", etc.). We locate it by header text so future
renumbering does not silently bypass the checks.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_ORCHESTRATOR_DIR = Path(__file__).resolve().parents[2] / "orchestrator"
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

from prompts import load_system_prompt  # noqa: E402


_RULE_USER_LANGUAGE_HEADER = "User-facing language"


@pytest.fixture(scope="module")
def system_prompt() -> str:
    """Return the pinned v1 system prompt text, read from disk via the loader."""
    text, version = load_system_prompt("v1")
    assert version == "v1"
    assert text, "v1 system prompt must not be empty"
    return text


@pytest.fixture(scope="module")
def user_language_rule_number(system_prompt: str) -> int:
    """Locate the numbered rule that starts the user-facing language section."""
    match = re.search(
        rf"^\s*(?P<num>\d+)\.\s.*{re.escape(_RULE_USER_LANGUAGE_HEADER)}",
        system_prompt,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    assert match, "Could not locate the user-facing language rule in _SYSTEM_PROMPT"
    return int(match.group("num"))


# ---------------------------------------------------------------------------
# Structural sanity — numbered rules must exist through the user-language rule.
# ---------------------------------------------------------------------------


def test_system_prompt_contains_numbered_rules(system_prompt, user_language_rule_number):
    for n in range(1, user_language_rule_number + 1):
        assert re.search(
            rf"^\s*{n}\.\s", system_prompt, flags=re.MULTILINE
        ), f"rule {n}. is missing from the orchestrator system prompt"


def test_rule_user_language_header_mentions_user_facing_language(system_prompt, user_language_rule_number):
    rule = _extract_rule(system_prompt, user_language_rule_number)
    assert rule, f"rule {user_language_rule_number} not found"
    assert re.search(
        r"user[- ]facing language|implementation leakage|no implementation leakage",
        rule,
        flags=re.IGNORECASE,
    ), (
        f"rule {user_language_rule_number} header no longer mentions user-facing language "
        "/ implementation leakage"
    )


# ---------------------------------------------------------------------------
# Content guardrails — the specific forbidden categories in the user-language rule.
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
def test_rule_user_language_bans_internal_names(system_prompt, user_language_rule_number, needle):
    rule = _extract_rule(system_prompt, user_language_rule_number)
    assert needle in rule, (
        f"rule {user_language_rule_number} no longer names {needle!r} as forbidden jargon; "
        "the LLM may regress to leaking it."
    )


def test_rule_user_language_bans_absolute_paths(system_prompt, user_language_rule_number):
    rule = _extract_rule(system_prompt, user_language_rule_number)
    assert re.search(
        r"absolute filesystem paths|/Users/|data-processing-agent/analyses|analysis_\*\.json",
        rule,
    ), (
        f"rule {user_language_rule_number} no longer bans leaking absolute filesystem paths "
        "/ bundle filenames"
    )


def test_rule_user_language_requires_alternative_on_refusal(system_prompt, user_language_rule_number):
    rule = _extract_rule(system_prompt, user_language_rule_number)
    assert re.search(r"alternative", rule, flags=re.IGNORECASE), (
        f"rule {user_language_rule_number} no longer requires offering a concrete alternative on refusal"
    )
    assert re.search(
        r"without explaining\s+\*?why\*?|do not explain why",
        rule,
        flags=re.IGNORECASE,
    ), (
        f"rule {user_language_rule_number} no longer tells the model to refuse without narrating the cause"
    )


def test_rule_user_language_bans_speculation(system_prompt, user_language_rule_number):
    rule = _extract_rule(system_prompt, user_language_rule_number)
    assert re.search(r"speculat", rule, flags=re.IGNORECASE), (
        f"rule {user_language_rule_number} no longer forbids speculating about internal data shapes"
    )


# ---------------------------------------------------------------------------
# Meta-check on the prompt as a whole — no raw absolute paths / secrets.
# ---------------------------------------------------------------------------


def test_prompt_contains_no_raw_absolute_paths(system_prompt):
    assert "/Users/" not in system_prompt or "``/Users/" in system_prompt, (
        "the system prompt appears to embed a real absolute path; only quoted "
        "examples inside the user-language rule are allowed"
    )


def test_prompt_still_mentions_every_tool(system_prompt):
    for tool in [
        "resolve_time_range",
        "check_meter_status",
        "get_meter_profile",
        "analyze_flow_data",
        "configure_meter_pipe",
        "set_transducer_angle_only",
        "sweep_transducer_angles",
        "set_zero_point",
        "list_tickets",
        "create_ticket",
        "update_ticket",
    ]:
        assert tool in system_prompt, f"tool {tool!r} dropped from the orchestrator prompt"


def test_ticket_accountability_rule(system_prompt):
    assert "Ticket accountability" in system_prompt
    rule = _extract_rule(system_prompt, 21)
    assert "success_criteria" in rule
    assert "bounded" in rule.lower()
    assert "tool result" in rule
    assert "verified diagnostic fact" in rule
    assert "Public sales mode must never create admin tickets" in rule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_rule(prompt: str, n: int) -> str:
    """Return the body of rule ``n.`` up to (but not including) the next rule marker."""
    pattern = rf"^\s*{n}\.\s(?P<body>.*?)(?=^\s*{n + 1}\.\s|\Z)"
    match = re.search(pattern, prompt, flags=re.DOTALL | re.MULTILINE)
    return match.group("body") if match else ""
