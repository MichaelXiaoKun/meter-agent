"""
Strip UI-only content blocks (``turn_activity``) before calls to Anthropic.

Persisted history may include a ``turn_activity`` dict on the last assistant
message of a turn; it must not be sent back to the model.
"""

TURN_ACTIVITY_BLOCK = "turn_activity"


def _block_type(block: object) -> str | None:
    if isinstance(block, dict):
        t = block.get("type")
        return str(t) if t is not None else None
    t = getattr(block, "type", None)
    return str(t) if t is not None else None


def content_without_turn_activity(content: object) -> object:
    if not isinstance(content, list):
        return content
    return [b for b in content if _block_type(b) != TURN_ACTIVITY_BLOCK]


def messages_for_anthropic_api(messages: list) -> list:
    """Shallow copy of *messages* with ``turn_activity`` blocks removed from content."""
    out: list = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        c = m.get("content")
        if isinstance(c, list):
            c2 = content_without_turn_activity(c)
            n = dict(m)
            n["content"] = c2
            out.append(n)
        else:
            out.append(m)
    return out


def append_turn_activity_block(assistant_message: dict, events: list[dict]) -> None:
    """
    Append a ``turn_activity`` dict block to an assistant *message* (mutates).

    *assistant_message* is a single message dict. *content* becomes a list of
    normalised dict blocks plus ``turn_activity`` if it was a string, list, etc.
    """
    from store import _normalize_block  # local import avoids cycles at import time

    if not events or assistant_message.get("role") != "assistant":
        return
    c = assistant_message.get("content")
    blocks: list = []
    if isinstance(c, str):
        blocks = [{"type": "text", "text": c}]
    elif isinstance(c, list):
        blocks = [_normalize_block(b) for b in c]
    else:
        return
    blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") != TURN_ACTIVITY_BLOCK]
    blocks.append(
        {
            "type": TURN_ACTIVITY_BLOCK,
            "v": 1,
            "events": events,
        }
    )
    assistant_message["content"] = blocks
