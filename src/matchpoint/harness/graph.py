"""A small, typed state graph: nodes, edges, conditional routing, interrupts,
checkpoints after every node, and fail-closed error routing.

Control flow is deterministic code; models run *inside* nodes. That is the
core design choice of this harness: the graph decides what happens next, an
LLM never does. It maps one-to-one onto LangGraph's StateGraph
(add_node / add_edge / add_conditional_edges / interrupt / checkpointer); see
docs/harness.md for why this project ships its own ~200 lines instead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from .checkpoint import Checkpointer

END = "__end__"
S = TypeVar("S", bound=BaseModel)


@dataclass
class Interrupt:
    """Returned by a node to pause the run until `resume(run_id, value)`.

    On resume the same node runs again with `ctx.resume_value` set, which is
    the LangGraph `interrupt()` contract."""

    reason: str
    payload: dict


@dataclass
class RunContext:
    run_id: str
    resume_value: Any = None

    def sync(self, state: S) -> S:  # hook for subclasses (e.g. copy budget usage into state)
        return state


@dataclass
class RunOutcome(Generic[S]):
    run_id: str
    status: str  # completed | interrupted | failed
    state: S
    interrupt: dict | None = None
    error: str | None = None


NodeFn = Callable[[Any, Any], "dict | Interrupt"]


class StateGraph(Generic[S]):
    def __init__(self, state_type: type[S]):
        self.state_type = state_type
        self.nodes: dict[str, NodeFn] = {}
        self.edges: dict[str, str] = {}
        self.branches: dict[str, tuple[Callable[[S], str], list[str]]] = {}
        self.entry: str | None = None
        self.error_handler: str | None = None

    def add_node(self, name: str, fn: NodeFn) -> StateGraph[S]:
        if name in self.nodes or name == END:
            raise ValueError(f"bad or duplicate node {name}")
        self.nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> StateGraph[S]:
        self.edges[src] = dst
        return self

    def add_conditional_edges(self, src: str, router: Callable[[S], str], destinations: list[str]) -> StateGraph[S]:
        self.branches[src] = (router, destinations)
        return self

    def set_entry(self, name: str) -> StateGraph[S]:
        self.entry = name
        return self

    def set_error_handler(self, name: str) -> StateGraph[S]:
        """Node that any other node's unhandled exception routes to (fail closed)."""
        self.error_handler = name
        return self

    def validate(self) -> None:
        if self.entry not in self.nodes:
            raise ValueError("entry node not set")
        for src, dst in self.edges.items():
            if src not in self.nodes or (dst != END and dst not in self.nodes):
                raise ValueError(f"bad edge {src} -> {dst}")
        for src, (_, dests) in self.branches.items():
            for d in dests:
                if d != END and d not in self.nodes:
                    raise ValueError(f"bad branch {src} -> {d}")
        for name in self.nodes:
            if name not in self.edges and name not in self.branches:
                raise ValueError(f"node {name} has no outgoing edge")

    def compile(self, checkpointer: Checkpointer, tracer, *, max_steps: int = 40) -> CompiledGraph[S]:
        self.validate()
        return CompiledGraph(self, checkpointer, tracer, max_steps)

    def mermaid(self) -> str:
        lines = ["flowchart TD", f"    START([start]) --> {self.entry}"]
        for src, dst in self.edges.items():
            lines.append(f"    {src} --> {'END([end])' if dst == END else dst}")
        for src, (_, dests) in self.branches.items():
            for d in dests:
                lines.append(f"    {src} -.-> {'END([end])' if d == END else d}")
        if self.error_handler:
            lines.append(f"    any[[any node error]] -.-> {self.error_handler}")
        return "\n".join(lines)


class CompiledGraph(Generic[S]):
    def __init__(self, graph: StateGraph[S], checkpointer: Checkpointer, tracer, max_steps: int):
        self.g = graph
        self.checkpointer = checkpointer
        self.tracer = tracer
        self.max_steps = max_steps

    def invoke(self, state: S, ctx: RunContext) -> RunOutcome[S]:
        return self._execute(state, self.g.entry, ctx, resumed=False)

    def resume(self, ctx: RunContext) -> RunOutcome[S]:
        cp = self.checkpointer.latest(ctx.run_id)
        if cp is None:
            raise KeyError(f"no checkpoint for run {ctx.run_id}")
        state = self.g.state_type.model_validate_json(cp["state"])
        if cp["next_node"] in (None, END):
            return RunOutcome(ctx.run_id, "completed", state)
        return self._execute(state, cp["next_node"], ctx, resumed=True)

    def _next(self, node: str, state: S) -> str:
        if node in self.g.branches:
            router, dests = self.g.branches[node]
            dst = router(state)
            if dst not in dests:
                raise ValueError(f"router for {node} returned {dst!r}, not in {dests}")
            return dst
        return self.g.edges[node]

    def _checkpoint(self, ctx: RunContext, node: str, nxt: str | None, status: str, state: S) -> None:
        self.checkpointer.save(ctx.run_id, node, nxt, status, state.model_dump_json())

    def _execute(self, state: S, node: str, ctx: RunContext, *, resumed: bool) -> RunOutcome[S]:
        cp = self.checkpointer
        with self.tracer.span("run", kind="run", trace_id=ctx.run_id, run_id=ctx.run_id, resumed=resumed,
                              entry_node=node) as root:
            steps = 0
            while node != END:
                steps += 1
                if steps > self.max_steps:
                    err = f"max_steps ({self.max_steps}) exceeded; possible cycle"
                    cp.update_run(ctx.run_id, status="failed", error=err)
                    root.status, root.error = "error", err
                    return RunOutcome(ctx.run_id, "failed", state, error=err)
                cp.update_run(ctx.run_id, status="running", current_node=node)
                try:
                    with self.tracer.span(f"node.{node}", kind="node", node=node):
                        out = self.g.nodes[node](state, ctx)
                except Exception as exc:
                    err = f"{node}: {type(exc).__name__}: {exc}"
                    state = ctx.sync(state.model_copy(update={"errors": [*getattr(state, "errors", []), err]}))
                    if self.g.error_handler and node != self.g.error_handler:
                        root.event("node_error", node=node, error=err[:300], routed_to=self.g.error_handler)
                        self._checkpoint(ctx, node, self.g.error_handler, "error", state)
                        node = self.g.error_handler
                        continue
                    self._checkpoint(ctx, node, node, "failed", state)
                    cp.update_run(ctx.run_id, status="failed", error=err)
                    root.status, root.error = "error", err
                    return RunOutcome(ctx.run_id, "failed", state, error=err)

                if isinstance(out, Interrupt):
                    state = ctx.sync(state)
                    self._checkpoint(ctx, node, node, "interrupted", state)
                    payload = {"reason": out.reason, **out.payload}
                    cp.update_run(ctx.run_id, status="interrupted", current_node=node, interrupt=payload)
                    root.set(interrupted_at=node)
                    return RunOutcome(ctx.run_id, "interrupted", state, interrupt=payload)

                state = ctx.sync(self.g.state_type.model_validate({**state.model_dump(), **out}))
                ctx.resume_value = None
                nxt = self._next(node, state)
                self._checkpoint(ctx, node, nxt, "running", state)
                node = nxt

            cp.update_run(ctx.run_id, status="completed", current_node=END, interrupt=None)
            return RunOutcome(ctx.run_id, "completed", state)
