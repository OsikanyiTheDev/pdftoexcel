"""Vercel Python serverless function: POST /api/extract (multipart file=statement.pdf).

Flow: verify Supabase JWT -> resolve org -> parse PDF (proven pipeline) ->
persist (storage + DB + xlsx) -> return statement id + verification report.

Heavy imports are lazy + guarded on purpose: if anything is missing at runtime the
function still boots and returns a JSON diagnostic instead of an HTML crash page.
"""
import json
import os
import traceback

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

app = FastAPI(title="Statement Intel - extract")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def _load_extractor():
    try:
        from _core import pipeline, supabase_rest  # noqa
        from _core.dispatcher import extract_any  # noqa
        return pipeline, supabase_rest, extract_any, None
    except Exception:
        return None, None, None, traceback.format_exc(limit=8)


def _sb(supabase_rest):
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY"))
    if not url or not key:
        raise HTTPException(500, "Server misconfigured: SUPABASE_URL / service key env vars are missing. "
                                 "Check the Vercel <-> Supabase integration or add them manually.")
    return supabase_rest.SB(url, key)


def _auth(sb, supabase_rest, authorization):
    if os.environ.get("DEV_BYPASS_AUTH") == "1":
        return "dev-user", "dev-org"
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token - sign in first")
    token = authorization.split(" ", 1)[1].strip()
    user = sb.get_user(token)
    if not user:
        raise HTTPException(401, "Invalid or expired session")
    profs = sb.select("profiles", f"select=org_id&id=eq.{user['id']}&limit=1")
    if not profs:
        raise HTTPException(403, "No profile for user")
    return user["id"], profs[0]["org_id"]


@app.get("/")
@app.get("/api/extract")
async def health():
    _, _, _, import_err = _load_extractor()
    return {
        "ok": import_err is None,
        "service": "statement-intel extract",
        "python": os.sys.version.split()[0],
        "imports": "OK" if import_err is None else "BROKEN (see detail)",
        "detail": import_err.splitlines()[-1] if import_err else None,
        "env_present": {k: bool(os.environ.get(k)) for k in (
            "SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL",
            "SUPABASE_SERVICE_ROLE_KEY", "NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY")},
    }


@app.post("/")
@app.post("/api/extract")
async def extract(file: UploadFile = File(None), authorization: str = Header(None)):
    pipeline, supabase_rest, extract_any, import_err = _load_extractor()
    if import_err:
        return JSONResponse(status_code=500, content={
            "detail": "Extractor libraries failed to load on the server",
            "traceback_tail": import_err.splitlines()[-6:],
        })
    if file is None or not file.filename:
        raise HTTPException(400, "No file uploaded (field name must be 'file')")

    sb = _sb(supabase_rest)
    user_id, org_id = _auth(sb, supabase_rest, authorization)

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
            "error": traceback.format_exc(limit=6).splitlines()[-6:],
        })

    try:
        result = pipeline.persist(sb, org_id, user_id, data, file.filename, extracted)
    except Exception:
        return JSONResponse(status_code=500, content={
            "detail": "Parsed OK but saving to the database failed",
            "error": traceback.format_exc(limit=6).splitlines()[-6:],
        })
    if result.get("status") == "DUPLICATE":
        return JSONResponse(status_code=409, content=result)
    return result
