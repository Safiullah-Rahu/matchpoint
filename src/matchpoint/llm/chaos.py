"""Fault injection: wrap any provider and make it misbehave on purpose.

Used by the chaos eval to show which harness controls absorb which failures:

  rate_limit / overloaded / server_error / timeout  -> retry + backoff, fallback
  malformed_args   (drops a required field)          -> validation repair
  unknown_tool     (calls a tool that doesn't exist)  -> error result + repair
  no_tool_call     (ends the turn with prose)        -> nudge
"""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field

from .errors import RetryableLLMError
from .types import LLMRequest, LLMResponse, ToolCall, text_block, to_jsonable

TRANSPORT = ("rate_limit", "overloaded", "server_error", "timeout")
BEHAVIOUR = ("malformed_args", "unknown_tool", "no_tool_call")


@dataclass
class ChaosStats:
    injected: dict[str, int] = field(default_factory=dict)

    def add(self, kind: str) -> None:
        self.injected[kind] = self.injected.get(kind, 0) + 1


class ChaosProvider:
    def __init__(self, inner, *, rate: float, seed: int = 0, kinds: tuple[str, ...] = TRANSPORT + BEHAVIOUR):
        self.inner = inner
        self.name = f"chaos({getattr(inner, 'name', 'provider')})"
        self.rate = rate
        self.kinds = kinds
        self.rng = random.Random(seed)
        self.stats = ChaosStats()

    def complete(self, req: LLMRequest) -> LLMResponse:
        if self.rng.random() >= self.rate:
            return self.inner.complete(req)
        kind = self.rng.choice(self.kinds)
        self.stats.add(kind)
        if kind == "rate_limit":
            raise RetryableLLMError("chaos: 429 rate limited", status=429, retry_after=0.0)
        if kind == "overloaded":
            raise RetryableLLMError("chaos: 529 overloaded", status=529)
        if kind == "server_error":
            raise RetryableLLMError("chaos: 500 internal server error", status=500)
        if kind == "timeout":
            raise RetryableLLMError("chaos: request timed out")

        resp = self.inner.complete(req)
        if kind == "no_tool_call" or not resp.tool_calls:
            return LLMResponse(resp.model, "I believe this is resolved.", [], "end_turn", resp.usage,
                               [text_block("I believe this is resolved.")], resp.latency_ms, resp.provider)
        blocks = [copy.deepcopy(to_jsonable(b)) for b in resp.raw_content]
        target = next(b for b in blocks if b.get("type") == "tool_use")
        if kind == "unknown_tool":
            target["name"] = "lookup_everything"
        else:  # malformed_args
            if target["input"]:
                target["input"].pop(self.rng.choice(sorted(target["input"])))
            else:
                target["input"] = {"unexpected": True}
        calls = [ToolCall(b["id"], b["name"], b["input"]) for b in blocks if b.get("type") == "tool_use"]
        return LLMResponse(resp.model, resp.text, calls, resp.stop_reason, resp.usage, blocks, resp.latency_ms,
                           resp.provider, resp.request_id)
