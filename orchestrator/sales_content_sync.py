"""Compatibility facade for the sales content sync CLI/module."""

from __future__ import annotations

import sys as _sys

try:
    from sales_chat import content_sync as _impl
except ModuleNotFoundError:  # pragma: no cover - package-style import.
    from .sales_chat import content_sync as _impl

if __name__ == "__main__":  # pragma: no cover - CLI passthrough.
    raise SystemExit(_impl.main())

_sys.modules[__name__] = _impl
