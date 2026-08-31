"""Generate a self-contained api/extract.py from api/_core/* modules.

Why: Vercel's Python runtime ships ONLY the entrypoint file into the sandbox;
local imports (api/_core) are not bundled reliably. A single flat file removes
the entire class of 'No module named _core' failures.

Usage:  python3 api/build_bundle.py      (run from repo root or anywhere)
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(HERE, "_core")

DROP_IMPORTS = (
    "from .parser_banks import", "from . import parser_banks",
    "from . import xlsx", "from .dispatcher import", "from . import pipeline",
    "from . import supabase_rest", "from _core import", "from parse_statement import",
)


def load(name: str) -> str:
    src = open(os.path.join(CORE, name), encoding="utf-8").read()
    lines = []
    for ln in src.splitlines():
        if any(ln.strip().startswith(d) for d in DROP_IMPORTS):
            continue
        lines.append(ln)
    return "\n".join(lines).strip()


def guard_heavy(src: str) -> str:
    """Replace hard pymupdf/openpyxl imports with guarded ones (JSON diagnostics)."""
    src = src.replace("import pymupdf", (
        "try:\n    import pymupdf\nexcept Exception as _e:  # guarded: reported via /api/extract health\n"
        "    pymupdf = None\n    _IMPORT_ERRS.append(f\"pymupdf: {_e}\")"))
    src = src.replace("from openpyxl import Workbook", (
        "try:\n    from openpyxl import Workbook\nexcept Exception as _e:\n"
        "    Workbook = None\n    _IMPORT_ERRS.append(f\"openpyxl: {_e}\")"))
    return src


APP_TEMPLATE = '''

# ============================================================
# serverless handler (FastAPI)
# ============================================================
import fastapi  # noqa: E402
from fastapi import FastAPI, File, Header, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

app = FastAPI(title="Statement Intel - extract")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _sb():
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY"))
    if not url or not key:
        raise HTTPException(500, "Server misconfigured: SUPABASE_URL / service key env vars missing. "
                                 "Check the Vercel <-> Supabase integration or add them manually.")
    return SB(url, key)


def _auth(sb, authorization):
    if os.environ.get("DEV_BYPASS_AUTH") == "1":
        return "dev-user", "dev-org"
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token - sign in first")
    user = sb.get_user(authorization.split(" ", 1)[1].strip())
    if not user:
        raise HTTPException(401, "Invalid or expired session")
    profs = sb.select("profiles", f"select=org_id&id=eq.{user['id']}&limit=1")
    if not profs:
        raise HTTPException(403, "No profile for user")
    return user["id"], profs[0]["org_id"]


@app.get("/")
@app.get("/api/extract")
async def health():
    return {
        "ok": not _IMPORT_ERRS,
        "service": "statement-intel extract",
        "python": sys.version.split()[0],
        "imports": "OK" if not _IMPORT_ERRS else "BROKEN (see detail)",
        "detail": _IMPORT_ERRS[0] if _IMPORT_ERRS else None,
        "env_present": {k: bool(os.environ.get(k)) for k in (
            "SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY", "NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY")},
    }


@app.post("/")
@app.post("/api/extract")
async def extract(file: UploadFile = File(None), authorization: str = Header(None)):
    if _IMPORT_ERRS:
        return JSONResponse(status_code=500, content={
            "detail": "Extractor libraries failed to load on the server",
            "errors": _IMPORT_ERRS})
    if file is None or not file.filename:
        raise HTTPException(400, "No file uploaded (field name must be 'file')")

    sb = _sb()
    user_id, org_id = _auth(sb, authorization)

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 20 MB)")
    if data[:5] != b"%PDF-":
        raise HTTPException(400, "Not a PDF file")

    try:
        extracted = extract_any(data, file.filename)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception:
        return JSONResponse(status_code=500, content={
            "detail": "Parser crashed while reading this PDF",
            "error": traceback.format_exc(limit=6).splitlines()[-6:]})

    try:
        result = persist(sb, org_id, user_id, data, file.filename, extracted)
    except Exception:
        return JSONResponse(status_code=500, content={
            "detail": "Parsed OK but saving to the database failed",
            "error": traceback.format_exc(limit=6).splitlines()[-6:]})
    if result.get("status") == "DUPLICATE":
        return JSONResponse(status_code=409, content=result)
    return result
'''


def main():
    errs = ["_IMPORT_ERRS: list[str] = []"]
    sections = [
        ("supabase client", "supabase_rest.py"),
        ("xlsx builder", "xlsx.py"),
        ("multi-bank parsers", "parser_banks.py"),
        ("firstbank parser", "parser_fbn.py"),
        ("format dispatcher", "dispatcher.py"),
        ("persistence pipeline", "pipeline.py"),
    ]
    parts = [
        '"""AUTO-GENERATED single-file serverless extractor. DO NOT EDIT BY HAND.',
        "Regenerate with:  python3 api/build_bundle.py",
        'Sources: api/_core/*.py"""',
        "import hashlib", "import io", "import json", "import os", "import re",
        "import sys", "import tempfile", "import traceback",
        "from datetime import datetime", "",
] + errs
    for title, fname in sections:
        body = guard_heavy(load(fname))
        # de-qualify cross-module references (everything is top-level in the bundle)
        body = (body.replace("xlsx.build_xlsx", "build_xlsx")
                    .replace("parser_fbn.extract_fbn", "extract_fbn")
                    .replace("parser_banks.process", "process"))
        parts.append(f"\n\n# ============================ {title} " + "=" * max(0, 40 - len(title)))
        parts.append(body)
    parts.append(APP_TEMPLATE)
    out = "\n".join(parts) + "\n"
    dst = os.path.join(HERE, "extract.py")
    open(dst, "w", encoding="utf-8").write(out)
    print(f"wrote {dst} ({len(out):,} bytes)")


if __name__ == "__main__":
    main()
