"""
pipelines/web_search.py — Lightweight web search for the Explore pipeline.

Priority:
  1. Serper.dev (Google) — if SERPER_API_KEY is set (best quality, 2500 free/month)
  2. Brave Search API    — if BRAVE_SEARCH_API_KEY is set (free tier: 2000/month)
  3. DuckDuckGo Instant Answer API — keyless fallback, best-effort

Returns a list of SearchResult dicts:
  [{"title": str, "url": str, "snippet": str}, ...]

Usage:
  from pipelines.web_search import search_web
  results = search_web("Transformer architecture 2024", max_results=5)

The results are injected into the explore prompt so Claude can reference
current information rather than relying solely on training data.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

SERPER_API_KEY  = os.getenv("SERPER_API_KEY", "")
SERPER_ENDPOINT = "https://google.serper.dev/search"

BRAVE_API_KEY  = os.getenv("BRAVE_SEARCH_API_KEY", "")
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

DDG_ENDPOINT   = "https://api.duckduckgo.com/"

# How long to wait for a search response before giving up (seconds)
SEARCH_TIMEOUT = 6


def search_web(topic: str, max_results: int = 5) -> list[dict]:
    """
    Fetch web search results for a topic.
    Returns up to max_results results, each with title / url / snippet.
    Never raises — returns [] on any error so the pipeline degrades gracefully.
    Priority: Serper (Google) → Brave → DuckDuckGo
    """
    if SERPER_API_KEY:
        results = _serper_search(topic, max_results)
        if results:
            logger.info("web_search: serper returned %d results for %r", len(results), topic[:50])
            return results
        logger.warning("web_search: serper returned nothing, trying brave")

    if BRAVE_API_KEY:
        results = _brave_search(topic, max_results)
        if results:
            logger.info("web_search: brave returned %d results for %r", len(results), topic[:50])
            return results
        logger.warning("web_search: brave returned nothing, falling back to DDG")

    results = _ddg_search(topic, max_results)
    logger.info("web_search: ddg returned %d results for %r", len(results), topic[:50])
    return results


def _serper_search(topic: str, max_results: int) -> list[dict]:
    """
    Serper.dev — Google Search results via API.
    Returns real web results with title, link, snippet, and optional date.
    Sign up free at serper.dev — 2500 queries/month on free plan.
    """
    try:
        resp = requests.post(
            SERPER_ENDPOINT,
            headers={
                "X-API-KEY": SERPER_API_KEY,
                "Content-Type": "application/json",
            },
            json={"q": topic, "num": max_results},
            timeout=SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("organic", [])[:max_results]:
            snippet = item.get("snippet", "")
            # Serper sometimes includes a date alongside snippet
            date_str = item.get("date", "")
            if date_str:
                snippet = f"[{date_str}] {snippet}"
            results.append({
                "title":   item.get("title", ""),
                "url":     item.get("link", ""),
                "snippet": snippet[:400],
            })
        return results

    except Exception as e:
        logger.warning("web_search: serper error: %s", e)
        return []


def _brave_search(topic: str, max_results: int) -> list[dict]:
    """Brave Search API — returns structured web results."""
    try:
        resp = requests.get(
            BRAVE_ENDPOINT,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_API_KEY,
            },
            params={
                "q": topic,
                "count": max_results,
                "search_lang": "en",
                "result_filter": "web",
                "freshness": "py",  # past year — keeps results current
            },
            timeout=SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("web", {}).get("results", [])[:max_results]:
            snippet = (
                item.get("description")
                or item.get("extra_snippets", [""])[0]
                or ""
            )
            results.append({
                "title":   item.get("title", ""),
                "url":     item.get("url", ""),
                "snippet": snippet[:400],
            })
        return results

    except Exception as e:
        logger.warning("web_search: brave error: %s", e)
        return []


def _ddg_search(topic: str, max_results: int) -> list[dict]:
    """
    DuckDuckGo Instant Answer API — keyless, no rate limit published.
    Returns the abstract + related topics as search-result-like dicts.
    Weaker than Brave (no full web results) but always available.
    """
    try:
        resp = requests.get(
            DDG_ENDPOINT,
            params={
                "q": topic,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            },
            timeout=SEARCH_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []

        # Main abstract (Wikipedia-sourced usually)
        if data.get("Abstract") and data.get("AbstractURL"):
            results.append({
                "title":   data.get("Heading", topic),
                "url":     data["AbstractURL"],
                "snippet": data["Abstract"][:400],
            })

        # Related topics
        for item in data.get("RelatedTopics", []):
            if len(results) >= max_results:
                break
            # RelatedTopics can have nested "Topics" lists
            if "Topics" in item:
                for sub in item["Topics"]:
                    if len(results) >= max_results:
                        break
                    text = sub.get("Text", "")
                    url  = sub.get("FirstURL", "")
                    if text and url:
                        results.append({"title": text[:80], "url": url, "snippet": text[:400]})
            else:
                text = item.get("Text", "")
                url  = item.get("FirstURL", "")
                if text and url:
                    results.append({"title": text[:80], "url": url, "snippet": text[:400]})

        return results[:max_results]

    except Exception as e:
        logger.warning("web_search: ddg error: %s", e)
        return []


def format_for_prompt(results: list[dict]) -> str:
    """
    Format search results as a compact block to inject into the Claude prompt.
    Returns empty string if results is empty.
    """
    if not results:
        return ""

    lines = ["Current web context (use to supplement your training knowledge — prefer specific facts, dates, and recent developments from these sources):"]
    for i, r in enumerate(results, 1):
        lines.append(f"\n[{i}] {r['title']}")
        lines.append(f"    URL: {r['url']}")
        if r["snippet"]:
            lines.append(f"    {r['snippet']}")

    return "\n".join(lines)
