"""
pipelines/_claude_helper.py — Shared Claude Sonnet call helper for pipelines.

All pipeline Claude calls follow the same pattern:
  - System prompt instructs Claude to respond ONLY with valid JSON
  - Strict json.loads parse
  - Single retry on parse failure
  - Returns parsed dict or None on failure (caller falls back to previous cycle)
  - Logs token usage for every call
"""

import json
import logging
import os
from typing import Optional, Any

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-20250514"

_client: Optional[Any] = None


def _get_client() -> Any:
    """Lazily import and initialise the Anthropic client."""
    global _client
    if _client is None:
        import anthropic  # lazy import — not needed in fixture mode
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


async def call_claude_json(
    user_prompt: str,
    context: str = "pipeline",
    system_override: Optional[str] = None,
    max_tokens: int = 4096,
) -> Optional[dict]:
    """
    Call Claude Sonnet with a JSON-only instruction.

    Args:
        user_prompt:    The full user-turn prompt (must request JSON output).
        context:        Short label used in log messages (e.g. "news_curation").
        system_override: If provided, replaces the default system prompt.
        max_tokens:     Max tokens for the response.

    Returns:
        Parsed dict, or None if both attempts fail.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("[%s] ANTHROPIC_API_KEY not set — skipping Claude call", context)
        return None

    system = system_override or (
        "You are a precise JSON-only responder. "
        "Every response you produce MUST be valid JSON and nothing else — "
        "no markdown, no code fences, no explanatory text before or after the JSON object."
    )

    client = _get_client()

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
            )

            # Log token usage
            usage = response.usage
            logger.info(
                "[%s] Claude call — input_tokens=%d output_tokens=%d attempt=%d",
                context, usage.input_tokens, usage.output_tokens, attempt + 1,
            )

            raw_text = response.content[0].text.strip()

            # Strip accidental markdown fences
            if raw_text.startswith("```"):
                lines = raw_text.split("\n")
                # Remove first and last fence lines
                inner = "\n".join(
                    l for l in lines
                    if not l.strip().startswith("```")
                )
                raw_text = inner.strip()

            parsed = json.loads(raw_text)
            return parsed

        except json.JSONDecodeError as e:
            logger.warning(
                "[%s] JSON parse failed (attempt %d): %s — raw: %r",
                context, attempt + 1, e, raw_text[:300] if "raw_text" in dir() else "N/A",
            )
            if attempt == 0:
                # Retry once
                continue
            logger.error("[%s] Both attempts failed — returning None", context)
            return None

        except Exception as e:
            # Catches anthropic.APIError and any other API-level errors
            if "APIError" in type(e).__name__ or "anthropic" in str(type(e).__module__):
                logger.error("[%s] Anthropic API error: %s", context, e)
                return None
            raise

    return None
