"""Compatibility facade for the admin chat turn loop.

The implementation lives in :mod:`admin_chat.turn_loop`.  Keep this flat
module so existing ``from agent import run_turn`` call sites and tests continue
to operate on the real implementation module.
"""

from __future__ import annotations

import sys as _sys

try:
    from admin_chat import turn_loop as _impl
except ModuleNotFoundError:  # pragma: no cover - package-style import.
    from .admin_chat import turn_loop as _impl

_sys.modules[__name__] = _impl
