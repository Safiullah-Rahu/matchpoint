"""A small ERP stand-in: vendor master, purchase orders, receipts, invoices.

Reads are plain queries. Every write goes through `ERP.apply()`, which runs the
caller's operations and an audit-log insert in one transaction keyed by an
idempotency key. A retried or resumed run that re-applies the same action is a
no-op, so a crash between "write" and "checkpoint" can never double-pay.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .db import rows, session

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
    vendor_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL, category TEXT,
    payment_terms TEXT, contact_email TEXT, contact_phone TEXT,
    bank_last4 TEXT, bank_changed_at TEXT, bank_change_verified INTEGER DEFAULT 1, risk_tier TEXT
);
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id TEXT PRIMARY KEY, vendor_id TEXT NOT NULL, status TEXT NOT NULL, currency TEXT NOT NULL,
    created_at TEXT, expected_delivery TEXT, buyer TEXT, budget_owner TEXT
);
CREATE TABLE IF NOT EXISTS po_lines (
    po_id TEXT NOT NULL, line_no INTEGER NOT NULL, sku TEXT, description TEXT, qty REAL, unit_price REAL,
    PRIMARY KEY (po_id, line_no)
);
CREATE TABLE IF NOT EXISTS goods_receipts (
    gr_id TEXT PRIMARY KEY, po_id TEXT NOT NULL, line_no INTEGER NOT NULL, qty_received REAL, received_at TEXT
);
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id TEXT PRIMARY KEY, vendor_id TEXT NOT NULL, po_id TEXT, invoice_number TEXT NOT NULL,
    invoice_date TEXT, currency TEXT, total REAL, status TEXT NOT NULL, exception_code TEXT, hold_reason TEXT,
    remit_bank_last4 TEXT, notes TEXT, attachment_text TEXT, received_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS invoice_lines (
    invoice_id TEXT NOT NULL, line_no INTEGER NOT NULL, sku TEXT, description TEXT, qty REAL, unit_price REAL,
    PRIMARY KEY (invoice_id, line_no)
);
CREATE TABLE IF NOT EXISTS payments (payment_id TEXT PRIMARY KEY, invoice_id TEXT, amount REAL, paid_at TEXT);
CREATE TABLE IF NOT EXISTS payment_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, amount REAL, scheduled_for TEXT, approved_by TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS credit_memo_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, amount REAL, reason TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, target TEXT, reason TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS verification_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, vendor_id TEXT, method TEXT, contact TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS email_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id TEXT, to_addr TEXT, subject TEXT, body TEXT, status TEXT,
    idempotency_key TEXT UNIQUE, created_at TEXT
);
CREATE TABLE IF NOT EXISTS vendor_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT, invoice_id TEXT, note TEXT, source TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT UNIQUE NOT NULL, run_id TEXT, invoice_id TEXT,
    actor TEXT, action TEXT, details TEXT, created_at TEXT
);
"""

# Tables an agent may read through the ad-hoc SQL tool. Audit, drafts and the
# write-side tables are deliberately excluded.
READABLE_TABLES = frozenset(
    {"vendors", "purchase_orders", "po_lines", "goods_receipts", "invoices", "invoice_lines", "payments", "vendor_memory"}
)


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ERP:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    # ------------------------------------------------------------------ setup
    def create_schema(self) -> None:
        with session(self.path) as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ reads
    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with session(self.path, readonly=True) as conn:
            return rows(conn.execute(sql, params))

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        found = self._query(sql, params)
        return found[0] if found else None

    def invoice(self, invoice_id: str) -> dict | None:
        inv = self._one("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
        if inv:
            inv["lines"] = self._query(
                "SELECT line_no, sku, description, qty, unit_price FROM invoice_lines WHERE invoice_id = ? ORDER BY line_no",
                (invoice_id,),
            )
        return inv

    def vendor(self, vendor_id: str) -> dict | None:
        return self._one("SELECT * FROM vendors WHERE vendor_id = ?", (vendor_id,))

    def purchase_order(self, po_id: str) -> dict | None:
        po = self._one("SELECT * FROM purchase_orders WHERE po_id = ?", (po_id,))
        if po:
            po["lines"] = self._query(
                "SELECT line_no, sku, description, qty, unit_price FROM po_lines WHERE po_id = ? ORDER BY line_no", (po_id,)
            )
        return po

    def receipts(self, po_id: str) -> list[dict]:
        return self._query("SELECT * FROM goods_receipts WHERE po_id = ? ORDER BY received_at, gr_id", (po_id,))

    def vendor_invoices(self, vendor_id: str) -> list[dict]:
        return self._query("SELECT * FROM invoices WHERE vendor_id = ? ORDER BY invoice_date", (vendor_id,))

    def vendor_memory(self, vendor_id: str) -> list[dict]:
        return self._query(
            "SELECT invoice_id, note, source, created_at FROM vendor_memory WHERE vendor_id = ? ORDER BY id", (vendor_id,)
        )

    def exception_queue(self) -> list[dict]:
        return self._query(
            """SELECT i.invoice_id, i.vendor_id, v.name AS vendor_name, i.po_id, i.invoice_number, i.invoice_date,
                      i.currency, i.total, i.status, i.exception_code, i.hold_reason, i.updated_at
               FROM invoices i JOIN vendors v ON v.vendor_id = i.vendor_id
               WHERE i.exception_code IS NOT NULL ORDER BY i.invoice_id"""
        )

    def outcome(self, invoice_id: str) -> dict:
        """Everything the system did to an invoice. Used by graders and the UI."""
        inv = self._one("SELECT status, hold_reason FROM invoices WHERE invoice_id = ?", (invoice_id,)) or {}
        q = lambda table: self._query(f"SELECT * FROM {table} WHERE invoice_id = ? ORDER BY id", (invoice_id,))  # noqa: E731
        return {
            "status": inv.get("status"),
            "hold_reason": inv.get("hold_reason"),
            "payment_schedule": q("payment_schedule"),
            "credit_memo_requests": q("credit_memo_requests"),
            "escalations": q("escalations"),
            "verification_tasks": q("verification_tasks"),
            "email_drafts": q("email_drafts"),
            "audit_log": q("audit_log"),
        }

    # ----------------------------------------------------------------- writes
    def apply(
        self,
        *,
        idempotency_key: str,
        run_id: str,
        invoice_id: str,
        actor: str,
        action: str,
        details: dict,
        ops: Callable[[sqlite3.Connection], None],
    ) -> bool:
        """Run `ops` exactly once per idempotency key. Returns False if already applied."""
        with session(self.path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                """INSERT OR IGNORE INTO audit_log (idempotency_key, run_id, invoice_id, actor, action, details, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (idempotency_key, run_id, invoice_id, actor, action, json.dumps(details, default=str), utcnow()),
            )
            if cur.rowcount == 0:
                return False
            ops(conn)
            return True

    def add_vendor_memory(self, vendor_id: str, invoice_id: str, note: str, source: str) -> None:
        with session(self.path) as conn:
            conn.execute(
                "INSERT INTO vendor_memory (vendor_id, invoice_id, note, source, created_at) VALUES (?, ?, ?, ?, ?)",
                (vendor_id, invoice_id, note, source, utcnow()),
            )
