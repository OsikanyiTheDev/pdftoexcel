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
async def health(id: int = 0, authorization: str = Header(None)):
    if _IMPORT_ERRS:
        return JSONResponse(status_code=500, content={
            "ok": False, "service": "statement-intel extract",
            "python": sys.version.split()[0],
            "imports": "BROKEN (see detail)", "detail": _IMPORT_ERRS[0],
            "env_present": {k: bool(os.environ.get(k)) for k in (
                "SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL",
                "SUPABASE_SERVICE_ROLE_KEY", "NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY")}})

    # ---- on-demand Excel export: /api/extract?id=<statement_id> ----
    if id > 0:
        try:
            from fastapi.responses import Response
            sb = _sb()
            user_id, org_id = _auth(sb, authorization)
            srows = sb.select("statements", f"select=*&id=eq.{id}&org_id=eq.{org_id}&limit=1")
            if not srows:
                raise HTTPException(404, f"Statement {id} not found in your workspace")
            s = srows[0]
            tx = sb.select("transactions", f"select=*&statement_id=eq.{id}&order=row_index.asc")
            fnum = lambda v: float(v) if v not in (None, "") else None
            summary = {
                "account_name": s.get("account_name") or "", "customer_address": "",
                "account_number": s.get("account_number") or "", "account_type": "",
                "currency": s.get("currency") or "GHS",
                "statement_period_start": s.get("period_start") or "",
                "statement_period_end": s.get("period_end") or "",
                "opening_balance": fnum(s.get("opening_balance")),
                "closing_balance": fnum(s.get("closing_balance")),
                "available_balance": fnum(s.get("available_balance")),
                "total_credit": fnum(s.get("total_credit")),
                "total_debit": fnum(s.get("total_debit")),
            }
            payload = {"bank_name": s.get("bank_name") or "", "account_summary": summary,
                       "transactions": [{
                           "trans_date": t.get("trans_date") or "",
                           "ref_number": t.get("ref_number") or "",
                           "raw_details": t.get("raw_details") or "",
                           "transaction_category": t.get("category") or "",
                           "party_name": t.get("party") or "",
                           "reference_id": t.get("reference_id") or "",
                           "value_date": t.get("value_date") or t.get("trans_date") or "",
                           "withdrawal_dr": float(t.get("withdrawal") or 0),
                           "deposit_cr": float(t.get("deposit") or 0),
                           "balance": float(t.get("balance") or 0)} for t in tx]}
            data = build_xlsx(payload)
            base_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", s.get("original_filename") or f"statement_{id}")
            fname = re.sub(r"\.pdf$", "", base_name, flags=re.I) + ".xlsx"
            return Response(content=data,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="{fname}"'})
        except HTTPException:
            raise
        except Exception:
            return JSONResponse(status_code=500, content={
                "detail": "Building the Excel file failed",
                "error": traceback.format_exc(limit=6).splitlines()[-6:]})

    return {
        "ok": True, "service": "statement-intel extract",
        "python": sys.version.split()[0], "imports": "OK", "detail": None,
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
