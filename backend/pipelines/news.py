"""
pipelines/news.py — P1: AI News pipeline.

Runs every 6 hours. Sources:
  - Hacker News Algolia API (front page + LLM/RAG queries)
  - arXiv API (cs.CL, cs.AI — most recent 30)
  - RSS feeds from sources.yaml (all feeds with kind=news or kind=both)
  - Hugging Face trending models API

Pipeline:
  1. Fetch + normalise to unified schema
  2. Hash dedup (content_hash = SHA-256 of url+title) against feed_raw
  3. Semantic dedup via brain.embed() + Qdrant feed_dedupe collection (cosine > 0.88, 7-day window)
  4. One batched Claude call for scoring + tagging + summarising
  5. Store top 10 as kind='news' with today's cycle_date
"""

import os
import json
import logging
import hashlib
import re
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import requests
import yaml

import dashboard_db
from pipelines._claude_helper import call_claude_json

# brain is only needed for semantic dedup — imported lazily so fixture mode
# works without qdrant_client installed
brain_module = None

def _get_brain():
    global brain_module
    if brain_module is None:
        import brain as _brain
        brain_module = _brain
    return brain_module

logger = logging.getLogger(__name__)

FIXTURE_MODE = os.getenv("FIXTURE_MODE", "").lower() in ("1", "true", "yes")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
DEDUP_COLLECTION = "feed_dedupe"
DEDUP_THRESHOLD = 0.88
DEDUP_WINDOW_DAYS = 7


async def run_pipeline() -> None:
    """Fetch, dedupe, score, and store top-10 AI news items. Idempotent per cycle_date."""
    today = date.today().isoformat()
    existing = dashboard_db.get_feed_items("news", today)
    if existing and not FIXTURE_MODE:
        logger.info("News pipeline: already done for %s", today)
        return

    if FIXTURE_MODE:
        _load_fixture(today)
        return

    try:
        raw_items = _fetch_all_sources()
        logger.info("News pipeline: fetched %d raw items", len(raw_items))

        deduped = _hash_dedup(raw_items)
        logger.info("News pipeline: %d items after hash dedup", len(deduped))

        deduped = await _semantic_dedup(deduped)
        logger.info("News pipeline: %d items after semantic dedup", len(deduped))

        if not deduped:
            logger.warning("News pipeline: no items after dedup — falling back to previous cycle")
            return

        curated = await _curate_with_claude(deduped, today)
        if not curated:
            logger.warning("News pipeline: curation failed — keeping previous cycle")
            return

        dashboard_db.upsert_feed_items(curated)
        logger.info("News pipeline: stored %d items for %s", len(curated), today)

    except Exception as e:
        logger.error("News pipeline error: %s", e, exc_info=True)


# ── Source fetchers ────────────────────────────────────────────

def _fetch_all_sources() -> list[dict]:
    items: list[dict] = []
    items += _fetch_hackernews()
    items += _fetch_arxiv()
    items += _fetch_rss_feeds()
    items += _fetch_huggingface_trending()
    return items


def _fetch_hackernews() -> list[dict]:
    """Fetch HN front page + LLM/RAG topic queries from Algolia API."""
    items: list[dict] = []
    queries = ["", "LLM", "RAG", "Claude", "GPT", "embedding", "agents"]
    base = "https://hn.algolia.com/api/v1"
    for q in queries:
        try:
            if q:
                url = f"{base}/search?query={q}&tags=story&hitsPerPage=10"
            else:
                url = f"{base}/search?tags=front_page&hitsPerPage=30"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            hits = resp.json().get("hits", [])
            for hit in hits:
                story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
                title = hit.get("title", "").strip()
                if not title:
                    continue
                items.append({
                    "title": title,
                    "source_url": story_url,
                    "source_name": "Hacker News",
                    "published_at": _ts_to_iso(hit.get("created_at_i")),
                    "summary_raw": hit.get("story_text") or "",
                    "media_type": "article",
                })
        except Exception as e:
            logger.warning("HN fetch failed (query=%r): %s", q, e)
    return items


def _fetch_arxiv() -> list[dict]:
    """Fetch recent arXiv papers in cs.CL and cs.AI."""
    items: list[dict] = []
    for cat in ["cs.CL", "cs.AI"]:
        try:
            resp = requests.get(
                f"http://export.arxiv.org/api/query?search_query=cat:{cat}&sortBy=submittedDate&sortOrder=descending&max_results=15",
                timeout=15,
            )
            resp.raise_for_status()
            # Parse Atom XML minimally
            text = resp.text
            entries = text.split("<entry>")[1:]
            for entry in entries:
                title = _xml_field(entry, "title")
                url = _xml_field(entry, "id")
                published = _xml_field(entry, "published")
                summary = _xml_field(entry, "summary")
                if title and url:
                    items.append({
                        "title": title.strip(),
                        "source_url": url.strip(),
                        "source_name": f"arXiv {cat}",
                        "published_at": published.strip() if published else None,
                        "summary_raw": (summary or "").strip()[:500],
                        "media_type": "article",
                    })
        except Exception as e:
            logger.warning("arXiv fetch failed (cat=%s): %s", cat, e)
    return items


def _fetch_rss_feeds() -> list[dict]:
    """Fetch all feeds from sources.yaml with kind=news or kind=both."""
    items: list[dict] = []
    try:
        import feedparser
        sources_path = os.path.join(os.path.dirname(__file__), "..", "sources.yaml")
        with open(sources_path) as f:
            config = yaml.safe_load(f)
        feeds = config.get("feeds", [])
        for feed_cfg in feeds:
            if feed_cfg.get("kind") not in ("news", "both"):
                continue
            try:
                parsed = feedparser.parse(feed_cfg["url"])
                for entry in parsed.entries[:10]:
                    title = getattr(entry, "title", "").strip()
                    link = getattr(entry, "link", "")
                    if not title or not link:
                        continue
                    summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
                    # Strip HTML tags from summary
                    summary = re.sub(r"<[^>]+>", " ", summary).strip()[:500]
                    published = getattr(entry, "published", None)
                    items.append({
                        "title": title,
                        "source_url": link,
                        "source_name": feed_cfg["name"],
                        "published_at": published,
                        "summary_raw": summary,
                        "media_type": "article",
                    })
            except Exception as e:
                logger.warning("RSS fetch failed (%s): %s", feed_cfg.get("url"), e)
    except Exception as e:
        logger.warning("RSS feed loading error: %s", e)
    return items


def _fetch_huggingface_trending() -> list[dict]:
    """Fetch trending models from Hugging Face."""
    items: list[dict] = []
    try:
        resp = requests.get(
            "https://huggingface.co/api/models?sort=likes7d&limit=10&direction=-1",
            timeout=10,
        )
        resp.raise_for_status()
        models = resp.json()
        for model in models[:10]:
            model_id = model.get("modelId") or model.get("id", "")
            if not model_id:
                continue
            title = f"Trending model: {model_id}"
            items.append({
                "title": title,
                "source_url": f"https://huggingface.co/{model_id}",
                "source_name": "Hugging Face",
                "published_at": model.get("lastModified"),
                "summary_raw": f"Pipeline: {model.get('pipeline_tag', 'unknown')}. Tags: {', '.join((model.get('tags') or [])[:5])}",
                "media_type": "article",
            })
    except Exception as e:
        logger.warning("HuggingFace trending fetch failed: %s", e)
    return items


# ── Dedup ──────────────────────────────────────────────────────

def _content_hash(item: dict) -> str:
    raw = f"{item.get('source_url', '')}\x00{item.get('title', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_dedup(items: list[dict]) -> list[dict]:
    """Remove items already seen by content_hash; store new ones in feed_raw."""
    seen_hashes: set[str] = set()
    deduped: list[dict] = []
    for item in items:
        h = _content_hash(item)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        if dashboard_db.raw_hash_exists(h):
            continue  # already processed in a previous cycle
        dashboard_db.insert_feed_raw(item.get("source_name", "unknown"), item, h)
        deduped.append(item)
    return deduped


async def _semantic_dedup(items: list[dict]) -> list[dict]:
    """
    Remove near-duplicates using vector similarity.
    Embeds title+summary and checks against Qdrant feed_dedupe collection.
    Items with cosine similarity > DEDUP_THRESHOLD against a recently-stored item are dropped.
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import (
            VectorParams, Distance, PointStruct, Filter,
            FieldCondition, Range, SearchParams,
        )
        client = QdrantClient(url=QDRANT_URL)
        # Ensure feed_dedupe collection exists (1536-dim, cosine)
        existing = [c.name for c in client.get_collections().collections]
        if DEDUP_COLLECTION not in existing:
            client.create_collection(
                collection_name=DEDUP_COLLECTION,
                vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
            )

        cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUP_WINDOW_DAYS)).timestamp()
        kept: list[dict] = []
        new_points: list[PointStruct] = []

        for item in items:
            text = f"{item['title']} {item.get('summary_raw', '')}".strip()
            try:
                vector = _get_brain().embed(text)
            except Exception as e:
                logger.warning("Embed failed for dedup: %s", e)
                kept.append(item)
                continue

            # Search for near-duplicates within the time window
            results = client.search(
                collection_name=DEDUP_COLLECTION,
                query_vector=vector,
                limit=1,
                score_threshold=DEDUP_THRESHOLD,
                query_filter=Filter(
                    must=[FieldCondition(key="ts", range=Range(gte=cutoff))]
                ),
            )
            if results:
                continue  # duplicate — drop

            # Keep and queue for insertion
            import uuid
            kept.append(item)
            new_points.append(PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={"title": item["title"], "ts": datetime.now(timezone.utc).timestamp()},
            ))

        if new_points:
            client.upsert(collection_name=DEDUP_COLLECTION, points=new_points)

        return kept

    except Exception as e:
        logger.warning("Semantic dedup failed: %s — skipping semantic dedup", e)
        return items


# ── Claude curation ────────────────────────────────────────────

async def _curate_with_claude(items: list[dict], today: str) -> Optional[list[dict]]:
    """Score, tag, and summarise items with a single batched Claude call."""
    # Cap at 40 candidates to keep prompt size manageable
    candidates = items[:40]

    prompt = f"""You curate AI/ML news for an AI engineer who builds production LLM apps
(RAG pipelines, agents, evaluation frameworks, local/cloud LLM deployment).
Today is {today}.

Score each item 1-10 for must-read value. High scores = novel/actionable/significant.
Low scores = hype, vague announcements, off-topic.

Also assign ONE tag per item from: new-model | RAG | agents | infra | research | regulation | tools | datasets | breaking

For the top-scored items, write:
  summary_short: one sentence (max 15 words)
  summary_detail: 5-8 lines — what happened, why it matters, one practical implication

Return ONLY valid JSON:
{{
  "items": [
    {{
      "title": "exact title from input",
      "score": 8.5,
      "tag": "new-model",
      "summary_short": "One sentence.",
      "summary_detail": "5-8 line detail..."
    }},
    ...
  ]
}}

Items to evaluate:
{json.dumps([{{"title": i["title"], "source": i["source_name"], "url": i["source_url"], "snippet": i.get("summary_raw", "")[:200]}} for i in candidates], indent=2)}
"""

    result = await call_claude_json(prompt, context="news_curation", max_tokens=8192)
    if not result:
        return None

    scored_items = result.get("items", [])
    # Build a lookup by title
    title_lookup = {i["title"]: i for i in candidates}

    enriched: list[dict] = []
    for scored in scored_items:
        title = scored.get("title", "")
        original = title_lookup.get(title) or _find_by_partial_title(title, candidates)
        if not original:
            continue
        enriched.append({
            "kind": "news",
            "title": title,
            "source_name": original["source_name"],
            "source_url": original["source_url"],
            "published_at": original.get("published_at"),
            "score": float(scored.get("score", 5.0)),
            "tag": scored.get("tag", "research"),
            "summary_short": scored.get("summary_short", ""),
            "summary_detail": scored.get("summary_detail", ""),
            "media_type": original.get("media_type", "article"),
            "cycle_date": today,
        })

    # Sort by score, rank top 10
    enriched.sort(key=lambda x: -x["score"])
    for i, item in enumerate(enriched[:10]):
        item["rank"] = i + 1

    return enriched[:10]


def _find_by_partial_title(title: str, items: list[dict]) -> Optional[dict]:
    """Fuzzy title match — Claude sometimes truncates titles slightly."""
    title_lower = title.lower()[:40]
    for item in items:
        if title_lower in item["title"].lower():
            return item
    return None


# ── Utilities ──────────────────────────────────────────────────

def _ts_to_iso(ts) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _xml_field(xml: str, field: str) -> Optional[str]:
    """Minimal XML field extractor for arXiv Atom feed."""
    import re
    m = re.search(rf"<{field}[^>]*>(.*?)</{field}>", xml, re.DOTALL)
    return m.group(1).strip() if m else None


def _load_fixture(today: str) -> None:
    fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "news.json")
    try:
        with open(fixture_path) as f:
            items = json.load(f)
        for i, item in enumerate(items[:10]):
            item["kind"] = "news"
            item["cycle_date"] = today
            item["rank"] = i + 1
        dashboard_db.upsert_feed_items(items[:10])
        logger.info("News pipeline: loaded %d fixture items", len(items[:10]))
    except FileNotFoundError:
        logger.warning("News fixture file not found")
