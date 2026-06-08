import os
import json
import asyncio
import tempfile
import logging
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, UploadFile, File, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from typing import Optional

import brain
import tasks as tasks_module
import history
import graphql_handler
import auth
import telegram_bot
from context_manager import ContextManager

logger = logging.getLogger(__name__)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazy-loaded Whisper model
_whisper_model = None

def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        logging.getLogger("faster_whisper").setLevel(logging.WARNING)
        _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _whisper_model


# ── Text extraction helpers ────────────────────────────────────

def _extract_pdf(path: str) -> str:
    import fitz  # pymupdf
    doc = fitz.open(path)
    pages = [page.get_text() for page in doc]
    doc.close()
    return "\n\n".join(p.strip() for p in pages if p.strip())


def _extract_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    return "\n\n".join(p.text.strip() for p in doc.paragraphs if p.text.strip())


def _extract_xlsx(path: str) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    sections = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        rows = []
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                rows.append("\t".join(cells))
        if rows:
            sections.append(f"[Sheet: {sheet}]\n" + "\n".join(rows))
    wb.close()
    return "\n\n".join(sections)


def _extract_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


EXTRACTORS = {
    ".pdf":  _extract_pdf,
    ".docx": _extract_docx,
    ".doc":  _extract_docx,
    ".xlsx": _extract_xlsx,
    ".xls":  _extract_xlsx,
    ".txt":  _extract_txt,
    ".md":   _extract_txt,
    ".csv":  _extract_txt,
}


# ── Startup ────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    import time
    for attempt in range(10):
        try:
            brain.ensure_collections()
            tasks_module.ensure_collections()
            logger.info("Qdrant collections ready")
            break
        except Exception as e:
            if attempt < 9:
                logger.warning("Qdrant not ready (%d/10): %s — retrying in 3s", attempt + 1, e)
                time.sleep(3)
            else:
                logger.error("Qdrant unreachable after 10 attempts: %s", e)
    history.ensure_db()
    auth.ensure_users_table()
    telegram_bot.ensure_telegram_table()
    telegram_bot.start_scheduler()
    if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("BACKEND_URL"):
        await telegram_bot.set_webhook(BACKEND_URL)


# ── Routes ─────────────────────────────────────────────────────

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/debug/env")
async def debug_env():
    import os
    redirect_uri = f"{os.getenv('BACKEND_URL', '')}/auth/google/callback"
    oauth_url = auth.google_auth_url(redirect_uri)
    return {
        "GOOGLE_CLIENT_ID_set":     bool(os.getenv("GOOGLE_CLIENT_ID")),
        "GOOGLE_CLIENT_SECRET_set": bool(os.getenv("GOOGLE_CLIENT_SECRET")),
        "JWT_SECRET_set":           bool(os.getenv("JWT_SECRET")),
        "BACKEND_URL":              os.getenv("BACKEND_URL", "NOT SET"),
        "FRONTEND_URL":             os.getenv("FRONTEND_URL", "NOT SET"),
        "oauth_url_preview":        oauth_url[:120],
    }


# ── Auth routes ────────────────────────────────────────────────

@app.get("/auth/google")
async def google_login():
    """Redirect browser to Google OAuth consent screen."""
    redirect_uri = f"{BACKEND_URL}/auth/google/callback"
    url = auth.google_auth_url(redirect_uri)
    return RedirectResponse(url)


@app.get("/auth/google/callback")
async def google_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Handle Google OAuth callback, issue JWT, redirect to frontend."""
    logger.info("OAuth callback — error=%s code_present=%s params=%s", error, bool(code), dict(request.query_params))
    if error:
        logger.error("Google returned error: %s", error)
        return RedirectResponse(f"{FRONTEND_URL}?error={error}")
    if not code:
        logger.error("No code in callback. Params: %s", dict(request.query_params))
        return RedirectResponse(f"{FRONTEND_URL}?error=no_code")
    try:
        redirect_uri = f"{BACKEND_URL}/auth/google/callback"
        logger.info("Exchanging code with redirect_uri=%s", redirect_uri)
        info  = auth.exchange_code(code, redirect_uri)
        logger.info("Got user info: email=%s", info["email"])
        user  = auth.upsert_user(info["google_id"], info["email"], info["name"], info["avatar_url"])
        token = auth.issue_jwt(user)
        logger.info("Issuing JWT for user=%s, redirecting to frontend", user["id"])
        return RedirectResponse(f"{FRONTEND_URL}?token={token}")
    except Exception as e:
        logger.error("OAuth callback error: %s", e, exc_info=True)
        return RedirectResponse(f"{FRONTEND_URL}?error=auth_failed")


@app.get("/auth/me")
async def get_me(authorization: Optional[str] = Header(None)):
    """Return the current user from JWT."""
    user_id = auth.extract_user_id(authorization)
    if not user_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    user = auth.get_user_by_id(user_id)
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    return JSONResponse({"id": user["id"], "email": user["email"],
                         "name": user["name"], "avatar_url": user["avatar_url"]})


# ── GraphQL ────────────────────────────────────────────────────

@app.post("/graphql")
async def graphql_post(request: Request):
    return await graphql_handler.handle(request)


@app.get("/graphql")
async def graphql_schema():
    return PlainTextResponse(graphql_handler.SCHEMA_SDL, media_type="text/plain")


@app.get("/context-debug")
async def context_debug(message: str = "test", authorization: Optional[str] = Header(None)):
    user_id = auth.extract_user_id(authorization) or "debug"
    ctx = await ContextManager.build(current_message=message, user_id=user_id)
    return PlainTextResponse(ctx.debug(), media_type="text/plain")


@app.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    authorization: Optional[str] = Header(None),
):
    """Transcribe audio using faster-whisper. Requires auth."""
    if not auth.extract_user_id(authorization):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        suffix = os.path.splitext(audio.filename or "audio.webm")[1] or ".webm"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await audio.read())
            tmp_path = tmp.name

        model = _get_whisper()
        segments, _ = model.transcribe(tmp_path, beam_size=5, language="en")
        text = " ".join(seg.text.strip() for seg in segments).strip()
        os.unlink(tmp_path)

        if not text:
            return JSONResponse({"error": "No speech detected"}, status_code=400)
        return JSONResponse({"text": text})

    except Exception as e:
        logger.error("Transcription error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Telegram webhook ───────────────────────────────────────────

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """Receive updates from Telegram and process them."""
    body = await request.body()
    logger.info("Telegram webhook hit — body: %s", body[:500].decode("utf-8", errors="replace"))
    try:
        update = json.loads(body)
        asyncio.create_task(telegram_bot._handle_update_safe(update))
    except Exception as e:
        logger.error("Telegram webhook parse error: %s", e)
    return JSONResponse({"ok": True})


@app.get("/telegram/debug")
async def telegram_debug():
    """Debug endpoint — shows token status, allowed IDs, and webhook info."""
    allowed_raw = os.getenv("TELEGRAM_ALLOWED_IDS", "NOT SET")
    token_set   = bool(os.getenv("TELEGRAM_BOT_TOKEN"))
    info        = await telegram_bot._tg_post("getWebhookInfo", {})
    return JSONResponse({
        "token_set":       token_set,
        "allowed_ids_raw": repr(allowed_raw),
        "webhook_info":    info.get("result", {}),
    })


@app.delete("/memory/{point_id}")
async def delete_memory(point_id: str, authorization: Optional[str] = Header(None)):
    """Delete a single memory point scoped to the authenticated user."""
    user_id = auth.extract_user_id(authorization)
    if not user_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    ok = brain.delete_memory(point_id, user_id)
    if ok:
        return JSONResponse({"deleted": point_id})
    return JSONResponse({"error": f"Failed to delete {point_id}"}, status_code=500)


@app.delete("/memory/source/{source_id}")
async def delete_memory_source(source_id: str, authorization: Optional[str] = Header(None)):
    user_id = auth.extract_user_id(authorization)
    if not user_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    brain.delete_memories_by_source(source_id, user_id)
    return JSONResponse({"deleted_source": source_id})


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    category: str = Form("General"),
    authorization: Optional[str] = Header(None),
):
    """Accept a file, extract text, chunk and save scoped to the authenticated user."""
    user_id = auth.extract_user_id(authorization)
    if not user_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    filename = file.filename or "upload"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in EXTRACTORS:
        return JSONResponse(
            {"error": f"Unsupported file type '{ext}'. Supported: {', '.join(EXTRACTORS)}"},
            status_code=400,
        )

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        text = EXTRACTORS[ext](tmp_path)
        os.unlink(tmp_path)

        if not text.strip():
            return JSONResponse({"error": "No text could be extracted from the file."}, status_code=400)

        logger.info("Extracted %d chars from %s → category=%s user=%s", len(text), filename, category, user_id[:8])

        result = brain.chunk_and_save(text, category, user_id)

        return JSONResponse({
            "filename":    filename,
            "category":    category,
            "saved":       result["saved_count"],
            "skipped":     result["skipped_count"],
            "total_chars": len(text),
        })

    except Exception as e:
        logger.error("Upload error for %s: %s", filename, e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)
