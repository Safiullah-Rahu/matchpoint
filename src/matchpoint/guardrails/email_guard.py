"""Output guard for vendor-facing email drafts (AP-POL-9.1, AP-POL-9.2)."""

from __future__ import annotations

import re

from ..domain.models import VendorEmail
from .redaction import IBAN, LONG_DIGITS

INTERNAL_TERMS = re.compile(
    r"\b(fraud|suspicious|injection|prompt|tolerance|policy engine|AP-POL-\d|risk flag|internal control|threshold)\b", re.I
)


def check_email(email: VendorEmail, *, invoice_number: str, amount: float | None) -> list[str]:
    text = f"{email.subject}\n{email.body}"
    problems = []
    if invoice_number.lower() not in text.lower():
        problems.append(f"Reference the invoice number {invoice_number}.")
    if amount is not None and f"{amount:,.2f}" not in text and f"{amount:.2f}" not in text:
        problems.append(f"State the amount concerned ({amount:,.2f}).")
    if IBAN.search(text) or LONG_DIGITS.search(text):
        problems.append("Remove bank account numbers.")
    if m := INTERNAL_TERMS.search(text):
        problems.append(f"Remove internal wording ({m.group(0)!r}); vendor emails must not reveal controls or suspicions.")
    if len(email.body.split()) > 200:
        problems.append("Keep the body under 200 words.")
    return problems
