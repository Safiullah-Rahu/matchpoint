from __future__ import annotations

from typing import Protocol

from .types import LLMRequest, LLMResponse


class Provider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...
