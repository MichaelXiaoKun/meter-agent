"""Compatibility facade for the FastAPI app.

The implementation lives in :mod:`server.app`.  Keep this flat module so
existing entrypoints such as ``uvicorn api:app`` and tests that import
``api`` continue to receive the real implementation module.
"""

from __future__ import annotations

import sys as _sys

try:
    from server import app as _impl
except ModuleNotFoundError:  # pragma: no cover - package-style import.
    from .server import app as _impl

_sys.modules[__name__] = _impl
