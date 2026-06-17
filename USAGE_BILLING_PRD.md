# PRD: Usage Limits & Billing

**Feature name:** Usage Dashboard + Billing  
**Status:** Draft  
**Owner:** Naveenkumar  
**Stack context:** FastAPI · SQLite · Claude API (Anthropic) · OpenAI Embeddings · faster-whisper (local) · React/TypeScript · Apollo GraphQL  
**Target modes:** Self-hosted (Phase 1) → Multi-user SaaS with Stripe (Phase 2)

---

## Problem Statement

Every interaction in Personal Brain costs real money — Claude calls for chat, reasoning, briefings, topic explorations; OpenAI for embeddings; optionally OpenAI TTS for podcast audio. Currently there is zero visibility into spend, no way to set a cap, and no mechanism to bill other users if the app is opened to others. A single busy day could burn $10–50 without any warning. Before scaling to multiple users, the app needs metered usage tracking, budget enforcement, and a clear path to subscription billing.

---

## Goals

1. **Full cost visibility** — every API call logs tokens consumed and a USD cost estimate, queryable per user per time window.
2. **Enforceable limits** — hard and soft caps per user per billing period; requests are rejected gracefully when the limit is hit.
3. **Plan-based access tiers** — free / pro / custom plans gate feature access and usage quotas.
4. **Upgrade-ready billing** — Stripe Checkout integration so paying users can self-serve subscribe; webhooks keep plan state in sync.
5. **Transparent user-facing UI** — Usage & Billing page in Settings shows spend breakdown, quota meter, and current plan with upgrade path.

---

## Non-Goals

- No real-time streaming cost counter (Phase 2+).
- No per-request invoicing or metered Stripe billing per API call — flat subscription only in v1.
- No usage-based pricing for SaaS in v1 — that's a future tier.
- No per-feature sub-limits (e.g., "max 5 explores/day") in v1 — only total monthly spend cap.
- No credit card storage — Stripe handles all PCI scope.
- No refund or pro-ration flows in v1.

---

## User Stories

### Self-hosted owner

- As the app owner, I want to see exactly how much I've spent on Claude and OpenAI this month so I can decide if I need to switch to a cheaper model or reduce usage.
- As the app owner, I want to set a monthly spend cap so the app auto-throttles before I get a surprise bill.
- As the app owner, I want a breakdown by feature (chat, explore, briefing, embedding) so I know what's driving cost.

### SaaS user (Phase 2)

- As a new user on the free plan, I want to clearly see how many requests I have left this month and what I get if I upgrade.
- As a paying subscriber, I want my billing to be handled entirely in Stripe — I should never have to share card details with this app directly.
- As a subscriber, I want to cancel my plan from within the app without emailing anyone.

### SaaS operator (Phase 2)

- As the operator, I want failed Stripe payments to automatically downgrade users to free tier so I'm not serving unpaid usage.
- As the operator, I want to grant specific users a "custom" plan with bespoke limits without modifying code.

---

## Requirements

### P0 — Must Have (Phase 1: self-hosted)

#### Usage Tracking

- [ ] New SQLite table `usage_events` — every Claude and OpenAI API call writes a row:
  `(id, user_id, feature, model, input_tokens, output_tokens, cost_usd, created_at)`
- [ ] Cost calculation at write time using current published per-token pricing (configurable via env):
  - `CLAUDE_SONNET_IN_PRICE` — $/M input tokens (default: $3.00)
  - `CLAUDE_SONNET_OUT_PRICE` — $/M output tokens (default: $15.00)
  - `OPENAI_EMBED_PRICE` — $/M tokens (default: $0.02)
  - `OPENAI_TTS_PRICE` — $/M characters (default: $15.00)
- [ ] Features tracked: `chat`, `explore`, `briefing`, `learning_picks`, `embedding`, `tts`, `vault` (no OpenAI calls today but logged for future)
- [ ] faster-whisper is local — no cost entry, but log `transcription_seconds` in a separate counter for capacity planning
- [ ] `usage_db.py` module mirrors `explore_db.py` pattern: `ensure_table()`, `log_event()`, `get_summary(user_id, period)`, `get_breakdown(user_id, period)`

#### Budget Limits

- [ ] New SQLite table `user_limits` — per-user configurable caps:
  `(user_id, monthly_budget_usd, daily_budget_usd, hard_limit, updated_at)`
- [ ] Default limits from env: `DEFAULT_MONTHLY_BUDGET_USD` (default: `10.0`), `DEFAULT_DAILY_BUDGET_USD` (default: `2.0`)
- [ ] `hard_limit` flag: if True, reject the API call with a 429 + clear error message when over budget; if False (soft limit), allow but flag the response
- [ ] Budget check middleware (`check_budget(user_id)`) called at the top of every Claude/OpenAI call — returns `(ok: bool, spent: float, budget: float)`
- [ ] When a hard limit is hit: GraphQL mutation returns `_err("Monthly budget exceeded — update your limit in Settings > Usage")` with a `budget_exceeded` error code the frontend can intercept and show a banner

#### GraphQL API

- [ ] `query usageSummary(period: UsagePeriod!): UsageSummary` — total spend, request count, by-feature breakdown for the period
- [ ] `query usageHistory(days: Int): [UsageDayBucket!]!` — daily spend totals for the chart (last N days)
- [ ] `mutation updateBudget(monthlyBudgetUsd: Float!, dailyBudgetUsd: Float!, hardLimit: Boolean!): UserLimits!`
- [ ] `query userLimits: UserLimits!` — current plan, limits, spent this period
- [ ] `query userPlan: UserPlan!` — plan name, features, quota limits (for gating in frontend)

#### Frontend: Usage & Billing tab in Settings

- [ ] New tab "Usage & Billing" added to `SettingsPage.tsx` (or extracted as `UsagePage.tsx` reachable from Settings)
- [ ] **Quota meter** — progress bar showing monthly spend vs. budget, colour-coded: green < 70%, amber 70–90%, red > 90%
- [ ] **Daily meter** — secondary progress bar for today's spend
- [ ] **By-feature breakdown table** — rows for chat / explore / briefing / embedding with spend and request count this month
- [ ] **Spend history chart** — 30-day bar chart (recharts, already in the frontend stack) of daily spend
- [ ] **Budget editor** — inline form: monthly cap (USD), daily cap (USD), hard limit toggle with warning "requests will be rejected when cap is hit"
- [ ] **Plan badge** — shows current plan (`Self-hosted`, `Free`, `Pro`) — Phase 2 adds upgrade CTA
- [ ] **Budget exceeded banner** — app-level banner (above nav) shown when the user hits > 100% of their budget, with link to Usage & Billing
- [ ] Model pricing disclaimer: "Cost estimates based on published API prices. Actual charges may differ."

---

### P1 — Should Have (Phase 2: multi-user SaaS)

#### Plans & Tiers

- [ ] New table `user_plans`:
  `(user_id, plan, stripe_customer_id, stripe_subscription_id, current_period_start, current_period_end, status, updated_at)`
- [ ] Three plans at launch:

  | Plan      | Monthly price | Monthly Claude budget | Explore limit | Chat limit |
  |-----------|--------------|----------------------|---------------|------------|
  | Free      | $0           | $2 spend cap          | 10 topics     | 50 messages|
  | Pro       | $9.99        | $15 spend cap         | Unlimited     | Unlimited  |
  | Custom    | Manual       | Configurable          | Configurable  | Configurable|

- [ ] Plan enforced at the GraphQL layer — not just budget, but feature-level gates (e.g., free users get an upgrade prompt after 10 explores)

#### Stripe Integration

- [ ] `stripe_billing.py` module: `create_checkout_session(user_id, plan)` → Stripe Checkout URL (hosted page, no card data in our app)
- [ ] `POST /billing/checkout` endpoint → creates session, returns `{url}` for frontend redirect
- [ ] `POST /billing/webhook` endpoint — handles:
  - `checkout.session.completed` → activate subscription, write `user_plans`
  - `invoice.payment_succeeded` → extend `current_period_end`, reset monthly usage counter
  - `invoice.payment_failed` → downgrade to free, set `status = 'past_due'`
  - `customer.subscription.deleted` → downgrade to free immediately
- [ ] Webhook signature verification via `STRIPE_WEBHOOK_SECRET` env var
- [ ] `GET /billing/portal` → Stripe Customer Portal URL for self-service cancel/update

#### Frontend additions (Phase 2)

- [ ] **Upgrade CTA** — on Usage page, "Upgrade to Pro" button → `POST /billing/checkout` → redirect to Stripe Checkout
- [ ] **Plan card** — shows plan name, renewal date, Stripe-managed price
- [ ] **"Manage Billing"** button → `GET /billing/portal` → Stripe portal (cancel, update card)
- [ ] **Feature gate toasts** — when a free user hits a limit, show a dismissible banner "You've used all 10 free explores this month — upgrade to Pro for unlimited access"
- [ ] **Past-due warning** — if `status === 'past_due'`, show a persistent red banner with "Update payment method" → portal link

---

### P2 — Future

- Usage-based metered billing (charge per token, not flat subscription)
- Team plans (shared quota across multiple users in an org)
- Per-model cost breakdowns (e.g., Haiku vs. Sonnet vs. Opus)
- Spend alerts via Telegram bot (`/budget` command returns current spend + % of cap)
- Exportable usage CSV for tax/accounting
- Annual billing discount (20% off Pro)
- Admin dashboard: all users, their plans, spend totals (operator view)

---

## Technical Architecture

### New Backend Files

```
backend/
  usage_db.py            # SQLite CRUD for usage_events + user_limits + user_plans
  pipelines/
    budget.py            # check_budget() middleware called before every API call
  stripe_billing.py      # Stripe Checkout + portal + webhook handler (Phase 2)
```

### Database Schema

```sql
-- Every API call that costs money
CREATE TABLE IF NOT EXISTS usage_events (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    feature      TEXT NOT NULL,   -- 'chat'|'explore'|'briefing'|'embedding'|'tts'|'learning_picks'
    model        TEXT NOT NULL,   -- 'claude-sonnet-4-5'|'text-embedding-3-small'|'tts-1'|etc.
    input_tokens  INT  DEFAULT 0,
    output_tokens INT  DEFAULT 0,
    char_count    INT  DEFAULT 0, -- for TTS cost (per character)
    cost_usd      REAL NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user_time ON usage_events(user_id, created_at);

-- Per-user configurable budget caps
CREATE TABLE IF NOT EXISTS user_limits (
    user_id            TEXT PRIMARY KEY,
    monthly_budget_usd REAL NOT NULL DEFAULT 10.0,
    daily_budget_usd   REAL NOT NULL DEFAULT 2.0,
    hard_limit         INT  NOT NULL DEFAULT 1,  -- 1=hard, 0=soft
    updated_at         TEXT NOT NULL
);

-- Phase 2: plan + Stripe subscription state
CREATE TABLE IF NOT EXISTS user_plans (
    user_id                  TEXT PRIMARY KEY,
    plan                     TEXT NOT NULL DEFAULT 'free',  -- 'free'|'pro'|'custom'
    stripe_customer_id       TEXT,
    stripe_subscription_id   TEXT,
    current_period_start     TEXT,
    current_period_end       TEXT,
    status                   TEXT NOT NULL DEFAULT 'active', -- 'active'|'past_due'|'canceled'
    updated_at               TEXT NOT NULL
);
```

### Usage Instrumentation Pattern

Every function that calls Claude or OpenAI adds two lines after the API response:

```python
# Example: in pipelines/explore.py after _client.messages.create()
from usage_db import log_event, calc_cost

response = _client.messages.create(...)
cost = calc_cost("claude", response.usage.input_tokens, response.usage.output_tokens)
log_event(user_id, feature="explore", model=MODEL,
          input_tokens=response.usage.input_tokens,
          output_tokens=response.usage.output_tokens,
          cost_usd=cost)
```

For OpenAI embeddings in `brain.py`:
```python
cost = calc_cost("openai_embed", token_count=len(text.split()))
log_event(user_id, feature="embedding", model=EMBED_MODEL, input_tokens=tokens, cost_usd=cost)
```

### Budget Check Pattern

```python
# In graphql_handler.py, at the top of any expensive operation:
from pipelines.budget import check_budget

ok, spent, limit = check_budget(user_id)
if not ok:
    return _err(f"Monthly budget of ${limit:.2f} exceeded (${spent:.2f} used). "
                "Update your limit in Settings → Usage & Billing.", code="BUDGET_EXCEEDED")
```

### GraphQL Schema Additions

```graphql
enum UsagePeriod { today, this_month, last_month, last_7_days, last_30_days }

type UsageSummary {
  periodLabel:     String!
  totalCostUsd:    Float!
  requestCount:    Int!
  breakdown:       [FeatureUsage!]!
}

type FeatureUsage {
  feature:         String!
  costUsd:         Float!
  requestCount:    Int!
  inputTokens:     Int!
  outputTokens:    Int!
}

type UsageDayBucket {
  date:            String!
  costUsd:         Float!
  requestCount:    Int!
}

type UserLimits {
  monthlyBudgetUsd: Float!
  dailyBudgetUsd:   Float!
  hardLimit:        Boolean!
  spentThisMonth:   Float!
  spentToday:       Float!
  percentMonthly:   Float!
  percentDaily:     Float!
}

type UserPlan {
  plan:              String!  # 'self_hosted'|'free'|'pro'|'custom'
  status:            String!
  currentPeriodEnd:  String
  monthlyQuotaUsd:   Float!
  featuresAllowed:   [String!]!
}

extend type Query {
  usageSummary(period: UsagePeriod!): UsageSummary!
  usageHistory(days: Int): [UsageDayBucket!]!
  userLimits: UserLimits!
  userPlan: UserPlan!
}

extend type Mutation {
  updateBudget(monthlyBudgetUsd: Float!, dailyBudgetUsd: Float!, hardLimit: Boolean!): UserLimits!
}
```

### Cost Reference Table (at time of writing)

| Service               | Model                     | Input price    | Output price   |
|-----------------------|---------------------------|----------------|----------------|
| Anthropic Claude      | claude-sonnet-4-5         | $3.00/M tokens | $15.00/M tokens|
| Anthropic Claude      | claude-haiku-4-5          | $0.25/M tokens | $1.25/M tokens |
| OpenAI Embeddings     | text-embedding-3-small    | $0.02/M tokens | —              |
| OpenAI TTS            | tts-1                     | $15.00/M chars | —              |
| faster-whisper        | (local)                   | $0.00          | —              |

All prices stored as env vars so they can be updated without a code deploy.

---

## UX Flow

```
Settings → Usage & Billing tab

┌──────────────────────────────────────────────────────┐
│  Plan: Self-hosted                    [Upgrade →]     │
│                                                       │
│  This month's spend                                   │
│  ████████████░░░░  $7.23 / $10.00    72%             │
│                                                       │
│  Today                                               │
│  ██░░░░░░░░░░░░░░  $0.41 / $2.00    21%             │
│                                                       │
│  Breakdown                                            │
│  Chat          $4.10    38 requests                  │
│  Explore       $2.15    12 topics                    │
│  Briefing      $0.74     5 runs                      │
│  Embeddings    $0.24   842 vectors                   │
│                                                       │
│  ── 30-day spend chart ──                             │
│  [bar chart via recharts]                             │
│                                                       │
│  ── Budget Settings ──                                │
│  Monthly cap  [$10.00   ]                            │
│  Daily cap    [$2.00    ]                             │
│  [x] Hard limit — reject requests when cap is hit     │
│      [ ] Soft limit — allow but warn                  │
│                          [Save Changes]               │
└──────────────────────────────────────────────────────┘
```

When budget > 90%: amber banner at top of app  
When budget > 100%: red banner + hard stop on new requests (if hard_limit=true)

---

## Frontend Component Structure

```
frontend/src/components/settings/
  UsageBillingTab.tsx      # Main tab content — quota meters, chart, breakdown table
  UsageBillingTab.css
  BudgetEditor.tsx         # Inline form: monthly/daily caps + hard limit toggle
  SpendChart.tsx           # recharts BarChart wrapper for 30-day history
  FeatureBreakdownTable.tsx # Table with feature rows + spend/requests
  PlanCard.tsx             # Current plan + Phase 2 Stripe upgrade CTA
  BudgetBanner.tsx         # App-level warning banner (imported in App.tsx)
```

`SettingsPage.tsx` gets a new tab: `{ id: 'usage', label: 'Usage & Billing' }` — no new nav item needed.

`App.tsx` renders `<BudgetBanner />` above the sidebar, queries `userLimits` on mount and re-queries every 5 minutes.

---

## Success Metrics

| Metric | Target | When |
|---|---|---|
| Cost tracking accuracy | ≤ 5% delta vs. actual API invoice | Week 1 |
| Budget overshoot rate | 0% hard overages when hard_limit=true | Week 1 |
| Time-to-insight on spend | < 3 seconds to load usage page | Week 2 |
| Phase 2 conversion (free→pro) | 15%+ of multi-user beta users | 4 weeks post SaaS launch |
| Churn due to payment failure | < 5% of subscribers lost per month | 4 weeks post SaaS launch |

---

## Open Questions

| Question | Owner | Blocking? |
|---|---|---|
| Should the free tier require email signup (Google OAuth already in place), or can unauthed users get a small trial quota? | Naveenkumar | Yes — Phase 2 |
| Stripe product/price IDs: create test + live IDs before Phase 2 build starts | Naveenkumar | Yes — Phase 2 |
| Should `learning_picks` and `briefing` (background jobs) be logged against a `system` user or the user who triggered the last refresh? | Engineering | Yes — Phase 1 |
| Do we want a Telegram `/budget` command in Phase 1 or Phase 2? | Naveenkumar | No |
| What's the right free tier quota — $2/month spend cap or operation-count limits (e.g., 50 chats, 10 explores)? Spend cap is simpler but users don't understand it; operation counts are legible but harder to enforce precisely. | Naveenkumar | Yes — Phase 2 |

---

## Phased Delivery

### Phase 1 — Visibility & Self-Protection (1–2 weeks)
`usage_db.py` + instrumentation across all API call sites + budget check middleware + `usageSummary` / `userLimits` / `updateBudget` GraphQL ops + Usage & Billing tab in Settings + BudgetBanner in App.tsx. Zero Stripe dependency.

### Phase 2 — Multi-user SaaS (2–3 weeks)
`user_plans` table + plan-gating middleware + `stripe_billing.py` + Checkout/portal/webhook endpoints + PlanCard with upgrade CTA + feature gate toasts + past-due banner.

### Phase 3 — Operator Tooling (future)
Admin view of all users + spend totals. Usage export CSV. Telegram `/budget` command. Annual billing. Usage-based metered tier.

---

## Dependencies

- No new Python packages for Phase 1 (SQLite only)
- `stripe` Python package for Phase 2 (`pip install stripe`)
- `recharts` already in frontend — no new npm installs needed
- Stripe account with test + live API keys
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRO_PRICE_ID` env vars (Phase 2 only)
- Token pricing env vars: `CLAUDE_SONNET_IN_PRICE`, `CLAUDE_SONNET_OUT_PRICE`, `OPENAI_EMBED_PRICE`, `OPENAI_TTS_PRICE`
