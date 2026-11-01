"""The ablation suite behind the README tables, and the CI gate."""

from __future__ import annotations

from pathlib import Path

import yaml

from ..config import Settings
from ..harness.reliability import Controls
from .runner import EvalConfig, run_eval

SUITE = [
    EvalConfig(name="reference", trials=3),
    EvalConfig(name="chaos-controls-on", trials=3, chaos=0.25),
    EvalConfig(name="chaos-controls-off", trials=3, chaos=0.25, controls=Controls.off()),
    EvalConfig(name="faulty-model", trials=3, mistake_rate=0.35, gullible=True),
]


def run_suite(settings: Settings, *, workers: int = 8, out_dir: Path | None = None) -> dict:
    reports = {}
    for cfg in SUITE:
        cfg.workers = workers
        reports[cfg.name] = run_eval(settings, cfg, out_dir=out_dir)
    out = out_dir or settings.home / "eval_reports"
    (out / "suite.md").write_text(render_suite(reports), encoding="utf-8")
    return reports


def render_suite(reports: dict) -> str:
    rows = [
        "| Suite | Runs | pass@1 | pass^3 | Safety violations | Unsafe proposals blocked | Fail-safe | "
        "First-pass action | Faults injected | Retries |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, rep in reports.items():
        s = rep["summary"]
        rows.append(
            f"| {name} | {s['runs']} | {s['pass_at_1']:.1%} | {s['pass_hat_k']:.1%} | {s['safety_violations']} | "
            f"{s['unsafe_proposals_blocked']} | {s['fail_safe_runs']} | {s['components']['first_pass_action']:.1%} | "
            f"{sum(s['chaos_injected'].values())} | {s['retries_total']} |"
        )
    return "# Eval suite (sim reference policy)\n\n" + "\n".join(rows) + "\n"


def check_gate(report: dict, thresholds_path: Path) -> list[str]:
    t = yaml.safe_load(thresholds_path.read_text(encoding="utf-8"))
    s = report["summary"]
    problems = []
    if s["safety_violations"] > t.get("max_safety_violations", 0):
        problems.append(f"safety_violations {s['safety_violations']} > {t.get('max_safety_violations', 0)}")
    for key in ("pass_at_1", "pass_hat_k", "completion_rate"):
        if key in t and s[key] < t[key]:
            problems.append(f"{key} {s[key]:.3f} < {t[key]}")
    for key, minimum in (t.get("outcome") or {}).items():
        if s["outcome"].get(key, 0) < minimum:
            problems.append(f"outcome.{key} {s['outcome'].get(key, 0):.3f} < {minimum}")
    if "max_cost_usd_per_run" in t and s["cost_usd_per_run"] > t["max_cost_usd_per_run"]:
        problems.append(f"cost_usd_per_run {s['cost_usd_per_run']} > {t['max_cost_usd_per_run']}")
    return problems
