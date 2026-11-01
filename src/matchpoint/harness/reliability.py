"""Retry with backoff, circuit breakers, budgets, and the controls switchboard."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from ..config import BudgetConfig
from ..domain.models import UsageTotals
from ..llm.errors import RetryableLLMError
from ..llm.pricing import cost_usd
from ..llm.types import Usage

T = TypeVar("T")


@dataclass(frozen=True)
class Controls:
    """Reliability controls, individually switchable for ablation evals."""

    retries: bool = True
    fallback: bool = True
    validation_repair: bool = True
    nudges: bool = True
    loop_guard: bool = True

    @classmethod
    def off(cls) -> Controls:
        return cls(retries=False, fallback=False, validation_repair=False, nudges=False, loop_guard=False)


@dataclass
class RetryPolicy:
    max_attempts: int = 4
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay_s)
        return self.rng.uniform(0, min(self.max_delay_s, self.base_delay_s * 2 ** (attempt - 1)))  # full jitter

    def call(self, fn: Callable[[], T], on_retry: Callable[[int, Exception, float], None] | None = None) -> T:
        attempt = 0
        while True:
            attempt += 1
            try:
                return fn()
            except RetryableLLMError as exc:
                if attempt >= self.max_attempts:
                    raise
                d = self.delay(attempt, exc.retry_after)
                if on_retry:
                    on_retry(attempt, exc, d)
                self.sleep(d)


class CircuitBreaker:
    """Closed -> open after N consecutive failures; half-open probe after a cooldown."""

    def __init__(self, failure_threshold: int = 5, reset_after_s: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.failure_threshold = failure_threshold
        self.reset_after_s = reset_after_s
        self.clock = clock
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.clock() - self.opened_at >= self.reset_after_s else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        with self._lock:
            self.failures = 0
            self.opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold or self.state == "half_open":
                self.opened_at = self.clock()


class BudgetExceeded(Exception):
    def __init__(self, kind: str, detail: str):
        super().__init__(f"budget exceeded: {kind} ({detail})")
        self.kind = kind


class Budget:
    """Per-run spend limits. Checked before every model call."""

    def __init__(self, cfg: BudgetConfig, start: UsageTotals | None = None):
        self.cfg = cfg
        self.totals = (start or UsageTotals()).model_copy()
        self.started = time.monotonic()
        self._lock = threading.Lock()

    def check(self) -> None:
        t, c = self.totals, self.cfg
        if t.llm_calls >= c.max_llm_calls:
            raise BudgetExceeded("llm_calls", f"{t.llm_calls}/{c.max_llm_calls}")
        tokens = t.input_tokens + t.output_tokens + t.cache_read_tokens + t.cache_write_tokens
        if tokens >= c.max_total_tokens:
            raise BudgetExceeded("tokens", f"{tokens}/{c.max_total_tokens}")
        if t.cost_usd >= c.max_cost_usd:
            raise BudgetExceeded("cost", f"${t.cost_usd:.4f}/${c.max_cost_usd:.2f}")
        elapsed = time.monotonic() - self.started
        if elapsed >= c.max_wall_seconds:
            raise BudgetExceeded("wall_clock", f"{elapsed:.0f}s/{c.max_wall_seconds:.0f}s")

    def charge_llm(self, model: str, usage: Usage) -> float:
        cost = cost_usd(model, usage)
        with self._lock:
            t = self.totals
            t.llm_calls += 1
            t.input_tokens += usage.input_tokens
            t.output_tokens += usage.output_tokens
            t.cache_read_tokens += usage.cache_read_tokens
            t.cache_write_tokens += usage.cache_write_tokens
            t.cost_usd = round(t.cost_usd + cost, 6)
        return cost

    def charge_tool(self, n: int = 1) -> None:
        with self._lock:
            self.totals.tool_calls += n

    def note_retry(self) -> None:
        with self._lock:
            self.totals.retries += 1

    def note_fallback(self) -> None:
        with self._lock:
            self.totals.fallbacks += 1

    def snapshot(self) -> UsageTotals:
        with self._lock:
            return self.totals.model_copy()
