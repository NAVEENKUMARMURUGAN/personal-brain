import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Optional

SQLITE_PATH = os.getenv("SQLITE_PATH", "/data/history.db")

# Ensure the directory exists before any connection attempt
os.makedirs(os.path.dirname(os.path.abspath(SQLITE_PATH)), exist_ok=True)


def get_conn():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db():
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                role TEXT NOT NULL,
                type TEXT NOT NULL DEFAULT 'text',
                payload TEXT,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def save_message(id: str, content: str, role: str, type: str = "text", payload: Optional[dict] = None) -> dict:
    conn = get_conn()
    try:
        created_at = datetime.now(timezone.utc).isoformat()
        payload_str = json.dumps(payload) if payload is not None else None
        conn.execute(
            "INSERT INTO messages (id, content, role, type, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (id, content, role, type, payload_str, created_at)
        )
        conn.commit()
        return {
            "id": id,
            "content": content,
            "role": role,
            "type": type,
            "payload": payload,
            "createdAt": created_at,
        }
    finally:
        conn.close()


def get_recent_turns(limit: int = 10, after: Optional[str] = None) -> list[dict]:
    """Return the last N messages formatted as Claude-compatible message dicts.

    Only includes text-type messages (skips task_list, category_list etc.)
    so the history fed to Claude is clean natural language turns.
    Returned in chronological order (oldest first).

    after: ISO timestamp — only return messages created after this time.
           Used to enforce a "cleared at" boundary so cleared sessions
           don't bleed prior history into new conversations.
    """
    conn = get_conn()
    try:
        if after:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE type = 'text' AND created_at > ? ORDER BY created_at DESC LIMIT ?",
                (after, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE type = 'text' ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
    finally:
        conn.close()

    # Reverse so oldest is first (Claude expects chronological order)
    turns = []
    for row in reversed(rows):
        role = row["role"]  # "user" or "assistant"
        turns.append({"role": role, "content": row["content"]})
    return turns


def get_messages(limit: int = 50, cursor: Optional[str] = None) -> dict:
    conn = get_conn()
    try:
        if cursor:
            rows = conn.execute(
                "SELECT * FROM messages WHERE created_at < ? ORDER BY created_at DESC LIMIT ?",
                (cursor, limit + 1)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM messages ORDER BY created_at DESC LIMIT ?",
                (limit + 1,)
            ).fetchall()

        has_next = len(rows) > limit
        rows = rows[:limit]

        messages = []
        for row in rows:
            payload = json.loads(row["payload"]) if row["payload"] else None
            messages.append({
                "id": row["id"],
                "content": row["content"],
                "role": row["role"],
                "type": row["type"],
                "payload": payload,
                "createdAt": row["created_at"],
            })

        next_cursor = rows[-1]["created_at"] if has_next and rows else None

        return {
            "messages": messages,
            "pageInfo": {
                "hasNextPage": has_next,
                "cursor": next_cursor,
            }
        }
    finally:
        conn.close()
