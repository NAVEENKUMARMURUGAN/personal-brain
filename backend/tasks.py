import os
import uuid
import logging
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional
from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
COLLECTION_NAME = "brain_tasks"
VECTOR_SIZE = 1536  # text-embedding-3-small outputs 1536-dim vectors

import brain as _brain_module
_qdrant = QdrantClient(url=QDRANT_URL)


def ensure_collections():
    existing = [c.name for c in _qdrant.get_collections().collections]
    if COLLECTION_NAME not in existing:
        _qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def _embed(text: str) -> list[float]:
    return _brain_module.embed(text)


def _row_to_task(point) -> dict:
    return {
        "id": str(point.id),
        "content": point.payload.get("content", ""),
        "status": point.payload.get("status", "pending"),
        "createdDate": point.payload.get("createdDate", ""),
        "completedDate": point.payload.get("completedDate", None),
        "carriedOver": point.payload.get("carriedOver", False),
        "taskType": point.payload.get("taskType", "task"),
        "reminderTime": point.payload.get("reminderTime", None),
        # Recurrence fields
        "isRecurring": point.payload.get("isRecurring", False),
        "recurrence": point.payload.get("recurrence", None),     # daily|weekly|monthly|weekdays
        "recurrenceEndDate": point.payload.get("recurrenceEndDate", None),
        "parentTaskId": point.payload.get("parentTaskId", None),
    }


def save_reminder_task(
    content: str,
    date: str,
    reminder_time: str,
    user_id: str,
    recurrence: Optional[str] = None,
    recurrence_end_date: Optional[str] = None,
) -> dict:
    """Create a task of type 'reminder' on the specified date with a reminder time (HH:MM).
    Optionally recurring (recurrence: daily|weekly|monthly|weekdays).
    """
    vector = _embed(content)
    task_id = str(uuid.uuid4())
    is_recurring = bool(recurrence and recurrence != "none")
    payload = {
        "content": content, "status": "pending",
        "createdDate": date, "completedDate": None,
        "carriedOver": False, "user_id": user_id,
        "taskType": "reminder", "reminderTime": reminder_time,
        "isRecurring": is_recurring,
        "recurrence": recurrence if is_recurring else None,
        "recurrenceEndDate": recurrence_end_date,
        "parentTaskId": None,
    }
    _qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=task_id, vector=vector, payload=payload)],
    )
    return {
        "id": task_id, "content": content, "status": "pending",
        "createdDate": date, "completedDate": None, "carriedOver": False,
        "taskType": "reminder", "reminderTime": reminder_time,
        "isRecurring": is_recurring,
        "recurrence": recurrence if is_recurring else None,
        "recurrenceEndDate": recurrence_end_date,
        "parentTaskId": None,
    }


def save_tasks(
    task_list: list[str],
    date: str,
    user_id: str,
    recurrence: Optional[str] = None,
    recurrence_end_date: Optional[str] = None,
) -> list[dict]:
    """Create one or more tasks. Supports recurrence (daily|weekly|monthly|weekdays)."""
    tasks = []
    is_recurring = bool(recurrence and recurrence != "none")
    for content in task_list:
        vector = _embed(content)
        task_id = str(uuid.uuid4())
        payload = {
            "content": content, "status": "pending",
            "createdDate": date, "completedDate": None,
            "carriedOver": False, "user_id": user_id,
            "taskType": "task",
            "reminderTime": None,
            "isRecurring": is_recurring,
            "recurrence": recurrence if is_recurring else None,
            "recurrenceEndDate": recurrence_end_date,
            "parentTaskId": None,
        }
        _qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=[PointStruct(id=task_id, vector=vector, payload=payload)],
        )
        tasks.append({
            "id": task_id, "content": content, "status": "pending",
            "createdDate": date, "completedDate": None, "carriedOver": False,
            "taskType": "task", "reminderTime": None,
            "isRecurring": is_recurring,
            "recurrence": recurrence if is_recurring else None,
            "recurrenceEndDate": recurrence_end_date,
            "parentTaskId": None,
        })
    return tasks


def get_tasks(date: str, user_id: str) -> dict:
    results, _ = _qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[
            FieldCondition(key="createdDate", match=MatchValue(value=date)),
            FieldCondition(key="user_id",     match=MatchValue(value=user_id)),
        ]),
        limit=200, with_payload=True, with_vectors=False,
    )
    pending, completed = [], []
    for point in results:
        task = _row_to_task(point)
        (completed if task["status"] == "complete" else pending).append(task)
    return {"pending": pending, "completed": completed, "date": date}


def get_pending_tasks(date: str, user_id: str) -> list[dict]:
    results, _ = _qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[
            FieldCondition(key="createdDate", match=MatchValue(value=date)),
            FieldCondition(key="status",      match=MatchValue(value="pending")),
            FieldCondition(key="user_id",     match=MatchValue(value=user_id)),
        ]),
        limit=200, with_payload=True, with_vectors=False,
    )
    return [_row_to_task(p) for p in results]


def complete_task(task_id: str, user_id: str) -> dict:
    result = _qdrant.retrieve(collection_name=COLLECTION_NAME, ids=[task_id],
                              with_payload=True, with_vectors=True)
    if not result:
        raise ValueError(f"Task {task_id} not found")
    point = result[0]
    if point.payload.get("user_id") != user_id:
        raise ValueError(f"Task {task_id} does not belong to this user")

    completed_date = datetime.now(timezone.utc).date().isoformat()
    updated_payload = dict(point.payload)
    updated_payload["status"] = "complete"
    updated_payload["completedDate"] = completed_date

    _qdrant.upsert(collection_name=COLLECTION_NAME,
                   points=[PointStruct(id=task_id, vector=point.vector, payload=updated_payload)])
    return {
        "id": task_id, "content": updated_payload.get("content", ""),
        "status": "complete", "createdDate": updated_payload.get("createdDate", ""),
        "completedDate": completed_date, "carriedOver": updated_payload.get("carriedOver", False),
    }


def edit_task(task_id: str, content: str, user_id: str) -> dict:
    result = _qdrant.retrieve(collection_name=COLLECTION_NAME, ids=[task_id],
                              with_payload=True, with_vectors=False)
    if not result:
        raise ValueError(f"Task {task_id} not found")
    point = result[0]
    if point.payload.get("user_id") != user_id:
        raise ValueError(f"Task {task_id} does not belong to this user")

    updated_payload = dict(point.payload)
    updated_payload["content"] = content
    _qdrant.upsert(collection_name=COLLECTION_NAME,
                   points=[PointStruct(id=task_id, vector=_embed(content), payload=updated_payload)])
    return {
        "id": task_id, "content": content,
        "status": updated_payload.get("status", "pending"),
        "createdDate": updated_payload.get("createdDate", ""),
        "completedDate": updated_payload.get("completedDate"),
        "carriedOver": updated_payload.get("carriedOver", False),
    }


def delete_task(task_id: str, user_id: str) -> bool:
    from qdrant_client.models import PointIdsList
    try:
        result = _qdrant.retrieve(collection_name=COLLECTION_NAME, ids=[task_id], with_payload=True)
        if not result or result[0].payload.get("user_id") != user_id:
            return False
        _qdrant.delete(collection_name=COLLECTION_NAME,
                       points_selector=PointIdsList(points=[task_id]))
        return True
    except Exception as e:
        logger.warning("Failed to delete task %s: %s", task_id, e)
        return False


def carry_forward(from_date: str, to_date: str, user_id: str) -> list[dict]:
    pending = get_pending_tasks(from_date, user_id)
    new_tasks = []
    for task in pending:
        vector = _embed(task["content"])
        new_id = str(uuid.uuid4())
        _qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=[PointStruct(
                id=new_id, vector=vector,
                payload={
                    "content": task["content"], "status": "pending",
                    "createdDate": to_date, "completedDate": None,
                    "carriedOver": True, "user_id": user_id,
                },
            )],
        )
        new_tasks.append({
            "id": new_id,
            "content": task["content"],
            "status": "pending",
            "createdDate": to_date,
            "completedDate": None,
            "carriedOver": True,
        })
    return new_tasks


def search_tasks(query: str, date: str, user_id: str) -> list[dict]:
    vector = _embed(query)
    results = _qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        query_filter=Filter(must=[
            FieldCondition(key="createdDate", match=MatchValue(value=date)),
            FieldCondition(key="status",      match=MatchValue(value="pending")),
            FieldCondition(key="user_id",     match=MatchValue(value=user_id)),
        ]),
        limit=5,
    )
    return [_row_to_task(r) for r in results]


def edit_task_status(task_id: str, new_status: str, user_id: str) -> dict:
    """Update a task's status field (used by inbox triage)."""
    result = _qdrant.retrieve(collection_name=COLLECTION_NAME, ids=[task_id],
                              with_payload=True, with_vectors=True)
    if not result:
        raise ValueError(f"Task {task_id} not found")
    point = result[0]
    if point.payload.get("user_id") != user_id:
        raise ValueError(f"Task {task_id} does not belong to this user")

    updated_payload = dict(point.payload)
    updated_payload["status"] = new_status
    _qdrant.upsert(collection_name=COLLECTION_NAME,
                   points=[PointStruct(id=task_id, vector=point.vector, payload=updated_payload)])
    return _row_to_task(type("P", (), {"id": task_id, "payload": updated_payload})())


def reschedule_task(task_id: str, new_date: str, user_id: str) -> dict:
    """Move a task to a different date."""
    result = _qdrant.retrieve(collection_name=COLLECTION_NAME, ids=[task_id],
                              with_payload=True, with_vectors=True)
    if not result:
        raise ValueError(f"Task {task_id} not found")
    point = result[0]
    if point.payload.get("user_id") != user_id:
        raise ValueError(f"Task {task_id} does not belong to this user")

    updated_payload = dict(point.payload)
    updated_payload["createdDate"] = new_date
    _qdrant.upsert(collection_name=COLLECTION_NAME,
                   points=[PointStruct(id=task_id, vector=point.vector, payload=updated_payload)])
    return _row_to_task(type("P", (), {"id": task_id, "payload": updated_payload})())


def _next_occurrence(from_date: str, recurrence: str) -> Optional[str]:
    """Return the next occurrence date string given a recurrence pattern."""
    try:
        d = date_type.fromisoformat(from_date)
    except ValueError:
        return None
    if recurrence == "daily":
        return (d + timedelta(days=1)).isoformat()
    elif recurrence == "weekly":
        return (d + timedelta(weeks=1)).isoformat()
    elif recurrence == "monthly":
        # Same day next month (clamped to last day if needed)
        month = d.month + 1 if d.month < 12 else 1
        year  = d.year if d.month < 12 else d.year + 1
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        day = min(d.day, last_day)
        return date_type(year, month, day).isoformat()
    elif recurrence == "weekdays":
        # Skip Sat/Sun
        nxt = d + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt.isoformat()
    return None


def spawn_recurring_tasks(target_date: Optional[str] = None) -> int:
    """Materialise next-day instances for all recurring tasks/reminders.

    Called by the daily 05:00 cron. Finds all recurring template tasks (those
    with isRecurring=True and no parentTaskId — i.e., the originating point),
    then checks whether an instance for *target_date* already exists.
    If not, it creates one.

    Returns the number of new instances spawned.
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date().isoformat()

    spawned = 0

    # Scroll all recurring template points (no parent = they ARE the template)
    offset = None
    templates = []
    while True:
        batch, offset = _qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[
                FieldCondition(key="isRecurring", match=MatchValue(value=True)),
            ]),
            limit=500, offset=offset, with_payload=True, with_vectors=False,
        )
        # Only keep templates (no parentTaskId) — children will have one set
        for p in batch:
            if not p.payload.get("parentTaskId"):
                templates.append(p)
        if offset is None:
            break

    for template in templates:
        payload = template.payload
        user_id = payload.get("user_id", "")
        recurrence = payload.get("recurrence")
        if not recurrence:
            continue

        # Respect recurrenceEndDate
        end_date = payload.get("recurrenceEndDate")
        if end_date and target_date > end_date:
            continue

        # Compute the expected source date for this target occurrence
        # We use the most recent occurrence (template itself OR its last child)
        # to compute the next one. For simplicity we compute from template
        # createdDate and advance by step until we reach target_date or pass it.
        source_date = payload.get("createdDate", target_date)
        next_date = _next_occurrence(source_date, recurrence)

        # Walk forward until we match target_date or overshoot
        while next_date and next_date < target_date:
            next_date = _next_occurrence(next_date, recurrence)

        if next_date != target_date:
            continue  # This template doesn't land on target_date

        # Check if an instance for this template+date already exists
        existing, _ = _qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[
                FieldCondition(key="parentTaskId", match=MatchValue(value=str(template.id))),
                FieldCondition(key="createdDate",  match=MatchValue(value=target_date)),
                FieldCondition(key="user_id",      match=MatchValue(value=user_id)),
            ]),
            limit=1, with_payload=False, with_vectors=False,
        )
        if existing:
            continue  # already spawned

        # Spawn the new instance
        content      = payload.get("content", "")
        task_type    = payload.get("taskType", "task")
        reminder_time = payload.get("reminderTime")
        vector = _embed(content)
        new_id = str(uuid.uuid4())
        new_payload = {
            "content": content, "status": "pending",
            "createdDate": target_date, "completedDate": None,
            "carriedOver": False, "user_id": user_id,
            "taskType": task_type,
            "reminderTime": reminder_time,
            "isRecurring": True,
            "recurrence": recurrence,
            "recurrenceEndDate": end_date,
            "parentTaskId": str(template.id),
        }
        _qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=[PointStruct(id=new_id, vector=vector, payload=new_payload)],
        )
        logger.info(
            "Spawned recurring %s '%s' for %s (parent=%s)",
            task_type, content[:40], target_date, template.id,
        )
        spawned += 1

    return spawned


def get_completed_tasks_last_n_days(days: int = 30, user_id: str = "") -> list[dict]:
    if not user_id:
        raise ValueError("get_completed_tasks_last_n_days requires a non-empty user_id")
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=days)).isoformat()

    all_points = []
    offset = None
    must = [
        FieldCondition(key="status", match=MatchValue(value="complete")),
        FieldCondition(key="user_id", match=MatchValue(value=user_id)),
    ]

    while True:
        batch, offset = _qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=must),
            limit=200, offset=offset, with_payload=True, with_vectors=False,
        )
        all_points.extend(batch)
        if offset is None:
            break

    tasks = [_row_to_task(p) for p in all_points if p.payload.get("createdDate", "") >= cutoff]
    tasks.sort(key=lambda t: t["createdDate"])
    return tasks
