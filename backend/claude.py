"""
Agent loop for personal-brain.

Uses Anthropic's native tool_use API — no framework.
Claude decides which tools to call; we execute them and feed results back.
Loop continues until stop_reason == "end_turn".
"""

import os
import json
import logging
import datetime as dt_module
from datetime import datetime, timezone

import anthropic
import brain
import tasks as tasks_module
import dashboard_db
import weather as weather_module
from context_manager import ContextManager

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL   = "claude-sonnet-4-20250514"
MAX_TOOL_ROUNDS = 5   # safety cap on the agentic loop

# ─────────────────────────────────────────────────────────────
# Tool schemas
# Claude uses these to decide what to call. Keep descriptions
# crisp — they directly shape Claude's reasoning.
# ─────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_knowledge",
        "description": (
            "Search the personal knowledge base for facts, links, credentials, notes, or anything "
            "the user has previously saved. Use this when the user asks a QUESTION about information "
            "they may have stored — e.g. 'what is my LinkedIn?', 'what did I save about Kafka?', "
            "'what is the prod DB host?'. Do NOT use for task-related queries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Specific search query to find relevant notes"},
                "limit": {"type": "integer", "description": "Max results (default 8)", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "save_knowledge",
        "description": (
            "Save a fact, note, link, credential, or any piece of information to the personal knowledge base. "
            "Use this ONLY when the user is explicitly telling you something to remember — "
            "e.g. 'my LinkedIn is ...', 'the prod DB is at ...', 'save this: ...', or pasting content. "
            "Do NOT use for questions, task management, or commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The exact content to save verbatim"},
                "category": {
                    "type": "string",
                    "description": (
                        "Category label. Use an existing category if it fits well "
                        "(check EXISTING KNOWLEDGE CATEGORIES in the system prompt), "
                        "otherwise create a short descriptive name like 'Personal Profile', 'Server Access', 'Meeting Notes'."
                    ),
                },
            },
            "required": ["content", "category"],
        },
    },
    {
        "name": "get_tasks",
        "description": (
            "Retrieve the FULL task list for a specific date. "
            "Use ONLY when the user explicitly wants to SEE or LIST ALL their tasks — "
            "e.g. 'show my tasks', 'what's on my list today', 'show pending tasks'. "
            "Do NOT use for filtered/specific questions like 'any tasks about X?' or 'tasks related to Y?' — "
            "for those, look at PENDING TASKS TODAY in the system prompt or use search_knowledge. "
            "Also use to find a task ID before calling complete_task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD. Resolve 'today', 'yesterday', 'last Monday' to an absolute date using TODAY from the system prompt.",
                },
            },
            "required": ["date"],
        },
    },
    {
        "name": "add_tasks",
        "description": (
            "Add one or more new tasks to today's task list. "
            "Use this when the user says they need to do something, lists action items, "
            "or asks you to add tasks. Examples: 'add task: write tests', 'I need to: review PR, update docs'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of task descriptions as clean, actionable items (no bullet symbols).",
                },
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "complete_task",
        "description": (
            "Mark a specific task as complete. "
            "Use this when the user says they finished, completed, or are done with a task — "
            "e.g. 'done with the PR review', 'finished the deploy task', 'mark X as complete'. "
            "You MUST call get_tasks first to find the matching task ID, then call this tool with that ID."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The full UUID of the task to mark complete"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "carry_forward_tasks",
        "description": (
            "Move all pending tasks from today to tomorrow. "
            "Use this when the user confirms they want to carry forward — "
            "e.g. 'carry forward', 'move tasks to tomorrow', 'yes carry them over'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_categories",
        "description": (
            "List all knowledge categories with item counts. "
            "Use this when the user asks to see their knowledge base, brain summary, "
            "categories, or what topics are stored — e.g. 'show my brain', 'what categories do I have'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "set_task_reminder",
        "description": (
            "Schedule a Telegram reminder for a specific task or message at a given time. "
            "Use when the user says 'remind me', 'ping me at', 'alert me when', 'set a reminder'. "
            "Examples: 'remind me to call John at 3pm', 'ping me about the deploy at 17:30', "
            "'set a reminder for standup at 9am tomorrow'. "
            "Only works if the user has Telegram linked. If not linked, tell them to connect Telegram in Settings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The reminder text to send, e.g. 'Time to call John!' or 'Deploy the backend now'",
                },
                "remind_at": {
                    "type": "string",
                    "description": (
                        "ISO 8601 datetime when to send the reminder, e.g. '2026-06-14T15:00:00'. "
                        "Resolve relative times like 'at 3pm', 'in 2 hours', 'tomorrow at 9am' "
                        "using TODAY from the system prompt. Always use the user's local date."
                    ),
                },
            },
            "required": ["message", "remind_at"],
        },
    },
    {
        "name": "save_vault_item",
        "description": (
            "Save a SENSITIVE credential or secret to the encrypted vault. "
            "Use this when the user wants to store a password, account number, PIN, API key, "
            "credit card number, passport number, or any other secret. "
            "Examples: 'save my Netflix password', 'store my bank account number', "
            "'remember my SSH key passphrase', 'vault my credit card'. "
            "The secret is encrypted with AES-256-GCM and NEVER stored in plain text. "
            "Do NOT use save_knowledge for sensitive secrets — always use this tool instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Short descriptive name, e.g. 'Netflix password', 'Bank of Sydney account number', 'AWS root API key'",
                },
                "secret": {
                    "type": "string",
                    "description": "The actual secret value to encrypt and store",
                },
                "category": {
                    "type": "string",
                    "description": "Category: 'Passwords', 'Banking', 'API Keys', 'Cards', 'Identity Documents', 'PIN Codes', or 'Other'",
                    "default": "Passwords",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional additional context, e.g. 'username: john@example.com', 'expires 2027-06'",
                    "default": "",
                },
            },
            "required": ["label", "secret"],
        },
    },
    {
        "name": "search_vault",
        "description": (
            "Search the encrypted vault for a stored credential or secret. "
            "Use this when the user asks to retrieve a password, account number, PIN, "
            "API key, or any secret they previously stored. "
            "Examples: 'what is my Netflix password?', 'show my bank account', "
            "'what API key did I save for GitHub?'. "
            "Always confirms the user's intent before displaying secrets."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for, e.g. 'Netflix password', 'bank account', 'GitHub API key'",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "delete_vault_item",
        "description": (
            "Delete a credential from the encrypted vault. "
            "Use only when the user explicitly says to delete or remove a saved secret. "
            "First call search_vault to find the item ID, then call this to delete it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The UUID of the vault item to delete",
                },
                "label": {
                    "type": "string",
                    "description": "Label of the item being deleted (for confirmation message)",
                },
            },
            "required": ["item_id", "label"],
        },
    },
]

# ─────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────
# Dashboard context builder
# ─────────────────────────────────────────────────────────────

def _build_dashboard_context(user_id: str = "") -> str:
    """Assemble a concise ENVIRONMENT block from cached dashboard data.

    Reads only from SQLite caches — never calls external APIs.
    Returns an empty string gracefully if any source fails.
    """
    lines: list[str] = []

    # Weather (30-min cached from Open-Meteo)
    try:
        w = weather_module.get_weather()
        if w:
            rain_note = f", {w['rain_probability']}% rain chance" if w.get("rain_probability") else ""
            lines.append(f"Weather: {w['temp_c']}°C{rain_note}. {w.get('description', '')}".strip())
    except Exception:
        pass

    # Transit alerts
    try:
        alerts = dashboard_db.get_transit_alerts()
        if alerts:
            disrupted = [a for a in alerts if dict(a).get("severity") not in ("normal", None)]
            if disrupted:
                for a in disrupted:
                    a = dict(a)
                    lines.append(f"Transit {a['line']}: {a['title']} ({a['severity']} disruption)")
            else:
                monitored = sorted({dict(a)["line"] for a in alerts})
                lines.append(f"Transit: Normal service on {', '.join(monitored)}.")
    except Exception:
        pass

    # Special today (personal items first)
    try:
        from datetime import date
        today_str = date.today().isoformat()
        special_rows = dashboard_db.get_special_today(today_str)
        if special_rows:
            items_json = special_rows[0]["items_json"] if hasattr(special_rows[0], "__getitem__") else None
            if items_json:
                import json as _json
                items = _json.loads(items_json) if isinstance(items_json, str) else items_json
                for it in items[:3]:
                    lines.append(f"Today: {it.get('emoji', '')} {it.get('label', '')} — {it.get('note', '')}".strip())
    except Exception:
        pass

    # Top news headline
    try:
        from datetime import date
        today_str = date.today().isoformat()
        news = dashboard_db.get_feed_items("news", today_str)
        if news:
            top = dict(news[0])
            lines.append(f"Top AI news: {top['title']} ({top.get('source_name', '')})")
    except Exception:
        pass

    # Today's reminders/meetings set via chat
    if user_id:
        try:
            reminders_str = _get_user_reminders_today(user_id)
            if reminders_str:
                lines.append(f"Reminders today: {reminders_str}")
        except Exception:
            pass

    if not lines:
        return "(weather and transit data not yet available)"
    return "\n".join(lines)


def _get_user_reminders_today(user_id: str) -> str:
    """Return today's reminders for this user from APScheduler, formatted as a string."""
    try:
        import sqlite3 as _sqlite3, re, os as _os
        from datetime import date as _date
        sqlite_path = _os.getenv("SQLITE_PATH", "/app/history.db")
        conn = _sqlite3.connect(sqlite_path)
        conn.row_factory = _sqlite3.Row
        row = conn.execute(
            "SELECT telegram_id FROM telegram_users WHERE user_id = ? OR linked_google_user_id = ?",
            (user_id, user_id)
        ).fetchone()
        conn.close()
        if not row:
            return ""
        telegram_id = row["telegram_id"]
        from telegram_bot import scheduler
        today = _date.today().isoformat()
        parts = []
        for job in scheduler.get_jobs():
            if not job.id.startswith(f"reminder_{telegram_id}_"):
                continue
            if job.next_run_time and job.next_run_time.date().isoformat() == today:
                args = job.args or []
                msg = str(args[1]) if len(args) > 1 else ""
                clean = re.sub(r"^⏰ \*Reminder.*?\*\n+", "", msg, flags=re.DOTALL).strip()
                parts.append(f"{job.next_run_time.strftime('%H:%M')} — {clean}")
        return "; ".join(parts) if parts else ""
    except Exception:
        return ""


# ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are Personal Brain — a personal knowledge and task management assistant.

You have access to tools to:
1. Save and search the user's personal knowledge base
2. Manage their daily tasks (add, complete, carry forward, review)
3. Save and retrieve sensitive secrets in an encrypted vault (passwords, account numbers, PINs, API keys)
4. Set Telegram reminders for specific tasks or messages at a given time

TODAY: {today}

ENVIRONMENT (weather, transit, events — already fetched, use directly, no tools needed):
{dashboard_context}

EXISTING KNOWLEDGE CATEGORIES:
{categories}

PENDING TASKS TODAY:
{pending_tasks}

RETRIEVED KNOWLEDGE (semantically relevant to current message):
{relevant_memories}

Decision rules — follow STRICTLY in this priority order:

KNOWLEDGE (not tasks):
- "my X is Y", "save this", "remember that", pasting content → save_knowledge
- "what is X", "tell me about X", "what did I save about X" → search_knowledge
- "show categories", "show my brain", "what topics" → get_categories

TASKS — explicit list requests only:
- "show my tasks", "list tasks", "what's on my list", "show pending" → get_tasks(today)
- "I need to:", "add task", "today I have to", "put X on my list" → add_tasks
- "done with X", "finished X", "completed X", "mark X done" → get_tasks(today) THEN complete_task
- "carry forward", "move to tomorrow" → carry_forward_tasks

FILTERED / SPECIFIC TASK QUESTIONS — answer from context, NO tools needed:
- "any tasks about X?", "tasks related to Y?", "do I have anything for Z?" →
  Read PENDING TASKS TODAY above. Find relevant items. Answer in plain text.
  Do NOT call get_tasks. Do NOT call search_knowledge. Just answer from the list above.
  Example: "Yes, you have: [relevant task]" or "No tasks related to X found."

PLAN MY DAY — when the user asks to plan their day:
- You already have everything you need: PENDING TASKS TODAY, ENVIRONMENT above.
- Do NOT ask the user to provide weather, transit, or tasks — you have them.
- Structure a realistic time-blocked plan: morning (before 9am), morning work block,
  lunch break, afternoon block, evening. Factor in weather and transit warnings.
- Prioritise overdue tasks first, then due-today, then inbox.
- Keep it concise — one short paragraph or a simple time-blocked list.

REMINDERS (Telegram notifications at a specific time):
- "remind me to X at Y", "ping me about X at Y", "set a reminder for X" → set_task_reminder
- Resolve relative times ('at 3pm', 'in 2 hours', 'tomorrow 9am') using TODAY in the system prompt
- If Telegram not linked, tell the user to connect it in Settings
- Confirm the exact time back to the user after setting

VAULT (encrypted secrets — passwords, account numbers, PINs, API keys, cards):
- "save my X password", "store my account number", "vault my PIN" → save_vault_item
- "what is my X password?", "show my bank account", "get my API key" → search_vault
- "delete my X from vault", "remove my password" → search_vault THEN delete_vault_item
- NEVER use save_knowledge for passwords or secrets — always use save_vault_item
- When displaying vault results: show the secret clearly, the user asked for it
- Vault is AES-256-GCM encrypted — assure the user their secrets are safe

CRITICAL rules:
- get_tasks returns the FULL list — only call it when the user wants to see everything.
- Filtered task questions: scan PENDING TASKS TODAY above and answer in plain text.
- For task completion: call get_tasks first to get the ID, then complete_task with that ID.
- "show categories" / "show my brain" means get_categories, not get_tasks.
- Be concise after tool calls — don't re-list what the tool already returned.
- Never save a user's question or command as knowledge.
- Never ask the user to provide weather, transport, or calendar data — use ENVIRONMENT above.
"""

# ─────────────────────────────────────────────────────────────
# Tool execution
# ─────────────────────────────────────────────────────────────

def _execute_tool(name: str, inputs: dict, today: str, user_id: str) -> tuple[str, dict]:
    """Execute a tool scoped to user_id. Returns (text_result, metadata)."""
    try:
        if name == "search_knowledge":
            query  = inputs["query"]
            limit  = inputs.get("limit", 8)
            memories = brain.search_memories(query, user_id, limit=limit)
            if not memories:
                return "No relevant saved notes found.", {"type": "search", "memories": []}
            lines = [f"[{m['category']}] {m['content']} (score: {m['score']:.2f})" for m in memories]
            return "\n".join(lines), {"type": "search", "memories": memories}

        elif name == "save_knowledge":
            content  = inputs["content"]
            category = inputs["category"]
            if len(content) > brain.CHUNK_THRESHOLD_CHARS:
                result  = brain.chunk_and_save(content, category, user_id)
                saved   = result["saved_count"]
                skipped = result["skipped_count"]
                if saved == 0:
                    return f"Already have all of this saved under '{category}'.", {"type": "save", "skipped": True}
                msg = f"Saved {saved} chunk{'s' if saved != 1 else ''} under '{category}'"
                if skipped:
                    msg += f" ({skipped} duplicate{'s' if skipped != 1 else ''} skipped)"
                return msg + ".", {"type": "save", "category": category, "memories": result["memories"], "chunked": True}
            else:
                if brain.is_duplicate(content, user_id):
                    return f"Already have this saved under '{category}'.", {"type": "save", "skipped": True}
                memory = brain.save_memory(content, category, user_id)
                return f"Saved under '{category}'.", {"type": "save", "category": category, "memories": [memory], "chunked": False}

        elif name == "get_tasks":
            date      = inputs["date"]
            task_page = tasks_module.get_tasks(date, user_id)
            pending   = task_page.get("pending", [])
            completed = task_page.get("completed", [])
            if not pending and not completed:
                return f"No tasks found for {date}.", {"type": "tasks", "task_page": task_page}
            lines  = [f"[PENDING] [{t['id'][:8]}] {t['content']}" for t in pending]
            lines += [f"[DONE]    [{t['id'][:8]}] {t['content']}" for t in completed]
            return "\n".join(lines), {"type": "tasks", "task_page": task_page}

        elif name == "add_tasks":
            task_list = inputs.get("tasks", [])
            if not task_list:
                return "No tasks provided.", {"type": "tasks_added", "tasks": []}
            saved = tasks_module.save_tasks(task_list, today, user_id)
            lines = "\n".join(f"- {t}" for t in task_list)
            return f"Added {len(task_list)} task{'s' if len(task_list) != 1 else ''}:\n{lines}", {
                "type": "tasks_added", "tasks": saved,
                "task_page": {"pending": saved, "completed": [], "date": today},
            }

        elif name == "complete_task":
            task_id   = inputs["task_id"]
            task      = tasks_module.complete_task(task_id, user_id)
            task_page = tasks_module.get_tasks(today, user_id)
            return f"Marked complete: {task['content']}", {"type": "task_completed", "task": task, "task_page": task_page}

        elif name == "carry_forward_tasks":
            tomorrow = (dt_module.date.fromisoformat(today) + dt_module.timedelta(days=1)).isoformat()
            carried  = tasks_module.carry_forward(today, tomorrow, user_id)
            n = len(carried)
            if n == 0:
                return "No pending tasks to carry forward.", {"type": "carried", "tasks": []}
            return f"Carried {n} task{'s' if n != 1 else ''} to {tomorrow}.", {
                "type": "carried",
                "task_page": {"pending": carried, "completed": [], "date": tomorrow},
            }

        elif name == "get_categories":
            cats = brain.get_categories(user_id)
            if not cats:
                return "No knowledge categories yet.", {"type": "categories", "categories": []}
            lines = [f"- {c['icon']} {c['name']} ({c['count']} items)" for c in cats]
            return "\n".join(lines), {"type": "categories", "categories": cats}

        elif name == "set_task_reminder":
            import telegram_bot
            import sqlite3 as _sqlite3
            from datetime import datetime as _dt

            message    = inputs.get("message", "").strip()
            remind_at  = inputs.get("remind_at", "").strip()

            if not message or not remind_at:
                return "Please provide both a reminder message and a time.", {}

            # Parse the datetime
            try:
                # Try ISO 8601 first
                remind_dt = _dt.fromisoformat(remind_at)
                if remind_dt.tzinfo is None:
                    # Treat as UTC if no tz provided
                    from datetime import timezone as _tz
                    remind_dt = remind_dt.replace(tzinfo=_tz.utc)
            except ValueError:
                return f"Could not parse reminder time '{remind_at}'. Please use a format like '2026-06-14T15:00:00'.", {}

            now = _dt.now(remind_dt.tzinfo)
            if remind_dt <= now:
                return "The reminder time is in the past. Please choose a future time.", {}

            # Find the user's linked Telegram chat_id
            sqlite_path = os.getenv("SQLITE_PATH", "/app/history.db")
            try:
                conn = _sqlite3.connect(sqlite_path)
                conn.row_factory = _sqlite3.Row
                row = conn.execute(
                    "SELECT telegram_id, first_name FROM telegram_users WHERE user_id = ? OR linked_google_user_id = ?",
                    (user_id, user_id)
                ).fetchone()
                conn.close()
            except Exception as e:
                return f"Could not look up your Telegram link: {e}", {}

            if not row:
                return (
                    "You don't have Telegram linked yet. "
                    "Go to Settings → Telegram Integration to connect your account, "
                    "then I can send you reminders there.",
                    {}
                )

            telegram_id = row["telegram_id"]
            first_name  = row["first_name"] or "there"
            reminder_text = f"⏰ *Reminder, {first_name}!*\n\n{message}"

            telegram_bot.schedule_reminder(telegram_id, reminder_text, remind_dt)

            # Format a friendly confirmation time
            friendly = remind_dt.strftime("%A, %B %-d at %-I:%M %p")
            return (
                f"Reminder set! I'll ping you on Telegram on {friendly}: \"{message}\"",
                {"type": "reminder_set", "remind_at": remind_at, "message": message}
            )

        elif name == "save_vault_item":
            import vault as vault_module
            label    = inputs["label"]
            secret   = inputs["secret"]
            category = inputs.get("category", "Passwords")
            notes    = inputs.get("notes", "")
            try:
                item = vault_module.save_item(user_id, label, secret, category, notes)
                return (
                    f"Saved '{label}' to your encrypted vault under '{category}'. "
                    f"It's protected with AES-256-GCM encryption and only accessible by you.",
                    {"type": "vault_saved", "item": item},
                )
            except RuntimeError as e:
                return f"Vault error: {e}", {"error": str(e)}

        elif name == "search_vault":
            import vault as vault_module
            query = inputs["query"]
            try:
                items = vault_module.search_items(user_id, query, limit=5)
                if not items:
                    return f"No vault items found matching '{query}'.", {"type": "vault_search", "items": []}
                lines = []
                for it in items:
                    lines.append(f"🔐 {it['label']} ({it['category']})")
                    # Mask the secret in the LLM tool result — the frontend
                    # receives the full secret via the metadata payload,
                    # shown directly to the user without passing through Anthropic.
                    masked = it['secret'][:2] + "•" * (len(it['secret']) - 2) if len(it['secret']) > 2 else "••"
                    lines.append(f"   Secret: {masked}  [shown securely to user]")
                    if it.get("notes"):
                        lines.append(f"   Notes:  {it['notes']}")
                    lines.append(f"   ID: {it['id']}")
                # Items in metadata payload shown directly to user by frontend (not via LLM)
                return "\n".join(lines), {"type": "vault_search", "items": items}
            except RuntimeError as e:
                return f"Vault error: {e}", {"error": str(e)}

        elif name == "delete_vault_item":
            import vault as vault_module
            item_id = inputs["item_id"]
            label   = inputs.get("label", "item")
            try:
                ok = vault_module.delete_item(user_id, item_id)
                if ok:
                    return f"Deleted '{label}' from your vault.", {"type": "vault_deleted", "item_id": item_id}
                return f"Could not find '{label}' in your vault.", {"type": "vault_deleted", "item_id": None}
            except RuntimeError as e:
                return f"Vault error: {e}", {"error": str(e)}

        else:
            return f"Unknown tool: {name}", {}

    except Exception as e:
        logger.error("Tool %s failed: %s", name, e, exc_info=True)
        return f"Tool error: {e}", {"error": str(e)}

# ─────────────────────────────────────────────────────────────
# Response shape builder
# Converts last tool metadata into the payload/type shape
# the frontend already understands.
# ─────────────────────────────────────────────────────────────

def _build_response(answer: str, tool_calls: list[dict]) -> dict:
    """
    Build the final response dict from the agent's answer text and the
    list of tool calls + their metadata from this run.

    When multiple tool types are used (e.g. get_tasks + search_knowledge),
    returns type="compound" with a list of card payloads so the frontend
    can render multiple cards alongside the text answer.
    """
    if not tool_calls:
        return {"answer": answer, "type": "text", "action": "chat", "payload": {}, "sources": []}

    # Collect distinct result types
    types_seen = set()
    task_meta = None
    search_meta = None
    save_meta = None
    cat_meta = None

    for call in tool_calls:
        meta = call.get("meta", {})
        t = meta.get("type", "")
        types_seen.add(t)
        if t in ("tasks", "task_completed", "tasks_added", "carried"):
            task_meta = meta
        elif t == "search":
            search_meta = meta
        elif t == "save":
            save_meta = meta
        elif t == "categories":
            cat_meta = meta

    # Compound: task + search used together → this is a QUESTION, not a list request.
    # Return plain text answer with sources only — no TaskCard.
    # The user asked something specific; dumping the full task list is confusing.
    if task_meta and search_meta:
        return {
            "answer": answer,
            "type": "text",
            "action": "question",
            "payload": {},
            "sources": search_meta.get("memories", []),
        }

    # Single tool results
    if task_meta:
        t = task_meta.get("type", "tasks")
        return {
            "answer": answer,
            "type": "task_list",
            "action": t if t != "tasks" else "show_tasks",
            "payload": task_meta.get("task_page", {}),
            "sources": [],
        }

    if save_meta:
        if save_meta.get("skipped"):
            return {"answer": answer, "type": "text", "action": "save_info", "payload": {}, "sources": []}
        return {
            "answer": answer,
            "type": "memory_list",
            "action": "save_info",
            "payload": {"category": save_meta.get("category", ""), "memories": save_meta.get("memories", [])},
            "sources": [],
        }

    if search_meta:
        return {
            "answer": answer,
            "type": "text",
            "action": "question",
            "payload": {},
            "sources": search_meta.get("memories", []),
        }

    if cat_meta:
        return {
            "answer": answer,
            "type": "category_list",
            "action": "show_categories",
            "payload": {"categories": cat_meta.get("categories", [])},
            "sources": [],
        }

    # No tool was called — plain text response
    return {
        "answer": answer,
        "type": "text",
        "action": "chat",
        "payload": {},
        "sources": [],
    }

# ─────────────────────────────────────────────────────────────
# Agent entry point
# ─────────────────────────────────────────────────────────────

async def process_message(ctx: ContextManager, user_id: str) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()

    system = SYSTEM_PROMPT.format(
        today=today,
        dashboard_context=_build_dashboard_context(user_id=user_id),
        categories="\n".join(f"- {c['name']}" for c in ctx.categories) or "(none yet)",
        pending_tasks="\n".join(f"- [{t['id']}] {t['content']}" for t in ctx.pending_tasks) or "(none)",
        relevant_memories="\n".join(f"- [{m['category']}] {m['content']}" for m in ctx.relevant_memories) or "(none)",
    )

    messages       = ctx.to_messages()
    tool_calls_log: list[dict] = []

    try:
        for _round in range(MAX_TOOL_ROUNDS):
            response = _client.messages.create(
                model=MODEL, max_tokens=4096,
                system=system, tools=TOOLS, messages=messages,
            )

            logger.debug("Agent round %d: stop_reason=%s blocks=%d",
                         _round + 1, response.stop_reason, len(response.content))

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks or response.stop_reason == "end_turn":
                text_blocks = [b for b in response.content if b.type == "text"]
                answer = text_blocks[0].text.strip() if text_blocks else "Done."
                return _build_response(answer, tool_calls_log)

            tool_results = []
            for block in tool_use_blocks:
                result_text, meta = _execute_tool(block.name, block.input, today, user_id)
                tool_calls_log.append({"name": block.name, "input": block.input, "meta": meta})
                logger.info("Tool %s(%s) → %s", block.name, list(block.input.keys()), result_text[:80])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            # Feed results back into the conversation
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user",      "content": tool_results},
            ]

        # Safety: hit MAX_TOOL_ROUNDS without end_turn
        logger.warning("Agent hit MAX_TOOL_ROUNDS (%d) without finishing", MAX_TOOL_ROUNDS)
        return {
            "answer": "I got a bit stuck. Could you rephrase that?",
            "type": "text",
            "action": "error",
            "payload": {},
            "sources": [],
        }

    except anthropic.RateLimitError:
        return {
            "answer": "Thinking too hard — give me a second and try again.",
            "type": "text",
            "action": "error",
            "payload": {},
            "sources": [],
        }
    except Exception as e:
        logger.error("Agent error: %s", e, exc_info=True)
        return {
            "answer": f"Something went wrong: {e}",
            "type": "text",
            "action": "error",
            "payload": {},
            "sources": [],
        }
