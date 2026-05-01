"""Sales content sync tests."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]
_orch = str(_root / "orchestrator")
if _orch in sys.path:
    sys.path.remove(_orch)
sys.path.insert(0, _orch)


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("BLUEBOT_CONV_DB", str(tmp_path / "sales_content.db"))
    monkeypatch.setenv("DATABASE_URL", "")
    for name in ("store", "sales_tools", "sales_content_sync"):
        sys.modules.pop(name, None)
    import sales_content_sync
    import sales_tools
    import store

    importlib.reload(store)
    importlib.reload(sales_tools)
    importlib.reload(sales_content_sync)
    store._bootstrapped.clear()
    store._ensure_ready()
    return sales_content_sync, sales_tools, store


def test_allows_only_bluebot_domains(tmp_path, monkeypatch):
    sync, _sales_tools, _store = _fresh_modules(tmp_path, monkeypatch)

    assert sync.is_allowed_bluebot_url("https://www.bluebot.com/shop/")
    assert sync.is_allowed_bluebot_url("https://support.bluebot.com/en/articles/example")
    assert sync.is_allowed_bluebot_url("https://help.bluebot.com/support/solutions")
    assert not sync.is_allowed_bluebot_url("https://bluebot.com/")
    assert not sync.is_allowed_bluebot_url("https://example.com/bluebot")
    assert not sync.is_allowed_bluebot_url("javascript:alert(1)")


def test_rejects_off_domain_redirects(tmp_path, monkeypatch):
    sync, _sales_tools, _store = _fresh_modules(tmp_path, monkeypatch)

    class Response:
        status_code = 302
        headers = {"location": "https://example.com/outside"}
        text = ""
        url = "https://www.bluebot.com/shop/"

    class Client:
        def get(self, *_args, **_kwargs):
            return Response()

    with pytest.raises(ValueError, match="off-domain redirect"):
        sync.fetch_bluebot_url("https://www.bluebot.com/shop/", client=Client())


def test_extracts_readable_text_and_redacts_pricing(tmp_path, monkeypatch):
    sync, _sales_tools, _store = _fresh_modules(tmp_path, monkeypatch)
    html = (_root / "tests" / "fixtures" / "sales_content" / "bluebot_product.html").read_text(
        encoding="utf-8"
    )

    page = sync.extract_readable_page("https://www.bluebot.com/shop/bluebot-prolink-prime/", html)
    article, errors = sync.normalize_article_page(page, now=1_777_680_000)

    assert errors == []
    assert "Bluebot ProLink Prime" in article["title"]
    assert "larger water lines" in article["body"]
    assert "$999" not in article["body"]
    assert "monthly package" not in article["body"].lower()


def test_sales_tools_merge_db_content_over_json_fallback(tmp_path, monkeypatch):
    _sync, sales_tools, store = _fresh_modules(tmp_path, monkeypatch)

    fallback = sales_tools.search_sales_kb("hydrated district metering")
    assert "synced-bluebot-district" not in {row["id"] for row in fallback["results"]}

    store.upsert_sales_content_record(
        "article",
        "synced-bluebot-district",
        {
            "id": "synced-bluebot-district",
            "title": "Synced district metering",
            "topic": "website-derived product fit",
            "source_url": "https://www.bluebot.com/district-metering/",
            "source_accessed": "2026-04-29",
            "body": "Hydrated district metering guidance for pressure, alerts, and dashboards.",
        },
        source_url="https://www.bluebot.com/district-metering/",
        domain="www.bluebot.com",
        title="Synced district metering",
    )
    store.upsert_sales_content_record(
        "product",
        "synced-large-long-range",
        {
            "id": "synced-large-long-range",
            "name": "Synced Large Long Range",
            "line": "Synced",
            "source_url": "https://www.bluebot.com/shop/synced-large-long-range/",
            "source_accessed": "2026-04-29",
            "positioning": "Long-range option for large synced pipe applications.",
            "pipe_size_min_in": 5.0,
            "pipe_size_max_in": 6.0,
            "connectivity": ["no_wifi_required"],
            "environment": ["outdoor"],
            "fit_notes": ["Good for large no-Wi-Fi sites."],
            "cautions": ["Confirm current pricing on the website before quoting."],
        },
        source_url="https://www.bluebot.com/shop/synced-large-long-range/",
        domain="www.bluebot.com",
        title="Synced Large Long Range",
    )

    synced = sales_tools.search_sales_kb("hydrated district metering")
    assert synced["results"][0]["id"] == "synced-bluebot-district"

    recommendation = sales_tools.recommend_product_line(
        pipe_size="5 inch",
        has_reliable_wifi=False,
        needs_long_range=True,
    )
    assert recommendation["recommendations"][0]["name"] == "Synced Large Long Range"


def test_failed_sync_keeps_previous_known_good_record(tmp_path, monkeypatch):
    sync, _sales_tools, store = _fresh_modules(tmp_path, monkeypatch)
    source_url = "https://www.bluebot.com/known-good/"
    store.upsert_sales_content_record(
        "article",
        "known-good",
        {
            "id": "known-good",
            "title": "Known Good",
            "topic": "website-derived product fit",
            "source_url": source_url,
            "source_accessed": "2026-04-28",
            "body": "Known good content should survive a later failed fetch.",
        },
        source_url=source_url,
        domain="www.bluebot.com",
        title="Known Good",
        content_hash="original",
    )

    def failing_fetcher(url: str):
        raise RuntimeError(f"boom for {url}")

    summary = sync.run_sync(
        fetcher=failing_fetcher,
        include_sitemap=False,
        extra_urls=[source_url],
        max_pages=1,
        now=1_777_680_000,
    )

    assert summary["failed"] == 1
    [record] = [r for r in store.load_sales_content_records("article") if r["id"] == "known-good"]
    assert record["body"] == "Known good content should survive a later failed fetch."
    assert store.load_sales_content_record_metadata("article", "known-good")["content_hash"] == "original"
    assert store.list_sales_content_sync_events(1)[0]["status"] == "failed"


def test_fixture_sync_updates_article_and_known_product(tmp_path, monkeypatch):
    sync, sales_tools, store = _fresh_modules(tmp_path, monkeypatch)
    url = "https://www.bluebot.com/shop/bluebot-prolink-prime/"
    html = (_root / "tests" / "fixtures" / "sales_content" / "bluebot_product.html").read_text(
        encoding="utf-8"
    )

    def fixture_fetcher(requested_url: str):
        assert requested_url == url
        return sync.FetchedPage(
            url=requested_url,
            final_url=requested_url,
            status_code=200,
            content_type="text/html; charset=utf-8",
            text=html,
        )

    summary = sync.run_sync(
        fetcher=fixture_fetcher,
        include_sitemap=False,
        extra_urls=[url],
        max_pages=1,
        now=1_777_680_000,
    )

    assert summary["updated_articles"] == 1
    assert summary["updated_products"] == 1
    product_meta = store.load_sales_content_record_metadata("product", "bluebot-prolink-prime")
    assert product_meta["extraction_status"] == "ok"

    recommendation = sales_tools.recommend_product_line(
        pipe_size="3 inch",
        has_reliable_wifi=False,
        needs_long_range=True,
    )
    assert recommendation["recommendations"][0]["name"] == "Bluebot ProLink Prime"
    assert "$999" not in recommendation["recommendations"][0]["positioning"]
