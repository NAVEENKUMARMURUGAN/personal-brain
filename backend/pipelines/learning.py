"""
pipelines/learning.py — P2: Learning Picks pipeline.

Runs every 48 hours. Sources:
  - YouTube Data API v3 (channels.yaml whitelist) — videos scored by views/hour velocity
  - RSS feeds from sources.yaml with kind=learning or kind=both
  - Explicit learning: true flag in sources.yaml

Claude rubric: score educational value (depth, code included, evergreen).
Explicitly downrank announcements and hype.
Store top 5 as kind='learning' with media_type and duration_min.

Requires: YOUTUBE_API_KEY (optional — degrades gracefully).
"""

import os
import json
import re
import logging
from datetime import date, datetime, timezone
from typing import Optional

import requests
import yaml

import dashboard_db
from pipelines._claude_helper import call_claude_json

logger = logging.getLogger(__name__)

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
FIXTURE_MODE = os.getenv("FIXTURE_MODE", "").lower() in ("1", "true", "yes")

YOUTUBE_BASE = "https://www.googleapis.com/youtube/v3"


async def run_pipeline() -> None:
    """Fetch, dedupe, score, and store top-5 learning picks. Idempotent per 48h cycle."""
    today = date.today().isoformat()

    # Check if we already ran within 48h
    last_cycle = dashboard_db.get_latest_feed_cycle("learning")
    if last_cycle and not FIXTURE_MODE:
        from datetime import timedelta
        last_dt = datetime.fromisoformat(last_cycle)
        if (datetime.now().date() - last_dt.date()).days < 2:
            logger.info("Learning pipeline: last cycle was %s, skipping", last_cycle)
            return

    if FIXTURE_MODE:
        _load_fixture(today)
        return

    try:
        raw_items = _fetch_all_sources()
        logger.info("Learning pipeline: fetched %d raw items", len(raw_items))

        if not raw_items:
            logger.warning("Learning pipeline: no items fetched")
            return

        curated = await _curate_with_claude(raw_items, today)
        if not curated:
            logger.warning("Learning pipeline: curation failed — keeping previous cycle")
            return

        dashboard_db.upsert_feed_items(curated)
        logger.info("Learning pipeline: stored %d items for %s", len(curated), today)

    except Exception as e:
        logger.error("Learning pipeline error: %s", e, exc_info=True)


def _fetch_all_sources() -> list[dict]:
    items: list[dict] = []
    items += _fetch_youtube_channels()
    items += _fetch_learning_rss()
    return items


def _fetch_youtube_channels() -> list[dict]:
    """Fetch recent uploads from whitelisted YouTube channels."""
    if not YOUTUBE_API_KEY:
        logger.warning("YOUTUBE_API_KEY not set — skipping YouTube source")
        return []

    items: list[dict] = []
    channels_path = os.path.join(os.path.dirname(__file__), "..", "channels.yaml")
    try:
        with open(channels_path) as f:
            config = yaml.safe_load(f)
        channels = config.get("channels", [])
    except Exception as e:
        logger.warning("channels.yaml load failed: %s", e)
        return []

    for channel in channels:
        # uploads_playlist_id = channel_id with "UC" → "UU"
        playlist_id = channel.get("uploads_playlist_id", "")
        if not playlist_id and channel.get("channel_id", "").startswith("UC"):
            playlist_id = "UU" + channel["channel_id"][2:]
        if not playlist_id:
            continue
        try:
            # Fetch recent uploads via playlistItems (1 unit per call)
            resp = requests.get(
                f"{YOUTUBE_BASE}/playlistItems",
                params={
                    "key": YOUTUBE_API_KEY,
                    "playlistId": playlist_id,
                    "part": "snippet,contentDetails",
                    "maxResults": 5,
                },
                timeout=10,
            )
            resp.raise_for_status()
            playlist_data = resp.json()
            video_ids = [
                item["contentDetails"]["videoId"]
                for item in playlist_data.get("items", [])
                if "contentDetails" in item
            ]

            if not video_ids:
                continue

            # Fetch video stats for view velocity calculation
            stats_resp = requests.get(
                f"{YOUTUBE_BASE}/videos",
                params={
                    "key": YOUTUBE_API_KEY,
                    "id": ",".join(video_ids),
                    "part": "statistics,contentDetails,snippet",
                },
                timeout=10,
            )
            stats_resp.raise_for_status()
            videos = stats_resp.json().get("items", [])

            for video in videos:
                snippet = video.get("snippet", {})
                stats = video.get("statistics", {})
                content = video.get("contentDetails", {})

                title = snippet.get("title", "").strip()
                vid_id = video.get("id", "")
                if not title or not vid_id:
                    continue

                # Duration: parse ISO 8601 duration (PT1H2M3S)
                duration_iso = content.get("duration", "PT0M")
                duration_min = _parse_duration_minutes(duration_iso)

                # Views per hour velocity
                view_count = int(stats.get("viewCount", 0) or 0)
                published_at = snippet.get("publishedAt")
                views_per_hour = 0.0
                if published_at:
                    try:
                        pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                        age_hours = max(1, (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600)
                        views_per_hour = view_count / age_hours
                    except Exception:
                        pass

                items.append({
                    "title": title,
                    "source_url": f"https://www.youtube.com/watch?v={vid_id}",
                    "source_name": channel["name"],
                    "published_at": published_at,
                    "summary_raw": (snippet.get("description") or "")[:300],
                    "media_type": "video",
                    "duration_min": duration_min,
                    "views_per_hour": views_per_hour,
                })

        except Exception as e:
            logger.warning("YouTube fetch failed for channel %s: %s", channel.get("name"), e)

    return items


def _fetch_learning_rss() -> list[dict]:
    """Fetch RSS feeds tagged as learning sources."""
    items: list[dict] = []
    try:
        import feedparser
        sources_path = os.path.join(os.path.dirname(__file__), "..", "sources.yaml")
        with open(sources_path) as f:
            config = yaml.safe_load(f)
        for feed_cfg in config.get("feeds", []):
            if feed_cfg.get("kind") not in ("learning", "both"):
                continue
            try:
                parsed = feedparser.parse(feed_cfg["url"])
                for entry in parsed.entries[:5]:
                    title = getattr(entry, "title", "").strip()
                    link = getattr(entry, "link", "")
                    if not title or not link:
                        continue
                    summary = getattr(entry, "summary", "") or ""
                    summary = re.sub(r"<[^>]+>", " ", summary).strip()[:500]
                    items.append({
                        "title": title,
                        "source_url": link,
                        "source_name": feed_cfg["name"],
                        "published_at": getattr(entry, "published", None),
                        "summary_raw": summary,
                        "media_type": "article",
                        "duration_min": None,
                        "views_per_hour": 0.0,
                    })
            except Exception as e:
                logger.warning("Learning RSS fetch failed (%s): %s", feed_cfg.get("url"), e)
    except Exception as e:
        logger.warning("Learning RSS load error: %s", e)
    return items


async def _curate_with_claude(items: list[dict], today: str) -> Optional[list[dict]]:
    """Score and summarise learning content with Claude."""
    candidates = items[:30]

    # Build the items JSON outside the f-string to avoid double-brace confusion
    candidates_json = json.dumps(
        [
            {
                "title": i["title"],
                "source": i["source_name"],
                "media_type": i["media_type"],
                "snippet": i.get("summary_raw", "")[:200],
            }
            for i in candidates
        ],
        indent=2,
    )

    prompt = f"""You curate learning content for an AI engineer building LLM apps (RAG, agents, evals).
Today is {today}.

Score each item 1-10 for educational value. High scores = depth, code examples, evergreen,
practical concepts, framework deep-dives. Low scores = surface-level announcements, hype, clickbait.

Also assign ONE tag from: RAG | agents | infra | research | fundamentals | evals | tools | architecture | local-llm

Return ONLY valid JSON:
{{
  "items": [
    {{
      "title": "exact title from input",
      "score": 9.0,
      "tag": "RAG",
      "summary_short": "One sentence (max 15 words).",
      "summary_detail": "5-8 lines — what you'll learn, why it matters, key takeaway."
    }},
    ...
  ]
}}

Items:
{candidates_json}
"""

    result = await call_claude_json(prompt, context="learning_curation", max_tokens=6144)
    if not result:
        return None

    scored_items = result.get("items", [])
    title_lookup = {i["title"]: i for i in candidates}

    enriched: list[dict] = []
    for scored in scored_items:
        title = scored.get("title", "")
        original = title_lookup.get(title)
        if not original:
            # Try partial match
            for orig in candidates:
                if title.lower()[:30] in orig["title"].lower():
                    original = orig
                    break
        if not original:
            continue
        enriched.append({
            "kind": "learning",
            "title": title,
            "source_name": original["source_name"],
            "source_url": original["source_url"],
            "published_at": original.get("published_at"),
            "score": float(scored.get("score", 5.0)),
            "tag": scored.get("tag", "fundamentals"),
            "summary_short": scored.get("summary_short", ""),
            "summary_detail": scored.get("summary_detail", ""),
            "media_type": original.get("media_type", "article"),
            "duration_min": original.get("duration_min"),
            "cycle_date": today,
        })

    enriched.sort(key=lambda x: -x["score"])
    for i, item in enumerate(enriched[:5]):
        item["rank"] = i + 1

    return enriched[:5]


def _parse_duration_minutes(iso_duration: str) -> Optional[int]:
    """Parse ISO 8601 duration string (e.g. PT1H2M3S) to total minutes."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not m:
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    return hours * 60 + minutes or None


def _load_fixture(today: str) -> None:
    fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "learning.json")
    try:
        with open(fixture_path) as f:
            items = json.load(f)
        for i, item in enumerate(items[:5]):
            item["kind"] = "learning"
            item["cycle_date"] = today
            item["rank"] = i + 1
        dashboard_db.upsert_feed_items(items[:5])
        logger.info("Learning pipeline: loaded %d fixture items", len(items[:5]))
    except FileNotFoundError:
        logger.warning("Learning fixture file not found")
