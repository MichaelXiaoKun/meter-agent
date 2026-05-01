"""
summarizer.py — Generates a short conversation title after each turn.

Uses the cheap tier of whatever provider backs the active model to produce
a 5-8 word summary and updates the conversation's title in the store.
Called after successful admin turns to keep conversation titles fresh.
"""

import os
import sys
from pathlib import Path

_ORCHESTRATOR_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _ORCHESTRATOR_DIR.parent
for _path in (_ORCHESTRATOR_DIR, _REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import store
from llm import get_provider
from llm.registry import get_cheap_model, MODEL_CATALOG
from shared.tpm_window import record_input_tokens


def _extract_transcript(messages: list[dict]) -> str:
    """
    Pull readable user/assistant text out of a messages list.

    Skips tool-result messages entirely; truncates long blocks to 300 chars.
    Uses the last 10 messages so the summary reflects recent context.
    """
    lines = []
    for msg in messages[-10:]:
        role    = msg["role"]
        content = msg["content"]

        if role == "user" and isinstance(content, str):
            lines.append(f"User: {content[:300]}")

        elif role == "assistant" and isinstance(content, list):
            for block in content:
                text = None
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block["text"]
                if text:
                    lines.append(f"Assistant: {text[:300]}")
                    break

    return "\n".join(lines)


def summarize(
    messages: list[dict],
    anthropic_api_key: str | None = None,
    *,
    active_model: str | None = None,
) -> str:
    """
    Generate a 5-8 word conversation title using the cheapest available model.

    Returns an empty string on any failure so callers can silently skip.
    """
    transcript = _extract_transcript(messages)
    if not transcript:
        return ""

    try:
        model_id = active_model or os.environ.get("ORCHESTRATOR_MODEL", "claude-haiku-4-5").strip()
        cheap = get_cheap_model(model_id)
        provider = get_provider(cheap, api_key_override=(anthropic_api_key or "").strip() or None)
        response = provider.complete(
            cheap,
            [{
                "role": "user",
                "content": (
                    "Write a very short title (max 5 words, under 30 characters) for this chat. "
                    "Include the meter ID if mentioned. "
                    "Reply with ONLY the title — no quotes, no punctuation.\n\n"
                    f"{transcript}"
                ),
            }],
            system="",
            tools=[],
            max_tokens=20,
        )
        record_input_tokens(response.input_tokens)
        title = response.text.strip().rstrip(".")
        return title[:40]
    except Exception:
        return ""


def update_title(
    conversation_id: str,
    messages: list[dict],
    anthropic_api_key: str | None = None,
    *,
    active_model: str | None = None,
) -> None:
    """Generate a summary title and persist it. Silently no-ops on failure."""
    title = summarize(messages, anthropic_api_key=anthropic_api_key, active_model=active_model)
    if title:
        store.set_title(conversation_id, title)
