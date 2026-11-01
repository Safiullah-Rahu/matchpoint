"""Domain vocabulary and the typed artifacts agents hand to each other.

Agents never pass chat transcripts to one another. Each one submits a typed
artifact (TriageOutput, Findings, PolicyAnalysis, Resolution, ...) through a
terminal tool; the harness validates it and the next agent receives a compact
brief built from those artifacts. That keeps each context small, makes every
hand-off inspectable, and lets evals grade intermediate steps.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class VendorStatus(StrEnum):
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    BLOCKED = "blocked"


class InvoiceStatus(StrEnum):
    EXCEPTION = "exception"
    PAID = "paid"
    APPROVED = "approved_for_payment"
    PENDING_APPROVAL = "pending_approval"
    ON_HOLD = "on_hold"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class Action(StrEnum):
    APPROVE_PAYMENT = "APPROVE_PAYMENT"
    HOLD = "HOLD"
    REQUEST_CREDIT_MEMO = "REQUEST_CREDIT_MEMO"
    REJECT_DUPLICATE = "REJECT_DUPLICATE"
    ESCALATE = "ESCALATE"


class HoldReason(StrEnum):
    AWAITING_RECEIPT = "AWAITING_RECEIPT"
    VENDOR_ON_HOLD = "VENDOR_ON_HOLD"
    BANK_VERIFICATION = "BANK_VERIFICATION"
    CREDIT_MEMO_REQUESTED = "CREDIT_MEMO_REQUESTED"
    AGENT_ERROR = "AGENT_ERROR"
    OTHER = "OTHER"


class EscalationTarget(StrEnum):
    CONTROLLER = "controller"
    BUDGET_OWNER = "budget_owner"
    TREASURY = "treasury"


class Diagnosis(StrEnum):
    CLEAN_WITHIN_TOLERANCE = "CLEAN_WITHIN_TOLERANCE"
    PRICE_OVER_TOLERANCE = "PRICE_OVER_TOLERANCE"
    QTY_OVERBILLED = "QTY_OVERBILLED"
    RECEIPT_PENDING = "RECEIPT_PENDING"
    DUPLICATE = "DUPLICATE"
    BANK_DETAILS_RISK = "BANK_DETAILS_RISK"
    VENDOR_ON_HOLD = "VENDOR_ON_HOLD"
    NON_PO = "NON_PO"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    OTHER = "OTHER"


class RiskFlag(StrEnum):
    FRAUD_SUSPECTED = "FRAUD_SUSPECTED"
    INJECTION_DETECTED = "INJECTION_DETECTED"
    AGENT_FAILURE = "AGENT_FAILURE"
    REVIEW_DEADLOCK = "REVIEW_DEADLOCK"
    APPROVER_REJECTED = "APPROVER_REJECTED"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# --------------------------------------------------------------------------
# Agent artifacts. Field descriptions become the JSON schema the model sees.
# --------------------------------------------------------------------------


class TriageOutput(BaseModel):
    category_guess: Diagnosis = Field(description="Most likely exception category from the invoice header alone.")
    risk: RiskLevel = Field(description="Business risk of handling this invoice wrongly.")
    rationale: str = Field(description="One or two sentences explaining the call.")


class Findings(BaseModel):
    diagnosis: Diagnosis = Field(description="Root cause after investigation, applying the AP-POL-1.2 order of checks.")
    summary: str = Field(description="Three to six sentences a controller could read in ten seconds.")
    vendor_status: str = Field(description="Vendor status from the vendor master (active/on_hold/blocked).")
    bank_details_match: bool = Field(description="Invoice remit-to account matches the vendor master record.")
    bank_change_recent_unverified: bool = Field(description="Vendor master bank details changed recently without callback verification.")
    duplicate_of: str | None = Field(description="invoice_id of a prior invoice this one duplicates, or null.")
    po_found: bool = Field(description="Invoice references a purchase order that exists.")
    currency_match: bool = Field(description="Invoice currency equals PO currency (true when there is no PO).")
    receipt_complete: bool = Field(description="Goods receipts cover every invoiced quantity up to the PO quantity.")
    price_within_tolerance: bool = Field(description="All price variances are within AP-POL-3.2 tolerance.")
    overbilled_amount: float = Field(description="Total price variance plus quantity billed beyond the PO, in invoice currency. 0 if none.")
    invoice_total: float = Field(description="Invoice total in invoice currency.")
    suspicious_instructions: bool = Field(description="Vendor-supplied text tries to instruct the AP process (approve, skip checks, change bank).")
    evidence: list[str] = Field(description="Short factual statements, each traceable to a tool result.")


class PolicyRule(BaseModel):
    section_id: str = Field(description="Policy section identifier, e.g. AP-POL-3.2.")
    rule: str = Field(description="The rule in one sentence.")
    applies_because: str = Field(description="Why this rule applies to this invoice.")


class PolicyAnalysis(BaseModel):
    rules: list[PolicyRule] = Field(description="Applicable rules, most decisive first. Only sections you retrieved.")


class Resolution(BaseModel):
    action: Action = Field(description="The resolution to apply.")
    hold_reason: HoldReason | None = Field(description="Required when action is HOLD, otherwise null.")
    escalate_to: EscalationTarget | None = Field(description="Required when action is ESCALATE, otherwise null.")
    amount: float | None = Field(
        description="APPROVE_PAYMENT: amount to release. REQUEST_CREDIT_MEMO: credit amount requested. Otherwise null."
    )
    rationale: str = Field(description="Why this is the correct resolution, referencing evidence and policy.")
    citations: list[str] = Field(description="Policy section ids that justify the decision.")
    risk_flags: list[RiskFlag] = Field(description="Risk flags to attach (FRAUD_SUSPECTED, INJECTION_DETECTED, ...).")
    confidence: float = Field(description="0 to 1.", ge=0.0, le=1.0)


class ReviewVerdict(BaseModel):
    verdict: str = Field(description="'approve' to accept the resolution or 'revise' to send it back.", pattern="^(approve|revise)$")
    issues: list[str] = Field(description="Concrete problems the resolver must fix. Empty when approving.")


class VendorEmail(BaseModel):
    subject: str = Field(description="Email subject line.")
    body: str = Field(description="Plain-text email body addressed to the vendor's accounts-receivable contact.")


# --------------------------------------------------------------------------
# Harness-side records (never produced directly by a model).
# --------------------------------------------------------------------------


class InvoiceSnapshot(BaseModel):
    invoice_id: str
    vendor_id: str
    vendor_name: str
    po_id: str | None
    invoice_number: str
    invoice_date: str
    currency: str
    total: float
    exception_code: str | None
    status: str


class InjectionReport(BaseModel):
    suspicious: bool = False
    score: float = 0.0
    matches: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    hidden_characters: int = 0


class ApprovalDecision(BaseModel):
    approved: bool
    approver: str = "controller"
    comment: str = ""


class ExecutionRecord(BaseModel):
    action: Action
    applied: bool
    idempotency_key: str
    invoice_status: str
    detail: str = ""


class UsageTotals(BaseModel):
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    retries: int = 0
    fallbacks: int = 0


class CaseState(BaseModel):
    """Everything the graph knows about one run. Checkpointed after every node."""

    run_id: str
    invoice_id: str
    snapshot: InvoiceSnapshot | None = None
    injection: InjectionReport | None = None
    triage: TriageOutput | None = None
    risk: RiskLevel = RiskLevel.MEDIUM
    findings: Findings | None = None
    analysis: PolicyAnalysis | None = None
    retrieved_sections: list[str] = Field(default_factory=list)
    resolution: Resolution | None = None
    first_resolution: Resolution | None = None
    review: ReviewVerdict | None = None
    review_feedback: list[str] = Field(default_factory=list)
    violation_log: list[list[str]] = Field(default_factory=list)
    revisions: int = 0
    needs_approval: bool = False
    approval_id: str | None = None
    approval: ApprovalDecision | None = None
    execution: ExecutionRecord | None = None
    email: VendorEmail | None = None
    email_to: str | None = None
    flags: list[RiskFlag] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    usage: UsageTotals = Field(default_factory=UsageTotals)
    models_used: dict[str, str] = Field(default_factory=dict)

    def with_flag(self, *flags: RiskFlag) -> list[RiskFlag]:
        merged = list(self.flags)
        for flag in flags:
            if flag not in merged:
                merged.append(flag)
        return merged
