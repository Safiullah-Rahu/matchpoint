from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from matchpoint.agents.workflow import APWorkflow, build_runtime
from matchpoint.config import load_settings
from matchpoint.domain.seed import seed
from matchpoint.harness.reliability import Controls, RetryPolicy
from matchpoint.llm.sim_provider import SimProvider


@pytest.fixture(scope="session")
def template(tmp_path_factory) -> Path:
    """Seed once per session; tests get their own copy."""
    home = tmp_path_factory.mktemp("template")
    seed(load_settings(home=home))
    return home


@pytest.fixture
def settings(template, tmp_path):
    shutil.copy(template / "erp.db", tmp_path / "erp.db")
    shutil.copy(template / "eval_cases.jsonl", tmp_path / "eval_cases.jsonl")
    return load_settings(home=tmp_path)


@pytest.fixture
def cases(settings):
    from matchpoint.domain.seed import load_cases

    return {c["case_id"]: c for c in load_cases(settings)}


def no_sleep_retry(settings) -> RetryPolicy:
    r = settings.retry
    return RetryPolicy(r.max_attempts, r.base_delay_s, r.max_delay_s, sleep=lambda _s: None)


@pytest.fixture
def make_workflow(settings):
    def factory(provider=None, controls: Controls = Controls()) -> APWorkflow:
        rt = build_runtime(settings, provider or SimProvider(), controls=controls, retry=no_sleep_retry(settings))
        return APWorkflow(rt)

    return factory
