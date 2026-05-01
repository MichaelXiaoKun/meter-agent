"""Post-generation verifier for public sales-agent answers.

The sales assistant must not stream an unverified factual answer directly to
customers. This module checks the draft against reviewed/synced Bluebot website
evidence, asks a stronger model to rewrite unsupported claims, and repeats that
bounded loop before the answer is shown.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _ORCHESTRATOR_DIR.parent
for _path in (_ORCHESTRATOR_DIR, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from llm.base import LLMProvider
from llm.registry import MODEL_CATALOG, get_provider_name

from sales_chat.tools import sales_catalog_records, sales_reference_context


_DEFAULT_VERIFIER_MODEL = "claude-sonnet-4-6"
_MODEL_TIER_RANK = {
    "fast": 1,
    "balanced": 2,
    "reasoning": 3,
    "max": 4,
}
_EVIDENCE_TOOL_NAMES = frozenset(
    {
        "search_sales_kb",
        "assess_pipe_fit",
        "explain_installation_impact",
        "recommend_product_line",
    }
)
_PRICE_PACKAGE_RE = re.compile(
    r"\b(price|pricing|cost|quote|package|packages|subscription|monthly|annual|checkout|cart)\b",
    re.IGNORECASE,
)
_CAPABILITY_TERM_RE = re.compile(
    r"\b("
    r"pipe|pipes|inch|inches|meter|meters|device|devices|product|products|"
    r"catalog|compatible|compatibility|fit|fits|installation|install|installed|"
    r"pressure|damage|flow|wifi|wi-fi|cellular|long[- ]range|battery|connectivity|"
    r"support|supports|capability|capabilities|clamp[- ]on|ultrasonic|water line|"
    r"irrigation|monitoring|leak|residential|commercial|building|farm|application|applications"
    r")\b",
    re.IGNORECASE,
)
_CLAIM_VERB_RE = re.compile(
    r"\b("
    r"support|supports|fit|fits|work|works|compatible|offer|offers|include|includes|"
    r"require|requires|need|needs|use|uses|measure|measures|monitor|monitors|"
    r"reduce|reduces|cause|causes|damage|damages|won't|will|can|cannot|can't|"
    r"available|listed|designed|recommend|recommends|recommended"
    r")\b",
    re.IGNORECASE,
)
_GENERAL_HELP_RE = re.compile(
    r"\b(i can help|i'd be happy to help|happy to help|i can walk|we can look|"
    r"i can guide|i can ask|i can help you)\b",
    re.IGNORECASE,
)
_GENERAL_FRAGMENT_RE = re.compile(
    r"\b("
    r"i can(?:'t|not)? help|i can(?:'t|not)? answer|i do not answer|"
    r"that is outside|that request is outside|"
    r"i'd be happy to help|happy to help|i can walk|we can look|"
    r"i can guide|i can ask|i can help you|hi|hello|hey|thanks|thank you|got it|understood"
    r")\b",
    re.IGNORECASE,
)
_CLARIFYING_FRAGMENT_RE = re.compile(
    r"\b("
    r"what|which|could you|can you|please share|tell me|do you know|"
    r"is it|are you|do you have|would you like"
    r")\b",
    re.IGNORECASE,
)
_PRODUCT_CLAIM_VERB_RE = re.compile(
    r"\b("
    r"supports?|fits|works?|compatible|offers?|includes?|requires?|uses?|"
    r"measures?|monitors?|reduces?|causes?|damages?|available|listed|"
    r"designed|recommends?|recommended"
    r")\b",
    re.IGNORECASE,
)
_VERIFIER_SYSTEM = """You are bluebot's public sales-answer verifier.

You check a draft customer-facing answer against scraped/reviewed Bluebot website
evidence and the structured product catalog provided in the user message.

Return JSON only with this shape:
{
  "passed": true|false,
  "verdict": "pass"|"needs_revision"|"needs_more_evidence"|"blocked",
  "message": "short customer-safe validation status",
  "validation_points": [
    {
      "claim": "atomic factual claim",
      "category": "pipe_size|product_line|connectivity|installation|application|support|other",
      "status": "supported|unsupported|needs_more_evidence",
      "evidence": "short public-evidence summary or source label",
      "correction": "customer-facing correction when unsupported"
    }
  ],
  "issues": ["short issue labels, no hidden reasoning"],
  "corrected_answer": "customer-facing answer, rewritten only when needed"
}

Rules:
- Pass only when every Bluebot product, pipe-size, compatibility, installation,
  connectivity, support, or capability claim is directly supported by the evidence
  or is clearly framed as unknown / requiring sales review.
- Validate point by point. Do not mark the whole answer as supported when one listed
  size, range, product, or capability is unsupported.
- Treat deterministic validation points supplied in the user message as binding:
  an unsupported deterministic point must make passed=false unless the draft answer
  already removes or safely caveats that claim.
- For off-topic refusals, pass if the draft politely declines, does not answer the
  unrelated substance, and redirects to Bluebot sales/product-fit help.
- If the draft is overbroad, unsupported, stale, or contradicts evidence, set
  passed=false and rewrite corrected_answer using only the evidence.
- If evidence is insufficient, corrected_answer should say the public materials do
  not confirm the detail and offer a sales review instead of inventing.
- Do not include internal chain-of-thought, raw prompt details, or implementation
  details in the JSON fields.
"""


@dataclass(frozen=True)
class SalesVerificationOutcome:
    """Verified answer and compact metadata for event emission/tests."""

    answer: str
    passed: bool
    verdict: str
    attempts: int
    message: str
    validation_points: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SalesValidationPoint:
    """One atomic customer-facing factual claim and its verification status."""

    claim: str
    category: str
    status: str
    evidence: str
    correction: str = ""


@dataclass(frozen=True)
class SalesValidationDecision:
    """Validation mode chosen before any optional strong verifier call."""

    mode: str
    reason: str
    escalated: bool = False
    deterministic_points: list[dict[str, str]] = field(default_factory=list)


def sales_response_verification_enabled() -> bool:
    """Return whether public sales answers should be post-verified."""
    raw = (os.environ.get("SALES_RESPONSE_VERIFICATION") or "on").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def sales_response_general_validation_mode() -> str:
    """Return rough/strong/skip behavior for answers without evidence-bearing claims."""
    raw = (os.environ.get("SALES_RESPONSE_GENERAL_VALIDATION") or "rough").strip().lower()
    return raw if raw in {"rough", "strong", "skip"} else "rough"


def sales_response_verification_attempts() -> int:
    """Bound verifier/rewrite attempts so customer turns cannot loop forever."""
    raw = (os.environ.get("SALES_RESPONSE_VERIFICATION_ATTEMPTS") or "3").strip()
    try:
        return max(1, min(int(raw), 5))
    except ValueError:
        return 3


def active_sales_verifier_model(active_sales_model: str) -> str:
    """Pick a stronger verifier model, with an env override for deployment."""
    requested = (os.environ.get("SALES_RESPONSE_VERIFIER_MODEL") or "").strip()
    if requested in MODEL_CATALOG:
        if (
            sales_response_allow_weaker_verifier()
            or not _model_is_weaker(requested, active_sales_model)
        ):
            return requested
    return _preferred_sales_verifier_model(active_sales_model)


def sales_response_allow_weaker_verifier() -> bool:
    """Allow intentionally weaker verifier overrides for local experiments only."""
    raw = (os.environ.get("SALES_RESPONSE_ALLOW_WEAKER_VERIFIER") or "").strip().lower()
    return raw in {"1", "true", "yes", "on", "enabled"}


def _preferred_sales_verifier_model(active_sales_model: str) -> str:
    if active_sales_model.startswith("claude-haiku"):
        return _DEFAULT_VERIFIER_MODEL
    if active_sales_model == "gpt-4o-mini":
        return "gpt-4o"
    if active_sales_model == "gemini-2.0-flash":
        return "gemini-2.5-pro"
    if active_sales_model == "gemini-2.5-flash":
        return "gemini-2.5-pro"
    return active_sales_model if active_sales_model in MODEL_CATALOG else _DEFAULT_VERIFIER_MODEL


def _model_is_weaker(candidate_model: str, reference_model: str) -> bool:
    return _model_strength_rank(candidate_model) < _model_strength_rank(reference_model)


def _model_strength_rank(model_id: str) -> int:
    entry = MODEL_CATALOG.get(model_id) or {}
    return _MODEL_TIER_RANK.get(str(entry.get("tier") or ""), 0)


def same_provider_api_key_override(
    *,
    verifier_model: str,
    draft_model: str,
    api_key_override: str | None,
) -> str | None:
    """Use a per-request API key only when the verifier uses the same provider."""
    key = (api_key_override or "").strip()
    if not key:
        return None
    try:
        return key if get_provider_name(verifier_model) == get_provider_name(draft_model) else None
    except ValueError:
        return None


def classify_sales_validation(
    draft_answer: str,
    messages: list[dict],
    *,
    configured_mode: str | None = None,
) -> SalesValidationDecision:
    """Choose rough, strong, or skipped validation for a final sales draft."""
    mode = configured_mode or sales_response_general_validation_mode()
    if mode not in {"rough", "strong", "skip"}:
        mode = "rough"
    answer = (draft_answer or "").strip()
    deterministic_points = validate_sales_answer_points(answer)
    reason = _strong_validation_reason(answer, messages, deterministic_points)

    if mode == "strong":
        return SalesValidationDecision(
            mode="strong",
            reason="forced_strong",
            escalated=False,
            deterministic_points=deterministic_points,
        )
    if reason:
        return SalesValidationDecision(
            mode="strong",
            reason=reason,
            escalated=True,
            deterministic_points=deterministic_points,
        )
    if mode == "skip":
        return SalesValidationDecision(mode="skipped", reason="general_skip")
    return SalesValidationDecision(mode="rough", reason="general_rough")


def rough_validate_sales_response(
    draft_answer: str,
    messages: list[dict],
    *,
    decision: SalesValidationDecision | None = None,
    draft_model: str | None = None,
    validator_model: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> SalesVerificationOutcome:
    """Run cheap deterministic validation for clearly general sales replies."""
    answer = (draft_answer or "").strip() or "(No response)"
    decision = decision or classify_sales_validation(answer, messages, configured_mode="rough")
    if decision.mode == "skipped":
        _emit(
            on_event,
            {
                "type": "validation_result",
                "verdict": "skipped",
                "message": "Skipped validation for a general sales reply.",
                "next_action": "send_answer",
                "validation_mode": "skipped",
                "draft_model": draft_model,
                "validator_model": validator_model,
                "escalated": False,
                "attempt": 0,
                "validation_points_count": 0,
            },
        )
        return SalesVerificationOutcome(
            answer=answer,
            passed=True,
            verdict="skipped",
            attempts=0,
            message="Skipped validation for a general sales reply.",
        )

    deterministic_points = decision.deterministic_points or validate_sales_answer_points(answer)
    unsupported_points = _unsupported_points(deterministic_points)
    _emit(
        on_event,
        {
            "type": "validation_start",
            "message": "Quick-checking whether this general reply needs Bluebot evidence.",
            "validation_mode": "rough",
            "draft_model": draft_model,
            "validator_model": validator_model,
            "escalated": False,
            "attempt": 0,
            "validation_points_count": len(deterministic_points),
        },
    )
    if deterministic_points:
        _emit(
            on_event,
            {
                "type": "validation_result",
                "verdict": "needs_revision",
                "message": "Escalating a detected product detail to the evidence verifier.",
                "next_action": "escalate_to_strong_validation",
                "validation_mode": "rough",
                "draft_model": draft_model,
                "validator_model": validator_model,
                "escalated": True,
                "attempt": 0,
                "validation_points_count": len(deterministic_points),
                "unsupported_points_count": len(unsupported_points),
            },
        )
        return SalesVerificationOutcome(
            answer=answer,
            passed=False,
            verdict="needs_revision",
            attempts=0,
            message="Escalating a detected product detail to the evidence verifier.",
            validation_points=deterministic_points,
        )
    _emit(
        on_event,
        {
            "type": "validation_result",
            "verdict": "pass",
            "message": "No Bluebot product claims requiring evidence were detected.",
            "next_action": "send_answer",
            "validation_mode": "rough",
            "draft_model": draft_model,
            "validator_model": validator_model,
            "escalated": False,
            "attempt": 0,
            "validation_points_count": 0,
        },
    )
    return SalesVerificationOutcome(
        answer=answer,
        passed=True,
        verdict="pass",
        attempts=0,
        message="No Bluebot product claims requiring evidence were detected.",
    )


def verify_sales_response(
    draft_answer: str,
    messages: list[dict],
    *,
    verifier_provider: LLMProvider,
    verifier_model: str,
    draft_model: str | None = None,
    validation_mode: str = "strong",
    escalated: bool = False,
    max_attempts: int | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> SalesVerificationOutcome:
    """Verify and, if needed, rewrite a final sales answer before display."""
    attempts = max_attempts or sales_response_verification_attempts()
    answer = (draft_answer or "").strip() or "(No response)"
    last_message = "Answer verification did not run."
    last_attempt = 0
    validation_points: list[dict[str, str]] = []

    for attempt in range(1, attempts + 1):
        last_attempt = attempt
        deterministic_points = validate_sales_answer_points(answer)
        _emit(
            on_event,
            {
                "type": "validation_start",
                "message": "Checking key product claims against Bluebot public website knowledge.",
                "validation_mode": validation_mode,
                "draft_model": draft_model,
                "validator_model": verifier_model,
                "escalated": escalated,
                "attempt": attempt,
                "validation_points_count": len(deterministic_points),
            },
        )
        payload = _run_verifier(
            answer,
            messages,
            verifier_provider=verifier_provider,
            verifier_model=verifier_model,
            deterministic_points=deterministic_points,
        )
        model_points = _normalize_validation_points(payload.get("validation_points"))
        validation_points = _merge_validation_points(deterministic_points, model_points)
        unsupported_points = _unsupported_points(validation_points)
        passed = _truthy_passed(payload.get("passed")) and not unsupported_points
        verdict = str(payload.get("verdict") or ("pass" if passed else "needs_revision"))
        if unsupported_points and verdict == "pass":
            verdict = "needs_revision"
        corrected = str(payload.get("corrected_answer") or "").strip()
        last_message = _customer_safe_message(payload, passed=passed, verdict=verdict)

        if passed:
            _emit(
                on_event,
                {
                    "type": "validation_result",
                    "verdict": "pass",
                    "message": "Verified against Bluebot public website knowledge.",
                    "next_action": "send_answer",
                    "validation_mode": validation_mode,
                    "draft_model": draft_model,
                    "validator_model": verifier_model,
                    "escalated": escalated,
                    "attempt": attempt,
                    "validation_points_count": len(validation_points),
                },
            )
            return SalesVerificationOutcome(
                answer=answer,
                passed=True,
                verdict="pass",
                attempts=attempt,
                message="Verified against Bluebot public website knowledge.",
                validation_points=validation_points,
            )

        if not corrected:
            corrected = _evidence_backed_answer(messages, answer, validation_points)

        next_action = (
            "revise_answer"
            if corrected and corrected != answer and attempt < attempts
            else "send_evidence_backed_answer"
        )
        _emit(
            on_event,
            {
                "type": "validation_result",
                "verdict": verdict,
                "message": (
                    "Revising an unsupported product detail before sending."
                    if next_action == "revise_answer"
                    else last_message
                ),
                "next_action": next_action,
                "validation_mode": validation_mode,
                "draft_model": draft_model,
                "validator_model": verifier_model,
                "escalated": escalated,
                "attempt": attempt,
                "validation_points_count": len(validation_points),
                "unsupported_points_count": len(unsupported_points),
            },
        )
        if corrected and corrected != answer and attempt < attempts:
            answer = corrected
            continue
        if corrected:
            return SalesVerificationOutcome(
                answer=corrected,
                passed=False,
                verdict=verdict,
                attempts=attempt,
                message=last_message,
                validation_points=validation_points,
            )
        break

    fallback = _evidence_backed_answer(messages, answer, validation_points)
    _emit(
        on_event,
        {
            "type": "validation_result",
            "verdict": "needs_more_evidence",
            "message": "Using the strongest answer supported by Bluebot public website knowledge.",
            "next_action": "send_evidence_backed_answer",
            "validation_mode": validation_mode,
            "draft_model": draft_model,
            "validator_model": verifier_model,
            "escalated": escalated,
            "attempt": last_attempt or attempts,
            "validation_points_count": len(validation_points),
            "unsupported_points_count": len(_unsupported_points(validation_points)),
        },
    )
    return SalesVerificationOutcome(
        answer=fallback,
        passed=False,
        verdict="needs_more_evidence",
        attempts=last_attempt or attempts,
        message=last_message,
        validation_points=validation_points,
    )


def _run_verifier(
    answer: str,
    messages: list[dict],
    *,
    verifier_provider: LLMProvider,
    verifier_model: str,
    deterministic_points: list[dict[str, str]],
) -> dict[str, Any]:
    query = f"{_last_user_text(messages)}\n\n{answer}".strip()
    evidence = sales_reference_context(query)
    prompt = "\n\n".join(
        [
            "Recent customer conversation:",
            _conversation_excerpt(messages),
            "Draft answer to verify:",
            answer,
            "Deterministic validation points from structured Bluebot catalog:",
            _validation_points_text(deterministic_points),
            "Evidence from scraped/reviewed Bluebot public content:",
            evidence,
        ]
    )
    response = verifier_provider.complete(
        verifier_model,
        [{"role": "user", "content": prompt}],
        system=_VERIFIER_SYSTEM,
        tools=[],
        max_tokens=1_600,
    )
    parsed = _parse_json_object(response.text)
    if not parsed:
        return {
            "passed": False,
            "verdict": "needs_revision",
            "message": "Verifier returned an unreadable result.",
            "validation_points": [],
            "issues": ["unreadable_verifier_output"],
            "corrected_answer": "",
        }
    return parsed


def _strong_validation_reason(
    answer: str,
    messages: list[dict],
    deterministic_points: list[dict[str, str]],
) -> str:
    if deterministic_points:
        return "deterministic_product_claim"
    if _mentions_specific_product(answer):
        return "product_name_claim"
    if _PRICE_PACKAGE_RE.search(answer or ""):
        return "pricing_or_package_claim"
    if _has_product_recommendation_claim(answer):
        return "product_recommendation_claim"
    if _has_capability_claim(answer):
        return "capability_claim"
    if _used_evidence_tool_this_turn(messages) and not _is_neutral_followup(answer):
        return "evidence_tool_result"
    return ""


def _mentions_specific_product(answer: str) -> bool:
    text = (answer or "").lower()
    if re.search(r"\b(prolink|flagship|bluebot\s+(?:mini|prime))\b", text):
        return True
    for product in sales_catalog_records():
        name = str(product.get("name") or "").strip().lower()
        if name and len(name) > len("bluebot") and name in text:
            return True
    return False


def _has_product_recommendation_claim(answer: str) -> bool:
    text = answer or ""
    return bool(
        re.search(r"\b(recommend|best fit|good fit|right product|product line)\b", text, re.I)
        and re.search(r"\b(bluebot|product|meter|device|prime|prolink|flagship|mini)\b", text, re.I)
    )


def _has_capability_claim(answer: str) -> bool:
    text = answer or ""
    if _is_general_help_answer(text):
        return False
    return bool(_CAPABILITY_TERM_RE.search(text) and _CLAIM_VERB_RE.search(text))


def _is_general_help_answer(answer: str) -> bool:
    text = answer or ""
    if validate_sales_answer_points(text) or _mentions_specific_product(text):
        return False
    if _PRICE_PACKAGE_RE.search(text):
        return False
    if not _GENERAL_HELP_RE.search(text):
        return False
    fragments = _sentence_fragments(text)
    return bool(fragments) and all(_is_general_or_clarifying_fragment(part) for part in fragments)


def _is_neutral_followup(answer: str) -> bool:
    text = _squash(answer or "", limit=500)
    if not text:
        return True
    if validate_sales_answer_points(text) or _mentions_specific_product(text):
        return False
    if _PRICE_PACKAGE_RE.search(text):
        return False
    if _is_general_help_answer(text):
        return True
    if len(text) <= 360 and re.search(
        r"\b(thanks|thank you|got it|understood|what|which|could you|can you|"
        r"please share|tell me|do you know|is it|are you)\b",
        text,
        re.IGNORECASE,
    ):
        return not _has_capability_claim(text)
    return False


def _sentence_fragments(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[.!?;\n]+", text or "") if part.strip()]


def _is_general_or_clarifying_fragment(fragment: str) -> bool:
    if _PRICE_PACKAGE_RE.search(fragment):
        return False
    if _PRODUCT_CLAIM_VERB_RE.search(fragment) and _CAPABILITY_TERM_RE.search(fragment):
        return False
    return bool(_GENERAL_FRAGMENT_RE.search(fragment) or _CLARIFYING_FRAGMENT_RE.search(fragment))


def _used_evidence_tool_this_turn(messages: list[dict]) -> bool:
    start = _last_plain_customer_message_index(messages)
    scan = messages[start + 1:] if start >= 0 else messages
    for msg in scan:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                if str(block.get("name") or "") in _EVIDENCE_TOOL_NAMES:
                    return True
    return False


def _last_plain_customer_message_index(messages: list[dict]) -> int:
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if isinstance(msg, dict) and msg.get("role") == "user" and isinstance(msg.get("content"), str):
            return idx
    return -1


def validate_sales_answer_points(answer: str) -> list[dict[str, str]]:
    """Validate deterministic catalog-backed points in a draft sales answer."""
    text = answer or ""
    points: list[dict[str, str]] = []
    consumed_spans: list[tuple[int, int]] = []

    for match in _range_claim_matches(text):
        min_size = match.get("min_size")
        max_size = match.get("max_size")
        claim = match["claim"]
        consumed_spans.append(match["span"])
        points.append(_pipe_range_point(claim, min_size=min_size, max_size=max_size))

    for match in _explicit_pipe_size_matches(text, consumed_spans):
        size = match["size"]
        points.append(_pipe_size_point(float(size), claim=match["claim"]))

    return points


def _range_claim_matches(text: str) -> list[dict[str, Any]]:
    number = r"\d+(?:\.\d+)?"
    unit = r"(?:inch(?:es)?|in\b|[\"”])"
    patterns = [
        re.compile(
            rf"(?P<min>{number})\s*\+\s*(?:{unit})?\s*(?:to|through|-|–)\s*"
            rf"(?P<max>{number})\s*\+?\s*{unit}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?:up to|all the way up to|as large as|through)\s*"
            rf"(?P<max>{number})\s*\+?\s*{unit}",
            re.IGNORECASE,
        ),
        re.compile(
            rf"\b(?P<min>{number})\s*\+\s*{unit}",
            re.IGNORECASE,
        ),
    ]
    matches: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            span = match.span()
            if any(_spans_overlap(span, existing) for existing in spans):
                continue
            spans.append(span)
            min_raw = match.groupdict().get("min")
            max_raw = match.groupdict().get("max")
            matches.append(
                {
                    "claim": _squash(match.group(0), limit=180),
                    "min_size": float(min_raw) if min_raw else None,
                    "max_size": float(max_raw) if max_raw else None,
                    "span": span,
                }
            )
    return matches


def _explicit_pipe_size_matches(
    text: str,
    consumed_spans: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    num = r"(?:\d+\s*/\s*\d+|\d+(?:\.\d+)?)"
    size_list = re.compile(
        rf"(?P<nums>{num}(?:\s*,\s*{num})*(?:\s*,?\s*(?:and|or)\s*{num})?)\s*"
        r"(?:inch(?:es)?|in\b|[\"”])",
        re.IGNORECASE,
    )
    out: list[dict[str, Any]] = []
    seen: set[float] = set()
    for match in size_list.finditer(text):
        span = match.span()
        if any(_spans_overlap(span, existing) for existing in consumed_spans):
            continue
        prefix = text[max(0, span[0] - 16):span[0]].lower()
        if re.search(r"(up to|through|as large as)$", prefix.strip()):
            continue
        values = [_parse_size_number(raw) for raw in re.findall(num, match.group("nums"))]
        for value in values:
            if value is None or value in seen:
                continue
            seen.add(value)
            out.append(
                {
                    "claim": f"{_format_claim_size(value)} pipe support",
                    "size": value,
                    "span": span,
                }
            )
    return out


def _pipe_size_point(size: float, *, claim: str) -> dict[str, str]:
    products = _products_supporting_size(size)
    if products:
        ranges = ", ".join(_product_range_label(product) for product in products[:3])
        return _validation_point(
            claim=claim,
            category="pipe_size",
            status="supported",
            evidence=f"Website-listed catalog includes {ranges}.",
        )
    return _validation_point(
        claim=claim,
        category="pipe_size",
        status="unsupported",
        evidence=(
            f"The structured public catalog does not include a product range covering "
            f"{_format_claim_size(size)}."
        ),
        correction=_catalog_supported_pipe_answer(include_unsupported_note=True),
    )


def _pipe_range_point(
    claim: str,
    *,
    min_size: float | None,
    max_size: float | None,
) -> dict[str, str]:
    catalog_min, catalog_max = _catalog_min_max()
    if max_size is None:
        return _validation_point(
            claim=claim,
            category="pipe_size",
            status="unsupported",
            evidence=(
                "The public catalog lists bounded product ranges only; it does not "
                "confirm open-ended pipe-size support."
            ),
            correction=_catalog_supported_pipe_answer(include_unsupported_note=True),
        )
    if max_size > catalog_max or (min_size is not None and min_size < catalog_min):
        return _validation_point(
            claim=claim,
            category="pipe_size",
            status="unsupported",
            evidence=(
                f"The largest pipe size in the structured public catalog is "
                f"{_format_catalog_size(catalog_max)}."
            ),
            correction=_catalog_supported_pipe_answer(include_unsupported_note=True),
        )
    return _validation_point(
        claim=claim,
        category="pipe_size",
        status="supported",
        evidence=(
            "The claimed pipe-size range stays within the structured public catalog "
            f"bounds of {_format_catalog_size(catalog_min)} to {_format_catalog_size(catalog_max)}."
        ),
    )


def _validation_point(
    *,
    claim: str,
    category: str,
    status: str,
    evidence: str,
    correction: str = "",
) -> dict[str, str]:
    return {
        "claim": claim,
        "category": category,
        "status": status,
        "evidence": evidence,
        "correction": correction,
    }


def _normalize_validation_points(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in {"supported", "unsupported", "needs_more_evidence"}:
            status = "needs_more_evidence"
        claim = str(item.get("claim") or "").strip()
        if not claim:
            continue
        out.append(
            _validation_point(
                claim=claim,
                category=str(item.get("category") or "other").strip() or "other",
                status=status,
                evidence=str(item.get("evidence") or "").strip(),
                correction=str(item.get("correction") or "").strip(),
            )
        )
    return out


def _merge_validation_points(
    deterministic_points: list[dict[str, str]],
    model_points: list[dict[str, str]],
) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for point in [*deterministic_points, *model_points]:
        key = (
            re.sub(r"\s+", " ", point.get("claim", "").lower()).strip(),
            point.get("category", ""),
        )
        if not key[0] or key in seen:
            continue
        seen.add(key)
        merged.append(point)
    return merged


def _unsupported_points(points: list[dict[str, str]]) -> list[dict[str, str]]:
    return [point for point in points if point.get("status") != "supported"]


def _validation_points_text(points: list[dict[str, str]]) -> str:
    if not points:
        return "No deterministic catalog-backed points were detected in this draft."
    return json.dumps(points, ensure_ascii=False, indent=2)


def _evidence_backed_answer(
    messages: list[dict],
    answer: str,
    validation_points: list[dict[str, str]],
) -> str:
    corrections = [
        point.get("correction", "").strip()
        for point in validation_points
        if point.get("correction", "").strip()
    ]
    if corrections:
        return corrections[0]

    combined = f"{_last_user_text(messages)}\n{answer}".lower()
    if re.search(r"\b(pipe|pipes|inch|inches|large|size|device|meter|product|prime|prolink)\b", combined):
        return _catalog_supported_pipe_answer(include_unsupported_note=False)

    return (
        "I can help with Bluebot product fit, pipe compatibility, installation impact, "
        "or a sales review. I do not have enough Bluebot public evidence to answer that "
        "specific detail confidently."
    )


def _catalog_supported_pipe_answer(*, include_unsupported_note: bool) -> str:
    large_products = _large_pipe_products()
    large_names = {str(product.get("name") or "") for product in large_products}
    wifi_name = "Bluebot Prime Wi-Fi" if "Bluebot Prime Wi-Fi" in large_names else "Bluebot Prime"
    no_wifi_name = (
        "Bluebot ProLink Prime"
        if "Bluebot ProLink Prime" in large_names
        else "Bluebot ProLink"
    )
    unsupported = (
        " The current public catalog does not confirm 24 inch pipe support."
        if include_unsupported_note
        else ""
    )
    return (
        "Bluebot public materials list clamp-on options for common 3/4 inch to "
        "2 inch pipes, plus larger-pipe options for 2.5, 3.0, and 4.0 inch pipes. "
        f"For larger listed sizes, {wifi_name} fits Wi-Fi locations and {no_wifi_name} "
        "fits long-range or no-Wi-Fi needs."
        f"{unsupported} Confirm pipe OD/material, access, package availability, and network "
        "conditions with sales before quoting."
    )


def _products_supporting_size(size: float) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for product in sales_catalog_records():
        try:
            min_size = float(product.get("pipe_size_min_in"))
            max_size = float(product.get("pipe_size_max_in"))
        except (TypeError, ValueError):
            continue
        if min_size <= size <= max_size:
            products.append(product)
    return products


def _large_pipe_products() -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for product in sales_catalog_records():
        try:
            min_size = float(product.get("pipe_size_min_in"))
            max_size = float(product.get("pipe_size_max_in"))
        except (TypeError, ValueError):
            continue
        if min_size >= 2.5 or max_size > 2.0:
            products.append(product)
    return products


def _catalog_min_max() -> tuple[float, float]:
    mins: list[float] = []
    maxes: list[float] = []
    for product in sales_catalog_records():
        try:
            mins.append(float(product.get("pipe_size_min_in")))
            maxes.append(float(product.get("pipe_size_max_in")))
        except (TypeError, ValueError):
            continue
    return (min(mins or [0.75]), max(maxes or [4.0]))


def _product_range_label(product: dict[str, Any]) -> str:
    return (
        f"{product.get('name') or 'Bluebot product'} "
        f"({_format_catalog_size(float(product.get('pipe_size_min_in')))} to "
        f"{_format_catalog_size(float(product.get('pipe_size_max_in')))})"
    )


def _parse_size_number(raw: str) -> float | None:
    text = re.sub(r"\s+", "", raw or "")
    if "/" in text:
        num, den = text.split("/", 1)
        try:
            return float(num) / float(den)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _format_claim_size(size: float) -> str:
    if abs(size - 0.75) < 0.001:
        return "3/4 inch"
    if float(size).is_integer():
        return f"{int(size)} inch"
    return f"{size:g} inch"


def _format_catalog_size(size: float) -> str:
    if abs(size - 0.75) < 0.001:
        return "3/4 inch"
    if size in {3.0, 4.0}:
        return f"{size:.1f} inch"
    if float(size).is_integer():
        return f"{int(size)} inch"
    return f"{size:g} inch"


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _conversation_excerpt(messages: list[dict], *, limit: int = 8) -> str:
    rows: list[str] = []
    for msg in messages[-limit:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        text = _message_text(msg)
        if not text:
            continue
        label = "Customer" if role == "user" else "Assistant"
        rows.append(f"{label}: {text}")
    return "\n".join(rows) or "No prior customer-visible text."


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = _message_text(msg)
            if text:
                return text
    return ""


def _message_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return _squash(content)
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            # Tool results are evidence for the draft model, but not customer-visible
            # conversation text; the verifier gets reviewed KB evidence separately.
        return _squash(" ".join(parts))
    return ""


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    candidates = [raw]
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _customer_safe_message(payload: dict[str, Any], *, passed: bool, verdict: str) -> str:
    raw = str(payload.get("message") or "").strip()
    if raw:
        return raw[:240]
    if passed:
        return "Verified against Bluebot public website knowledge."
    if verdict == "needs_more_evidence":
        return "The draft needed more public Bluebot evidence."
    return "Found an unsupported detail, revising before sending."


def _truthy_passed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _squash(text: str, *, limit: int = 1_500) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) <= limit:
        return clean
    return f"{clean[:limit].rsplit(' ', 1)[0]}..."


def _emit(callback: Callable[[dict[str, Any]], None] | None, event: dict[str, Any]) -> None:
    if callback:
        callback(event)
