import os
import uuid
import logging
from datetime import datetime, timezone, date as date_type
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
    }


def save_tasks(task_list: list[str], date: str) -> list[dict]:
    tasks = []
    for content in task_list:
        vector = _embed(content)
        task_id = str(uuid.uuid4())
        _qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=task_id,
                    vector=vector,
                    payload={
                        "content": content,
                        "status": "pending",
                        "createdDate": date,
                        "completedDate": None,
                        "carriedOver": False,
                    },
                )
            ],
        )
        tasks.append({
            "id": task_id,
            "content": content,
            "status": "pending",
            "createdDate": date,
            "completedDate": None,
            "carriedOver": False,
        })
    return tasks


def get_tasks(date: str) -> dict:
    results, _ = _qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="createdDate", match=MatchValue(value=date))]
        ),
        limit=200,
        with_payload=True,
        with_vectors=False,
    )

    pending = []
    completed = []
    for point in results:
        task = _row_to_task(point)
        if task["status"] == "complete":
            completed.append(task)
        else:
            pending.append(task)

    return {"pending": pending, "completed": completed, "date": date}


def get_pending_tasks(date: str) -> list[dict]:
    results, _ = _qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="createdDate", match=MatchValue(value=date)),
                FieldCondition(key="status", match=MatchValue(value="pending")),
            ]
        ),
        limit=200,
        with_payload=True,
        with_vectors=False,
    )
    return [_row_to_task(p) for p in results]


def complete_task(task_id: str) -> dict:
    result = _qdrant.retrieve(
        collection_name=COLLECTION_NAME,
        ids=[task_id],
        with_payload=True,
        with_vectors=True,
    )

    if not result:
        raise ValueError(f"Task {task_id} not found")

    point = result[0]
    completed_date = datetime.now(timezone.utc).date().isoformat()

    updated_payload = dict(point.payload)
    updated_payload["status"] = "complete"
    updated_payload["completedDate"] = completed_date

    _qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[
            PointStruct(
                id=task_id,
                vector=point.vector,
                payload=updated_payload,
            )
        ],
    )

    return {
        "id": task_id,
        "content": updated_payload.get("content", ""),
        "status": "complete",
        "createdDate": updated_payload.get("createdDate", ""),
        "completedDate": completed_date,
        "carriedOver": updated_payload.get("carriedOver", False),
    }


def edit_task(task_id: str, content: str) -> dict:
    """Update the content of an existing task."""
    result = _qdrant.retrieve(
        collection_name=COLLECTION_NAME,
        ids=[task_id],
        with_payload=True,
        with_vectors=False,
    )
    if not result:
        raise ValueError(f"Task {task_id} not found")

    point = result[0]
    new_vector = _embed(content)
    updated_payload = dict(point.payload)
    updated_payload["content"] = content

    _qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=task_id, vector=new_vector, payload=updated_payload)],
    )
    return {
        "id": task_id,
        "content": content,
        "status": updated_payload.get("status", "pending"),
        "createdDate": updated_payload.get("createdDate", ""),
        "completedDate": updated_payload.get("completedDate"),
        "carriedOver": updated_payload.get("carriedOver", False),
    }


def delete_task(task_id: str) -> bool:
    """Permanently delete a task from Qdrant."""
    from qdrant_client.models import PointIdsList
    try:
        _qdrant.delete(
            collection_name=COLLECTION_NAME,
            points_selector=PointIdsList(points=[task_id]),
        )
        return True
    except Exception as e:
        logger.warning("Failed to delete task %s: %s", task_id, e)
        return False


def carry_forward(from_date: str, to_date: str) -> list[dict]:
    pending = get_pending_tasks(from_date)
    new_tasks = []
    for task in pending:
        vector = _embed(task["content"])
        new_id = str(uuid.uuid4())
        _qdrant.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                PointStruct(
                    id=new_id,
                    vector=vector,
                    payload={
                        "content": task["content"],
                        "status": "pending",
                        "createdDate": to_date,
                        "completedDate": None,
                        "carriedOver": True,
                    },
                )
            ],
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


def search_tasks(query: str, date: str) -> list[dict]:
    vector = _embed(query)
    results = _qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        query_filter=Filter(
            must=[
                FieldCondition(key="createdDate", match=MatchValue(value=date)),
                FieldCondition(key="status", match=MatchValue(value="pending")),
            ]
        ),
        limit=5,
    )
    return [_row_to_task(r) for r in results]


def get_completed_tasks_last_n_days(days: int = 30) -> list[dict]:
    """Return all completed tasks from the last N days."""
    from datetime import timedelta
    today = datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=days)).isoformat()

    all_points = []
    offset = None
    while True:
        batch, offset = _qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(
                must=[FieldCondition(key="status", match=MatchValue(value="complete"))]
            ),
            limit=200,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        all_points.extend(batch)
        if offset is None:
            break

    tasks = []
    for point in all_points:
        created = point.payload.get("createdDate", "")
        if created >= cutoff:
            tasks.append(_row_to_task(point))

    tasks.sort(key=lambda t: t["createdDate"])
    return tasks
