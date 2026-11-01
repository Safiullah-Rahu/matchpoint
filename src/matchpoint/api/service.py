"""Run service: starts runs in background workers and serves their state."""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..agents.workflow import APWorkflow, build_runtime
from ..config import Settings
from ..domain.erp import ERP
from ..guardrails import redact, scan
from ..harness.checkpoint import Checkpointer
from ..llm.factory import make_provider
from ..obs.tracing import build_tracer


class RunService:
    def __init__(self, settings: Settings, *, provider: str = "sim", sim_latency_ms: float = 0.0, workers: int = 4):
        self.settings = settings
        self.provider = provider
        self.sim_latency_ms = sim_latency_ms
        self.tracer, self.spans = build_tracer(settings.app_db, buffered=False)
        self.checkpointer = Checkpointer(settings.app_db)
        self.erp = ERP(settings.erp_db)
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="run")
        self._lock = threading.Lock()
        self._errors: dict[str, str] = {}

    def workflow(self, provider: str | None = None) -> APWorkflow:
        name = provider or self.provider
        p = make_provider(name, self.settings, latency_ms=self.sim_latency_ms if name.startswith("sim") else 0.0,
                          seed=uuid.uuid4().int % 10_000)
        return APWorkflow(build_runtime(self.settings, p, tracer=self.tracer, spans=self.spans))

    def _guard(self, run_id: str, fn, *args, **kwargs) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:  # the graph fails closed; this only catches harness bugs
            with self._lock:
                self._errors[run_id] = f"{type(exc).__name__}: {exc}"
            self.checkpointer.update_run(run_id, status="failed", error=self._errors[run_id])

    def start(self, invoice_id: str, provider: str | None = None) -> str:
        inv = self.erp.invoice(invoice_id)
        if inv is None:
            raise KeyError(invoice_id)
        if inv["status"] != "exception":
            raise ValueError(f"{invoice_id} is already {inv['status']}; reset the demo data to run it again")
        if any(r["status"] in ("running", "interrupted") for r in self.checkpointer.runs(invoice_id)):
            raise ValueError(f"{invoice_id} already has an active run")
        run_id = uuid.uuid4().hex
        wf = self.workflow(provider)
        self.checkpointer.create_run(run_id, invoice_id, wf.rt.provider_name)
        self.pool.submit(self._guard, run_id, wf.start, invoice_id, run_id, create_run=False)
        return run_id

    def decide(self, approval_id: str, approved: bool, approver: str, comment: str) -> str:
        appr = self.checkpointer.decide_approval(approval_id, approved, approver, comment)  # atomic claim
        if appr is None:
            raise KeyError(approval_id)
        run_id = appr["run_id"]
        self.checkpointer.update_run(run_id, status="running")
        run = self.checkpointer.run(run_id) or {}
        wf = self.workflow("anthropic" if "anthropic" in (run.get("provider") or "") else "sim")
        value = {"approved": approved, "approver": approver, "comment": comment}
        self.pool.submit(self._guard, run_id, wf.resume, run_id, value)
        return run_id

    def run_view(self, run_id: str) -> dict | None:
        run = self.checkpointer.run(run_id)
        if run is None:
            return None
        cp = self.checkpointer.latest(run_id)
        state = json.loads(cp["state"]) if cp else None
        return {
            "run": run,
            "state": state,
            "history": self.checkpointer.history(run_id),
            "erp": self.erp.outcome(run["invoice_id"]),
        }

    def activity(self, limit: int = 12) -> list[dict]:
        """Recent runs with their outcome, for the overview feed."""
        out = []
        for r in self.checkpointer.runs(limit=limit):
            cp = self.checkpointer.latest(r["run_id"])
            state = json.loads(cp["state"]) if cp else {}
            res, snap = state.get("resolution") or {}, state.get("snapshot") or {}
            out.append({
                "run_id": r["run_id"], "invoice_id": r["invoice_id"], "status": r["status"], "provider": r["provider"],
                "created_at": r["created_at"], "updated_at": r["updated_at"],
                "vendor_name": snap.get("vendor_name"), "invoice_number": snap.get("invoice_number"),
                "currency": snap.get("currency"), "total": snap.get("total"),
                "action": res.get("action"), "hold_reason": res.get("hold_reason"),
                "escalate_to": res.get("escalate_to"), "amount": res.get("amount"),
                "flags": state.get("flags") or [], "revisions": state.get("revisions", 0),
                "cost_usd": (state.get("usage") or {}).get("cost_usd", 0.0),
            })
        return out

    def scenarios(self) -> list[dict]:
        """One invoice per scenario for the guided demo cards: the first still-open variant, else the first."""
        path = self.settings.home / "eval_cases.jsonl"
        if not path.exists():
            return []
        status = {row["invoice_id"]: row["status"] for row in self.erp.exception_queue()}
        picked: dict[str, dict] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            inv = c["invoice_id"]
            current = picked.get(c["scenario"])
            if current is None or (current["status"] != "exception" and status.get(inv) == "exception"):
                picked[c["scenario"]] = {"scenario": c["scenario"], "invoice_id": inv, "status": status.get(inv)}
        return list(picked.values())

    def queue(self) -> list[dict]:
        latest: dict[str, dict] = {}
        for r in self.checkpointer.runs(limit=1000):
            latest.setdefault(r["invoice_id"], r)
        out = []
        for row in self.erp.exception_queue():
            r = latest.get(row["invoice_id"])
            row["last_run"] = {"run_id": r["run_id"], "status": r["status"]} if r else None
            inv = self.erp.invoice(row["invoice_id"]) or {}
            vendor = self.erp.vendor(row["vendor_id"]) or {}
            row["security"] = bool(
                scan(inv.get("notes") or "").suspicious or scan(redact(inv.get("attachment_text") or "")[0]).suspicious
                or (inv.get("remit_bank_last4") and inv.get("remit_bank_last4") != vendor.get("bank_last4"))
                or (vendor.get("bank_changed_at") and not vendor.get("bank_change_verified"))
            )
            out.append(row)
        return out
