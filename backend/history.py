import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Optional

SQLITE_PATH = os.getenv("SQLITE_PATH", "/app/history.db")

# Ensure the parent directory exists before any connection attempt
_db_dir = os.path.dirname(os.path.abspath(SQLITE_PATH))
os.makedirs(_db_dir, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_db():
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id         TEXT PRIMARY KEY,
                content    TEXT NOT NULL,
                role       TEXT NOT NULL,
                type       TEXT NOT NULL DEFAULT 'text',
                payload    TEXT,
                created_at TEXT NOT NULL,
                user_id    TEXT
            )
        """)
        # Migrate existing DB — add user_id column if missing
        try:
            conn.execute("ALTER TABLE messages ADD COLUMN user_id TEXT")
        except Exception:
            pass  # column already exists
        conn.commit()
    finally:
        conn.close()


def save_message(
    id: str,
    content: str,
    role: str,
    type: str = "text",
    payload: Optional[dict] = None,
    user_id: Optional[str] = None,
) -> dict:
    conn = get_conn()
    try:
        created_at  = datetime.now(timezone.utc).isoformat()
        payload_str = json.dumps(payload) if payload is not None else None
        conn.execute(
            "INSERT INTO messages (id, content, role, type, payload, created_at, user_id) VALUES (?,?,?,?,?,?,?)",
            (id, content, role, type, payload_str, created_at, user_id)
        )
        conn.commit()
        return {"id": id, "content": content, "role": role, "type": type,
                "payload": payload, "createdAt": created_at}
    finally:
        conn.close()


def get_recent_turns(
    limit: int = 10,
    after: Optional[str] = None,
    user_id: Optional[str] = None,
) -> list[dict]:
    """Return the last N text messages for Claude context, scoped to a user."""
    conn = get_conn()
    try:
        conditions = ["type = 'text'"]
        params: list = []
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if after:
            conditions.append("created_at > ?")
            params.append(after)
        where = " AND ".join(conditions)
        params.append(limit)
        rows = conn.execute(
            f"SELECT role, content FROM messages WHERE {where} ORDER BY created_at DESC LIMIT ?",
            params
        ).fetchall()
    finally:
        conn.close()

    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def get_messages(
    limit: int = 50,
    cursor: Optional[str] = None,
    user_id: Optional[str] = None,
) -> dict:
    conn = get_conn()
    try:
        conditions: list[str] = []
        params: list = []
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if cursor:
            conditions.append("created_at < ?")
            params.append(cursor)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit + 1)
        rows = conn.execute(
            f"SELECT * FROM messages {where} ORDER BY created_at DESC LIMIT ?",
            params
        ).fetchall()

        has_next = len(rows) > limit
        rows     = rows[:limit]

        messages = []
        for row in rows:
            payload = json.loads(row["payload"]) if row["payload"] else None
            messages.append({
                "id":        row["id"],
                "content":   row["content"],
                "role":      row["role"],
                "type":      row["type"],
                "payload":   payload,
                "createdAt": row["created_at"],
            })

        next_cursor = rows[-1]["created_at"] if has_next and rows else None
        return {
            "messages": messages,
            "pageInfo": {"hasNextPage": has_next, "cursor": next_cursor},
        }
    finally:
        conn.close()
