"""
vault.py — Encrypted personal vault for sensitive credentials.

Design principles:
- AES-256-GCM encryption: every value is encrypted at rest before touching Qdrant or SQLite.
- The encryption key is derived from VAULT_SECRET env var via PBKDF2-HMAC-SHA256.
- Vault items live in a SEPARATE Qdrant collection ('brain_vault') — never mixed with
  regular memories and never returned by general search_memories().
- Embeddings are computed on the LABEL only (e.g. "Netflix password"), never the secret
  value — so semantic search works without exposing the secret to the embedding API.
- Decryption happens only in this module, only when the caller explicitly requests it.
- If VAULT_SECRET is not set, vault operations raise an error immediately.

Usage:
    vault.ensure_collection()          # called from main.py startup
    vault.save_item(user_id, label, secret, category)
    results = vault.search_items(user_id, query)   # returns decrypted items
    vault.delete_item(user_id, item_id)
"""

import os
import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import requests
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue,
)

logger = logging.getLogger(__name__)

QDRANT_URL      = os.getenv("QDRANT_URL", "http://localhost:6333")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
EMBED_MODEL     = os.getenv("EMBED_MODEL", "text-embedding-3-small")
VAULT_SECRET    = os.getenv("VAULT_SECRET", "")
COLLECTION_NAME = "brain_vault"
VECTOR_SIZE     = 1536

_qdrant = QdrantClient(url=QDRANT_URL)


# ── Encryption helpers ─────────────────────────────────────────────────────────

def _derive_key(vault_secret: str) -> bytes:
    """Derive a 32-byte AES key from the VAULT_SECRET using PBKDF2-HMAC-SHA256.
    Salt is deterministic so we don't need to store it — the secret itself provides entropy."""
    salt = hashlib.sha256(b"personal-brain-vault-v1").digest()
    return hashlib.pbkdf2_hmac("sha256", vault_secret.encode(), salt, iterations=200_000)


def _encrypt(plaintext: str, key: bytes) -> str:
    """AES-256-GCM encrypt. Returns base64-encoded 'nonce:ciphertext:tag'."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import secrets as _secrets
        nonce = _secrets.token_bytes(12)  # 96-bit nonce for GCM
        aesgcm = AESGCM(key)
        ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        # ct includes the 16-byte authentication tag appended by cryptography lib
        payload = base64.b64encode(nonce).decode() + ":" + base64.b64encode(ct).decode()
        return payload
    except ImportError:
        raise RuntimeError("cryptography package not installed. Add it to requirements.txt.")


def _decrypt(ciphertext_b64: str, key: bytes) -> str:
    """AES-256-GCM decrypt. Raises ValueError on tampered/invalid ciphertext."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce_b64, ct_b64 = ciphertext_b64.split(":", 1)
        nonce = base64.b64decode(nonce_b64)
        ct    = base64.b64decode(ct_b64)
        aesgcm = AESGCM(key)
        return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
    except ImportError:
        raise RuntimeError("cryptography package not installed.")
    except Exception as e:
        raise ValueError(f"Vault decryption failed: {e}")


def _get_key() -> bytes:
    """Return the encryption key, raising clearly if VAULT_SECRET is not set."""
    if not VAULT_SECRET:
        raise RuntimeError(
            "VAULT_SECRET environment variable is not set. "
            "Set it to a long random string to enable the vault. "
            "Example: openssl rand -hex 32"
        )
    return _derive_key(VAULT_SECRET)


# ── Qdrant collection ──────────────────────────────────────────────────────────

def ensure_collection() -> None:
    """Create the brain_vault collection if it doesn't exist."""
    existing = [c.name for c in _qdrant.get_collections().collections]
    if COLLECTION_NAME not in existing:
        _qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info("Vault collection created: %s", COLLECTION_NAME)


# ── Embedding (label only, never the secret) ───────────────────────────────────

def _embed_label(label: str) -> list[float]:
    """Embed the label text using OpenAI. The secret value is NEVER sent to OpenAI."""
    response = requests.post(
        "https://api.openai.com/v1/embeddings",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": EMBED_MODEL, "input": label},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


# ── Public CRUD ────────────────────────────────────────────────────────────────

def save_item(
    user_id: str,
    label: str,
    secret: str,
    category: str = "General",
    notes: str = "",
) -> dict:
    """Encrypt and store a vault item. Returns the saved item metadata (no secret)."""
    key = _get_key()
    encrypted_secret = _encrypt(secret, key)
    encrypted_notes  = _encrypt(notes, key) if notes else ""

    item_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    vector = _embed_label(f"{category}: {label}")

    payload = {
        "user_id":          user_id,
        "label":            label,           # label stored in plain text (it's not a secret)
        "encrypted_secret": encrypted_secret,
        "encrypted_notes":  encrypted_notes,
        "category":         category,
        "created_at":       now,
    }

    _qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=item_id, vector=vector, payload=payload)],
    )
    logger.info("Vault item saved: user=%s label=%r category=%s", user_id[:8], label, category)
    return {"id": item_id, "label": label, "category": category, "created_at": now}


def search_items(user_id: str, query: str, limit: int = 5) -> list[dict]:
    """Semantic search over vault labels, then decrypt and return matching items."""
    key = _get_key()
    vector = _embed_label(query)

    results = _qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=vector,
        query_filter=Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        ),
        limit=limit,
        with_payload=True,
        score_threshold=0.3,
    )

    items = []
    for hit in results:
        p = hit.payload
        try:
            secret = _decrypt(p["encrypted_secret"], key)
            notes  = _decrypt(p["encrypted_notes"], key) if p.get("encrypted_notes") else ""
        except ValueError:
            logger.error("Vault decrypt failed for item %s — skipping", hit.id)
            continue
        items.append({
            "id":         str(hit.id),
            "label":      p["label"],
            "secret":     secret,
            "notes":      notes,
            "category":   p.get("category", "General"),
            "created_at": p.get("created_at", ""),
            "score":      round(hit.score, 3),
        })
    return items


def list_items(user_id: str, limit: int = 50) -> list[dict]:
    """List all vault items for a user (labels only, no secrets)."""
    results, _ = _qdrant.scroll(
        collection_name=COLLECTION_NAME,
        scroll_filter=Filter(
            must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        ),
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    items = []
    for point in results:
        p = point.payload
        items.append({
            "id":         str(point.id),
            "label":      p["label"],
            "category":   p.get("category", "General"),
            "created_at": p.get("created_at", ""),
        })
    # Sort newest first
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items


def get_item(user_id: str, item_id: str) -> Optional[dict]:
    """Retrieve and decrypt a single vault item by ID."""
    key = _get_key()
    results = _qdrant.retrieve(
        collection_name=COLLECTION_NAME,
        ids=[item_id],
        with_payload=True,
        with_vectors=False,
    )
    if not results:
        return None
    point = results[0]
    p = point.payload
    if p.get("user_id") != user_id:
        return None  # not this user's item
    try:
        secret = _decrypt(p["encrypted_secret"], key)
        notes  = _decrypt(p["encrypted_notes"], key) if p.get("encrypted_notes") else ""
    except ValueError:
        return None
    return {
        "id":         str(point.id),
        "label":      p["label"],
        "secret":     secret,
        "notes":      notes,
        "category":   p.get("category", "General"),
        "created_at": p.get("created_at", ""),
    }


def delete_item(user_id: str, item_id: str) -> bool:
    """Delete a vault item. Returns True if deleted, False if not found or not owner."""
    results = _qdrant.retrieve(
        collection_name=COLLECTION_NAME,
        ids=[item_id],
        with_payload=True,
        with_vectors=False,
    )
    if not results or results[0].payload.get("user_id") != user_id:
        return False
    _qdrant.delete(
        collection_name=COLLECTION_NAME,
        points_selector=[item_id],
    )
    logger.info("Vault item deleted: user=%s id=%s", user_id[:8], item_id)
    return True


def update_item(
    user_id: str,
    item_id: str,
    label: Optional[str] = None,
    secret: Optional[str] = None,
    notes: Optional[str] = None,
    category: Optional[str] = None,
) -> Optional[dict]:
    """Update label, secret, notes, or category of an existing vault item."""
    existing = get_item(user_id, item_id)
    if not existing:
        return None

    key = _get_key()
    new_label    = label    or existing["label"]
    new_secret   = secret   or existing["secret"]
    new_notes    = notes    if notes is not None else existing["notes"]
    new_category = category or existing["category"]

    encrypted_secret = _encrypt(new_secret, key)
    encrypted_notes  = _encrypt(new_notes, key) if new_notes else ""

    vector = _embed_label(f"{new_category}: {new_label}")
    now = datetime.now(timezone.utc).isoformat()

    payload = {
        "user_id":          user_id,
        "label":            new_label,
        "encrypted_secret": encrypted_secret,
        "encrypted_notes":  encrypted_notes,
        "category":         new_category,
        "created_at":       existing["created_at"],
        "updated_at":       now,
    }

    _qdrant.upsert(
        collection_name=COLLECTION_NAME,
        points=[PointStruct(id=item_id, vector=vector, payload=payload)],
    )
    return {"id": item_id, "label": new_label, "category": new_category, "updated_at": now}
