"""FastAPI app: REST API for runs, approvals, traces and evals, plus the web UI."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..config import Settings, load_settings
from ..obs.metrics import METRICS
from ..rag import PolicyCorpus
from .service import RunService

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


class StartRun(BaseModel):
    invoice_id: str
    provider: str | None = None


class Decision(BaseModel):
    approved: bool
    approver: str = "controller"
    comment: str = ""


def create_app(settings: Settings | None = None, *, provider: str = "sim", sim_latency_ms: float = 0.0) -> FastAPI:
    settings = settings or load_settings()
    if not settings.erp_db.exists():
        from ..domain.seed import seed

        seed(settings)
    svc = RunService(settings, provider=provider, sim_latency_ms=sim_latency_ms)
    corpus = PolicyCorpus.load()
    token = os.environ.get("MATCHPOINT_API_TOKEN")
    app = FastAPI(title="Matchpoint", version=__version__, description="Agent harness for AP invoice exceptions")
    app.state.service = svc

    @app.middleware("http")
    async def auth(request: Request, call_next):
        # Optional bearer token for anything under /api (health stays open for load balancers).
        if token and request.url.path.startswith("/api") and request.url.path != "/api/health":
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/ui"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "provider": svc.provider,
                "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")), "clock": settings.clock.isoformat()}

    @app.get("/api/config")
    def config():
        return {"models": settings.models, "fallback_chain": settings.fallback_chain,
                "roles": {k: v.model_dump() for k, v in settings.roles.items()}, "routing": settings.routing.model_dump(),
                "policy": settings.policy.model_dump(), "budget": settings.budget.model_dump(),
                "harness": settings.harness.model_dump()}

    @app.get("/api/queue")
    def queue():
        return svc.queue()

    @app.post("/api/runs")
    def start(body: StartRun):
        try:
            return {"run_id": svc.start(body.invoice_id, body.provider)}
        except KeyError as exc:
            raise HTTPException(404, f"unknown invoice {exc}") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/runs")
    def runs(invoice_id: str | None = None, limit: int = 50):
        return svc.checkpointer.runs(invoice_id, limit)

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        view = svc.run_view(run_id)
        if view is None:
            raise HTTPException(404, "unknown run")
        return view

    @app.get("/api/runs/{run_id}/trace")
    def trace(run_id: str):
        return svc.spans.get_trace(run_id)

    @app.get("/api/approvals")
    def approvals(status: str | None = "pending"):
        return svc.checkpointer.approvals(status or None)

    @app.post("/api/approvals/{approval_id}")
    def decide(approval_id: str, body: Decision):
        try:
            return {"run_id": svc.decide(approval_id, body.approved, body.approver, body.comment)}
        except KeyError as exc:
            raise HTTPException(404, "unknown approval") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/policy/{section_id}")
    def policy(section_id: str):
        chunk = corpus.get(section_id)
        if chunk is None:
            raise HTTPException(404, "unknown section")
        return {"section_id": chunk.section_id, "title": chunk.title, "text": chunk.text, "document": chunk.document}

    @app.get("/api/evals")
    def evals():
        out = {}
        for path in sorted((settings.home / "eval_reports").glob("*.json")):
            if path.stem == "latest":
                continue
            rep = json.loads(path.read_text(encoding="utf-8"))
            out[path.stem] = {"summary": rep["summary"], "config": rep["config"], "generated_at": rep["generated_at"]}
        return out

    @app.get("/api/evals/{name}")
    def eval_report(name: str):
        path = settings.home / "eval_reports" / f"{Path(name).name}.json"
        if not path.exists():
            raise HTTPException(404, "unknown report")
        rep = json.loads(path.read_text(encoding="utf-8"))
        rep.pop("runs", None)
        return rep

    @app.get("/api/graph")
    def graph():
        return {"mermaid": svc.workflow("sim").build_graph().mermaid()}

    @app.post("/api/reset")
    def reset():
        if os.environ.get("MATCHPOINT_ALLOW_RESET", "1") != "1":
            raise HTTPException(403, "reset disabled")
        from ..domain.db import session
        from ..domain.seed import seed

        seed(settings)
        with session(settings.app_db, wal=True) as conn:
            for table in ("runs", "checkpoints", "approvals", "spans"):
                conn.execute(f"DELETE FROM {table}")
        return {"status": "reseeded"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        return METRICS.render()

    @app.get("/")
    def index():
        return FileResponse(UI_DIR / "index.html")

    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")
    return app
