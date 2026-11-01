"""Anthropic provider request shape and error mapping (no network), plus record/replay."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from matchpoint.llm.anthropic_provider import SERVER_FALLBACK_BETA, AnthropicProvider
from matchpoint.llm.errors import NonRetryableLLMError, RefusalError, RetryableLLMError
from matchpoint.llm.replay import CassetteProvider
from matchpoint.llm.sim_provider import SimProvider
from matchpoint.llm.types import LLMRequest, Message, text_block

TOOLS = [{"name": "submit_x", "description": "d", "input_schema": {"type": "object", "properties": {}}, "strict": True}]


def _req(model="claude-opus-5-5"):
    return LLMRequest(role="resolver", model=model, system="sys", messages=[Message("user", [text_block("hi")])],
                      tools=TOOLS, max_tokens=1000, effort="high")


class FakeMessages:
    def __init__(self, result):
        self.result, self.kwargs = result, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _client(result):
    msgs, beta = FakeMessages(result), FakeMessages(result)
    return SimpleNamespace(messages=msgs, beta=SimpleNamespace(messages=beta)), msgs, beta


def _response(stop="tool_use"):
    return SimpleNamespace(
        model="claude-opus-5-5", stop_reason=stop, stop_details=None, _request_id="req_1",
        content=[SimpleNamespace(type="thinking", thinking="", signature="sig"),
                 SimpleNamespace(type="text", text="Proposing."),
                 SimpleNamespace(type="tool_use", id="tu_1", name="submit_x", input={"a": 1})],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=80, cache_creation_input_tokens=0),
    )


def test_request_shape_caching_effort_and_server_fallbacks():
    client, plain, beta = _client(_response())
    resp = AnthropicProvider(client).complete(_req())
    kw = beta.kwargs
    assert plain.kwargs is None and kw["betas"] == [SERVER_FALLBACK_BETA] and kw["extra_body"] == {"fallbacks": "default"}
    assert kw["output_config"] == {"effort": "high"} and kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["tools"][-1]["cache_control"] == {"type": "ephemeral"} and "tool_choice" not in kw
    assert "temperature" not in kw and "thinking" not in kw
    assert resp.tool_calls[0].name == "submit_x" and resp.usage.cache_read_tokens == 80
    assert len(resp.raw_content) == 3  # thinking block kept for verbatim replay


def test_haiku_uses_the_plain_endpoint():
    client, plain, beta = _client(_response())
    AnthropicProvider(client).complete(_req("claude-haiku-5-5"))
    assert beta.kwargs is None and "betas" not in plain.kwargs


def _status_error(cls, code):
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx2.Response(code, request=req, headers={"retry-after": "2"})
    return cls("boom", response=resp, body=None)


@pytest.mark.parametrize("exc,expected", [
    (lambda: _status_error(anthropic.RateLimitError, 429), RetryableLLMError),
    (lambda: _status_error(anthropic.InternalServerError, 500), RetryableLLMError),
    (lambda: _status_error(anthropic.APIStatusError, 529), RetryableLLMError),
    (lambda: anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), RetryableLLMError),
    (lambda: _status_error(anthropic.BadRequestError, 400), NonRetryableLLMError),
])
def test_error_mapping(exc, expected):
    client, _, _ = _client(exc())
    with pytest.raises(expected) as info:
        AnthropicProvider(client).complete(_req())
    if info.value.status == 429:
        assert info.value.retry_after == 2.0


class RejectOnce(FakeMessages):
    """First call fails with a 400 naming the feature; later calls succeed."""

    def __init__(self, result, message):
        super().__init__(result)
        self.message, self.calls = message, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise _status_error(anthropic.BadRequestError, 400).__class__(
                self.message, response=httpx2.Response(400, request=httpx2.Request("POST", "https://x")), body=None)
        return self.result


@pytest.mark.parametrize("message,feature", [
    ("tools.0.input_schema: strict mode does not support this schema", "strict_tools"),
    ("fallbacks: beta not enabled for this organization", "server_side_fallbacks"),
])
def test_optional_features_are_dropped_after_a_400(message, feature):
    beta = RejectOnce(_response(), message)  # opus requests go to the beta endpoint first
    plain = FakeMessages(_response())
    client = SimpleNamespace(messages=plain, beta=SimpleNamespace(messages=beta))
    provider = AnthropicProvider(client)
    resp = provider.complete(_req())
    assert resp.tool_calls and provider.downgrades == [feature]
    if feature == "strict_tools":
        assert len(beta.calls) == 2 and "strict" not in beta.calls[-1]["tools"][0]
    else:
        assert len(beta.calls) == 1 and "betas" not in plain.kwargs


def test_unrelated_400_is_not_retried():
    client, _, beta = _client(_status_error(anthropic.BadRequestError, 400))
    with pytest.raises(NonRetryableLLMError):
        AnthropicProvider(client).complete(_req())


def test_refusal_is_its_own_error():
    client, _, _ = _client(_response(stop="refusal"))
    with pytest.raises(RefusalError):
        AnthropicProvider(client).complete(_req())


def test_record_then_replay_reproduces_a_run(make_workflow, cases, tmp_path):
    tape = tmp_path / "tape.jsonl"
    inv = cases["duplicate_number-0"]["invoice_id"]
    recorded = make_workflow(CassetteProvider(tape, inner=SimProvider())).start(inv)
    assert recorded.status == "completed" and tape.exists()


def test_replay_cassette_matches(make_workflow, cases, tmp_path, settings, template):
    import shutil

    tape = tmp_path / "tape.jsonl"
    inv = cases["price_over_tolerance-1"]["invoice_id"]
    first = make_workflow(CassetteProvider(tape, inner=SimProvider())).start(inv)
    shutil.copy(template / "erp.db", settings.erp_db)  # fresh ERP, same tape
    second = make_workflow(CassetteProvider(tape)).start(inv)
    assert second.status == "completed"
    assert second.state.resolution.model_dump() == first.state.resolution.model_dump()
