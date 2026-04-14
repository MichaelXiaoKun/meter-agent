"""
summarizer.py — Generates a short conversation title after each turn.

Uses Claude Haiku (fast, cheap) to produce a 5-8 word summary of the
conversation so far and updates the conversation's title in the store.
Called after every successful run_turn() in both CLI and Streamlit UI.
"""

import anthropic
import store


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
                elif hasattr(block, "text"):
                    text = block.text
                if text:
                    lines.append(f"Assistant: {text[:300]}")
                    break

    return "\n".join(lines)


def summarize(messages: list[dict]) -> str:
    """
    Generate a 5-8 word conversation title using Claude Haiku.

    Returns an empty string on any failure so callers can silently skip.
    """
    transcript = _extract_transcript(messages)
    if not transcript:
        return ""

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{
                "role": "user",
                "content": (
                    "Write a very short title (max 5 words, under 30 characters) for this chat. "
                    "Include the meter ID if mentioned. "
                    "Reply with ONLY the title — no quotes, no punctuation.\n\n"
                    f"{transcript}"
                ),
            }],
        )
        title = response.content[0].text.strip().rstrip(".")
        return title[:40]
    except Exception:
        return ""


def update_title(conversation_id: str, messages: list[dict]) -> None:
    """Generate a summary title and persist it. Silently no-ops on failure."""
    title = summarize(messages)
    if title:
        store.set_title(conversation_id, title)
