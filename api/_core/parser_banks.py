"""Multi-bank statement parsers: GTBank, ADB, OmniBSIC, Ecobank, MTN MoMo.
Ported 1:1 from the verified pipeline; process() returns payload instead of writing files."""
import json
import os
import re

import pymupdf


"""
Multi-bank statement extractor for AVE MARIA SCHOOL statements.
Banks: GTBank (GT), ADB, OmniBSIC, Ecobank, MTN MoMo  (+ FBN handled by parse_statement.py)

Design: shared word/line engine with per-bank column configs + row anchors,
description attachment by nearest anchor, running-balance verification.
"""
import re
from datetime import datetime

import pymupdf

MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}
IDX_MONTHS = {i + 1: m for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def fmt_date_ddmmyyyy(s):
    """01-04-2026 -> 01-Apr-2026 (day-first); DD-Mon-YYYY passes through."""
    if re.match(r"^\d{2}-[A-Za-z]{3}-\d{4}$", s):
        return s
    d, m, y = s.split("-")
    return f"{d}-{IDX_MONTHS[int(m)]}-{y}"


def fmt_date_slashmon(s):
    """01/JUN/2026 -> 01-Jun-2026."""
    d, m, y = s.split("/")
    return f"{d}-{m.capitalize()}-{y}"


def fmt_date_iso(s):
    """2024-01-04 -> 04-Jan-2024."""
    y, m, d = s.split("-")
    return f"{d}-{IDX_MONTHS[int(m)]}-{y}"


def num(s):
    """'1,234.56' / '(2.00)' / '2017.6' / '' -> float"""
    s = (s or "").strip().replace(",", "").replace(" ", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if not s or s in {"-", "."}:
        return 0.0
    try:
        v = float(s)
    except ValueError:
        return 0.0
    return -v if neg else v


def group_lines(words, tol=2.0):
    """words: [(x0,y0,x1,y1,text)] -> list of (y, [words sorted by x])"""
    lines = {}
    for w in words:
        placed = False
        for k in list(lines):
            if abs(k - w[1]) < tol:
                lines[k].append(w)
                placed = True
                break
        if not placed:
            lines[w[1]] = [w]
    return [(y, sorted(ws, key=lambda w: w[0])) for y, ws in sorted(lines.items())]


def bucket(ws, edges):
    """assign words to columns by x0 with right-edge correction for numbers"""
    cols = [[] for _ in edges[:-1]]
    for w in ws:
        idx = len(cols) - 1
        for i in range(len(cols) - 1):
            if w[0] < edges[i + 1] - 2:
                idx = i
                break
        cols[idx].append(w)
    return [" ".join(w[4] for w in c) for c in cols]


NUM_RE = re.compile(r"^-?\(?[\d,]+\.\d{2}\)?$|^-?\(?[\d,]+\)?$")


class ColumnBank:
    """Generic engine: fixed column edges, anchor predicate, nearest-anchor desc attach."""
    edges = None
    date_col = 0
    balance_col = -1
    amount_cols = ()
    desc_cols = ()
    anchor_date_re = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$|^\d{2}-\d{2}-\d{4}$")
    skip_lines = ()
    max_gap = 40.0
    attach_mode = "nearest"

    def line_texts(self, ws):
        return bucket(ws, self.edges)

    def is_anchor(self, cols):
        return bool(self.anchor_date_re.match(cols[self.date_col])) and \
            NUM_RE.match(cols[self.balance_col].replace(" ", "")) is not None

    def is_junk(self, y, cols):
        joined = " ".join(c for c in cols if c).upper()
        return any(pat in joined for pat in self.skip_lines)

    def parse(self, doc):
        """Two-pass: collect lines, then attach desc lines to nearest anchor (either side)."""
        all_lines = []  # (pno, y, cols)
        for pno, page in enumerate(doc):
            for y, ws in group_lines(page.get_text("words")):
                cols = self.line_texts(ws)
                if self.is_junk(y, cols):
                    continue
                all_lines.append((pno, y, cols))
        rows = []
        anchors = []  # (pno, y, row)
        for pno, y, cols in all_lines:
            if self.is_anchor(cols):
                row = self.make_row(pno, y, cols)
                rows.append(row)
                anchors.append([pno, y, row])
        # attach desc-only lines
        last_above = None
        for pno, y, cols in all_lines:
            if self.is_anchor(cols):
                last_above = next((a for a in reversed(anchors) if a[0] == pno and a[1] <= y), last_above)
                continue
            if not self.has_desc(cols):
                continue
            if self.attach_mode == "above":
                if last_above is not None and last_above[0] == pno:
                    self.absorb(y, last_above[2], cols)
                continue
            best, bd = None, None
            for a in anchors:
                if a[0] != pno:
                    continue
                d = abs(a[1] - y)
                if bd is None or d < bd:
                    best, bd = a, d
            if best is not None and bd <= self.max_gap:
                self.absorb(y, best[2], cols)
        return rows

    def has_desc(self, cols):
        return bool(" ".join(cols[i] for i in self.desc_cols).strip())

    def make_row(self, pno, y, cols):
        raise NotImplementedError

    def absorb(self, y, row, cols, anchors_y):
        desc = " ".join(cols[i] for i in self.desc_cols).strip()
        if not desc:
            return
        row.setdefault("_extra", []).append(desc)

    def finalize(self, rows):
        """attach _extra lines to nearest anchor (within max_gap)"""
        for i, r in enumerate(rows):
            extras = []
            for y, desc, pno in r.get("_extra_meta", []):
                extras.append(desc)
            base = r.get("raw_details", "")
            pieces = [p for p in [base] + extras if p]
            r["raw_details"] = re.sub(r"\s+", " ", " ".join(pieces)).strip()
        return rows


# ---------------------------------------------------------------- GTBank ----
class GTB(ColumnBank):
    attach_mode = "above"
    edges = [0, 100, 200, 285, 365, 450, 10000]  # date value debit credit balance remarks
    date_col, balance_col = 0, 4
    amount_cols = (2, 3)
    desc_cols = (5,)
    anchor_date_re = re.compile(r"^\d{2}-[A-Za-z]{3}-\d{4}$")
    skip_lines = ("TOTAL:", "Statement Period", "Trans. Date")

    def make_row(self, pno, y, cols):
        return {"page": pno, "y": y, "_pre": [], "_post": [], "_extra_meta": [],
                "trans": fmt_date_ddmmyyyy(cols[0]) if "-" in cols[0] else cols[0],
                "value": fmt_date_ddmmyyyy(cols[1]) if "-" in cols[1] else cols[1],
                "dr_raw": cols[2].strip(), "cr_raw": cols[3].strip(),
                "bal": num(cols[4]),
                "raw_details": cols[5].strip()}

    def absorb(self, y, row, cols, anchors=None):
        desc = cols[5].strip()
        if desc:
            row["_post"].append(desc)

    def compose(self, r):
        parts = [re.sub(r"\s+", " ", p).strip() for p in [r["raw_details"]] + r["_post"]]
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()

    def finalize(self, rows):
        for r in rows:
            r["raw_details"] = self.compose(r)
            r["withdrawal_dr"] = round(num(r["dr_raw"]), 2)
            r["deposit_cr"] = round(num(r["cr_raw"]), 2)
            r["balance"] = round(r["bal"], 2)
            r["value_date"] = r["value"]
        return rows


# ------------------------------------------------------------------ ADB ----
class ADB(ColumnBank):
    edges = [0, 55, 90, 246, 336, 404, 462, 520, 10000]
    max_gap = 14.0
    # date branch desc ref value debit credit balance
    date_col, balance_col = 0, 7
    amount_cols = (5, 6)
    desc_cols = (2, 3)
    anchor_date_re = re.compile(r"^\d{2}-\d{2}-\d{4}$")
    skip_lines = ("STATEMENT OF ACCOUNT", "Period From", "Account No", "NOT FOR VISA")

    def make_row(self, pno, y, cols):
        return {"page": pno, "y": y, "_pre": [], "_post": [], "_extra_meta": [],
                "trans": cols[0].strip(), "branch": cols[1].strip(),
                "raw_details": cols[2].strip(), "ref": cols[3].strip(),
                "value": cols[4].strip(), "dr_raw": cols[5].strip(),
                "cr_raw": cols[6].strip(), "bal": num(cols[7])}

    def absorb(self, y, row, cols, anchors=None):
        desc_cols_txt = " ".join(cols[i] for i in self.desc_cols).strip()
        if not desc_cols_txt:
            return
        (row["_pre"] if y < row["y"] else row["_post"]).append(desc_cols_txt)

    def compose(self, r):
        parts = [re.sub(r"\s+", " ", p).strip() for p in r["_pre"] + [r["raw_details"]] + r["_post"]]
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()

    def finalize(self, rows):
        for r in rows:
            r["raw_details"] = self.compose(r)
            r["trans"] = fmt_date_ddmmyyyy(r["trans"])
            r["value_date"] = fmt_date_ddmmyyyy(r["value"]) if re.match(r"^\d{2}-\d{2}-\d{4}$", r["value"]) else r["trans"]
            r["withdrawal_dr"] = round(num(r["dr_raw"]), 2)
            r["deposit_cr"] = round(num(r["cr_raw"]), 2)
            r["balance"] = round(r["bal"], 2)
            r["ref_number"] = r["ref"]
        return rows


# ------------------------------------------------------------- OmniBSIC ----
class OMNI(ColumnBank):
    edges = [0, 75, 238, 300, 368, 440, 10000]
    max_gap = 10.0
    # booking desc value DR CR bal
    date_col, balance_col = 0, 5
    amount_cols = (3, 4)
    desc_cols = (1,)
    anchor_date_re = re.compile(r"^\d{2}-\d{2}-\d{4}$")
    skip_lines = ("ACCOUNT STATEMENT", "Name Of Customer", "Balance Brought Forward",
                  "Booking Date", "INTERNAL USE ONLY")
    watermark = {"INTERNAL", "USE", "ONLY", "USE ONLY", "INTERNAL USE"}

    def make_row(self, pno, y, cols):
        return {"page": pno, "y": y, "_pre": [], "_post": [], "_extra_meta": [],
                "trans": cols[0].strip(), "raw_details": cols[1].strip(),
                "value": cols[2].strip(), "dr_raw": cols[3].strip(),
                "cr_raw": cols[4].strip(), "bal": num(cols[5])}

    def absorb(self, y, row, cols, anchors=None):
        desc = cols[1].strip()
        if not desc or desc in self.watermark:
            return
        (row["_pre"] if y < row["y"] else row["_post"]).append(desc)

    def compose(self, r):
        parts = [re.sub(r"\s+", " ", p).strip() for p in r["_pre"] + [r["raw_details"]] + r["_post"]]
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()

    def finalize(self, rows):
        for r in rows:
            r["raw_details"] = self.compose(r)
            r["trans"] = fmt_date_ddmmyyyy(r["trans"])
            r["value_date"] = fmt_date_ddmmyyyy(r["value"]) if re.match(r"^\d{2}-\d{2}-\d{4}$", r["value"]) else r["trans"]
            r["withdrawal_dr"] = round(num(r["dr_raw"]), 2)
            r["deposit_cr"] = round(num(r["cr_raw"]), 2)
            r["balance"] = round(r["bal"], 2)
        return rows


# ------------------------------------------------------------- Ecobank ----
ECO_DATE = re.compile(r"^\d{2}/[A-Z]{3}/\d{4}$")
ECO_REF = re.compile(r"\b(H[0-9A-Z]{13,})\b")
ECO_ACCT_FRAG = {"14410", "04968", "722"}


class ECO(ColumnBank):
    edges = [0, 60, 220, 262, 355, 435, 510, 10000]
    max_gap = 30.0
    # date desc inst value debit credit balance
    date_col, balance_col = 0, 6
    amount_cols = (4, 5)
    desc_cols = (1, 2)
    anchor_date_re = ECO_DATE
    skip_lines = ("STATEMENT OF ACCOUNT", "Cust Ac No", "Opening Balance", "Closing Balance",
                  "Cleared Balance", "Uncleared Effects", "Total Withdrawals", "Total Deposits",
                  "Statement Period", "P O BOX", "AVE MARIA SCHOOL LIMITED")

    def make_row(self, pno, y, cols):
        return {"page": pno, "y": y, "_pre": [], "_post": [],
                "trans": fmt_date_slashmon(cols[0].strip()),
                "raw_details": "", "value": "", "dr_raw": cols[4].strip(),
                "cr_raw": cols[5].strip(), "bal": num(cols[6])}

    def absorb(self, y, row, cols, anchors=None):
        desc = cols[1].strip()
        if not desc:
            return
        desc = " ".join(p for p in desc.split() if p not in ECO_ACCT_FRAG)
        if desc:
            (row["_pre"] if y < row["y"] else row["_post"]).append(desc)

    def compose(self, r):
        parts = [re.sub(r"\s+", " ", p).strip() for p in r["_pre"] + [r["raw_details"]] + r["_post"]]
        return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()

    def finalize(self, rows):
        for r in rows:
            r["raw_details"] = self.compose(r)
            r["value_date"] = r["value"] or r["trans"]
            r["withdrawal_dr"] = round(num(r["dr_raw"]), 2)
            r["deposit_cr"] = round(num(r["cr_raw"]), 2)
            r["balance"] = round(r["bal"], 2)
        return rows


# ---------------------------------------------------------- MTN MoMo ----
MOMO_EDGES = [83, 130, 180, 230, 320, 380, 494, 538, 564, 586, 614, 660, 704, 746, 800, 862, 930, 20000]
MOMO_COLS = ["d1", "datetime", "from_acct", "from_name", "from_phone", "type", "amt",
             "from_fee", "to_fee", "tax", "bal_before", "bal_after", "after_acct",
             "after_name", "to_msisdn", "fin_id", "message"]
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
YYYYMMDD = re.compile(r"^\d{8}$")


def parse_momo(doc):
    rows = []
    for pno, page in enumerate(doc):
        words = page.get_text("words")
        # drop the header words (top of page) for pages > 1 handled by anchor detection
        for y, ws in group_lines(words, tol=2.5):
            cols = bucket(ws, MOMO_EDGES)
            d1 = cols[0].strip()
            if not YYYYMMDD.match(d1):
                # continuation line -> previous row
                if rows and y > 240:
                    extra = " ".join(c for c in cols[3:] if c.strip()).strip()
                    if extra and not ISO_DATE.match(cols[1].strip()):
                        rows[-1]["_cont"].append(extra)
                continue
            rec = dict(zip(MOMO_COLS, [c.strip() for c in cols]))
            rec["page"] = pno
            rec["_cont"] = []
            rows.append(rec)
    out = []
    for r in rows:
        dt = f"{r['d1'][:4]}-{r['d1'][4:6]}-{r['d1'][6:]}"
        trans = fmt_date_iso(dt)
        bal_before, bal_after = num(r["bal_before"]), num(r["bal_after"])
        delta = round(bal_after - bal_before, 2)
        amt = num(r["amt"])
        fee = round(num(r["from_fee"]) + num(r["to_fee"]), 2)
        tax = num(r["tax"])
        dr, cr = (0.0, 0.0)
        if delta >= 0:
            cr = delta
        else:
            dr = -delta
        cont = " ".join(r["_cont"])
        from_side = r["from_name"] or r["from_phone"]
        raw = (f"{r['type']} | From: {from_side} ({r['from_phone']}) Acct {r['from_acct']} | "
               f"To: {r['after_name']} ({r['to_msisdn']}) Acct {r['after_acct']} | "
               f"Bal {bal_before:,.2f} -> {bal_after:,.2f} | Fee {fee:,.2f} Tax {tax:,.2f} | "
               f"FinID {r['fin_id']} Msg {r['message']} {cont}")
        raw = re.sub(r"\s+", " ", raw).strip()
        out.append({
            "page": r["page"], "trans": trans, "time": r["datetime"],
            "raw_details": raw, "withdrawal_dr": round(dr, 2), "deposit_cr": round(cr, 2),
            "balance": round(bal_after, 2), "bal_before": round(bal_before, 2), "value_date": trans,
            "type": r["type"], "amount": amt, "from_name": r["from_name"],
            "from_phone": r["from_phone"], "to_msisdn": r["to_msisdn"],
            "after_name": r["after_name"], "ref": r["from_acct"],
            "delta_match": abs(abs(delta) - amt) < 0.005,
            "fee": fee, "tax": tax,
        })
    return out


# ------------------------------------------------ metadata extraction ----
def between(text, start_label, end_labels):
    i = text.find(start_label)
    if i < 0:
        return ""
    rest = text[i + len(start_label):]
    for el in end_labels:
        j = rest.find(el)
        if j >= 0:
            rest = rest[:j]
    return rest.strip(" :\n").strip()


def meta_adb(doc):
    t = doc[0].get_text()
    addr = between(t, "Customer Address", ["Account Title", "Date\nBranch"])
    addr = re.sub(r"\s+", " ", addr)
    m = re.search(r"Period From :\s*(\d{2}-\d{2}-\d{4})\s*To :\s*(\d{2}-\d{2}-\d{4})", t)
    return {
        "account_name": between(t, "Customer Name", ["Customer Address", "Account Title"]),
        "customer_address": addr,
        "account_number": between(t, "Account No", ["Product Name"]),
        "account_type": "Product " + between(t, "Product Name", ["Currency Name"]) + " - " +
                        between(t, "Branch Name", ["Customer ID"]),
        "currency": between(t, "Currency Name", ["Branch Code"]),
        "statement_period_start": fmt_date_ddmmyyyy(m.group(1)) if m else "",
        "statement_period_end": fmt_date_ddmmyyyy(m.group(2)) if m else "",
        "available_balance": None, "total_credit": None, "total_debit": None,
        "opening_balance": None, "closing_balance": None,
    }


def meta_gt(doc):
    t = doc[0].get_text()
    m = re.search(r"Statement Period:\s*(\d{2}-[A-Za-z]{3}-\d{4})\s*to\s*(\d{2}-[A-Za-z]{3}-\d{4})", t)
    return {
        "account_name": between(t, "CUSTOMER STATEMENT", ["Print. Date"]),
        "customer_address": between(t, "Address", ["Account Type"]),
        "account_number": between(t, "Account No.", ["Address", "Account Type"]),
        "account_type": between(t, "Account Type", ["Currency"]),
        "currency": between(t, "Currency", ["Opening Balance"]),
        "statement_period_start": m.group(1) if m else "",
        "statement_period_end": m.group(2) if m else "",
        "opening_balance": num(between(t, "Opening Balance", ["Closing Balance", "Trans. Date"])),
        "closing_balance": num(between(t, "Closing Balance", ["Trans. Date"])),
        "available_balance": None, "total_credit": None, "total_debit": None,
    }


def meta_omni(doc):
    t = doc[0].get_text()
    m = re.search(r"Start Date\s*:\s*([A-Za-z]+ \d{1,2}, \d{4})", t)
    m2 = re.search(r"End Date\s*:\s*([A-Za-z]+ \d{1,2}, \d{4})", t)
    f = lambda s: datetime.strptime(s, "%b %d, %Y").strftime("%d-%b-%Y") if s else ""
    return {
        "account_name": between(t, "Name Of Customer:", ["Branch Name"]),
        "customer_address": "",
        "account_number": between(t, "Account Number", ["Uncleared Balance"]),
        "account_type": "ACCOUNT STATEMENT - " + between(t, "Branch Name:", ["Available Balance"]).title(),
        "currency": between(t, "Ccy", ["Balance Brought Forward", "Start Date"]),
        "statement_period_start": f(m.group(1)) if m else "",
        "statement_period_end": f(m2.group(1)) if m2 else "",
        "opening_balance": num(between(t, "Balance Brought Forward:", ["Start Date", "Closing Balance"])),
        "closing_balance": num(between(t, "Closing Balance", ["End Date"])),
        "available_balance": num(between(t, "Available Balance", ["Account Number"])),
        "total_credit": None, "total_debit": None,
    }


def meta_eco(doc):
    t = doc[0].get_text()
    m = re.search(r"Statement Period\s*\n?\s*(\d{2}-[A-Z]{3}-\d{4})\s*To\s*(\d{2}-[A-Z]{3}-\d{4})", t)
    return {
        "account_name": "AVE MARIA SCHOOL LIMITED",
        "customer_address": between(t, "AVE MARIA SCHOOL LIMITED", ["STATEMENT OF ACCOUNT", "1"]),
        "account_number": between(t, "Cust Ac No", ["Ccy"]),
        "account_type": "CURRENT ACCOUNT",
        "currency": between(t, "Ccy", ["Opening Balance"]),
        "statement_period_start": fmt_date_slashmon(m.group(1).replace("-", "/")) if m else "",
        "statement_period_end": fmt_date_slashmon(m.group(2).replace("-", "/")) if m else "",
        "opening_balance": num(between(t, "Opening Balance", ["Closing Balance"])),
        "closing_balance": num(between(t, "Closing Balance", ["Cleared Balance"])),
        "available_balance": num(between(t, "Cleared Balance", ["Uncleared Effects"])),
        "total_debit": num(between(t, "Total Withdrawals", ["Total Deposits"])),
        "total_credit": num(between(t, "Total Deposits", ["Statement Period"])),
    }


def meta_momo(doc):
    p = doc[0]
    words = p.get_text("words")
    def near(label, xlo, xhi, pat):
        ys = [w[1] for w in words if w[4] == label]
        if not ys:
            return ""
        y0 = ys[0]
        for w in words:
            if w[1] > y0 and xlo <= w[0] <= xhi and re.match(pat, w[4]):
                return w[4]
        return ""
    m_from = near("From_date", 760, 800, r"\d{1,2}/\d{1,2}/\d{4}")
    m_to = near("To_Date", 808, 850, r"\d{1,2}/\d{1,2}/\d{4}")
    num = near("Customer_Number", 480, 545, r"\d{9,15}")
    label_y = next((w[1] for w in words if w[4] == "profile"), None)
    prof = [w[4] for w in sorted(words, key=lambda w: (w[1], w[0]))
            if 718 <= w[0] < 770 and label_y is not None and label_y + 2 < w[1] < label_y + 45]
    f = lambda s: datetime.strptime(s, "%m/%d/%Y").strftime("%d-%b-%Y") if s else ""
    return {
        "account_name": "AVE MARIA SCHOOL LIMITED (MTN MoMo Merchant)",
        "customer_address": "",
        "account_number": num,
        "account_type": "MTN Mobile Money - " + " ".join(prof),
        "currency": "GHS",
        "statement_period_start": f(m_from),
        "statement_period_end": f(m_to),
        "opening_balance": None, "closing_balance": None,
        "available_balance": None, "total_credit": None, "total_debit": None,
    }


# ------------------------------------------------------- categorization ----
def categorize_gtb(raw, dr=0.0, cr=0.0):
    d = raw.upper()
    if "CASH DEP" in d:
        return "Cash Deposit (3rd Party)"
    if "GIP_" in d or "_GIP" in d:
        return "GhIPSS Credit (Fees)"
    if "RTGSGH" in d:
        return "Interbank Transfer (RTGS)"
    if "E-PRODUCT BUNDLE" in d or "E-BUNDLE" in d:
        return "E-Product Bundle Charge"
    if "GT INWARD CLEARING" in d:
        return "Inward Clearing (Cheque)"
    if "OUT CHQ" in d or "CHQ" in d and dr > 0:
        return "Cheque Payment (Outward)"
    if "COT:" in d:
        return "COT Charge"
    if "STATEMENT CHARGE" in d:
        return "Statement Charge"
    if "WITHDRAWAL" in d or "CASH W/D" in d:
        return "Cash Withdrawal"
    if "TRANSFER" in d or "TRF" in d:
        return "Funds Transfer"
    if cr > 0:
        return "Counter Credit (Deposit)"
    if dr > 0:
        return "Other Debit"
    return "Other"


def categorize_adb(raw, dr=0.0, cr=0.0):
    d = raw.upper()
    if "CASH DEPOSIT BY" in d:
        return "Cash Deposit (3rd Party)"
    if "CHEQUE WITHDRAWAL BY" in d:
        return "Cheque Withdrawal (Self)"
    if "CREDIT INTEREST" in d:
        return "Credit Interest"
    if "T-BILL" in d:
        return "T-Bill Transaction"
    if "COMMISSION ON NRT" in d:
        return "Interbank Transfer Charge (NRT)"
    if "SALARY" in d:
        return "Salary Payment (FTM)"
    if "MOMO" in d:
        return "MoMo Transfer"
    if "ACH NRT" in d or "ACH CREDIT" in d:
        return "ACH/Interbank Credit"
    if "NEGOTIATED BENEFITS TRUST" in d:
        return "Transfer (Negotiated Benefits Trust)"
    if re.search(r"109101001112890\d\s*-\s*TO\s*-", d):
        return "Internal Transfer (Own ADB Accounts)"
    if "MTN-PULL" in d:
        return "MoMo Pull Debit"
    if "M_APP" in d and "INTERNAL" in d:
        return "Mobile App Transfer"
    if "GIP" in d and "INCOMING" in d:
        return "GhIPSS Incoming Credit"
    if "INWARD CLEARING CHEQUE" in d:
        return "Inward Clearing (Cheque Paid)"
    if "ADB CHQ NO" in d:
        return "Cheque Payment (ADB)"
    if "FUNDS TRANSFER FROM" in d or "FUND TRANSFER FROM" in d:
        return "Funds Transfer (Credit)"
    if "ACCOUNT MAINTENANCE FEE" in d:
        return "Account Maintenance Fee"
    if "EBANKING SERVICE FEE" in d:
        return "E-Banking Service Fee"
    if "COT" in d:
        return "COT Charge"
    if cr > 0:
        return "Interbank Credit"
    if dr > 0:
        return "Other Debit"
    return "Other"


def categorize_omni(raw, dr=0.0, cr=0.0):
    d = raw.upper()
    if "CASH DEP BY" in d:
        return "Cash Deposit (3rd Party)"
    if "CHEQUE BOOK CHARGE" in d:
        return "Cheque Book Issuance Fee"
    if "INWARD CHQ NO" in d or "INWARD OMNIBSIC CHQ" in d or "INWARD EXPRESS CHQ" in d:
        return "Cheque Payment (Own)"
    if "SSNIT" in d:
        return "Direct Debit (SSNIT)"
    if "MONTHLY SERVICE CHARGE" in d:
        return "Monthly Service Charge"
    if "MOMO POSTRXN" in d:
        return "MoMo Settlement Credit (OmniBSIC)"
    if "ACH NRT" in d or "NRT-TRF" in d:
        return "Interbank Transfer (ACH/NRT)"
    if "CASH WD" in d or "CASH WITHDRAWAL" in d:
        return "Cash Withdrawal"
    if "PYMT" in d:
        return "Payment"
    if cr > 0:
        return "Interbank Credit"
    if dr > 0:
        return "Other Debit"
    return "Other"


def categorize_eco(raw, dr, cr):
    d = raw.upper()
    if cr < 0 or dr < 0:
        base = "Cash Deposit" if "DEP" in d or "RECEIVED" in d else "Transaction"
        return f"{base} Reversal (Negative Entry)"
    if re.search(r"DEP\s*(BY|-)", d) or "CASH DEPOSITED" in d:
        return "Cash Deposit (3rd Party)"
    if "CHEQUE DEPOSIT" in d:
        return "Cheque Deposit (Ecobank)"
    if "ECOBANK CHQ NO" in d and "RECEIVED" in d:
        return "Cheque Deposit (Ecobank)"
    if "CHEQUE WITHDRAWAL" in d:
        return "Cheque Withdrawal (Self)"
    if "GIP INCOMING" in d:
        return "GhIPSS Incoming Credit"
    if "SALARY" in d:
        return "Salary Payment"
    if "B/O" in d and cr > 0:
        return "Interbank Credit"
    if "CASH DEP" in d:
        return "Cash Deposit"
    if "CHARGE" in d or "FEE" in d or "COMM" in d:
        return "Bank Charge"
    if "LOAN" in d:
        return "Loan Transaction"
    if cr > 0:
        return "Interbank Credit"
    if dr > 0:
        return "Other Debit"
    return "Other"


def categorize_momo(type_, dr, cr):
    t = (type_ or "").upper()
    side = "Credit" if cr > 0 else "Debit"
    if t.startswith("TRANSFER"):
        return f"MoMo Transfer ({side})"
    if "PAYMENT" in t:
        return f"MoMo Payment ({side})"
    if "FLOAT" in t:
        return f"MoMo Float Transfer ({side})"
    if "PULL" in t:
        return f"MoMo Pull ({side})"
    return f"MoMo {t.title() or 'Transaction'} ({side})"


def party_eco(raw, dr, cr):
    m = re.search(r"DEP (?:BY |-)(.*?)(?: IFO| RECEIVED| REF|$)", raw, re.I)
    if m:
        return m.group(1).strip(" -")
    m = re.search(r"PAID TO (.*?)$", raw, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"B/O (.*?)(?: IFO| IRO| RECEIVED| $|$)", raw, re.I)
    if m and cr > 0:
        return m.group(1).strip()
    return ""


def party_momo(r):
    if r["deposit_cr"] > 0:
        return (r["from_name"] or r["from_phone"]).upper()
    return (r["to_msisdn"] or r["after_name"]).upper()




def process(path):
    """Detect bank from page 1 and run the right parser. Returns dict(payload, issues, stats) or None."""
    doc = pymupdf.open(path)
    t0 = doc[0].get_text()
    base = os.path.splitext(os.path.basename(path))[0]
    if "STATEMENT\nOF\nACCOUNT" in t0 or t0.startswith("NOT FOR VISA"):
        return finish("ADB", base, doc, meta_adb(doc), ADB().parse(doc), ADB().finalize)
    if "CUSTOMER STATEMENT" in t0:
        rows = GTB().parse(doc)
        meta = meta_gt(doc)
        m = re.search(r"TOTAL:\s*([\d,]+\.\d{2})\s*([\d,]+\.\d{2})", doc[-1].get_text())
        if m:
            meta["total_debit"] = num(m.group(1))
            meta["total_credit"] = num(m.group(2))
        return finish("GTB", base, doc, meta, rows, GTB().finalize)
    if "ACCOUNT STATEMENT" in t0:
        return finish("OMNI", base, doc, meta_omni(doc), OMNI().parse(doc), OMNI().finalize)
    if "STATEMENT OF ACCOUNT" in t0:
        return finish("ECO", base, doc, meta_eco(doc), ECO().parse(doc), ECO().finalize)
    if "MTN MoMo Statement" in t0:
        rows = parse_momo(doc)
        meta = meta_momo(doc)
        meta["opening_balance"] = rows[0]["bal_before"]
        meta["closing_balance"] = rows[-1]["balance"]
        issues, stats = verify_momo(rows)
        decorate_momo(rows)
        return {"payload": build_payload("MTN Mobile Money (MoMo)", meta, rows),
                "issues": issues, "stats": stats, "base": base}
    return None


def verify(rows, opening, closing=None, tot_dr=None, tot_cr=None):
    """Resolve single-sided amounts by delta; check running balance. Returns (issues, stats)."""
    issues = []
    run = opening
    bad = 0
    resolved = 0
    for r in rows:
        d, c = r.get("withdrawal_dr") or 0.0, r.get("deposit_cr") or 0.0
        delta = round(r["balance"] - run, 2)
        if (d is None or c is None):
            side_amt = c if c is not None else d
            if delta >= 0:
                c, d = side_amt, 0.0
            else:
                d, c = side_amt, 0.0
            resolved += 1
        d, c = round(d or 0, 2), round(c or 0, 2)
        run = round(run + c - d, 2)
        if abs(run - r["balance"]) > 0.005:
            bad += 1
            if bad <= 5:
                issues.append(f"balance mismatch @{r.get('trans')}: expected {run:,.2f} got {r['balance']:,.2f} ({r.get('raw_details','')[:50]})")
        r["withdrawal_dr"], r["deposit_cr"] = d, c
    if bad:
        issues.insert(0, f"{bad}/{len(rows)} running-balance mismatches")
    td = round(sum(r["withdrawal_dr"] for r in rows), 2)
    tc = round(sum(r["deposit_cr"] for r in rows), 2)
    if tot_dr is not None and abs(td - tot_dr) > 0.01:
        issues.append(f"sumDR {td:,.2f} != stated {tot_dr:,.2f}")
    if tot_cr is not None and abs(tc - tot_cr) > 0.01:
        issues.append(f"sumCR {tc:,.2f} != stated {tot_cr:,.2f}")
    if closing is not None and abs(run - closing) > 0.01:
        issues.append(f"final balance {run:,.2f} != stated closing {closing:,.2f}")
    stats = {"rows": len(rows), "bad": bad, "resolved": resolved,
             "total_dr": td, "total_cr": tc, "final": run}
    return issues, stats


def build_payload(bank, meta, rows):
    tx = []
    for r in rows:
        tx.append({
            "trans_date": r["trans"], "ref_number": r.get("ref_number", "") or r.get("ref", "") or r.get("refid", ""),
            "raw_details": r["raw_details"], "transaction_category": r.get("cat", ""),
            "party_name": r.get("party", ""), "reference_id": r.get("refid", ""),
            "value_date": r.get("value_date", r["trans"]),
            "withdrawal_dr": round(float(r["withdrawal_dr"] or 0), 2),
            "deposit_cr": round(float(r["deposit_cr"] or 0), 2),
            "balance": round(float(r["balance"] or 0), 2),
        })
    return {"bank_name": bank, "account_summary": meta, "transactions": tx}


def finish(bank_key, base, doc, meta, rows, finalize):
    rows = finalize(rows)
    if bank_key == "ADB":
        meta["opening_balance"] = round(rows[0]["balance"] - rows[0]["deposit_cr"] + rows[0]["withdrawal_dr"], 2)
        meta["closing_balance"] = rows[-1]["balance"]
        for r in rows:
            r["cat"] = categorize_adb(r["raw_details"], r["withdrawal_dr"], r["deposit_cr"])
            r["party"] = party_adb(r["raw_details"])
            r["refid"] = r.get("ref_number", "")
    elif bank_key == "GTB":
        for r in rows:
            r["cat"] = categorize_gtb(r["raw_details"], r["withdrawal_dr"], r["deposit_cr"])
            r["party"] = party_gtb(r["raw_details"])
            r["refid"] = ""
    elif bank_key == "OMNI":
        for r in rows:
            r["cat"] = categorize_omni(r["raw_details"], r["withdrawal_dr"], r["deposit_cr"])
            r["party"] = party_omni(r["raw_details"])
            r["refid"] = ""
    elif bank_key == "ECO":
        run = meta["opening_balance"]
        for r in rows:
            delta = round(r["balance"] - run, 2)
            d0, c0 = r["withdrawal_dr"] or 0.0, r["deposit_cr"] or 0.0
            # keep printed values when consistent with the balance movement
            # (incl. negative credits/debits as printed for reversals)
            printed_ok = (d0 != 0 and abs(d0 + delta) < 0.005) or \
                         (c0 != 0 and abs(c0 - delta) < 0.005) or \
                         (d0 == 0 and c0 == 0 and delta == 0)
            if not printed_ok:
                if delta >= 0:
                    r["deposit_cr"], r["withdrawal_dr"] = round(delta, 2), 0.0
                else:
                    r["withdrawal_dr"], r["deposit_cr"] = round(-delta, 2), 0.0
            run = r["balance"]
        for r in rows:
            r["cat"] = categorize_eco(r["raw_details"], r["withdrawal_dr"], r["deposit_cr"])
            r["party"] = party_eco(r["raw_details"], r["withdrawal_dr"], r["deposit_cr"])
            m = ECO_REF.search(r["raw_details"])
            r["refid"] = m.group(1) if m else ""
    issues, stats = verify(rows, meta["opening_balance"], meta.get("closing_balance"),
                           meta.get("total_debit"), meta.get("total_credit"))
    payload = build_payload({"ADB": "ADB Bank (Agricultural Development Bank)",
                             "GTB": "Guaranty Trust Bank (Ghana) Ltd",
                             "OMNI": "OmniBSIC Bank Ghana Ltd",
                             "ECO": "Ecobank Ghana PLC"}[bank_key], meta, rows)
    return {"payload": payload, "issues": issues, "stats": stats, "base": base}


def verify_momo(rows):
    issues, bad, dm = [], 0, 0
    prev_after = None
    for i, r in enumerate(rows):
        if prev_after is not None and abs(r["bal_before"] - prev_after) > 0.005:
            bad += 1
            if bad <= 5:
                issues.append(f"continuity break row {i}: prev after {prev_after:,.2f} vs bal before {r['bal_before']:,.2f}")
        if not r["delta_match"]:
            dm += 1
        prev_after = r["balance"]
    if dm:
        issues.append(f"{dm} rows where |balance delta| != amount (fee/reversal rows)")
    td = round(sum(r["withdrawal_dr"] for r in rows), 2)
    tc = round(sum(r["deposit_cr"] for r in rows), 2)
    return issues, {"rows": len(rows), "bad": bad, "resolved": dm,
                    "total_dr": td, "total_cr": tc, "final": rows[-1]["balance"]}


def decorate_momo(rows):
    for r in rows:
        r["cat"] = categorize_momo(r["type"], r["withdrawal_dr"], r["deposit_cr"])
        r["party"] = party_momo(r)
        r["refid"] = r["ref"]
        r["ref_number"] = r["ref"]


# party extractors
def _after(raw, key, stop=None):
    i = raw.upper().find(key)
    if i < 0:
        return ""
    rest = raw[i + len(key):]
    if stop:
        for s in stop:
            j = rest.upper().find(s)
            if j >= 0:
                rest = rest[:j]
    return rest.strip(" ,.-")


def party_gtb(raw):
    bo = _after(raw, "B/O ", [" The zone", "The zone", "Remarks"])
    if bo:
        bo = re.sub(r"\s+", " ", bo).upper()[:60]
        half = len(bo) // 2
        if bo[:half] == bo[half:]:
            bo = bo[:half]
        return bo
    return ""


def party_adb(raw):
    d = raw.upper()
    if "IFO " in d:
        return _after(raw, "IFO ").upper().replace(" FOR", "", 1).strip()[:60]
    if "B/O " in d:
        return _after(raw, "B/O ").upper()[:60]
    if "FROM " in d:
        return _after(raw, "FROM ").upper()[:60]
    if "MTN-PULL" in d:
        m = re.search(r"MTN-PULL\s+(\d+)", raw)
        return f"MTN MOMO {m.group(1)}" if m else "MTN MOMO"
    return ""


def party_omni(raw):
    d = raw.upper()
    if " BY " in d:
        return _after(raw, " BY ", [" AT "]).upper()[:60]
    if "B/O" in d:
        return _after(raw, "B/O ").upper()[:60]
    if "MOMO POSTRXN" in d:
        return "MTN MOMO (SETTLEMENT)"
    return ""
