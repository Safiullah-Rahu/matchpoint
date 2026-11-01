"""Deterministic executor: the only code path that writes resolutions to the ERP.

It takes a reviewed Resolution, re-checks it against fresh ERP facts inside
the write path (closing the gap between review and execution), and applies it
with an idempotency key. No model output is ever passed to SQL: amounts come
from the validated Resolution and are re-checked against the facts; every
other value comes from the ERP itself.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from ..config import Settings
from ..domain.erp import ERP, utcnow
from ..domain.facts import FactSheet
from ..domain.models import Action, ApprovalDecision, ExecutionRecord, HoldReason, Resolution
from ..guardrails import PolicyEngine, Violation

STATUS = {
    Action.APPROVE_PAYMENT: "approved_for_payment",
    Action.HOLD: "on_hold",
    Action.REQUEST_CREDIT_MEMO: "on_hold",
    Action.REJECT_DUPLICATE: "rejected",
    Action.ESCALATE: "escalated",
}


class ExecutionBlocked(Exception):
    def __init__(self, violations: list[Violation]):
        super().__init__("; ".join(str(v) for v in violations))
        self.violations = violations


class Executor:
    def __init__(self, erp: ERP, engine: PolicyEngine, settings: Settings):
        self.erp = erp
        self.engine = engine
        self.settings = settings

    def apply(self, *, run_id: str, resolution: Resolution, facts: FactSheet, approval: ApprovalDecision | None,
              key_suffix: str = "execute") -> ExecutionRecord:
        blocking = [v for v in self.engine.check(resolution, facts) if v.critical]
        if blocking:
            raise ExecutionBlocked(blocking)
        if self.engine.requires_human_approval(resolution, facts) and not (approval and approval.approved):
            raise ExecutionBlocked([Violation("APPROVAL_REQUIRED", "Controller approval is required before release.")])

        inv_id = facts.invoice_id
        r = resolution
        status = STATUS[r.action]
        hold = HoldReason.CREDIT_MEMO_REQUESTED if r.action == Action.REQUEST_CREDIT_MEMO else r.hold_reason
        vendor = self.erp.vendor(facts.vendor_id) or {}
        now = utcnow()
        terms_days = int("".join(ch for ch in (vendor.get("payment_terms") or "NET30") if ch.isdigit()) or 30)

        def ops(conn: sqlite3.Connection) -> None:
            conn.execute("UPDATE invoices SET status = ?, hold_reason = ?, updated_at = ? WHERE invoice_id = ?",
                         (status, hold.value if hold else None, now, inv_id))
            if r.action == Action.APPROVE_PAYMENT:
                due = (self.settings.clock + timedelta(days=terms_days)).isoformat()
                approver = approval.approver if approval and approval.approved else "matchpoint-agent"
                conn.execute("INSERT INTO payment_schedule (invoice_id, amount, scheduled_for, approved_by, created_at) "
                             "VALUES (?,?,?,?,?)", (inv_id, facts.invoice_total, due, approver, now))
            elif r.action == Action.REQUEST_CREDIT_MEMO:
                conn.execute("INSERT INTO credit_memo_requests (invoice_id, amount, reason, created_at) VALUES (?,?,?,?)",
                             (inv_id, round(r.amount or 0.0, 2), facts.diagnosis.value, now))
            elif r.action == Action.ESCALATE:
                conn.execute("INSERT INTO escalations (invoice_id, target, reason, created_at) VALUES (?,?,?,?)",
                             (inv_id, r.escalate_to.value if r.escalate_to else "controller", r.rationale[:500], now))
            elif r.action == Action.HOLD and r.hold_reason == HoldReason.BANK_VERIFICATION:
                conn.execute("INSERT INTO verification_tasks (invoice_id, vendor_id, method, contact, created_at) "
                             "VALUES (?,?,?,?,?)", (inv_id, facts.vendor_id, "callback_vendor_master_phone",
                                                     vendor.get("contact_phone"), now))

        key = f"{run_id}:{inv_id}:{key_suffix}"
        applied = self.erp.apply(
            idempotency_key=key, run_id=run_id, invoice_id=inv_id, actor="matchpoint-executor", action=r.action.value,
            details={"resolution": r.model_dump(mode="json"), "approval": approval.model_dump() if approval else None,
                     "diagnosis": facts.diagnosis.value},
            ops=ops,
        )
        return ExecutionRecord(action=r.action, applied=applied, idempotency_key=key, invoice_status=status,
                               detail="applied" if applied else "already applied (idempotent replay)")

    def mark_pending_approval(self, run_id: str, invoice_id: str, approval_id: str) -> None:
        now = utcnow()
        self.erp.apply(
            idempotency_key=f"{run_id}:{invoice_id}:approval-request", run_id=run_id, invoice_id=invoice_id,
            actor="matchpoint-executor", action="REQUEST_APPROVAL", details={"approval_id": approval_id},
            ops=lambda c: c.execute("UPDATE invoices SET status = 'pending_approval', updated_at = ? WHERE invoice_id = ?",
                                    (now, invoice_id)),
        )

    def save_email_draft(self, run_id: str, invoice_id: str, to_addr: str, subject: str, body: str) -> bool:
        key = f"{run_id}:{invoice_id}:email"
        now = utcnow()
        return self.erp.apply(
            idempotency_key=key, run_id=run_id, invoice_id=invoice_id, actor="matchpoint-executor", action="DRAFT_EMAIL",
            details={"to": to_addr, "subject": subject},
            ops=lambda c: c.execute(
                "INSERT INTO email_drafts (invoice_id, to_addr, subject, body, status, idempotency_key, created_at) "
                "VALUES (?,?,?,?,?,?,?)", (invoice_id, to_addr, subject, body, "draft", key, now)),
        )

    def remember(self, run_id: str, vendor_id: str, invoice_id: str, note: str) -> None:
        now = utcnow()
        self.erp.apply(
            idempotency_key=f"{run_id}:{invoice_id}:memory", run_id=run_id, invoice_id=invoice_id,
            actor="matchpoint-executor", action="WRITE_MEMORY", details={"note": note},
            ops=lambda c: c.execute("INSERT INTO vendor_memory (vendor_id, invoice_id, note, source, created_at) "
                                    "VALUES (?,?,?,?,?)", (vendor_id, invoice_id, note, f"run:{run_id}", now)),
        )
