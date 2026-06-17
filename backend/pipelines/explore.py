"""
pipelines/explore.py — Topic Explorer generation pipeline.

Given a topic string, produces a structured learning package:
  - ELI5 overview with key concepts, why it matters, misconceptions
  - Mermaid mind map syntax
  - 8 flashcards (Q&A pairs)
  - 5 quiz questions (MCQ with explanations)

One Claude call, structured JSON output. SQLite cache per (user_id, topic_slug).
Context-aware: injects user's existing related memories into the prompt so the
explanation references what they already know.
"""

import json
import logging
import os
import random
from typing import Optional

import anthropic

import brain
import explore_db

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5")

SURPRISE_SEED_TOPICS = [
    "Transformer architecture",
    "Retrieval-Augmented Generation",
    "Attention mechanisms",
    "Vector databases",
    "Reinforcement learning from human feedback",
    "Mixture of Experts",
    "Constitutional AI",
    "Prompt injection attacks",
    "Chain of thought reasoning",
    "Model quantization",
    "LoRA fine-tuning",
    "Semantic search",
    "Agentic AI systems",
    "Multimodal models",
    "Speculative decoding",
]

SYSTEM_PROMPT = """You are an expert educator who explains complex topics clearly and engagingly.
You always output valid JSON matching the exact schema provided.
No markdown code fences. No extra keys. No trailing commas. Pure JSON only."""


async def generate_exploration(
    topic: str,
    user_id: str,
    force: bool = False,
) -> Optional[dict]:
    """
    Generate (or return cached) a topic exploration.

    Args:
        topic:   The topic to explore.
        user_id: Scopes cache and memory lookup.
        force:   If True, bypass cache and regenerate.

    Returns:
        Dict with keys: id, topic, topic_slug, content, created_at
        content has: overview, mindmap_mermaid, flashcards, quiz
    """
    topic = topic.strip()
    if not topic:
        return None

    slug = explore_db.slugify(topic)

    # Return cached result unless force=True
    if not force:
        cached = explore_db.get_exploration(user_id, slug)
        if cached:
            logger.info("Explore cache hit: user=%s topic=%r", user_id[:8], topic)
            return cached

    # Gather context from user's brain
    related_memories = brain.search_memories(topic, user_id, limit=5)
    relevant = [m for m in related_memories if (m.get("score") or 0) >= 0.65]

    existing_knowledge = ""
    if relevant:
        lines = [f"- [{m['category']}] {m['content'][:120]}" for m in relevant]
        existing_knowledge = "\n".join(lines)

    user_prompt = _build_prompt(topic, existing_knowledge)

    logger.info("Explore generating: user=%s topic=%r", user_id[:8], topic)

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()

        # Strip markdown fences if Claude adds them anyway
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        content = json.loads(raw)

        # Validate required keys
        required = {"overview", "mindmap_mermaid", "flashcards", "quiz"}
        missing = required - set(content.keys())
        if missing:
            logger.error("Explore: missing keys %s for topic %r", missing, topic)
            return None

        # Attach related memories to overview for frontend "what you already know" block
        if relevant:
            content["related_memories"] = [
                {"content": m["content"][:200], "category": m["category"]}
                for m in relevant
            ]
        else:
            content["related_memories"] = []

        result = explore_db.upsert_exploration(user_id, topic, slug, content)
        logger.info("Explore stored: user=%s topic=%r slug=%s", user_id[:8], topic, slug)
        return result

    except json.JSONDecodeError as e:
        logger.error("Explore: JSON parse error for topic %r: %s", topic, e)
        logger.debug("Raw response: %s", raw[:500])
        return None
    except Exception as e:
        logger.error("Explore: generation error for topic %r: %s", topic, e, exc_info=True)
        return None


def get_surprise_topic(user_id: str) -> str:
    """
    Pick a random topic from the user's knowledge categories.
    Falls back to curated seed list if brain is empty.
    """
    try:
        categories = brain.get_categories(user_id)
        if categories:
            # Weight toward categories with more items
            weights = [c.get("count", 1) for c in categories]
            chosen = random.choices(categories, weights=weights, k=1)[0]
            return chosen["name"]
    except Exception as e:
        logger.warning("Surprise topic: could not load categories: %s", e)

    return random.choice(SURPRISE_SEED_TOPICS)


def _build_prompt(topic: str, existing_knowledge: str) -> str:
    existing_block = ""
    if existing_knowledge:
        existing_block = f"""The user already knows these related things — reference them briefly in the eli5 section:
{existing_knowledge}

"""

    # Escape braces in topic for f-string safety
    safe_topic = topic.replace('"', '\\"')

    return f"""{existing_block}Create a complete learning package for: "{safe_topic}"

Return ONLY valid JSON with this exact structure:

{{
  "topic": "{safe_topic}",
  "overview": {{
    "eli5": "3-4 paragraphs. Start with the simplest possible mental model. Use analogies. No jargon without definition. If the user already knows related things, briefly reference them in the first paragraph to build on their existing knowledge.",
    "key_concepts": [
      {{"term": "concept name", "definition": "one sentence, plain English, no jargon"}}
    ],
    "why_it_matters": "2-3 sentences on real-world impact and relevance.",
    "misconceptions": [
      "Wrong belief people have — and why it is actually incorrect. (one sentence each)"
    ]
  }},
  "mindmap_mermaid": "mindmap\\n  root(({safe_topic}))\\n    Branch1\\n      leaf1\\n      leaf2\\n    Branch2\\n      leaf3\\n      leaf4",
  "flashcards": [
    {{"question": "Question text?", "answer": "Answer text."}}
  ],
  "quiz": [
    {{
      "question": "Question text?",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct_index": 0,
      "explanation": "Why option A is correct. Why B, C, D are wrong."
    }}
  ]
}}

RULES — follow exactly:
- overview.eli5: minimum 3 paragraphs separated by \\n\\n. Real analogies only.
- overview.key_concepts: 4-6 items. Only genuinely important terms.
- overview.misconceptions: exactly 3 items.
- mindmap_mermaid: valid Mermaid mindmap syntax. Use (()) for root, square brackets for branches, plain text for leaves. 4-6 branches, 2-3 leaves each. No special characters in node labels.
- flashcards: exactly 8 cards. Mix recall questions (what is X?) with application questions (when would you use X?). Last 2 should be harder.
- quiz: exactly 5 questions. First 2 easy, next 2 medium, last 1 hard. 4 options each. correct_index is 0-3 (index into options array).
- Output pure JSON only. No markdown. No code fences. No trailing commas.
"""
