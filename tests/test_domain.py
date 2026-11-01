import hashlib
import sqlite3

from matchpoint.config import load_settings
from matchpoint.domain.erp import ERP
from matchpoint.domain.facts import build_fact_sheet, find_duplicates, normalize_invoice_number
from matchpoint.domain.seed import SCENARIOS, seed


def _digest(path):
    conn = sqlite3.connect(path)
    rows = conn.execute("SELECT * FROM invoices ORDER BY invoice_id").fetchall()
    conn.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def test_seed_is_deterministic_and_self_checked(tmp_path):
    a = load_settings(home=tmp_path / "a")
    b = load_settings(home=tmp_path / "b")
    cases_a, cases_b = seed(a), seed(b)
    assert len(cases_a) == len(SCENARIOS) * 4 == 52
    assert [c.to_dict() for c in cases_a] == [c.to_dict() for c in cases_b]
    assert _digest(a.erp_db) == _digest(b.erp_db)


def test_labels_never_live_in_the_erp(settings):
    conn = sqlite3.connect(settings.erp_db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert not any("label" in t or "eval" in t for t in tables)


def test_invoice_number_normalization():
    same = ["AF-2026-0412", "af 2026 412", "AF2026/0412", "AF.2026.0412"]
    assert len({normalize_invoice_number(n) for n in same}) == 1
    assert normalize_invoice_number("AF-2026-0413") != normalize_invoice_number("AF-2026-0412")


def test_fact_sheet_matches_every_label(settings, cases):
    erp = ERP(settings.erp_db)
    for c in cases.values():
        f = build_fact_sheet(erp, c["invoice_id"], settings.policy, settings.clock)
        assert f.diagnosis.value == c["expected"]["diagnosis"], c["case_id"]
        assert f.expected_action.value == c["expected"]["action"], c["case_id"]


def test_duplicate_detection_reasons(settings, cases):
    erp = ERP(settings.erp_db)
    by_number = find_duplicates(erp, cases["duplicate_number-1"]["invoice_id"], 7)
    by_amount = find_duplicates(erp, cases["duplicate_amount_date-0"]["invoice_id"], 7)
    assert by_number and "same_invoice_number" in by_number[0]["reasons"]
    assert by_amount and "same_po_amount_within_window" in by_amount[0]["reasons"]
    assert find_duplicates(erp, cases["clean_within_tolerance-0"]["invoice_id"], 7) == []


def test_erp_apply_is_idempotent(settings, cases):
    erp = ERP(settings.erp_db)
    inv = cases["clean_within_tolerance-0"]["invoice_id"]
    calls = []

    def ops(conn):
        calls.append(1)
        conn.execute("INSERT INTO escalations (invoice_id, target, reason, created_at) VALUES (?, 'controller', 'x', 'now')",
                     (inv,))

    kwargs = dict(idempotency_key="k1", run_id="r", invoice_id=inv, actor="t", action="X", details={}, ops=ops)
    assert erp.apply(**kwargs) is True
    assert erp.apply(**kwargs) is False
    assert len(calls) == 1 and len(erp.outcome(inv)["escalations"]) == 1
