import pytest

from matchpoint.config import BudgetConfig
from matchpoint.harness.reliability import Budget, BudgetExceeded, CircuitBreaker, Controls, RetryPolicy
from matchpoint.llm.errors import AllModelsFailedError, NonRetryableLLMError, RefusalError, RetryableLLMError
from matchpoint.llm.router import ModelRouter
from matchpoint.llm.types import LLMResponse, Usage
from matchpoint.obs.tracing import Tracer


def test_retry_backs_off_and_honours_retry_after():
    sleeps, n = [], {"calls": 0}

    def flaky():
        n["calls"] += 1
        if n["calls"] < 3:
            raise RetryableLLMError("429", retry_after=1.5 if n["calls"] == 1 else None)
        return "ok"

    policy = RetryPolicy(max_attempts=4, base_delay_s=0.5, max_delay_s=8, sleep=sleeps.append)
    assert policy.call(flaky) == "ok"
    assert sleeps[0] == 1.5 and 0 <= sleeps[1] <= 1.0


def test_retry_gives_up_after_max_attempts():
    policy = RetryPolicy(max_attempts=3, sleep=lambda _s: None)
    calls = []
    with pytest.raises(RetryableLLMError):
        policy.call(lambda: calls.append(1) or (_ for _ in ()).throw(RetryableLLMError("503")))
    assert len(calls) == 3


def test_circuit_breaker_opens_and_half_opens():
    now = [0.0]
    cb = CircuitBreaker(failure_threshold=2, reset_after_s=10, clock=lambda: now[0])
    cb.record_failure()
    assert cb.allow()
    cb.record_failure()
    assert not cb.allow() and cb.state == "open"
    now[0] = 11
    assert cb.state == "half_open" and cb.allow()
    cb.record_failure()  # failed probe re-opens
    assert cb.state == "open"
    now[0] = 22
    cb.record_success()
    assert cb.state == "closed"


def test_budget_limits():
    b = Budget(BudgetConfig(max_llm_calls=2, max_cost_usd=10))
    b.charge_llm("claude-haiku-5-5", Usage(10, 10))
    b.check()
    b.charge_llm("claude-haiku-5-5", Usage(10, 10))
    with pytest.raises(BudgetExceeded):
        b.check()


class ScriptedProvider:
    name = "scripted"

    def __init__(self, behaviour):
        self.behaviour = behaviour  # model -> exception or "ok"
        self.calls = []

    def complete(self, req):
        self.calls.append(req.model)
        b = self.behaviour.get(req.model, "ok")
        if isinstance(b, Exception):
            raise b
        return LLMResponse(req.model, "hi", [], "end_turn", Usage(1, 1), [{"type": "text", "text": "hi"}])


def _router(settings, provider, controls=Controls()):
    return ModelRouter(provider, settings, tracer=Tracer(), controls=controls,
                       retry=RetryPolicy(max_attempts=2, sleep=lambda _s: None))


def test_router_routes_high_risk_resolver_to_frontier(settings):
    r = _router(settings, ScriptedProvider({}))
    assert r.route("resolver", "medium").model == settings.models["balanced"]
    assert r.route("resolver", "high").model == settings.models["frontier"]
    assert r.route("triage", "high").model == settings.models["fast"]


def test_router_falls_back_on_exhausted_retries_and_refusals(settings):
    budget = Budget(settings.budget)
    frontier, balanced = settings.models["frontier"], settings.models["balanced"]
    p = ScriptedProvider({frontier: RetryableLLMError("529 overloaded")})
    resp = _router(settings, p).complete(role="reviewer", system="s", messages=[], tools=[], budget=budget)
    assert resp.model == balanced and p.calls == [frontier, frontier, balanced]
    assert budget.totals.retries == 1 and budget.totals.fallbacks == 1

    p = ScriptedProvider({frontier: RefusalError("refused")})
    assert _router(settings, p).complete(role="reviewer", system="s", messages=[], tools=[], budget=budget).model == balanced


def test_router_does_not_mask_bugs_and_respects_controls_off(settings):
    budget = Budget(settings.budget)
    frontier = settings.models["frontier"]
    with pytest.raises(NonRetryableLLMError):
        _router(settings, ScriptedProvider({frontier: NonRetryableLLMError("400")})).complete(
            role="reviewer", system="s", messages=[], tools=[], budget=budget)
    p = ScriptedProvider({frontier: RetryableLLMError("529")})
    with pytest.raises(AllModelsFailedError):
        _router(settings, p, Controls.off()).complete(role="reviewer", system="s", messages=[], tools=[], budget=budget)
    assert p.calls == [frontier]  # no retry, no fallback
