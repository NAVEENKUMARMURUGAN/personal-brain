import json
import uuid
import logging
import traceback
from datetime import datetime, timezone, date
from typing import Any
from fastapi import Request
from fastapi.responses import JSONResponse

import brain
import tasks as tasks_module
import history
import dashboard_db
import weather as weather_module
import claude
import auth
from context_manager import ContextManager

logger = logging.getLogger(__name__)

SCHEMA_SDL = """
type Query {
  messages(limit: Int, cursor: String): MessagesPage
  tasks(date: String): TasksPage
  categories: [Category]
  memories(category: String, limit: Int, cursor: String): MemoriesPage
}

input AttachmentInput {
  name: String!
  mimeType: String!
  data: String!
}

type Mutation {
  send(content: String!, clearedAt: String, attachments: [AttachmentInput]): BrainResponse
  addTask(content: String!, date: String, recurrence: String, recurrenceEndDate: String): Task
  addReminder(content: String!, date: String!, time: String!, recurrence: String, recurrenceEndDate: String): Task
  editTask(taskId: String!, content: String!): Task
  deleteTask(taskId: String!): Boolean
  completeTask(taskId: String!): Task
}

type BrainResponse {
  answer: String
  type: String
  action: String
  payload: String
  sources: [Source]
}

type Task {
  id: String
  content: String
  status: String
  createdDate: String
  completedDate: String
  carriedOver: Boolean
  taskType: String
  reminderTime: String
  isRecurring: Boolean
  recurrence: String
  recurrenceEndDate: String
  parentTaskId: String
}

type Memory {
  id: String
  content: String
  category: String
  createdAt: String
  score: Float
}

type Source {
  id: String
  content: String
  category: String
  score: Float
  createdAt: String
}

type Message {
  id: String
  content: String
  type: String
  role: String
  payload: String
  createdAt: String
}

type MessagesPage {
  messages: [Message]
  pageInfo: PageInfo
}

type TasksPage {
  pending: [Task]
  completed: [Task]
  date: String
}

type MemoriesPage {
  memories: [Memory]
  pageInfo: PageInfo
}

type Category {
  name: String
  icon: String
  count: Int
}

type PageInfo {
  hasNextPage: Boolean
  cursor: String
}

# ── Dashboard ──────────────────────────────────────────────────

extend type Query {
  dashboard: Dashboard!
}

extend type Mutation {
  saveToBrain(feedItemId: ID!): Bookmark!
  reviewLearningCard(cardId: ID!, result: ReviewResult!): LearningCard!
  triageInboxItem(itemId: ID!, action: TriageAction!): Task!
  refreshBriefing: Briefing!
  saveVaultItem(label: String!, secret: String!, category: String, notes: String): VaultItem!
  deleteVaultItem(itemId: ID!): Boolean!
  updateVaultItem(itemId: ID!, label: String, secret: String, category: String, notes: String): VaultItem!
}

type VaultItem {
  id: ID!
  label: String!
  secret: String
  notes: String
  category: String!
  createdAt: String
  updatedAt: String
}

type Query {
  vaultItems: [VaultItem!]!
  searchVault(query: String!): [VaultItem!]!
}

type Dashboard {
  briefing: Briefing
  weather: Weather
  transit: TransitStatus!
  specialToday: [SpecialItem!]!
  today: TodayPanel!
  news: FeedSection!
  learningPicks: FeedSection!
  localToday: LocalSection!
  trendingRepos: [Repo!]!
  conceptOfTheDay: LearningCard
  weeklyStats: WeeklyStats!
}

type Briefing {
  id: String
  text: String!
  generatedAt: String!
  cycleDate: String!
}

type Weather {
  tempC: Float!
  feelsLikeC: Float
  rainProbability: Int!
  condition: String!
  windKmh: Float
  uvIndex: Float
  uvMax: Float
  precipSumMm: Float
  windMaxKmh: Float
  sunrise: String
  sunset: String
  hourly: [HourlyWeather!]!
}

type HourlyWeather {
  hour: String!
  tempC: Float!
  rainMm: Float!
}

type TransitStatus {
  overallSeverity: String!
  alerts: [TransitAlert!]!
}

type TransitAlert {
  id: String!
  line: String!
  severity: String!
  title: String!
  detail: String
}

type SpecialItem {
  emoji: String!
  label: String!
  kind: String!
  note: String
}

type TodayPanel {
  due: [Task!]!
  overdue: [OverdueTask!]!
  inbox: [InboxItem!]!
}

type OverdueTask {
  id: String!
  content: String!
  createdDate: String!
  daysOverdue: Int!
}

type InboxItem {
  id: String!
  content: String!
  createdDate: String!
  source: String!
}

type FeedSection {
  refreshedAt: String
  items: [FeedItem!]!
}

type FeedItem {
  id: String!
  rank: Int!
  title: String!
  sourceName: String!
  tag: String!
  summaryShort: String!
  summaryDetail: String!
  sourceUrl: String!
  mediaType: String!
  durationMin: Int
  bookmarked: Boolean!
  videoId: String
}

type LocalSection {
  alerts: [TransitAlert!]!
  advisories: [WeatherAdvisory!]!
}

type WeatherAdvisory {
  title: String!
  detail: String
  icon: String
  severity: String
}

type Repo {
  fullName: String!
  description: String!
  language: String
  starsGained7d: Int!
  whyItMatters: String!
}

type LearningCard {
  id: String!
  term: String!
  explanation: String!
  usageLine: String
  codeExample: String
  pathwayNode: String!
  ease: Float!
  timesSeen: Int!
  mastered: Boolean!
}

type WeeklyStats {
  tasksDone7d: Int!
  articlesSaved: Int!
  cardsMastered: Int!
  dayStreak: Int!
}

type Bookmark {
  id: String!
  feedItemId: String!
  createdAt: String!
}

enum ReviewResult {
  knew_it
  show_again
}

enum TriageAction {
  today
  later
  archive
}
"""


def _ok(data: Any) -> dict:
    return {"data": data, "errors": None}


def _err(message: str) -> dict:
    return {"data": None, "errors": [{"message": message}]}


def _serialize_payload(payload) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload)


def _parse_operation(body: dict) -> tuple[str, str, dict]:
    query = body.get("query", "")
    variables = body.get("variables") or {}

    # Detect operation type and name
    stripped = query.strip()
    if stripped.startswith("mutation"):
        op_type = "mutation"
    else:
        op_type = "query"

    return op_type, query, variables


def _extract_field_name(query: str) -> str:
    """Extract the root field name from a GraphQL query/mutation string."""
    import re
    # Strip comments and normalize whitespace
    # Match the first field inside the outermost { ... } block
    # Handles: { send(...) }, mutation Send($x: X!) { send(...) }, query { messages(...) }
    m = re.search(r'\{\s*(\w+)', query)
    if m:
        return m.group(1)
    return ""


async def handle(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(_err("Invalid JSON body"), status_code=400)

    # Extract user_id from Authorization header
    authorization = request.headers.get("Authorization")
    user_id = auth.extract_user_id(authorization)
    if not user_id:
        return JSONResponse(_err("Unauthorized — please log in"), status_code=401)

    op_type, query, variables = _parse_operation(body)
    field = _extract_field_name(query)

    logger.info("GraphQL op=%s field=%s user=%s", op_type, field, user_id[:8])

    try:
        if op_type == "mutation" and field == "send":
            result = await _handle_send(variables, user_id)
        elif op_type == "mutation" and field == "completeTask":
            result = await _handle_complete_task(variables, user_id)
        elif op_type == "mutation" and field == "addTask":
            result = await _handle_add_task(variables, user_id)
        elif op_type == "mutation" and field == "addReminder":
            result = await _handle_add_reminder(variables, user_id)
        elif op_type == "mutation" and field == "editTask":
            result = await _handle_edit_task(variables, user_id)
        elif op_type == "mutation" and field == "deleteTask":
            result = await _handle_delete_task(variables, user_id)
        elif op_type == "query" and field == "messages":
            result = await _handle_messages(variables, user_id)
        elif op_type == "query" and field == "tasks":
            result = await _handle_tasks(variables, user_id)
        elif op_type == "query" and field == "categories":
            result = await _handle_categories(user_id)
        elif op_type == "query" and field == "memories":
            result = await _handle_memories(variables, user_id)
        # ── Dashboard queries ──────────────────────────────────
        elif op_type == "query" and field == "dashboard":
            result = await _handle_dashboard(user_id)
        # ── Dashboard mutations ────────────────────────────────
        elif op_type == "mutation" and field == "saveToBrain":
            result = await _handle_save_to_brain(variables, user_id)
        elif op_type == "mutation" and field == "reviewLearningCard":
            result = await _handle_review_learning_card(variables, user_id)
        elif op_type == "mutation" and field == "triageInboxItem":
            result = await _handle_triage_inbox_item(variables, user_id)
        elif op_type == "mutation" and field == "refreshBriefing":
            result = await _handle_refresh_briefing(user_id)
        # ── Vault mutations ────────────────────────────────────
        elif op_type == "mutation" and field == "saveVaultItem":
            result = await _handle_save_vault_item(variables, user_id)
        elif op_type == "mutation" and field == "deleteVaultItem":
            result = await _handle_delete_vault_item(variables, user_id)
        elif op_type == "mutation" and field == "updateVaultItem":
            result = await _handle_update_vault_item(variables, user_id)
        # ── Vault queries ──────────────────────────────────────
        elif op_type == "query" and field == "vaultItems":
            result = await _handle_vault_items(user_id)
        elif op_type == "query" and field == "searchVault":
            result = await _handle_search_vault(variables, user_id)
        else:
            logger.error("Unknown operation: op=%s field=%s query=%r", op_type, field, query)
            return JSONResponse(_err(f"Unknown operation: {field}"), status_code=400)

        return JSONResponse(result)

    except Exception as e:
        logger.error("GraphQL handler error: %s\n%s", e, traceback.format_exc())
        return JSONResponse(_err(str(e)), status_code=500)


async def _handle_send(variables: dict, user_id: str) -> dict:
    content = variables.get("content", "").strip()
    if not content:
        return _err("content is required")

    cleared_at  = variables.get("clearedAt") or None
    attachments = variables.get("attachments") or []
    today       = datetime.now(timezone.utc).date().isoformat()

    history.save_message(str(uuid.uuid4()), content, "user", "text", None, user_id=user_id)

    ctx = await ContextManager.build(
        current_message=content, user_id=user_id, history_after=cleared_at
    )
    logger.debug("\n%s", ctx.debug())

    response = await claude.process_message(ctx, user_id=user_id, attachments=attachments)

    history.save_message(
        str(uuid.uuid4()), response["answer"], "assistant",
        response["type"], response.get("payload"), user_id=user_id,
    )

    serialized = dict(response)
    serialized["payload"] = _serialize_payload(response.get("payload"))
    return _ok({"send": serialized})


async def _handle_complete_task(variables: dict, user_id: str) -> dict:
    task_id = variables.get("taskId", "").strip()
    if not task_id:
        return _err("taskId is required")
    try:
        task = tasks_module.complete_task(task_id, user_id)
        return _ok({"completeTask": task})
    except ValueError as e:
        return _err(str(e))


async def _handle_add_task(variables: dict, user_id: str) -> dict:
    content = variables.get("content", "").strip()
    if not content:
        return _err("content is required")
    today = datetime.now(timezone.utc).date().isoformat()
    date              = variables.get("date", today) or today
    recurrence        = variables.get("recurrence") or None
    recurrence_end    = variables.get("recurrenceEndDate") or None
    tasks = tasks_module.save_tasks(
        [content], date, user_id,
        recurrence=recurrence,
        recurrence_end_date=recurrence_end,
    )
    return _ok({"addTask": tasks[0] if tasks else None})


async def _handle_add_reminder(variables: dict, user_id: str) -> dict:
    content = variables.get("content", "").strip()
    time_   = variables.get("time", "").strip()
    if not content or not time_:
        return _err("content and time are required")
    today = datetime.now(timezone.utc).date().isoformat()
    date           = variables.get("date", today) or today
    recurrence     = variables.get("recurrence") or None
    recurrence_end = variables.get("recurrenceEndDate") or None
    task = tasks_module.save_reminder_task(
        content, date, time_, user_id,
        recurrence=recurrence,
        recurrence_end_date=recurrence_end,
    )
    return _ok({"addReminder": task})


async def _handle_edit_task(variables: dict, user_id: str) -> dict:
    task_id = variables.get("taskId", "").strip()
    content = variables.get("content", "").strip()
    if not task_id or not content:
        return _err("taskId and content are required")
    try:
        task = tasks_module.edit_task(task_id, content, user_id)
        return _ok({"editTask": task})
    except ValueError as e:
        return _err(str(e))


async def _handle_delete_task(variables: dict, user_id: str) -> dict:
    task_id = variables.get("taskId", "").strip()
    if not task_id:
        return _err("taskId is required")
    ok = tasks_module.delete_task(task_id, user_id)
    return _ok({"deleteTask": ok})


async def _handle_messages(variables: dict, user_id: str) -> dict:
    limit  = int(variables.get("limit", 50))
    cursor = variables.get("cursor", None)
    page   = history.get_messages(limit=limit, cursor=cursor, user_id=user_id)
    msgs   = [dict(m, payload=_serialize_payload(m.get("payload"))) for m in page["messages"]]
    return _ok({"messages": {"messages": msgs, "pageInfo": page["pageInfo"]}})


async def _handle_tasks(variables: dict, user_id: str) -> dict:
    today     = datetime.now(timezone.utc).date().isoformat()
    date      = variables.get("date", today)
    task_page = tasks_module.get_tasks(date, user_id)
    return _ok({"tasks": task_page})


async def _handle_categories(user_id: str) -> dict:
    cats = brain.get_categories(user_id)
    return _ok({"categories": cats})


async def _handle_memories(variables: dict, user_id: str) -> dict:
    category = variables.get("category", None)
    limit    = int(variables.get("limit", 20))
    cursor   = variables.get("cursor", None)

    if category:
        page = brain.get_memories_by_category(category, user_id, limit=limit, cursor=cursor)
    else:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        offset  = int(cursor) if cursor else 0
        results, _ = brain._qdrant.scroll(
            collection_name=brain.COLLECTION_NAME,
            scroll_filter=Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]),
            limit=limit + 1, offset=offset, with_payload=True, with_vectors=False,
        )
        has_next = len(results) > limit
        memories = [
            {"id": str(p.id), "content": p.payload.get("content", ""),
             "category": p.payload.get("category", ""),
             "createdAt": p.payload.get("createdAt", ""), "score": None}
            for p in results[:limit]
        ]
        page = {"memories": memories, "pageInfo": {
            "hasNextPage": has_next,
            "cursor": str(offset + limit) if has_next else None,
        }}

    return _ok({"memories": page})


# ══════════════════════════════════════════════════════════════
# Dashboard handlers
# ══════════════════════════════════════════════════════════════

async def _handle_dashboard(user_id: str) -> dict:
    """Assemble the full Dashboard payload. Each sub-section fails independently."""
    today = date.today().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    dashboard: dict[str, Any] = {}

    # ── Briefing ──────────────────────────────────────────────
    try:
        from pipelines.briefing import generate_briefing
        briefing = await generate_briefing(user_id)
        if briefing:
            dashboard["briefing"] = {
                "id": briefing.get("id"),
                "text": briefing.get("text", ""),
                "generatedAt": briefing.get("generated_at", now_iso),
                "cycleDate": briefing.get("cycle_date", today),
            }
        else:
            dashboard["briefing"] = None
    except Exception as e:
        logger.error("Dashboard briefing error: %s", e)
        dashboard["briefing"] = None

    # ── Weather ───────────────────────────────────────────────
    try:
        w = weather_module.get_weather()
        dashboard["weather"] = {
            "tempC":          w.get("tempC", 0.0),
            "feelsLikeC":     w.get("feelsLikeC", 0.0),
            "rainProbability":w.get("rainProbability", 0),
            "condition":      w.get("condition", ""),
            "windKmh":        w.get("windKmh", 0.0),
            "uvIndex":        w.get("uvIndex", 0.0),
            "uvMax":          w.get("uvMax", 0.0),
            "precipSumMm":    w.get("precipSumMm", 0.0),
            "windMaxKmh":     w.get("windMaxKmh", 0.0),
            "sunrise":        w.get("sunrise", ""),
            "sunset":         w.get("sunset", ""),
            "hourly":         w.get("hourly", []),
        }
    except Exception as e:
        logger.error("Dashboard weather error: %s", e)
        dashboard["weather"] = None

    # ── Transit ───────────────────────────────────────────────
    try:
        alerts = dashboard_db.get_transit_alerts()
        disrupted = [a for a in alerts if a.get("severity") not in ("normal",)]
        overall_severity = "major" if any(a["severity"] == "major" for a in disrupted) \
            else "minor" if disrupted else "normal"
        dashboard["transit"] = {
            "overallSeverity": overall_severity,
            "alerts": [
                {
                    "id": a.get("id", ""),
                    "line": a.get("line", ""),
                    "severity": a.get("severity", "normal"),
                    "title": a.get("title", ""),
                    "detail": a.get("detail"),
                }
                for a in alerts
            ],
        }
    except Exception as e:
        logger.error("Dashboard transit error: %s", e)
        dashboard["transit"] = {"overallSeverity": "normal", "alerts": []}

    # ── Special Today ─────────────────────────────────────────
    try:
        # Merge user personal dates first, then global picks
        month_day = f"{datetime.now().month:02d}-{datetime.now().day:02d}"
        personal = dashboard_db.get_user_special_dates(user_id, month_day)
        global_items = dashboard_db.get_special_today(today) or []
        personal_items = [
            {"emoji": "🎂", "label": row.get("label", ""), "kind": "personal",
             "note": row.get("note")}
            for row in personal
        ]
        combined = (personal_items + global_items)[:5]
        dashboard["specialToday"] = combined
    except Exception as e:
        logger.error("Dashboard specialToday error: %s", e)
        dashboard["specialToday"] = []

    # ── Today Panel (tasks) ───────────────────────────────────
    try:
        task_page = tasks_module.get_tasks(today, user_id)
        pending = task_page.get("pending", [])
        due_today = [t for t in pending if t.get("createdDate") == today or not t.get("createdDate")]
        overdue_tasks = []
        for t in pending:
            created = t.get("createdDate", today)
            if created and created < today:
                from datetime import date as date_type
                try:
                    delta = (date.fromisoformat(today) - date.fromisoformat(created)).days
                except Exception:
                    delta = 0
                overdue_tasks.append({
                    "id": t["id"],
                    "content": t["content"],
                    "createdDate": created,
                    "daysOverdue": delta,
                })
        # Inbox: tasks with status='inbox'
        inbox_tasks = [t for t in pending if t.get("status") == "inbox"]
        dashboard["today"] = {
            "due": due_today,
            "overdue": overdue_tasks,
            "inbox": [
                {"id": t["id"], "content": t["content"],
                 "createdDate": t.get("createdDate", today), "source": "chat"}
                for t in inbox_tasks
            ],
        }
    except Exception as e:
        logger.error("Dashboard today error: %s", e)
        dashboard["today"] = {"due": [], "overdue": [], "inbox": []}

    # ── News ──────────────────────────────────────────────────
    try:
        bookmarked_ids = dashboard_db.get_bookmarked_item_ids(user_id)
        news_items = dashboard_db.get_feed_items("news", today)
        # Fall back to most recent cycle if today has no items yet
        if not news_items:
            last = dashboard_db.get_latest_feed_cycle("news")
            if last:
                news_items = dashboard_db.get_feed_items("news", last)
        refreshed_at = news_items[0].get("created_at") if news_items else None
        dashboard["news"] = {
            "refreshedAt": refreshed_at,
            "items": [_serialize_feed_item(item, bookmarked_ids) for item in news_items],
        }
    except Exception as e:
        logger.error("Dashboard news error: %s", e)
        dashboard["news"] = {"refreshedAt": None, "items": []}

    # ── Learning Picks ────────────────────────────────────────
    try:
        bookmarked_ids = dashboard_db.get_bookmarked_item_ids(user_id)
        learn_items = dashboard_db.get_feed_items("learning", today)
        if not learn_items:
            last = dashboard_db.get_latest_feed_cycle("learning")
            if last:
                learn_items = dashboard_db.get_feed_items("learning", last)
        refreshed_at = learn_items[0].get("created_at") if learn_items else None
        dashboard["learningPicks"] = {
            "refreshedAt": refreshed_at,
            "items": [_serialize_feed_item(item, bookmarked_ids) for item in learn_items],
        }
    except Exception as e:
        logger.error("Dashboard learningPicks error: %s", e)
        dashboard["learningPicks"] = {"refreshedAt": None, "items": []}

    # ── Local Today ───────────────────────────────────────────
    try:
        all_alerts = dashboard_db.get_transit_alerts()
        transit_alerts_out = []

        # All transit lines — show disruptions with severity, normals as green
        for a in all_alerts:
            a = dict(a)
            transit_alerts_out.append({
                "id":       a.get("id", ""),
                "line":     a.get("line", ""),
                "severity": a.get("severity", "normal"),
                "title":    a.get("title", "Normal Service"),
                "detail":   a.get("detail"),
            })

        # Weather-derived advisories — multiple thresholds, not just rain
        advisories = []
        w = dashboard.get("weather") or {}

        rain_prob   = w.get("rainProbability", 0)
        temp_c      = w.get("tempC", 20)
        feels_like  = w.get("feelsLikeC", temp_c)
        wind_kmh    = w.get("windKmh", 0)
        wind_max    = w.get("windMaxKmh", 0)
        uv_max      = w.get("uvMax", 0)
        precip_sum  = w.get("precipSumMm", 0)
        sunrise     = w.get("sunrise", "")
        sunset      = w.get("sunset", "")
        condition   = w.get("condition", "")

        if rain_prob >= 70:
            advisories.append({
                "title": f"Heavy rain likely — {rain_prob}% chance",
                "detail": f"Expected rainfall {precip_sum}mm today. Carry an umbrella and allow extra travel time.",
                "icon": "🌧", "severity": "major",
            })
        elif rain_prob >= 40:
            advisories.append({
                "title": f"Rain possible — {rain_prob}% chance",
                "detail": "Light rain possible. An umbrella wouldn't hurt.",
                "icon": "🌦", "severity": "minor",
            })

        if temp_c >= 35 or feels_like >= 37:
            advisories.append({
                "title": f"Extreme heat — {temp_c}°C (feels {feels_like}°C)",
                "detail": "Stay hydrated, limit outdoor activity during peak hours (11am–3pm), and wear sunscreen.",
                "icon": "🥵", "severity": "major",
            })
        elif temp_c >= 28:
            advisories.append({
                "title": f"Hot day — {temp_c}°C",
                "detail": f"Feels like {feels_like}°C. UV index up to {uv_max} — apply sunscreen if heading outside.",
                "icon": "☀️", "severity": "minor",
            })
        elif temp_c <= 5:
            advisories.append({
                "title": f"Cold day — {temp_c}°C",
                "detail": f"Feels like {feels_like}°C. Dress in layers.",
                "icon": "🧊", "severity": "minor",
            })

        if uv_max >= 8:
            advisories.append({
                "title": f"Very high UV — index {uv_max}",
                "detail": "SPF 50+ sunscreen, hat and protective clothing recommended if outdoors.",
                "icon": "🕶", "severity": "minor",
            })
        elif uv_max >= 6 and not any(a["icon"] in ("🥵", "☀️") for a in advisories):
            advisories.append({
                "title": f"High UV today — index {uv_max}",
                "detail": "Apply sunscreen before heading out.",
                "icon": "☀️", "severity": "info",
            })

        if wind_max >= 60:
            advisories.append({
                "title": f"Strong winds — up to {wind_max} km/h",
                "detail": "Secure outdoor furniture and allow extra travel time. Delays likely on exposed routes.",
                "icon": "💨", "severity": "major",
            })
        elif wind_max >= 40:
            advisories.append({
                "title": f"Windy conditions — up to {wind_max} km/h",
                "detail": "Gusty winds expected. Take care on elevated areas and cycle paths.",
                "icon": "💨", "severity": "minor",
            })

        if "thunder" in condition.lower():
            advisories.append({
                "title": "Thunderstorm warning",
                "detail": "Lightning and heavy rain possible. Avoid open areas and postpone outdoor activities.",
                "icon": "⛈", "severity": "major",
            })

        # Sunrise / sunset info always shown (informational)
        if sunrise and sunset:
            advisories.append({
                "title": f"Sunrise {sunrise} · Sunset {sunset}",
                "detail": None,
                "icon": "🌅", "severity": "info",
            })

        # Today's special events from special_today table
        try:
            import json as _json
            special_rows = dashboard_db.get_special_today(today)
            if special_rows:
                items_json = dict(special_rows[0]).get("items_json", "[]")
                items = _json.loads(items_json) if isinstance(items_json, str) else items_json
                for it in items:
                    advisories.append({
                        "title": f"{it.get('emoji', '')} {it.get('label', '')}",
                        "detail": it.get("note"),
                        "icon": it.get("emoji", "📅"),
                        "severity": "personal" if it.get("kind") == "personal" else "info",
                    })
        except Exception as e:
            logger.warning("localToday special items error: %s", e)

        dashboard["localToday"] = {
            "alerts":    transit_alerts_out,
            "advisories": advisories,
        }
    except Exception as e:
        logger.error("Dashboard localToday error: %s", e)
        dashboard["localToday"] = {"alerts": [], "advisories": []}

    # ── Trending Repos ────────────────────────────────────────
    try:
        repos = dashboard_db.get_repo_trends(today)
        if not repos:
            last = dashboard_db.get_latest_repo_cycle()
            if last:
                repos = dashboard_db.get_repo_trends(last)
        dashboard["trendingRepos"] = [
            {
                "fullName": r.get("full_name", ""),
                "description": r.get("description") or "",
                "language": r.get("language"),
                "starsGained7d": r.get("stars_gained_7d", 0),
                "whyItMatters": r.get("why_it_matters") or r.get("description") or "",
            }
            for r in repos[:6]
        ]
    except Exception as e:
        logger.error("Dashboard trendingRepos error: %s", e)
        dashboard["trendingRepos"] = []

    # ── Concept of the Day ────────────────────────────────────
    try:
        card = dashboard_db.get_concept_of_day()
        dashboard["conceptOfTheDay"] = _serialize_card(card) if card else None
    except Exception as e:
        logger.error("Dashboard conceptOfTheDay error: %s", e)
        dashboard["conceptOfTheDay"] = None

    # ── Weekly Stats ──────────────────────────────────────────
    try:
        stats = dashboard_db.compute_weekly_stats(user_id)
        # tasks_done_7d: query Qdrant for completed tasks in last 7 days
        try:
            seven_days_ago = (date.today().replace(day=max(1, date.today().day - 7))).isoformat()
            completed_page = tasks_module.get_tasks(today, user_id)
            completed = completed_page.get("completed", [])
            recent_done = sum(
                1 for t in completed
                if t.get("completedDate") and t["completedDate"] >= seven_days_ago
            )
            stats["tasksDone7d"] = recent_done
        except Exception:
            stats["tasksDone7d"] = 0
        dashboard["weeklyStats"] = {
            "tasksDone7d": stats["tasksDone7d"],
            "articlesSaved": stats["articlesSaved"],
            "cardsMastered": stats["cardsMastered"],
            "dayStreak": stats["dayStreak"],
        }
    except Exception as e:
        logger.error("Dashboard weeklyStats error: %s", e)
        dashboard["weeklyStats"] = {"tasksDone7d": 0, "articlesSaved": 0,
                                    "cardsMastered": 0, "dayStreak": 0}

    return _ok({"dashboard": dashboard})


def _extract_youtube_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from a watch or short URL, or None for non-YouTube URLs."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        if "youtube.com" in parsed.netloc:
            vid = parse_qs(parsed.query).get("v", [None])[0]
            return vid or None
        if "youtu.be" in parsed.netloc:
            return parsed.path.lstrip("/") or None
    except Exception:
        pass
    return None


def _serialize_feed_item(item: dict, bookmarked_ids: set) -> dict:
    """Convert a raw feed_items row to a GraphQL FeedItem shape."""
    source_url = item.get("source_url") or ""
    return {
        "id": item.get("id", ""),
        "rank": item.get("rank") or 0,
        "title": item.get("title", ""),
        "sourceName": item.get("source_name") or "",
        "tag": item.get("tag") or "research",
        "summaryShort": item.get("summary_short") or "",
        "summaryDetail": item.get("summary_detail") or "",
        "sourceUrl": source_url,
        "mediaType": item.get("media_type") or "article",
        "durationMin": item.get("duration_min"),
        "bookmarked": item.get("id", "") in bookmarked_ids,
        "videoId": _extract_youtube_id(source_url),
    }


def _serialize_card(card: dict) -> dict:
    """Convert a learning_cards row to a GraphQL LearningCard shape."""
    return {
        "id": card.get("id", ""),
        "term": card.get("term", ""),
        "explanation": card.get("explanation", ""),
        "usageLine": card.get("usage_line"),
        "codeExample": card.get("code_example"),
        "pathwayNode": card.get("pathway_node", "fundamentals"),
        "ease": card.get("ease", 2.5),
        "timesSeen": card.get("times_seen", 0),
        "mastered": bool(card.get("mastered", 0)),
    }


async def _handle_save_to_brain(variables: dict, user_id: str) -> dict:
    """Bookmark a feed item AND ingest it into Qdrant via existing ingestion path."""
    feed_item_id = variables.get("feedItemId", "").strip()
    if not feed_item_id:
        return _err("feedItemId is required")

    item = dashboard_db.get_feed_item_by_id(feed_item_id)
    if not item:
        return _err(f"Feed item {feed_item_id} not found")

    # Ingest into Qdrant (existing knowledge path)
    try:
        content = f"{item['title']}\n\n{item.get('summary_detail') or item.get('summary_short') or ''}"
        source_url = item.get("source_url", "")
        brain.chunk_and_save(content, "AI News", user_id)
        logger.info("saveToBrain: ingested feed item %s for user %s", feed_item_id, user_id[:8])
    except Exception as e:
        logger.error("saveToBrain ingest error: %s", e)
        # Don't fail the bookmark even if ingest fails

    # Create SQLite bookmark
    bookmark = dashboard_db.create_bookmark(user_id, feed_item_id)
    if not bookmark:
        return _err("Failed to create bookmark")

    return _ok({
        "saveToBrain": {
            "id": bookmark["id"],
            "feedItemId": feed_item_id,
            "createdAt": bookmark["created_at"],
        }
    })


async def _handle_review_learning_card(variables: dict, user_id: str) -> dict:
    card_id = variables.get("cardId", "").strip()
    result = variables.get("result", "").strip()
    if not card_id or result not in ("knew_it", "show_again"):
        return _err("cardId and result (knew_it | show_again) are required")

    updated = dashboard_db.update_learning_card_review(card_id, result)
    if not updated:
        return _err(f"Learning card {card_id} not found")

    return _ok({"reviewLearningCard": _serialize_card(updated)})


async def _handle_triage_inbox_item(variables: dict, user_id: str) -> dict:
    item_id = variables.get("itemId", "").strip()
    action = variables.get("action", "").strip()
    if not item_id or action not in ("today", "later", "archive"):
        return _err("itemId and action (today | later | archive) are required")

    today = date.today().isoformat()
    try:
        if action == "today":
            task = tasks_module.edit_task_status(item_id, "pending", user_id)
        elif action == "archive":
            tasks_module.delete_task(item_id, user_id)
            return _ok({"triageInboxItem": {"id": item_id, "content": "", "status": "deleted",
                                            "createdDate": today, "completedDate": None,
                                            "carriedOver": False}})
        else:  # later — move to a future date (tomorrow)
            from datetime import timedelta
            tomorrow = (date.today() + timedelta(days=1)).isoformat()
            task = tasks_module.reschedule_task(item_id, tomorrow, user_id)
        return _ok({"triageInboxItem": task})
    except Exception as e:
        logger.error("triageInboxItem error: %s", e)
        return _err(str(e))


async def _handle_refresh_briefing(user_id: str) -> dict:
    """Force-refresh the briefing (bypasses 30-min cache)."""
    try:
        from pipelines.briefing import generate_briefing
        briefing = await generate_briefing(user_id, force_refresh=True)
        if not briefing:
            return _err("Failed to generate briefing")
        return _ok({
            "refreshBriefing": {
                "id": briefing.get("id"),
                "text": briefing.get("text", ""),
                "generatedAt": briefing.get("generated_at", ""),
                "cycleDate": briefing.get("cycle_date", ""),
            }
        })
    except Exception as e:
        logger.error("refreshBriefing error: %s", e)
        return _err(str(e))


# ── Vault handlers ─────────────────────────────────────────────────────────────

def _serialize_vault_item(item: dict, include_secret: bool = False) -> dict:
    """Serialize a vault item for GraphQL response."""
    return {
        "id":         item.get("id", ""),
        "label":      item.get("label", ""),
        "secret":     item.get("secret", "") if include_secret else None,
        "notes":      item.get("notes", ""),
        "category":   item.get("category", "General"),
        "createdAt":  item.get("created_at", ""),
        "updatedAt":  item.get("updated_at", ""),
    }


async def _handle_save_vault_item(variables: dict, user_id: str) -> dict:
    """Save an encrypted vault item."""
    try:
        import vault as vault_module
        label    = variables.get("label", "").strip()
        secret   = variables.get("secret", "").strip()
        category = variables.get("category", "Passwords")
        notes    = variables.get("notes", "")
        if not label or not secret:
            return _err("label and secret are required")
        item = vault_module.save_item(user_id, label, secret, category, notes)
        return _ok({"saveVaultItem": _serialize_vault_item(item)})
    except RuntimeError as e:
        return _err(str(e))
    except Exception as e:
        logger.error("saveVaultItem error: %s", e)
        return _err(str(e))


async def _handle_delete_vault_item(variables: dict, user_id: str) -> dict:
    """Delete a vault item by ID."""
    try:
        import vault as vault_module
        item_id = variables.get("itemId", "")
        if not item_id:
            return _err("itemId is required")
        ok = vault_module.delete_item(user_id, item_id)
        return _ok({"deleteVaultItem": ok})
    except RuntimeError as e:
        return _err(str(e))
    except Exception as e:
        logger.error("deleteVaultItem error: %s", e)
        return _err(str(e))


async def _handle_update_vault_item(variables: dict, user_id: str) -> dict:
    """Update an existing vault item."""
    try:
        import vault as vault_module
        item_id  = variables.get("itemId", "")
        label    = variables.get("label")
        secret   = variables.get("secret")
        category = variables.get("category")
        notes    = variables.get("notes")
        if not item_id:
            return _err("itemId is required")
        updated = vault_module.update_item(user_id, item_id, label, secret, notes, category)
        if not updated:
            return _err("Item not found or access denied")
        return _ok({"updateVaultItem": _serialize_vault_item(updated)})
    except RuntimeError as e:
        return _err(str(e))
    except Exception as e:
        logger.error("updateVaultItem error: %s", e)
        return _err(str(e))


async def _handle_vault_items(user_id: str) -> dict:
    """List all vault items (labels only, no secrets)."""
    try:
        import vault as vault_module
        items = vault_module.list_items(user_id)
        return _ok({"vaultItems": [_serialize_vault_item(i) for i in items]})
    except RuntimeError as e:
        return _err(str(e))
    except Exception as e:
        logger.error("vaultItems error: %s", e)
        return _err(str(e))


async def _handle_search_vault(variables: dict, user_id: str) -> dict:
    """Search vault items and return with decrypted secrets."""
    try:
        import vault as vault_module
        query = variables.get("query", "")
        if not query:
            return _err("query is required")
        items = vault_module.search_items(user_id, query)
        # Include secret in search results — user explicitly asked to find it
        return _ok({"searchVault": [_serialize_vault_item(i, include_secret=True) for i in items]})
    except RuntimeError as e:
        return _err(str(e))
    except Exception as e:
        logger.error("searchVault error: %s", e)
        return _err(str(e))
