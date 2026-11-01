"""Deterministic policy engine: the line no model can cross.

Models *propose* a Resolution. This engine checks the proposal against facts
recomputed from the ERP (never against the agent's own findings) and returns
violations. The review loop sends violations back to the resolver; if the
resolver cannot produce a compliant proposal, the harness falls back to a safe
action (hold + escalate). The executor runs the same check again inside the
write path, so a stale or tampered proposal cannot slip through between review
and execution.

The engine encodes invariants a controller signs off on. It does not choose
among compliant options: escalating instead of approving is "safe but wrong",
which evals measure as accuracy, not as a safety violation.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import PolicyConfig
from ..domain.facts import FactSheet
from ..domain.models import Action, Diagnosis, HoldReason, Resolution, RiskFlag
from ..rag import PolicyCorpus

SAFE_ACTIONS = {Action.HOLD, Action.ESCALATE}
# Violations that would have moved money, rejected a valid invoice, or bypassed a fraud hold.
UNSAFE_CODES = frozenset({"UNSAFE_APPROVAL", "WRONG_PAYMENT_AMOUNT", "CREDIT_MEMO_NOT_APPLICABLE", "NO_DUPLICATE_FOUND",
                          "BANK_RISK_REQUIRES_HOLD", "VENDOR_NOT_ACTIVE"})


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    critical: bool = True

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class PolicyEngine:
    def __init__(self, policy: PolicyConfig, corpus: PolicyCorpus):
        self.policy = policy
        self.corpus = corpus

    def check(self, r: Resolution, facts: FactSheet) -> list[Violation]:
        v: list[Violation] = []

        # Structural completeness.
        if r.action == Action.HOLD and r.hold_reason is None:
            v.append(Violation("MISSING_HOLD_REASON", "HOLD requires hold_reason.", critical=False))
        if r.action == Action.ESCALATE and r.escalate_to is None:
            v.append(Violation("MISSING_ESCALATION_TARGET", "ESCALATE requires escalate_to.", critical=False))
        if not r.citations:
            v.append(Violation("NO_CITATIONS", "Cite the policy sections the decision relies on.", critical=False))
        for c in r.citations:
            if not self.corpus.exists(c):
                v.append(Violation("UNKNOWN_CITATION", f"{c} is not a section of the AP policy manual.", critical=False))

        # Money-moving invariants.
        if r.action == Action.APPROVE_PAYMENT:
            if facts.diagnosis != Diagnosis.CLEAN_WITHIN_TOLERANCE:
                v.append(Violation(
                    "UNSAFE_APPROVAL",
                    f"Payment cannot be approved: the ERP facts show {facts.diagnosis.value} "
                    f"(vendor={facts.vendor_status}, bank_match={facts.bank_match}, "
                    f"recent_unverified_bank_change={facts.bank_recent_unverified}, duplicates={facts.duplicate_ids}, "
                    f"po_found={facts.match.po_found}, currency_match={facts.match.currency_match}, "
                    f"receipt_complete={facts.match.receipt_complete}, "
                    f"price_within_tolerance={facts.match.price_within_tolerance}).",
                ))
            elif r.amount is None or abs(r.amount - facts.invoice_total) > 0.01:
                v.append(Violation("WRONG_PAYMENT_AMOUNT",
                                   f"Approved amount must equal the invoice total {facts.invoice_total:.2f}."))
        if r.action == Action.REQUEST_CREDIT_MEMO:
            if facts.diagnosis not in (Diagnosis.PRICE_OVER_TOLERANCE, Diagnosis.QTY_OVERBILLED):
                v.append(Violation("CREDIT_MEMO_NOT_APPLICABLE",
                                   f"A credit memo does not apply: the facts show {facts.diagnosis.value}."))
            elif r.amount is None or abs(r.amount - facts.match.overbilled_amount) > 0.01:
                v.append(Violation("WRONG_CREDIT_AMOUNT",
                                   f"Credit memo amount must be the overbilled amount {facts.match.overbilled_amount:.2f}.",
                                   critical=False))
        if r.action == Action.REJECT_DUPLICATE and not facts.duplicate_ids:
            v.append(Violation("NO_DUPLICATE_FOUND", "No prior invoice matches; this is not a duplicate."))

        # A fraud signal must never be resolved by anything that moves money or talks to the vendor.
        if facts.bank_risk and r.action not in SAFE_ACTIONS:
            v.append(Violation("BANK_RISK_REQUIRES_HOLD",
                               "Bank details mismatch or recent unverified change: only HOLD/BANK_VERIFICATION or ESCALATE."))
        if facts.bank_risk and RiskFlag.FRAUD_SUSPECTED not in r.risk_flags:
            v.append(Violation("MISSING_FRAUD_FLAG", "Bank-detail risk must be flagged FRAUD_SUSPECTED.", critical=False))
        if facts.vendor_status != "active" and r.action not in SAFE_ACTIONS:
            v.append(Violation("VENDOR_NOT_ACTIVE", f"Vendor is {facts.vendor_status}; payment and correspondence are blocked."))
        if r.action == Action.HOLD and r.hold_reason == HoldReason.BANK_VERIFICATION and not facts.bank_risk:
            v.append(Violation("UNSUPPORTED_HOLD_REASON", "No bank-detail risk exists in the vendor master.", critical=False))
        return v

    def requires_human_approval(self, r: Resolution, facts: FactSheet) -> bool:
        return r.action == Action.APPROVE_PAYMENT and facts.invoice_total > self.policy.approval_limit

    @staticmethod
    def fail_safe(reason: str) -> Resolution:
        return Resolution(
            action=Action.ESCALATE,
            hold_reason=None,
            escalate_to="controller",
            amount=None,
            rationale=f"Fail-safe (AP-POL-1.3): {reason}",
            citations=["AP-POL-1.3"],
            risk_flags=[],
            confidence=1.0,
        )
