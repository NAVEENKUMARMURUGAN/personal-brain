import json
import uuid
import logging
import traceback
from datetime import datetime, timezone
from typing import Any
from fastapi import Request
from fastapi.responses import JSONResponse

import brain
import tasks as tasks_module
import history
import claude
from context_manager import ContextManager

logger = logging.getLogger(__name__)

SCHEMA_SDL = """
type Query {
  messages(limit: Int, cursor: String): MessagesPage
  tasks(date: String): TasksPage
  categories: [Category]
  memories(category: String, limit: Int, cursor: String): MemoriesPage
}

type Mutation {
  send(content: String!, clearedAt: String): BrainResponse
  addTask(content: String!, date: String): Task
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

    op_type, query, variables = _parse_operation(body)
    field = _extract_field_name(query)

    logger.info("GraphQL op=%s field=%s variables=%s", op_type, field, list(variables.keys()))

    try:
        if op_type == "mutation" and field == "send":
            result = await _handle_send(variables)
        elif op_type == "mutation" and field == "completeTask":
            result = await _handle_complete_task(variables)
        elif op_type == "mutation" and field == "addTask":
            result = await _handle_add_task(variables)
        elif op_type == "mutation" and field == "editTask":
            result = await _handle_edit_task(variables)
        elif op_type == "mutation" and field == "deleteTask":
            result = await _handle_delete_task(variables)
        elif op_type == "query" and field == "messages":
            result = await _handle_messages(variables)
        elif op_type == "query" and field == "tasks":
            result = await _handle_tasks(variables)
        elif op_type == "query" and field == "categories":
            result = await _handle_categories()
        elif op_type == "query" and field == "memories":
            result = await _handle_memories(variables)
        else:
            logger.error("Unknown operation: op=%s field=%s query=%r", op_type, field, query)
            return JSONResponse(_err(f"Unknown operation: {field}"), status_code=400)

        return JSONResponse(result)

    except Exception as e:
        logger.error("GraphQL handler error: %s\n%s", e, traceback.format_exc())
        return JSONResponse(_err(str(e)), status_code=500)


async def _handle_send(variables: dict) -> dict:
    content = variables.get("content", "").strip()
    if not content:
        return _err("content is required")

    # clearedAt is an ISO timestamp set by the frontend when the user clicks
    # "Clear" — ensures no history before that point is used as context.
    cleared_at = variables.get("clearedAt") or None

    today = datetime.now(timezone.utc).date().isoformat()

    # Save user message
    user_msg_id = str(uuid.uuid4())
    history.save_message(user_msg_id, content, "user", "text", None)

    # Build context snapshot (history, categories, pending tasks)
    ctx = await ContextManager.build(current_message=content, history_after=cleared_at)
    logger.debug("\n%s", ctx.debug())

    # Process via Claude
    response = await claude.process_message(ctx)

    # Save assistant message
    asst_msg_id = str(uuid.uuid4())
    history.save_message(
        asst_msg_id,
        response["answer"],
        "assistant",
        response["type"],
        response.get("payload"),
    )

    # Serialize payload for GraphQL (payload is returned as JSON string)
    serialized = dict(response)
    serialized["payload"] = _serialize_payload(response.get("payload"))

    return _ok({"send": serialized})


async def _handle_complete_task(variables: dict) -> dict:
    task_id = variables.get("taskId", "").strip()
    if not task_id:
        return _err("taskId is required")
    try:
        task = tasks_module.complete_task(task_id)
        return _ok({"completeTask": task})
    except ValueError as e:
        return _err(str(e))


async def _handle_add_task(variables: dict) -> dict:
    content = variables.get("content", "").strip()
    if not content:
        return _err("content is required")
    today = datetime.now(timezone.utc).date().isoformat()
    date = variables.get("date", today)
    tasks = tasks_module.save_tasks([content], date)
    return _ok({"addTask": tasks[0] if tasks else None})


async def _handle_edit_task(variables: dict) -> dict:
    task_id = variables.get("taskId", "").strip()
    content = variables.get("content", "").strip()
    if not task_id or not content:
        return _err("taskId and content are required")
    try:
        task = tasks_module.edit_task(task_id, content)
        return _ok({"editTask": task})
    except ValueError as e:
        return _err(str(e))


async def _handle_delete_task(variables: dict) -> dict:
    task_id = variables.get("taskId", "").strip()
    if not task_id:
        return _err("taskId is required")
    ok = tasks_module.delete_task(task_id)
    return _ok({"deleteTask": ok})


async def _handle_messages(variables: dict) -> dict:
    limit = int(variables.get("limit", 50))
    cursor = variables.get("cursor", None)
    page = history.get_messages(limit=limit, cursor=cursor)

    # Serialize payload field in each message
    messages = []
    for msg in page["messages"]:
        m = dict(msg)
        m["payload"] = _serialize_payload(m.get("payload"))
        messages.append(m)

    return _ok({"messages": {"messages": messages, "pageInfo": page["pageInfo"]}})


async def _handle_tasks(variables: dict) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    date = variables.get("date", today)
    task_page = tasks_module.get_tasks(date)
    return _ok({"tasks": task_page})


async def _handle_categories() -> dict:
    cats = brain.get_categories()
    return _ok({"categories": cats})


async def _handle_memories(variables: dict) -> dict:
    category = variables.get("category", None)
    limit = int(variables.get("limit", 20))
    cursor = variables.get("cursor", None)

    if category:
        page = brain.get_memories_by_category(category, limit=limit, cursor=cursor)
    else:
        # Return all memories (no category filter) via scroll
        all_points = []
        offset = int(cursor) if cursor else 0
        results, _ = brain._qdrant.scroll(
            collection_name=brain.COLLECTION_NAME,
            limit=limit + 1,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        has_next = len(results) > limit
        results = results[:limit]
        memories = [
            {
                "id": str(p.id),
                "content": p.payload.get("content", ""),
                "category": p.payload.get("category", ""),
                "createdAt": p.payload.get("createdAt", ""),
                "score": None,
            }
            for p in results
        ]
        page = {
            "memories": memories,
            "pageInfo": {
                "hasNextPage": has_next,
                "cursor": str(offset + limit) if has_next else None,
            }
        }

    return _ok({"memories": page})
