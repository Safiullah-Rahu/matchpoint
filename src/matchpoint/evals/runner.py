"""Eval runner: every case x trial in an isolated ERP copy, in parallel.

* Isolation: each run gets its own copy of the seeded ERP, so runs cannot
  interfere and graders can read the exact end state a run produced.
* Human-in-the-loop: an auto-approver plays the controller; the grader checks
  that approval was requested exactly when policy requires it.
* Reliability: `trials > 1` gives pass^k (all k trials of a case pass), the
  metric that matters for production agents, next to pass@1.
* Ablations: chaos rate, faulty-model knobs and harness controls are inputs,
  so one command reproduces the comparison tables in the README.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..agents.workflow import APWorkflow, build_runtime
from ..config import Settings
from ..domain.seed import load_cases, seed
from ..guardrails.policy_engine import UNSAFE_CODES
from ..harness.reliability import Controls, RetryPolicy
from ..llm.factory import make_provider
from ..obs.tracing import build_tracer
from .graders import COMPONENT, OUTCOME, grade, passed


@dataclass
class EvalConfig:
    name: str = "baseline"
    provider: str = "sim"
    trials: int = 1
    chaos: float = 0.0
    mistake_rate: float = 0.0
    gullible: bool = False
    controls: Controls = field(default_factory=Controls)
    workers: int = 8
    scenarios: list[str] | None = None
    variants: list[int] | None = None
    limit: int | None = None
    cassette: str | None = None
    record: str | None = None
    seed: int = 0


@dataclass
class RunResult:
    case_id: str
    scenario: str
    trial: int
    run_id: str
    status: str
    passed: bool
    grades: list[dict]
    action: str | None
    expected_action: str
    cost_usd: float
    tokens: int
    llm_calls: int
    retries: int
    fallbacks: int
    revisions: int
    latency_s: float
    fail_safe: bool
    chaos: dict
    models: dict
    unsafe_blocked: int = 0
    error: str | None = None


def _seed_for(case_id: str, trial: int, base: int) -> int:
    return int(hashlib.sha256(f"{base}:{case_id}:{trial}".encode()).hexdigest()[:8], 16)


def run_one(settings: Settings, cfg: EvalConfig, case: dict, trial: int, workdir: Path, tracer, spans) -> RunResult:
    erp_path = workdir / f"erp-{case['case_id']}-t{trial}.db"
    shutil.copy(settings.erp_db, erp_path)
    seed = _seed_for(case["case_id"], trial, cfg.seed)
    provider = make_provider(cfg.provider, settings, mistake_rate=cfg.mistake_rate, gullible=cfg.gullible,
                             chaos=cfg.chaos, seed=seed, cassette=cfg.cassette, record=cfg.record)
    fast = cfg.provider != "anthropic"
    retry = RetryPolicy(settings.retry.max_attempts, settings.retry.base_delay_s, settings.retry.max_delay_s,
                        sleep=(lambda s: time.sleep(min(s, 0.002))) if fast else time.sleep)
    rt = build_runtime(settings, provider, controls=cfg.controls, retry=retry, tracer=tracer, spans=spans,
                       erp_path=erp_path)
    wf = APWorkflow(rt)
    started = time.perf_counter()
    approval_requested, error = False, None
    outcome = wf.start(case["invoice_id"], run_id=f"{cfg.name}-{case['case_id']}-t{trial}-{seed:08x}")
    if outcome.status == "interrupted" and outcome.interrupt and outcome.interrupt.get("approval_id"):
        approval_requested = True
        outcome = wf.decide_approval(outcome.interrupt["approval_id"], approved=True, approver="eval-controller",
                                     comment="auto-approved by eval harness")
    if outcome.status == "failed":
        error = outcome.error
    state = outcome.state
    erp = rt.erp.outcome(case["invoice_id"])
    grades = grade(case, state, erp, outcome.status, approval_requested)
    ok = passed(grades)
    if ok:
        erp_path.unlink(missing_ok=True)
    u = state.usage
    chaos = dict(getattr(getattr(provider, "stats", None), "injected", {}) or {})
    unsafe_blocked = sum(any(v[1:].split("]")[0] in UNSAFE_CODES for v in round_) for round_ in state.violation_log)
    return RunResult(
        case_id=case["case_id"], scenario=case["scenario"], trial=trial, run_id=outcome.run_id, status=outcome.status,
        passed=ok, grades=[gr.to_dict() for gr in grades],
        action=state.resolution.action.value if state.resolution else None, expected_action=case["expected"]["action"],
        cost_usd=u.cost_usd, tokens=u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_write_tokens,
        llm_calls=u.llm_calls, retries=u.retries, fallbacks=u.fallbacks, revisions=state.revisions,
        latency_s=round(time.perf_counter() - started, 3), fail_safe=any(f.value == "AGENT_FAILURE" for f in state.flags),
        chaos=chaos, models=state.models_used, unsafe_blocked=unsafe_blocked,
        error=error or (state.errors[-1] if state.errors else None),
    )


def run_eval(settings: Settings, cfg: EvalConfig, *, out_dir: Path | None = None) -> dict:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    workdir = settings.home / "eval_runs" / f"{stamp}-{cfg.name}"
    workdir.mkdir(parents=True, exist_ok=True)
    # Hermetic: seed a pristine ERP for this eval instead of reusing the live/demo database,
    # whose invoices may already have been resolved.
    settings = settings.model_copy(update={"home": workdir})
    seed(settings)
    cases = load_cases(settings)
    if cfg.scenarios:
        cases = [c for c in cases if c["scenario"] in cfg.scenarios]
    if cfg.variants is not None:
        cases = [c for c in cases if c["variant"] in cfg.variants]
    if cfg.limit:
        cases = cases[: cfg.limit]
    tracer, spans = build_tracer(workdir / "matchpoint.db")
    jobs = [(c, t) for c in cases for t in range(cfg.trials)]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as pool:
        results = list(pool.map(lambda job: run_one(settings, cfg, job[0], job[1], workdir, tracer, spans), jobs))
    report = aggregate(cfg, results, wall_s=time.perf_counter() - started)
    report["workdir"] = str(workdir)
    out = out_dir or workdir.parent.parent / "eval_reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{cfg.name}.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    (out / f"{cfg.name}.md").write_text(render_markdown(report), encoding="utf-8")
    (out / "latest.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 4)


def aggregate(cfg: EvalConfig, results: list[RunResult], wall_s: float = 0.0) -> dict:
    n = len(results)
    by_case: dict[str, list[RunResult]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r)

    def rate(name: str) -> float:
        vals = [g["passed"] for r in results for g in r.grades if g["name"] == name]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    scenarios: dict[str, dict] = {}
    for r in results:
        s = scenarios.setdefault(r.scenario, {"runs": 0, "passed": 0, "safety_violations": 0})
        s["runs"] += 1
        s["passed"] += int(r.passed)
        s["safety_violations"] += int(not _grade(r, "safety"))
    for s in scenarios.values():
        s["pass_rate"] = round(s["passed"] / s["runs"], 4)

    confusion: dict[str, dict[str, int]] = {}
    for r in results:
        row = confusion.setdefault(r.expected_action, {})
        row[str(r.action)] = row.get(str(r.action), 0) + 1

    chaos_total: dict[str, int] = {}
    for r in results:
        for k, v in r.chaos.items():
            chaos_total[k] = chaos_total.get(k, 0) + v

    failures = [
        {"case_id": r.case_id, "trial": r.trial, "run_id": r.run_id, "status": r.status, "action": r.action,
         "expected": r.expected_action, "failed": [g["name"] + (": " + g["detail"] if g["detail"] else "")
                                                   for g in r.grades if g["name"] in OUTCOME and not g["passed"]],
         "error": r.error}
        for r in results if not r.passed
    ]
    costs = [r.cost_usd for r in results]
    lat = [r.latency_s for r in results]
    return {
        "name": cfg.name,
        "config": {**asdict(cfg), "controls": asdict(cfg.controls)},
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "summary": {
            "runs": n,
            "cases": len(by_case),
            "trials": cfg.trials,
            "pass_at_1": round(sum(r.passed for r in results) / n, 4) if n else 0.0,
            "pass_hat_k": round(sum(all(x.passed for x in rs) for rs in by_case.values()) / len(by_case), 4) if by_case else 0.0,
            "completion_rate": round(sum(r.status == "completed" for r in results) / n, 4) if n else 0.0,
            "safety_violations": sum(not _grade(r, "safety") for r in results),
            "unsafe_proposals_blocked": sum(r.unsafe_blocked for r in results),
            "fail_safe_runs": sum(r.fail_safe for r in results),
            "unnecessary_escalations": sum(r.action == "ESCALATE" and r.expected_action != "ESCALATE" for r in results),
            "outcome": {name: rate(name) for name in OUTCOME},
            "components": {name: rate(name) for name in COMPONENT},
            "avg_revisions": round(statistics.mean(r.revisions for r in results), 3) if n else 0.0,
            "cost_usd_total": round(sum(costs), 4),
            "cost_usd_per_run": round(statistics.mean(costs), 5) if n else 0.0,
            "tokens_per_run": round(statistics.mean(r.tokens for r in results)) if n else 0,
            "llm_calls_per_run": round(statistics.mean(r.llm_calls for r in results), 2) if n else 0.0,
            "retries_total": sum(r.retries for r in results),
            "fallbacks_total": sum(r.fallbacks for r in results),
            "latency_p50_s": _pct(lat, 0.5),
            "latency_p95_s": _pct(lat, 0.95),
            "wall_clock_s": round(wall_s, 2),
            "chaos_injected": chaos_total,
        },
        "scenarios": dict(sorted(scenarios.items())),
        "confusion": confusion,
        "failures": failures,
        "runs": [asdict(r) for r in results],
    }


def _grade(r: RunResult, name: str) -> bool:
    return next((g["passed"] for g in r.grades if g["name"] == name), False)


def render_markdown(rep: dict) -> str:
    s, c = rep["summary"], rep["config"]
    lines = [
        f"# Eval report: {rep['name']}",
        "",
        f"Provider `{c['provider']}`, {s['cases']} cases x {s['trials']} trial(s) = {s['runs']} runs; chaos rate "
        f"{c['chaos']}; mistake rate {c['mistake_rate']}; gullible {c['gullible']}; controls "
        f"{'on' if all(c['controls'].values()) else c['controls']}. Generated {rep['generated_at']}.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| pass@1 | {s['pass_at_1']:.1%} |",
        f"| pass^{s['trials']} (all trials of a case pass) | {s['pass_hat_k']:.1%} |",
        f"| Runs completed | {s['completion_rate']:.1%} |",
        f"| **Safety violations** (executed) | **{s['safety_violations']}** |",
        f"| Unsafe proposals blocked by the policy engine | {s['unsafe_proposals_blocked']} |",
        f"| Fail-safe escalations | {s['fail_safe_runs']} |",
        f"| Unnecessary escalations | {s['unnecessary_escalations']} |",
        f"| Avg review revisions | {s['avg_revisions']} |",
        f"| LLM calls / run | {s['llm_calls_per_run']} |",
        f"| Tokens / run | {s['tokens_per_run']:,} |",
        f"| Cost / run (USD) | {s['cost_usd_per_run']:.5f} |",
        f"| Retries / fallbacks (total) | {s['retries_total']} / {s['fallbacks_total']} |",
        f"| Latency p50 / p95 (s) | {s['latency_p50_s']} / {s['latency_p95_s']} |",
        "",
        "**Outcome graders:** " + ", ".join(f"{k} {v:.0%}" for k, v in s["outcome"].items()),
        "",
        "**Component diagnostics:** " + ", ".join(f"{k} {v:.0%}" for k, v in s["components"].items()),
        "",
    ]
    if s["chaos_injected"]:
        lines += ["**Faults injected:** " + ", ".join(f"{k} {v}" for k, v in sorted(s["chaos_injected"].items())), ""]
    lines += ["## By scenario", "", "| Scenario | Runs | Pass rate | Safety violations |", "|---|---|---|---|"]
    for name, sc in rep["scenarios"].items():
        lines.append(f"| {name} | {sc['runs']} | {sc['pass_rate']:.0%} | {sc['safety_violations']} |")
    if rep["failures"]:
        lines += ["", "## Failures", ""]
        for f in rep["failures"][:40]:
            lines.append(f"- `{f['case_id']}` t{f['trial']} ({f['status']}, {f['action']} vs {f['expected']}): "
                         + "; ".join(f["failed"]) + (f" — error: {f['error'][:160]}" if f.get("error") else ""))
    return "\n".join(lines) + "\n"
