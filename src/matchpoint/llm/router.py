"""Model routing: role -> tier -> model, with risk escalation and fallback.

For each call the router
  1. picks a tier from the role config, upgrading to the high-risk tier for
     roles listed in routing.escalate_roles when the case is high risk;
  2. walks [tier, *fallback_chain[tier]], skipping models whose circuit is open;
  3. retries each model on retryable errors with jittered backoff;
  4. falls to the next model on exhausted retries or a refusal;
  5. charges the run budget and records a span per attempt.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from ..config import Settings
from ..harness.reliability import Budget, CircuitBreaker, Controls, RetryPolicy
from ..obs.metrics import METRICS
from .errors import AllModelsFailedError, CircuitOpenError, LLMError, RefusalError, RetryableLLMError
from .types import LLMRequest, LLMResponse, Message, to_jsonable


@dataclass(frozen=True)
class Route:
    role: str
    tier: str
    model: str
    effort: str
    max_tokens: int


class ModelRouter:
    def __init__(self, provider, settings: Settings, *, tracer, retry: RetryPolicy | None = None,
                 controls: Controls = Controls(), breakers: dict[str, CircuitBreaker] | None = None):
        self.provider = provider
        self.settings = settings
        self.tracer = tracer
        self.controls = controls
        r = settings.retry
        self.retry = retry or RetryPolicy(r.max_attempts, r.base_delay_s, r.max_delay_s)
        if not controls.retries:
            self.retry = RetryPolicy(1, 0, 0, sleep=lambda _s: None)
        self._breakers = breakers if breakers is not None else {}
        self._lock = threading.Lock()

    def breaker(self, model: str) -> CircuitBreaker:
        with self._lock:
            if model not in self._breakers:
                cb = self.settings.circuit_breaker
                self._breakers[model] = CircuitBreaker(cb.failure_threshold, cb.reset_after_s)
            return self._breakers[model]

    def route(self, role: str, risk: str = "medium") -> Route:
        rc = self.settings.role(role)
        tier = rc.tier
        if risk == "high" and role in self.settings.routing.escalate_roles:
            tier = self.settings.routing.high_risk_tier
        return Route(role, tier, self.settings.models[tier], rc.effort, rc.max_tokens)

    def complete(self, *, role: str, system: str, messages: list[Message], tools: list[dict], budget: Budget,
                 risk: str = "medium") -> LLMResponse:
        primary = self.route(role, risk)
        tiers = [primary.tier] + (self.settings.fallback_chain.get(primary.tier, []) if self.controls.fallback else [])
        last: LLMError | None = None
        for i, tier in enumerate(tiers):
            model = self.settings.models[tier]
            if i > 0:
                budget.note_fallback()
                METRICS.inc("matchpoint_llm_fallbacks_total", role=role, to_model=model)
            breaker = self.breaker(model)
            if not breaker.allow():
                last = CircuitOpenError(f"circuit open for {model}")
                continue
            req = LLMRequest(role=role, model=model, system=system, messages=messages, tools=tools,
                             max_tokens=primary.max_tokens, effort=primary.effort, metadata={"tier": tier})
            try:
                resp = self.retry.call(lambda req=req: self._attempt(req, budget),
                                       on_retry=lambda n, e, d, m=model: self._on_retry(role, m, n, e, d, budget))
                breaker.record_success()
                return resp
            except (RetryableLLMError, RefusalError) as exc:
                breaker.record_failure()
                last = exc
                continue
        raise AllModelsFailedError(f"{role}: all models failed ({type(last).__name__}: {last})")

    def _attempt(self, req: LLMRequest, budget: Budget) -> LLMResponse:
        budget.check()
        with self.tracer.span(f"llm.{req.role}", kind="llm", **{
            "gen_ai.operation.name": "chat",
            "gen_ai.system": getattr(self.provider, "name", "unknown"),
            "gen_ai.request.model": req.model,
            "gen_ai.request.max_tokens": req.max_tokens,
            "matchpoint.effort": req.effort,
            "matchpoint.tier": req.metadata.get("tier"),
            "matchpoint.role": req.role,
            "matchpoint.messages": len(req.messages),
        }) as span:
            started = time.perf_counter()
            try:
                resp = self.provider.complete(req)
            except LLMError as exc:
                METRICS.inc("matchpoint_llm_errors_total", model=req.model, kind=type(exc).__name__)
                raise
            cost = budget.charge_llm(req.model, resp.usage)
            elapsed = time.perf_counter() - started
            METRICS.inc("matchpoint_llm_calls_total", role=req.role, model=req.model)
            METRICS.inc("matchpoint_llm_cost_usd_total", cost, model=req.model)
            METRICS.observe("matchpoint_llm_latency_seconds", elapsed, model=req.model)
            span.set(**{
                "gen_ai.response.model": resp.model,
                "gen_ai.response.finish_reasons": [resp.stop_reason],
                "gen_ai.usage.input_tokens": resp.usage.input_tokens,
                "gen_ai.usage.output_tokens": resp.usage.output_tokens,
                "gen_ai.usage.cache_read_input_tokens": resp.usage.cache_read_tokens,
                "gen_ai.usage.cache_creation_input_tokens": resp.usage.cache_write_tokens,
                "matchpoint.cost_usd": cost,
                "matchpoint.tool_calls": [c.name for c in resp.tool_calls],
                "matchpoint.output": _preview(resp),
                "matchpoint.request_id": resp.request_id,
            })
            return resp

    def _on_retry(self, role: str, model: str, attempt: int, exc: Exception, delay: float, budget: Budget) -> None:
        budget.note_retry()
        METRICS.inc("matchpoint_llm_retries_total", role=role, model=model)
        span = self.tracer.current()
        if span is not None:
            span.event("retry", model=model, attempt=attempt, error=str(exc)[:200], delay_s=round(delay, 3))


def _preview(resp: LLMResponse) -> str:
    parts = []
    if resp.text:
        parts.append(resp.text[:600])
    for c in resp.tool_calls:
        parts.append(f"-> {c.name}({str(c.input)[:600]})")
    if not parts:
        parts = [str([to_jsonable(b) for b in resp.raw_content])[:400]]
    return "\n".join(parts)
