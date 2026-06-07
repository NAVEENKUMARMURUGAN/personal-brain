"""
Authentication module — Google OAuth 2.0 + JWT.

Flow:
  1. GET /auth/google          → redirect to Google consent screen
  2. GET /auth/google/callback → exchange code, upsert user, return JWT
  3. Every protected request   → verify JWT, return user_id

Users are stored in SQLite alongside chat history.
"""

import os
import time
import uuid
import sqlite3
import logging
import urllib.parse
import requests as http_requests

import jwt  # PyJWT

logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
JWT_SECRET           = os.getenv("JWT_SECRET", "change-me-in-production-use-a-random-32-char-string")
JWT_ALGORITHM        = "HS256"
JWT_EXPIRY_DAYS      = 30

FRONTEND_URL         = os.getenv("FRONTEND_URL", "http://localhost:3000")

GOOGLE_AUTH_URL      = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL     = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL  = "https://www.googleapis.com/oauth2/v3/userinfo"

SQLITE_PATH          = os.getenv("SQLITE_PATH", "/app/history.db")

# ── DB helpers ─────────────────────────────────────────────────

def _get_conn():
    import os as _os
    _os.makedirs(_os.path.dirname(_os.path.abspath(SQLITE_PATH)), exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_users_table():
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          TEXT PRIMARY KEY,
                google_id   TEXT UNIQUE NOT NULL,
                email       TEXT UNIQUE NOT NULL,
                name        TEXT,
                avatar_url  TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


def upsert_user(google_id: str, email: str, name: str, avatar_url: str) -> dict:
    """Create user if not exists, otherwise update name/avatar. Returns user dict."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE google_id = ?", (google_id,)).fetchone()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if row:
            conn.execute(
                "UPDATE users SET name = ?, avatar_url = ? WHERE google_id = ?",
                (name, avatar_url, google_id)
            )
            conn.commit()
            return {"id": row["id"], "email": email, "name": name, "avatar_url": avatar_url}
        else:
            user_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO users (id, google_id, email, name, avatar_url, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, google_id, email, name, avatar_url, now)
            )
            conn.commit()
            return {"id": user_id, "email": email, "name": name, "avatar_url": avatar_url}
    finally:
        conn.close()


def get_user_by_id(user_id: str) -> dict | None:
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()

# ── JWT ────────────────────────────────────────────────────────

def issue_jwt(user: dict) -> str:
    """Issue a signed JWT for the given user."""
    payload = {
        "sub":   user["id"],
        "email": user["email"],
        "name":  user.get("name", ""),
        "iat":   int(time.time()),
        "exp":   int(time.time()) + JWT_EXPIRY_DAYS * 86400,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> dict:
    """
    Verify and decode a JWT. Returns the payload dict.
    Raises jwt.ExpiredSignatureError or jwt.InvalidTokenError on failure.
    """
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def extract_user_id(authorization: str | None) -> str | None:
    """
    Extract user_id from an Authorization: Bearer <token> header.
    Returns None if missing or invalid.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    try:
        payload = verify_jwt(token)
        return payload["sub"]
    except Exception as e:
        logger.warning("JWT verification failed: %s", e)
        return None

# ── Google OAuth ───────────────────────────────────────────────

def google_auth_url(redirect_uri: str) -> str:
    """Build the Google OAuth consent screen URL."""
    params = {
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "openid email profile",
        "access_type":   "online",
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for user info. Returns user dict."""
    # Step 1: exchange code for tokens
    token_resp = http_requests.post(GOOGLE_TOKEN_URL, data={
        "code":          code,
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }, timeout=10)
    token_resp.raise_for_status()
    tokens = token_resp.json()

    # Step 2: fetch user info
    userinfo_resp = http_requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        timeout=10,
    )
    userinfo_resp.raise_for_status()
    info = userinfo_resp.json()

    return {
        "google_id":  info["sub"],
        "email":      info["email"],
        "name":       info.get("name", ""),
        "avatar_url": info.get("picture", ""),
    }
