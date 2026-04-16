"""
store.py — conversation store.

Backends (selected at runtime from environment):
  • PostgreSQL — when DATABASE_URL env var is set  (e.g. Supabase free tier)
  • SQLite     — default for local development

All conversations are scoped to a user_id so users on a shared deployment
cannot see each other's history.

Database path for SQLite (in priority order):
  1. BLUEBOT_CONV_DB environment variable
  2. conversations.db next to this file
"""

import contextlib
import json
import os
import threading
import time
import uuid
from typing import Any

from plots_paths import resolved_plots_dir


# ---------------------------------------------------------------------------
# Backend helpers  (evaluated lazily so env vars set after import work)
# ---------------------------------------------------------------------------

def _use_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def _ph() -> str:
    """Placeholder token for the active backend."""
    return "%s" if _use_postgres() else "?"


def _q(sql: str) -> str:
    """Translate ? placeholders to %s for PostgreSQL."""
    return sql.replace("?", "%s") if _use_postgres() else sql


_pg_pool = None
_pg_pool_lock = threading.Lock()


def _get_pg_pool():
    """Return a lazily-initialised threaded PostgreSQL connection pool."""
    global _pg_pool
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                from psycopg2.pool import ThreadedConnectionPool  # type: ignore
                _pg_pool = ThreadedConnectionPool(
                    minconn=2,
                    maxconn=int(os.environ.get("PG_POOL_MAX", "10")),
                    dsn=os.environ["DATABASE_URL"],
                )
    return _pg_pool


@contextlib.contextmanager
def _conn():
    """Yield an open (connection, cursor) pair for the active backend."""
    if _use_postgres():
        import psycopg2.extras             # type: ignore
        pool = _get_pg_pool()
        conn = pool.getconn()
        cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            yield conn, cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            pool.putconn(conn)
    else:
        import sqlite3
        db_path = os.environ.get(
            "BLUEBOT_CONV_DB",
            os.path.join(os.path.dirname(__file__), "conversations.db"),
        )
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.cursor()
        try:
            yield conn, cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


# ---------------------------------------------------------------------------
# Schema bootstrap  (called once per backend on first use)
# ---------------------------------------------------------------------------

_bootstrapped: dict[str, bool] = {}


def _ensure_ready() -> None:
    """Create/migrate tables if not done yet for the current backend."""
    key = "pg" if _use_postgres() else "sqlite"
    if _bootstrapped.get(key):
        return

    if _use_postgres():
        with _conn() as (conn, cur):
            cur.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id         TEXT   PRIMARY KEY,
                    user_id    TEXT   NOT NULL DEFAULT '',
                    title      TEXT   NOT NULL DEFAULT '',
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id              SERIAL PRIMARY KEY,
                    conversation_id TEXT   NOT NULL REFERENCES conversations(id),
                    role            TEXT   NOT NULL,
                    content         TEXT   NOT NULL,
                    created_at      BIGINT NOT NULL
                )
            """)
            # Migration: add user_id if an older schema exists
            cur.execute("""
                ALTER TABLE conversations
                    ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT ''
            """)
    else:
        with _conn() as (conn, cur):
            cur.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id         TEXT    PRIMARY KEY,
                    user_id    TEXT    NOT NULL DEFAULT '',
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
            # Migration: add user_id if an older schema exists (SQLite ALTER TABLE
            # doesn't support IF NOT EXISTS, so we catch the error)
            try:
                cur.execute(
                    "ALTER TABLE conversations ADD COLUMN user_id TEXT NOT NULL DEFAULT ''"
                )
            except Exception:
                pass  # column already exists

    _bootstrapped[key] = True


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
# Public API
# ---------------------------------------------------------------------------

def create_conversation(user_id: str, title: str = "") -> str:
    """Create a new conversation scoped to *user_id* and return its short ID."""
    _ensure_ready()
    now = int(time.time())
    for _ in range(10):
        conv_id = str(uuid.uuid4())[:8]
        try:
            with _conn() as (conn, cur):
                cur.execute(
                    _q("INSERT INTO conversations (id, user_id, title, created_at, updated_at)"
                       " VALUES (?, ?, ?, ?, ?)"),
                    (conv_id, user_id, title, now, now),
                )
            return conv_id
        except Exception as exc:
            # Retry only on uniqueness violations; propagate other errors.
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                continue
            raise
    raise RuntimeError("Failed to generate a unique conversation ID")


def set_title(conversation_id: str, title: str) -> None:
    """Set or update a conversation's title."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("UPDATE conversations SET title = ? WHERE id = ?"),
            (title, conversation_id),
        )


def list_conversations(user_id: str) -> list[dict]:
    """
    Return all conversations for *user_id* ordered by most recently updated.

    Each dict: {id, title, created_at, updated_at, message_count}
    message_count counts user-role messages only.
    """
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("""
                SELECT c.id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM   conversations c
                LEFT JOIN messages m
                       ON m.conversation_id = c.id AND m.role = 'user'
                WHERE  c.user_id = ?
                GROUP  BY c.id, c.title, c.created_at, c.updated_at
                ORDER  BY c.updated_at DESC
            """),
            (user_id,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def load_messages(conversation_id: str) -> list[dict]:
    """
    Load all messages for a conversation as plain dicts.

    The returned list is safe to pass directly to run_turn().
    """
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY id"),
            (conversation_id,),
        )
        rows = cur.fetchall()
    return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]


def replace_conversation_messages(conversation_id: str, messages: list[dict]) -> None:
    """
    Replace all messages for a conversation (used after in-place context compression).

    Preserves the conversation row; only message rows are deleted and re-inserted.
    """
    _ensure_ready()
    if not messages:
        with _conn() as (conn, cur):
            cur.execute(
                _q("DELETE FROM messages WHERE conversation_id = ?"),
                (conversation_id,),
            )
            cur.execute(
                _q("UPDATE conversations SET updated_at = ? WHERE id = ?"),
                (int(time.time()), conversation_id),
            )
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
    with _conn() as (conn, cur):
        cur.execute(
            _q("DELETE FROM messages WHERE conversation_id = ?"),
            (conversation_id,),
        )
        cur.executemany(
            _q("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)"),
            rows,
        )
        cur.execute(
            _q("UPDATE conversations SET updated_at = ? WHERE id = ?"),
            (now, conversation_id),
        )


def append_messages(conversation_id: str, messages: list[dict]) -> None:
    """
    Persist new messages, normalising any SDK objects before writing.
    Updates the conversation's updated_at timestamp.
    """
    _ensure_ready()
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
    with _conn() as (conn, cur):
        cur.executemany(
            _q("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)"),
            rows,
        )
        cur.execute(
            _q("UPDATE conversations SET updated_at = ? WHERE id = ?"),
            (now, conversation_id),
        )


def _plot_png_basenames_from_content(content: Any) -> set[str]:
    """Extract plot PNG basenames from tool_result blocks in a message content value."""
    out: set[str] = set()
    if not isinstance(content, list):
        return out
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        raw = block.get("content", "")
        if not raw:
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        for p in data.get("plot_paths") or []:
            if isinstance(p, str) and p:
                base = os.path.basename(p)
                if base.endswith(".png"):
                    out.add(base)
    return out


def _plot_filename_referenced_outside_conversation(cur, filename: str, exclude_id: str) -> bool:
    """True if any message in a *different* conversation references *filename* (content substring)."""
    cur.execute(
        _q("SELECT 1 FROM messages WHERE conversation_id != ? AND content LIKE ? LIMIT 1"),
        (exclude_id, f"%{filename}%"),
    )
    return cur.fetchone() is not None


def _unlink_orphan_plot_files(filenames: set[str]) -> None:
    """Remove PNG files under PLOTS_DIR that are no longer referenced by any message."""
    root = resolved_plots_dir()
    root_resolved = root.resolve()
    for name in filenames:
        if "/" in name or "\\" in name or ".." in name:
            continue
        path = (root / name).resolve()
        try:
            path.relative_to(root_resolved)
        except ValueError:
            continue
        try:
            if path.is_file() and path.suffix.lower() == ".png":
                path.unlink()
        except OSError:
            pass


def delete_conversation(conversation_id: str, user_id: str) -> None:
    """Delete a conversation (and its messages) owned by *user_id*.

    Also removes plot PNG files on disk that were only referenced by this conversation
    (same PLOTS_DIR as GET /api/plots). Files still referenced elsewhere are kept.
    """
    _ensure_ready()
    orphan_pngs: set[str] = set()
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT content FROM messages WHERE conversation_id = ?"),
            (conversation_id,),
        )
        rows = cur.fetchall()
        seen: set[str] = set()
        for row in rows:
            content = json.loads(row["content"])
            seen.update(_plot_png_basenames_from_content(content))
        for fn in seen:
            if not _plot_filename_referenced_outside_conversation(cur, fn, conversation_id):
                orphan_pngs.add(fn)

        cur.execute(
            _q("DELETE FROM messages WHERE conversation_id = ?"),
            (conversation_id,),
        )
        cur.execute(
            _q("DELETE FROM conversations WHERE id = ? AND user_id = ?"),
            (conversation_id, user_id),
        )

    _unlink_orphan_plot_files(orphan_pngs)
