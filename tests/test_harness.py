"""Agent loop and state graph behaviour: repair, nudges, loop guard, interrupts,
fail-closed routing, crash recovery and idempotency."""

import pytest

from matchpoint.domain.models import RiskFlag
from matchpoint.harness.reliability import Controls
from matchpoint.llm.sim_provider import SimProvider
from matchpoint.llm.types import LLMResponse, ToolCall


class Saboteur:
    """Wrap the sim and corrupt specific calls, deterministically."""

    name = "saboteur"

    def __init__(self, inner, plan):
        self.inner, self.plan, self.n = inner, plan, {}

    def complete(self, req):
        self.n[req.role] = self.n.get(req.role, 0) + 1
        action = self.plan.get((req.role, self.n[req.role]))
        resp = self.inner.complete(req)
        if action == "prose":
            return LLMResponse(resp.model, "All good.", [], "end_turn", resp.usage, [{"type": "text", "text": "All good."}])
        if action == "drop_field":
            blocks = [dict(b) for b in resp.raw_content]
            tu = next(b for b in blocks if b["type"] == "tool_use")
            tu["input"] = {k: v for k, v in tu["input"].items() if k != next(iter(tu["input"]))}
            return LLMResponse(resp.model, resp.text, [ToolCall(tu["id"], tu["name"], tu["input"])], "tool_use",
                               resp.usage, blocks)
        if action == "repeat":
            blocks = [{"type": "tool_use", "id": f"rep{self.n[req.role]}", "name": "get_vendor",
                       "input": {"vendor_id": "V-101"}}]
            return LLMResponse(resp.model, "", [ToolCall(b["id"], b["name"], b["input"]) for b in blocks], "tool_use",
                               resp.usage, blocks)
        return resp


def _events(wf, run_id, name):
    return [e for s in wf.rt.spans.get_trace(run_id) for e in s["events"] if e["name"] == name]


def test_validation_repair_and_nudge(make_workflow, cases):
    wf = make_workflow(Saboteur(SimProvider(), {("resolver", 1): "drop_field", ("triage", 1): "prose"}))
    out = wf.start(cases["price_over_tolerance-0"]["invoice_id"])
    assert out.status == "completed" and out.state.resolution.action.value == "REQUEST_CREDIT_MEMO"
    assert _events(wf, out.run_id, "validation_error") and _events(wf, out.run_id, "nudge")


def test_loop_guard_stops_identical_calls(make_workflow, cases):
    plan = {("investigator", i): "repeat" for i in range(1, 6)}
    wf = make_workflow(Saboteur(SimProvider(), plan))
    out = wf.start(cases["clean_within_tolerance-1"]["invoice_id"])
    assert out.status == "completed"
    assert _events(wf, out.run_id, "loop_guard")


def test_controls_off_fails_closed(make_workflow, cases):
    wf = make_workflow(Saboteur(SimProvider(), {("investigator", 1): "prose"}), controls=Controls.off())
    inv = cases["clean_within_tolerance-0"]["invoice_id"]
    out = wf.start(inv)
    assert out.status == "completed"
    assert RiskFlag.AGENT_FAILURE in out.state.flags
    assert out.state.resolution.action.value == "ESCALATE"
    erp = wf.rt.erp.outcome(inv)
    assert erp["status"] == "escalated" and erp["payment_schedule"] == []


def test_unsafe_proposal_is_revised_never_executed(make_workflow, cases):
    wf = make_workflow(SimProvider(gullible=True))
    inv = cases["injection_bank_change-0"]["invoice_id"]
    out = wf.start(inv)
    st = out.state
    assert st.first_resolution.action.value == "APPROVE_PAYMENT"  # the model was fooled...
    assert any("UNSAFE_APPROVAL" in v for v in st.violation_log[0])  # ...the engine caught it...
    assert st.resolution.action.value == "HOLD" and st.revisions == 1  # ...and the revision is correct
    erp = wf.rt.erp.outcome(inv)
    assert erp["payment_schedule"] == [] and erp["status"] == "on_hold" and len(erp["verification_tasks"]) == 1


def test_human_approval_interrupt_resume_and_reject(make_workflow, cases):
    wf = make_workflow()
    inv = cases["over_approval_limit-0"]["invoice_id"]
    out = wf.start(inv)
    assert out.status == "interrupted" and wf.rt.erp.outcome(inv)["status"] == "pending_approval"
    assert wf.rt.erp.outcome(inv)["payment_schedule"] == []
    done = wf.decide_approval(out.interrupt["approval_id"], approved=True, approver="cfo")
    assert done.status == "completed"
    erp = wf.rt.erp.outcome(inv)
    assert erp["status"] == "approved_for_payment" and erp["payment_schedule"][0]["approved_by"] == "cfo"

    inv2 = cases["over_approval_limit-1"]["invoice_id"]
    out2 = wf.start(inv2)
    rej = wf.decide_approval(out2.interrupt["approval_id"], approved=False, comment="vendor dispute")
    assert rej.state.resolution.action.value == "ESCALATE" and RiskFlag.APPROVER_REJECTED in rej.state.flags
    assert wf.rt.erp.outcome(inv2)["payment_schedule"] == []


class Crash(BaseException):
    """Simulates the process dying (not an Exception, so nothing catches it)."""


def test_crash_after_write_then_resume_does_not_double_pay(make_workflow, cases):
    wf = make_workflow()
    inv = cases["clean_within_tolerance-2"]["invoice_id"]
    original = wf.graph.g.nodes["execute"]

    def execute_then_die(state, ctx):
        original(state, ctx)  # the ERP write commits...
        raise Crash()  # ...then the process dies before the checkpoint

    wf.graph.g.nodes["execute"] = execute_then_die
    with pytest.raises(Crash):
        wf.start(inv, run_id="crashy")
    assert len(wf.rt.erp.outcome(inv)["payment_schedule"]) == 1
    assert wf.rt.checkpointer.latest("crashy")["next_node"] == "execute"

    wf.graph.g.nodes["execute"] = original
    out = wf.resume("crashy")
    assert out.status == "completed"
    assert out.state.execution.applied is False  # idempotent replay
    assert len(wf.rt.erp.outcome(inv)["payment_schedule"]) == 1


def test_graph_mermaid_lists_every_node(make_workflow):
    mermaid = make_workflow().build_graph().mermaid()
    for node in ("intake", "triage", "investigate", "policy", "decide", "review", "human_approval", "execute",
                 "communicate", "finalize", "fail_safe"):
        assert node in mermaid
