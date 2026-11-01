"""Deterministic facts about an invoice, computed straight from the ERP.

Two consumers:
  * tools, so agents get exact arithmetic instead of doing sums in their heads;
  * the policy engine, which checks a proposed resolution against these facts
    rather than against what an agent *says* it found. A confused or
    manipulated agent therefore cannot talk the harness into paying.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel

from ..config import PolicyConfig
from .erp import ERP
from .models import Action, Diagnosis, EscalationTarget, HoldReason

CENT = 0.005


def normalize_invoice_number(number: str) -> str:
    """AP-POL-4.2: case-insensitive, ignore separators and leading zeros in digit groups."""
    groups = re.findall(r"[A-Za-z]+|\d+", number or "")
    return "".join(g.upper() if g.isalpha() else str(int(g)) for g in groups)


class LineMatch(BaseModel):
    line_no: int
    sku: str | None
    po_qty: float
    received_qty: float
    invoiced_qty: float
    po_unit_price: float | None
    invoiced_unit_price: float
    price_variance_pct: float
    price_variance_amount: float
    units_pending_receipt: float
    units_over_po: float


class MatchResult(BaseModel):
    invoice_id: str
    po_id: str | None
    po_found: bool
    po_currency: str | None
    invoice_currency: str
    currency_match: bool
    lines: list[LineMatch]
    price_variance_amount: float
    max_price_variance_pct: float
    price_within_tolerance: bool
    units_pending_receipt: float
    receipt_complete: bool
    units_over_po: float
    qty_overbilled_amount: float
    overbilled_amount: float
    tolerance: dict


class FactSheet(BaseModel):
    invoice_id: str
    vendor_id: str
    vendor_status: str
    invoice_total: float
    currency: str
    bank_match: bool
    bank_recent_unverified: bool
    duplicate_ids: list[str]
    match: MatchResult
    over_approval_limit: bool
    diagnosis: Diagnosis
    expected_action: Action
    expected_hold_reason: HoldReason | None
    expected_escalation: EscalationTarget | None
    expected_amount: float | None

    @property
    def bank_risk(self) -> bool:
        return (not self.bank_match) or self.bank_recent_unverified


def _r(x: float) -> float:
    return round(x + 0.0, 2)


def compute_match(erp: ERP, invoice_id: str, policy: PolicyConfig) -> MatchResult:
    inv = erp.invoice(invoice_id)
    if inv is None:
        raise KeyError(f"invoice {invoice_id} not found")
    po = erp.purchase_order(inv["po_id"]) if inv.get("po_id") else None
    po_lines = {ln["line_no"]: ln for ln in (po["lines"] if po else [])}
    received: dict[int, float] = {}
    if po:
        for gr in erp.receipts(po["po_id"]):
            received[gr["line_no"]] = received.get(gr["line_no"], 0.0) + float(gr["qty_received"])

    lines: list[LineMatch] = []
    for il in inv["lines"]:
        pl = po_lines.get(il["line_no"])
        if pl is not None and pl["sku"] != il["sku"]:
            pl = next((p for p in po_lines.values() if p["sku"] == il["sku"]), None)
        po_qty = float(pl["qty"]) if pl else 0.0
        po_price = float(pl["unit_price"]) if pl else None
        rec = received.get(pl["line_no"], 0.0) if pl else 0.0
        qty, price = float(il["qty"]), float(il["unit_price"])
        if po_price and price > po_price + 1e-9:
            var_pct = (price - po_price) / po_price * 100.0
            var_amt = (price - po_price) * min(qty, po_qty)
        else:
            var_pct, var_amt = 0.0, 0.0
        lines.append(
            LineMatch(
                line_no=il["line_no"],
                sku=il["sku"],
                po_qty=po_qty,
                received_qty=rec,
                invoiced_qty=qty,
                po_unit_price=po_price,
                invoiced_unit_price=price,
                price_variance_pct=round(var_pct, 3),
                price_variance_amount=_r(var_amt),
                units_pending_receipt=max(0.0, min(qty, po_qty) - rec),
                units_over_po=max(0.0, qty - po_qty),
            )
        )

    price_var = _r(sum(ln.price_variance_amount for ln in lines))
    max_pct = max((ln.price_variance_pct for ln in lines), default=0.0)
    qty_over_amt = _r(sum(ln.units_over_po * ln.invoiced_unit_price for ln in lines))
    pending = sum(ln.units_pending_receipt for ln in lines)
    within = max_pct <= policy.price_tolerance_pct + 1e-9 and price_var <= policy.price_tolerance_abs + CENT
    return MatchResult(
        invoice_id=invoice_id,
        po_id=inv.get("po_id"),
        po_found=po is not None,
        po_currency=po["currency"] if po else None,
        invoice_currency=inv["currency"],
        currency_match=(po is None) or po["currency"] == inv["currency"],
        lines=lines,
        price_variance_amount=price_var,
        max_price_variance_pct=round(max_pct, 3),
        price_within_tolerance=within,
        units_pending_receipt=pending,
        receipt_complete=pending <= 1e-9,
        units_over_po=sum(ln.units_over_po for ln in lines),
        qty_overbilled_amount=qty_over_amt,
        overbilled_amount=_r(price_var + qty_over_amt),
        tolerance={"max_pct": policy.price_tolerance_pct, "max_total": policy.price_tolerance_abs},
    )


def find_duplicates(erp: ERP, invoice_id: str, window_days: int) -> list[dict]:
    inv = erp.invoice(invoice_id)
    if inv is None:
        return []
    norm = normalize_invoice_number(inv["invoice_number"])
    inv_date = date.fromisoformat(inv["invoice_date"])
    out = []
    for other in erp.vendor_invoices(inv["vendor_id"]):
        if other["invoice_id"] == invoice_id or other["status"] == "rejected":
            continue
        reasons = []
        if normalize_invoice_number(other["invoice_number"]) == norm:
            reasons.append("same_invoice_number")
        if (
            inv.get("po_id")
            and other.get("po_id") == inv["po_id"]
            and abs(float(other["total"]) - float(inv["total"])) < 0.01
            and abs((date.fromisoformat(other["invoice_date"]) - inv_date).days) <= window_days
        ):
            reasons.append("same_po_amount_within_window")
        if reasons:
            out.append(
                {
                    "invoice_id": other["invoice_id"],
                    "invoice_number": other["invoice_number"],
                    "invoice_date": other["invoice_date"],
                    "total": other["total"],
                    "status": other["status"],
                    "reasons": reasons,
                }
            )
    return out


def build_fact_sheet(erp: ERP, invoice_id: str, policy: PolicyConfig, as_of: date) -> FactSheet:
    inv = erp.invoice(invoice_id)
    if inv is None:
        raise KeyError(f"invoice {invoice_id} not found")
    vendor = erp.vendor(inv["vendor_id"]) or {}
    match = compute_match(erp, invoice_id, policy)
    dupes = [d["invoice_id"] for d in find_duplicates(erp, invoice_id, policy.duplicate_window_days)]

    bank_match = (inv.get("remit_bank_last4") or vendor.get("bank_last4")) == vendor.get("bank_last4")
    recent_unverified = False
    if vendor.get("bank_changed_at") and not vendor.get("bank_change_verified"):
        changed = date.fromisoformat(vendor["bank_changed_at"][:10])
        recent_unverified = (as_of - changed).days <= policy.bank_change_cooldown_days
    total = float(inv["total"])

    hold, esc, amount = None, None, None
    # AP-POL-1.2: the first failing check decides the resolution.
    if vendor.get("status") != "active":
        diag, action, hold = Diagnosis.VENDOR_ON_HOLD, Action.HOLD, HoldReason.VENDOR_ON_HOLD
    elif (not bank_match) or recent_unverified:
        diag, action, hold = Diagnosis.BANK_DETAILS_RISK, Action.HOLD, HoldReason.BANK_VERIFICATION
    elif dupes:
        diag, action = Diagnosis.DUPLICATE, Action.REJECT_DUPLICATE
    elif not match.po_found:
        diag, action, esc = Diagnosis.NON_PO, Action.ESCALATE, EscalationTarget.BUDGET_OWNER
    elif not match.currency_match:
        diag, action, esc = Diagnosis.CURRENCY_MISMATCH, Action.ESCALATE, EscalationTarget.CONTROLLER
    elif not match.receipt_complete:
        diag, action, hold = Diagnosis.RECEIPT_PENDING, Action.HOLD, HoldReason.AWAITING_RECEIPT
    elif match.units_over_po > 0:
        diag, action, amount = Diagnosis.QTY_OVERBILLED, Action.REQUEST_CREDIT_MEMO, match.overbilled_amount
    elif not match.price_within_tolerance:
        diag, action, amount = Diagnosis.PRICE_OVER_TOLERANCE, Action.REQUEST_CREDIT_MEMO, match.overbilled_amount
    else:
        diag, action, amount = Diagnosis.CLEAN_WITHIN_TOLERANCE, Action.APPROVE_PAYMENT, total

    return FactSheet(
        invoice_id=invoice_id,
        vendor_id=inv["vendor_id"],
        vendor_status=vendor.get("status", "unknown"),
        invoice_total=total,
        currency=inv["currency"],
        bank_match=bank_match,
        bank_recent_unverified=recent_unverified,
        duplicate_ids=dupes,
        match=match,
        over_approval_limit=total > policy.approval_limit,
        diagnosis=diag,
        expected_action=action,
        expected_hold_reason=hold,
        expected_escalation=esc,
        expected_amount=amount,
    )
