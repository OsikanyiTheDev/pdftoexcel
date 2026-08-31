"""Vercel Python serverless function: POST /api/extract (multipart file=statement.pdf).

Flow: verify Supabase JWT -> resolve org -> parse PDF (proven pipeline) ->
persist (storage + DB + xlsx) -> return statement id + verification report.

Env vars:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY   (server-side, required in prod)
  DEV_BYPASS_AUTH=1                         (local dev only; never enable in prod)
"""
import os

from fastapi import FastAPI, File, Header, HTTPException, UploadFile

from _core import pipeline, supabase_rest
from _core.dispatcher import extract_any

app = FastAPI(title="Statement Intel - extract")


def _sb() -> supabase_rest.SB:
    # accepts either naming convention (manual env vars OR the Vercel-Supabase integration sync)
    url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY"))
    if not url or not key:
        raise HTTPException(500, "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured")
    return supabase_rest.SB(url, key)


def _resolve_org(sb, user_id: str) -> str:
    profs = sb.select("profiles", f"select=org_id&id=eq.{user_id}&limit=1")
    if not profs:
        raise HTTPException(403, "No profile for user")
    return profs[0]["org_id"]


@app.post("/")
@app.post("/api/extract")
async def extract(file: UploadFile = File(...), authorization: str = Header(None)):
    sb = _sb()
    if os.environ.get("DEV_BYPASS_AUTH") == "1":
        user_id, org_id = "dev-user", "dev-org"
    else:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "Missing bearer token")
        token = authorization.split(" ", 1)[1].strip()
        user = sb.get_user(token)
        if not user:
            raise HTTPException(401, "Invalid or expired session")
        user_id = user["id"]
        org_id = _resolve_org(sb, user_id)

    if not file:
        raise HTTPException(400, "No file uploaded")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 20 MB)")
    if not data[:5] == b"%PDF-":
        raise HTTPException(400, "Not a PDF file")

    try:
        extracted = extract_any(data, file.filename or "statement.pdf")
    except ValueError as e:
        raise HTTPException(422, str(e))

    result = pipeline.persist(sb, org_id, user_id, data, file.filename or "statement.pdf", extracted)
    return result


@app.get("/")
@app.get("/api/extract")
async def health():
    return {"ok": True, "service": "statement-intel extract"}
