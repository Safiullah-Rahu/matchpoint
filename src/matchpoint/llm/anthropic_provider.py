"""Claude via the official Anthropic Python SDK.

Choices worth calling out:
  * SDK retries are off (max_retries=0). The harness owns retries so every
    attempt, back-off and fallback is visible in the trace and counted against
    the run budget.
  * Thinking is left at the model default (adaptive on the 5.x models) and
    depth is set per role with `output_config.effort`.
  * Assistant content is appended back exactly as returned (thinking blocks
    included) and history is never edited, which keeps the prompt cache warm
    and preserved thinking valid.
  * Tools and the system prompt carry `cache_control`, so the static prefix of
    each role is cached across turns and across runs.
  * Server-side refusal fallbacks (`fallbacks: "default"`) are enabled for the
    models that support them; the harness's own fallback chain covers the rest.
  * `tool_choice` stays "auto": forced tool choice is rejected on the 5.5
    models, so the loop nudges the model if it ends a turn without the
    terminal tool.
"""

from __future__ import annotations

import time

from .errors import NonRetryableLLMError, RefusalError, RetryableLLMError
from .types import LLMRequest, LLMResponse, Message, ToolCall, Usage

SERVER_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-opus-5"}
SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client=None, *, timeout_s: float = 120.0, prompt_caching: bool = True,
                 server_side_fallbacks: bool = True):
        if client is None:
            import os

            import anthropic

            # Organisation-level keys must name a workspace; plain workspace keys don't need this.
            workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
            headers = {"anthropic-workspace-id": workspace} if workspace else None
            client = anthropic.Anthropic(max_retries=0, timeout=timeout_s, default_headers=headers)
        self.client = client
        self.prompt_caching = prompt_caching
        self.server_side_fallbacks = server_side_fallbacks
        self.strict_tools = True
        self.downgrades: list[str] = []  # optional features dropped after the API rejected them

    # ------------------------------------------------------------ request
    def build_params(self, req: LLMRequest) -> dict:
        system = [{"type": "text", "text": req.system}]
        tools = [dict(t) for t in req.tools]
        if not self.strict_tools:
            tools = [{k: v for k, v in t.items() if k != "strict"} for t in tools]
        if self.prompt_caching:
            system[0]["cache_control"] = {"type": "ephemeral"}
            if tools:
                tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        params: dict = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": system,
            "messages": [_message(m) for m in req.messages],
            "output_config": {"effort": req.effort},
        }
        if tools:
            params["tools"] = tools
        if self.server_side_fallbacks and req.model in SERVER_FALLBACK_MODELS:
            params["betas"] = [SERVER_FALLBACK_BETA]
            params["extra_body"] = {"fallbacks": "default"}
        return params

    def _downgrade(self, message: str, params: dict) -> bool:
        """After a 400, drop one optional feature the request used (strict schemas, server-side
        fallbacks) and report whether a retry makes sense. Sticky for this provider instance."""
        text = message.lower()
        if "betas" in params and ("fallback" in text or "beta" in text):
            self.server_side_fallbacks = False
            self.downgrades.append("server_side_fallbacks")
            return True
        if self.strict_tools and any(t.get("strict") for t in params.get("tools", [])) and \
                any(word in text for word in ("strict", "schema", "input_schema", "tools.")):
            self.strict_tools = False
            self.downgrades.append("strict_tools")
            return True
        return False

    def _create(self, params: dict):
        if "betas" in params:
            return self.client.beta.messages.create(**params)
        return self.client.messages.create(**params)

    def complete(self, req: LLMRequest) -> LLMResponse:
        import anthropic

        params = self.build_params(req)
        start = time.perf_counter()
        try:
            try:
                resp = self._create(params)
            except anthropic.BadRequestError as e:
                if not self._downgrade(str(e), params):
                    raise
                params = self.build_params(req)
                resp = self._create(params)
        except anthropic.RateLimitError as e:
            raise RetryableLLMError(f"rate limited: {e}", status=429, retry_after=_retry_after(e)) from e
        except anthropic.InternalServerError as e:
            raise RetryableLLMError(f"server error: {e}", status=e.status_code, retry_after=_retry_after(e)) from e
        except anthropic.APIConnectionError as e:  # includes APITimeoutError
            raise RetryableLLMError(f"connection error: {e}") from e
        except anthropic.APIStatusError as e:
            # 529 overloaded is its own class (not an InternalServerError) in current SDKs.
            if e.status_code in (408, 409) or e.status_code >= 500:
                raise RetryableLLMError(f"transient {e.status_code}: {e}", status=e.status_code,
                                        retry_after=_retry_after(e)) from e
            raise NonRetryableLLMError(f"{e.status_code}: {e}", status=e.status_code) from e

        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            raise RefusalError(f"model refused (category={getattr(details, 'category', None)})")

        text, calls = [], []
        for block in resp.content:
            if block.type == "text":
                text.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, input=dict(block.input or {})))
        u = resp.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        return LLMResponse(
            model=resp.model,
            text="\n".join(text),
            tool_calls=calls,
            stop_reason=resp.stop_reason or "end_turn",
            usage=usage,
            raw_content=list(resp.content),
            latency_ms=round((time.perf_counter() - start) * 1000, 1),
            provider=self.name,
            request_id=getattr(resp, "_request_id", None),
        )


def _message(m: Message) -> dict:
    return {"role": m.role, "content": m.content}


def _retry_after(exc) -> float | None:
    try:
        value = exc.response.headers.get("retry-after")
        return float(value) if value else None
    except Exception:
        return None
