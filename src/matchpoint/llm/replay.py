"""Record/replay cassettes for deterministic regression runs of a live model.

Record once against Claude (`--record cassettes/x.jsonl`), then replay in CI
(`--provider replay --cassette cassettes/x.jsonl`) with zero cost and zero
flakiness. Keys hash the semantic request (model, role, system, text, tool
calls, tool results, tool schemas) and ignore opaque blocks such as thinking,
so a replayed history matches the recorded one.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from .errors import NonRetryableLLMError
from .types import LLMRequest, LLMResponse, ToolCall, Usage, block_attr, block_type


def request_key(req: LLMRequest) -> str:
    msgs = []
    for m in req.messages:
        content = []
        for b in m.content:
            kind = block_type(b)
            if kind == "text":
                content.append(["text", block_attr(b, "text")])
            elif kind == "tool_use":
                content.append(["tool_use", block_attr(b, "name"), block_attr(b, "input")])
            elif kind == "tool_result":
                content.append(["tool_result", block_attr(b, "content"), bool(block_attr(b, "is_error", False))])
        msgs.append([m.role, content])
    payload = json.dumps([req.model, req.role, req.system, msgs, req.tools], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


class CassetteProvider:
    def __init__(self, path: str | Path, *, inner=None):
        """inner=None -> replay only; inner=<provider> -> record (pass-through + save)."""
        self.path = Path(path)
        self.inner = inner
        self.name = f"record({inner.name})" if inner else "replay"
        self._lock = threading.Lock()
        self._tape: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._tape[entry["key"]] = entry["response"]

    def complete(self, req: LLMRequest) -> LLMResponse:
        key = request_key(req)
        if self.inner is None:
            entry = self._tape.get(key)
            if entry is None:
                raise NonRetryableLLMError(f"cassette miss for {req.role} on {req.model} (key {key[:12]})")
            return _decode(entry)
        resp = self.inner.complete(req)
        with self._lock:
            self._tape[key] = _encode(resp)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "role": req.role, "response": self._tape[key]}) + "\n")
        return resp


def _encode(r: LLMResponse) -> dict:
    return {"model": r.model, "text": r.text, "stop_reason": r.stop_reason,
            "tool_calls": [{"id": c.id, "name": c.name, "input": c.input} for c in r.tool_calls],
            "usage": vars(r.usage), "latency_ms": r.latency_ms}


def _decode(e: dict) -> LLMResponse:
    calls = [ToolCall(c["id"], c["name"], c["input"]) for c in e["tool_calls"]]
    blocks = ([{"type": "text", "text": e["text"]}] if e["text"] else []) + [
        {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls]
    return LLMResponse(e["model"], e["text"], calls, e["stop_reason"], Usage(**e["usage"]), blocks,
                       e.get("latency_ms", 0.0), "replay")
