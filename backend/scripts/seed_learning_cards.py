"""
scripts/seed_learning_cards.py — One-time seeding of learning_cards table.

Usage:
    cd backend
    ANTHROPIC_API_KEY=... python -m scripts.seed_learning_cards

Two modes:
    1. Default: generates ~200 cards via Claude Sonnet across 6 pathway nodes.
    2. --fixture: loads the bundled fixture file (no API calls, safe for dev).
    3. --count N: generate N cards per pathway node (default: 8).

The script is idempotent: it skips cards whose term already exists (case-insensitive).
It emits a JSON file dashboard_cards_review.json for manual review alongside writing to SQLite.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

# Add parent dir to path so we can import backend modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dashboard_db
from pipelines._claude_helper import call_claude_json

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PATHWAY_NODES = [
    "fundamentals",
    "embeddings",
    "RAG",
    "agents",
    "evals",
    "production",
]

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "..", "fixtures", "learning_cards_seed.json")

PATHWAY_DESCRIPTIONS = {
    "fundamentals": "Core LLM concepts: tokens, context windows, temperature, prompting, hallucination, system prompts, structured output",
    "embeddings": "Embedding models, vector spaces, cosine similarity, dimensionality, matryoshka embeddings, sparse vs dense vectors",
    "RAG": "Retrieval-Augmented Generation: chunking, indexing, hybrid search, BM25, re-ranking, HNSW, semantic caching",
    "agents": "Agentic AI: tool use, function calling, ReAct, agent memory types, multi-agent coordination, planning, interruption",
    "evals": "Evaluation: LLM-as-judge, RAGAS, faithfulness, answer relevancy, regression testing, red-teaming, benchmark datasets",
    "production": "Production deployment: quantisation, batching, caching, latency budgets, observability, cost management, versioning",
}


async def generate_cards_for_node(node: str, count: int) -> list[dict]:
    """Ask Claude to generate `count` learning cards for a pathway node."""
    prompt = f"""Generate exactly {count} spaced-repetition flashcards for an AI engineer
learning about the "{node}" pathway node.

Topic coverage: {PATHWAY_DESCRIPTIONS[node]}

Rules:
- Each card must teach ONE distinct, atomic concept.
- No duplicates across cards.
- explanation: 2-4 sentences, plain English, no jargon without definition.
- usage_line: one real-world use case sentence (max 15 words).
- code_example: 4-8 lines of practical Python or pseudocode (null if not applicable).
- term: concise name (2-5 words max).

Return ONLY valid JSON:
{{
  "cards": [
    {{
      "term": "Concept Name",
      "explanation": "Clear explanation in plain English.",
      "usage_line": "Where you would use this in a real system.",
      "code_example": "optional_code_or_null",
      "pathway_node": "{node}"
    }},
    ...
  ]
}}
"""

    result = await call_claude_json(prompt, context=f"seed_cards_{node}", max_tokens=8192)
    if not result:
        logger.error("Failed to generate cards for node: %s", node)
        return []

    cards = result.get("cards", [])
    # Ensure pathway_node is set correctly
    for card in cards:
        card["pathway_node"] = node

    return cards[:count]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed learning cards into the dashboard database.")
    parser.add_argument("--fixture", action="store_true", help="Load from fixture file (no API calls)")
    parser.add_argument("--count", type=int, default=8, help="Cards per pathway node (default: 8, total ~48)")
    parser.add_argument("--nodes", nargs="+", default=PATHWAY_NODES, help="Which nodes to seed (default: all)")
    args = parser.parse_args()

    dashboard_db.ensure_dashboard_tables()
    existing_count = dashboard_db.count_learning_cards()
    logger.info("Existing learning cards: %d", existing_count)

    if args.fixture:
        logger.info("Loading cards from fixture file: %s", FIXTURE_PATH)
        with open(FIXTURE_PATH) as f:
            all_cards = json.load(f)
        inserted = dashboard_db.insert_learning_cards(all_cards)
        logger.info("Inserted %d cards from fixture (skipped duplicates)", inserted)
        _write_review_file(all_cards)
        return

    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY not set. Use --fixture for dev mode without API.")
        sys.exit(1)

    all_cards: list[dict] = []
    for node in args.nodes:
        if node not in PATHWAY_NODES:
            logger.warning("Unknown node: %s — skipping", node)
            continue
        logger.info("Generating %d cards for node: %s", args.count, node)
        cards = await generate_cards_for_node(node, args.count)
        logger.info("  → got %d cards", len(cards))
        all_cards.extend(cards)

    if not all_cards:
        logger.error("No cards generated — exiting")
        sys.exit(1)

    inserted = dashboard_db.insert_learning_cards(all_cards)
    logger.info("Inserted %d new cards (skipped %d duplicates)", inserted, len(all_cards) - inserted)

    total = dashboard_db.count_learning_cards()
    logger.info("Total learning cards in DB: %d", total)

    _write_review_file(all_cards)


def _write_review_file(cards: list[dict]) -> None:
    out_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_cards_review.json")
    with open(out_path, "w") as f:
        json.dump(cards, f, indent=2)
    logger.info("Review file written: %s (%d cards)", out_path, len(cards))


if __name__ == "__main__":
    asyncio.run(main())
