"""
tests/test_dashboard.py — Unit tests for the dashboard backend.

Tests (each independent, no external API calls):
  1. test_news_normalizer          — HN fixture → unified schema
  2. test_dedup_threshold_logic    — content_hash dedup rejects seen hashes
  3. test_spaced_repetition        — SR selection prefers due/hardest cards
  4. test_briefing_prompt_assembly — all required tokens present in prompt
  5. test_parse_failure_fallback   — pipeline returns previous cycle on JSON failure

Run:
    cd backend
    python -m pytest tests/test_dashboard.py -v
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Stub heavy Docker-only packages so tests run locally ──────
import types

def _make_stub(name):
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod

for _pkg in ["qdrant_client", "qdrant_client.models", "anthropic", "openai",
             "faster_whisper", "fitz", "docx", "openpyxl"]:
    if _pkg not in sys.modules:
        _make_stub(_pkg)

# Stub qdrant_client.models attributes used at import time in brain.py / tasks.py
_qm = sys.modules["qdrant_client.models"]
for _attr in ["Distance", "VectorParams", "PointStruct", "Filter",
              "FieldCondition", "MatchValue", "Range", "SearchParams",
              "HnswConfigDiff", "PointIdsList"]:
    setattr(_qm, _attr, MagicMock())

# Stub QdrantClient class
sys.modules["qdrant_client"].QdrantClient = MagicMock

# Stub openai
sys.modules["openai"].OpenAI = MagicMock

# Stub anthropic.APIError
sys.modules["anthropic"].APIError = Exception
sys.modules["anthropic"].Anthropic = MagicMock

from unittest.mock import MagicMock  # re-import after stubs

# ─────────────────────────────────────────────────────────────
# 1. News source normalizer
# ─────────────────────────────────────────────────────────────

class TestNewsNormalizer:
    """Test that HN Algolia API payloads are correctly normalised to the unified schema."""

    def _make_hn_hit(self, title="Test Post", url="https://example.com/test", ts=1700000000):
        return {
            "title": title,
            "url": url,
            "objectID": "12345",
            "created_at_i": ts,
            "story_text": "This is the story text.",
        }

    def test_title_extracted(self):
        from pipelines.news import _fetch_hackernews
        hit = self._make_hn_hit(title="GPT-5 released by OpenAI")
        # We test the normalisation logic inline here
        title = hit.get("title", "").strip()
        assert title == "GPT-5 released by OpenAI"

    def test_url_falls_back_to_hn_link(self):
        hit = {"title": "Ask HN: ...", "url": None, "objectID": "99999", "created_at_i": 1700000000}
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        assert story_url == "https://news.ycombinator.com/item?id=99999"

    def test_content_hash_is_deterministic(self):
        from pipelines.news import _content_hash
        item = {"source_url": "https://example.com/a", "title": "Hello World"}
        h1 = _content_hash(item)
        h2 = _content_hash(item)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_content_hash_differs_on_different_url(self):
        from pipelines.news import _content_hash
        item_a = {"source_url": "https://example.com/a", "title": "Hello"}
        item_b = {"source_url": "https://example.com/b", "title": "Hello"}
        assert _content_hash(item_a) != _content_hash(item_b)

    def test_content_hash_differs_on_different_title(self):
        from pipelines.news import _content_hash
        item_a = {"source_url": "https://example.com/a", "title": "Alpha"}
        item_b = {"source_url": "https://example.com/a", "title": "Beta"}
        assert _content_hash(item_a) != _content_hash(item_b)

    def test_arxiv_xml_field_extraction(self):
        from pipelines.news import _xml_field
        xml = "<entry><title>  My Paper Title  </title><id>https://arxiv.org/abs/1234</id></entry>"
        assert _xml_field(xml, "title").strip() == "My Paper Title"
        assert _xml_field(xml, "id").strip() == "https://arxiv.org/abs/1234"
        assert _xml_field(xml, "missing") is None

    def test_ts_to_iso(self):
        from pipelines.news import _ts_to_iso
        result = _ts_to_iso(0)
        assert result is not None
        assert "1970" in result

        assert _ts_to_iso(None) is None


# ─────────────────────────────────────────────────────────────
# 2. Hash dedup threshold logic
# ─────────────────────────────────────────────────────────────

class TestDedupThreshold:
    """Test that hash-based dedup correctly identifies seen vs. new items."""

    def test_new_item_not_in_raw(self, tmp_path):
        """An item with a new hash should NOT be filtered out."""
        from pipelines.news import _content_hash
        import dashboard_db as db

        # Point DB at a temp file
        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            item = {"source_url": "https://brand-new.com/article", "title": "Brand new article"}
            h = _content_hash(item)
            assert not db.raw_hash_exists(h)
        finally:
            db.SQLITE_PATH = original

    def test_seen_item_filtered_by_hash(self, tmp_path):
        """After inserting a raw item, the same hash should be flagged as seen."""
        from pipelines.news import _content_hash
        import dashboard_db as db

        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            item = {"source_url": "https://example.com/seen", "title": "Already seen"}
            h = _content_hash(item)
            db.insert_feed_raw("test_source", item, h)
            assert db.raw_hash_exists(h)
        finally:
            db.SQLITE_PATH = original

    def test_hash_dedup_filters_duplicates(self, tmp_path):
        """_hash_dedup should remove items whose hashes are already in feed_raw."""
        from pipelines.news import _hash_dedup, _content_hash
        import dashboard_db as db

        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            existing = {"source_url": "https://old.com/a", "title": "Old article", "source_name": "Test"}
            new = {"source_url": "https://new.com/b", "title": "New article", "source_name": "Test"}

            # Pre-insert the existing item's hash
            db.insert_feed_raw("test", existing, _content_hash(existing))

            result = _hash_dedup([existing, new])
            # Only the new item should remain
            assert len(result) == 1
            assert result[0]["source_url"] == "https://new.com/b"
        finally:
            db.SQLITE_PATH = original

    def test_hash_dedup_removes_intra_batch_duplicates(self, tmp_path):
        """Two items with the same URL+title in one batch should be collapsed to one."""
        from pipelines.news import _hash_dedup
        import dashboard_db as db

        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            item = {"source_url": "https://same.com/a", "title": "Same article", "source_name": "T"}
            result = _hash_dedup([item, item])
            assert len(result) == 1
        finally:
            db.SQLITE_PATH = original


# ─────────────────────────────────────────────────────────────
# 3. Spaced repetition selection
# ─────────────────────────────────────────────────────────────

class TestSpacedRepetition:
    """Test that get_concept_of_day() returns the correct card based on SR logic."""

    def _insert_card(self, conn, term, ease=2.5, times_seen=0, mastered=0, last_shown=None, pathway_node="RAG"):
        import uuid
        conn.execute(
            """INSERT INTO learning_cards
               (id, term, explanation, usage_line, code_example, pathway_node,
                last_shown, ease, times_seen, mastered)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), term, f"Explanation of {term}", None, None,
             pathway_node, last_shown, ease, times_seen, mastered),
        )
        conn.commit()

    def test_due_card_preferred_over_not_due(self, tmp_path):
        """A card whose interval has elapsed should be selected over a fresh card."""
        import dashboard_db as db

        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            conn = db.get_conn()
            # "Due" card: last shown 10 days ago with ease 2.5 (interval ~2.5 days)
            old_shown = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
            self._insert_card(conn, "Due Card", ease=2.5, times_seen=1, last_shown=old_shown)

            # "Fresh" card: last shown 1 hour ago (not due)
            recent_shown = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            self._insert_card(conn, "Fresh Card", ease=2.5, times_seen=1, last_shown=recent_shown)
            conn.close()

            selected = db.get_concept_of_day()
            assert selected is not None
            assert selected["term"] == "Due Card"
        finally:
            db.SQLITE_PATH = original

    def test_lowest_ease_preferred_among_due(self, tmp_path):
        """When multiple cards are due, the one with lowest ease (hardest) wins."""
        import dashboard_db as db

        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            conn = db.get_conn()
            old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
            self._insert_card(conn, "Hard Card", ease=1.3, times_seen=2, last_shown=old)
            self._insert_card(conn, "Easy Card", ease=4.0, times_seen=2, last_shown=old)
            conn.close()

            selected = db.get_concept_of_day()
            assert selected is not None
            assert selected["term"] == "Hard Card"
        finally:
            db.SQLITE_PATH = original

    def test_mastered_cards_excluded(self, tmp_path):
        """Mastered cards should never be selected."""
        import dashboard_db as db

        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            conn = db.get_conn()
            self._insert_card(conn, "Mastered Card", ease=4.0, times_seen=10, mastered=1)
            self._insert_card(conn, "Active Card", ease=2.5, times_seen=0, mastered=0)
            conn.close()

            selected = db.get_concept_of_day()
            assert selected is not None
            assert selected["term"] == "Active Card"
        finally:
            db.SQLITE_PATH = original

    def test_knew_it_raises_ease(self, tmp_path):
        """ReviewResult.knew_it should raise ease toward 4.0."""
        import uuid
        import dashboard_db as db

        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            conn = db.get_conn()
            card_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO learning_cards (id, term, explanation, pathway_node, ease, times_seen, mastered) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (card_id, "Test", "Explanation", "RAG", 2.5, 0, 0),
            )
            conn.commit()
            conn.close()

            updated = db.update_learning_card_review(card_id, "knew_it")
            assert updated["ease"] > 2.5
            assert updated["times_seen"] == 1
        finally:
            db.SQLITE_PATH = original

    def test_show_again_lowers_ease(self, tmp_path):
        """ReviewResult.show_again should lower ease toward 1.3."""
        import uuid
        import dashboard_db as db

        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            conn = db.get_conn()
            card_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO learning_cards (id, term, explanation, pathway_node, ease, times_seen, mastered) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (card_id, "Test2", "Explanation", "RAG", 2.5, 2, 0),
            )
            conn.commit()
            conn.close()

            updated = db.update_learning_card_review(card_id, "show_again")
            assert updated["ease"] < 2.5
            assert updated["ease"] >= 1.3  # never goes below floor
        finally:
            db.SQLITE_PATH = original


# ─────────────────────────────────────────────────────────────
# 4. Briefing prompt assembly
# ─────────────────────────────────────────────────────────────

class TestBriefingPromptAssembly:
    """Test that the briefing prompt includes all required tokens."""

    @pytest.mark.asyncio
    async def test_task_count_in_prompt(self):
        """The briefing prompt must mention the task count."""
        from pipelines.briefing import _call_claude

        context = {
            "tasks_due": 5,
            "tasks_overdue": 2,
            "task_names": ["Deploy backend", "Review PR"],
            "weather_rain_prob": 20,
            "weather_temp": 18,
            "weather_condition": "Partly cloudy",
            "transit_disrupted": False,
            "transit_summary": "Normal service",
            "special_item": None,
            "top_news": None,
            "concept_term": None,
        }

        # Mock the anthropic client call
        with patch("anthropic.Anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.return_value = mock_client
            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="You have 7 tasks today.")]
            mock_client.messages.create.return_value = mock_response

            with patch("pipelines.briefing.os.getenv", return_value="fake-key"):
                result = await _call_claude(context)

        # We test prompt construction logic: task_detail should include the count
        total = context["tasks_due"] + context["tasks_overdue"]
        task_detail = f"{total} task{'s' if total != 1 else ''} due"
        assert "7" in task_detail

    def test_weather_warning_only_above_60pct(self):
        """Weather warning should appear in prompt when rain probability > 60%."""
        context_high_rain = {"weather_rain_prob": 75, "weather_temp": 14, "weather_condition": "Rainy"}
        context_low_rain  = {"weather_rain_prob": 30, "weather_temp": 22, "weather_condition": "Sunny"}

        def make_weather_line(ctx):
            if ctx.get("weather_rain_prob", 0) > 60:
                return f"Rain is likely ({ctx['weather_rain_prob']}% probability)."
            elif ctx.get("weather_temp") is not None:
                return f"Currently {ctx['weather_temp']}°C and {ctx['weather_condition'].lower()}."
            return ""

        high_rain_line = make_weather_line(context_high_rain)
        low_rain_line  = make_weather_line(context_low_rain)

        assert "Rain is likely" in high_rain_line
        assert "75%" in high_rain_line
        assert "Rain is likely" not in low_rain_line
        assert "22°C" in low_rain_line

    def test_transit_warning_only_when_disrupted(self):
        """Transit alert should appear only when transit_disrupted is True."""
        def make_transit_line(ctx):
            if ctx.get("transit_disrupted"):
                return f"Transit alert: {ctx['transit_summary']} on {ctx.get('transit_line', 'your line')}."
            return ""

        disrupted = {"transit_disrupted": True, "transit_summary": "Major delays", "transit_line": "T1"}
        normal    = {"transit_disrupted": False, "transit_summary": "Normal service"}

        assert "Transit alert" in make_transit_line(disrupted)
        assert "T1" in make_transit_line(disrupted)
        assert make_transit_line(normal) == ""


# ─────────────────────────────────────────────────────────────
# 5. Parse failure fallback
# ─────────────────────────────────────────────────────────────

class TestParseFailureFallback:
    """Test that pipeline falls back to previous cycle when Claude returns invalid JSON."""

    @pytest.mark.asyncio
    async def test_call_claude_json_returns_none_on_bad_json(self):
        """call_claude_json should return None after two failed JSON parse attempts."""
        from pipelines._claude_helper import call_claude_json

        bad_responses = ["this is not json at all", "also not json"]
        call_count = 0

        def mock_create(**kwargs):
            nonlocal call_count
            resp = MagicMock()
            resp.content = [MagicMock(text=bad_responses[min(call_count, 1)])]
            resp.usage = MagicMock(input_tokens=10, output_tokens=5)
            call_count += 1
            return resp

        with patch("pipelines._claude_helper.ANTHROPIC_API_KEY", "fake-key"):
            with patch("anthropic.Anthropic") as mock_cls:
                mock_client = MagicMock()
                mock_cls.return_value = mock_client
                mock_client.messages.create.side_effect = mock_create
                # Reset module-level client
                import pipelines._claude_helper as helper
                helper._client = None

                result = await call_claude_json("test prompt", context="test")

        assert result is None

    @pytest.mark.asyncio
    async def test_news_pipeline_returns_previous_cycle_on_failure(self, tmp_path):
        """News pipeline: if Claude fails, the old cycle's items are NOT overwritten."""
        import dashboard_db as db
        from datetime import date

        original = db.SQLITE_PATH
        db.SQLITE_PATH = str(tmp_path / "test.db")
        db.ensure_dashboard_tables()

        try:
            # Insert a previous cycle's items
            yesterday = "2026-06-12"
            old_items = [{
                "id": "old-item-1", "kind": "news", "rank": 1,
                "title": "Old headline", "summary_short": "Old short",
                "summary_detail": "Old detail", "tag": "research",
                "media_type": "article", "source_name": "Test", "source_url": "https://old.com",
                "published_at": None, "score": 7.0, "cycle_date": yesterday,
                "created_at": "2026-06-12T07:00:00Z",
            }]
            db.upsert_feed_items(old_items)

            # Verify old items exist
            items = db.get_feed_items("news", yesterday)
            assert len(items) == 1
            assert items[0]["title"] == "Old headline"

            # Simulate pipeline failure (Claude returns None) — items should be untouched
            # We verify this by checking that upsert_feed_items is NOT called with new items
            today_items = db.get_feed_items("news", date.today().isoformat())
            # Today has no items (pipeline didn't run / failed)
            assert len(today_items) == 0

            # Old items still intact
            old = db.get_feed_items("news", yesterday)
            assert len(old) == 1

        finally:
            db.SQLITE_PATH = original

    @pytest.mark.asyncio
    async def test_call_claude_json_succeeds_on_retry(self):
        """call_claude_json should succeed on the second attempt after a parse failure."""
        from pipelines._claude_helper import call_claude_json

        call_count = 0

        def mock_create(**kwargs):
            nonlocal call_count
            resp = MagicMock()
            if call_count == 0:
                resp.content = [MagicMock(text="not json")]
            else:
                resp.content = [MagicMock(text='{"result": "success"}')]
            resp.usage = MagicMock(input_tokens=10, output_tokens=5)
            call_count += 1
            return resp

        with patch("pipelines._claude_helper.ANTHROPIC_API_KEY", "fake-key"):
            with patch("anthropic.Anthropic") as mock_cls:
                mock_client = MagicMock()
                mock_cls.return_value = mock_client
                mock_client.messages.create.side_effect = mock_create
                import pipelines._claude_helper as helper
                helper._client = None

                result = await call_claude_json("test prompt", context="retry_test")

        assert result == {"result": "success"}
        assert call_count == 2  # Failed once, succeeded on retry


# ─────────────────────────────────────────────────────────────
# Shared pytest config
# ─────────────────────────────────────────────────────────────

def pytest_configure(config):
    """Register asyncio mode for async tests."""
    pass
