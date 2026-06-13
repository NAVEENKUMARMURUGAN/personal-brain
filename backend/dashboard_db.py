"""
dashboard_db.py — SQLite DDL and CRUD helpers for all dashboard tables.

Tables created here:
  feed_items      — curated news + learning items (shared table, kind column)
  feed_raw        — raw fetched payloads before curation (dedup / replay)
  repo_snapshots  — daily star counts per repo (for velocity calculation)
  repo_trends     — curated top-6 trending repos per cycle
  learning_cards  — spaced-repetition concept cards
  special_dates   — per-user personal dates (birthdays, anniversaries)
  special_today   — daily curated 2-3 global special items
  briefings       — per-user daily briefing paragraphs
  transit_alerts  — cached transit alerts from TfNSW
  bookmarks       — user-saved feed items

Migration pattern: ALTER TABLE ... ADD COLUMN inside try/except (column already exists = silent).
Connection pattern: get_conn() returns sqlite3.Row-enabled connection; caller does try/finally close.
"""

import os
import sqlite3
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

SQLITE_PATH = os.getenv("SQLITE_PATH", "/app/history.db")

# Ensure parent directory exists (best-effort; may fail in read-only envs like test runners)
try:
    _db_dir = os.path.dirname(os.path.abspath(SQLITE_PATH))
    os.makedirs(_db_dir, exist_ok=True)
except OSError:
    pass


def get_conn() -> sqlite3.Connection:
    """Return a sqlite3 connection with Row factory enabled."""
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_dashboard_tables() -> None:
    """Create all dashboard tables if they do not already exist. Idempotent."""
    conn = get_conn()
    try:
        conn.executescript("""
            -- Curated feed items: news + learning share one table
            CREATE TABLE IF NOT EXISTS feed_items (
                id           TEXT PRIMARY KEY,
                kind         TEXT NOT NULL CHECK(kind IN ('news', 'learning')),
                rank         INTEGER,
                title        TEXT NOT NULL,
                summary_short TEXT,
                summary_detail TEXT,
                tag          TEXT,
                media_type   TEXT DEFAULT 'article',
                duration_min INTEGER,
                source_name  TEXT,
                source_url   TEXT,
                published_at TEXT,
                score        REAL,
                cycle_date   TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );

            -- Raw fetched items before curation (debugging / replay)
            CREATE TABLE IF NOT EXISTS feed_raw (
                id           TEXT PRIMARY KEY,
                source       TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                content_hash TEXT UNIQUE,
                fetched_at   TEXT NOT NULL
            );

            -- Daily star snapshots per repo
            CREATE TABLE IF NOT EXISTS repo_snapshots (
                id        TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                stars     INTEGER NOT NULL,
                snap_date TEXT NOT NULL,
                UNIQUE(full_name, snap_date)
            );

            -- Curated trending repos per cycle
            CREATE TABLE IF NOT EXISTS repo_trends (
                id              TEXT PRIMARY KEY,
                full_name       TEXT NOT NULL,
                description     TEXT,
                language        TEXT,
                stars_gained_7d INTEGER DEFAULT 0,
                why_it_matters  TEXT,
                cycle_date      TEXT NOT NULL
            );

            -- Spaced-repetition concept cards
            CREATE TABLE IF NOT EXISTS learning_cards (
                id           TEXT PRIMARY KEY,
                term         TEXT NOT NULL,
                explanation  TEXT NOT NULL,
                usage_line   TEXT,
                code_example TEXT,
                pathway_node TEXT NOT NULL,
                last_shown   TEXT,
                ease         REAL NOT NULL DEFAULT 2.5,
                times_seen   INTEGER NOT NULL DEFAULT 0,
                mastered     INTEGER NOT NULL DEFAULT 0
            );

            -- Per-user personal special dates
            CREATE TABLE IF NOT EXISTS special_dates (
                id           TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                label        TEXT NOT NULL,
                date_monthday TEXT NOT NULL,
                kind         TEXT NOT NULL DEFAULT 'personal',
                note         TEXT
            );

            -- Daily curated global special items (2-3 picks)
            CREATE TABLE IF NOT EXISTS special_today (
                id          TEXT PRIMARY KEY,
                cycle_date  TEXT NOT NULL UNIQUE,
                items_json  TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );

            -- Per-user daily briefing paragraphs
            CREATE TABLE IF NOT EXISTS briefings (
                id           TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                cycle_date   TEXT NOT NULL,
                text         TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                UNIQUE(user_id, cycle_date)
            );

            -- Cached transit alerts from TfNSW
            CREATE TABLE IF NOT EXISTS transit_alerts (
                id         TEXT PRIMARY KEY,
                line       TEXT NOT NULL,
                severity   TEXT NOT NULL DEFAULT 'normal',
                title      TEXT NOT NULL,
                detail     TEXT,
                starts_at  TEXT,
                ends_at    TEXT,
                fetched_at TEXT NOT NULL
            );

            -- User-saved feed items (bookmarks)
            CREATE TABLE IF NOT EXISTS bookmarks (
                id           TEXT PRIMARY KEY,
                user_id      TEXT NOT NULL,
                feed_item_id TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                UNIQUE(user_id, feed_item_id)
            );
        """)
        conn.commit()
        logger.info("Dashboard tables ensured")
    finally:
        conn.close()


# ── feed_items ─────────────────────────────────────────────────

def get_feed_items(kind: str, cycle_date: str) -> list[dict]:
    """Return all curated feed items for a given kind and cycle date, ordered by rank."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM feed_items WHERE kind = ? AND cycle_date = ? ORDER BY rank ASC",
            (kind, cycle_date),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_feed_items(items: list[dict]) -> None:
    """Insert or replace feed items. Each item dict must include all required fields."""
    conn = get_conn()
    try:
        for item in items:
            conn.execute(
                """INSERT OR REPLACE INTO feed_items
                   (id, kind, rank, title, summary_short, summary_detail, tag,
                    media_type, duration_min, source_name, source_url,
                    published_at, score, cycle_date, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.get("id", str(uuid.uuid4())),
                    item["kind"], item.get("rank"), item["title"],
                    item.get("summary_short"), item.get("summary_detail"),
                    item.get("tag"), item.get("media_type", "article"),
                    item.get("duration_min"), item.get("source_name"),
                    item.get("source_url"), item.get("published_at"),
                    item.get("score"), item["cycle_date"],
                    item.get("created_at", datetime.now(timezone.utc).isoformat()),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def get_latest_feed_cycle(kind: str) -> Optional[str]:
    """Return the most recent cycle_date for a given kind, or None."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT cycle_date FROM feed_items WHERE kind = ? ORDER BY cycle_date DESC LIMIT 1",
            (kind,),
        ).fetchone()
        return row["cycle_date"] if row else None
    finally:
        conn.close()


# ── feed_raw ───────────────────────────────────────────────────

def raw_hash_exists(content_hash: str) -> bool:
    """Return True if a raw item with this content_hash already exists."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM feed_raw WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def insert_feed_raw(source: str, payload: dict, content_hash: str) -> None:
    """Insert a raw feed payload (idempotent on content_hash)."""
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO feed_raw (id, source, payload_json, content_hash, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), source, json.dumps(payload), content_hash,
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


# ── repo_snapshots + repo_trends ──────────────────────────────

def upsert_repo_snapshot(full_name: str, stars: int, snap_date: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO repo_snapshots (id, full_name, stars, snap_date)
               VALUES (?, ?, ?, ?)""",
            (str(uuid.uuid4()), full_name, stars, snap_date),
        )
        conn.commit()
    finally:
        conn.close()


def get_stars_7d_ago(full_name: str, today: str) -> Optional[int]:
    """Return star count from 7 days ago for a repo, or None if no snapshot."""
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT stars FROM repo_snapshots
               WHERE full_name = ? AND snap_date <= date(?, '-7 days')
               ORDER BY snap_date DESC LIMIT 1""",
            (full_name, today),
        ).fetchone()
        return row["stars"] if row else None
    finally:
        conn.close()


def upsert_repo_trends(items: list[dict], cycle_date: str) -> None:
    conn = get_conn()
    try:
        for item in items:
            conn.execute(
                """INSERT OR REPLACE INTO repo_trends
                   (id, full_name, description, language, stars_gained_7d, why_it_matters, cycle_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), item["full_name"], item.get("description"),
                 item.get("language"), item.get("stars_gained_7d", 0),
                 item.get("why_it_matters"), cycle_date),
            )
        conn.commit()
    finally:
        conn.close()


def get_repo_trends(cycle_date: str) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM repo_trends WHERE cycle_date = ? ORDER BY stars_gained_7d DESC",
            (cycle_date,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_latest_repo_cycle() -> Optional[str]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT cycle_date FROM repo_trends ORDER BY cycle_date DESC LIMIT 1"
        ).fetchone()
        return row["cycle_date"] if row else None
    finally:
        conn.close()


# ── learning_cards ─────────────────────────────────────────────

def get_concept_of_day() -> Optional[dict]:
    """
    Spaced-repetition selection:
    1. Prefer cards that are due (last_shown + interval(ease) in the past).
    2. Among due cards, pick the one with lowest ease (hardest).
    3. Fallback: lowest times_seen, not yet mastered.
    """
    conn = get_conn()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        # Due cards: last_shown + (ease * 24h) < now, not mastered
        row = conn.execute(
            """SELECT * FROM learning_cards
               WHERE mastered = 0
                 AND (last_shown IS NULL
                      OR datetime(last_shown, '+' || CAST(ROUND(ease) AS TEXT) || ' days') < ?)
               ORDER BY ease ASC, times_seen ASC
               LIMIT 1""",
            (now_iso,),
        ).fetchone()
        if not row:
            # Fallback: pick least-seen non-mastered card
            row = conn.execute(
                "SELECT * FROM learning_cards WHERE mastered = 0 ORDER BY times_seen ASC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_learning_card_review(card_id: str, result: str) -> Optional[dict]:
    """
    Apply spaced-repetition update.
    result = 'knew_it' — raises ease toward 4.0, increments times_seen; mastered after 3+ with ease >= 3.5
    result = 'show_again' — lowers ease toward 1.3, resets last_shown to now
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM learning_cards WHERE id = ?", (card_id,)
        ).fetchone()
        if not row:
            return None
        ease = row["ease"]
        times_seen = row["times_seen"]
        mastered = row["mastered"]
        now_iso = datetime.now(timezone.utc).isoformat()

        if result == "knew_it":
            ease = min(4.0, ease + 0.15)
            times_seen += 1
            # Mastered after 3 consecutive "knew_it" with ease >= 3.5
            if times_seen >= 3 and ease >= 3.5:
                mastered = 1
        else:  # show_again
            ease = max(1.3, ease - 0.3)
            times_seen = max(0, times_seen - 1)

        conn.execute(
            """UPDATE learning_cards
               SET ease = ?, times_seen = ?, mastered = ?, last_shown = ?
               WHERE id = ?""",
            (ease, times_seen, mastered, now_iso, card_id),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM learning_cards WHERE id = ?", (card_id,)
        ).fetchone()
        return dict(updated) if updated else None
    finally:
        conn.close()


def insert_learning_cards(cards: list[dict]) -> int:
    """Bulk insert learning cards. Returns count inserted."""
    conn = get_conn()
    inserted = 0
    try:
        for card in cards:
            try:
                conn.execute(
                    """INSERT INTO learning_cards
                       (id, term, explanation, usage_line, code_example, pathway_node,
                        last_shown, ease, times_seen, mastered)
                       VALUES (?, ?, ?, ?, ?, ?, NULL, 2.5, 0, 0)""",
                    (str(uuid.uuid4()), card["term"], card["explanation"],
                     card.get("usage_line"), card.get("code_example"),
                     card["pathway_node"]),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                pass  # duplicate term
        conn.commit()
        return inserted
    finally:
        conn.close()


def count_learning_cards() -> int:
    conn = get_conn()
    try:
        row = conn.execute("SELECT COUNT(*) as n FROM learning_cards").fetchone()
        return row["n"] if row else 0
    finally:
        conn.close()


# ── special_today ──────────────────────────────────────────────

def get_special_today(cycle_date: str) -> Optional[list]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT items_json FROM special_today WHERE cycle_date = ?", (cycle_date,)
        ).fetchone()
        return json.loads(row["items_json"]) if row else None
    finally:
        conn.close()


def upsert_special_today(cycle_date: str, items: list) -> None:
    conn = get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO special_today (id, cycle_date, items_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (str(uuid.uuid4()), cycle_date, json.dumps(items),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_user_special_dates(user_id: str, month_day: str) -> list[dict]:
    """Return personal special dates matching MM-DD for a user."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM special_dates WHERE user_id = ? AND date_monthday = ?",
            (user_id, month_day),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── briefings ──────────────────────────────────────────────────

def get_briefing(user_id: str, cycle_date: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM briefings WHERE user_id = ? AND cycle_date = ?",
            (user_id, cycle_date),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_briefing(user_id: str, cycle_date: str, text: str) -> dict:
    conn = get_conn()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT OR REPLACE INTO briefings (id, user_id, cycle_date, text, generated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), user_id, cycle_date, text, now_iso),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM briefings WHERE user_id = ? AND cycle_date = ?",
            (user_id, cycle_date),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


# ── transit_alerts ─────────────────────────────────────────────

def get_transit_alerts() -> list[dict]:
    """Return all currently active (non-expired) transit alerts."""
    conn = get_conn()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            """SELECT * FROM transit_alerts
               WHERE ends_at IS NULL OR ends_at > ?
               ORDER BY severity DESC, starts_at ASC""",
            (now_iso,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def replace_transit_alerts(alerts: list[dict]) -> None:
    """Delete all transit alerts and insert fresh ones atomically."""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM transit_alerts")
        now_iso = datetime.now(timezone.utc).isoformat()
        for alert in alerts:
            conn.execute(
                """INSERT INTO transit_alerts
                   (id, line, severity, title, detail, starts_at, ends_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), alert.get("line", "T1"),
                 alert.get("severity", "normal"),
                 alert["title"], alert.get("detail"),
                 alert.get("starts_at"), alert.get("ends_at"), now_iso),
            )
        conn.commit()
    finally:
        conn.close()


# ── bookmarks ──────────────────────────────────────────────────

def create_bookmark(user_id: str, feed_item_id: str) -> Optional[dict]:
    """Create a bookmark; return the bookmark dict, or None if already exists."""
    conn = get_conn()
    try:
        bid = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            conn.execute(
                """INSERT INTO bookmarks (id, user_id, feed_item_id, created_at)
                   VALUES (?, ?, ?, ?)""",
                (bid, user_id, feed_item_id, now_iso),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Already bookmarked — return existing
            row = conn.execute(
                "SELECT * FROM bookmarks WHERE user_id = ? AND feed_item_id = ?",
                (user_id, feed_item_id),
            ).fetchone()
            return dict(row) if row else None
        return {"id": bid, "user_id": user_id, "feed_item_id": feed_item_id, "created_at": now_iso}
    finally:
        conn.close()


def get_bookmarked_item_ids(user_id: str) -> set[str]:
    """Return the set of feed_item_ids bookmarked by a user."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT feed_item_id FROM bookmarks WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {r["feed_item_id"] for r in rows}
    finally:
        conn.close()


def get_feed_item_by_id(feed_item_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM feed_items WHERE id = ?", (feed_item_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── weekly stats ───────────────────────────────────────────────

def compute_weekly_stats(user_id: str) -> dict:
    """
    Compute dashboard footer stats. tasks_done_7d comes from Qdrant (handled in resolver).
    articles_saved and cards_mastered come from SQLite.
    day_streak is a simple consecutive-day briefing streak.
    """
    conn = get_conn()
    try:
        articles_saved = conn.execute(
            "SELECT COUNT(*) as n FROM bookmarks WHERE user_id = ?", (user_id,)
        ).fetchone()["n"]

        cards_mastered = conn.execute(
            "SELECT COUNT(*) as n FROM learning_cards WHERE mastered = 1"
        ).fetchone()["n"]

        # Day streak: count consecutive days with a briefing
        rows = conn.execute(
            """SELECT cycle_date FROM briefings WHERE user_id = ?
               ORDER BY cycle_date DESC LIMIT 30""",
            (user_id,),
        ).fetchall()
        streak = 0
        from datetime import date, timedelta
        today = date.today()
        for i, row in enumerate(rows):
            expected = (today - timedelta(days=i)).isoformat()
            if row["cycle_date"] == expected:
                streak += 1
            else:
                break

        return {
            "tasksDone7d": 0,       # filled in by resolver from Qdrant
            "articlesSaved": articles_saved,
            "cardsMastered": cards_mastered,
            "dayStreak": streak,
        }
    finally:
        conn.close()
