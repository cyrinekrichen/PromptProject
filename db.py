"""SQLite storage for chat sessions (built-in sqlite3, file sessions.db next to the code)."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).with_name("sessions.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,          -- user / assistant / tool
    content     TEXT NOT NULL DEFAULT '',
    tool_calls  TEXT,                   -- JSON list, only for assistant messages that call tools
    tool_name   TEXT,                   -- only for tool results
    created_at  TEXT NOT NULL
);
"""


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _connect():
    # A fresh connection per call keeps this safe to use from the UI's worker thread.
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def make_title(text, max_len=40):
    """Build a session title from the first user message (no model call)."""
    first_line = text.strip().splitlines()[0] if text.strip() else "New session"
    title = " ".join(first_line.split())
    return title if len(title) <= max_len else title[: max_len - 1] + "…"


def create_session(title):
    with _connect() as conn:
        now = _now()
        cur = conn.execute(
            "INSERT INTO sessions (title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        return cur.lastrowid


def list_sessions():
    """All sessions, most recently used first."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def load_messages(session_id):
    """All messages of a session in order, with tool_calls decoded from JSON."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
    messages = []
    for r in rows:
        m = dict(r)
        m["tool_calls"] = json.loads(m["tool_calls"]) if m["tool_calls"] else None
        messages.append(m)
    return messages


def add_message(session_id, role, content, tool_calls=None, tool_name=None):
    with _connect() as conn:
        now = _now()
        conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_calls, tool_name, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, role, content or "",
             json.dumps(tool_calls) if tool_calls else None, tool_name, now),
        )
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))


def rename_session(session_id, title):
    with _connect() as conn:
        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))


def delete_session(session_id):
    """Delete a session and all its messages."""
    with _connect() as conn:
        # SQLite doesn't enforce ON DELETE CASCADE unless foreign keys are enabled,
        # so remove the messages explicitly.
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
