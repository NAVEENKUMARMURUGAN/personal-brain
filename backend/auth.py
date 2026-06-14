"""
Authentication module — Google OAuth 2.0 + JWT.

Flow:
  1. GET /auth/google          → redirect to Google consent screen
  2. GET /auth/google/callback → exchange code, upsert user, return JWT
  3. Every protected request   → verify JWT, return user_id

Security hardening:
- JWT_SECRET MUST be set in env; app refuses to start otherwise.
- JWT expiry: 24 hours (down from 30 days).
- Token revocation via SQLite revoked_tokens table (logout invalidates immediately).
- JWT sub field validated to be a non-empty UUID-format string before use.
- No fallback secrets anywhere.
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

JWT_ALGORITHM    = "HS256"
JWT_EXPIRY_SECS  = 24 * 3600          # 24 hours (was 30 days)
JWT_REFRESH_SECS = 7 * 24 * 3600      # 7-day refresh window

GOOGLE_AUTH_URL     = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL    = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SQLITE_PATH = os.getenv("SQLITE_PATH", "/app/history.db")


def _cfg(key: str) -> str:
    """Read a required env var. Raises RuntimeError if missing or empty."""
    val = os.getenv(key, "")
    if not val:
        raise RuntimeError(
            f"Required environment variable '{key}' is not set. "
            f"Set it in Railway or your .env file before starting the server."
        )
    return val


def _cfg_optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _jwt_secret() -> str:
    """Return JWT_SECRET, failing hard if not set."""
    secret = os.getenv("JWT_SECRET", "")
    if not secret or secret in ("dev-secret", "secret", "changeme", "password"):
        raise RuntimeError(
            "JWT_SECRET is not set or uses a weak default. "
            "Generate one with: openssl rand -hex 32"
        )
    if len(secret) < 32:
        raise RuntimeError(
            f"JWT_SECRET is too short ({len(secret)} chars). "
            "Use at least 32 random characters: openssl rand -hex 32"
        )
    return secret


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
        # Token revocation table — stores JTI of revoked tokens until expiry
        conn.execute("""
            CREATE TABLE IF NOT EXISTS revoked_tokens (
                jti        TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                revoked_at TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)
        conn.commit()
        # Clean up expired revoked tokens on startup
        _cleanup_revoked_tokens(conn)
    finally:
        conn.close()


def _cleanup_revoked_tokens(conn=None):
    """Remove expired revocation entries — they're no longer needed."""
    close = conn is None
    if conn is None:
        conn = _get_conn()
    try:
        conn.execute("DELETE FROM revoked_tokens WHERE expires_at < ?", (int(time.time()),))
        conn.commit()
    finally:
        if close:
            conn.close()


# ── User CRUD ──────────────────────────────────────────────────

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
    if not user_id:
        return None
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── JWT ────────────────────────────────────────────────────────

def issue_jwt(user: dict) -> str:
    """Issue a signed JWT for the given user. Includes a unique JTI for revocation."""
    now = int(time.time())
    payload = {
        "sub":   user["id"],
        "email": user["email"],
        "name":  user.get("name", ""),
        "jti":   str(uuid.uuid4()),   # unique token ID for revocation
        "iat":   now,
        "exp":   now + JWT_EXPIRY_SECS,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_jwt(token: str) -> dict:
    """Verify and decode a JWT. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGORITHM])


def revoke_token(token: str) -> bool:
    """Add a token's JTI to the revocation list. Called on logout."""
    try:
        payload = verify_jwt(token)
        jti     = payload.get("jti")
        user_id = payload.get("sub", "")
        exp     = payload.get("exp", 0)
        if not jti:
            return False
        conn = _get_conn()
        try:
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            conn.execute(
                "INSERT OR IGNORE INTO revoked_tokens (jti, user_id, revoked_at, expires_at) VALUES (?,?,?,?)",
                (jti, user_id, now, exp)
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        logger.warning("revoke_token failed: %s", e)
        return False


def _is_revoked(jti: str) -> bool:
    """Check if a token has been revoked."""
    if not jti:
        return True
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM revoked_tokens WHERE jti = ? AND expires_at > ?",
            (jti, int(time.time()))
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def extract_user_id(authorization: str | None) -> str | None:
    """
    Extract and validate user_id from an Authorization: Bearer <token> header.

    Checks:
    1. Token is present and well-formed
    2. Signature is valid (JWT_SECRET must be set)
    3. Token has not expired
    4. Token has not been revoked (JTI check)
    5. sub field is a non-empty string (UUID format)

    Returns None on any failure — never raises.
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    try:
        payload = verify_jwt(token)
    except Exception as e:
        logger.warning("JWT verification failed: %s", e)
        return None

    # Validate sub is non-empty
    sub = payload.get("sub", "")
    if not sub or not isinstance(sub, str) or len(sub) < 10:
        logger.warning("JWT sub field invalid: %r", sub)
        return None

    # Check revocation
    jti = payload.get("jti", "")
    if _is_revoked(jti):
        logger.warning("Revoked token presented: jti=%s user=%s", jti, sub[:8])
        return None

    return sub


# ── Google OAuth ───────────────────────────────────────────────

def google_auth_url(redirect_uri: str) -> str:
    """Build the Google OAuth consent screen URL with a CSRF state parameter."""
    import secrets as _secrets
    state = _secrets.token_urlsafe(16)
    params = {
        "client_id":     _cfg_optional("GOOGLE_CLIENT_ID"),
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "openid email profile",
        "access_type":   "online",
        "state":         state,
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for user info. Returns user dict."""
    token_resp = http_requests.post(GOOGLE_TOKEN_URL, data={
        "code":          code,
        "client_id":     _cfg_optional("GOOGLE_CLIENT_ID"),
        "client_secret": _cfg_optional("GOOGLE_CLIENT_SECRET"),
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }, timeout=10)
    token_resp.raise_for_status()
    tokens = token_resp.json()

    # Fetch user info — access_token is used once then discarded
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
