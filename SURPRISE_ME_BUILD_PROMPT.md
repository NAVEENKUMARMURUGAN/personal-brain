# Build Prompt: Surprise Me — Topic Explorer (Phase 1)

You are building a new feature called **Surprise Me / Topic Explorer** for an existing personal brain app. Read every word of this prompt before writing a single line of code.

---

## Codebase Context

This is a full-stack personal AI assistant app. The stack:
- **Backend**: FastAPI (Python), Qdrant (vector DB), SQLite, APScheduler, Anthropic Claude API, OpenAI API
- **Frontend**: React + TypeScript, Apollo GraphQL client, CSS modules (no Tailwind)
- **Auth**: Google OAuth2 → JWT, passed as `Authorization: Bearer <token>` header
- **Agent pattern**: See `backend/claude.py` — single `_client.messages.create()` call with structured output
- **Pipeline pattern**: See `backend/pipelines/briefing.py` — async function, SQLite cache, graceful degradation
- **GraphQL**: Custom parser in `backend/graphql_handler.py` — no strawberry/ariadne. Add new types/mutations/queries there.
- **CSS conventions**: BEM-style class names like `chat__mic-btn`, `dashboard__card`. Match existing style.
- **No new npm packages** unless absolutely necessary. `mermaid` is the one exception — add it.

Read these files before starting:
- `backend/claude.py` — understand the message format and JSON output pattern
- `backend/pipelines/briefing.py` — understand the pipeline/cache pattern to replicate
- `backend/graphql_handler.py` — understand how to add new GraphQL types and resolvers
- `backend/history.py` — understand the SQLite pattern (`ensure_db`, `get_conn`, `row_factory`)
- `backend/brain.py` — understand `search_memories()` and `get_categories()` signatures
- `frontend/src/components/DashboardPage.tsx` + `DashboardPage.css` — match this UI pattern
- `frontend/src/graphql/queries.ts` — add new queries/mutations here

---

## What to Build (Phase 1 Only)

Build EXACTLY these deliverables. Nothing more.

### Phase 1 scope:
1. Overview (ELI5 + key concepts + why it matters + misconceptions + "what you already know")
2. Mind Map (Mermaid syntax rendered client-side)
3. Flashcards (flip-card UI, 8 cards)
4. Quiz (5 MCQ questions, scoring, explanations)
5. Cache per `(user_id, topic_slug)` in SQLite
6. "Save to Brain" on the Overview section only
7. "Surprise Me" button (random topic from user's brain_memories)

**NOT in Phase 1**: Podcast, audio, TTS, history page, export, sharing, SVG diagrams.

---

## Backend — Step by Step

### Step 1: Create `backend/explore_db.py`

SQLite helpers for topic explorations. Model after `backend/history.py`.

```python
# Functions to implement:
def ensure_table() -> None: ...
    # CREATE TABLE IF NOT EXISTS topic_explorations (
    #   id              TEXT PRIMARY KEY,
    #   user_id         TEXT NOT NULL,
    #   topic           TEXT NOT NULL,
    #   topic_slug      TEXT NOT NULL,
    #   content_json    TEXT NOT NULL,
    #   created_at      TEXT NOT NULL,
    #   regenerated_at  TEXT
    # );
    # CREATE UNIQUE INDEX IF NOT EXISTS idx_explore_user_slug 
    #   ON topic_explorations(user_id, topic_slug);

def get_exploration(user_id: str, topic_slug: str) -> Optional[dict]: ...
    # Returns row as dict or None

def upsert_exploration(user_id: str, topic: str, topic_slug: str, content: dict) -> dict: ...
    # INSERT OR REPLACE. Returns the stored row.

def slugify(topic: str) -> str: ...
    # lowercase, strip special chars, replace spaces with hyphens, max 80 chars
```

### Step 2: Create `backend/pipelines/explore.py`

The core generation pipeline. One Claude call, structured JSON output.

```python
async def generate_exploration(topic: str, user_id: str, force: bool = False) -> Optional[dict]:
    """
    Check SQLite cache first (unless force=True).
    If cached, return it immediately.
    Otherwise: gather context, call Claude, cache result, return.
    """
```

**Context gathering** (before the Claude call):
```python
# 1. Get user's existing knowledge categories
categories = brain.get_categories(user_id)  # list of {name, count}

# 2. Semantic search for related memories
related = brain.search_memories(topic, user_id, limit=5)
# Filter to score > 0.65 only

# 3. Build context strings for the prompt
existing_knowledge = "\n".join(
    f"- [{m['category']}] {m['content'][:100]}" 
    for m in related
) or "(none)"
```

**The Claude prompt** (use this exact structure):

```python
SYSTEM = """You are an expert educator. You explain any topic clearly, accurately, and engagingly.
You always output valid JSON matching the exact schema provided. No markdown fences, no extra keys."""

USER = f"""Create a complete learning package for the topic: "{topic}"

The user already knows these related things (reference them briefly in eli5, don't re-explain):
{existing_knowledge}

Output ONLY this JSON structure (no markdown, no code fences):
{{
  "topic": "{topic}",
  "overview": {{
    "eli5": "3-4 paragraph layman explanation. Use analogies. Reference existing knowledge if relevant. No jargon without definition.",
    "key_concepts": [
      {{"term": "concept name", "definition": "one sentence, plain English"}}
    ],
    "why_it_matters": "2-3 sentences on real-world relevance and impact.",
    "misconceptions": [
      "Common wrong belief — and why it's wrong (one sentence each)"
    ]
  }},
  "mindmap_mermaid": "mindmap\\n  root(({topic}))\\n    SubTopic1\\n      detail1\\n      detail2\\n    SubTopic2\\n      ...",
  "flashcards": [
    {{"question": "...", "answer": "..."}}
  ],
  "quiz": [
    {{
      "question": "...",
      "options": ["A", "B", "C", "D"],
      "correct_index": 0,
      "explanation": "Why A is correct and the others are wrong."
    }}
  ]
}}

Rules:
- eli5: minimum 3 paragraphs. Use real analogies. Start with the simplest possible mental model.
- key_concepts: 4-6 items. Only genuinely important terms, not common words.
- misconceptions: exactly 3 items.
- mindmap_mermaid: valid Mermaid mindmap syntax. Root node, 4-6 branches, 2-3 leaves each. 
  Use (()) for root, [] for branches, () for leaves.
- flashcards: exactly 8 cards. Mix recall (what is X) and application (when would you use X).
- quiz: exactly 5 questions. 2 easy, 2 medium, 1 hard. 4 options each. correct_index is 0-3.
"""
```

**The Claude call**:
```python
import anthropic, json, os

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

response = client.messages.create(
    model=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5"),
    max_tokens=8000,
    system=SYSTEM,
    messages=[{"role": "user", "content": USER}],
)

raw = response.content[0].text.strip()
# Strip markdown fences if Claude adds them anyway
if raw.startswith("```"):
    raw = raw.split("```")[1]
    if raw.startswith("json"):
        raw = raw[4:]
content = json.loads(raw)
```

After successful parse: call `explore_db.upsert_exploration(user_id, topic, slug, content)` and return the content dict.

### Step 3: Add to `backend/main.py`

In the `startup()` function, add:
```python
import explore_db
explore_db.ensure_table()
```

### Step 4: Add GraphQL support to `backend/graphql_handler.py`

Add to `SCHEMA_SDL`:
```graphql
extend type Query {
  exploration(topic: String!): TopicExploration
}

extend type Mutation {
  exploreTopic(topic: String!, regenerate: Boolean): TopicExploration!
  saveExplorationSection(topic: String!, content: String!, category: String!): Memory!
  surpriseMe: String!
}

type TopicExploration {
  id: String!
  topic: String!
  topicSlug: String!
  overviewJson: String!
  mindmapMermaid: String!
  flashcardsJson: String!
  quizJson: String!
  createdAt: String!
  cached: Boolean!
}

type Memory {
  id: String!
  content: String!
  category: String!
  createdAt: String!
}
```

Add resolvers in the `handle()` function:

**`exploreTopic` mutation**:
```python
elif field == "exploreTopic":
    topic = args.get("topic", "").strip()
    regenerate = args.get("regenerate", False)
    if not topic:
        return JSONResponse({"errors": [{"message": "topic is required"}]}, 400)
    
    from pipelines.explore import generate_exploration
    result = await generate_exploration(topic, user_id, force=regenerate)
    if not result:
        return JSONResponse({"errors": [{"message": "Generation failed"}]}, 500)
    
    content = result["content"]
    return JSONResponse({"data": {field: {
        "id": result["id"],
        "topic": result["topic"],
        "topicSlug": result["topic_slug"],
        "overviewJson": json.dumps(content["overview"]),
        "mindmapMermaid": content["mindmap_mermaid"],
        "flashcardsJson": json.dumps(content["flashcards"]),
        "quizJson": json.dumps(content["quiz"]),
        "createdAt": result["created_at"],
        "cached": not regenerate,
    }}})
```

**`saveExplorationSection` mutation**:
```python
elif field == "saveExplorationSection":
    content = args.get("content", "").strip()
    category = args.get("category", "Explorations").strip()
    if not content:
        return JSONResponse({"errors": [{"message": "content required"}]}, 400)
    memory = brain.save_memory(content, category, user_id)
    return JSONResponse({"data": {field: {
        "id": memory["id"], "content": memory["content"],
        "category": memory["category"], "createdAt": memory["createdAt"],
    }}})
```

**`surpriseMe` mutation**:
```python
elif field == "surpriseMe":
    import random
    # Grab a random memory and use its category or first few words as topic
    cats = brain.get_categories(user_id)
    if cats:
        topic = random.choice(cats)["name"]
    else:
        topic = random.choice([
            "Transformer architecture", "RAG pipelines", "Attention mechanisms",
            "Vector databases", "Reinforcement learning from human feedback",
            "Mixture of experts", "Constitutional AI", "Prompt injection attacks"
        ])
    return JSONResponse({"data": {field: topic}})
```

---

## Frontend — Step by Step

### Step 1: Install mermaid

```bash
cd frontend && npm install mermaid
```

### Step 2: Add GraphQL queries to `frontend/src/graphql/queries.ts`

```typescript
export const EXPLORE_TOPIC = gql`
  mutation ExploreTopic($topic: String!, $regenerate: Boolean) {
    exploreTopic(topic: $topic, regenerate: $regenerate) {
      id topic topicSlug overviewJson mindmapMermaid flashcardsJson quizJson createdAt cached
    }
  }
`;

export const SURPRISE_ME = gql`
  mutation SurpriseMe {
    surpriseMe
  }
`;

export const SAVE_EXPLORATION_SECTION = gql`
  mutation SaveExplorationSection($topic: String!, $content: String!, $category: String!) {
    saveExplorationSection(topic: $topic, content: $content, category: $category) {
      id content category createdAt
    }
  }
`;
```

### Step 3: Add route to `frontend/src/App.tsx`

```tsx
import ExplorePage from './components/ExplorePage'
// In the router:
<Route path="/explore" element={<ExplorePage />} />
```

Add "Explore" to the nav alongside Dashboard, Knowledge, Tasks.

### Step 4: Create `frontend/src/components/ExplorePage.tsx`

**Component structure**:
```
ExplorePage
  ├── TopicInput (text field + Submit + Surprise Me button)
  ├── [when loading] ExploreLoadingSkeleton
  └── [when loaded] ExploreContent
        ├── ExploreHeader (topic title + cached badge + Regenerate button)
        ├── TabBar ([Overview] [Mind Map] [Flashcards] [Quiz])
        └── TabContent
              ├── OverviewTab
              ├── MindMapTab
              ├── FlashcardsTab
              └── QuizTab
```

**Key behaviours**:

1. **Topic input**: on submit, call `EXPLORE_TOPIC` mutation. Show loading skeleton during the ~10-15s generation. Disable input while loading.

2. **Tab state**: managed with `useState<'overview'|'mindmap'|'flashcards'|'quiz'>`. Persist active tab in URL hash (`#overview` etc.) so refresh stays on same tab.

3. **URL state**: on successful exploration, push `?topic=transformer-architecture` to URL so the page is bookmarkable and refreshes without re-generating (reads cache).

4. **Surprise Me**: call `SURPRISE_ME` mutation → get topic string back → populate input and immediately submit.

**OverviewTab** (`overview: { eli5, key_concepts, why_it_matters, misconceptions }`):
- Render `eli5` as paragraphs (split on `\n\n`)
- Key concepts: pill-style cards with term bold, definition below
- Why it matters: blockquote style
- Misconceptions: numbered list with ❌ prefix
- "Save Overview to Brain" button at bottom → calls `SAVE_EXPLORATION_SECTION` with `content = eli5 + why_it_matters`, `category = "Explorations"`
- Show "What you already know" block only if the backend returns related memories (add `relatedMemories` field to GraphQL response)

**MindMapTab** (`mindmapMermaid: string`):
```tsx
import mermaid from 'mermaid'
import { useEffect, useRef } from 'react'

// In component:
const ref = useRef<HTMLDivElement>(null)
useEffect(() => {
  if (!ref.current || !mindmapMermaid) return
  mermaid.initialize({ startOnLoad: false, theme: 'dark' })
  mermaid.render('mindmap-svg', mindmapMermaid).then(({ svg }) => {
    if (ref.current) ref.current.innerHTML = svg
  })
}, [mindmapMermaid])

return <div ref={ref} className="explore__mindmap" />
```
- Wrap in a horizontally scrollable container so wide maps don't break layout
- "Copy Mermaid" button copies raw syntax to clipboard

**FlashcardsTab** (`flashcards: [{question, answer}]`):
- One card visible at a time, flip animation on click
- Front: question. Back: answer.
- Prev / Next navigation
- Shuffle button (Fisher-Yates on the array)
- Progress indicator: "Card 3 of 8"
- CSS flip animation using `transform: rotateY(180deg)` — no JS library

**QuizTab** (`quiz: [{question, options, correct_index, explanation}]`):
- One question at a time
- 4 option buttons. On click: lock all options, highlight correct green, highlight selected red if wrong, show explanation below
- "Next Question" button appears after answering
- Final screen: score X/5, verdict message, "Try Again" button (resets state, same questions)
- Verdict messages: 5/5 → "Perfect score 🎯", 4/5 → "Almost there!", 3/5 → "Good foundation", <3 → "Review the overview"
- Do NOT randomise question order (makes retrying meaningful)

### Step 5: Create `frontend/src/components/ExplorePage.css`

Match the existing dark theme. Key CSS variables already in use:
```css
var(--bg-base)        /* page background */
var(--bg-card)        /* card surfaces */
var(--bg-elevated)    /* hover states */
var(--border-base)    /* borders */
var(--text-primary)   /* main text */
var(--text-muted)     /* secondary text */
var(--text-faint)     /* placeholder text */
var(--accent-blue)    /* primary actions */
var(--accent-green)   /* success states */
var(--accent-red)     /* error/wrong answer */
```

Key classes to implement:
```css
.explore__page          /* full page container */
.explore__input-row     /* topic input + buttons row */
.explore__topic-input   /* the text field — style like chat__textarea */
.explore__submit-btn    /* primary action button */
.explore__surprise-btn  /* secondary, ghost style */
.explore__tabs          /* tab bar */
.explore__tab           /* individual tab button */
.explore__tab--active   /* active tab underline */
.explore__content       /* tab content area */
.explore__loading       /* skeleton loading state */

/* Overview */
.explore__eli5          /* paragraph block */
.explore__concepts      /* key concepts grid */
.explore__concept-card  /* individual concept pill */
.explore__misconceptions /* misconceptions list */
.explore__save-btn      /* save to brain CTA */

/* Mind map */
.explore__mindmap       /* scrollable svg container */

/* Flashcards */
.explore__flashcard-wrap /* perspective container */
.explore__flashcard      /* the card itself */
.explore__flashcard--flipped /* rotated state */
.explore__flashcard-front
.explore__flashcard-back
.explore__card-nav       /* prev/next/shuffle row */

/* Quiz */
.explore__question
.explore__options
.explore__option         /* button */
.explore__option--correct
.explore__option--wrong
.explore__option--locked
.explore__explanation
.explore__quiz-result
```

---

## Checklist Before Calling Done

- [ ] `explore_db.ensure_table()` called in startup — no migration errors
- [ ] `POST /graphql` with `exploreTopic` mutation returns valid JSON for a test topic
- [ ] Same topic called twice returns cached result (check `created_at` is same)
- [ ] `regenerate: true` bypasses cache and generates fresh content
- [ ] Mermaid renders without console errors
- [ ] Flashcard flip animation works on click (both directions)
- [ ] Quiz locks options after answering, shows explanation
- [ ] Quiz final score screen shows correct count
- [ ] "Save Overview to Brain" creates a memory (check via Knowledge page)
- [ ] "Surprise Me" button populates input with a topic and auto-submits
- [ ] Page is bookmarkable — `?topic=xxx` reloads correctly from cache
- [ ] No TypeScript errors (`npm run build` passes)
- [ ] No Python errors (`python -m py_compile backend/pipelines/explore.py`)
- [ ] Loading state shows skeleton, not blank page
- [ ] Error state shows a message if Claude call fails, doesn't crash

---

## What Good Looks Like

A user types "Attention Is All You Need" and within 15 seconds sees:
- A clear 3-paragraph explanation that references any saved notes they already have about transformers
- A mind map with branches: Self-Attention, Multi-Head Attention, Positional Encoding, Encoder/Decoder, Training
- 8 flashcards mixing "what is a query vector?" with "when would you use a decoder-only model?"
- A 5-question quiz where question 5 is genuinely hard ("What is the computational complexity of self-attention with respect to sequence length?")
- A "Save to Brain" button that works

That's the bar. Build to that standard.
