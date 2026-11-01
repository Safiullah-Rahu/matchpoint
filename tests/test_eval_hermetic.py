"""Regression test: evals must not depend on the state of the live/demo ERP."""

from matchpoint.agents.workflow import APWorkflow, build_runtime
from matchpoint.evals.runner import EvalConfig, run_eval
from matchpoint.llm.sim_provider import SimProvider


def test_eval_ignores_already_resolved_demo_invoices(settings, cases, tmp_path):
    inv = cases["price_over_tolerance-0"]["invoice_id"]
    APWorkflow(build_runtime(settings, SimProvider())).start(inv)  # resolve it in the "demo" ERP first
    rep = run_eval(settings, EvalConfig(name="hermetic", scenarios=["price_over_tolerance"]), out_dir=tmp_path)
    assert rep["summary"]["pass_at_1"] == 1.0 and rep["summary"]["safety_violations"] == 0
