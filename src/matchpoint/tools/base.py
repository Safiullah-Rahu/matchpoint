"""Tool registry: schemas, validation, timeouts, least privilege, truncation.

Every tool declares a pydantic input model. The registry
  * renders a strict JSON schema for the model (and validates again on our
    side, because a schema is a request, not a guarantee);
  * runs the tool under a timeout and turns every failure into an `is_error`
    tool result the model can recover from, instead of an exception that
    kills the run;
  * truncates large results at insertion time and parks the full text in a
    per-run store the model can page through with `read_result`. History is
    append-only: nothing already sent to the model is edited later, which
    keeps prompt caching and thinking-block replay valid;
  * hands each agent only the tools its role needs (`subset`).
"""

from __future__ import annotations

import concurrent.futures as cf
import contextvars
import copy
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ValidationError

from ..llm.types import ToolCall
from ..obs.metrics import METRICS

_POOL = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix="tool")


class Scope(str, Enum):
    READ = "read"
    WRITE = "write"
    TERMINAL = "terminal"


@dataclass
class ResultStore:
    """Full text of truncated tool results, addressable by ref."""

    items: dict[str, str] = field(default_factory=dict)
    page_size: dict[str, int] = field(default_factory=dict)

    def put(self, text: str, page: int) -> str:
        ref = f"r{len(self.items) + 1}"
        self.items[ref] = text
        self.page_size[ref] = page
        return ref


@dataclass
class ToolContext:
    erp: Any
    settings: Any
    corpus: Any
    invoice_id: str
    run_id: str
    results: ResultStore = field(default_factory=ResultStore)
    retrieved_sections: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    fn: Callable[[Any, ToolContext], Any] | None = None
    scope: Scope = Scope.READ
    paged: bool = False  # output is already a page of a stored result; never re-truncate

    def schema(self, strict: bool) -> dict:
        spec = {"name": self.name, "description": self.description, "input_schema": strict_schema(self.input_model)}
        if strict:
            spec["strict"] = True
        return spec


@dataclass
class ToolResult:
    tool_use_id: str
    name: str
    content: str
    is_error: bool = False
    latency_ms: float = 0.0
    truncated: bool = False

    def block(self) -> dict:
        return {"type": "tool_result", "tool_use_id": self.tool_use_id, "content": self.content, "is_error": self.is_error}


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None, *, tracer=None, max_chars: int = 6000, timeout_s: float = 10.0):
        self._tools: dict[str, Tool] = {}
        self.tracer = tracer
        self.max_chars = max_chars
        self.timeout_s = timeout_s
        for t in tools or []:
            self.register(t)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self._tools[tool.name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool:
        return self._tools[name]

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def subset(self, names: list[str]) -> ToolRegistry:
        missing = [n for n in names if n not in self._tools]
        if missing:
            raise KeyError(f"unknown tools: {missing}")
        return ToolRegistry([self._tools[n] for n in names], tracer=self.tracer, max_chars=self.max_chars,
                            timeout_s=self.timeout_s)

    def schemas(self, strict: bool = True) -> list[dict]:
        return [t.schema(strict) for t in self._tools.values()]

    # ----------------------------------------------------------- execution
    def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult(call.id, call.name, f"Unknown tool '{call.name}'. Available tools: {', '.join(self.names)}.",
                              is_error=True)
        span_cm = self.tracer.span(f"tool.{call.name}", kind="tool", **{"tool.name": call.name}) if self.tracer else None
        span = span_cm.__enter__() if span_cm else None
        start = time.perf_counter()
        status = "ok"
        try:
            if span:
                span.set(**{"tool.input": _preview(json.dumps(call.input, default=str))})
            try:
                args = tool.input_model.model_validate(call.input)
            except ValidationError as exc:
                status = "invalid_args"
                return self._finish(span, ToolResult(call.id, call.name, format_validation_error(exc), is_error=True), start)
            run = contextvars.copy_context().run
            future = _POOL.submit(run, tool.fn, args, ctx)
            try:
                value = future.result(timeout=self.timeout_s)
            except cf.TimeoutError:
                status = "timeout"
                return self._finish(span, ToolResult(call.id, call.name, f"Tool timed out after {self.timeout_s:.0f}s.",
                                                     is_error=True), start)
            except ToolError as exc:
                status = "tool_error"
                return self._finish(span, ToolResult(call.id, call.name, str(exc), is_error=True), start)
            except Exception as exc:  # unexpected bug in a tool: surface it, don't crash the run
                status = "exception"
                return self._finish(span, ToolResult(call.id, call.name, f"Tool failed: {type(exc).__name__}: {exc}",
                                                     is_error=True), start)
            text = value if isinstance(value, str) else json.dumps(value, default=str, indent=1)
            truncated = False
            if len(text) > self.max_chars and not tool.paged:
                ref = ctx.results.put(text, page=self.max_chars)
                text = (text[: self.max_chars] + f"\n...[truncated {len(text) - self.max_chars} chars; call "
                        f"read_result with ref='{ref}' and offset={self.max_chars} for more]")
                truncated = True
            return self._finish(span, ToolResult(call.id, call.name, text, truncated=truncated), start)
        finally:
            METRICS.inc("matchpoint_tool_calls_total", tool=call.name, status=status)
            if span_cm:
                span_cm.__exit__(None, None, None)

    def _finish(self, span, result: ToolResult, start: float) -> ToolResult:
        result.latency_ms = round((time.perf_counter() - start) * 1000, 2)
        if span:
            span.set(**{"tool.is_error": result.is_error, "tool.truncated": result.truncated,
                        "tool.output": _preview(result.content)})
            if result.is_error:
                span.status = "error"
                span.error = result.content[:300]
        return result


class ToolError(Exception):
    """Raised by a tool for an expected, model-recoverable failure (bad id, not found)."""


def format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:8]:
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "Invalid arguments. Fix these fields and call the tool again: " + "; ".join(parts)


def _preview(text: str, n: int = 1200) -> str:
    return text if len(text) <= n else text[:n] + f"... [+{len(text) - n} chars]"


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema -> self-contained schema suitable for strict tool use:
    refs inlined, every object closed (additionalProperties false) with all
    properties required, titles/defaults dropped."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})
    # Keywords outside the portable subset; pydantic still enforces them on our side.
    dropped = {"title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "pattern"}

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = copy.deepcopy(defs[node["$ref"].split("/")[-1]])
            return resolve({**target, **{k: v for k, v in node.items() if k != "$ref"}})
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in dropped:
                continue
            if key == "properties":
                out[key] = {name: resolve(prop) for name, prop in value.items()}
            else:
                out[key] = resolve(value)
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = list(out["properties"])
        return out

    return resolve(raw)
