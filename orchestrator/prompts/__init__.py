"""Versioned system prompts for the orchestrator.

Prompts live on disk (``system_v<N>.md``) instead of being hard-coded in
``orchestrator/agent.py`` so we can:

* review prompt changes as regular file diffs,
* roll back by flipping ``ORCHESTRATOR_PROMPT_VERSION``,
* A/B test (``v1`` vs ``v2``) by pointing different users at different files,
* tag every orchestrator turn with the exact prompt version that produced it
  (see ``prompt_version`` on the ``turn_start`` observability event).

The loader is intentionally tiny — no templating, no YAML front-matter, no
Jinja. A prompt is just UTF-8 text. If we later need per-locale prompts or
dynamic parameter injection we add it here, but today KISS wins.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Tuple

DEFAULT_PROMPT_VERSION = "v1"

_PROMPTS_DIR = Path(__file__).resolve().parent
_VERSION_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class PromptNotFoundError(FileNotFoundError):
    """Raised when ``ORCHESTRATOR_PROMPT_VERSION`` points at a missing prompt file."""


def _resolve_version(version: str | None) -> str:
    if version is not None:
        v = version.strip()
    else:
        v = (os.environ.get("ORCHESTRATOR_PROMPT_VERSION") or "").strip()
    if not v:
        v = DEFAULT_PROMPT_VERSION
    if not _VERSION_RE.match(v):
        raise ValueError(
            f"invalid prompt version {v!r}: only [A-Za-z0-9._-] are allowed"
        )
    return v


def prompt_path(version: str | None = None) -> Path:
    """Return the on-disk path for ``system_v<version>.md`` without reading it."""
    v = _resolve_version(version)
    return _PROMPTS_DIR / f"system_{v}.md"


def load_system_prompt(version: str | None = None) -> Tuple[str, str]:
    """Return ``(prompt_text, resolved_version)``.

    Resolution order:
      1. explicit ``version`` argument
      2. ``ORCHESTRATOR_PROMPT_VERSION`` env var
      3. :data:`DEFAULT_PROMPT_VERSION`

    Raises :class:`PromptNotFoundError` if the resolved file does not exist —
    callers are expected to fail loudly rather than silently fall back.
    """
    v = _resolve_version(version)
    path = _PROMPTS_DIR / f"system_{v}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PromptNotFoundError(
            f"no system prompt file for version {v!r} at {path}"
        ) from exc
    return text, v


def available_versions() -> list[str]:
    """List every ``system_v*.md`` file currently shipped, sorted lexically."""
    out: list[str] = []
    for entry in _PROMPTS_DIR.iterdir():
        m = re.match(r"^system_(.+)\.md$", entry.name)
        if m:
            out.append(m.group(1))
    out.sort()
    return out


__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "PromptNotFoundError",
    "available_versions",
    "load_system_prompt",
    "prompt_path",
]
