"""matchpoint CLI: seed, run, approve, trace, eval, suite, serve."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .config import load_settings

BOLD, DIM, GREEN, RED, YELLOW, CYAN, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"
if os.name == "nt":
    os.system("")  # enable ANSI escapes on Windows consoles
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    BOLD = DIM = GREEN = RED = YELLOW = CYAN = RESET = ""


def _workflow(settings, args):
    from .agents.workflow import APWorkflow, build_runtime
    from .llm.factory import make_provider

    provider = make_provider(args.provider, settings, mistake_rate=getattr(args, "mistakes", 0.0),
                             gullible=getattr(args, "gullible", False), chaos=getattr(args, "chaos", 0.0),
                             seed=getattr(args, "seed", 0), cassette=getattr(args, "cassette", None),
                             record=getattr(args, "record", None))
    return APWorkflow(build_runtime(settings, provider))


def cmd_seed(settings, args) -> int:
    from .domain.seed import seed

    cases = seed(settings)
    print(f"{GREEN}seeded{RESET} {settings.erp_db} with {len(cases)} labelled exception invoices")
    if args.export:
        shutil.copy(settings.home / "eval_cases.jsonl", args.export)
        print(f"exported labels to {args.export}")
    return 0


def cmd_queue(settings, args) -> int:
    from .domain.erp import ERP

    for row in ERP(settings.erp_db).exception_queue():
        print(f"{row['invoice_id']}  {row['status']:<21} {row['exception_code'] or '-':<19} {row['currency']} "
              f"{row['total']:>11,.2f}  {row['vendor_name']}")
    return 0


def _print_outcome(out) -> None:
    st = out.state
    color = GREEN if out.status == "completed" else YELLOW if out.status == "interrupted" else RED
    print(f"\n{BOLD}run {out.run_id}{RESET}  {color}{out.status}{RESET}")
    if st.snapshot:
        s = st.snapshot
        print(f"  invoice   {s.invoice_id} {s.invoice_number} · {s.vendor_name} · {s.currency} {s.total:,.2f} · "
              f"code {s.exception_code}")
    if st.findings:
        print(f"  diagnosis {st.findings.diagnosis.value}  (risk {st.risk.value})")
    if st.resolution:
        r = st.resolution
        extra = r.hold_reason.value if r.hold_reason else r.escalate_to.value if r.escalate_to else ""
        amt = f" {r.amount:,.2f}" if r.amount is not None else ""
        print(f"  decision  {BOLD}{r.action.value}{RESET} {extra}{amt}  cites {', '.join(r.citations)}")
        print(f"  rationale {DIM}{r.rationale[:220]}{RESET}")
    if st.flags:
        print(f"  flags     {YELLOW}{', '.join(f.value for f in st.flags)}{RESET}")
    if st.revisions:
        print(f"  revisions {st.revisions}  violations seen: {sum(len(v) for v in st.violation_log)}")
    if st.email:
        print(f"  email     to {st.email_to}: {st.email.subject}")
    if out.interrupt:
        print(f"  {YELLOW}waiting for approval {out.interrupt['approval_id']}{RESET}: {out.interrupt['summary']}")
        print(f"  approve with: matchpoint approve {out.interrupt['approval_id']}")
    u = st.usage
    print(f"  usage     {u.llm_calls} LLM calls · {u.tool_calls} tool calls · "
          f"{u.input_tokens + u.output_tokens:,} tokens · ${u.cost_usd:.4f} · retries {u.retries} · "
          f"fallbacks {u.fallbacks}")
    if st.models_used:
        print("  models    " + ", ".join(f"{k}={v}" for k, v in st.models_used.items()))
    if out.error:
        print(f"  {RED}error {out.error}{RESET}")


def cmd_run(settings, args) -> int:
    wf = _workflow(settings, args)
    out = wf.start(args.invoice_id)
    _print_outcome(out)
    if args.trace:
        _print_trace(wf.rt.spans.get_trace(out.run_id))
    return 0 if out.status != "failed" else 1


def cmd_approvals(settings, args) -> int:
    from .harness.checkpoint import Checkpointer

    for a in Checkpointer(settings.app_db).approvals(None if args.all else "pending"):
        print(f"{a['approval_id']}  {a['status']:<9} {a['invoice_id']}  {a['amount']:>11,.2f}  {a['summary']}")
    return 0


def cmd_approve(settings, args) -> int:
    wf = _workflow(settings, args)
    out = wf.decide_approval(args.approval_id, approved=not args.reject, approver=args.approver, comment=args.comment)
    _print_outcome(out)
    return 0


def _print_trace(spans: list[dict]) -> None:
    by_id = {s["span_id"]: s for s in spans}
    depth = {}
    for s in spans:
        d, p = 0, s["parent_id"]
        while p and p in by_id:
            d, p = d + 1, by_id[p]["parent_id"]
        depth[s["span_id"]] = d
    print(f"\n{BOLD}trace{RESET} ({len(spans)} spans)")
    for s in spans:
        a = s["attributes"]
        info = ""
        if s["kind"] == "llm":
            info = (f"{a.get('gen_ai.request.model')} in={a.get('gen_ai.usage.input_tokens')} "
                    f"out={a.get('gen_ai.usage.output_tokens')} ${a.get('matchpoint.cost_usd', 0):.4f} "
                    f"-> {','.join(a.get('matchpoint.tool_calls') or [])}")
        elif s["kind"] == "guardrail":
            info = json.dumps({k: v for k, v in a.items() if k.startswith("guard.")})[:160]
        mark = f"{RED}✗{RESET}" if s["status"] == "error" else " "
        print(f"{mark}{'  ' * depth[s['span_id']]}{CYAN if s['kind'] in ('node', 'run') else ''}{s['name']}{RESET} "
              f"{DIM}{s['duration_ms']:.0f}ms {info}{RESET}")


def cmd_trace(settings, args) -> int:
    from .obs.tracing import SpanStore

    _print_trace(SpanStore(settings.app_db).get_trace(args.run_id))
    return 0


def cmd_eval(settings, args) -> int:
    from .evals.runner import EvalConfig, run_eval
    from .evals.suite import check_gate
    from .harness.reliability import Controls

    cfg = EvalConfig(name=args.name, provider=args.provider, trials=args.trials, chaos=args.chaos,
                     mistake_rate=args.mistakes, gullible=args.gullible,
                     controls=Controls.off() if args.controls_off else Controls(), workers=args.workers,
                     scenarios=args.scenario, variants=args.variant, limit=args.limit, cassette=args.cassette,
                     record=args.record, seed=args.seed)
    if args.provider == "anthropic" and not args.yes:
        n = (args.limit or (13 * len(args.variant) if args.variant else 52)) * args.trials
        print(f"{YELLOW}This calls the Claude API for ~{n} runs (~10 calls each). Re-run with --yes to confirm.{RESET}")
        return 2
    rep = run_eval(settings, cfg, out_dir=Path(args.out) if args.out else None)
    s = rep["summary"]
    print(f"{BOLD}{cfg.name}{RESET}: {s['runs']} runs · pass@1 {s['pass_at_1']:.1%} · pass^{s['trials']} "
          f"{s['pass_hat_k']:.1%} · safety violations {s['safety_violations']} · fail-safe {s['fail_safe_runs']} · "
          f"${s['cost_usd_per_run']:.4f}/run · p95 {s['latency_p95_s']}s")
    print("  outcome:    " + " ".join(f"{k}={v:.0%}" for k, v in s["outcome"].items()))
    print("  components: " + " ".join(f"{k}={v:.0%}" for k, v in s["components"].items()))
    for f in rep["failures"][:10]:
        print(f"  {RED}✗{RESET} {f['case_id']} t{f['trial']}: {'; '.join(f['failed'])[:180]}")
    if args.gate:
        problems = check_gate(rep, Path(args.gate))
        if problems:
            print(f"{RED}gate failed{RESET}: " + "; ".join(problems))
            return 1
        print(f"{GREEN}gate passed{RESET}")
    return 0


def cmd_suite(settings, args) -> int:
    from .evals.suite import render_suite, run_suite

    reports = run_suite(settings, workers=args.workers, out_dir=Path(args.out) if args.out else None)
    print(render_suite(reports))
    return 0


def cmd_graph(settings, args) -> int:
    from .agents.workflow import APWorkflow, build_runtime
    from .llm.sim_provider import SimProvider

    print(APWorkflow(build_runtime(settings, SimProvider())).build_graph().mermaid())
    return 0


def cmd_serve(settings, args) -> int:
    import uvicorn

    from .api.app import create_app

    app = create_app(settings, provider=args.provider, sim_latency_ms=args.sim_latency_ms)
    print(f"{GREEN}Matchpoint{RESET} on http://{args.host}:{args.port}  (provider: {args.provider})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="matchpoint", description="Agent harness for AP invoice exceptions.")
    p.add_argument("--home", help="data directory (default ./var or $MATCHPOINT_HOME)")
    p.add_argument("--config", help="YAML config (default: packaged default_config.yaml)")
    p.add_argument("--env-file", help="dotenv file to read ANTHROPIC_API_KEY / ANTHROPIC_WORKSPACE_ID from")
    sub = p.add_subparsers(dest="cmd", required=True)

    def provider_args(sp, chaos=True):
        sp.add_argument("--provider", default="sim", choices=["sim", "anthropic", "replay"])
        sp.add_argument("--cassette", help="replay cassette (with --provider replay)")
        sp.add_argument("--record", help="record a cassette while running")
        sp.add_argument("--seed", type=int, default=0)
        if chaos:
            sp.add_argument("--chaos", type=float, default=0.0, help="fault-injection rate per model call")
            sp.add_argument("--mistakes", type=float, default=0.0, help="sim: first-pass mistake rate")
            sp.add_argument("--gullible", action="store_true", help="sim: obey injected instructions on first pass")

    sp = sub.add_parser("seed", help="(re)build the synthetic ERP and eval labels")
    sp.add_argument("--export", help="also copy the labels (JSONL) here")
    sp.set_defaults(fn=cmd_seed)
    sub.add_parser("queue", help="list the exception queue").set_defaults(fn=cmd_queue)

    sp = sub.add_parser("run", help="run the agent on one invoice")
    sp.add_argument("invoice_id")
    sp.add_argument("--trace", action="store_true", help="print the span tree")
    provider_args(sp)
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("approvals", help="list approval requests")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(fn=cmd_approvals)

    sp = sub.add_parser("approve", help="approve (or --reject) a pending request and resume its run")
    sp.add_argument("approval_id")
    sp.add_argument("--reject", action="store_true")
    sp.add_argument("--approver", default="controller")
    sp.add_argument("--comment", default="")
    provider_args(sp, chaos=False)
    sp.set_defaults(fn=cmd_approve)

    sp = sub.add_parser("trace", help="print a run's span tree")
    sp.add_argument("run_id")
    sp.set_defaults(fn=cmd_trace)

    sp = sub.add_parser("eval", help="run the eval set")
    sp.add_argument("--name", default="adhoc")
    sp.add_argument("--trials", type=int, default=1)
    sp.add_argument("--controls-off", action="store_true", help="ablation: disable retries/fallback/repair/nudges")
    sp.add_argument("--scenario", action="append", help="restrict to scenario(s)")
    sp.add_argument("--variant", action="append", type=int, help="restrict to variant(s) 0-3, e.g. --variant 0")
    sp.add_argument("--limit", type=int)
    sp.add_argument("--workers", type=int, default=8)
    sp.add_argument("--gate", help="thresholds YAML; exit 1 if not met")
    sp.add_argument("--out", help="report directory")
    sp.add_argument("--yes", action="store_true", help="confirm live API spend")
    provider_args(sp)
    sp.set_defaults(fn=cmd_eval)

    sp = sub.add_parser("suite", help="run the ablation suite (sim): reference, chaos on/off, faulty model")
    sp.add_argument("--workers", type=int, default=8)
    sp.add_argument("--out", help="report directory")
    sp.set_defaults(fn=cmd_suite)

    sub.add_parser("graph", help="print the workflow graph as Mermaid").set_defaults(fn=cmd_graph)

    sp = sub.add_parser("serve", help="start the API + web UI")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8400)
    sp.add_argument("--provider", default="sim", choices=["sim", "anthropic"])
    sp.add_argument("--sim-latency-ms", type=float, default=220.0, help="make sim runs watchable in the UI")
    sp.set_defaults(fn=cmd_serve)
    return p


def load_env_file(path: str) -> list[str]:
    """Load ANTHROPIC_* variables from a dotenv file (other keys are ignored). Existing env vars win."""
    loaded = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key.startswith("ANTHROPIC_") and value and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    if args.env_file:
        loaded = load_env_file(args.env_file)
        print(f"{DIM}loaded {', '.join(loaded) or 'no ANTHROPIC_* variables'} from {args.env_file}{RESET}")
    settings = load_settings(args.config, home=args.home)
    if args.cmd not in ("seed",) and not settings.erp_db.exists():
        from .domain.seed import seed

        seed(settings)
        print(f"{DIM}(first run: seeded {settings.erp_db}){RESET}")
    return args.fn(settings, args) or 0


if __name__ == "__main__":
    sys.exit(main())
