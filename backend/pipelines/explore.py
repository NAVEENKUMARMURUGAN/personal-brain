"""
pipelines/explore.py — Topic Explorer generation pipeline.

Given a topic string, produces a structured learning package:
  - ELI5 overview with key concepts, why it matters, misconceptions
  - Engineer-level deep dive (internals, complexity, trade-offs)
  - 2-3 real-world use cases with how-it-applies context
  - Sample implementation (code snippet, null if non-programming topic)
  - Mermaid mind map syntax
  - 8 flashcards (Q&A pairs)
  - 5 quiz questions (MCQ with explanations)

One Claude call, structured JSON output. SQLite cache per (user_id, topic_slug).
Context-aware: injects user's existing related memories into the prompt so the
explanation references what they already know.
"""

import asyncio
import json
import logging
import os
import random
from typing import Optional

import anthropic

import brain
import explore_db
from pipelines.web_search import search_web, format_for_prompt

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

    # Web search for current information (runs in thread pool — requests is sync)
    web_results = await asyncio.get_event_loop().run_in_executor(
        None, lambda: search_web(topic, max_results=5)
    )
    web_context = format_for_prompt(web_results)
    if web_context:
        logger.info("Explore web search: %d results for %r", len(web_results), topic)
    else:
        logger.info("Explore web search: no results for %r (will use training data only)", topic)

    user_prompt = _build_prompt(topic, existing_knowledge, web_context)


    logger.info("Explore generating: user=%s topic=%r", user_id[:8], topic)

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=12000,
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
        required = {"overview", "engineer", "use_cases", "mindmap_mermaid", "flashcards", "quiz"}
        missing = required - set(content.keys())
        if missing:
            logger.error("Explore: missing keys %s for topic %r", missing, topic)
            return None

        # Attach related memories for "what you already know" block
        content["related_memories"] = [
            {"content": m["content"][:200], "category": m["category"]}
            for m in relevant
        ]

        # Attach web sources so frontend can show "Sources searched"
        content["web_sources"] = [
            {"title": r["title"], "url": r["url"]}
            for r in web_results
        ]

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


def _build_prompt(topic: str, existing_knowledge: str, web_context: str = "") -> str:
    existing_block = ""
    if existing_knowledge:
        existing_block = f"""The user already knows these related things — reference them briefly in the eli5 section:
{existing_knowledge}

"""

    web_block = ""
    if web_context:
        web_block = f"""{web_context}

"""

    safe_topic = topic.replace('"', '\\"')

    return f"""{web_block}{existing_block}Create a complete learning package for: "{safe_topic}"

Return ONLY valid JSON with this exact structure (pure JSON, no markdown fences, no trailing commas):

{{
  "topic": "{safe_topic}",

  "overview": {{
    "eli5": "3-4 paragraphs explaining the topic to a complete newcomer. Start with the simplest possible mental model using an everyday analogy. Define every term the first time you use it. If the user already knows related things from context, reference them in paragraph 1 to build a bridge.",
    "key_concepts": [
      {{"term": "concept name", "definition": "one sentence, plain English only"}}
    ],
    "why_it_matters": "2-3 sentences. Real-world impact. Why someone should care.",
    "misconceptions": [
      "One common wrong belief — followed immediately by why it is wrong. (one sentence each)"
    ]
  }},

  "engineer": {{
    "deep_dive": "4-6 paragraphs for an engineering student (3rd/4th year undergrad or early grad). Cover: how it works step by step under the hood, time/space complexity where relevant, key design decisions and WHY they were made, common mistakes beginners make and how to avoid them, and how this connects to familiar CS concepts (data structures, algorithms, OS, networks). Define technical jargon the first time you use it. Build intuition before formalism — explain the 'why' before the 'how'. Use short worked examples or analogies to cement understanding.",
    "internals": [
      {{"aspect": "Aspect name (e.g. How Data Flows, Memory Layout)", "detail": "2-3 sentences. Technical but accessible — imagine a smart CS junior who knows theory but hasn't seen this in production yet."}}
    ],
    "trade_offs": [
      {{"pro": "Concrete advantage — include a brief reason why this matters", "con": "Corresponding cost or limitation — include when this bites you"}}
    ]
  }},

  "use_cases": [
    {{
      "title": "Short title (e.g. GPT-4 pre-training)",
      "company_or_context": "Where/who uses this (e.g. OpenAI, Netflix, Linux kernel)",
      "description": "2-3 sentences. How this topic is applied in this specific context. What problem it solves. What results it achieves. Be concrete — cite real numbers or outcomes if known."
    }}
  ],

  "sample_implementation": {{
    "applicable": true,
    "language": "python",
    "description": "One sentence on what this snippet demonstrates.",
    "code": "# Minimal but complete runnable example\\n# showing the core idea of the topic\\n# Use clear variable names, add comments explaining non-obvious lines\\n# Maximum 40 lines"
  }},

  "mindmap_mermaid": "mindmap\\n  root(({safe_topic}))\\n    Branch1\\n      leaf1\\n      leaf2\\n    Branch2\\n      leaf3\\n      leaf4",

  "flashcards": [
    {{"question": "Question?", "answer": "Answer."}}
  ],

  "quiz": [
    {{
      "question": "Question?",
      "options": ["A", "B", "C", "D"],
      "correct_index": 0,
      "explanation": "Why A is correct. Why B, C, D are wrong."
    }}
  ]
}}

RULES — every rule is mandatory:

overview:
- eli5: minimum 3 paragraphs separated by \\n\\n. Analogies must be to everyday objects/experiences.
- key_concepts: 4-6 items. Only terms a newcomer would not know.
- misconceptions: exactly 3 items.

engineer:
- Audience is an engineering student, NOT a senior engineer. Build intuition, define terms, use examples.
- deep_dive: minimum 4 paragraphs separated by \\n\\n. No hand-waving — explain the why behind every design choice. Use a short worked example or analogy in at least one paragraph.
- internals: 3-5 aspects. Each must be a distinct technical dimension. Explain each as if to a smart CS junior seeing it for the first time.
- trade_offs: 3-4 pairs. Each pro/con must be specific (not "it's fast" — say why and when).

use_cases:
- exactly 3 items.
- Must be real products, companies, or well-known systems — not made-up examples.
- description must say HOW the topic is used, not just THAT it is used.

sample_implementation:
- If the topic has a natural code representation (algorithms, data structures, protocols, ML concepts, systems), set applicable=true and write real runnable code.
- If the topic is non-technical or abstract (e.g. Black holes, Renaissance art), set applicable=false and code=null.
- Language: prefer Python unless the topic is inherently tied to another language (e.g. Linux syscalls → C).
- Code must be correct, minimal, and commented.

mindmap_mermaid:
- Valid Mermaid mindmap syntax. Root uses (()). Branches use plain text. 4-6 branches, 2-3 leaves each.
- No special characters in node labels (no colons, parentheses, slashes inside labels).

flashcards: exactly 8 cards. First 4 recall, last 4 application/synthesis.
quiz: exactly 5 questions. Difficulty: 2 easy, 2 medium, 1 hard.

Output: pure JSON only. No markdown. No code fences. No trailing commas.
"""
