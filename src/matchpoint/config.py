"""Settings: one YAML file plus environment overrides."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG = Path(__file__).with_name("default_config.yaml")


class RoleConfig(BaseModel):
    tier: str
    effort: str = "medium"
    max_tokens: int = 4096
    max_turns: int = 6


class RoutingConfig(BaseModel):
    high_risk_tier: str = "frontier"
    high_risk_amount: float = 10_000.0
    escalate_roles: list[str] = Field(default_factory=lambda: ["resolver"])


class BudgetConfig(BaseModel):
    max_llm_calls: int = 45
    max_total_tokens: int = 600_000
    max_cost_usd: float = 2.0
    max_wall_seconds: float = 600.0


class RetryConfig(BaseModel):
    max_attempts: int = 4
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0


class BreakerConfig(BaseModel):
    failure_threshold: int = 5
    reset_after_s: float = 30.0


class PolicyConfig(BaseModel):
    price_tolerance_pct: float = 2.0
    price_tolerance_abs: float = 250.0
    approval_limit: float = 25_000.0
    duplicate_window_days: int = 7
    bank_change_cooldown_days: int = 30


class HarnessConfig(BaseModel):
    max_revisions: int = 2
    max_graph_steps: int = 40
    tool_result_max_chars: int = 6000
    tool_timeout_s: float = 10.0
    context_soft_limit_tokens: int = 60_000
    strict_tools: bool = True
    server_side_fallbacks: bool = True
    prompt_caching: bool = True


class Settings(BaseModel):
    home: Path
    clock: date = date(2026, 10, 15)
    models: dict[str, str]
    fallback_chain: dict[str, list[str]] = Field(default_factory=dict)
    roles: dict[str, RoleConfig]
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    circuit_breaker: BreakerConfig = Field(default_factory=BreakerConfig)
    policy: PolicyConfig = Field(default_factory=PolicyConfig)
    harness: HarnessConfig = Field(default_factory=HarnessConfig)

    @property
    def erp_db(self) -> Path:
        return self.home / "erp.db"

    @property
    def app_db(self) -> Path:
        return self.home / "matchpoint.db"

    def role(self, name: str) -> RoleConfig:
        try:
            return self.roles[name]
        except KeyError as exc:
            raise KeyError(f"no role config for {name!r}") from exc


def load_settings(config_path: str | Path | None = None, home: str | Path | None = None, **overrides) -> Settings:
    path = Path(config_path or os.environ.get("MATCHPOINT_CONFIG") or DEFAULT_CONFIG)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["home"] = Path(home or os.environ.get("MATCHPOINT_HOME") or data.get("home") or "var").resolve()
    data.update(overrides)
    settings = Settings.model_validate(data)
    settings.home.mkdir(parents=True, exist_ok=True)
    return settings
