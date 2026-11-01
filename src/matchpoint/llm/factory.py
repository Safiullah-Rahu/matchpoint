"""Build a provider stack from CLI/API options."""

from __future__ import annotations

from ..config import Settings
from .anthropic_provider import AnthropicProvider
from .chaos import ChaosProvider
from .replay import CassetteProvider
from .sim_provider import SimProvider


def make_provider(name: str, settings: Settings, *, mistake_rate: float = 0.0, gullible: bool = False,
                  latency_ms: float = 0.0, chaos: float = 0.0, seed: int = 0, cassette: str | None = None,
                  record: str | None = None):
    # Demo presets for the UI: the reference model made adversarial, or wrapped in fault injection.
    if name == "sim-gullible":
        name, gullible = "sim", True
    elif name == "sim-chaos":
        name, chaos = "sim", chaos or 0.3
    if name == "sim":
        provider = SimProvider(mistake_rate=mistake_rate, gullible=gullible, latency_ms=latency_ms, seed=seed)
    elif name == "anthropic":
        provider = AnthropicProvider(prompt_caching=settings.harness.prompt_caching,
                                     server_side_fallbacks=settings.harness.server_side_fallbacks)
    elif name == "replay":
        if not cassette:
            raise ValueError("--provider replay needs --cassette")
        provider = CassetteProvider(cassette)
    else:
        raise ValueError(f"unknown provider {name!r} (sim | sim-gullible | sim-chaos | anthropic | replay)")
    if record:
        provider = CassetteProvider(record, inner=provider)
    if chaos:
        provider = ChaosProvider(provider, rate=chaos, seed=seed)
    return provider
