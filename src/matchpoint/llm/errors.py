"""Error taxonomy the harness reacts to.

Retryable  -> back off and retry the same model, then fall back down the chain.
Refusal    -> do not retry the same model; fall back.
NonRetryable -> a bug or bad request; fail the step (the graph fails closed).
"""

from __future__ import annotations


class LLMError(Exception):
    retryable = False

    def __init__(self, message: str, *, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class RetryableLLMError(LLMError):
    retryable = True


class RefusalError(LLMError):
    pass


class NonRetryableLLMError(LLMError):
    pass


class CircuitOpenError(LLMError):
    pass


class AllModelsFailedError(LLMError):
    pass
