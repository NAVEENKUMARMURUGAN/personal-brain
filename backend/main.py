import os
import tempfile
import logging
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

import brain
import tasks as tasks_module
import history
import graphql_handler
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
    # Retry Qdrant connection — on Railway, Qdrant may take a moment to be ready
    import time
    for attempt in range(10):
        try:
            brain.ensure_collections()
            tasks_module.ensure_collections()
            logger.info("Qdrant collections ready")
            break
        except Exception as e:
            if attempt < 9:
                logger.warning("Qdrant not ready (attempt %d/10): %s — retrying in 3s", attempt + 1, e)
                time.sleep(3)
            else:
                logger.error("Qdrant unreachable after 10 attempts: %s", e)
    history.ensure_db()


# ── Routes ─────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/graphql")
async def graphql_post(request: Request):
    return await graphql_handler.handle(request)


@app.get("/graphql")
async def graphql_schema():
    return PlainTextResponse(graphql_handler.SCHEMA_SDL, media_type="text/plain")


@app.get("/context-debug")
async def context_debug(message: str = "test"):
    ctx = await ContextManager.build(current_message=message)
    return PlainTextResponse(ctx.debug(), media_type="text/plain")


@app.post("/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    """Transcribe audio using faster-whisper (tiny model, CPU)."""
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


@app.delete("/memory/{point_id}")
async def delete_memory(point_id: str):
    """
    Delete a single memory point from Qdrant by its UUID.
    Used by the Knowledge Explorer delete button.
    """
    ok = brain.delete_memory(point_id)
    if ok:
        return JSONResponse({"deleted": point_id})
    return JSONResponse({"error": f"Failed to delete {point_id}"}, status_code=500)


@app.delete("/memory/source/{source_id}")
async def delete_memory_source(source_id: str):
    """
    Delete all chunks that share a source_id (e.g. all chunks of an uploaded PDF).
    """
    brain.delete_memories_by_source(source_id)
    return JSONResponse({"deleted_source": source_id})


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    category: str = Form("General"),
):
    """
    Accept a file (PDF, DOCX, XLSX, TXT, MD, CSV), extract its text,
    and run it through the chunk_and_save pipeline.

    Returns: { saved: int, skipped: int, category: str, filename: str }
    """
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

        logger.info("Extracted %d chars from %s → category=%s", len(text), filename, category)

        result = brain.chunk_and_save(text, category)

        return JSONResponse({
            "filename": filename,
            "category": category,
            "saved": result["saved_count"],
            "skipped": result["skipped_count"],
            "total_chars": len(text),
        })

    except Exception as e:
        logger.error("Upload error for %s: %s", filename, e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)
