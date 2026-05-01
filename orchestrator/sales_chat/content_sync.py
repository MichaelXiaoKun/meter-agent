"""Sync public Bluebot website content into the sales-agent runtime KB.

The sales chat itself remains deterministic: it reads reviewed/runtime records
from the database via ``sales_tools`` and never browses live during a customer
turn. This module is the controlled refresh path for those records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse, urlunparse
from xml.etree import ElementTree

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
if str(_ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_ORCHESTRATOR_DIR))

try:  # Support both ``python -m orchestrator.sales_content_sync`` and tests.
    import store
except ModuleNotFoundError:  # pragma: no cover - exercised by package execution.
    from .. import store  # type: ignore


ALLOWED_DOMAINS = frozenset(
    {
        "www.bluebot.com",
        "support.bluebot.com",
        "help.bluebot.com",
    }
)
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_MAX_PAGES = 80
USER_AGENT = "BluebotSalesContentSync/1.0 (+https://www.bluebot.com)"

_ARTICLES_PATH = _ORCHESTRATOR_DIR / "sales_kb" / "articles.json"
_CATALOG_PATH = _ORCHESTRATOR_DIR / "sales_kb" / "product_catalog.json"
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_PRICE_LINE_RE = re.compile(
    r"(\$\s*\d|"
    r"\b(price|pricing|package|packages|subscription|data\s*plan|per\s*month|"
    r"monthly|annual|annually|checkout|cart|coupon|discount)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FetchedPage:
    """HTTP result used by the sync pipeline and fixture tests."""

    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str


@dataclass(frozen=True)
class ExtractedPage:
    """Readable text extracted from an HTML page."""

    url: str
    title: str
    text: str
    links: tuple[dict[str, str], ...]


def _load_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _canonical_url(url: str, base_url: str | None = None) -> str:
    joined = urljoin(base_url or "", (url or "").strip())
    parsed = urlparse(joined)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.netloc or "").lower()
    path = parsed.path or "/"
    return urlunparse((scheme, host, path, "", parsed.query, ""))


def _domain(url: str) -> str:
    return (urlparse(url).netloc or "").lower()


def is_allowed_bluebot_url(url: str) -> bool:
    """Return whether URL is fetchable by the sales content sync."""
    parsed = urlparse(_canonical_url(url))
    return parsed.scheme in {"http", "https"} and parsed.netloc.lower() in ALLOWED_DOMAINS


def _assert_allowed_url(url: str) -> str:
    canonical = _canonical_url(url)
    if not is_allowed_bluebot_url(canonical):
        raise ValueError(f"URL is outside the Bluebot sales-content allowlist: {url}")
    return canonical


def _looks_like_page(url: str) -> bool:
    path = urlparse(url).path.lower()
    blocked_suffixes = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".svg",
        ".ico",
        ".pdf",
        ".zip",
        ".xml",
        ".css",
        ".js",
    )
    return not path.endswith(blocked_suffixes)


def fetch_bluebot_url(
    url: str,
    *,
    client: Any | None = None,
    timeout: float = 20.0,
    max_redirects: int = 5,
) -> FetchedPage:
    """Fetch one allowlisted Bluebot URL without following off-domain redirects."""
    current_url = _assert_allowed_url(url)
    created_client = client is None
    if client is None:
        import httpx

        client = httpx.Client()
    try:
        for _ in range(max_redirects + 1):
            response = client.get(
                current_url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                follow_redirects=False,
            )
            status_code = int(getattr(response, "status_code", 0))
            headers = getattr(response, "headers", {}) or {}
            if status_code in _REDIRECT_STATUSES:
                location = headers.get("location") or headers.get("Location")
                if not location:
                    raise RuntimeError(f"Redirect from {current_url} had no Location header")
                next_url = _canonical_url(str(location), current_url)
                if not is_allowed_bluebot_url(next_url):
                    raise ValueError(f"Rejected off-domain redirect from {current_url} to {next_url}")
                current_url = next_url
                continue
            if status_code < 200 or status_code >= 300:
                raise RuntimeError(f"Fetch failed for {current_url}: HTTP {status_code}")
            final_url = _canonical_url(str(getattr(response, "url", current_url)))
            if not is_allowed_bluebot_url(final_url):
                raise ValueError(f"Rejected off-domain final URL: {final_url}")
            content_type = str(headers.get("content-type") or headers.get("Content-Type") or "")
            return FetchedPage(
                url=url,
                final_url=final_url,
                status_code=status_code,
                content_type=content_type,
                text=str(getattr(response, "text", "")),
            )
        raise RuntimeError(f"Too many redirects while fetching {url}")
    finally:
        if created_client and hasattr(client, "close"):
            client.close()


class _ReadableHTMLParser(HTMLParser):
    """Small dependency-free extractor for page title, text, and links."""

    _SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas"}
    _BREAK_TAGS = {
        "article",
        "aside",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "header",
        "li",
        "main",
        "nav",
        "p",
        "section",
        "td",
        "th",
        "tr",
    }

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._text_parts: list[str] = []
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_l = tag.lower()
        if tag_l in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag_l == "title":
            self._in_title = True
        if tag_l == "a":
            href = dict(attrs).get("href")
            if href:
                url = _canonical_url(href, self.base_url)
                if is_allowed_bluebot_url(url) and _looks_like_page(url):
                    self.links.append({"label": "", "url": url})
        if tag_l in self._BREAK_TAGS and self._skip_depth == 0:
            self._text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag_l == "title":
            self._in_title = False
        if tag_l in self._BREAK_TAGS and self._skip_depth == 0:
            self._text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        clean = re.sub(r"\s+", " ", data or "").strip()
        if not clean:
            return
        if self._in_title:
            self._title_parts.append(clean)
        self._text_parts.append(clean)

    @property
    def title(self) -> str:
        return _collapse_text(" ".join(self._title_parts))

    @property
    def text(self) -> str:
        lines = [_collapse_text(line) for line in " ".join(self._text_parts).splitlines()]
        return "\n".join(line for line in lines if line)


def _collapse_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def extract_readable_page(url: str, html: str) -> ExtractedPage:
    """Extract readable, redacted text from an HTML page."""
    parser = _ReadableHTMLParser(url)
    parser.feed(html or "")
    parser.close()
    clean_text = _redact_pricing_text(parser.text)
    title = parser.title or _title_from_url(url)
    seen: set[str] = set()
    links: list[dict[str, str]] = []
    for link in parser.links:
        link_url = link["url"]
        if link_url in seen:
            continue
        seen.add(link_url)
        links.append({"label": _title_from_url(link_url), "url": link_url})
    return ExtractedPage(
        url=_canonical_url(url),
        title=_collapse_text(title),
        text=clean_text,
        links=tuple(links[:12]),
    )


def _redact_pricing_text(text: str) -> str:
    """Remove pricing/package sentences from sales-answerable synced text."""
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    kept = []
    for chunk in chunks:
        clean = _collapse_text(chunk)
        if not clean:
            continue
        if _PRICE_LINE_RE.search(clean):
            continue
        kept.append(clean)
    return " ".join(kept)


def _title_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    if not path:
        return "Bluebot"
    leaf = path.split("/")[-1]
    return re.sub(r"[-_]+", " ", leaf).strip().title() or "Bluebot"


def _record_id_from_url(prefix: str, url: str) -> str:
    parsed = urlparse(url)
    slug = parsed.path.strip("/") or parsed.netloc
    slug = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    return f"{prefix}-{slug or 'home'}"


def _topic_for_page(url: str, title: str, text: str) -> str:
    haystack = f"{url} {title} {text[:400]}".lower()
    if "compatib" in haystack or "specification" in haystack:
        return "pipe compatibility"
    if "install" in haystack or "mount" in haystack:
        return "installation requirements"
    if "prolink" in haystack or "mini" in haystack or "prime" in haystack or "/shop" in haystack:
        return "product fit"
    if "alert" in haystack or "dashboard" in haystack or "application" in haystack:
        return "website-derived applications"
    return "website-derived product fit"


def _summary_body(page: ExtractedPage, max_chars: int = 1400) -> str:
    text = _collapse_text(page.text)
    if len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rsplit(" ", 1)[0].strip()
    return f"{trimmed}."


def _hash_source(*parts: str) -> str:
    raw = "\n".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _accessed_date(now: int) -> str:
    return datetime.fromtimestamp(now, timezone.utc).date().isoformat()


def normalize_article_page(
    page: ExtractedPage,
    *,
    now: int,
    fallback: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Build a sales KB article payload from an extracted page."""
    body = _summary_body(page)
    article = {
        "id": str((fallback or {}).get("id") or _record_id_from_url("web", page.url)),
        "title": str((fallback or {}).get("title") or page.title or _title_from_url(page.url)),
        "topic": str((fallback or {}).get("topic") or _topic_for_page(page.url, page.title, page.text)),
        "source_url": page.url,
        "source_accessed": _accessed_date(now),
        "supporting_links": list((fallback or {}).get("supporting_links") or page.links[:5]),
        "body": body,
    }
    errors = _validate_article(article)
    return article, errors


def normalize_product_page(
    page: ExtractedPage,
    *,
    now: int,
    fallback: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Refresh a known product record while preserving structured catalog fields."""
    product = dict(fallback)
    positioning = _summary_body(page, max_chars=320) or str(fallback.get("positioning") or "")
    cautions = [str(item) for item in (fallback.get("cautions") or []) if str(item).strip()]
    pricing_caution = "Confirm current pricing, package, and data-plan details on the website before quoting."
    if pricing_caution not in cautions:
        cautions.append(pricing_caution)
    product.update(
        {
            "source_url": page.url,
            "source_accessed": _accessed_date(now),
            "positioning": positioning,
            "cautions": cautions,
        }
    )
    errors = _validate_product(product)
    return product, errors


def _validate_article(article: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(article.get("id") or "").strip():
        errors.append("missing id")
    if not str(article.get("title") or "").strip():
        errors.append("missing title")
    if not is_allowed_bluebot_url(str(article.get("source_url") or "")):
        errors.append("source_url is not an allowed Bluebot URL")
    if len(str(article.get("body") or "").strip()) < 40:
        errors.append("body is too short")
    if _PRICE_LINE_RE.search(str(article.get("body") or "")):
        errors.append("body contains pricing/package text")
    return errors


def _validate_product(product: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("id", "name", "source_url", "positioning"):
        if not str(product.get(key) or "").strip():
            errors.append(f"missing {key}")
    if not is_allowed_bluebot_url(str(product.get("source_url") or "")):
        errors.append("source_url is not an allowed Bluebot URL")
    for key in ("pipe_size_min_in", "pipe_size_max_in"):
        try:
            float(product.get(key))
        except (TypeError, ValueError):
            errors.append(f"missing numeric {key}")
    if not product.get("connectivity"):
        errors.append("missing connectivity")
    if _PRICE_LINE_RE.search(str(product.get("positioning") or "")):
        errors.append("positioning contains pricing/package text")
    return errors


def _snapshot_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _load_json_list(_ARTICLES_PATH), _load_json_list(_CATALOG_PATH)


def _snapshot_url_maps() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    articles, catalog = _snapshot_records()
    article_by_url: dict[str, dict[str, Any]] = {}
    product_by_url: dict[str, dict[str, Any]] = {}
    for article in articles:
        urls = [article.get("source_url")]
        for link in article.get("supporting_links") or []:
            if isinstance(link, dict):
                urls.append(link.get("url"))
        for url in urls:
            if isinstance(url, str) and is_allowed_bluebot_url(url):
                article_by_url[_canonical_url(url)] = article
    for product in catalog:
        url = product.get("source_url")
        if isinstance(url, str) and is_allowed_bluebot_url(url):
            product_by_url[_canonical_url(url)] = product
    return article_by_url, product_by_url


def _bootstrap_missing_snapshot_records(now: int) -> int:
    """Seed DB records from checked-in snapshots without overwriting synced rows."""
    articles, catalog = _snapshot_records()
    existing_articles = {
        str(item.get("id") or "") for item in store.load_sales_content_records("article")
    }
    existing_products = {
        str(item.get("id") or "") for item in store.load_sales_content_records("product")
    }
    inserted = 0
    for record_type, records, existing in (
        ("article", articles, existing_articles),
        ("product", catalog, existing_products),
    ):
        for record in records:
            record_id = str(record.get("id") or "").strip()
            if not record_id or record_id in existing:
                continue
            source_url = str(record.get("source_url") or "")
            if source_url and not is_allowed_bluebot_url(source_url):
                continue
            store.upsert_sales_content_record(
                record_type,
                record_id,
                record,
                source_url=source_url,
                domain=_domain(source_url),
                title=str(record.get("title") or record.get("name") or ""),
                last_fetched_at=now,
                extraction_status="snapshot",
            )
            inserted += 1
    return inserted


def discover_sitemap_urls(
    *,
    fetcher: Callable[[str], FetchedPage] = fetch_bluebot_url,
    domains: set[str] | frozenset[str] = ALLOWED_DOMAINS,
    max_urls: int = 100,
) -> list[str]:
    """Discover allowlisted page URLs from Bluebot sitemap files."""
    sitemap_queue = [f"https://{domain}/sitemap.xml" for domain in sorted(domains)]
    seen_sitemaps: set[str] = set()
    page_urls: list[str] = []
    seen_pages: set[str] = set()

    while sitemap_queue and len(seen_sitemaps) < 20 and len(page_urls) < max_urls:
        sitemap_url = _canonical_url(sitemap_queue.pop(0))
        if sitemap_url in seen_sitemaps or not is_allowed_bluebot_url(sitemap_url):
            continue
        seen_sitemaps.add(sitemap_url)
        try:
            fetched = fetcher(sitemap_url)
        except Exception as exc:
            store.record_sales_content_sync_event(
                sitemap_url,
                domain=_domain(sitemap_url),
                status="sitemap_fetch_failed",
                message=str(exc),
            )
            continue
        locs = _parse_sitemap_locs(fetched.text)
        for loc in locs:
            loc_url = _canonical_url(loc)
            if not is_allowed_bluebot_url(loc_url):
                continue
            if loc_url.endswith(".xml"):
                sitemap_queue.append(loc_url)
                continue
            if not _looks_like_page(loc_url) or loc_url in seen_pages:
                continue
            seen_pages.add(loc_url)
            page_urls.append(loc_url)
            if len(page_urls) >= max_urls:
                break
    return page_urls


def _parse_sitemap_locs(xml_text: str) -> list[str]:
    try:
        root = ElementTree.fromstring(xml_text.encode("utf-8"))
    except ElementTree.ParseError:
        return re.findall(r"<loc>\s*([^<]+)\s*</loc>", xml_text or "", flags=re.IGNORECASE)
    locs: list[str] = []
    for elem in root.iter():
        if elem.tag.lower().endswith("loc") and elem.text:
            locs.append(elem.text.strip())
    return locs


def _seed_urls(
    *,
    include_sitemap: bool,
    fetcher: Callable[[str], FetchedPage],
    extra_urls: list[str] | None,
    max_pages: int,
) -> list[str]:
    article_by_url, product_by_url = _snapshot_url_maps()
    seen: set[str] = set()
    urls: list[str] = []
    for source in (
        extra_urls or [],
        list(article_by_url),
        list(product_by_url),
        [f"https://{domain}/" for domain in sorted(ALLOWED_DOMAINS)],
    ):
        for url in source:
            canonical = _canonical_url(str(url))
            if is_allowed_bluebot_url(canonical) and _looks_like_page(canonical) and canonical not in seen:
                seen.add(canonical)
                urls.append(canonical)
    if include_sitemap and len(urls) < max_pages:
        for url in discover_sitemap_urls(fetcher=fetcher, max_urls=max_pages):
            if url not in seen:
                seen.add(url)
                urls.append(url)
            if len(urls) >= max_pages:
                break
    return urls[:max_pages]


def run_sync(
    *,
    fetcher: Callable[[str], FetchedPage] = fetch_bluebot_url,
    include_sitemap: bool = True,
    extra_urls: list[str] | None = None,
    max_pages: int | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Refresh the runtime sales KB/catalog from allowlisted Bluebot pages."""
    sync_now = int(now or time.time())
    page_limit = max(1, int(max_pages or os.environ.get("SALES_CONTENT_SYNC_MAX_PAGES", DEFAULT_MAX_PAGES)))
    bootstrapped = _bootstrap_missing_snapshot_records(sync_now)
    article_by_url, product_by_url = _snapshot_url_maps()
    urls = _seed_urls(
        include_sitemap=include_sitemap,
        fetcher=fetcher,
        extra_urls=extra_urls,
        max_pages=page_limit,
    )
    summary = {
        "success": True,
        "checked": 0,
        "updated_articles": 0,
        "updated_products": 0,
        "failed": 0,
        "bootstrapped": bootstrapped,
        "max_pages": page_limit,
    }

    for url in urls:
        summary["checked"] += 1
        try:
            fetched = fetcher(url)
            if "html" not in (fetched.content_type or "text/html").lower():
                raise RuntimeError(f"Unsupported content type: {fetched.content_type}")
            final_url = _assert_allowed_url(fetched.final_url)
            page = extract_readable_page(final_url, fetched.text)
            fallback_article = article_by_url.get(final_url)
            article, article_errors = normalize_article_page(
                page,
                now=sync_now,
                fallback=fallback_article,
            )
            fallback_product = product_by_url.get(final_url)
            product: dict[str, Any] | None = None
            product_errors: list[str] = []
            if fallback_product:
                product, product_errors = normalize_product_page(
                    page,
                    now=sync_now,
                    fallback=fallback_product,
                )
            validation_errors = [*article_errors, *product_errors]
            if validation_errors:
                raise RuntimeError("; ".join(validation_errors))

            content_hash = _hash_source(final_url, page.title, page.text)
            store.upsert_sales_content_record(
                "article",
                str(article["id"]),
                article,
                source_url=final_url,
                domain=_domain(final_url),
                title=str(article.get("title") or ""),
                content_hash=content_hash,
                last_fetched_at=sync_now,
                extraction_status="ok",
            )
            summary["updated_articles"] += 1

            if product:
                store.upsert_sales_content_record(
                    "product",
                    str(product["id"]),
                    product,
                    source_url=final_url,
                    domain=_domain(final_url),
                    title=str(product.get("name") or ""),
                    content_hash=content_hash,
                    last_fetched_at=sync_now,
                    extraction_status="ok",
                )
                summary["updated_products"] += 1

            store.record_sales_content_sync_event(
                final_url,
                domain=_domain(final_url),
                status="ok",
                message="sales content synced",
                metadata={
                    "article_id": article.get("id"),
                    "product_id": (fallback_product or {}).get("id"),
                    "content_hash": content_hash,
                },
            )
        except Exception as exc:
            summary["failed"] += 1
            store.record_sales_content_sync_event(
                url,
                domain=_domain(url),
                status="failed",
                message=str(exc),
            )
    return summary


def _run_loop(interval_hours: float, *, max_pages: int | None, include_sitemap: bool) -> None:
    interval_seconds = max(60.0, interval_hours * 60 * 60)
    while True:
        summary = run_sync(max_pages=max_pages, include_sitemap=include_sitemap)
        print(json.dumps(summary, sort_keys=True), flush=True)
        time.sleep(interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync Bluebot website content into the sales KB DB.")
    parser.add_argument("--run-once", action="store_true", help="Run one sync and exit.")
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=float(os.environ.get("SALES_CONTENT_SYNC_INTERVAL_HOURS", DEFAULT_INTERVAL_HOURS)),
        help="Loop interval when --run-once is not set. Defaults to daily.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=int(os.environ.get("SALES_CONTENT_SYNC_MAX_PAGES", DEFAULT_MAX_PAGES)),
        help="Maximum pages to fetch per sync run.",
    )
    parser.add_argument("--no-sitemap", action="store_true", help="Skip sitemap discovery.")
    args = parser.parse_args(argv)

    include_sitemap = not args.no_sitemap
    if args.run_once:
        print(
            json.dumps(
                run_sync(max_pages=args.max_pages, include_sitemap=include_sitemap),
                sort_keys=True,
            )
        )
        return 0
    _run_loop(args.interval_hours, max_pages=args.max_pages, include_sitemap=include_sitemap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
