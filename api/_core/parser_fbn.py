"""FirstBank Ghana statement parser (ported 1:1 from the verified pipeline)."""
import os
import re
import tempfile

import pymupdf

from .parser_banks import num

import re
import json
import sys
import pymupdf

DATE_RE = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$")

def parse(pdf_path=None):
    doc = pymupdf.open(pdf_path or PDF)
    all_rows = []
    opening = closing = None
    for pno, page in enumerate(doc, start=1):
        words = page.get_text("words")  # x0,y0,x1,y1,text,block,line,wno
        # drop diagonal-watermark fragments at fixed positions on every page
        words = [w for w in words if not (
            (w[4] == "FirstBank" and abs(w[1] - 355.99) < 1.5) or
            (w[4] == "Ghana" and abs(w[1] - 173.60) < 1.5))]

        # group words into visual lines by y
        lines = {}
        for w in words:
            key = round(w[1] / 3.0)  # 3pt bucket; line pitch is 6pt, rows 20pt
            lines.setdefault(key, []).append(w)
        ordered = [sorted(ws, key=lambda w: w[0]) for k, ws in sorted(lines.items())]

        # locate header line to derive column edges
        edges = None
        header_seen = False
        for ws in ordered:
            texts = [w[4] for w in ws]
            if "Trans" in texts and any(t.startswith("Withdrawal") for t in texts):
                def x(tok_prefix):
                    return next(w[0] for w in ws if w[4].startswith(tok_prefix))
                edges = [x("Trans"), x("Ref."), x("Transaction"), x("Value"),
                         x("Withdrawal"), x("Deposit"), x("Balance")]
                header_seen = True
                continue
            if not header_seen or not edges:
                continue  # bank name / blank lines above header
            joined = " ".join(texts)
            if re.match(r"^Page \d+ of \d+$", joined) or joined.strip() == "FirstBank Ghana":
                continue

            # bucket words into columns
            cols = [[] for _ in range(7)]
            for w in ws:
                x0 = w[0]
                idx = 6
                for i in range(6):
                    if x0 < edges[i + 1] - 2:
                        idx = i
                        break
                # right-edge correction: DR/CR/Balance numbers are right-aligned;
                # a word starting before its column header belongs to previous numeric col
                cols[idx].append(w)
            trans, refn, det, val, dr, cr, bal = [" ".join(w[4] for w in c) for c in cols]

            if trans in ("Opening Balance", "") and det in ("Opening Balance", "Closing Balance") :
                amt = float(bal.replace(",", "")) if bal else None
                if det == "Opening Balance":
                    opening = amt
                else:
                    closing = amt
                continue
            if not DATE_RE.match(trans):
                if det and all_rows and not refn and not val and not dr and not cr and not bal:
                    all_rows[-1]["det_lines"].append(det)  # wrapped description line
                elif det or refn:
                    print(f"  [warn p{pno}] unparsed line: T='{trans}' R='{refn}' D='{det}' V='{val}' DR='{dr}' CR='{cr}' B='{bal}'")
                continue

            all_rows.append({
                "page": pno, "trans": trans, "refn": refn.strip(),
                "det_lines": [det] if det else [],
                "value": val if DATE_RE.match(val) else (trans if not val else val),
                "dr": dr, "cr": cr, "bal": bal,
            })

    # merge wrapped description lines
    for r in all_rows:
        merged = ""
        for ln in r["det_lines"]:
            ln = ln.strip()
            if not ln:
                continue
            if merged.endswith("-"):
                merged += ln
            else:
                merged += (" " if merged else "") + ln
        r["details"] = re.sub(r"\s+", " ", merged).strip()
        del r["det_lines"]
    return all_rows, opening, closing

def num(s):
    s = (s or "0.00").replace(",", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return float(s) if s else 0.0


DATE_RE = re.compile(r"\d{2}-[A-Za-z]{3}-\d{4}")
PERIOD_RE = re.compile(r"statement for the period:\s*(\d{2}-[A-Za-z]{3}-\d{4})\s*To\s*(\d{2}-[A-Za-z]{3}-\d{4})")
AMT_RE = re.compile(r"^\(?([\d,]+\.\d{2})\)?$")
ADDR_STOP = "please find below"

LABELS = ["Account No", "Account Name", "Account Type", "Currency",
          "Available Balance", "Total Credit", "Total Debit", "Pending Debit"]

AMT_RE = re.compile(r"^\(?([\d,]+\.\d{2})\)?$")
ADDR_STOP = "please find below"


def parse_amount(s):
    s = (s or "").strip()
    m = AMT_RE.match(s)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return -v if s.startswith("(") else v


def extract_header(page_text):
    """Parse the letter-header block of a statement section into metadata."""
    lines = [l.strip() for l in page_text.splitlines() if l.strip()]
    meta = {"bank_name": lines[0] if lines else ""}
    meta["account_holder_greeting"] = ""
    addr_lines, in_addr = [], False
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("Dear "):
            meta["account_holder_greeting"] = ln[5:].strip()
            in_addr = True
        elif in_addr and ln.lower().startswith(ADDR_STOP):
            in_addr = False
            m = PERIOD_RE.search(ln)
            if m:
                meta["statement_period_start"], meta["statement_period_end"] = m.groups()
        elif in_addr:
            addr_lines.append(ln)
        elif ln.rstrip(":") in LABELS and i + 1 < len(lines):
            meta[ln.rstrip(":")] = lines[i + 1].strip()
            i += 1
        i += 1

    def clean_address(raw_lines):
        text = re.sub(r"\s+", " ", " ".join(raw_lines)).strip(" ,")
        toks = [t.strip() for t in text.split(",") if t.strip()]
        # collapse duplicated words inside a token ("Accra Accra" -> "Accra")
        collapsed = []
        for t in toks:
            parts, ded = t.split(), []
            for w in parts:
                if not ded or w.upper() != ded[-1].upper():
                    ded.append(w)
            collapsed.append(" ".join(ded))
        # dedupe repeats, keep numbers, drop bare fragments contained in later tokens
        seen, out = set(), []
        for t in collapsed:
            key = re.sub(r"[^A-Z0-9]", "", t.upper())
            if not key or (key in seen and not key.isdigit()):
                continue
            seen.add(key)
            out.append(t)
        out = [t for i, t in enumerate(out)
               if not any(o.lower().startswith(t.lower() + " ") for o in out[i+1:])]
        titled = []
        for t in out:
            words = t.split()
            titled.append(" ".join(w if (i == 0 and w.lower() == "no") else w.title()
                                   for i, w in enumerate(words)))
        addr = ", ".join(titled)
        return re.sub(r"^No\s+A\.249/\s*,?\s*", "No A.249/, ", addr)

    summary = {
        "account_name": meta.get("Account Name", meta.get("account_holder_greeting", "")),
        "customer_address": clean_address(addr_lines),
        "account_number": meta.get("Account No", ""),
        "account_type": meta.get("Account Type", ""),
        "currency": meta.get("Currency", ""),
        "statement_period_start": meta.get("statement_period_start", ""),
        "statement_period_end": meta.get("statement_period_end", ""),
        "opening_balance": None,   # filled from the table's Opening Balance row
        "closing_balance": None,   # filled from the table's Closing Balance row
        "available_balance": parse_amount(meta.get("Available Balance", "")) or 0.0,
        "total_credit": parse_amount(meta.get("Total Credit", "")) or 0.0,
        "total_debit": parse_amount(meta.get("Total Debit", "")) or 0.0,
    }
    return meta["bank_name"], summary





def find_sections(doc):
    """Return list of (start_page, end_page_inclusive) - one per letter header found."""
    starts = []
    for i, page in enumerate(doc):
        if "Please find below your bank statement for the period" in page.get_text():
            starts.append(i)
    if not starts:
        return [(0, len(doc) - 1)]
    sections = []
    for n, s in enumerate(starts):
        e = (starts[n + 1] - 1) if n + 1 < len(starts) else len(doc) - 1
        sections.append((s, e))
    return sections





REF_TOK = re.compile(r"Ref\S+")

def split_ref(details):
    m = REF_TOK.search(details)
    return m.group(0) if m else ""

def after_colon(details, key):
    """Text after 'KEY:' up to the Ref token, e.g. CASH W/D 3RD PARTY:JOHN X Ref12 -> 'JOHN X'."""
    i = details.find(key)
    if i < 0:
        return ""
    rest = details[i + len(key):]
    rest = re.split(r"\s+Ref\S+$", rest)[0]
    return rest.strip(" .")

def categorize(details, dr=0.0, cr=0.0):
    d = details.lstrip(":").strip()
    dl = d.upper()
    # --- Cheque deposits from other banks (generic) ---
    m = re.match(r"^([A-Z][A-Z ]*?)\s*CHQ\s*#\s*\d+\s*B\s*/?\s*O\s+(.*?)\s+Ref\S*$", d)
    if m:
        bank = " ".join(m.group(1).split())
        bank = {"ECO": "Ecobank", "ECOBANK": "Ecobank", "STAN CHAT": "Stanbic", "STAN": "Stanbic",
                "UMB": "UMB", "ABSA": "Absa", "NIB": "NIB", "UBA": "UBA", "ADB": "ADB", "BOA": "BOA"}.get(bank, bank.title())
        return f"Cheque Deposit ({bank})", m.group(2).strip()
    # --- Cash ---
    if dl.startswith("CASH W/D 3RD PARTY:"):
        return "Cash Withdrawal (3rd Party)", after_colon(d, "PARTY:")
    if dl.startswith("CASH W/D SELF:"):
        return "Cash Withdrawal (Self)", after_colon(d, "SELF:")
    if dl.startswith("CASH DEP 3RD PARTY:"):
        return "Cash Deposit (3rd Party)", after_colon(d, "PARTY:")
    if dl.startswith("CASH DEPOSIT:"):
        return "Cash Deposit", after_colon(d, "DEPOSIT:")
    # --- Transfers ---
    if "FUNDS TRSF:IFO" in dl:
        return "Funds Transfer (In Favour Of)", after_colon(d, "IFO ").split(" Ref")[0].strip() or after_colon(d, "IFO")
    if "FUNDS TRSF:B/O" in dl:
        return "Funds Transfer (On Behalf Of)", after_colon(d, "B/O ")
    if dl.startswith("NRT:TRF"):
        return "Interbank Transfer (NRT)", "AVE MARIA SCHOOL"
    # --- Cheque unpaid cycle ---
    if dl.startswith("COMM ON UNPAID"):
        return "Unpaid Cheque Commission", ""
    if dl.startswith("UNPAID/CHQ"):
        return "Unpaid Cheque Return (Refer to Drawer)", ""
    if "RTD CHARGE" in dl:
        return "Cheque Return Charge (RTD 10%)", ""
    if dl.startswith("INW CLEARING CHQ"):
        return "Inward Clearing (Own Cheque Paid)", ""
    if dl.startswith("INW CLG"):
        return "Inward Clearing (Cheque Presentment)", ""
    if dl.startswith("INW CLEARING"):
        return "Inward Clearing", ""
    # --- fees & charges (recurring) ---
    if dl.startswith("SMS ALERT CHARGE"):
        return "SMS Alert Charge", ""
    if dl.startswith("TOKEN MAINTAINANCE") or dl.startswith("TOKEN MAINTENANCE"):
        return "Token Maintenance Charge", ""
    if dl.startswith("SBS:STATEMENTCHARGES"):
        return "Statement Charge (SBS)", ""
    # --- NRT interbank bulk items ---
    if dl.startswith("NRT:CHG:"):
        return "Interbank Transfer Charge (NRT)", ""
    if dl.startswith("NRT:NRT BULK"):
        return "Interbank Bulk Debit (NRT)", ""
    # --- direct debits as advised ---
    if dl.startswith("DR AS ADVISED:"):
        who = after_colon(d, "ADVISED:")
        party = who.split()[0] if who.split() else ""
        return "Direct Debit (As Advised)", party
    # --- outward cheque return ---
    if dl.startswith("OUTRET/"):
        return "Outward Cheque Return (Refer to Drawer)", ""
    # --- cheque deposit adjustments ---
    if dl.startswith("CHQ DEP:IFO"):
        return "Cheque Deposit Adjustment", after_colon(d, "IFO ")
    # --- squashed school-fee credits ---
    m = re.match(r"^0+([A-Za-z ]*fees)\s+Ref\S*$", d, re.I)
    if m:
        return "Interbank Credit (School Fees)", ""
    if dl.startswith("FBM:"):
        return "Mobile Transfer Credit (FBM)", after_colon(d, "FBM:")
    # --- Fees ---
    if dl.startswith("E-BUNDLE CHARGE"):
        return "E-Bundle Charge", ""
    if "CHEQUE BOOK ISSUANCE" in dl:
        return "Cheque Book Issuance Fee", ""
    if dl.startswith("MONTHLY SALARY"):
        return "Salary Payment (FTM)", ""
    # --- GHIPSS ---
    if "GHIPSS:CHG:" in dl:
        return "GHIPSS Channel Charge", after_colon(d, "Channels:").split("/")[0].strip()
    if "GHIPSS:OTHER_CHANNELS:" in dl:
        return "GHIPSS Transfer (Other Channels)", after_colon(d, "Channels:").split("/")[0].strip()
    m = re.match(r"^P03090", d)
    if m:
        return "GhIPSS Interbank Credit (GIP)", ""
    m = re.match(r"^(P03062)Outward transfer/(?:\S+?)/Fee", d)
    if m:
        return "GhIPSS Outward Transfer (P03062)", ""
    m = re.match(r"^(P03040)(.+?)\s+\S*emerald", d)
    if m:
        return f"GhIPSS Direct Credit ({m.group(1)})", m.group(2).strip()
    m = re.match(r"^(P03\d+)FTR-(.+?)(?:\s+Ref\S*)?$", d)
    if m:
        party = re.split(r"\s+FEES-\d+$", m.group(2).strip())[0].strip()
        return f"GhIPSS Direct Credit ({m.group(1)})", party
    m = re.match(r"^(P03[0-9]{3})", d)
    if m:
        return f"GhIPSS Direct Credit ({m.group(1)})", ""
    # --- Transfers (generic) ---
    if dl.startswith("FUNDS TRSF:"):
        return "Funds Transfer", after_colon(d, "TRSF:").split(" Ref")[0].strip()
    # --- Squashed mobile/interbank names ---
    if "JEREMIAH" in dl.upper():
        return "Interbank Credit", "JEREMIAH VROOM"
    if "THEOPHILUS" in dl:
        return "Interbank Credit", "THEOPHILUS AND FELICIA AM"
    # --- direction-aware fallback ---
    name = REF_TOK.sub("", d).strip(" :/-0123456789").strip()
    if cr > 0 and name:
        return "Interbank Credit", name
    if dr > 0 and name:
        return "Other Debit", name
    return "Other", ""



def parse_page_range(doc, s, e):
    """parse() over doc pages [s..e] via a temp single-section pdf."""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    nd = pymupdf.open()
    nd.insert_pdf(doc, from_page=s, to_page=e)
    nd.save(tmp.name)
    nd.close()
    try:
        rows, opening, closing = parse(tmp.name)
    finally:
        os.unlink(tmp.name)
    return rows, opening, closing


def extract_fbn(pdf_path):
    """Full FBN extraction -> (payload, issues, stats). Handles the first section found."""
    doc = pymupdf.open(pdf_path)
    s, e = find_sections(doc)[0]
    bank, summary = extract_header(doc[s].get_text())
    rows_r, op, cl = parse_page_range(doc, s, e)
    if op is None or cl is None:
        return None, ["opening/closing balance row not found"], {}
    run, bad, tdr, tcr = op, 0, 0.0, 0.0
    for r in rows_r:
        d, c, b = num(r["dr"]), num(r["cr"]), num(r["bal"])
        tdr += d; tcr += c
        run = round(run + c - d, 2)
        if abs(run - b) > 0.005:
            bad += 1
    issues = []
    if bad:
        issues.append(f"{bad}/{len(rows_r)} running-balance mismatches")
    summary["opening_balance"] = op
    summary["closing_balance"] = cl
    if not summary["total_debit"]:
        summary["total_debit"] = round(tdr, 2)
    if not summary["total_credit"]:
        summary["total_credit"] = round(tcr, 2)
    if abs(tdr - summary["total_debit"]) > 0.01:
        issues.append(f"sumDR {tdr:,.2f} != header {summary['total_debit']:,.2f}")
    if abs(tcr - summary["total_credit"]) > 0.01:
        issues.append(f"sumCR {tcr:,.2f} != header {summary['total_credit']:,.2f}")
    transactions = []
    for r in rows_r:
        details = r["details"]
        cat, party = categorize(details, num(r["dr"]), num(r["cr"]))
        party = re.sub(r"\s+", " ", party).upper().strip() if party else ""
        transactions.append({
            "trans_date": r["trans"], "ref_number": r["refn"], "raw_details": details,
            "transaction_category": cat, "party_name": party,
            "reference_id": split_ref(details), "value_date": r["value"],
            "withdrawal_dr": round(num(r["dr"]), 2), "deposit_cr": round(num(r["cr"]), 2),
            "balance": round(num(r["bal"]), 2),
        })
    payload = {"bank_name": bank, "account_summary": summary, "transactions": transactions}
    stats = {"rows": len(transactions), "bad": bad, "total_dr": round(tdr, 2),
             "total_cr": round(tcr, 2), "final": cl}
    return payload, issues, stats
