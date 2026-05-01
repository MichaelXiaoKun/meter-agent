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
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from plots_paths import resolved_plots_dir


TICKET_STATUSES = {"open", "in_progress", "waiting_on_human", "resolved", "cancelled"}
TICKET_PRIORITIES = {"low", "normal", "high", "urgent"}
TICKET_OWNER_TYPES = {"agent", "human", "unassigned"}
TICKET_OPEN_STATUSES = {"open", "in_progress", "waiting_on_human"}


# ---------------------------------------------------------------------------
# Backend helpers  (evaluated lazily so env vars set after import work)
# ---------------------------------------------------------------------------

def _use_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


def _ph() -> str:
    """Placeholder token for the active backend."""
    return "%s" if _use_postgres() else "?"


def _sqlite_db_path() -> str:
    """
    Resolve the SQLite DB path.

    Railway volumes are often exposed as a directory path. If BLUEBOT_CONV_DB
    points at a directory/mount root, store the database inside it instead of
    passing the directory itself to sqlite3.connect().
    """
    raw = os.environ.get(
        "BLUEBOT_CONV_DB",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "conversations.db"),
    )
    path = Path(raw).expanduser()
    if str(raw).endswith(("/", os.sep)) or path.exists() and path.is_dir():
        path = path / "conversations.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


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
        conn = sqlite3.connect(_sqlite_db_path())
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
            cur.execute("""
                ALTER TABLE conversations
                    ADD COLUMN IF NOT EXISTS context_summary TEXT
            """)
            cur.execute("""
                ALTER TABLE conversations
                    ADD COLUMN IF NOT EXISTS context_summary_covers INTEGER DEFAULT 0
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS shares (
                    token            TEXT   PRIMARY KEY,
                    conversation_id  TEXT   NOT NULL,
                    owner_user_id    TEXT   NOT NULL,
                    title            TEXT   NOT NULL DEFAULT '',
                    messages_json    TEXT   NOT NULL,
                    created_at       BIGINT NOT NULL,
                    revoked          INTEGER NOT NULL DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sales_conversations (
                    id                TEXT   PRIMARY KEY,
                    title             TEXT   NOT NULL DEFAULT '',
                    lead_summary_json TEXT   NOT NULL DEFAULT '{}',
                    created_at        BIGINT NOT NULL,
                    updated_at        BIGINT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sales_messages (
                    id              SERIAL PRIMARY KEY,
                    conversation_id TEXT   NOT NULL REFERENCES sales_conversations(id),
                    role            TEXT   NOT NULL,
                    content         TEXT   NOT NULL,
                    created_at      BIGINT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sales_content_records (
                    record_type            TEXT   NOT NULL,
                    record_id              TEXT   NOT NULL,
                    payload_json           TEXT   NOT NULL,
                    source_url             TEXT   NOT NULL DEFAULT '',
                    domain                 TEXT   NOT NULL DEFAULT '',
                    title                  TEXT   NOT NULL DEFAULT '',
                    content_hash           TEXT   NOT NULL DEFAULT '',
                    last_fetched_at        BIGINT NOT NULL,
                    last_changed_at        BIGINT NOT NULL,
                    extraction_status      TEXT   NOT NULL DEFAULT 'ok',
                    validation_errors_json TEXT   NOT NULL DEFAULT '[]',
                    updated_at             BIGINT NOT NULL,
                    PRIMARY KEY (record_type, record_id)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sales_content_sync_events (
                    id            SERIAL PRIMARY KEY,
                    source_url    TEXT   NOT NULL,
                    domain        TEXT   NOT NULL DEFAULT '',
                    status        TEXT   NOT NULL,
                    message       TEXT   NOT NULL DEFAULT '',
                    metadata_json TEXT   NOT NULL DEFAULT '{}',
                    created_at    BIGINT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id                 TEXT   PRIMARY KEY,
                    user_id            TEXT   NOT NULL,
                    conversation_id    TEXT,
                    serial_number      TEXT,
                    title              TEXT   NOT NULL,
                    description        TEXT   NOT NULL DEFAULT '',
                    success_criteria   TEXT   NOT NULL,
                    status             TEXT   NOT NULL DEFAULT 'open',
                    priority           TEXT   NOT NULL DEFAULT 'normal',
                    owner_type         TEXT   NOT NULL DEFAULT 'unassigned',
                    owner_id           TEXT   NOT NULL DEFAULT '',
                    created_by_turn_id TEXT,
                    due_at             BIGINT,
                    closed_at          BIGINT,
                    metadata_json      TEXT   NOT NULL DEFAULT '{}',
                    created_at         BIGINT NOT NULL,
                    updated_at         BIGINT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS ticket_events (
                    id            SERIAL PRIMARY KEY,
                    ticket_id     TEXT   NOT NULL REFERENCES tickets(id),
                    event_type    TEXT   NOT NULL,
                    actor_type    TEXT   NOT NULL DEFAULT 'agent',
                    actor_id      TEXT   NOT NULL DEFAULT '',
                    note          TEXT   NOT NULL DEFAULT '',
                    turn_id       TEXT,
                    evidence_json TEXT   NOT NULL DEFAULT '{}',
                    created_at    BIGINT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tool_evidence (
                    id                  TEXT   PRIMARY KEY,
                    conversation_id     TEXT   NOT NULL,
                    turn_id             TEXT   NOT NULL DEFAULT '',
                    tool_use_id         TEXT   NOT NULL DEFAULT '',
                    tool_name           TEXT   NOT NULL,
                    input_json          TEXT   NOT NULL DEFAULT '{}',
                    raw_result_json     TEXT   NOT NULL DEFAULT '{}',
                    compact_result_json TEXT   NOT NULL DEFAULT '{}',
                    result_sha256       TEXT   NOT NULL DEFAULT '',
                    success             INTEGER NOT NULL DEFAULT 0,
                    created_at          BIGINT NOT NULL
                )
            """)
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_tickets_user_status_updated "
                "ON tickets(user_id, status, updated_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_tickets_conv_serial "
                "ON tickets(conversation_id, serial_number)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_evidence_conv_turn "
                "ON tool_evidence(conversation_id, turn_id, created_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_evidence_tool_use "
                "ON tool_evidence(tool_use_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sales_content_records_type "
                "ON sales_content_records(record_type, updated_at)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_sales_content_sync_events_created "
                "ON sales_content_sync_events(created_at)"
            )
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
                CREATE TABLE IF NOT EXISTS shares (
                    token            TEXT    PRIMARY KEY,
                    conversation_id  TEXT    NOT NULL,
                    owner_user_id    TEXT    NOT NULL,
                    title            TEXT    NOT NULL DEFAULT '',
                    messages_json    TEXT    NOT NULL,
                    created_at      INTEGER NOT NULL,
                    revoked          INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sales_conversations (
                    id                TEXT    PRIMARY KEY,
                    title             TEXT    NOT NULL DEFAULT '',
                    lead_summary_json TEXT    NOT NULL DEFAULT '{}',
                    created_at        INTEGER NOT NULL,
                    updated_at        INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sales_messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT    NOT NULL REFERENCES sales_conversations(id),
                    role            TEXT    NOT NULL,
                    content         TEXT    NOT NULL,
                    created_at      INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sales_content_records (
                    record_type            TEXT    NOT NULL,
                    record_id              TEXT    NOT NULL,
                    payload_json           TEXT    NOT NULL,
                    source_url             TEXT    NOT NULL DEFAULT '',
                    domain                 TEXT    NOT NULL DEFAULT '',
                    title                  TEXT    NOT NULL DEFAULT '',
                    content_hash           TEXT    NOT NULL DEFAULT '',
                    last_fetched_at        INTEGER NOT NULL,
                    last_changed_at        INTEGER NOT NULL,
                    extraction_status      TEXT    NOT NULL DEFAULT 'ok',
                    validation_errors_json TEXT    NOT NULL DEFAULT '[]',
                    updated_at             INTEGER NOT NULL,
                    PRIMARY KEY (record_type, record_id)
                );
                CREATE TABLE IF NOT EXISTS sales_content_sync_events (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_url    TEXT    NOT NULL,
                    domain        TEXT    NOT NULL DEFAULT '',
                    status        TEXT    NOT NULL,
                    message       TEXT    NOT NULL DEFAULT '',
                    metadata_json TEXT    NOT NULL DEFAULT '{}',
                    created_at    INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    id                 TEXT    PRIMARY KEY,
                    user_id            TEXT    NOT NULL,
                    conversation_id    TEXT,
                    serial_number      TEXT,
                    title              TEXT    NOT NULL,
                    description        TEXT    NOT NULL DEFAULT '',
                    success_criteria   TEXT    NOT NULL,
                    status             TEXT    NOT NULL DEFAULT 'open',
                    priority           TEXT    NOT NULL DEFAULT 'normal',
                    owner_type         TEXT    NOT NULL DEFAULT 'unassigned',
                    owner_id           TEXT    NOT NULL DEFAULT '',
                    created_by_turn_id TEXT,
                    due_at             INTEGER,
                    closed_at          INTEGER,
                    metadata_json      TEXT    NOT NULL DEFAULT '{}',
                    created_at         INTEGER NOT NULL,
                    updated_at         INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ticket_events (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id     TEXT    NOT NULL REFERENCES tickets(id),
                    event_type    TEXT    NOT NULL,
                    actor_type    TEXT    NOT NULL DEFAULT 'agent',
                    actor_id      TEXT    NOT NULL DEFAULT '',
                    note          TEXT    NOT NULL DEFAULT '',
                    turn_id       TEXT,
                    evidence_json TEXT    NOT NULL DEFAULT '{}',
                    created_at    INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_evidence (
                    id                  TEXT    PRIMARY KEY,
                    conversation_id     TEXT    NOT NULL,
                    turn_id             TEXT    NOT NULL DEFAULT '',
                    tool_use_id         TEXT    NOT NULL DEFAULT '',
                    tool_name           TEXT    NOT NULL,
                    input_json          TEXT    NOT NULL DEFAULT '{}',
                    raw_result_json     TEXT    NOT NULL DEFAULT '{}',
                    compact_result_json TEXT    NOT NULL DEFAULT '{}',
                    result_sha256       TEXT    NOT NULL DEFAULT '',
                    success             INTEGER NOT NULL DEFAULT 0,
                    created_at          INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tickets_user_status_updated
                    ON tickets(user_id, status, updated_at);
                CREATE INDEX IF NOT EXISTS idx_tickets_conv_serial
                    ON tickets(conversation_id, serial_number);
                CREATE INDEX IF NOT EXISTS idx_tool_evidence_conv_turn
                    ON tool_evidence(conversation_id, turn_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_tool_evidence_tool_use
                    ON tool_evidence(tool_use_id);
                CREATE INDEX IF NOT EXISTS idx_sales_content_records_type
                    ON sales_content_records(record_type, updated_at);
                CREATE INDEX IF NOT EXISTS idx_sales_content_sync_events_created
                    ON sales_content_sync_events(created_at);
            """)
            # Migration: add columns if an older schema exists (SQLite ALTER TABLE
            # doesn't support IF NOT EXISTS, so we catch the error)
            for _col_sql in (
                "ALTER TABLE conversations ADD COLUMN user_id TEXT NOT NULL DEFAULT ''",
                "ALTER TABLE conversations ADD COLUMN context_summary TEXT",
                "ALTER TABLE conversations ADD COLUMN context_summary_covers INTEGER DEFAULT 0",
            ):
                try:
                    cur.execute(_col_sql)
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


def get_api_context_info(conversation_id: str) -> tuple[str | None, int]:
    """Return (context_summary, context_summary_covers) cached from the last compression."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT context_summary, context_summary_covers FROM conversations WHERE id = ?"),
            (conversation_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None, 0
    return row["context_summary"], int(row["context_summary_covers"] or 0)


def set_api_context_info(conversation_id: str, summary: str, covers: int) -> None:
    """Persist a compressed context summary so the next turn skips re-compression."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("UPDATE conversations SET context_summary = ?, context_summary_covers = ? WHERE id = ?"),
            (summary, covers, conversation_id),
        )


# ---------------------------------------------------------------------------
# Tool evidence ledger
# ---------------------------------------------------------------------------

def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _new_evidence_id() -> str:
    return "ev_" + uuid.uuid4().hex[:12]


def _tool_evidence_from_row(row: Any) -> dict:
    d = dict(row)
    for key in ("input_json", "raw_result_json", "compact_result_json"):
        target = key.removesuffix("_json")
        try:
            d[target] = json.loads(d.pop(key) or "{}")
        except Exception:
            d[target] = {}
    d["success"] = bool(d.get("success"))
    return d


def record_tool_evidence(
    *,
    conversation_id: str,
    tool_name: str,
    input_payload: dict | None,
    raw_result: dict | None,
    compact_result: dict | None = None,
    turn_id: str | None = None,
    tool_use_id: str | None = None,
    success: bool = False,
) -> dict:
    """Append one immutable tool-call evidence row for validation/audit."""
    _ensure_ready()
    conversation_id = _clean_required_text(
        conversation_id or "default", "conversation_id", max_len=120
    )
    tool_name = _clean_required_text(tool_name, "tool_name", max_len=160)
    turn_id = _clean_optional_text(turn_id, max_len=120) or ""
    tool_use_id = _clean_optional_text(tool_use_id, max_len=160) or ""
    input_json = _json_text(input_payload or {})
    raw_json = _json_text(raw_result or {})
    compact_json = _json_text(compact_result if compact_result is not None else raw_result or {})
    result_sha256 = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
    now = int(time.time())
    for _ in range(10):
        evidence_id = _new_evidence_id()
        try:
            with _conn() as (conn, cur):
                cur.execute(
                    _q(
                        "INSERT INTO tool_evidence (id, conversation_id, turn_id, "
                        "tool_use_id, tool_name, input_json, raw_result_json, "
                        "compact_result_json, result_sha256, success, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    ),
                    (
                        evidence_id,
                        conversation_id,
                        turn_id,
                        tool_use_id,
                        tool_name,
                        input_json,
                        raw_json,
                        compact_json,
                        result_sha256,
                        1 if success else 0,
                        now,
                    ),
                )
                cur.execute(
                    _q("SELECT * FROM tool_evidence WHERE id = ?"),
                    (evidence_id,),
                )
                row = cur.fetchone()
            return _tool_evidence_from_row(row) if row is not None else {}
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                continue
            raise
    raise RuntimeError("Failed to generate a unique evidence ID")


def list_tool_evidence(
    conversation_id: str,
    *,
    turn_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return tool evidence rows newest-last for a conversation or one turn."""
    _ensure_ready()
    conversation_id = _clean_required_text(conversation_id, "conversation_id", max_len=120)
    clauses = ["conversation_id = ?"]
    params: list[Any] = [conversation_id]
    clean_turn_id = _clean_optional_text(turn_id, max_len=120)
    if clean_turn_id:
        clauses.append("turn_id = ?")
        params.append(clean_turn_id)
    params.append(max(1, min(int(limit or 100), 500)))
    sql = (
        "SELECT * FROM tool_evidence WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at ASC LIMIT ?"
    )
    with _conn() as (conn, cur):
        cur.execute(_q(sql), tuple(params))
        rows = cur.fetchall()
    return [_tool_evidence_from_row(row) for row in rows]


# ---------------------------------------------------------------------------
# Ticket/work-item persistence
# ---------------------------------------------------------------------------

def _clean_optional_text(value: Any, *, max_len: int | None = None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return s[:max_len] if max_len is not None else s


def _clean_required_text(value: Any, field: str, *, max_len: int | None = None) -> str:
    s = _clean_optional_text(value, max_len=max_len)
    if not s:
        raise ValueError(f"{field} is required")
    return s


def _validate_choice(value: str, allowed: set[str], field: str) -> str:
    v = (value or "").strip()
    if v not in allowed:
        allowed_s = ", ".join(sorted(allowed))
        raise ValueError(f"{field} must be one of: {allowed_s}")
    return v


def _json_dict(value: Any, field: str) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise ValueError(f"{field} must be an object")


def _loads_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _ticket_from_row(row: Any) -> dict:
    d = dict(row)
    d["metadata"] = _loads_dict(d.pop("metadata_json", "{}"))
    return d


def _ticket_event_from_row(row: Any) -> dict:
    d = dict(row)
    d["evidence"] = _loads_dict(d.pop("evidence_json", "{}"))
    return d


def _ticket_closed_at(status: str, closed_at: int | None = None) -> int | None:
    if status in {"resolved", "cancelled"}:
        return int(closed_at or time.time())
    return None


def _new_ticket_id() -> str:
    return "tkt_" + uuid.uuid4().hex[:10]


def get_conversation_user_id(conversation_id: str) -> str | None:
    """Return the owner user_id for an admin conversation, if it exists."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT user_id FROM conversations WHERE id = ?"),
            (conversation_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    user_id = row["user_id"]
    return str(user_id) if user_id is not None else None


def create_ticket(
    *,
    user_id: str,
    title: str,
    success_criteria: str,
    conversation_id: str | None = None,
    serial_number: str | None = None,
    description: str = "",
    status: str = "open",
    priority: str = "normal",
    owner_type: str = "unassigned",
    owner_id: str | None = None,
    created_by_turn_id: str | None = None,
    due_at: int | None = None,
    metadata: dict | None = None,
    actor_type: str = "agent",
    actor_id: str = "bluebot-admin-agent",
    event_note: str = "",
    evidence: dict | None = None,
) -> dict:
    """Create a ticket and its initial append-only timeline event."""
    _ensure_ready()
    user_id = _clean_required_text(user_id, "user_id", max_len=240)
    title = _clean_required_text(title, "title", max_len=160)
    success_criteria = _clean_required_text(
        success_criteria, "success_criteria", max_len=500
    )
    description = _clean_optional_text(description, max_len=2_000) or ""
    status = _validate_choice(status or "open", TICKET_STATUSES, "status")
    priority = _validate_choice(priority or "normal", TICKET_PRIORITIES, "priority")
    owner_type = _validate_choice(
        owner_type or "unassigned", TICKET_OWNER_TYPES, "owner_type"
    )
    owner_id = _clean_optional_text(owner_id, max_len=240) or ""
    conversation_id = _clean_optional_text(conversation_id, max_len=120)
    serial_number = _clean_optional_text(serial_number, max_len=80)
    created_by_turn_id = _clean_optional_text(created_by_turn_id, max_len=120)
    metadata = _json_dict(metadata, "metadata")
    evidence = _json_dict(evidence, "evidence")
    now = int(time.time())
    closed_at = _ticket_closed_at(status)
    for _ in range(10):
        ticket_id = _new_ticket_id()
        try:
            with _conn() as (conn, cur):
                cur.execute(
                    _q(
                        "INSERT INTO tickets (id, user_id, conversation_id, serial_number, "
                        "title, description, success_criteria, status, priority, owner_type, "
                        "owner_id, created_by_turn_id, due_at, closed_at, metadata_json, "
                        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    ),
                    (
                        ticket_id,
                        user_id,
                        conversation_id,
                        serial_number,
                        title,
                        description,
                        success_criteria,
                        status,
                        priority,
                        owner_type,
                        owner_id,
                        created_by_turn_id,
                        int(due_at) if due_at is not None else None,
                        closed_at,
                        json.dumps(metadata, sort_keys=True, default=str),
                        now,
                        now,
                    ),
                )
                cur.execute(
                    _q(
                        "INSERT INTO ticket_events (ticket_id, event_type, actor_type, "
                        "actor_id, note, turn_id, evidence_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    ),
                    (
                        ticket_id,
                        "created",
                        actor_type or "agent",
                        actor_id or "",
                        event_note or "Ticket created.",
                        created_by_turn_id,
                        json.dumps(evidence, sort_keys=True, default=str),
                        now,
                    ),
                )
            return get_ticket(ticket_id, user_id) or {}
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                continue
            raise
    raise RuntimeError("Failed to generate a unique ticket ID")


def get_ticket(ticket_id: str, user_id: str) -> dict | None:
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT * FROM tickets WHERE id = ? AND user_id = ?"),
            (ticket_id, user_id),
        )
        row = cur.fetchone()
    return _ticket_from_row(row) if row is not None else None


def list_tickets(
    user_id: str,
    *,
    conversation_id: str | None = None,
    serial_number: str | None = None,
    status: str | list[str] | tuple[str, ...] | set[str] | None = None,
    limit: int = 100,
) -> list[dict]:
    """List tickets scoped to one admin user, newest first."""
    _ensure_ready()
    user_id = _clean_required_text(user_id, "user_id", max_len=240)
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    conversation_id = _clean_optional_text(conversation_id, max_len=120)
    serial_number = _clean_optional_text(serial_number, max_len=80)
    if conversation_id:
        clauses.append("conversation_id = ?")
        params.append(conversation_id)
    if serial_number:
        clauses.append("serial_number = ?")
        params.append(serial_number)
    if status:
        raw_statuses = (
            [status]
            if isinstance(status, str)
            else list(status)
        )
        statuses = [
            _validate_choice(str(s), TICKET_STATUSES, "status")
            for s in raw_statuses
            if str(s).strip()
        ]
        if statuses:
            clauses.append("status IN (" + ", ".join("?" for _ in statuses) + ")")
            params.extend(statuses)
    max_rows = max(1, min(int(limit or 100), 500))
    params.append(max_rows)
    sql = (
        "SELECT * FROM tickets WHERE "
        + " AND ".join(clauses)
        + " ORDER BY updated_at DESC, created_at DESC LIMIT ?"
    )
    with _conn() as (conn, cur):
        cur.execute(_q(sql), tuple(params))
        rows = cur.fetchall()
    return [_ticket_from_row(r) for r in rows]


def list_ticket_events(ticket_id: str, user_id: str) -> list[dict]:
    _ensure_ready()
    if get_ticket(ticket_id, user_id) is None:
        return []
    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "SELECT * FROM ticket_events WHERE ticket_id = ? "
                "ORDER BY id ASC"
            ),
            (ticket_id,),
        )
        rows = cur.fetchall()
    return [_ticket_event_from_row(r) for r in rows]


def append_ticket_event(
    *,
    ticket_id: str,
    user_id: str,
    event_type: str,
    actor_type: str = "agent",
    actor_id: str = "",
    note: str = "",
    turn_id: str | None = None,
    evidence: dict | None = None,
) -> dict:
    """Append one timeline event to a ticket and bump the ticket updated time."""
    _ensure_ready()
    if get_ticket(ticket_id, user_id) is None:
        raise LookupError("Ticket not found")
    event_type = _clean_required_text(event_type, "event_type", max_len=80)
    actor_type = _clean_optional_text(actor_type, max_len=40) or "agent"
    actor_id = _clean_optional_text(actor_id, max_len=240) or ""
    note = _clean_optional_text(note, max_len=2_000) or ""
    turn_id = _clean_optional_text(turn_id, max_len=120)
    evidence = _json_dict(evidence, "evidence")
    now = int(time.time())
    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "INSERT INTO ticket_events (ticket_id, event_type, actor_type, "
                "actor_id, note, turn_id, evidence_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            ),
            (
                ticket_id,
                event_type,
                actor_type,
                actor_id,
                note,
                turn_id,
                json.dumps(evidence, sort_keys=True, default=str),
                now,
            ),
        )
        cur.execute(
            _q("UPDATE tickets SET updated_at = ? WHERE id = ? AND user_id = ?"),
            (now, ticket_id, user_id),
        )
        cur.execute(
            _q(
                "SELECT * FROM ticket_events WHERE ticket_id = ? "
                "ORDER BY id DESC LIMIT 1"
            ),
            (ticket_id,),
        )
        row = cur.fetchone()
    return _ticket_event_from_row(row) if row is not None else {}


def update_ticket(
    *,
    ticket_id: str,
    user_id: str,
    updates: dict[str, Any],
    actor_type: str = "agent",
    actor_id: str = "bluebot-admin-agent",
    note: str = "",
    turn_id: str | None = None,
    evidence: dict | None = None,
) -> dict:
    """Update mutable ticket fields and append a compact timeline event."""
    _ensure_ready()
    current = get_ticket(ticket_id, user_id)
    if current is None:
        raise LookupError("Ticket not found")
    allowed = {
        "title",
        "description",
        "success_criteria",
        "status",
        "priority",
        "owner_type",
        "owner_id",
        "due_at",
        "metadata",
        "serial_number",
    }
    clean: dict[str, Any] = {}
    for key, value in (updates or {}).items():
        if key not in allowed or value is None:
            continue
        if key == "title":
            clean[key] = _clean_required_text(value, "title", max_len=160)
        elif key == "description":
            clean[key] = _clean_optional_text(value, max_len=2_000) or ""
        elif key == "success_criteria":
            clean[key] = _clean_required_text(
                value, "success_criteria", max_len=500
            )
        elif key == "status":
            clean[key] = _validate_choice(str(value), TICKET_STATUSES, "status")
        elif key == "priority":
            clean[key] = _validate_choice(str(value), TICKET_PRIORITIES, "priority")
        elif key == "owner_type":
            clean[key] = _validate_choice(str(value), TICKET_OWNER_TYPES, "owner_type")
        elif key == "metadata":
            clean[key] = _json_dict(value, "metadata")
        elif key == "due_at":
            clean[key] = int(value) if value not in ("", None) else None
        else:
            clean[key] = _clean_optional_text(value, max_len=240) or ""
    if not clean:
        return current

    if clean.get("status") == "resolved" and not note and not evidence:
        raise ValueError("Resolving a ticket requires a note or evidence")

    now = int(time.time())
    db_fields: dict[str, Any] = {}
    for key, value in clean.items():
        if key == "metadata":
            db_fields["metadata_json"] = json.dumps(value, sort_keys=True, default=str)
        else:
            db_fields[key] = value
    if "status" in clean:
        db_fields["closed_at"] = _ticket_closed_at(clean["status"])
    db_fields["updated_at"] = now

    assignments = ", ".join(f"{k} = ?" for k in db_fields)
    params = list(db_fields.values()) + [ticket_id, user_id]
    with _conn() as (conn, cur):
        cur.execute(
            _q(f"UPDATE tickets SET {assignments} WHERE id = ? AND user_id = ?"),
            tuple(params),
        )
    event_type = "updated"
    if "status" in clean:
        event_type = f"status:{clean['status']}"
    elif "owner_type" in clean or "owner_id" in clean:
        event_type = "owner_updated"
    append_ticket_event(
        ticket_id=ticket_id,
        user_id=user_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        note=note,
        turn_id=turn_id,
        evidence=evidence,
    )
    return get_ticket(ticket_id, user_id) or {}


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
    """True if *filename* appears in other conversations' messages or in any non-revoked share."""
    cur.execute(
        _q("SELECT 1 FROM messages WHERE conversation_id != ? AND content LIKE ? LIMIT 1"),
        (exclude_id, f"%{filename}%"),
    )
    if cur.fetchone() is not None:
        return True
    cur.execute(
        _q("SELECT 1 FROM shares WHERE messages_json LIKE ? AND revoked = 0 LIMIT 1"),
        (f"%{filename}%",),
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


# ---------------------------------------------------------------------------
# Public share links (read-only snapshot)
# ---------------------------------------------------------------------------


def create_share(conversation_id: str, user_id: str) -> str:
    """
    Snapshot the conversation into a new share row. Verifies *user_id* owns
    the conversation. Returns a 32-hex *token* for public URLs.
    """
    _ensure_ready()
    messages = load_messages(conversation_id)
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT id, title FROM conversations WHERE id = ? AND user_id = ?"),
            (conversation_id, user_id),
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError("Conversation not found or access denied")
        title_raw = row["title"]
        title = (title_raw or "") if title_raw is not None else ""
    token = uuid.uuid4().hex
    now = int(time.time())
    messages_json = json.dumps(messages, default=str)
    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "INSERT INTO shares (token, conversation_id, owner_user_id, title, "
                "messages_json, created_at, revoked) VALUES (?, ?, ?, ?, ?, ?, 0)"
            ),
            (token, conversation_id, user_id, title, messages_json, now),
        )
    return token


def load_share(token: str) -> dict[str, Any] | None:
    """
    Return ``{title, messages, revoked}`` for *token*, or ``None`` if missing.
    """
    _ensure_ready()
    t = (token or "").strip()
    if not t or len(t) < 8:
        return None
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT title, messages_json, revoked FROM shares WHERE token = ?"),
            (t,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    revoked = int(row["revoked"])
    return {
        "title": row["title"] or "",
        "messages": json.loads(row["messages_json"]),
        "revoked": bool(revoked),
    }


def revoke_share(token: str, user_id: str) -> bool:
    """Set *revoked* on a share if *user_id* is the owner. Returns whether a row was updated."""
    _ensure_ready()
    t = (token or "").strip()
    if not t:
        return False
    with _conn() as (conn, cur):
        cur.execute(
            _q("UPDATE shares SET revoked = 1 WHERE token = ? AND owner_user_id = ?"),
            (t, user_id),
        )
        n = cur.rowcount
    return bool(n and n > 0)


# ---------------------------------------------------------------------------
# Public sales conversations
# ---------------------------------------------------------------------------

_SALES_SHARE_OWNER_PREFIX = "public-sales:"


def create_sales_share(conversation_id: str) -> tuple[str, str]:
    """
    Snapshot a public sales conversation into the generic share table.

    Returns ``(token, revoke_key)``. Public sales chat is pre-login, so the
    revoke key acts as the browser-held ownership secret for the generated
    snapshot link.
    """
    _ensure_ready()
    messages = load_sales_messages(conversation_id)
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT id, title FROM sales_conversations WHERE id = ?"),
            (conversation_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise LookupError("Sales conversation not found")
        title_raw = row["title"]
        title = (title_raw or "") if title_raw is not None else ""
    if not messages:
        raise ValueError("Cannot share an empty sales conversation")

    token = uuid.uuid4().hex
    revoke_key = uuid.uuid4().hex
    now = int(time.time())
    messages_json = json.dumps(messages, default=str)
    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "INSERT INTO shares (token, conversation_id, owner_user_id, title, "
                "messages_json, created_at, revoked) VALUES (?, ?, ?, ?, ?, ?, 0)"
            ),
            (
                token,
                conversation_id,
                f"{_SALES_SHARE_OWNER_PREFIX}{revoke_key}",
                title or "Sales conversation",
                messages_json,
                now,
            ),
        )
    return token, revoke_key


def revoke_sales_share(token: str, revoke_key: str) -> bool:
    """Revoke a public sales share using the browser-held revoke key."""
    _ensure_ready()
    t = (token or "").strip()
    key = (revoke_key or "").strip()
    if not t or not key:
        return False
    with _conn() as (conn, cur):
        cur.execute(
            _q("UPDATE shares SET revoked = 1 WHERE token = ? AND owner_user_id = ?"),
            (t, f"{_SALES_SHARE_OWNER_PREFIX}{key}"),
        )
        n = cur.rowcount
    return bool(n and n > 0)


def create_sales_conversation(title: str = "") -> str:
    """Create a public pre-login sales conversation and return its short ID."""
    _ensure_ready()
    now = int(time.time())
    for _ in range(10):
        # Public pre-login conversations are bearerless, so use an unguessable
        # id rather than the short authenticated-support conversation id.
        conv_id = uuid.uuid4().hex
        try:
            with _conn() as (conn, cur):
                cur.execute(
                    _q(
                        "INSERT INTO sales_conversations "
                        "(id, title, lead_summary_json, created_at, updated_at) "
                        "VALUES (?, ?, '{}', ?, ?)"
                    ),
                    (conv_id, title, now, now),
                )
            return conv_id
        except Exception as exc:
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                continue
            raise
    raise RuntimeError("Failed to generate a unique sales conversation ID")


def list_sales_conversations(conversation_ids: list[str]) -> list[dict]:
    """
    Return public sales conversations for the caller-supplied id list.

    Public sales chat has no Auth0 user, so the browser owns its local list
    of conversation ids. The API returns only those explicit ids instead of
    exposing every anonymous sales conversation on the server.
    """
    _ensure_ready()
    ids = [str(x).strip() for x in conversation_ids if str(x).strip()]
    if not ids:
        return []
    placeholders = ", ".join([_ph()] * len(ids))
    sql = f"""
        SELECT c.id, c.title, c.created_at, c.updated_at,
               COUNT(m.id) AS message_count
        FROM   sales_conversations c
        LEFT JOIN sales_messages m
               ON m.conversation_id = c.id AND m.role = 'user'
        WHERE  c.id IN ({placeholders})
        GROUP  BY c.id, c.title, c.created_at, c.updated_at
    """
    with _conn() as (conn, cur):
        cur.execute(_q(sql), tuple(ids))
        rows = [dict(r) for r in cur.fetchall()]
    order = {cid: ix for ix, cid in enumerate(ids)}
    rows.sort(key=lambda r: order.get(str(r.get("id")), len(order)))
    return rows


def sales_conversation_exists(conversation_id: str) -> bool:
    """Return whether a public sales conversation exists."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT 1 FROM sales_conversations WHERE id = ?"),
            (conversation_id,),
        )
        return cur.fetchone() is not None


def set_sales_title(conversation_id: str, title: str) -> None:
    """Set or update a public sales conversation title."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("UPDATE sales_conversations SET title = ?, updated_at = ? WHERE id = ?"),
            (title, int(time.time()), conversation_id),
        )


def load_sales_messages(conversation_id: str) -> list[dict]:
    """Load public sales conversation messages."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT role, content FROM sales_messages WHERE conversation_id = ? ORDER BY id"),
            (conversation_id,),
        )
        rows = cur.fetchall()
    return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]


def delete_sales_conversation(conversation_id: str) -> None:
    """Delete a public sales conversation by unguessable id."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("DELETE FROM sales_messages WHERE conversation_id = ?"),
            (conversation_id,),
        )
        cur.execute(
            _q("DELETE FROM sales_conversations WHERE id = ?"),
            (conversation_id,),
        )


def append_sales_messages(conversation_id: str, messages: list[dict]) -> None:
    """Persist new public sales messages."""
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
            _q(
                "INSERT INTO sales_messages "
                "(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)"
            ),
            rows,
        )
        cur.execute(
            _q("UPDATE sales_conversations SET updated_at = ? WHERE id = ?"),
            (now, conversation_id),
        )


def load_sales_lead_summary(conversation_id: str) -> dict[str, Any]:
    """Return the structured lead summary for a public sales conversation."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q("SELECT lead_summary_json FROM sales_conversations WHERE id = ?"),
            (conversation_id,),
        )
        row = cur.fetchone()
    if row is None:
        return {}
    try:
        parsed = json.loads(row["lead_summary_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def update_sales_lead_summary(conversation_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Merge and persist a structured public-sales lead summary."""
    _ensure_ready()
    current = load_sales_lead_summary(conversation_id)
    merged = {**current}
    for key, value in (summary or {}).items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and not value:
            continue
        if isinstance(value, dict):
            existing = merged.get(key)
            if isinstance(existing, dict):
                merged[key] = {**existing, **value}
            elif value:
                merged[key] = value
            continue
        merged[key] = value
    now = int(time.time())
    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "UPDATE sales_conversations "
                "SET lead_summary_json = ?, updated_at = ? WHERE id = ?"
            ),
            (json.dumps(merged, default=str), now, conversation_id),
        )
    return merged


def _payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_sales_content_records(record_type: str) -> list[dict[str, Any]]:
    """Load synced sales KB/catalog payloads for a record type."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "SELECT payload_json FROM sales_content_records "
                "WHERE record_type = ? AND extraction_status IN ('ok', 'snapshot') "
                "ORDER BY record_id"
            ),
            (record_type,),
        )
        rows = cur.fetchall()
    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def load_sales_content_record_metadata(
    record_type: str,
    record_id: str,
) -> dict[str, Any] | None:
    """Return sync metadata for a single sales content record."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "SELECT record_type, record_id, source_url, domain, title, content_hash, "
                "last_fetched_at, last_changed_at, extraction_status, "
                "validation_errors_json, updated_at "
                "FROM sales_content_records WHERE record_type = ? AND record_id = ?"
            ),
            (record_type, record_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    meta = dict(row)
    try:
        errors = json.loads(meta.get("validation_errors_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        errors = []
    meta["validation_errors"] = errors if isinstance(errors, list) else []
    meta.pop("validation_errors_json", None)
    return meta


def upsert_sales_content_record(
    record_type: str,
    record_id: str,
    payload: dict[str, Any],
    *,
    source_url: str = "",
    domain: str = "",
    title: str = "",
    content_hash: str = "",
    last_fetched_at: int | None = None,
    extraction_status: str = "ok",
    validation_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Insert/update a synced sales content payload while preserving change time."""
    _ensure_ready()
    now = int(time.time())
    fetched_at = int(last_fetched_at or now)
    clean_payload = payload if isinstance(payload, dict) else {}
    clean_hash = content_hash or _payload_hash(clean_payload)
    errors = validation_errors or []

    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "SELECT content_hash, last_changed_at FROM sales_content_records "
                "WHERE record_type = ? AND record_id = ?"
            ),
            (record_type, record_id),
        )
        existing = cur.fetchone()
        if existing is not None and existing["content_hash"] == clean_hash:
            changed_at = int(existing["last_changed_at"])
        else:
            changed_at = now

        cur.execute(
            _q(
                "INSERT INTO sales_content_records "
                "(record_type, record_id, payload_json, source_url, domain, title, "
                "content_hash, last_fetched_at, last_changed_at, extraction_status, "
                "validation_errors_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (record_type, record_id) DO UPDATE SET "
                "payload_json = excluded.payload_json, "
                "source_url = excluded.source_url, "
                "domain = excluded.domain, "
                "title = excluded.title, "
                "content_hash = excluded.content_hash, "
                "last_fetched_at = excluded.last_fetched_at, "
                "last_changed_at = excluded.last_changed_at, "
                "extraction_status = excluded.extraction_status, "
                "validation_errors_json = excluded.validation_errors_json, "
                "updated_at = excluded.updated_at"
            ),
            (
                record_type,
                record_id,
                json.dumps(clean_payload, default=str),
                source_url,
                domain,
                title,
                clean_hash,
                fetched_at,
                changed_at,
                extraction_status,
                json.dumps(errors, default=str),
                now,
            ),
        )
    return {
        "record_type": record_type,
        "record_id": record_id,
        "content_hash": clean_hash,
        "last_fetched_at": fetched_at,
        "last_changed_at": changed_at,
        "extraction_status": extraction_status,
        "validation_errors": errors,
    }


def record_sales_content_sync_event(
    source_url: str,
    *,
    domain: str = "",
    status: str,
    message: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record a sales content sync success or failure for observability."""
    _ensure_ready()
    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "INSERT INTO sales_content_sync_events "
                "(source_url, domain, status, message, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ),
            (
                source_url,
                domain,
                status,
                message,
                json.dumps(metadata or {}, default=str),
                int(time.time()),
            ),
        )


def list_sales_content_sync_events(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent sales content sync events."""
    _ensure_ready()
    safe_limit = max(1, min(int(limit or 20), 200))
    with _conn() as (conn, cur):
        cur.execute(
            _q(
                "SELECT source_url, domain, status, message, metadata_json, created_at "
                "FROM sales_content_sync_events ORDER BY created_at DESC, id DESC LIMIT ?"
            ),
            (safe_limit,),
        )
        rows = cur.fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        event = dict(row)
        try:
            metadata = json.loads(event.get("metadata_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        event["metadata"] = metadata if isinstance(metadata, dict) else {}
        event.pop("metadata_json", None)
        events.append(event)
    return events
