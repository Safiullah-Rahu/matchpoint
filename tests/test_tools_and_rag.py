import time

import pytest
from pydantic import BaseModel

from matchpoint.domain.erp import ERP
from matchpoint.domain.models import Findings, Resolution
from matchpoint.llm.sim_provider import QUERIES
from matchpoint.llm.types import ToolCall
from matchpoint.rag import PolicyCorpus
from matchpoint.tools import Tool, ToolContext, ToolRegistry, build_ap_tools, strict_schema


@pytest.fixture
def ctx(settings, cases):
    return ToolContext(erp=ERP(settings.erp_db), settings=settings, corpus=PolicyCorpus.load(),
                       invoice_id=cases["clean_within_tolerance-0"]["invoice_id"], run_id="t")


@pytest.fixture
def registry():
    return ToolRegistry(build_ap_tools(), max_chars=6000, timeout_s=2)


def call(reg, ctx, name, **args):
    return reg.execute(ToolCall("id1", name, args), ctx)


def test_strict_schema_is_closed_and_self_contained():
    for model in (Findings, Resolution):
        s = strict_schema(model)
        assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
        assert "$defs" not in s and "$ref" not in str(s)


def test_sql_tool_is_read_only_and_allow_listed(registry, ctx):
    ok = call(registry, ctx, "sql_query", sql="SELECT vendor_id, status FROM vendors ORDER BY vendor_id LIMIT 3")
    assert not ok.is_error and '"columns"' in ok.content
    for sql in ("DELETE FROM invoices", "SELECT 1; DROP TABLE invoices", "SELECT * FROM audit_log",
                "SELECT * FROM sqlite_master", "WITH x AS (SELECT * FROM email_drafts) SELECT * FROM x",
                "ATTACH DATABASE 'x.db' AS x"):
        assert call(registry, ctx, "sql_query", sql=sql).is_error, sql


def test_invalid_args_become_a_recoverable_error(registry, ctx):
    r = registry.execute(ToolCall("id1", "get_vendor", {"vendorid": "V-101"}), ctx)
    assert r.is_error and "vendor_id" in r.content


def test_unknown_tool_lists_available_tools(registry, ctx):
    r = call(registry, ctx, "lookup_everything")
    assert r.is_error and "get_invoice" in r.content


def test_vendor_text_is_spotlighted_and_redacted(registry, ctx, cases):
    r = call(registry, ctx, "get_invoice", invoice_id=cases["injection_bank_change-3"]["invoice_id"])
    assert "<untrusted_vendor_text" in r.content and "GB29 NWBK" not in r.content and "****6819" in r.content
    assert any(s.startswith("injection:") for s in ctx.signals)


def test_timeout_and_truncation(ctx):
    class Args(BaseModel):
        n: int

    reg = ToolRegistry([Tool("slow", "", Args, lambda a, c: time.sleep(2)),
                        Tool("big", "", Args, lambda a, c: "x" * a.n),
                        *[t for t in build_ap_tools() if t.name == "read_result"]], max_chars=100, timeout_s=0.2)
    assert "timed out" in call(reg, ctx, "slow", n=1).content
    big = call(reg, ctx, "big", n=250)
    assert big.truncated and "read_result" in big.content
    page = call(reg, ctx, "read_result", ref="r1", offset=100)
    assert not page.is_error and not page.truncated
    assert page.content.startswith("x" * 100) and "offset=200" in page.content
    last = call(reg, ctx, "read_result", ref="r1", offset=200)
    assert last.content == "x" * 50


def test_retrieval_top1_for_every_diagnosis():
    corpus = PolicyCorpus.load()
    expected = {"CLEAN_WITHIN_TOLERANCE": "AP-POL-3.2", "PRICE_OVER_TOLERANCE": "AP-POL-3.2", "QTY_OVERBILLED": "AP-POL-3.3",
                "RECEIPT_PENDING": "AP-POL-3.4", "DUPLICATE": "AP-POL-4.1", "BANK_DETAILS_RISK": "AP-POL-6.2",
                "VENDOR_ON_HOLD": "AP-POL-2.2", "NON_PO": "AP-POL-7.1", "CURRENCY_MISMATCH": "AP-POL-3.6"}
    for diag, queries in QUERIES.items():
        assert corpus.search(queries[0], 3)[0].section_id == expected[diag], diag
    assert corpus.search("what does AP-POL-8.1 say", 1)[0].section_id == "AP-POL-8.1"
