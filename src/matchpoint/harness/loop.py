"""The agent loop: one role, a brief, a tool subset, and a terminal tool.

    brief -> [model -> tool calls -> tool results]* -> terminal tool -> artifact

Controls (each switchable for ablations, see harness.reliability.Controls):
  * budget check before every model call (cost, tokens, calls, wall clock);
  * schema validation of the terminal artifact, with the validation error fed
    back so the model can repair it (`validation_repair`);
  * a nudge when the model ends its turn without a tool call (`nudges`);
  * loop guard: identical calls beyond a limit get an error result, not a
    re-execution (`loop_guard`);
  * a context nudge when the transcript nears the role's soft token limit;
  * parallel execution of independent read-only tool calls;
  * max_turns per role: the loop always terminates.
"""

from __future__ import annotations

import concurrent.futures as cf
import contextvars
import json
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from ..llm.types import Message, text_block
from ..tools.base import ToolContext, ToolRegistry, ToolResult, format_validation_error
from .reliability import Budget, Controls

_CALL_POOL = cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="agent-call")

NUDGE = "You ended your turn without calling a tool. Call {tool} now with your result. Do not reply in prose."
CONTEXT_NUDGE = "Context budget is nearly used up. Call {tool} now with the evidence you already have."
LOOP_LIMIT = 3


class AgentProtocolError(Exception):
    """The model did not produce a valid artifact within its turn budget."""


@dataclass
class AgentSpec:
    role: str
    system: str
    tools: list[str]
    terminal: str
    output: type[BaseModel]


@dataclass
class AgentRun:
    output: BaseModel
    turns: int
    tool_calls: int
    model: str
    messages: list[Message]


class AgentLoop:
    def __init__(self, router, registry: ToolRegistry, tracer, settings, controls: Controls = Controls()):
        self.router = router
        self.registry = registry
        self.tracer = tracer
        self.settings = settings
        self.controls = controls

    def run(self, spec: AgentSpec, brief: str, *, ctx: ToolContext, budget: Budget, risk: str = "medium") -> AgentRun:
        role_cfg = self.settings.role(spec.role)
        tools = self.registry.subset([*spec.tools, spec.terminal])
        schemas = tools.schemas(strict=self.settings.harness.strict_tools)
        soft_limit = self.settings.harness.context_soft_limit_tokens
        messages: list[Message] = [Message("user", [text_block(brief)])]
        seen: Counter[str] = Counter()
        nudges = 0
        n_calls = 0
        model = ""

        with self.tracer.span(f"agent.{spec.role}", kind="agent", **{"agent.role": spec.role, "agent.risk": risk,
                                                                     "agent.tools": [*spec.tools, spec.terminal]}) as span:
            for turn in range(1, role_cfg.max_turns + 1):
                resp = self.router.complete(role=spec.role, system=spec.system, messages=messages, tools=schemas,
                                            budget=budget, risk=risk)
                model = resp.model
                messages.append(Message("assistant", list(resp.raw_content)))

                if not resp.tool_calls:
                    if self.controls.nudges and nudges < 2:
                        nudges += 1
                        span.event("nudge", turn=turn, reason="no_tool_call")
                        messages.append(Message("user", [text_block(NUDGE.format(tool=spec.terminal))]))
                        continue
                    raise AgentProtocolError(f"{spec.role} ended its turn without calling {spec.terminal}")

                results: list[ToolResult | None] = [None] * len(resp.tool_calls)
                pending: list[tuple[int, object]] = []
                output: BaseModel | None = None
                for i, call in enumerate(resp.tool_calls):
                    key = f"{call.name}:{json.dumps(call.input, sort_keys=True, default=str)}"
                    seen[key] += 1
                    if call.name == spec.terminal:
                        try:
                            output = spec.output.model_validate(call.input)
                            results[i] = ToolResult(call.id, call.name, "Accepted.")
                        except ValidationError as exc:
                            if not self.controls.validation_repair:
                                raise AgentProtocolError(f"{spec.role}: invalid {spec.terminal}: {exc}") from exc
                            span.event("validation_error", turn=turn, error=format_validation_error(exc)[:300])
                            results[i] = ToolResult(call.id, call.name, format_validation_error(exc), is_error=True)
                    elif self.controls.loop_guard and seen[key] > LOOP_LIMIT:
                        span.event("loop_guard", turn=turn, tool=call.name)
                        results[i] = ToolResult(call.id, call.name, f"You already made this exact {call.name} call "
                                                f"{seen[key] - 1} times. Use the results you have.", is_error=True)
                    elif call.name not in tools:
                        if not self.controls.validation_repair:
                            raise AgentProtocolError(f"{spec.role} called unknown tool {call.name}")
                        results[i] = tools.execute(call, ctx)  # returns an is_error result listing valid tools
                    else:
                        pending.append((i, call))

                if pending:
                    n_calls += len(pending)
                    budget.charge_tool(len(pending))
                    futures = {i: _CALL_POOL.submit(contextvars.copy_context().run, tools.execute, call, ctx)
                               for i, call in pending}
                    for i, fut in futures.items():
                        results[i] = fut.result()

                content: list = [r.block() for r in results if r is not None]
                if output is None and _approx_tokens(messages) > soft_limit:
                    span.event("context_nudge", turn=turn)
                    content.append(text_block(CONTEXT_NUDGE.format(tool=spec.terminal)))
                messages.append(Message("user", content))

                if output is not None:
                    span.set(**{"agent.turns": turn, "agent.tool_calls": n_calls, "agent.model": model})
                    return AgentRun(output, turn, n_calls, model, messages)

            raise AgentProtocolError(f"{spec.role} did not call {spec.terminal} within {role_cfg.max_turns} turns")


def _approx_tokens(messages: list[Message]) -> int:
    return sum(len(str(b)) for m in messages for b in m.content) // 4
