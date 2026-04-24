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


def _tool_use_ids(message: dict) -> set[str]:
    """Return all tool_use block ids in an assistant message."""
    content = message.get("content")
    if not isinstance(content, list):
        return set()
    return {
        b["id"]
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
    }


def _tool_result_ids(message: dict) -> set[str]:
    """Return all tool_use_id values from tool_result blocks in a user message."""
    content = message.get("content")
    if not isinstance(content, list):
        return set()
    return {
        b["tool_use_id"]
        for b in content
        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id")
    }


def drop_orphaned_tool_pairs(messages: list) -> list:
    """
    Remove assistant+user message pairs where tool_use ids have no matching
    tool_result.  This cleans up corrupted history that can accumulate when a
    provider turn fails mid-loop (e.g. Gemini streaming with bad indices stores
    a tool_use block but the corresponding tool_result never makes it in).

    Returns a new list; the input is not mutated.
    """
    out: list = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            out.append(msg)
            i += 1
            continue

        use_ids = _tool_use_ids(msg)
        if not use_ids:
            out.append(msg)
            i += 1
            continue

        # Peek at the next message for matching tool_results.
        next_msg = messages[i + 1] if i + 1 < len(messages) else None
        result_ids = _tool_result_ids(next_msg) if next_msg else set()

        if use_ids <= result_ids:
            # All tool_use ids are covered — keep both messages.
            out.append(msg)
            if next_msg is not None:
                out.append(next_msg)
                i += 2
            else:
                i += 1
        else:
            # Orphaned tool_use detected — drop this assistant message and
            # its paired user message (if it exists and has tool_results).
            import logging
            logging.getLogger(__name__).warning(
                "Dropping orphaned tool_use pair: unmatched ids %s",
                use_ids - result_ids,
            )
            i += 1  # skip assistant message
            if next_msg is not None and _tool_result_ids(next_msg):
                i += 1  # also skip the partial tool_result user message

    return out


def messages_for_anthropic_api(messages: list) -> list:
    """Shallow copy of *messages* with ``turn_activity`` blocks removed and
    orphaned tool_use/tool_result pairs dropped."""
    cleaned = drop_orphaned_tool_pairs(messages)
    out: list = []
    for m in cleaned:
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
