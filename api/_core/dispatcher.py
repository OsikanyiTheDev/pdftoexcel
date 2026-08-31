"""Sniff bank format from page 1 and dispatch to the right parser."""
import os
import tempfile

import pymupdf

from . import parser_banks, parser_fbn


def extract_any(pdf_bytes: bytes, filename: str = "statement.pdf"):
    """Returns dict: {bank_key, payload, issues, stats, pages} or raises ValueError."""
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    try:
        tmp.write(pdf_bytes)
        tmp.close()
        doc = pymupdf.open(tmp.name)
        if not any(p.get_text().strip() for p in doc):
            raise ValueError("Scanned/image-only PDF (no text layer). OCR is not supported yet.")
        pages = len(doc)
        doc.close()

        t0 = pymupdf.open(tmp.name)[0].get_text()
        if "Please find below your bank statement" in t0 or (
                "FirstBank" in t0 and "Dear " in t0):
            payload, issues, stats = parser_fbn.extract_fbn(tmp.name)
            bank_key = "FBN"
        else:
            r = parser_banks.process(tmp.name)
            if r is None:
                raise ValueError(
                    "Unrecognised statement format. Supported: FBN, GTBank, ADB, OmniBSIC, "
                    "Ecobank, MTN MoMo. Send a sample and the parser can be extended.")
            payload, issues, stats = r["payload"], r["issues"], r["stats"]
            bank_key = {"ADB": "ADB", "GTB": "GTB", "OMNI": "OMNI", "ECO": "ECO"}[
                r["base"][:3].upper()] if False else _bank_key_from_payload(payload)
        if payload is None:
            raise ValueError("Could not find statement table in this PDF.")
        return {"bank_key": bank_key, "payload": payload, "issues": issues,
                "stats": stats, "pages": pages}
    finally:
        os.unlink(tmp.name)


def _bank_key_from_payload(payload):
    name = (payload.get("bank_name") or "").upper()
    if "ADB" in name or "AGRICULTURAL" in name:
        return "ADB"
    if "GUARANTY" in name or "GTB" in name:
        return "GTB"
    if "OMNIBSIC" in name:
        return "OMNI"
    if "ECOBANK" in name:
        return "ECO"
    if "MTN" in name or "MOMO" in name:
        return "MOMO"
    return "OTHER"
