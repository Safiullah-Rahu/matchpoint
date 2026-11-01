"""Per-model prices (USD per million tokens), Claude API first-party rates as of
October 2026. Cache writes are billed at 1.25x input for the 5-minute TTL.
Verify against the live pricing page before using these numbers for budgeting."""

from __future__ import annotations

from .types import Usage

PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
}
DEFAULT = {"input": 4.00, "output": 20.00, "cache_read": 0.40}


def cost_usd(model: str, usage: Usage) -> float:
    p = PRICES.get(model, DEFAULT)
    return round(
        (usage.input_tokens * p["input"] + usage.output_tokens * p["output"] + usage.cache_read_tokens * p["cache_read"]
         + usage.cache_write_tokens * p["input"] * 1.25) / 1_000_000,
        6,
    )
