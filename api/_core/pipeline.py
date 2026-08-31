"""Persist an extracted statement into Supabase (storage + Postgres) + build XLSX."""
import hashlib
import re
from datetime import datetime
from urllib.parse import quote

from . import xlsx


def dk(s):
    """DD-Mon-YYYY -> YYYY-MM-DD (or None)."""
    if not s:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def txn_hash(org_id, account_number, bank, t):
    basis = "|".join([
        str(org_id), str(account_number), str(bank),
        str(t.get("trans_date", "")), str(t.get("raw_details", "")),
        f"{t.get('withdrawal_dr') or 0:.2f}", f"{t.get('deposit_cr') or 0:.2f}",
        f"{t.get('balance') or 0:.2f}",
    ])
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def persist(sb, org_id, user_id, pdf_bytes, filename, extracted):
    """extracted: dispatcher result. Returns dict for the API response."""
    payload = extracted["payload"]
    s = payload["account_summary"]
    tx = payload["transactions"]
    bank = payload["bank_name"]
    acct_no = s.get("account_number", "")
    period_start, period_end = dk(s.get("statement_period_start")), dk(s.get("statement_period_end"))

    file_sha = hashlib.sha256(pdf_bytes).hexdigest()
    dedupe_key = f"{acct_no}|{period_start}|{period_end}|{file_sha[:16]}"

    # 1) duplicate import check (quote: key contains "|" which must be encoded)
    existing = sb.select("statements",
                         f"select=id,original_filename&dedupe_key=eq.{quote(dedupe_key, safe='')}&limit=1")
    if existing:
        return {"status": "DUPLICATE", "statement_id": existing[0]["id"],
                "message": f"Already imported as '{existing[0].get('original_filename')}'."}

    # 2) upsert account
    acc_rows = sb.insert("accounts", [{
        "org_id": org_id, "bank_name": bank, "account_number": acct_no,
        "account_name": s.get("account_name", ""), "account_type": s.get("account_type", ""),
        "currency": s.get("currency", "GHS"),
    }], upsert=True)
    account_id = acc_rows[0]["id"] if acc_rows else None

    # 3) store original pdf (non-fatal: DB import must not depend on storage)
    pdf_path = None
    try:
        pdf_path = f"{org_id}/{file_sha}_{re.sub(r'[^A-Za-z0-9._-]+', '_', filename)}"
        sb.storage_upload("statements", pdf_path, pdf_bytes, "application/pdf")
    except Exception:
        pdf_path = None

    # 4) statement row
    warnings = extracted["issues"]
    st_rows = sb.insert("statements", [{
        "org_id": org_id, "account_id": account_id, "bank_name": bank,
        "account_number": acct_no, "account_name": s.get("account_name", ""),
        "currency": s.get("currency", "GHS"),
        "period_start": period_start, "period_end": period_end,
        "opening_balance": s.get("opening_balance"), "closing_balance": s.get("closing_balance"),
        "available_balance": s.get("available_balance"),
        "total_debit": s.get("total_debit"), "total_credit": s.get("total_credit"),
        "row_count": len(tx), "pages": extracted.get("pages"),
        "original_filename": filename, "file_sha256": file_sha,
        "storage_path": pdf_path, "dedupe_key": dedupe_key,
        "status": "OK_WITH_WARNINGS" if warnings else "OK",
        "verification": {"issues": warnings, "stats": extracted["stats"]},
        "created_by": user_id,
    }])
    statement_id = st_rows[0]["id"]

    # 5) transactions
    rows = []
    for i, t in enumerate(tx):
        rows.append({
            "statement_id": statement_id, "org_id": org_id, "account_id": account_id,
            "row_index": i,
            "trans_date": dk(t.get("trans_date")) or period_start,
            "value_date": dk(t.get("value_date")) or dk(t.get("trans_date")) or period_start,
            "ref_number": t.get("ref_number", ""), "raw_details": t.get("raw_details", ""),
            "category": t.get("transaction_category", ""), "party": t.get("party_name", ""),
            "reference_id": t.get("reference_id", ""),
            "withdrawal": t.get("withdrawal_dr") or 0, "deposit": t.get("deposit_cr") or 0,
            "balance": t.get("balance") or 0,
            "txn_hash": txn_hash(org_id, acct_no, bank, t),
        })
    for i in range(0, len(rows), 500):
        sb.insert("transactions", rows[i:i + 500], returning="return=minimal")

    # 6) xlsx export -> storage (non-fatal; Excel is also built on-demand at download time)
    try:
        xls = xlsx.build_xlsx(payload)
        sb.storage_upload("exports", f"{org_id}/{statement_id}.xlsx", xls,
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception:
        pass

    # 7) extraction log
    sb.insert("extraction_logs", [{
        "statement_id": statement_id, "org_id": org_id,
        "bank_key": extracted.get("bank_key"), "warnings": warnings,
        "stats": extracted["stats"],
    }], returning="return=minimal")

    if pdf_path is None:
        warnings = warnings + ["original PDF could not be stored (storage error) - statement data saved fine"]
    return {"status": "OK", "statement_id": statement_id, "account_id": account_id,
            "rows": len(tx), "warnings": warnings,
            "summary": {"bank": bank, "account_number": acct_no,
                        "period": f"{s.get('statement_period_start')} -> {s.get('statement_period_end')}",
                        "opening": s.get("opening_balance"), "closing": s.get("closing_balance"),
                        "total_debit": s.get("total_debit"), "total_credit": s.get("total_credit")}}
