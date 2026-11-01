"""The AP exception workflow: six agent roles inside a deterministic graph.

    intake -> triage -> investigate -> policy -> decide -> review --+--> execute -> communicate -> finalize
                                                  ^                 |       ^
                                                  +---- revise -----+       |
                                                                    +-> human_approval (interrupt)
    any node error -> fail_safe (escalate to controller, never pay)

Agents (role, default tier):
  triage (fast)          first look at the header; sets routing risk
  investigator (balanced) gathers evidence with read-only tools -> Findings
  policy_analyst (balanced) retrieves governing policy -> PolicyAnalysis
  resolver (balanced; frontier when high risk) -> Resolution
  reviewer (frontier)    second opinion after the deterministic policy engine
  communicator (fast)    vendor email draft, checked by an output guard

Deterministic components own everything that must not be wrong: injection
screening, fact computation, policy invariants, approval thresholds, ERP
writes, idempotency and memory writes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..domain.erp import ERP
from ..domain.facts import build_fact_sheet
from ..domain.models import (
    Action,
    ApprovalDecision,
    CaseState,
    EscalationTarget,
    Findings,
    InjectionReport,
    InvoiceSnapshot,
    PolicyAnalysis,
    Resolution,
    ReviewVerdict,
    RiskFlag,
    RiskLevel,
    TriageOutput,
    VendorEmail,
)
from ..guardrails import PolicyEngine, check_email, redact, scan
from ..harness.checkpoint import Checkpointer
from ..harness.graph import END, Interrupt, RunContext, RunOutcome, StateGraph
from ..harness.loop import AgentLoop, AgentProtocolError, AgentSpec
from ..harness.reliability import Budget, Controls, RetryPolicy
from ..llm.errors import AllModelsFailedError
from ..llm.router import ModelRouter
from ..obs.metrics import METRICS
from ..obs.tracing import SpanStore, Tracer, build_tracer
from ..rag import PolicyCorpus
from ..tools import ToolContext, ToolRegistry, build_ap_tools
from . import prompts
from .executor import Executor

READ_TOOLS = ["get_invoice", "get_vendor", "get_purchase_order", "get_goods_receipts", "compute_three_way_match",
              "check_duplicates", "recall_vendor_history", "sql_query", "read_result"]
RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def _max_risk(*levels: RiskLevel) -> RiskLevel:
    return max(levels, key=lambda r: RISK_ORDER[r])


def case_context(data: dict) -> str:
    return "<case_context>\n" + json.dumps(data, indent=1, default=str) + "\n</case_context>"


@dataclass
class Runtime:
    settings: Settings
    erp: ERP
    corpus: PolicyCorpus
    engine: PolicyEngine
    executor: Executor
    router: ModelRouter
    registry: ToolRegistry
    tracer: Tracer
    spans: SpanStore | None
    checkpointer: Checkpointer
    controls: Controls
    provider_name: str


def build_runtime(settings: Settings, provider, *, controls: Controls = Controls(), retry: RetryPolicy | None = None,
                  tracer: Tracer | None = None, spans: SpanStore | None = None, erp_path: Path | None = None) -> Runtime:
    erp = ERP(erp_path or settings.erp_db)
    corpus = PolicyCorpus.load()
    engine = PolicyEngine(settings.policy, corpus)
    if tracer is None:
        tracer, spans = build_tracer(settings.app_db)
    registry = ToolRegistry(build_ap_tools(), tracer=tracer, max_chars=settings.harness.tool_result_max_chars,
                            timeout_s=settings.harness.tool_timeout_s)
    router = ModelRouter(provider, settings, tracer=tracer, retry=retry, controls=controls)
    return Runtime(settings, erp, corpus, engine, Executor(erp, engine, settings), router, registry, tracer, spans,
                   Checkpointer(settings.app_db), controls, getattr(provider, "name", "provider"))


@dataclass
class APRunContext(RunContext):
    budget: Budget | None = None
    tools: ToolContext | None = None
    models: dict[str, str] = field(default_factory=dict)

    def sync(self, state: CaseState) -> CaseState:
        return state.model_copy(update={"usage": self.budget.snapshot(), "models_used": {**state.models_used,
                                                                                          **self.models}})


class APWorkflow:
    def __init__(self, rt: Runtime):
        self.rt = rt
        s = rt.settings
        self.loop = AgentLoop(rt.router, rt.registry, rt.tracer, s, rt.controls)
        self.specs = {
            "triage": AgentSpec("triage", prompts.triage(s), [], "submit_triage", TriageOutput),
            "investigator": AgentSpec("investigator", prompts.investigator(s), READ_TOOLS, "submit_findings", Findings),
            "policy_analyst": AgentSpec("policy_analyst", prompts.policy_analyst(s),
                                        ["search_policy", "get_policy_section"], "submit_policy_analysis", PolicyAnalysis),
            "resolver": AgentSpec("resolver", prompts.resolver(s), ["get_policy_section"], "submit_resolution", Resolution),
            "reviewer": AgentSpec("reviewer", prompts.reviewer(s), ["get_policy_section"], "submit_review", ReviewVerdict),
            "communicator": AgentSpec("communicator", prompts.communicator(s), [], "submit_vendor_email", VendorEmail),
        }
        self.graph = self.build_graph().compile(rt.checkpointer, rt.tracer, max_steps=s.harness.max_graph_steps)

    # ------------------------------------------------------------- graph
    def build_graph(self) -> StateGraph[CaseState]:
        g = StateGraph(CaseState)
        for name in ("intake", "triage", "investigate", "policy", "decide", "review", "human_approval", "execute",
                     "communicate", "finalize", "fail_safe"):
            g.add_node(name, getattr(self, f"node_{name}"))
        g.set_entry("intake").set_error_handler("fail_safe")
        g.add_edge("intake", "triage").add_edge("triage", "investigate").add_edge("investigate", "policy")
        g.add_edge("policy", "decide").add_edge("decide", "review")
        g.add_conditional_edges("review", self.after_review, ["decide", "human_approval", "execute"])
        g.add_edge("human_approval", "execute").add_edge("execute", "communicate")
        g.add_edge("communicate", "finalize").add_edge("finalize", END).add_edge("fail_safe", END)
        return g

    @staticmethod
    def after_review(state: CaseState) -> str:
        if state.review and state.review.verdict == "revise":
            return "decide"
        if state.needs_approval and state.approval is None:
            return "human_approval"
        return "execute"

    # ---------------------------------------------------------- lifecycle
    def _context(self, run_id: str, invoice_id: str, state: CaseState | None = None) -> APRunContext:
        tools = ToolContext(erp=self.rt.erp, settings=self.rt.settings, corpus=self.rt.corpus, invoice_id=invoice_id,
                            run_id=run_id)
        if state:
            tools.retrieved_sections = list(state.retrieved_sections)
        return APRunContext(run_id=run_id, budget=Budget(self.rt.settings.budget, state.usage if state else None),
                            tools=tools)

    def start(self, invoice_id: str, run_id: str | None = None, *, create_run: bool = True) -> RunOutcome[CaseState]:
        run_id = run_id or uuid.uuid4().hex
        if create_run:
            self.rt.checkpointer.create_run(run_id, invoice_id, self.rt.provider_name)
        state = CaseState(run_id=run_id, invoice_id=invoice_id)
        return self._finish(self.graph.invoke(state, self._context(run_id, invoice_id)))

    def resume(self, run_id: str, value: dict | None = None) -> RunOutcome[CaseState]:
        cp = self.rt.checkpointer.latest(run_id)
        if cp is None:
            raise KeyError(f"unknown run {run_id}")
        state = CaseState.model_validate_json(cp["state"])
        ctx = self._context(run_id, state.invoice_id, state)
        ctx.resume_value = value
        return self._finish(self.graph.resume(ctx))

    def decide_approval(self, approval_id: str, approved: bool, approver: str = "controller",
                        comment: str = "") -> RunOutcome[CaseState]:
        appr = self.rt.checkpointer.decide_approval(approval_id, approved, approver, comment)
        if appr is None:
            raise KeyError(f"unknown approval {approval_id}")
        return self.resume(appr["run_id"], {"approved": appr["status"] == "approved", "approver": approver,
                                            "comment": comment})

    def _finish(self, outcome: RunOutcome[CaseState]) -> RunOutcome[CaseState]:
        METRICS.inc("matchpoint_runs_total", status=outcome.status)
        return outcome

    def _agent(self, role: str, brief: str, ctx: APRunContext, risk: RiskLevel = RiskLevel.MEDIUM):
        run = self.loop.run(self.specs[role], brief, ctx=ctx.tools, budget=ctx.budget, risk=risk.value)
        ctx.models[role] = run.model
        return run.output

    # -------------------------------------------------------------- nodes
    def node_intake(self, state: CaseState, ctx: APRunContext) -> dict:
        inv = self.rt.erp.invoice(state.invoice_id)
        if inv is None:
            raise KeyError(f"invoice {state.invoice_id} not found")
        vendor = self.rt.erp.vendor(inv["vendor_id"]) or {}
        snap = InvoiceSnapshot(
            invoice_id=inv["invoice_id"], vendor_id=inv["vendor_id"], vendor_name=vendor.get("name", "?"),
            po_id=inv["po_id"], invoice_number=inv["invoice_number"], invoice_date=inv["invoice_date"],
            currency=inv["currency"], total=inv["total"], exception_code=inv["exception_code"], status=inv["status"],
        )
        with self.rt.tracer.span("guard.injection_scan", kind="guardrail") as span:
            report = InjectionReport()
            for source, text in (("invoice.notes", inv["notes"]), ("invoice.attachment", redact(inv["attachment_text"] or "")[0])):
                r = scan(text or "")
                if r.suspicious:
                    report.suspicious = True
                    report.sources.append(source)
                report.score = max(report.score, r.score)
                report.hidden_characters += r.hidden_characters
                report.matches += [m for m in r.matches if m not in report.matches]
            span.set(**{"guard.suspicious": report.suspicious, "guard.matches": report.matches,
                        "guard.sources": report.sources})
        flags = state.with_flag(RiskFlag.INJECTION_DETECTED) if report.suspicious else state.flags
        if report.suspicious:
            METRICS.inc("matchpoint_guardrail_events_total", guard="injection_scan")
        return {"snapshot": snap, "injection": report, "flags": flags}

    def node_triage(self, state: CaseState, ctx: APRunContext) -> dict:
        s = self.rt.settings
        brief = "Triage this invoice from its header.\n" + case_context({"invoice": state.snapshot.model_dump()})
        floor = RiskLevel.HIGH if (state.injection and state.injection.suspicious) or \
            state.snapshot.total > s.routing.high_risk_amount else RiskLevel.LOW
        try:
            triage: TriageOutput | None = self._agent("triage", brief, ctx)
        except (AgentProtocolError, AllModelsFailedError) as exc:  # triage is advisory: degrade, don't fail
            return {"triage": None, "risk": RiskLevel.HIGH, "notes": [*state.notes, f"triage unavailable ({exc}); "
                                                                                    "assuming high risk"]}
        return {"triage": triage, "risk": _max_risk(triage.risk, floor)}

    def node_investigate(self, state: CaseState, ctx: APRunContext) -> dict:
        brief = ("Investigate this invoice exception and submit your findings.\n" + case_context({
            "invoice": state.snapshot.model_dump(),
            "triage": state.triage.model_dump() if state.triage else None,
        }))
        findings: Findings = self._agent("investigator", brief, ctx, state.risk)
        risk = state.risk
        if findings.diagnosis.value == "BANK_DETAILS_RISK" or findings.suspicious_instructions:
            risk = RiskLevel.HIGH
        return {"findings": findings, "risk": risk}

    def node_policy(self, state: CaseState, ctx: APRunContext) -> dict:
        brief = "Identify the AP policy sections that govern this case.\n" + case_context({
            "invoice": state.snapshot.model_dump(),
            "findings": state.findings.model_dump(),
            "approval_limit": self.rt.settings.policy.approval_limit,
        })
        analysis: PolicyAnalysis = self._agent("policy_analyst", brief, ctx)
        retrieved = list(ctx.tools.retrieved_sections)
        grounded = [r for r in analysis.rules if r.section_id in retrieved and self.rt.corpus.exists(r.section_id)]
        dropped = [r.section_id for r in analysis.rules if r not in grounded]
        notes = state.notes + ([f"dropped ungrounded policy citations: {dropped}"] if dropped else [])
        return {"analysis": PolicyAnalysis(rules=grounded), "retrieved_sections": retrieved, "notes": notes}

    def node_decide(self, state: CaseState, ctx: APRunContext) -> dict:
        brief = "Propose the resolution for this invoice.\n" + case_context({
            "invoice": state.snapshot.model_dump(),
            "findings": state.findings.model_dump(),
            "rules": [r.model_dump() for r in state.analysis.rules],
            "revision": state.revisions,
            "review_feedback": state.review_feedback,
        })
        resolution: Resolution = self._agent("resolver", brief, ctx, state.risk)
        update = {"resolution": resolution, "review": None}
        if state.first_resolution is None:
            update["first_resolution"] = resolution
        return update

    def node_review(self, state: CaseState, ctx: APRunContext) -> dict:
        s, res = self.rt.settings, state.resolution
        facts = build_fact_sheet(self.rt.erp, state.invoice_id, s.policy, s.clock)
        with self.rt.tracer.span("guard.policy_engine", kind="guardrail") as span:
            violations = self.rt.engine.check(res, facts)
            span.set(**{"guard.violations": [str(v) for v in violations], "guard.action": res.action.value})
        log = [*state.violation_log, [str(v) for v in violations]]
        for v in violations:
            METRICS.inc("matchpoint_guardrail_violations_total", code=v.code)

        if violations:
            issues = [str(v) for v in violations]
        else:
            brief = "Review the proposed resolution.\n" + case_context({
                "invoice": state.snapshot.model_dump(),
                "findings": state.findings.model_dump(),
                "rules": [r.model_dump() for r in state.analysis.rules],
                "resolution": res.model_dump(mode="json"),
                "deterministic_policy_checks": "passed",
            })
            try:
                verdict: ReviewVerdict = self._agent("reviewer", brief, ctx, state.risk)
            except (AgentProtocolError, AllModelsFailedError) as exc:
                verdict = ReviewVerdict(verdict="approve", issues=[f"reviewer unavailable ({exc}); deterministic checks passed"])
            issues = verdict.issues if verdict.verdict == "revise" else []

        if issues:
            if state.revisions < s.harness.max_revisions:
                return {"review": ReviewVerdict(verdict="revise", issues=issues), "review_feedback": issues,
                        "revisions": state.revisions + 1, "violation_log": log}
            safe = self.rt.engine.fail_safe(f"no accepted resolution after {state.revisions} revisions: {issues[0]}")
            return {"resolution": safe, "review": ReviewVerdict(verdict="approve", issues=[]), "violation_log": log,
                    "flags": state.with_flag(RiskFlag.REVIEW_DEADLOCK), "needs_approval": False}
        return {"review": ReviewVerdict(verdict="approve", issues=[]), "violation_log": log, "review_feedback": [],
                "needs_approval": self.rt.engine.requires_human_approval(res, facts)}

    def node_human_approval(self, state: CaseState, ctx: APRunContext) -> dict | Interrupt:
        approval_id = state.approval_id or "APR-" + hashlib.sha256(state.run_id.encode()).hexdigest()[:12]
        if ctx.resume_value is None:
            summary = (f"{state.snapshot.vendor_name} invoice {state.snapshot.invoice_number}: "
                       f"{state.snapshot.currency} {state.snapshot.total:,.2f} fully matched; above the "
                       f"{self.rt.settings.policy.approval_limit:,.0f} release limit (AP-POL-8.1).")
            self.rt.checkpointer.create_approval(approval_id, state.run_id, state.invoice_id, state.snapshot.total, summary)
            self.rt.executor.mark_pending_approval(state.run_id, state.invoice_id, approval_id)
            return Interrupt("controller_approval", {"approval_id": approval_id, "invoice_id": state.invoice_id,
                                                     "amount": state.snapshot.total, "summary": summary})
        decision = ApprovalDecision.model_validate(ctx.resume_value)
        if decision.approved:
            return {"approval": decision, "approval_id": approval_id}
        rejected = Resolution(action=Action.ESCALATE, hold_reason=None, escalate_to=EscalationTarget.CONTROLLER,
                              amount=None, rationale=f"Controller declined release: {decision.comment or 'no reason given'}",
                              citations=["AP-POL-8.1"], risk_flags=[], confidence=1.0)
        return {"approval": decision, "approval_id": approval_id, "resolution": rejected, "needs_approval": False,
                "flags": state.with_flag(RiskFlag.APPROVER_REJECTED)}

    def node_execute(self, state: CaseState, ctx: APRunContext) -> dict:
        s = self.rt.settings
        facts = build_fact_sheet(self.rt.erp, state.invoice_id, s.policy, s.clock)  # fresh: closes the TOCTOU gap
        record = self.rt.executor.apply(run_id=state.run_id, resolution=state.resolution, facts=facts,
                                        approval=state.approval)
        flags = state.with_flag(RiskFlag.FRAUD_SUSPECTED) if facts.bank_risk else state.flags
        for f in state.resolution.risk_flags:
            if f not in flags:
                flags = [*flags, f]
        return {"execution": record, "flags": flags}

    def node_communicate(self, state: CaseState, ctx: APRunContext) -> dict:
        r = state.resolution
        if r.action not in (Action.REQUEST_CREDIT_MEMO, Action.REJECT_DUPLICATE) or RiskFlag.FRAUD_SUSPECTED in state.flags:
            return {}
        s = self.rt.settings
        facts = build_fact_sheet(self.rt.erp, state.invoice_id, s.policy, s.clock)
        vendor = self.rt.erp.vendor(facts.vendor_id) or {}
        dup_number = None
        if facts.duplicate_ids:
            dup = self.rt.erp.invoice(facts.duplicate_ids[0])
            dup_number = dup["invoice_number"] if dup else None
        affected = [f"line {ln.line_no} {ln.sku}: billed {ln.invoiced_qty:g} @ {ln.invoiced_unit_price:.2f} vs PO "
                    f"{ln.po_qty:g} @ {ln.po_unit_price or 0:.2f}" for ln in facts.match.lines
                    if ln.price_variance_amount > 0 or ln.units_over_po > 0]
        amount = r.amount if r.action == Action.REQUEST_CREDIT_MEMO else None
        base = {"action": r.action.value, "invoice_number": state.snapshot.invoice_number, "po_id": state.snapshot.po_id,
                "vendor_name": vendor.get("name"), "currency": state.snapshot.currency, "total": state.snapshot.total,
                "amount": amount, "affected_lines": affected, "duplicate_of": facts.duplicate_ids[0] if facts.duplicate_ids
                else None, "duplicate_of_number": dup_number}
        feedback: list[str] = []
        for _attempt in range(2):
            brief = "Draft the vendor email for this resolution.\n" + case_context({**base, "fix_these_issues": feedback})
            try:
                email: VendorEmail = self._agent("communicator", brief, ctx)
            except (AgentProtocolError, AllModelsFailedError) as exc:
                feedback = [f"communicator unavailable: {exc}"]
                break
            with self.rt.tracer.span("guard.email", kind="guardrail") as span:
                feedback = check_email(email, invoice_number=state.snapshot.invoice_number, amount=amount)
                span.set(**{"guard.problems": feedback})
            if not feedback:
                self.rt.executor.save_email_draft(state.run_id, state.invoice_id, vendor.get("contact_email", ""),
                                                  email.subject, email.body)
                return {"email": email, "email_to": vendor.get("contact_email")}
        return {"notes": [*state.notes, f"vendor email left for manual drafting: {'; '.join(feedback)}"]}

    def node_finalize(self, state: CaseState, ctx: APRunContext) -> dict:
        r = state.resolution
        snap = state.snapshot
        detail = {Action.APPROVE_PAYMENT: f"released {snap.currency} {r.amount or 0:,.2f}",
                  Action.REQUEST_CREDIT_MEMO: f"credit memo requested for {snap.currency} {r.amount or 0:,.2f}",
                  Action.HOLD: f"held ({r.hold_reason.value if r.hold_reason else 'n/a'})",
                  Action.REJECT_DUPLICATE: "rejected as duplicate",
                  Action.ESCALATE: f"escalated to {r.escalate_to.value if r.escalate_to else 'controller'}"}[r.action]
        note = f"{self.rt.settings.clock.isoformat()} invoice {snap.invoice_number}: {detail}."
        self.rt.executor.remember(state.run_id, snap.vendor_id, state.invoice_id, note)
        METRICS.inc("matchpoint_resolutions_total", action=r.action.value)
        return {}

    def node_fail_safe(self, state: CaseState, ctx: APRunContext) -> dict:
        reason = state.errors[-1] if state.errors else "unknown failure"
        flags = state.with_flag(RiskFlag.AGENT_FAILURE)
        METRICS.inc("matchpoint_fail_safe_total")
        if state.execution is not None:  # already resolved; never overwrite a completed write
            return {"flags": flags, "notes": [*state.notes, f"post-execution error: {reason}"]}
        s = self.rt.settings
        res = self.rt.engine.fail_safe(reason)
        facts = build_fact_sheet(self.rt.erp, state.invoice_id, s.policy, s.clock)
        record = self.rt.executor.apply(run_id=state.run_id, resolution=res, facts=facts, approval=None,
                                        key_suffix="fail-safe")
        return {"resolution": res, "execution": record, "flags": flags}
