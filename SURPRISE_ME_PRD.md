# PRD: Surprise Me — Topic Explorer

**Feature name:** Surprise Me / Topic Explorer  
**Status:** Draft  
**Owner:** Naveenkumar  
**Stack context:** FastAPI · Qdrant · SQLite · Claude API · OpenAI API · React/TypeScript · Apollo GraphQL

---

## Problem Statement

Learning something new requires stitching together multiple sources — a YouTube video for the overview, a blog for depth, Anki for memorisation, a podcast for context while commuting. This friction means most topics get half-explored and forgotten. Personal Brain already holds the user's knowledge — it should also be the place where they go deep on anything, instantly, in whatever format fits the moment.

---

## Goals

1. **Time to insight under 15 seconds** — user types a topic and gets a complete, multi-format learning page without leaving the app.
2. **Multi-modal by default** — every exploration produces at minimum a visual mind map, flashcards, and a quiz so the user learns, not just reads.
3. **Connected to existing knowledge** — generated content is aware of what the user already knows and skips or extends accordingly.
4. **One-click to Brain** — any section can be saved to the knowledge base, closing the loop between exploration and memory.
5. **Replayable** — explorations are cached so the user can return to a topic and resume from where they left off.

---

## Non-Goals

- **Not a general search engine.** This is deep explanation of a single topic, not broad web search results.
- **Not real-time.** Content is generated once and cached. Live streaming generation is a P2.
- **Not collaborative.** No sharing or multi-user sessions in v1.
- **Not a replacement for the learning pipeline.** The existing daily learning picks (RSS + YouTube curation) remain separate.
- **Not mobile-native.** PWA / responsive is fine; a dedicated native app is out of scope.

---

## User Stories

### As the user exploring a new concept

- As a user, I want to type any topic and get an instant layman explanation so I can understand it in under 2 minutes without prior knowledge.
- As a user, I want to see a visual mind map of the topic so I can understand how sub-concepts relate before diving in.
- As a user, I want flashcards generated automatically so I can test my understanding immediately after reading.
- As a user, I want a quiz with scoring so I know whether I actually understood the topic or just skimmed it.
- As a user, I want a podcast-style audio version so I can consume the topic hands-free while commuting or cooking.

### As the user connecting exploration to their existing brain

- As a user, I want the explanation to reference things I've already saved so it doesn't re-explain what I already know.
- As a user, I want to save any section (explanation, a specific flashcard, a quiz question) directly to my knowledge base with one click.
- As a user, I want a "Surprise Me" button that picks a random topic from my knowledge base so I rediscover things I saved but forgot.

### As the user returning to a previous exploration

- As a user, I want my past explorations cached so I can revisit a topic without regenerating it.
- As a user, I want to regenerate an exploration on demand if I want a fresh perspective.

---

## Requirements

### P0 — Must Have (MVP ships without these = product doesn't work)

#### Topic Input & Agent Pipeline
- [ ] Free-text topic input field with submit (Enter or button)
- [ ] Backend agent pipeline (`pipelines/explore.py`) that accepts a topic string and returns structured JSON covering all sections
- [ ] Single Claude call with structured output schema (JSON mode) — one round trip, not multiple sequential calls
- [ ] Loading state with progress indication per section as content streams in
- [ ] SQLite cache table `topic_explorations(id, user_id, topic, topic_slug, content_json, created_at, regenerated_at)` — keyed by `(user_id, topic_slug)` so the same topic doesn't regenerate on every visit
- [ ] `POST /explore` FastAPI endpoint (auth required, rate-limited to 10/hour)
- [ ] GraphQL mutation: `exploreTopic(topic: String!, regenerate: Boolean): TopicExploration`

#### Layman Explanation (Overview tab)
- [ ] ELI5-style opening paragraph — no jargon, analogy-first
- [ ] 3–5 key concept callouts (term + one-sentence definition, highlighted inline)
- [ ] "Why it matters" section — real-world relevance in 2–3 sentences
- [ ] "Common misconceptions" — 2–3 things people get wrong about this topic
- [ ] If the user has saved memories related to the topic, inject a "What you already know" block at the top surfacing relevant existing notes (semantic search over `brain_memories`, threshold 0.75)

#### Mind Map
- [ ] Generated as Mermaid `mindmap` syntax by Claude — rendered client-side using `mermaid.js`
- [ ] Central node = topic, first ring = major sub-topics (4–6), second ring = key concepts under each (2–3 per branch)
- [ ] Rendered in a scrollable container, not cut off on small screens
- [ ] Copy-as-Mermaid button for export

#### Flashcards
- [ ] 8 Q&A pairs generated per topic
- [ ] Flip-card UI — front shows question, click/tap reveals answer
- [ ] Navigation: Previous / Next / Shuffle
- [ ] Cards stored in existing `learning_cards` schema so they integrate with the concept-of-day system
- [ ] "Save card to Brain" saves the Q&A as a memory under category "Flashcards — {topic}"

#### Quiz
- [ ] 5 multiple-choice questions (4 options each), difficulty range: 2 easy / 2 medium / 1 hard
- [ ] Immediate feedback per question — correct answer highlighted, brief explanation shown on wrong answer
- [ ] Final score screen: X/5 with verdict ("You've got this", "Review the basics", etc.)
- [ ] Quiz state resets on each page visit (not persisted — exploration is cached, quiz attempts are not)

---

### P1 — Should Have (high-value fast follows)

#### Podcast Mode
- [ ] Claude generates a 400–600 word host + guest dialogue script covering the topic conversationally
- [ ] Script displayed as readable text with speaker labels (Host / Guest)
- [ ] Optional TTS audio generation via OpenAI `tts-1` model (voice: `alloy`) — triggered by "Generate Audio" button, not on page load (cost control)
- [ ] Audio cached in SQLite as base64 or filesystem path — not regenerated on every visit
- [ ] In-page audio player: play/pause, scrub, 1x/1.5x/2x speed
- [ ] Graceful degradation: if TTS not enabled (`OPENAI_TTS_ENABLED` env flag), show script-only with a note

#### Visual Concept Diagram
- [ ] For topics with clear relationships (algorithms, architectures, processes), Claude generates an SVG flow diagram inline in the explanation
- [ ] Claude outputs raw SVG — rendered directly, no external dependency
- [ ] Falls back to Mermaid flowchart if topic doesn't suit SVG generation

#### "Surprise Me" Random Topic
- [ ] Button on the explore page that picks a random topic from the user's knowledge base (random scroll from Qdrant `brain_memories`)
- [ ] Alternatively picks from a curated seed list if the brain is empty
- [ ] Navigates to the same explore flow with the selected topic pre-filled

#### Exploration History
- [ ] `/explore/history` page listing past explorations sorted by recency
- [ ] Each entry: topic name, date generated, thumbnail (first 80 chars of explanation), regenerate button
- [ ] Delete exploration (removes SQLite row + cached audio)

#### Share / Export
- [ ] "Copy link" generates a short URL (`/explore/slug`) — read-only, no auth required, expires in 7 days
- [ ] "Export as Markdown" dumps the explanation + flashcards + quiz to a `.md` file download

---

### P2 — Future Considerations (design now, build later)

- **Streaming generation** — stream each section as it's generated rather than waiting for the full response, using SSE or WebSocket
- **Voice input for topic** — type or speak the topic (reuse existing Whisper transcription endpoint)
- **Telegram-native exploration** — send `/explore quantum entanglement` to the bot, receive a condensed version with flashcards via Telegram inline keyboard
- **Spaced repetition integration** — flashcards feed into SM-2 scheduler, resurfaced in the daily learning picks
- **Difficulty personalisation** — if the user has saved advanced notes on a topic, the explanation skips ELI5 and goes deeper
- **Multi-topic comparison** — "Compare RAG vs fine-tuning" generates a side-by-side view
- **DALL-E illustrations** — generate one contextual image per exploration (cost: ~$0.04/image)
- **Collaborative annotations** — invite someone to annotate the exploration (future multi-user feature)

---

## Technical Architecture

### Backend — New Files

```
backend/
  pipelines/
    explore.py          # Core agent pipeline — topic → structured JSON
  explore_db.py         # SQLite CRUD for topic_explorations + audio cache
```

### Database Schema

```sql
CREATE TABLE topic_explorations (
  id              TEXT PRIMARY KEY,
  user_id         TEXT NOT NULL,
  topic           TEXT NOT NULL,
  topic_slug      TEXT NOT NULL,          -- lowercase-hyphenated for URL
  content_json    TEXT NOT NULL,          -- full JSON payload
  audio_path      TEXT,                   -- path to cached TTS audio file
  created_at      TEXT NOT NULL,
  regenerated_at  TEXT
);
CREATE UNIQUE INDEX idx_explore_user_slug ON topic_explorations(user_id, topic_slug);
```

### Claude Output Schema (structured JSON)

```json
{
  "topic": "string",
  "overview": {
    "eli5": "string",
    "key_concepts": [{ "term": "string", "definition": "string" }],
    "why_it_matters": "string",
    "misconceptions": ["string"]
  },
  "mindmap_mermaid": "string",
  "svg_diagram": "string | null",
  "flashcards": [{ "question": "string", "answer": "string" }],
  "quiz": [{
    "question": "string",
    "options": ["string", "string", "string", "string"],
    "correct_index": 0,
    "explanation": "string"
  }],
  "podcast_script": [{ "speaker": "Host|Guest", "line": "string" }]
}
```

### Frontend — New Files

```
frontend/src/
  components/
    ExplorePage.tsx         # Main page — input + tab shell
    ExplorePage.css
    explore/
      OverviewTab.tsx        # ELI5 + key concepts + misconceptions
      MindMapTab.tsx         # Mermaid renderer
      FlashcardsTab.tsx      # Flip-card carousel
      QuizTab.tsx            # MCQ + scoring
      PodcastTab.tsx         # Script viewer + audio player
```

### System Prompt Design for Explore Agent

The agent receives:
- The topic string
- User's existing knowledge categories (from `brain.get_categories()`)
- Top-5 semantically related memories (from `brain.search_memories(topic)`)
- Instruction to reference existing knowledge in the ELI5 section

One Claude `messages.create` call with `json` response format. No tool-use loop needed — this is generative, not agentic.

---

## UX Flow

```
/explore
  ┌─────────────────────────────────────┐
  │  🎲 Surprise Me    [topic input]  → │
  └─────────────────────────────────────┘
           ↓ (submit)
  Loading skeleton (per-section, ~8-12s)
           ↓
  ┌──────────────────────────────────────────────┐
  │  ✦ Quantum Entanglement                      │
  │  [Overview] [Mind Map] [Flashcards] [Quiz]   │
  │  [Podcast]                                   │
  │                                              │
  │  What you already know (from your brain):    │
  │  • [Saved memory excerpt]                    │
  │                                              │
  │  [ELI5 paragraph]                            │
  │  Key concepts: ...                           │
  │  Why it matters: ...                         │
  │  Common misconceptions: ...                  │
  │                          [Save to Brain ↗]   │
  └──────────────────────────────────────────────┘
```

---

## Success Metrics

### Leading (measure at 1 week)
- **Activation rate**: % of DAU who visit `/explore` at least once
- **Completion rate**: % of explorations where user visits 3+ tabs (not just overview)
- **Quiz attempt rate**: % of explorations where user starts the quiz
- **Save-to-Brain rate**: % of explorations where at least one section is saved

### Lagging (measure at 4 weeks)
- **Return exploration rate**: % of users who generate a second exploration within 7 days
- **Flashcard save rate**: flashcards saved per user per week vs. prior learning card saves
- **Session length delta**: average session duration on days with explore usage vs. without

### Targets (hypotheses)
- 40%+ of DAU try explore within 2 weeks of launch
- 60%+ of explorations result in quiz attempt
- 25%+ of explorations result in at least one save-to-Brain action

---

## Open Questions

| Question | Owner | Blocking? |
|---|---|---|
| Should audio generation be on-demand (button) or automatic? Cost is ~$0.02/exploration at 500 words. | Naveenkumar | No — default to on-demand |
| Should the explore page be reachable from the Telegram bot? If yes, bot returns a condensed text version. | Naveenkumar | No |
| Rate limit: 10 explorations/hour seems reasonable but could be too low for power use. | Engineering | No |
| Should topic slug collisions across users be namespaced in the URL? (`/explore/me/quantum` vs `/explore/quantum`) | Engineering | No |
| Do flashcards generated here feed into the existing concept-of-day rotation, or stay separate? | Naveenkumar | Yes — decide before DB schema finalises |

---

## Phased Delivery

### Phase 1 — Core Explorer (2–3 weeks)
Overview + Mind Map + Flashcards + Quiz. No audio. No history page. Cache works. Save-to-Brain on overview only.

### Phase 2 — Podcast + Polish (1–2 weeks)
Podcast script + optional TTS. "Surprise Me" button. Exploration history. Save-to-Brain on all sections. SVG diagrams.

### Phase 3 — Connected & Intelligent (ongoing)
Telegram integration. Spaced repetition. Streaming. Difficulty personalisation based on existing knowledge depth.

---

## Dependencies

- `mermaid` npm package (frontend) — for mind map rendering
- `openai` Python package (already in requirements.txt) — for TTS
- `OPENAI_TTS_ENABLED` env flag — feature gate for audio generation
- No new vector collections needed — reuses `brain_memories` for "what you already know" lookup
- New SQLite table `topic_explorations` — migration in `explore_db.py::ensure_table()`
