"""The AP exception toolset.

Read tools wrap the ERP gateway and the deterministic fact functions. Vendor
free text goes through `untrusted()` (normalise, redact, spotlight) at this
boundary, so no prompt ever contains raw vendor text. Terminal tools have no
function: the agent loop validates their input as the agent's output artifact.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import date

from pydantic import BaseModel, Field

from ..domain.db import connect
from ..domain.erp import READABLE_TABLES
from ..domain.facts import compute_match, find_duplicates, normalize_invoice_number
from ..domain.models import Findings, PolicyAnalysis, Resolution, ReviewVerdict, TriageOutput, VendorEmail
from ..guardrails import redact, scan, spotlight
from .base import Scope, Tool, ToolContext, ToolError


class InvoiceIdArgs(BaseModel):
    invoice_id: str = Field(description="Invoice id, e.g. INV-10012.")


class VendorIdArgs(BaseModel):
    vendor_id: str = Field(description="Vendor id, e.g. V-104.")


class POIdArgs(BaseModel):
    po_id: str = Field(description="Purchase order id, e.g. PO-55031.")


class SQLArgs(BaseModel):
    sql: str = Field(description="One read-only SELECT statement against the ERP tables.")


class ReadResultArgs(BaseModel):
    ref: str = Field(description="The ref from a truncated tool result, e.g. r1.")
    offset: int = Field(description="Character offset to continue reading from.", ge=0)


class SearchPolicyArgs(BaseModel):
    query: str = Field(description="Natural-language query, e.g. 'price variance tolerance credit memo'.")
    k: int = Field(description="Number of sections to return (1-8).", ge=1, le=8)


class SectionArgs(BaseModel):
    section_id: str = Field(description="Policy section id, e.g. AP-POL-6.2.")


def untrusted(ctx: ToolContext, text: str | None, source: str) -> str:
    text, n_redacted = redact(text or "")
    result = scan(text)
    if result.suspicious:
        ctx.signals.append(f"injection:{source}")
    if n_redacted:
        ctx.signals.append(f"redacted:{source}:{n_redacted}")
    return spotlight(text, source, result)


# ----------------------------------------------------------------- readers
def get_invoice(args: InvoiceIdArgs, ctx: ToolContext) -> dict:
    inv = ctx.erp.invoice(args.invoice_id)
    if inv is None:
        raise ToolError(f"No invoice with id {args.invoice_id}.")
    return {
        "invoice_id": inv["invoice_id"],
        "vendor_id": inv["vendor_id"],
        "po_id": inv["po_id"],
        "invoice_number": inv["invoice_number"],
        "invoice_date": inv["invoice_date"],
        "currency": inv["currency"],
        "total": inv["total"],
        "status": inv["status"],
        "exception_code": inv["exception_code"],
        "remit_to_account_last4": inv["remit_bank_last4"],
        "lines": inv["lines"],
        "notes": untrusted(ctx, inv["notes"], "invoice.notes"),
        "attachment_text": untrusted(ctx, inv["attachment_text"], "invoice.attachment"),
    }


def get_vendor(args: VendorIdArgs, ctx: ToolContext) -> dict:
    v = ctx.erp.vendor(args.vendor_id)
    if v is None:
        raise ToolError(f"No vendor with id {args.vendor_id}.")
    age = None
    if v.get("bank_changed_at"):
        age = (ctx.settings.clock - date.fromisoformat(v["bank_changed_at"][:10])).days
    return {
        "vendor_id": v["vendor_id"],
        "name": v["name"],
        "status": v["status"],
        "category": v["category"],
        "payment_terms": v["payment_terms"],
        "ar_contact_email": v["contact_email"],
        "ar_contact_phone": v["contact_phone"],
        "bank_account_last4": v["bank_last4"],
        "bank_details_changed_at": v["bank_changed_at"],
        "bank_change_age_days": age,
        "bank_change_callback_verified": bool(v["bank_change_verified"]),
        "today": ctx.settings.clock.isoformat(),
    }


def get_purchase_order(args: POIdArgs, ctx: ToolContext) -> dict:
    po = ctx.erp.purchase_order(args.po_id)
    if po is None:
        raise ToolError(f"No purchase order with id {args.po_id}.")
    return po


def get_goods_receipts(args: POIdArgs, ctx: ToolContext) -> dict:
    if ctx.erp.purchase_order(args.po_id) is None:
        raise ToolError(f"No purchase order with id {args.po_id}.")
    return {"po_id": args.po_id, "receipts": ctx.erp.receipts(args.po_id)}


def compute_three_way_match(args: InvoiceIdArgs, ctx: ToolContext) -> dict:
    try:
        return compute_match(ctx.erp, args.invoice_id, ctx.settings.policy).model_dump()
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


def check_duplicates(args: InvoiceIdArgs, ctx: ToolContext) -> dict:
    inv = ctx.erp.invoice(args.invoice_id)
    if inv is None:
        raise ToolError(f"No invoice with id {args.invoice_id}.")
    window = ctx.settings.policy.duplicate_window_days
    return {
        "invoice_id": args.invoice_id,
        "normalized_invoice_number": normalize_invoice_number(inv["invoice_number"]),
        "window_days": window,
        "candidates": find_duplicates(ctx.erp, args.invoice_id, window),
    }


def recall_vendor_history(args: VendorIdArgs, ctx: ToolContext) -> dict:
    if ctx.erp.vendor(args.vendor_id) is None:
        raise ToolError(f"No vendor with id {args.vendor_id}.")
    entries = ctx.erp.vendor_memory(args.vendor_id)
    recent = [
        {"invoice_id": i["invoice_id"], "invoice_number": i["invoice_number"], "date": i["invoice_date"],
         "total": i["total"], "status": i["status"]}
        for i in ctx.erp.vendor_invoices(args.vendor_id)[-6:]
    ]
    return {"vendor_id": args.vendor_id, "memory": entries, "recent_invoices": recent,
            "note": "Memory entries are written by the system from verified outcomes only."}


def sql_query(args: SQLArgs, ctx: ToolContext) -> dict:
    sql = args.sql.strip().rstrip(";").strip()
    if ";" in sql:
        raise ToolError("Only a single statement is allowed.")
    if not re.match(r"(?is)^(select|with)\b", sql):
        raise ToolError("Only SELECT queries are allowed.")
    conn = connect(ctx.erp.path, readonly=True)
    deadline = time.monotonic() + 2.0

    def authorizer(action, arg1, arg2, dbname, source):  # noqa: ARG001
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if (arg1 in READABLE_TABLES or arg1 is None) else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    try:
        conn.set_authorizer(authorizer)
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 5000)
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description or []]
        fetched = cur.fetchmany(51)
    except sqlite3.DatabaseError as exc:
        raise ToolError(f"SQL error: {exc}. Readable tables: {', '.join(sorted(READABLE_TABLES))}.") from exc
    finally:
        conn.close()
    return {"columns": cols, "rows": [list(r) for r in fetched[:50]], "truncated": len(fetched) > 50}


def read_result(args: ReadResultArgs, ctx: ToolContext) -> str:
    text = ctx.results.items.get(args.ref)
    if text is None:
        raise ToolError(f"Unknown ref {args.ref}.")
    page = ctx.results.page_size[args.ref]
    end = args.offset + page
    chunk = text[args.offset : end]
    if not chunk:
        return "[end of result]"
    if end < len(text):
        chunk += f"\n...[{len(text) - end} more chars; call read_result with ref='{args.ref}' and offset={end}]"
    return chunk


def search_policy(args: SearchPolicyArgs, ctx: ToolContext) -> dict:
    hits = ctx.corpus.search(args.query, k=args.k)
    for h in hits:
        if h.section_id not in ctx.retrieved_sections:
            ctx.retrieved_sections.append(h.section_id)
    return {"query": args.query, "hits": [{"section_id": h.section_id, "title": h.title, "score": h.score, "text": h.text}
                                          for h in hits]}


def get_policy_section(args: SectionArgs, ctx: ToolContext) -> dict:
    chunk = ctx.corpus.get(args.section_id)
    if chunk is None:
        raise ToolError(f"No policy section {args.section_id}. Use search_policy to find sections.")
    if chunk.section_id not in ctx.retrieved_sections:
        ctx.retrieved_sections.append(chunk.section_id)
    return {"section_id": chunk.section_id, "title": chunk.title, "text": chunk.text}


# --------------------------------------------------------------- registry
def build_ap_tools() -> list[Tool]:
    return [
        Tool("get_invoice", "Fetch an invoice header, lines, remit-to account (last 4 digits) and vendor-supplied notes/"
             "attachment text. Vendor text arrives inside <untrusted_vendor_text> envelopes.", InvoiceIdArgs, get_invoice),
        Tool("get_vendor", "Fetch the vendor master record: status, AR contact, bank account last 4 and when bank details "
             "last changed and whether the change was callback-verified.", VendorIdArgs, get_vendor),
        Tool("get_purchase_order", "Fetch a purchase order header and lines.", POIdArgs, get_purchase_order),
        Tool("get_goods_receipts", "List goods receipts posted against a purchase order.", POIdArgs, get_goods_receipts),
        Tool("compute_three_way_match", "Deterministically compare invoice lines with PO lines and goods receipts. Returns "
             "per-line and total price variance, units pending receipt, units over the PO, currency match, and whether "
             "the price variance is within tolerance. Prefer this over manual arithmetic.", InvoiceIdArgs,
             compute_three_way_match),
        Tool("check_duplicates", "Find prior invoices from the same vendor with the same normalized invoice number, or "
             "the same PO and total within the duplicate window.", InvoiceIdArgs, check_duplicates),
        Tool("recall_vendor_history", "Recall system-recorded history for a vendor: verified past outcomes and recent "
             "invoices.", VendorIdArgs, recall_vendor_history),
        Tool("sql_query", "Run one read-only SELECT against ERP tables (vendors, purchase_orders, po_lines, goods_receipts, "
             "invoices, invoice_lines, payments, vendor_memory). At most 50 rows.", SQLArgs, sql_query),
        Tool("read_result", "Continue reading a truncated tool result.", ReadResultArgs, read_result, paged=True),
        Tool("search_policy", "Search the AP policy manual. Returns sections with ids, titles and text.", SearchPolicyArgs,
             search_policy),
        Tool("get_policy_section", "Fetch one AP policy section by id.", SectionArgs, get_policy_section),
        # Terminal tools: their input is the agent's output artifact.
        Tool("submit_triage", "Submit your triage of the invoice. Call exactly once.", TriageOutput, scope=Scope.TERMINAL),
        Tool("submit_findings", "Submit your investigation findings. Call once, after gathering evidence.", Findings,
             scope=Scope.TERMINAL),
        Tool("submit_policy_analysis", "Submit the applicable policy rules. Only cite sections you retrieved.",
             PolicyAnalysis, scope=Scope.TERMINAL),
        Tool("submit_resolution", "Submit the proposed resolution for this invoice.", Resolution, scope=Scope.TERMINAL),
        Tool("submit_review", "Submit your review verdict on the proposed resolution.", ReviewVerdict, scope=Scope.TERMINAL),
        Tool("submit_vendor_email", "Submit the vendor email draft (subject and body).", VendorEmail, scope=Scope.TERMINAL),
    ]
