"""
pipelines/briefing.py — P6: Daily Briefing generation.

Per-user, on-demand (with 30-minute cache). Also scheduled at 06:30 local.

Gathers:
  - Tasks due/overdue + inbox count (Qdrant brain_tasks)
  - Weather summary (weather.py — 30-min cache)
  - Transit status (transit_alerts SQLite cache)
  - Top special item for today
  - #1 news headline for today
  - Today's concept card term

One Claude Sonnet call → one short paragraph (≤ 60 words):
  - Friendly but not cutesy
  - Must mention task count
  - Must mention any weather warning if rain probability > 60%
  - Must mention any transit disruption if severity != 'normal'
  - Generated AFTER fetching weather/transit so text never contradicts chips
"""

import os
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import dashboard_db
import weather as weather_module
from pipelines._claude_helper import call_claude_json

# tasks module imports qdrant_client — lazy so fixture mode works locally
def _get_tasks():
    import tasks as _tasks
    return _tasks

logger = logging.getLogger(__name__)

CACHE_MINUTES = 30
FIXTURE_MODE = os.getenv("FIXTURE_MODE", "").lower() in ("1", "true", "yes")


async def generate_briefing(user_id: str, force_refresh: bool = False) -> Optional[dict]:
    """
    Return a briefing for the user. Uses SQLite cache (30-min TTL) unless force_refresh=True.

    Returns briefing dict with keys: id, user_id, cycle_date, text, generated_at
    """
    today = date.today().isoformat()

    if FIXTURE_MODE:
        return _fixture_briefing(user_id, today)

    # Check cache (within 30 minutes and same cycle_date)
    if not force_refresh:
        cached = dashboard_db.get_briefing(user_id, today)
        if cached:
            gen_at = datetime.fromisoformat(cached["generated_at"])
            age = datetime.now(timezone.utc) - gen_at.replace(tzinfo=timezone.utc) if gen_at.tzinfo is None else datetime.now(timezone.utc) - gen_at
            if age.total_seconds() < CACHE_MINUTES * 60:
                return cached

    try:
        context = await _gather_context(user_id, today)
        text = await _call_claude(context)
        if not text:
            # Fall back to cached if available
            cached = dashboard_db.get_briefing(user_id, today)
            if cached:
                logger.warning("Briefing generation failed — returning cached for user %s", user_id[:8])
                return cached
            return _stub_briefing(user_id, today, context)

        briefing = dashboard_db.upsert_briefing(user_id, today, text)
        return briefing

    except Exception as e:
        logger.error("Briefing generation error for user %s: %s", user_id[:8], e, exc_info=True)
        cached = dashboard_db.get_briefing(user_id, today)
        return cached


async def run_pipeline(user_id: Optional[str] = None) -> None:
    """Scheduled pipeline: generate briefing at 06:30 for a user (or all users)."""
    if user_id:
        await generate_briefing(user_id, force_refresh=False)
    # Multi-user: caller iterates over all users and calls this with each user_id


async def _gather_context(user_id: str, today: str) -> dict:
    """Assemble all inputs for the briefing prompt."""
    context: dict = {}

    # Tasks from Qdrant
    try:
        tasks_module = _get_tasks()
        task_page = tasks_module.get_tasks(today, user_id)
        pending = task_page.get("pending", [])
        context["tasks_due"] = len(pending)
        context["task_names"] = [t["content"] for t in pending[:3]]

        # Overdue: tasks created before today that are still pending
        from datetime import date as date_type
        overdue = []
        for task in pending:
            created = task.get("createdDate", today)
            if created < today:
                overdue.append(task["content"])
        context["tasks_overdue"] = len(overdue)
    except Exception as e:
        logger.warning("Briefing: task fetch failed: %s", e)
        context["tasks_due"] = 0
        context["tasks_overdue"] = 0
        context["task_names"] = []

    # Weather
    try:
        w = weather_module.get_weather()
        context["weather_temp"] = w.get("tempC", 0)
        context["weather_rain_prob"] = w.get("rainProbability", 0)
        context["weather_condition"] = w.get("condition", "")
    except Exception as e:
        logger.warning("Briefing: weather fetch failed: %s", e)
        context["weather_temp"] = None
        context["weather_rain_prob"] = 0
        context["weather_condition"] = ""

    # Transit
    try:
        alerts = dashboard_db.get_transit_alerts()
        disrupted = [a for a in alerts if a.get("severity") not in ("normal", None)]
        context["transit_disrupted"] = bool(disrupted)
        context["transit_summary"] = disrupted[0]["title"] if disrupted else "Normal service"
        context["transit_line"] = disrupted[0]["line"] if disrupted else ""
    except Exception as e:
        logger.warning("Briefing: transit fetch failed: %s", e)
        context["transit_disrupted"] = False
        context["transit_summary"] = "Normal service"

    # Top special item
    try:
        specials = dashboard_db.get_special_today(today)
        context["special_item"] = specials[0]["label"] if specials else None
    except Exception:
        context["special_item"] = None

    # #1 news headline
    try:
        news_items = dashboard_db.get_feed_items("news", today)
        context["top_news"] = news_items[0]["title"] if news_items else None
    except Exception:
        context["top_news"] = None

    # Concept of the day term
    try:
        card = dashboard_db.get_concept_of_day()
        context["concept_term"] = card["term"] if card else None
    except Exception:
        context["concept_term"] = None

    return context


async def _call_claude(context: dict) -> Optional[str]:
    """Generate a ≤60-word briefing paragraph via Claude."""
    total_tasks = context.get("tasks_due", 0) + context.get("tasks_overdue", 0)
    task_detail = f"{total_tasks} task{'s' if total_tasks != 1 else ''} due"
    if context.get("tasks_overdue"):
        task_detail += f" ({context['tasks_overdue']} overdue)"

    weather_line = ""
    if context.get("weather_rain_prob", 0) > 60:
        weather_line = f"Rain is likely ({context['weather_rain_prob']}% probability)."
    elif context.get("weather_temp") is not None:
        weather_line = f"Currently {context['weather_temp']}°C and {context['weather_condition'].lower()}."

    transit_line = ""
    if context.get("transit_disrupted"):
        transit_line = f"Transit alert: {context['transit_summary']} on {context.get('transit_line', 'your line')}."

    prompt = f"""Write a single paragraph (≤60 words) for a morning dashboard briefing.
Tone: direct, friendly, not cutesy. No emoji. No greeting line (do not start with "Good morning").

Facts to weave in naturally — include ALL that are non-empty:
- Tasks: {task_detail}
- Weather: {weather_line or "No notable weather"}
- Transit: {transit_line or "Normal service"}
- Special: {context.get("special_item") or "nothing special"}
- Top news: {context.get("top_news") or "no major headlines"}
- Today's concept: {context.get("concept_term") or "none"}

Rules:
1. Task count MUST appear (number, not just "some tasks").
2. Mention weather only if rain > 60% or condition is notable.
3. Mention transit only if disrupted.
4. Keep it ≤ 60 words. Output the paragraph only — no labels, no JSON.
"""

    result = await call_claude_json(
        prompt,
        context="briefing",
        system_override=(
            "You write concise morning briefing paragraphs. "
            "Respond with ONLY the paragraph text — no JSON, no markdown, no labels."
        ),
        max_tokens=200,
    )

    # For briefings, we allow plain-text response (not JSON)
    # call_claude_json will fail to parse it — we handle that here
    if result is None:
        # Try a direct call without JSON parsing
        try:
            import anthropic  # lazy — only reached when not in fixture mode
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                system="You write concise morning briefing paragraphs. Respond with ONLY the paragraph text.",
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            logger.info("Briefing: generated %d chars", len(text))
            return text
        except Exception as e:
            logger.error("Briefing Claude call failed: %s", e)
            return None

    # If call_claude_json somehow returned a dict (e.g., {"text": "..."}), extract it
    if isinstance(result, dict):
        return result.get("text") or result.get("briefing") or str(result)

    return None


def _stub_briefing(user_id: str, today: str, context: dict) -> dict:
    """Return a minimal stub briefing when Claude is unavailable."""
    total = context.get("tasks_due", 0)
    text = f"You have {total} task{'s' if total != 1 else ''} today. {context.get('weather_condition', 'Have a great day.')}."
    return {"id": "stub", "user_id": user_id, "cycle_date": today,
            "text": text, "generated_at": datetime.now(timezone.utc).isoformat()}


def _fixture_briefing(user_id: str, today: str) -> dict:
    return {
        "id": "fixture",
        "user_id": user_id,
        "cycle_date": today,
        "text": "You have 4 tasks due today, including the Architecture Review. Heavy rain is expected at 3 PM. T1 lines are running with minor delays. Notable: Llama 4-base weights just leaked.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
