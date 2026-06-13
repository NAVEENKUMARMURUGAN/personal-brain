"""
pipelines/special.py — P4: Special Today pipeline.

Runs daily at 00:30. Fetches:
  - Nager.Date public holidays (Australia / NSW)
  - Wikipedia "On This Day" feed
  - TMDB film anniversaries (optional, requires TMDB_API_KEY)

One Claude Sonnet call selects the best 2-3 items and writes one charming line each.
Results cached in special_today table keyed on cycle_date.

Hard rules in Claude prompt:
  - Prefer light/wondrous/milestone events
  - Skip wars, disasters, deaths (unless major commemoration)
  - Max one fun observance
"""

import os
import json
import logging
import hashlib
from datetime import datetime, timezone, date

import requests

import dashboard_db
from pipelines._claude_helper import call_claude_json

logger = logging.getLogger(__name__)

TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
FIXTURE_MODE = os.getenv("FIXTURE_MODE", "").lower() in ("1", "true", "yes")


async def run_pipeline() -> None:
    """Generate today's special items. Idempotent — skips if already done today."""
    today = date.today().isoformat()
    existing = dashboard_db.get_special_today(today)
    if existing is not None and not FIXTURE_MODE:
        logger.info("Special today pipeline: already done for %s", today)
        return

    if FIXTURE_MODE:
        _load_fixture(today)
        return

    try:
        candidates = _gather_candidates()
        if not candidates:
            logger.warning("Special today: no candidates gathered, skipping")
            return
        items = await _curate_with_claude(candidates, today)
        dashboard_db.upsert_special_today(today, items)
        logger.info("Special today pipeline: stored %d items for %s", len(items), today)
    except Exception as e:
        logger.error("Special today pipeline error: %s", e, exc_info=True)


def _gather_candidates() -> list[dict]:
    """Gather holiday, Wikipedia, and optional TMDB candidates for today."""
    today = date.today()
    mm = f"{today.month:02d}"
    dd = f"{today.day:02d}"
    candidates: list[dict] = []

    # 1. Nager.Date public holidays (Australia / NSW)
    try:
        resp = requests.get(
            f"https://date.nager.at/api/v3/PublicHolidays/{today.year}/AU",
            timeout=10,
        )
        resp.raise_for_status()
        holidays = resp.json()
        for h in holidays:
            if h.get("date", "").endswith(f"-{mm}-{dd}"):
                # Filter to National + NSW (AU state code "NSW")
                counties = h.get("counties") or []
                if not counties or "AU-NSW" in counties:
                    candidates.append({
                        "kind": "holiday",
                        "label": h.get("localName") or h.get("name", ""),
                        "detail": h.get("name", ""),
                        "priority": 8,
                    })
    except Exception as e:
        logger.warning("Nager.Date fetch failed: %s", e)

    # 2. Wikipedia "On This Day"
    try:
        resp = requests.get(
            f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/all/{mm}/{dd}",
            headers={"User-Agent": "PersonalBrain/1.0 (personal assistant)"},
            timeout=10,
        )
        resp.raise_for_status()
        wiki_data = resp.json()
        # Mix of: events, births, deaths, holidays, selected
        for category in ["selected", "events", "holidays"]:
            items = wiki_data.get(category, [])[:5]
            for item in items:
                year = item.get("year")
                text = item.get("text", "")
                pages = item.get("pages", [])
                page_title = pages[0].get("titles", {}).get("normalized", "") if pages else ""
                label = f"{year}: {text[:120]}" if year else text[:120]
                candidates.append({
                    "kind": category,
                    "label": label,
                    "detail": page_title,
                    "priority": 6 if category == "selected" else 4,
                })
    except Exception as e:
        logger.warning("Wikipedia On This Day fetch failed: %s", e)

    # 3. TMDB film anniversaries (optional)
    if TMDB_API_KEY:
        try:
            resp = requests.get(
                "https://api.themoviedb.org/3/discover/movie",
                params={
                    "api_key": TMDB_API_KEY,
                    "primary_release_month": mm,
                    "primary_release_day": dd,
                    "sort_by": "popularity.desc",
                    "page": 1,
                },
                timeout=10,
            )
            resp.raise_for_status()
            movies = resp.json().get("results", [])[:3]
            for movie in movies:
                release = movie.get("release_date", "")
                year = release[:4] if release else "?"
                title = movie.get("title", "")
                age = today.year - int(year) if year.isdigit() else 0
                if age > 0 and age % 5 == 0:  # only round anniversaries
                    candidates.append({
                        "kind": "film",
                        "label": f"{title} premiered {age} years ago",
                        "detail": movie.get("overview", "")[:200],
                        "priority": 5,
                    })
        except Exception as e:
            logger.warning("TMDB fetch failed: %s", e)

    return candidates


async def _curate_with_claude(candidates: list[dict], today: str) -> list[dict]:
    """Ask Claude to pick the best 2-3 items and write one charming line each."""
    prompt = f"""Today is {today}. Below are candidate events/facts for a "Today is special" strip
on a dashboard. Pick the best 2-3 items. Prefer: light, wondrous, milestone, or celebratory items.
Avoid: wars, disasters, violent deaths (unless it is a major widely-known historic commemoration).
Max one fun food observance.

For each chosen item, write ONE short charming sentence (max 12 words) and pick ONE emoji.

Return ONLY valid JSON, no commentary:
{{
  "items": [
    {{"emoji": "🎬", "label": "Short charming one-liner", "kind": "film"}},
    ...
  ]
}}

Candidates:
{json.dumps(candidates, indent=2)}
"""

    result = await call_claude_json(prompt, context="special_today")
    items = result.get("items", []) if result else []

    # Ensure each item has required fields
    curated = []
    for item in items[:3]:
        if isinstance(item, dict) and item.get("label"):
            curated.append({
                "emoji": item.get("emoji", "✨"),
                "label": str(item["label"])[:120],
                "kind": item.get("kind", "event"),
                "note": item.get("note"),
            })

    # Fallback: use top 2 candidates raw if Claude failed
    if not curated:
        top = sorted(candidates, key=lambda c: -c.get("priority", 0))[:2]
        curated = [
            {"emoji": "📅", "label": c["label"][:80], "kind": c["kind"], "note": None}
            for c in top
        ]

    return curated


def _load_fixture(today: str) -> None:
    fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "special_today.json")
    try:
        with open(fixture_path) as f:
            items = json.load(f)
        dashboard_db.upsert_special_today(today, items)
        logger.info("Special today pipeline: loaded %d fixture items", len(items))
    except FileNotFoundError:
        # Default fixture
        items = [
            {"emoji": "🎂", "label": "Happy fixture day!", "kind": "personal", "note": None},
            {"emoji": "🌍", "label": "World Hello Day", "kind": "holiday", "note": None},
        ]
        dashboard_db.upsert_special_today(today, items)
