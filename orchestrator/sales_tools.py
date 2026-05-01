"""Sales-only tools for the public pre-login bluebot sales agent."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import store

_KB_PATH = Path(__file__).resolve().parent / "sales_kb" / "articles.json"
_CATALOG_PATH = Path(__file__).resolve().parent / "sales_kb" / "product_catalog.json"


SALES_TOOL_NAMES = frozenset(
    {
        "search_sales_kb",
        "qualify_meter_use_case",
        "assess_pipe_fit",
        "explain_installation_impact",
        "capture_lead_summary",
        "recommend_product_line",
    }
)


TOOL_DEFINITIONS = [
    {
        "name": "search_sales_kb",
        "description": (
            "Search reviewed bluebot sales knowledge about product fit, pipe compatibility, "
            "installation, pipe impact, network/power, and qualification questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "qualify_meter_use_case",
        "description": (
            "Evaluate how complete a buyer's flow-meter qualification details are and list "
            "the highest-value missing questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "application": {"type": "string"},
                "industry": {"type": "string"},
                "pipe_material": {"type": "string"},
                "pipe_size": {"type": "string"},
                "liquid": {"type": "string"},
                "expected_flow_range": {"type": "string"},
                "pipe_access": {"type": "string"},
                "installation_environment": {"type": "string"},
                "network_or_power": {"type": "string"},
                "reporting_goals": {"type": "string"},
                "timeline": {"type": "string"},
                "buyer_role": {"type": "string"},
            },
        },
    },
    {
        "name": "assess_pipe_fit",
        "description": (
            "Run a preliminary non-binding pipe-fit screen for clamp-on ultrasonic monitoring."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipe_material": {"type": "string"},
                "pipe_size": {"type": "string"},
                "liquid": {"type": "string"},
                "pipe_access": {"type": "string"},
                "pipe_condition": {"type": "string"},
                "installation_environment": {"type": "string"},
            },
        },
    },
    {
        "name": "explain_installation_impact",
        "description": (
            "Explain how clamp-on ultrasonic installation affects the pipe, water, pressure, "
            "and operations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "concern": {"type": "string"},
                "pipe_material": {"type": "string"},
            },
        },
    },
    {
        "name": "capture_lead_summary",
        "description": (
            "Persist newly learned sales qualification fields for the compact lead summary UI."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "object",
                    "properties": {
                        "application": {"type": "string"},
                        "industry": {"type": "string"},
                        "site_count": {"type": "string"},
                        "pipe_material": {"type": "string"},
                        "pipe_size": {"type": "string"},
                        "liquid": {"type": "string"},
                        "expected_flow_range": {"type": "string"},
                        "pipe_access": {"type": "string"},
                        "installation_environment": {"type": "string"},
                        "network_or_power": {"type": "string"},
                        "reporting_goals": {"type": "string"},
                        "timeline": {"type": "string"},
                        "buyer_role": {"type": "string"},
                        "contact": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                }
            },
            "required": ["summary"],
        },
    },
    {
        "name": "recommend_product_line",
        "description": (
            "Recommend website-listed bluebot product lines from structured sales catalog "
            "based on pipe size, Wi-Fi/network availability, environment, and application."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipe_size": {
                    "type": "string",
                    "description": "Nominal pipe size or outside diameter if known, e.g. 1 inch, 2.5 inch, 4\".",
                },
                "has_reliable_wifi": {
                    "type": "boolean",
                    "description": "Whether reliable Wi-Fi is available at the meter location.",
                },
                "needs_long_range": {
                    "type": "boolean",
                    "description": "Whether the buyer needs a no-Wi-Fi or long-range connection option.",
                },
                "installation_environment": {"type": "string"},
                "application": {"type": "string"},
            },
        },
    },
]


_QUALIFICATION_FIELDS = [
    "application",
    "industry",
    "pipe_material",
    "pipe_size",
    "liquid",
    "expected_flow_range",
    "pipe_access",
    "installation_environment",
    "network_or_power",
    "reporting_goals",
    "timeline",
    "buyer_role",
]


def _load_json_records(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _load_synced_records(record_type: str) -> list[dict[str, Any]]:
    try:
        return store.load_sales_content_records(record_type)
    except Exception:
        return []


def _merge_records(
    snapshot_records: list[dict[str, Any]],
    synced_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not synced_records:
        return snapshot_records

    records_by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for record in snapshot_records:
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            continue
        records_by_id[record_id] = dict(record)
        order.append(record_id)

    for record in synced_records:
        record_id = str(record.get("id") or "").strip()
        if not record_id:
            continue
        if record_id not in records_by_id:
            order.append(record_id)
        records_by_id[record_id] = {**records_by_id.get(record_id, {}), **record}

    return [records_by_id[record_id] for record_id in order if record_id in records_by_id]


def _load_articles() -> list[dict[str, Any]]:
    snapshots = _load_json_records(_KB_PATH)
    return _merge_records(snapshots, _load_synced_records("article"))


def _load_catalog() -> list[dict[str, Any]]:
    snapshots = _load_json_records(_CATALOG_PATH)
    return _merge_records(snapshots, _load_synced_records("product"))


def _link(label: str, url: str | None) -> dict[str, str] | None:
    clean = (url or "").strip()
    if not clean:
        return None
    return {"label": label, "url": clean}


def _article_links(*article_ids: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    wanted = set(article_ids)
    for article in _load_articles():
        if wanted and article.get("id") not in wanted:
            continue
        primary = _link(str(article.get("title") or "Bluebot details"), article.get("source_url"))
        candidates = [primary] if primary else []
        raw_supporting = article.get("supporting_links")
        if isinstance(raw_supporting, list):
            for item in raw_supporting:
                if not isinstance(item, dict):
                    continue
                candidates.append(_link(str(item.get("label") or "Bluebot details"), item.get("url")))
        for candidate in candidates:
            if not candidate:
                continue
            url = candidate["url"]
            if url in seen:
                continue
            seen.add(url)
            links.append(candidate)
    return links


def _catalog_links(product_ids: list[str] | None = None) -> list[dict[str, str]]:
    wanted = set(product_ids or [])
    order = {pid: ix for ix, pid in enumerate(product_ids or [])}
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    products = _load_catalog()
    if wanted:
        products.sort(key=lambda p: order.get(str(p.get("id") or ""), len(order)))
    for product in products:
        if wanted and product.get("id") not in wanted:
            continue
        candidate = _link(str(product.get("name") or "Bluebot product"), product.get("source_url"))
        if not candidate or candidate["url"] in seen:
            continue
        seen.add(candidate["url"])
        links.append(candidate)
    return links


def _terms(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(t) > 2
    }


def _pipe_size_inches(value: str | None) -> float | None:
    s = (value or "").lower().strip()
    if not s:
        return None
    mixed = re.search(r"(\d+)\s+(\d+)\s*/\s*(\d+)", s)
    if mixed:
        whole, num, den = mixed.groups()
        try:
            return float(whole) + float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    frac = re.search(r"(\d+)\s*/\s*(\d+)", s)
    if frac:
        num, den = frac.groups()
        try:
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            return None
    num = re.search(r"\d+(?:\.\d+)?", s)
    if not num:
        return None
    try:
        return float(num.group(0))
    except ValueError:
        return None


def search_sales_kb(query: str, max_results: int = 3) -> dict[str, Any]:
    """Return compact reviewed KB passages ranked by simple term overlap."""
    q_terms = _terms(query)
    scored: list[tuple[int, dict[str, str]]] = []
    for article in _load_articles():
        haystack = " ".join(
            str(article.get(k, "")) for k in ("title", "topic", "body")
        )
        score = len(q_terms & _terms(haystack))
        if score > 0:
            scored.append((score, article))
    if not scored:
        scored = [(0, a) for a in _load_articles()[: int(max_results or 3)]]
    scored.sort(key=lambda item: (-item[0], item[1].get("id", "")))
    limit = max(1, min(int(max_results or 3), 5))
    return {
        "success": True,
        "query": query,
        "results": [
            {
                "id": article.get("id"),
                "title": article.get("title"),
                "topic": article.get("topic"),
                "source_url": article.get("source_url"),
                "source_accessed": article.get("source_accessed"),
                "supporting_links": article.get("supporting_links") or [],
                "body": article.get("body"),
            }
            for _score, article in scored[:limit]
        ],
        "relevant_links": _article_links(
            *[
                str(article.get("id"))
                for _score, article in scored[:limit]
                if article.get("id")
            ]
        )[:5],
    }


def sales_reference_context(
    query: str,
    *,
    max_articles: int = 5,
    max_chars: int = 14_000,
) -> str:
    """Return compact scraped/reviewed Bluebot evidence for answer verification."""
    kb = search_sales_kb(query, max_results=max_articles)
    parts: list[str] = ["Reviewed/synced Bluebot sales KB evidence:"]
    for ix, article in enumerate(kb.get("results") or [], start=1):
        if not isinstance(article, dict):
            continue
        body = str(article.get("body") or "").strip()
        if len(body) > 1_200:
            body = f"{body[:1_200].rsplit(' ', 1)[0]}."
        links = []
        source_url = str(article.get("source_url") or "").strip()
        if source_url:
            links.append(source_url)
        for link in article.get("supporting_links") or []:
            if isinstance(link, dict) and str(link.get("url") or "").strip():
                links.append(str(link["url"]))
        parts.append(
            "\n".join(
                [
                    f"[KB {ix}] {article.get('title') or article.get('id')}",
                    f"Topic: {article.get('topic') or 'n/a'}",
                    f"Source: {source_url or 'n/a'}",
                    f"Source accessed: {article.get('source_accessed') or 'n/a'}",
                    f"Text: {body}",
                    f"Related URLs: {', '.join(dict.fromkeys(links[:5])) or 'n/a'}",
                ]
            )
        )

    parts.append("Structured website-listed product catalog:")
    for product in _load_catalog():
        links = [str(product.get("source_url") or "").strip()]
        fit_notes = "; ".join(str(item) for item in (product.get("fit_notes") or []) if item)
        cautions = "; ".join(str(item) for item in (product.get("cautions") or []) if item)
        parts.append(
            "\n".join(
                [
                    f"[Product] {product.get('name') or product.get('id')}",
                    f"Source: {links[0] or 'n/a'}",
                    (
                        "Pipe size range inches: "
                        f"{product.get('pipe_size_min_in')} to {product.get('pipe_size_max_in')}"
                    ),
                    f"Connectivity: {', '.join(product.get('connectivity') or []) or 'n/a'}",
                    f"Positioning: {product.get('positioning') or 'n/a'}",
                    f"Fit notes: {fit_notes or 'n/a'}",
                    f"Cautions: {cautions or 'n/a'}",
                ]
            )
        )

    context = "\n\n".join(parts)
    if len(context) <= max_chars:
        return context
    return context[:max_chars].rsplit("\n", 1)[0].strip()


def sales_catalog_records() -> list[dict[str, Any]]:
    """Return website-listed product catalog records for deterministic validation."""
    return [dict(product) for product in _load_catalog()]


def qualify_meter_use_case(**fields: Any) -> dict[str, Any]:
    """Score qualification completeness and suggest the next discovery questions."""
    present = {
        k: v
        for k, v in fields.items()
        if k in _QUALIFICATION_FIELDS and str(v or "").strip()
    }
    missing = [k for k in _QUALIFICATION_FIELDS if k not in present]
    score = round(len(present) / len(_QUALIFICATION_FIELDS), 2)
    next_questions = [
        _question_for_field(k)
        for k in missing[:3]
    ]
    if score >= 0.75:
        stage = "well_qualified"
    elif score >= 0.4:
        stage = "partially_qualified"
    else:
        stage = "early_discovery"
    return {
        "success": True,
        "stage": stage,
        "completion_score": score,
        "known_fields": present,
        "missing_fields": missing,
        "next_questions": next_questions,
    }


def _question_for_field(field: str) -> str:
    return {
        "application": "What are you trying to monitor or improve?",
        "industry": "What industry or site type is this for?",
        "pipe_material": "What is the pipe material?",
        "pipe_size": "What is the pipe size or outside diameter?",
        "liquid": "Is the line mostly water, or another liquid?",
        "expected_flow_range": "What flow range do you expect during normal operation?",
        "pipe_access": "Can someone access the outside of the pipe where the sensor would mount?",
        "installation_environment": "Is the installation indoors, outdoors, buried, or in a mechanical room?",
        "network_or_power": "Do you have Wi-Fi/power nearby, or do you need a lower-power remote option?",
        "reporting_goals": "Do you need alerts, dashboards, compliance reporting, or operational trending?",
        "timeline": "What timeline are you working toward?",
        "buyer_role": "Are you evaluating, specifying, installing, or purchasing?",
    }.get(field, f"What should we know about {field.replace('_', ' ')}?")


def assess_pipe_fit(
    pipe_material: str | None = None,
    pipe_size: str | None = None,
    liquid: str | None = None,
    pipe_access: str | None = None,
    pipe_condition: str | None = None,
    installation_environment: str | None = None,
) -> dict[str, Any]:
    """Preliminary screen for obvious fit blockers and unknowns."""
    blockers: list[str] = []
    cautions: list[str] = []
    unknowns: list[str] = []

    access = (pipe_access or "").lower()
    condition = (pipe_condition or "").lower()
    env = (installation_environment or "").lower()
    liquid_l = (liquid or "").lower()

    if not pipe_material:
        unknowns.append("pipe material")
    if not pipe_size:
        unknowns.append("pipe size or outside diameter")
    if not liquid:
        unknowns.append("liquid type")
    if not pipe_access:
        unknowns.append("external pipe access")

    if any(word in access for word in ("buried", "inaccessible", "no access", "cannot access")):
        blockers.append("The sensor needs physical access to the outside of the pipe.")
    if "buried" in env:
        blockers.append("Buried pipe usually needs site review unless an accessible section exists.")
    if any(word in condition for word in ("heavy corrosion", "severe corrosion", "badly scaled")):
        cautions.append("Severe corrosion or scale can make ultrasonic signal quality harder to validate.")
    if any(word in liquid_l for word in ("slurry", "solids", "air", "bubbles", "unknown")):
        cautions.append("Solids, bubbles, or unknown acoustic properties need review before promising fit.")

    if blockers:
        status = "needs_site_review"
    elif unknowns or cautions:
        status = "needs_more_information"
    else:
        status = "likely_fit_for_review"

    return {
        "success": True,
        "fit_status": status,
        "blockers": blockers,
        "cautions": cautions,
        "unknowns": unknowns,
        "next_steps": [
            "Confirm pipe material and size.",
            "Confirm the liquid is mostly water and the pipe normally runs full.",
            "Identify an accessible straight pipe section for mounting.",
        ],
        "relevant_links": _article_links("pipe-fit", "installation")[:4],
    }


def explain_installation_impact(
    concern: str | None = None,
    pipe_material: str | None = None,
) -> dict[str, Any]:
    """Return reviewed pipe-impact guidance for customer-facing answers."""
    return {
        "success": True,
        "concern": concern,
        "pipe_material": pipe_material,
        "impact_summary": (
            "Clamp-on ultrasonic monitoring is non-invasive: the transducers mount on the "
            "outside of a suitable pipe, so normal installation does not cut the pipe, add "
            "wetted parts, create a leak path, restrict flow, or introduce pressure drop."
        ),
        "important_conditions": [
            "The pipe must be accessible from the outside at the mounting location.",
            "The pipe and liquid must support a usable ultrasonic signal.",
            "Surface condition, pipe fullness, bubbles, solids, and nearby disturbances can affect fit.",
        ],
        "relevant_links": _article_links("pipe-impact", "clamp-on-overview")[:4],
    }


def recommend_product_line(
    pipe_size: str | None = None,
    has_reliable_wifi: bool | None = None,
    needs_long_range: bool | None = None,
    installation_environment: str | None = None,
    application: str | None = None,
) -> dict[str, Any]:
    """Recommend website-listed product lines using deterministic catalog rules."""
    size_in = _pipe_size_inches(pipe_size)
    unknowns: list[str] = []
    if size_in is None:
        unknowns.append("pipe_size")
    if has_reliable_wifi is None and needs_long_range is None:
        unknowns.append("wifi_or_long_range_need")

    preferred_connectivity: str | None = None
    if needs_long_range is True or has_reliable_wifi is False:
        preferred_connectivity = "no_wifi_required"
    elif has_reliable_wifi is True and needs_long_range is not True:
        preferred_connectivity = "wifi"

    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    for product in _load_catalog():
        reasons: list[str] = []
        score = 0
        min_size = float(product.get("pipe_size_min_in") or 0)
        max_size = float(product.get("pipe_size_max_in") or 999)
        if size_in is not None:
            if min_size <= size_in <= max_size:
                score += 5
                reasons.append(f"Pipe size {pipe_size} falls within its listed range.")
            else:
                score -= 5
        connectivity = product.get("connectivity") or []
        if preferred_connectivity:
            if preferred_connectivity in connectivity:
                score += 4
                if preferred_connectivity == "wifi":
                    reasons.append("Reliable Wi-Fi is available at the meter location.")
                else:
                    reasons.append("No-Wi-Fi / long-range connectivity is requested.")
            else:
                score -= 3
        env_terms = _terms(str(installation_environment or ""))
        product_env = set(str(v).lower() for v in (product.get("environment") or []))
        if env_terms & product_env:
            score += 1
        app_terms = _terms(str(application or ""))
        if app_terms & _terms(" ".join(product.get("fit_notes") or [])):
            score += 1
        if score > 0:
            scored.append((score, product, reasons))

    scored.sort(key=lambda item: (-item[0], item[1].get("name", "")))
    recommendations = [
        {
            "id": product.get("id"),
            "name": product.get("name"),
            "line": product.get("line"),
            "positioning": product.get("positioning"),
            "pipe_size_range_in": [
                product.get("pipe_size_min_in"),
                product.get("pipe_size_max_in"),
            ],
            "connectivity": product.get("connectivity") or [],
            "source_url": product.get("source_url"),
            "source_accessed": product.get("source_accessed"),
            "reasons": reasons or product.get("fit_notes") or [],
            "cautions": product.get("cautions") or [],
        }
        for _score, product, reasons in scored[:3]
    ]
    recommended_ids = [
        str(product.get("id"))
        for _score, product, _reasons in scored[:3]
        if product.get("id")
    ]
    if recommendations and not unknowns:
        confidence = "medium"
    elif recommendations:
        confidence = "low"
    else:
        confidence = "insufficient_information"
    return {
        "success": True,
        "confidence": confidence,
        "input_interpreted": {
            "pipe_size_inches": size_in,
            "has_reliable_wifi": has_reliable_wifi,
            "needs_long_range": needs_long_range,
            "installation_environment": installation_environment,
            "application": application,
        },
        "unknowns": unknowns,
        "recommendations": recommendations,
        "relevant_links": [
            *_catalog_links(recommended_ids),
            *_article_links("product-fit", "network-power"),
        ][:5],
        "disclaimer": (
            "This is a preliminary website-catalog recommendation. Confirm current product "
            "availability, pricing, data plan, pipe OD/material, site access, and network "
            "conditions before quoting."
        ),
    }


def capture_lead_summary(
    conversation_id: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Persist a structured lead summary and return completion metadata."""
    merged = store.update_sales_lead_summary(conversation_id, summary or {})
    qualification = qualify_meter_use_case(**merged)
    return {
        "success": True,
        "lead_summary": merged,
        "completion_score": qualification["completion_score"],
        "missing_fields": qualification["missing_fields"],
        "next_questions": qualification["next_questions"],
    }


def dispatch_sales_tool(
    name: str,
    tool_input: dict[str, Any],
    *,
    conversation_id: str,
) -> dict[str, Any]:
    """Execute a sales-only tool by name."""
    return _SALES_REGISTRY.dispatch(name, tool_input, conversation_id=conversation_id)


# ---- Tool Registry ----

try:
    from tool_registry import Tool, ToolRegistry
except ImportError:  # pragma: no cover - supports package-style imports.
    from .tool_registry import Tool, ToolRegistry

_SALES_REGISTRY = ToolRegistry()

_SALES_REGISTRY.register(Tool(
    definition=TOOL_DEFINITIONS[0],  # search_sales_kb
    handler=search_sales_kb,
    context_params=frozenset(),
))

_SALES_REGISTRY.register(Tool(
    definition=TOOL_DEFINITIONS[1],  # qualify_meter_use_case
    handler=qualify_meter_use_case,
    context_params=frozenset(),
))

_SALES_REGISTRY.register(Tool(
    definition=TOOL_DEFINITIONS[2],  # assess_pipe_fit
    handler=assess_pipe_fit,
    context_params=frozenset(),
))

_SALES_REGISTRY.register(Tool(
    definition=TOOL_DEFINITIONS[3],  # explain_installation_impact
    handler=explain_installation_impact,
    context_params=frozenset(),
))

_SALES_REGISTRY.register(Tool(
    definition=TOOL_DEFINITIONS[4],  # capture_lead_summary
    handler=lambda summary, *, conversation_id: capture_lead_summary(conversation_id, summary),
    context_params=frozenset({"conversation_id"}),
))

_SALES_REGISTRY.register(Tool(
    definition=TOOL_DEFINITIONS[5],  # recommend_product_line
    handler=recommend_product_line,
    context_params=frozenset(),
))
