import time

import pytest
from fastapi.testclient import TestClient

from matchpoint.api.app import create_app


@pytest.fixture
def client(settings, monkeypatch):
    monkeypatch.delenv("MATCHPOINT_API_TOKEN", raising=False)
    return TestClient(create_app(settings))


def _wait(client, run_id, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        view = client.get(f"/api/runs/{run_id}").json()
        if view["run"]["status"] != "running":
            return view
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_run_lifecycle_over_http(client, cases):
    inv = cases["qty_overbilled-0"]["invoice_id"]
    assert client.get("/api/health").json()["status"] == "ok"
    assert any(r["invoice_id"] == inv for r in client.get("/api/queue").json())
    run_id = client.post("/api/runs", json={"invoice_id": inv}).json()["run_id"]
    view = _wait(client, run_id)
    assert view["run"]["status"] == "completed"
    assert view["state"]["resolution"]["action"] == "REQUEST_CREDIT_MEMO"
    assert view["erp"]["status"] == "on_hold" and len(view["erp"]["email_drafts"]) == 1
    kinds = {s["kind"] for s in client.get(f"/api/runs/{run_id}/trace").json()}
    assert {"run", "node", "agent", "llm", "tool", "guardrail"} <= kinds
    assert client.post("/api/runs", json={"invoice_id": inv}).status_code == 400  # already resolved
    assert "matchpoint_llm_calls_total" in client.get("/metrics").text


def test_approval_over_http(client, cases):
    inv = cases["over_approval_limit-2"]["invoice_id"]
    run_id = client.post("/api/runs", json={"invoice_id": inv}).json()["run_id"]
    view = _wait(client, run_id)
    assert view["run"]["status"] == "interrupted"
    pending = client.get("/api/approvals").json()
    approval_id = next(a["approval_id"] for a in pending if a["invoice_id"] == inv)
    assert client.post(f"/api/approvals/{approval_id}", json={"approved": True}).json()["run_id"] == run_id
    view = _wait(client, run_id)
    assert view["run"]["status"] == "completed" and view["erp"]["status"] == "approved_for_payment"
    assert client.post(f"/api/approvals/{approval_id}", json={"approved": True}).status_code == 409


def test_overview_activity_and_scenarios(client, cases):
    ov = client.get("/api/overview").json()
    assert ov["total"] == 52 and ov["open"] == 52 and ov["resolved"] == 0
    inv = cases["bank_change_fraud-0"]["invoice_id"]
    run_id = client.post("/api/runs", json={"invoice_id": inv}).json()["run_id"]
    _wait(client, run_id)
    ov = client.get("/api/overview").json()
    assert ov["open"] == 51 and ov["fraud_held"] == 1 and ov["runs"] == 1
    feed = client.get("/api/activity").json()
    assert feed[0]["invoice_id"] == inv and feed[0]["action"] == "HOLD" and "FRAUD_SUSPECTED" in feed[0]["flags"]
    picks = {s["scenario"]: s for s in client.get("/api/scenarios").json()}
    assert len(picks) == 13 and picks["bank_change_fraud"]["invoice_id"] != inv  # next open variant
    assert len(client.get("/api/policy").json()) == 24


def test_policy_and_unknowns(client):
    assert client.get("/api/policy/AP-POL-6.2").json()["title"].startswith("Bank detail")
    assert client.get("/api/policy/AP-POL-0.0").status_code == 404
    assert client.post("/api/runs", json={"invoice_id": "INV-0"}).status_code == 404


def test_bearer_token(settings, monkeypatch):
    monkeypatch.setenv("MATCHPOINT_API_TOKEN", "s3cret")
    c = TestClient(create_app(settings))
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/queue").status_code == 401
    assert c.get("/api/queue", headers={"Authorization": "Bearer s3cret"}).status_code == 200
