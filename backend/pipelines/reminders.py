"""
pipelines/reminders.py — Telegram task reminder and overdue alert pipeline.

Two jobs:

1. OVERDUE ALERT — runs daily at 09:00 local (server time).
   For every user who has a linked Telegram account and overdue tasks,
   sends a concise Telegram message listing their overdue items.

2. DUE TODAY MORNING BRIEF — runs daily at 08:00 local.
   Sends each linked user a short list of tasks due today, so they
   start the day knowing what's on their plate.

Both jobs iterate all linked telegram_users, resolve their brain user_id,
query Qdrant for their tasks, and fire send_message() per user.
Jobs are idempotent — re-running won't double-send because APScheduler
tracks the last fire time.
"""

import os
import logging
from datetime import datetime, timezone, date, timedelta

logger = logging.getLogger(__name__)

FIXTURE_MODE = os.getenv("FIXTURE_MODE", "").lower() in ("1", "true", "yes")


def _get_all_linked_telegram_users() -> list[dict]:
    """Return all telegram_users that have a brain user_id linked."""
    import sqlite3
    sqlite_path = os.getenv("SQLITE_PATH", "/app/history.db")
    try:
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT telegram_id, user_id, first_name FROM telegram_users WHERE user_id IS NOT NULL AND user_id != ''"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("reminders: could not query telegram_users: %s", e)
        return []


def _get_tasks_for_user(user_id: str, target_date: str) -> dict:
    """Get pending + completed tasks for a user on target_date."""
    try:
        import tasks as tasks_module
        return tasks_module.get_tasks(target_date, user_id)
    except Exception as e:
        logger.error("reminders: get_tasks failed for user %s: %s", user_id[:8], e)
        return {"pending": [], "completed": []}


def _get_overdue_tasks(user_id: str) -> list[dict]:
    """Return all pending tasks with createdDate before today."""
    today_str = date.today().isoformat()
    try:
        import tasks as tasks_module
        # Scan last 30 days
        overdue = []
        for offset in range(1, 31):
            check_date = (date.today() - timedelta(days=offset)).isoformat()
            page = tasks_module.get_tasks(check_date, user_id)
            overdue.extend(page.get("pending", []))
        return overdue
    except Exception as e:
        logger.error("reminders: overdue check failed for user %s: %s", user_id[:8], e)
        return []


async def send_overdue_alerts() -> None:
    """
    Daily 09:00 job: send a Telegram message to every linked user who has overdue tasks.
    """
    if FIXTURE_MODE:
        logger.info("reminders: FIXTURE_MODE — skipping overdue alerts")
        return

    from telegram_bot import send_message  # lazy to avoid circular import at module load

    users = _get_all_linked_telegram_users()
    logger.info("reminders/overdue: checking %d linked users", len(users))

    for user in users:
        telegram_id = user["telegram_id"]
        brain_user_id = user["user_id"]
        first_name = user.get("first_name") or "there"

        try:
            overdue = _get_overdue_tasks(brain_user_id)
            if not overdue:
                continue

            lines = [f"⚠️ *Overdue tasks, {first_name}*\n"]
            for t in overdue[:10]:  # cap at 10 to avoid huge messages
                days_old = (date.today() - date.fromisoformat(t.get("createdDate", date.today().isoformat()))).days
                age = f"({days_old}d overdue)" if days_old > 0 else "(due today)"
                lines.append(f"• {t['content']} {age}")

            if len(overdue) > 10:
                lines.append(f"\n_...and {len(overdue) - 10} more._")

            lines.append("\n_Reply or open Personal Brain to act on these._")

            await send_message(telegram_id, "\n".join(lines))
            logger.info("reminders/overdue: sent alert to tg=%s (%d tasks)", telegram_id, len(overdue))

        except Exception as e:
            logger.error("reminders/overdue: failed for tg=%s: %s", telegram_id, e)


async def send_due_today_brief() -> None:
    """
    Daily 08:00 job: send each linked user their task list for today.
    Only fires if the user has at least one pending task today.
    """
    if FIXTURE_MODE:
        logger.info("reminders: FIXTURE_MODE — skipping due-today brief")
        return

    from telegram_bot import send_message

    today_str = date.today().isoformat()
    users = _get_all_linked_telegram_users()
    logger.info("reminders/due-today: checking %d linked users", len(users))

    for user in users:
        telegram_id  = user["telegram_id"]
        brain_user_id = user["user_id"]
        first_name   = user.get("first_name") or "there"

        try:
            page = _get_tasks_for_user(brain_user_id, today_str)
            pending = page.get("pending", [])
            if not pending:
                continue

            lines = [f"📋 *Your tasks for today, {first_name}*\n"]
            for t in pending[:15]:
                lines.append(f"• {t['content']}")
            if len(pending) > 15:
                lines.append(f"\n_...and {len(pending) - 15} more._")
            lines.append("\n_Good luck! ✓ them off as you go._")

            await send_message(telegram_id, "\n".join(lines))
            logger.info("reminders/due-today: sent to tg=%s (%d tasks)", telegram_id, len(pending))

        except Exception as e:
            logger.error("reminders/due-today: failed for tg=%s: %s", telegram_id, e)
