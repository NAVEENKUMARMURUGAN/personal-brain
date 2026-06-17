"""
explore_db.py — SQLite cache for Topic Explorer explorations.

Each exploration is keyed by (user_id, topic_slug) and stores the full
Claude-generated JSON payload. Cache is hit on every page load;
force=True in the pipeline bypasses it.
"""

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

SQLITE_PATH = os.getenv("SQLITE_PATH", "/app/history.db")


def _get_conn():
    os.makedirs(os.path.dirname(os.path.abspath(SQLITE_PATH)), exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table() -> None:
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS topic_explorations (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                topic           TEXT NOT NULL,
                topic_slug      TEXT NOT NULL,
                content_json    TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                regenerated_at  TEXT
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_explore_user_slug
            ON topic_explorations(user_id, topic_slug)
        """)
        conn.commit()
    finally:
        conn.close()


def slugify(topic: str) -> str:
    """Convert topic to URL-safe slug: lowercase, hyphens, max 80 chars."""
    slug = topic.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:80]


def get_exploration(user_id: str, topic_slug: str) -> Optional[dict]:
    """Return cached exploration or None."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM topic_explorations WHERE user_id = ? AND topic_slug = ?",
            (user_id, topic_slug),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["content"] = json.loads(result["content_json"])
        return result
    finally:
        conn.close()


def upsert_exploration(user_id: str, topic: str, topic_slug: str, content: dict) -> dict:
    """Insert or replace an exploration. Returns the stored row."""
    conn = _get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        existing = conn.execute(
            "SELECT id FROM topic_explorations WHERE user_id = ? AND topic_slug = ?",
            (user_id, topic_slug),
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE topic_explorations
                   SET content_json = ?, regenerated_at = ?
                   WHERE user_id = ? AND topic_slug = ?""",
                (json.dumps(content), now, user_id, topic_slug),
            )
            row_id = existing["id"]
        else:
            row_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO topic_explorations
                   (id, user_id, topic, topic_slug, content_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (row_id, user_id, topic, topic_slug, json.dumps(content), now),
            )

        conn.commit()
        return {
            "id": row_id,
            "user_id": user_id,
            "topic": topic,
            "topic_slug": topic_slug,
            "content": content,
            "created_at": now,
            "regenerated_at": now if existing else None,
        }
    finally:
        conn.close()


def list_explorations(user_id: str, limit: int = 20) -> list[dict]:
    """Return recent explorations for a user (no content, just metadata)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            """SELECT id, topic, topic_slug, created_at, regenerated_at
               FROM topic_explorations WHERE user_id = ?
               ORDER BY COALESCE(regenerated_at, created_at) DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_exploration(user_id: str, topic_slug: str) -> bool:
    """Delete an exploration. Returns True if deleted."""
    conn = _get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM topic_explorations WHERE user_id = ? AND topic_slug = ?",
            (user_id, topic_slug),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
