"""Data minimisation: mask bank account numbers before text reaches a model.

The ERP stores only the last four digits of remit-to accounts. OCR'd invoice
attachments can still carry full account numbers or IBANs, so they are masked
at the tool boundary. Models only ever need the last four digits to compare
against the vendor master.
"""

from __future__ import annotations

import re

IBAN = re.compile(r"\b([A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?)\b")
# 8+ digit runs that are not part of a formatted amount like 12,345,678.90
LONG_DIGITS = re.compile(r"(?<!\d)(?<!\d[.,])(\d{8,})(?!\d)(?![.,]\d)")


def _mask(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return f"****{digits[-4:]}" if len(digits) >= 4 else "****"


def redact(text: str) -> tuple[str, int]:
    """Return (redacted_text, number_of_redactions)."""
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        raw = m.group(1)
        if len(re.sub(r"\D", "", raw)) < 8:
            return m.group(0)
        count += 1
        return _mask(raw)

    text = IBAN.sub(repl, text or "")
    text = LONG_DIGITS.sub(repl, text)
    return text, count
