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
    conn.commit()
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
    await send_message(chat_id, f"Reminder: {text}")


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
    logger.info("Message text=%r voice=%s", text[:100] if text else "", bool(message.get("voice")))

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
        logger.info("Calling Claude agent for user=%s message=%r", user_id[:8], text[:80])
        await _tg_post("sendChatAction", {"chat_id": chat_id, "action": "typing"})

        ctx = await ContextManager.build(current_message=text, user_id=user_id)
        result = await claude.process_message(ctx, user_id)
        answer = result.get("answer", "Done.")

        logger.info("Claude returned answer=%r", answer[:120])

        # Save to chat history
        history.add_message(user_id, "user", text)
        history.add_message(user_id, "assistant", answer)

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
    """Register the webhook URL with Telegram. Called on startup."""
    webhook_url = f"{backend_url.rstrip('/')}/telegram/webhook"
    result = await _tg_post("setWebhook", {
        "url": webhook_url,
        "allowed_updates": ["message", "edited_message"],
        "drop_pending_updates": True,
    })
    if result.get("ok"):
        logger.info("Telegram webhook set: %s", webhook_url)
    else:
        logger.error("Failed to set Telegram webhook: %s", result)
