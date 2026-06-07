import os
import re
import uuid
import logging
import requests
from datetime import datetime, timezone
from typing import Optional
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

logger = logging.getLogger(__name__)

QDRANT_URL     = os.getenv("QDRANT_URL", "http://localhost:6333")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBED_MODEL    = os.getenv("EMBED_MODEL", "text-embedding-3-small")
COLLECTION_NAME = "brain_memories"
VECTOR_SIZE = 1536  # text-embedding-3-small outputs 1536-dim vectors

# Chunking thresholds
CHUNK_THRESHOLD_CHARS = 500   # content longer than this gets chunked
CHUNK_SIZE_CHARS = 400        # target size of each chunk
CHUNK_OVERLAP_CHARS = 80      # overlap between consecutive chunks
DEDUP_SCORE_THRESHOLD = 0.97  # cosine score above which we consider content a duplicate

_qdrant = QdrantClient(url=QDRANT_URL)

CATEGORY_ICONS = {
    "Access & Permissions": "🔐",
    "Tools & Links": "🔗",
    "Meetings & Schedules": "📅",
    "Procedures": "📄",
    "People & Contacts": "👤",
    "Achievements": "🏆",
}
DEFAULT_ICON = "📌"


def ensure_collections():
    existing = [c.name for c in _qdrant.get_collections().collections]
    if COLLECTION_NAME not in existing:
        _qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )


def embed(text: str) -> list[float]:
    """Embed text using OpenAI text-embedding-3-small (1536-dim, $0.02/M tokens)."""
    response = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"input": text, "model": EMBED_MODEL},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def _split_into_chunks(text: str) -> list[str]:
    """Split text into overlapping chunks on paragraph/sentence boundaries."""
    # First try to split on double newlines (paragraphs)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) <= CHUNK_SIZE_CHARS:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            # Para itself might be too long — split on sentences
            if len(para) > CHUNK_SIZE_CHARS:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                sent_buf = ""
                for sent in sentences:
                    if len(sent_buf) + len(sent) <= CHUNK_SIZE_CHARS:
                        sent_buf = (sent_buf + " " + sent).strip()
                    else:
                        if sent_buf:
                            chunks.append(sent_buf)
                        sent_buf = sent
                if sent_buf:
                    # Start next chunk with overlap from end of previous
                    overlap = chunks[-1][-CHUNK_OVERLAP_CHARS:] if chunks else ""
                    current = (overlap + " " + sent_buf).strip() if overlap else sent_buf
                else:
                    current = ""
            else:
                # Start next chunk with overlap from end of previous
                overlap = chunks[-1][-CHUNK_OVERLAP_CHARS:] if chunks else ""
                current = (overlap + "\n\n" + para).strip() if overlap else para

    if current:
        chunks.append(current)

    return chunks if chunks else [text]


_QUERY_PATTERNS = re.compile(
    r"^(show|what|who|where|when|how|which|list|display|get|find|"
    r"add tasks?:|done with|finished|i completed|i'm done|i finished|"
    r"my pending|pending tasks|show tasks|show my)",
    re.IGNORECASE,
)

def looks_like_query(content: str) -> bool:
    """Return True if content looks like a user query rather than saveable knowledge."""
    stripped = content.strip()
    if _QUERY_PATTERNS.match(stripped):
        logger.warning("Blocked save of query-like content: %.80s", stripped)
        return True
    return False


def _user_filter(user_id: str) -> Filter:
    """Build a Qdrant filter that scopes queries to a single user."""
    return Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])


def _user_and_filter(user_id: str, extra: Filter | None = None) -> Filter:
    """Combine user_id filter with an optional additional filter."""
    conditions = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
    if extra and extra.must:
        conditions.extend(extra.must)
    return Filter(must=conditions)


def is_duplicate(content: str, user_id: str, threshold: float = DEDUP_SCORE_THRESHOLD) -> bool:
    """Return True if a near-identical memory already exists for this user."""
    try:
        vector = embed(content)
        results = _qdrant.search(
            collection_name=COLLECTION_NAME,
            query_vector=vector,
            query_filter=_user_filter(user_id),
            limit=1,
        )
        if results and results[0].score >= threshold:
            logger.info("Dedup hit: score=%.3f for content: %.80s", results[0].score, content)
            return True
    except Exception as e:
        logger.warning("Dedup check failed: %s", e)
    return False


def save_memory(content: str, category: str, user_id: str, source_id: Optional[str] = None, chunk_index: Optional[int] = None) -> dict:
    """Save a single memory point scoped to a user."""
    vector = embed(content)
    memory_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    payload = {
        "content":   content,
        "category":  category,
        "createdAt": created_at,
        "user_id":   user_id,
    }
    if source_id:
        payload["source_id"] = source_id
    if chunk_index is not None:
        payload["chunk_index"] = chunk_index

    _qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=memory_id, vector=vector, payload=payload)],
    )

    return {
        "id": memory_id,
        "content": content,
        "category": category,
        "createdAt": created_at,
        "score": None,
    }


def chunk_and_save(content: str, category: str, user_id: str) -> dict:
    """Split into chunks, dedup per-user, embed and store individually."""
    chunks = _split_into_chunks(content)
    source_id = str(uuid.uuid4())

    saved = []
    skipped = 0
    for i, chunk in enumerate(chunks):
        if is_duplicate(chunk, user_id):
            skipped += 1
            continue
        memory = save_memory(chunk, category, user_id, source_id=source_id, chunk_index=i)
        saved.append(memory)

    logger.info("chunk_and_save: %d chunks, %d saved, %d skipped", len(chunks), len(saved), skipped)
    return {
        "saved_count":   len(saved),
        "skipped_count": skipped,
        "total_chunks":  len(chunks),
        "source_id":     source_id,
        "category":      category,
        "memories":      saved,
    }


def search_memories(query: str, user_id: str, limit: int = 5) -> list[dict]:
    """Search memories scoped to a user."""
    vector = embed(query)
    results = _qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        query_filter=_user_filter(user_id),
        limit=limit,
    )
    return [{
        "id":        str(r.id),
        "content":   r.payload.get("content", ""),
        "category":  r.payload.get("category", ""),
        "createdAt": r.payload.get("createdAt", ""),
        "score":     r.score,
    } for r in results]


def delete_memory(point_id: str, user_id: str) -> bool:
    """Delete a memory point — only if it belongs to this user."""
    from qdrant_client.models import PointIdsList
    try:
        # Verify ownership before deleting
        result = _qdrant.retrieve(collection_name=COLLECTION_NAME, ids=[point_id], with_payload=True)
        if not result or result[0].payload.get("user_id") != user_id:
            logger.warning("Delete denied: point %s does not belong to user %s", point_id, user_id)
            return False
        _qdrant.delete(
            collection_name=COLLECTION_NAME,
            points_selector=PointIdsList(points=[point_id]),
        )
        return True
    except Exception as e:
        logger.error("Failed to delete memory %s: %s", point_id, e)
        return False


def delete_memories_by_source(source_id: str, user_id: str) -> int:
    """Delete all chunks of an uploaded document belonging to this user."""
    from qdrant_client.models import FilterSelector
    try:
        _qdrant.delete(
            collection_name=COLLECTION_NAME,
            points_selector=FilterSelector(
                filter=Filter(must=[
                    FieldCondition(key="source_id", match=MatchValue(value=source_id)),
                    FieldCondition(key="user_id",   match=MatchValue(value=user_id)),
                ])
            ),
        )
        return 1
    except Exception as e:
        logger.error("Failed to delete source %s: %s", source_id, e)
        return 0


def get_categories(user_id: str) -> list[dict]:
    """Return knowledge categories for a specific user."""
    all_points = []
    offset = None
    while True:
        batch, offset = _qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=_user_filter(user_id),
            limit=100,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        all_points.extend(batch)
        if offset is None:
            break

    counts: dict[str, int] = {}
    for point in all_points:
        cat = point.payload.get("category", "Uncategorized")
        counts[cat] = counts.get(cat, 0) + 1

    return [
        {"name": name, "icon": CATEGORY_ICONS.get(name, DEFAULT_ICON), "count": count}
        for name, count in sorted(counts.items(), key=lambda x: -x[1])
    ]


def get_memories_by_category(category: str, user_id: str, limit: int = 20, cursor: Optional[str] = None) -> dict:
    """Return paginated memories for a specific category and user."""
    offset = int(cursor) if cursor else 0

    results, _ = _qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(must=[
            FieldCondition(key="category", match=MatchValue(value=category)),
            FieldCondition(key="user_id",  match=MatchValue(value=user_id)),
        ]),
        limit=limit + 1,
        offset=offset,
        with_payload=True,
        with_vectors=False,
    )

    has_next = len(results) > limit
    memories = [{
        "id":        str(p.id),
        "content":   p.payload.get("content", ""),
        "category":  p.payload.get("category", ""),
        "createdAt": p.payload.get("createdAt", ""),
        "score":     None,
    } for p in results[:limit]]

    return {
        "memories": memories,
        "pageInfo": {
            "hasNextPage": has_next,
            "cursor":      str(offset + limit) if has_next else None,
        }
    }
