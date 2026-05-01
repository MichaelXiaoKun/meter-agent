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
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from llm.base import LLMProvider
    from llm.registry import MODEL_CATALOG, get_provider_name
    from sales_tools import sales_catalog_records, sales_reference_context
except ImportError:  # pragma: no cover - supports package-style imports.
    from ..llm.base import LLMProvider  # type: ignore
    from ..llm.registry import MODEL_CATALOG, get_provider_name  # type: ignore
    from .sales_tools import sales_catalog_records, sales_reference_context  # type: ignore


_DEFAULT_VERIFIER_MODEL = "claude-sonnet-4-6"
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


def sales_response_verification_enabled() -> bool:
    """Return whether public sales answers should be post-verified."""
    raw = (os.environ.get("SALES_RESPONSE_VERIFICATION") or "on").strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


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
        return requested
    if active_sales_model.startswith("claude-haiku"):
        return _DEFAULT_VERIFIER_MODEL
    if active_sales_model == "gpt-4o-mini":
        return "gpt-4o"
    if active_sales_model == "gemini-2.0-flash":
        return "gemini-2.5-pro"
    if active_sales_model == "gemini-2.5-flash":
        return "gemini-2.5-pro"
    return active_sales_model if active_sales_model in MODEL_CATALOG else _DEFAULT_VERIFIER_MODEL


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


def verify_sales_response(
    draft_answer: str,
    messages: list[dict],
    *,
    verifier_provider: LLMProvider,
    verifier_model: str,
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
                "validator_model": verifier_model,
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
