"""
Telegram bot integration for Personal Brain.

Architecture:
- Telegram sends updates to POST /telegram/webhook
- Each message is processed by the same Claude agent loop used by the web app
- Users are identified by their Telegram user ID, linked to their brain account
- Voice messages are transcribed via Whisper (same as web)
- Reminders are scheduled via APScheduler and delivered back to Telegram

Setup:
  TELEGRAM_BOT_TOKEN   — from @BotFather
  TELEGRAM_ALLOWED_IDS — comma-separated Telegram user IDs allowed to use the bot
                         (leave empty to allow all, not recommended for personal use)

The bot maps Telegram user IDs to brain user_ids via SQLite.
On first message, a brain account is auto-created for the Telegram user.
"""

import os
import logging
import tempfile
import asyncio
from datetime import datetime, timezone, timedelta

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

import brain
import tasks as tasks_module
import history
from context_manager import ContextManager
import claude

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────

def _bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")

def _allowed_ids() -> set[str]:
    raw = os.getenv("TELEGRAM_ALLOWED_IDS", "")
    # Strip surrounding quotes Railway may add (e.g. '8842935233' → 8842935233)
    return {i.strip().strip("'\"") for i in raw.split(",") if i.strip().strip("'\"")}

TELEGRAM_API = "https://api.telegram.org/bot{token}"

# ── APScheduler for reminders ──────────────────────────────────

scheduler = AsyncIOScheduler(timezone="UTC")


def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        logger.info("APScheduler started")


# ── Telegram API helpers ───────────────────────────────────────

async def _tg_post(method: str, payload: dict) -> dict:
    token = _bot_token()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return {}
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
        return resp.json()


async def send_message(chat_id: int | str, text: str, parse_mode: str = "Markdown") -> dict:
    """Send a text message to a Telegram chat."""
    return await _tg_post("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    })


async def _get_file_url(file_id: str) -> str:
    """Resolve a Telegram file_id to a download URL."""
    token = _bot_token()
    data = await _tg_post("getFile", {"file_id": file_id})
    file_path = data.get("result", {}).get("file_path", "")
    if not file_path:
        return ""
    return f"https://api.telegram.org/file/bot{token}/{file_path}"


async def _download_file(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


# ── User mapping: Telegram ID ↔ brain user_id ─────────────────

import sqlite3

SQLITE_PATH = os.getenv("SQLITE_PATH", "/app/history.db")


def _get_conn():
    import os as _os
    _os.makedirs(_os.path.dirname(_os.path.abspath(SQLITE_PATH)), exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_telegram_table():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_users (
            telegram_id  TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            username     TEXT,
            first_name   TEXT,
            created_at   TEXT NOT NULL
        )
    """)
    # Add linked_google_user_id column if it doesn't exist (migration)
    try:
        conn.execute("ALTER TABLE telegram_users ADD COLUMN linked_google_user_id TEXT")
    except Exception:
        pass  # column already exists
    conn.commit()
    conn.close()


def link_telegram_user(telegram_id: str, google_user_id: str) -> bool:
    """
    Link a Telegram ID to a Google OAuth user_id.
    After linking, all Telegram messages from this Telegram ID will be
    routed to the Google user's data (tasks, memories, etc).
    Returns True on success.
    """
    conn = _get_conn()
    try:
        existing = conn.execute(
            "SELECT user_id FROM telegram_users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if existing:
            # Update the existing Telegram user row to use the Google user's UUID
            conn.execute(
                "UPDATE telegram_users SET user_id = ?, linked_google_user_id = ? WHERE telegram_id = ?",
                (google_user_id, google_user_id, telegram_id),
            )
        else:
            # Telegram user hasn't messaged the bot yet — pre-register the link
            conn.execute(
                """INSERT INTO telegram_users (telegram_id, user_id, linked_google_user_id, username, first_name, created_at)
                   VALUES (?, ?, ?, '', '', ?)""",
                (telegram_id, google_user_id, google_user_id, datetime.now(timezone.utc).isoformat()),
            )
        conn.commit()
        logger.info("Linked Telegram %s → Google user %s", telegram_id, google_user_id[:8])
        return True
    except Exception as e:
        logger.error("Error linking Telegram user: %s", e, exc_info=True)
        return False
    finally:
        conn.close()


def unlink_telegram_user(google_user_id: str) -> bool:
    """Remove the Telegram ↔ Google user link. Telegram user reverts to their own data silo."""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE telegram_users SET user_id = telegram_id, linked_google_user_id = NULL WHERE linked_google_user_id = ?",
            (google_user_id,)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error("Error unlinking Telegram user: %s", e, exc_info=True)
        return False
    finally:
        conn.close()


def get_telegram_link_status(google_user_id: str) -> dict:
    """Return whether a Google user has a linked Telegram account."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT telegram_id, username, first_name FROM telegram_users WHERE linked_google_user_id = ?",
            (google_user_id,)
        ).fetchone()
        if row:
            return {
                "linked": True,
                "telegram_id": row["telegram_id"],
                "username": row["username"] or "",
                "first_name": row["first_name"] or "",
            }
        return {"linked": False}
    finally:
        conn.close()


def get_or_create_user(telegram_id: str, username: str, first_name: str) -> str:
    """
    Return the brain user_id for this Telegram user.
    Creates a new brain user if this Telegram ID is seen for the first time.
    """
    import uuid
    conn = _get_conn()
    row = conn.execute(
        "SELECT user_id FROM telegram_users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()

    if row:
        conn.close()
        return row["user_id"]

    # New Telegram user — create a brain account
    user_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO telegram_users (telegram_id, user_id, username, first_name, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (telegram_id, user_id, username, first_name, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    logger.info("Created brain user %s for Telegram user %s (%s)", user_id[:8], telegram_id, first_name)
    return user_id


# ── Voice transcription ────────────────────────────────────────

_whisper_model = None

def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        logging.getLogger("faster_whisper").setLevel(logging.WARNING)
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper_model


async def _transcribe_voice(file_id: str) -> str:
    """Download a Telegram voice/audio file and transcribe it."""
    url = await _get_file_url(file_id)
    if not url:
        return ""
    audio_bytes = await _download_file(url)
    suffix = ".ogg"  # Telegram voice messages are OGG/Opus
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        # Run blocking Whisper transcription in a thread pool so we don't freeze the event loop
        loop = asyncio.get_event_loop()
        def _run():
            model = _get_whisper()
            segments, _ = model.transcribe(tmp_path, beam_size=5)
            return " ".join(seg.text.strip() for seg in segments).strip()
        return await loop.run_in_executor(None, _run)
    finally:
        import os as _os
        _os.unlink(tmp_path)


# ── Reminder scheduling ────────────────────────────────────────

def schedule_reminder(chat_id: int | str, text: str, run_at: datetime):
    """Schedule a one-shot reminder message to be sent at run_at (UTC)."""
    job_id = f"reminder_{chat_id}_{run_at.isoformat()}"
    scheduler.add_job(
        _send_reminder,
        trigger=DateTrigger(run_date=run_at),
        args=[chat_id, text],
        id=job_id,
        replace_existing=True,
    )
    logger.info("Reminder scheduled: chat=%s at=%s", chat_id, run_at)


async def _send_reminder(chat_id: int | str, text: str):
    await send_message(chat_id, text)


# ── Response formatting for Telegram ──────────────────────────

def _format_response(result: dict) -> str:
    """
    Convert structured agent response into plain Telegram-friendly text.
    The web app renders cards; Telegram gets formatted Markdown instead.
    """
    answer  = result.get("answer", "Done.")
    rtype   = result.get("type", "text")
    payload = result.get("payload", {})
    sources = result.get("sources", [])

    if rtype == "task_list":
        pending   = payload.get("pending", [])
        completed = payload.get("completed", [])
        lines = [answer] if answer else []
        if pending:
            lines.append("\n*Pending:*")
            for t in pending:
                lines.append(f"  • {t['content']}")
        if completed:
            lines.append("\n*Completed:*")
            for t in completed:
                lines.append(f"  ✓ {t['content']}")
        if not pending and not completed:
            lines.append("No tasks found.")
        return "\n".join(lines)

    elif rtype == "memory_list":
        memories = payload.get("memories", [])
        lines = [answer] if answer else []
        for m in memories:
            lines.append(f"  • [{m.get('category', '')}] {m.get('content', '')}")
        return "\n".join(lines)

    elif rtype == "category_list":
        cats  = payload.get("categories", [])
        lines = [answer] if answer else []
        for c in cats:
            lines.append(f"  {c.get('icon', '•')} *{c.get('name', '')}* — {c.get('count', 0)} items")
        return "\n".join(lines)

    elif sources:
        # search result with sources
        lines = [answer]
        lines.append("\n*Sources:*")
        for s in sources[:5]:
            lines.append(f"  • [{s.get('category', '')}] {s.get('content', '')[:120]}")
        return "\n".join(lines)

    return answer


# ── Notification helper ────────────────────────────────────────

def _maybe_push_notification(result: dict, user_id: str, sender_name: str) -> None:
    """
    Push a web-app notification when Telegram adds a task or saves a memory.
    Only fires for write operations — not for queries/reads.
    """
    rtype   = result.get("type", "text")
    payload = result.get("payload", {})
    answer  = result.get("answer", "")

    if rtype == "task_added":
        tasks = payload.get("tasks", [])
        if tasks:
            titles = ", ".join(t.get("content", "")[:40] for t in tasks[:3])
            history.push_notification(
                user_id=user_id,
                type="task_added",
                title=f"Task added via Telegram",
                body=titles or answer[:80],
            )
        else:
            # Fallback: answer text suggests a task was added
            if any(kw in answer.lower() for kw in ["task added", "added task", "i've added", "i added"]):
                history.push_notification(
                    user_id=user_id,
                    type="task_added",
                    title="Task added via Telegram",
                    body=answer[:80],
                )

    elif rtype in ("memory_saved", "save_info"):
        history.push_notification(
            user_id=user_id,
            type="memory_saved",
            title="Memory saved via Telegram",
            body=answer[:80],
        )

    elif rtype == "text":
        # Heuristic for plain-text responses that indicate a write happened
        lower = answer.lower()
        if any(kw in lower for kw in ["task added", "added task", "i've added", "i added", "added to your"]):
            history.push_notification(
                user_id=user_id,
                type="task_added",
                title="Task added via Telegram",
                body=answer[:80],
            )
        elif any(kw in lower for kw in ["saved", "remembered", "noted", "i'll remember", "stored"]):
            history.push_notification(
                user_id=user_id,
                type="memory_saved",
                title="Memory saved via Telegram",
                body=answer[:80],
            )


# ── Message processing ─────────────────────────────────────────

async def handle_update(update: dict):
    """
    Main entry point — called for every Telegram update (webhook payload).
    Handles text messages and voice notes.
    """
    message = update.get("message") or update.get("edited_message")
    if not message:
        logger.info("Telegram update has no message field — keys: %s", list(update.keys()))
        return  # ignore non-message updates (inline queries, etc.)

    chat_id    = message["chat"]["id"]
    from_user  = message.get("from", {})
    tg_id      = str(from_user.get("id", ""))
    username   = from_user.get("username", "")
    first_name = from_user.get("first_name", "")

    logger.info("Telegram message — chat_id=%s tg_id=%s username=%s first_name=%s",
                chat_id, tg_id, username, first_name)

    # ── Access control ──────────────────────────────────────────
    allowed = _allowed_ids()
    logger.info("Access check — tg_id=%r allowed=%r in_set=%s", tg_id, allowed, tg_id in allowed)
    if allowed and tg_id not in allowed:
        logger.warning("Rejected tg_id=%s not in allowed=%s", tg_id, allowed)
        await send_message(chat_id, "Sorry, you are not authorised to use this bot.")
        return

    if not tg_id:
        logger.warning("Empty tg_id — skipping")
        return

    # ── Resolve brain user ──────────────────────────────────────
    user_id = get_or_create_user(tg_id, username, first_name)
    logger.info("Resolved brain user_id=%s for tg_id=%s", user_id[:8], tg_id)

    # ── Extract text ────────────────────────────────────────────
    text = message.get("text", "").strip()
    # Do NOT log message content — it may contain passwords, PINs, or secrets
    logger.info("Telegram message received: voice=%s text_len=%d", bool(message.get("voice")), len(text))

    # Voice message → transcribe
    voice = message.get("voice") or message.get("audio")
    if voice and not text:
        await send_message(chat_id, "_Transcribing voice note..._")
        text = await _transcribe_voice(voice["file_id"])
        if not text:
            await send_message(chat_id, "Sorry, I couldn't transcribe that voice note.")
            return
        # Echo transcription so user can confirm
        await send_message(chat_id, f"_You said:_ {text}")

    if not text:
        await send_message(chat_id, "Please send a text or voice message.")
        return

    # ── Handle /start command ───────────────────────────────────
    if text.startswith("/start"):
        await send_message(
            chat_id,
            f"Hi {first_name}! I'm your Personal Brain assistant.\n\n"
            "I can help you:\n"
            "• Save and recall information\n"
            "• Manage your tasks\n"
            "• Set reminders\n\n"
            "Just talk to me naturally. Try:\n"
            "`Remember: my gym membership expires in March`\n"
            "`What do I have on my task list today?`\n"
            "`Remind me to call the doctor at 5pm`"
        )
        return

    # ── Route through Claude agent ──────────────────────────────
    try:
        logger.info("Calling Claude agent for user=%s", user_id[:8])
        await _tg_post("sendChatAction", {"chat_id": chat_id, "action": "typing"})

        ctx = await ContextManager.build(current_message=text, user_id=user_id)
        result = await claude.process_message(ctx, user_id)
        answer = _format_response(result)

        logger.info("Claude responded: action=%s", result.get("action", "chat"))

        # Save to chat history
        import uuid as _uuid
        history.save_message(str(_uuid.uuid4()), text, "user", user_id=user_id)
        history.save_message(str(_uuid.uuid4()), answer, "assistant", user_id=user_id)

        # Push notification to web app for write operations
        _maybe_push_notification(result, user_id, first_name or username or "Telegram")

        send_result = await send_message(chat_id, answer)
        logger.info("send_message result: %s", send_result.get("ok"))

    except Exception as e:
        logger.error("Error handling Telegram message: %s", e, exc_info=True)
        await send_message(chat_id, "Something went wrong. Please try again.")


async def _handle_update_safe(update: dict):
    """Wrapper that logs any uncaught exception from handle_update."""
    try:
        await handle_update(update)
    except Exception as e:
        logger.error("Unhandled error in handle_update: %s", e, exc_info=True)


# ── Webhook registration ────────────────────────────────────────

async def set_webhook(backend_url: str):
    """Register the webhook URL with Telegram. Called on startup.

    Includes a secret_token when TELEGRAM_WEBHOOK_SECRET is set so Telegram
    authenticates each incoming webhook call. This prevents anyone who discovers
    the webhook URL from spoofing Telegram updates.
    """
    webhook_url = f"{backend_url.rstrip('/')}/telegram/webhook"
    payload: dict = {
        "url": webhook_url,
        "allowed_updates": ["message", "edited_message"],
        "drop_pending_updates": True,
    }
    webhook_secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")
    if webhook_secret:
        payload["secret_token"] = webhook_secret
        logger.info("Telegram webhook secret_token configured")
    else:
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET not set — webhook has no signature validation. "
            "Set it to a random string (openssl rand -hex 16) and update Railway."
        )
    result = await _tg_post("setWebhook", payload)
    if result.get("ok"):
        logger.info("Telegram webhook set: %s", webhook_url)
    else:
        logger.error("Failed to set Telegram webhook: %s", result)
