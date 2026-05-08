"""Synced sales-content persistence facade."""

from __future__ import annotations

from persistence.store_impl import (
    list_sales_content_sync_events,
    load_sales_content_record_metadata,
    load_sales_content_records,
    record_sales_content_sync_event,
    upsert_sales_content_record,
)

__all__ = [
    "list_sales_content_sync_events",
    "load_sales_content_record_metadata",
    "load_sales_content_records",
    "record_sales_content_sync_event",
    "upsert_sales_content_record",
]
