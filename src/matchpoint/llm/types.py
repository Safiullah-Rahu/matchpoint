"""Provider-neutral request/response types.

Messages hold content blocks. Blocks the harness creates (text, tool_result)
are plain dicts in the Anthropic wire shape. Assistant content is stored as the
provider returned it (`raw_content`) and replayed verbatim, so thinking blocks
and anything else the provider needs back survive the round trip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: list[Any]


@dataclass
class LLMRequest:
    role: str  # agent role, e.g. "investigator"
    model: str
    system: str
    messages: list[Message]
    tools: list[dict]
    max_tokens: int
    effort: str = "medium"
    metadata: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    model: str
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: Usage
    raw_content: list[Any]
    latency_ms: float = 0.0
    provider: str = ""
    request_id: str | None = None


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def block_type(block: Any) -> str | None:
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


def block_attr(block: Any, key: str, default: Any = None) -> Any:
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def to_jsonable(block: Any) -> Any:
    """Best-effort JSON form of a content block (for hashing, traces, cassettes)."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    return {"type": block_type(block), "repr": repr(block)}
