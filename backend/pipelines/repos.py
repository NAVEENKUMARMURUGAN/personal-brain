"""
pipelines/repos.py — P3: Trending GitHub Repos pipeline.

Runs daily. Searches GitHub for repos tagged with AI/ML topics,
snapshots star counts, computes 7-day velocity, and asks Claude
to write a "why it matters" line for the top 6.

Requires: GITHUB_TOKEN env var (optional — rate-limited without it).
Degrades gracefully if key is absent: uses unauthenticated GitHub API (60 req/h).
"""

import os
import json
import logging
from datetime import date

import requests

import dashboard_db
from pipelines._claude_helper import call_claude_json

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
FIXTURE_MODE = os.getenv("FIXTURE_MODE", "").lower() in ("1", "true", "yes")

SEARCH_TOPICS = ["llm", "rag", "agents", "vector-database", "large-language-model"]
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"

# Keywords from the user's stack — used in Claude prompt context
USER_STACK_KEYWORDS = [
    "qdrant", "anthropic", "claude", "voyage", "fastapi", "react",
    "typescript", "sqlite", "python", "rag", "vector", "embedding",
]


async def run_pipeline() -> None:
    """Fetch trending repos, snapshot stars, compute velocity, curate top 6. Idempotent."""
    today = date.today().isoformat()
    existing = dashboard_db.get_repo_trends(today)
    if existing and not FIXTURE_MODE:
        logger.info("Repos pipeline: already done for %s", today)
        return

    if FIXTURE_MODE:
        _load_fixture(today)
        return

    try:
        repos = _fetch_candidate_repos()
        if not repos:
            logger.warning("Repos pipeline: no repos fetched")
            return

        # Snapshot current star counts
        for repo in repos:
            dashboard_db.upsert_repo_snapshot(repo["full_name"], repo["stars"], today)

        # Compute 7-day velocity
        for repo in repos:
            stars_7d_ago = dashboard_db.get_stars_7d_ago(repo["full_name"], today)
            if stars_7d_ago is not None:
                repo["stars_gained_7d"] = max(0, repo["stars"] - stars_7d_ago)
            else:
                # Fallback: use recent_stars field from search if available
                repo["stars_gained_7d"] = repo.get("recent_stars", 0)

        # Sort by velocity, take top 6
        repos.sort(key=lambda r: -r.get("stars_gained_7d", 0))
        top6 = repos[:6]

        # Ask Claude for "why it matters" lines
        top6 = await _add_why_it_matters(top6)

        dashboard_db.upsert_repo_trends(top6, today)
        logger.info("Repos pipeline: stored %d repos for %s", len(top6), today)

    except Exception as e:
        logger.error("Repos pipeline error: %s", e, exc_info=True)


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers


def _fetch_candidate_repos() -> list[dict]:
    """Search GitHub across relevant topics, deduplicate by full_name."""
    seen: set[str] = set()
    repos: list[dict] = []

    for topic in SEARCH_TOPICS:
        try:
            resp = requests.get(
                GITHUB_SEARCH_URL,
                params={
                    "q": f"topic:{topic} pushed:>2024-01-01",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 15,
                },
                headers=_github_headers(),
                timeout=15,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            for item in items:
                fn = item.get("full_name", "")
                if fn and fn not in seen:
                    seen.add(fn)
                    repos.append({
                        "full_name": fn,
                        "description": (item.get("description") or "")[:200],
                        "language": item.get("language"),
                        "stars": item.get("stargazers_count", 0),
                        "recent_stars": 0,
                    })
        except Exception as e:
            logger.warning("GitHub search failed for topic %s: %s", topic, e)

    return repos


async def _add_why_it_matters(repos: list[dict]) -> list[dict]:
    """Ask Claude to write a 'why it matters' line for each repo."""
    if not repos:
        return repos

    # Build JSON outside f-string to avoid double-brace/dict-literal conflict
    repos_json = json.dumps(
        [
            {
                "full_name": r["full_name"],
                "description": r["description"],
                "language": r["language"],
            }
            for r in repos
        ],
        indent=2,
    )
    stack_str = ", ".join(USER_STACK_KEYWORDS)

    prompt = f"""Below are {len(repos)} trending AI/ML GitHub repositories.
For each repo, write ONE punchy sentence (max 15 words) explaining why it matters to an AI engineer
who builds RAG pipelines, agents, and LLM apps using this stack: {stack_str}.
If a repo directly touches a stack item, mention it.

Return ONLY valid JSON:
{{
  "repos": [
    {{"full_name": "org/repo", "why_it_matters": "One punchy sentence."}},
    ...
  ]
}}

Repos:
{repos_json}
"""

    result = await call_claude_json(prompt, context="repos_why_it_matters")
    if not result:
        return repos

    why_map = {item["full_name"]: item.get("why_it_matters", "") for item in result.get("repos", [])}
    for repo in repos:
        repo["why_it_matters"] = why_map.get(repo["full_name"], repo.get("description", ""))[:200]

    return repos


def _load_fixture(today: str) -> None:
    fixture_path = os.path.join(os.path.dirname(__file__), "..", "fixtures", "repos.json")
    try:
        with open(fixture_path) as f:
            items = json.load(f)
        # Snapshot stars for fixture repos too
        for item in items:
            dashboard_db.upsert_repo_snapshot(item["full_name"], item.get("stars", 0), today)
        dashboard_db.upsert_repo_trends(items, today)
        logger.info("Repos pipeline: loaded %d fixture repos", len(items))
    except FileNotFoundError:
        logger.warning("Repos fixture file not found")
