"""
store.py — SQLite-backed conversation store.

Persists conversation metadata and message history so that sessions survive
restarts and multiple conversations can be created, listed, and resumed.

Database path (in priority order):
  1. BLUEBOT_CONV_DB environment variable
  2. conversations.db next to this file
"""

import json
import os
import sqlite3
import time
import uuid
from typing import Any


_DEFAULT_DB = os.path.join(os.path.dirname(__file__), "conversations.db")


def _db_path() -> str:
    return os.environ.get("BLUEBOT_CONV_DB", _DEFAULT_DB)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _normalize_block(block: Any) -> dict:
    """Convert an Anthropic SDK content block to a plain dict."""
    if isinstance(block, dict):
        return block
    t = getattr(block, "type", None)
    if t == "text":
        return {"type": "text", "text": block.text}
    if t == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
    if t == "tool_result":
        return {"type": "tool_result", "tool_use_id": block.tool_use_id, "content": block.content}
    return vars(block) if hasattr(block, "__dict__") else {"raw": str(block)}


def _normalize_content(content: Any) -> Any:
    """Normalize message content to a JSON-serialisable form."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [_normalize_block(b) for b in content]
    return content


# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS conversations (
            id         TEXT    PRIMARY KEY,
            title      TEXT    NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT    NOT NULL REFERENCES conversations(id),
            role            TEXT    NOT NULL,
            content         TEXT    NOT NULL,
            created_at      INTEGER NOT NULL
        );
    """)
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_conversation(title: str = "") -> str:
    """Create a new conversation and return its short ID."""
    now = int(time.time())
    for _ in range(10):
        conv_id = str(uuid.uuid4())[:8]
        try:
            with _connect() as conn:
                conn.execute(
                    "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (conv_id, title, now, now),
                )
            return conv_id
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("Failed to generate a unique conversation ID")


def set_title(conversation_id: str, title: str) -> None:
    """Set or update a conversation's title."""
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (title, conversation_id),
        )


def list_conversations() -> list[dict]:
    """
    Return all conversations ordered by most recently updated.

    Each dict: {id, title, created_at, updated_at, message_count}
    message_count counts user-role messages only.
    """
    with _connect() as conn:
        rows = conn.execute("""
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
            FROM   conversations c
            LEFT JOIN messages m
                   ON m.conversation_id = c.id AND m.role = 'user'
            GROUP  BY c.id
            ORDER  BY c.updated_at DESC
        """).fetchall()
    return [dict(r) for r in rows]


def load_messages(conversation_id: str) -> list[dict]:
    """
    Load all messages for a conversation as plain dicts.

    The returned list is safe to pass directly to run_turn().
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
    return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]


def append_messages(conversation_id: str, messages: list[dict]) -> None:
    """
    Persist new messages, normalising any SDK objects before writing.
    Updates the conversation's updated_at timestamp.
    """
    if not messages:
        return
    now = int(time.time())
    rows = [
        (
            conversation_id,
            msg["role"],
            json.dumps(_normalize_content(msg["content"]), default=str),
            now,
        )
        for msg in messages
    ]
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )


def delete_conversation(conversation_id: str) -> None:
    """Delete a conversation and all its messages."""
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
