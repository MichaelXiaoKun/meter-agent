"""Compatibility facade for persistence helpers.

The implementation lives in :mod:`persistence.store_impl`.  Keep this flat
module so existing ``import store`` call sites and tests continue to operate
on the real implementation module, including private test hooks.
"""

from __future__ import annotations

import sys as _sys

from persistence import store_impl as _impl

_sys.modules[__name__] = _impl
