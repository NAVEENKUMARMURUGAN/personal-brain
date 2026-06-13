#!/usr/bin/env bash
# scripts/dashboard_dev.sh — Run all dashboard pipelines with fixture data.
#
# Usage:
#   ./scripts/dashboard_dev.sh [--seed-cards]
#
# What it does:
#   1. Sets FIXTURE_MODE=1 so pipelines load JSON fixture files instead of calling APIs.
#   2. Runs each pipeline once (news, learning, repos, special, transit).
#   3. Optionally seeds learning cards from the fixture file (--seed-cards).
#   4. Prints a summary of rows inserted.
#
# After running this script, start docker-compose normally and the dashboard
# will render fully populated without any API keys.
#
# Requirements: Python 3.11+, backend dependencies installed (or run inside Docker).
# Outside Docker, set SQLITE_PATH to a writable location, e.g.:
#   SQLITE_PATH=/tmp/brain_dev.db ./scripts/dashboard_dev.sh --seed-cards

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"
cd "$BACKEND_DIR"

# Detect Python binary
if command -v python3 &>/dev/null; then
    PY=python3
elif command -v python &>/dev/null; then
    PY=python
else
    echo "ERROR: No python3 or python found in PATH." >&2
    exit 1
fi

echo ""
echo "=========================================="
echo " Personal Brain — Dashboard Dev Populate  "
echo "=========================================="
echo ""
echo "→ Python: $PY ($($PY --version 2>&1))"

# Set fixture mode
export FIXTURE_MODE=1

# If SQLITE_PATH is not set, use /tmp/brain_dev.db locally
# (inside Docker it will already be set to /data/history.db)
if [[ -z "${SQLITE_PATH:-}" ]]; then
    export SQLITE_PATH="/tmp/brain_dev.db"
    echo "→ SQLITE_PATH not set — using $SQLITE_PATH (local dev fallback)"
else
    echo "→ SQLITE_PATH=$SQLITE_PATH"
fi

echo "→ FIXTURE_MODE=1 (no external API calls)"
echo ""

# ── Ensure tables exist ────────────────────────────────────────

$PY -c "
import sys
sys.path.insert(0, '.')
import dashboard_db
dashboard_db.ensure_dashboard_tables()
print('  Tables ready.')
"

echo ""

# ── Run each pipeline ──────────────────────────────────────────

run_pipeline() {
    local name="$1"
    local module="$2"
    echo "Running pipeline: $name ..."
    $PY -c "
import asyncio, sys
sys.path.insert(0, '.')
from ${module} import run_pipeline
asyncio.run(run_pipeline())
print('  done.')
"
}

run_pipeline "Transit"        "pipelines.transit"
run_pipeline "Special Today"  "pipelines.special"
run_pipeline "Trending Repos" "pipelines.repos"
run_pipeline "AI News"        "pipelines.news"
run_pipeline "Learning Picks" "pipelines.learning"

echo ""

# ── Seed learning cards ────────────────────────────────────────

if [[ "${1:-}" == "--seed-cards" ]]; then
    echo "Seeding learning cards from fixture ..."
    $PY -m scripts.seed_learning_cards --fixture
    echo ""
fi

# ── Summary ────────────────────────────────────────────────────

echo "Summary of dashboard data:"
$PY -c "
import sys
sys.path.insert(0, '.')
import dashboard_db as db
from datetime import date
today = date.today().isoformat()

conn = db.get_conn()
try:
    news_count    = conn.execute(\"SELECT COUNT(*) FROM feed_items WHERE kind='news' AND cycle_date=?\", (today,)).fetchone()[0]
    learn_count   = conn.execute(\"SELECT COUNT(*) FROM feed_items WHERE kind='learning' AND cycle_date=?\", (today,)).fetchone()[0]
    repos_count   = conn.execute(\"SELECT COUNT(*) FROM repo_trends WHERE cycle_date=?\", (today,)).fetchone()[0]
    transit_count = conn.execute('SELECT COUNT(*) FROM transit_alerts').fetchone()[0]
    special_count = conn.execute(\"SELECT COUNT(*) FROM special_today WHERE cycle_date=?\", (today,)).fetchone()[0]
    cards_count   = conn.execute('SELECT COUNT(*) FROM learning_cards').fetchone()[0]
    print(f'  news items     ({today}): {news_count}')
    print(f'  learning picks ({today}): {learn_count}')
    print(f'  trending repos ({today}): {repos_count}')
    print(f'  transit alerts:           {transit_count}')
    print(f'  special today  ({today}): {special_count}')
    print(f'  learning cards total:     {cards_count}')
finally:
    conn.close()
"

echo ""
echo "Done! The DB at \$SQLITE_PATH is populated."
if [[ "$SQLITE_PATH" == "/tmp/"* ]]; then
    echo ""
    echo "To use this data with Docker, copy it into the volume:"
    echo "  docker cp $SQLITE_PATH \$(docker compose ps -q backend):/data/history.db"
    echo "  docker compose restart backend"
fi
echo ""
