"""Durable run state: one row per run, one checkpoint per completed node.

A run can stop for three reasons and resume from the same place:
  * interrupted: a node asked for human input (e.g. controller approval);
  * crashed: the process died mid-node; the last checkpoint names the next
    node, which re-runs (nodes with side effects are idempotent);
  * failed: recorded with the error for inspection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..domain.db import rows, session

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, invoice_id TEXT, status TEXT, current_node TEXT, provider TEXT,
    interrupt TEXT, error TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS checkpoints (
    run_id TEXT, seq INTEGER, node TEXT, next_node TEXT, status TEXT, state TEXT, created_at TEXT,
    PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY, run_id TEXT, invoice_id TEXT, amount REAL, summary TEXT, status TEXT,
    decided_by TEXT, comment TEXT, created_at TEXT, decided_at TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class Checkpointer:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        with session(self.path, wal=True) as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------ runs
    def create_run(self, run_id: str, invoice_id: str, provider: str) -> None:
        with session(self.path, wal=True) as conn:
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, invoice_id, "running", None, provider, None, None, _now(), _now()),
            )

    def update_run(self, run_id: str, **fields) -> None:
        fields["updated_at"] = _now()
        if "interrupt" in fields and fields["interrupt"] is not None:
            fields["interrupt"] = json.dumps(fields["interrupt"], default=str)
        cols = ", ".join(f"{k} = ?" for k in fields)
        with session(self.path, wal=True) as conn:
            conn.execute(f"UPDATE runs SET {cols} WHERE run_id = ?", (*fields.values(), run_id))

    def run(self, run_id: str) -> dict | None:
        with session(self.path, readonly=True) as conn:
            found = rows(conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)))
        if not found:
            return None
        r = found[0]
        r["interrupt"] = json.loads(r["interrupt"]) if r["interrupt"] else None
        return r

    def runs(self, invoice_id: str | None = None, limit: int = 100) -> list[dict]:
        sql, params = "SELECT * FROM runs", ()
        if invoice_id:
            sql, params = sql + " WHERE invoice_id = ?", (invoice_id,)
        with session(self.path, readonly=True) as conn:
            return rows(conn.execute(sql + " ORDER BY created_at DESC LIMIT ?", (*params, limit)))

    # ----------------------------------------------------- checkpoints
    def save(self, run_id: str, node: str, next_node: str | None, status: str, state_json: str) -> int:
        with session(self.path, wal=True) as conn:
            seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM checkpoints WHERE run_id = ?", (run_id,)).fetchone()[0]
            conn.execute("INSERT INTO checkpoints VALUES (?,?,?,?,?,?,?)",
                         (run_id, seq, node, next_node, status, state_json, _now()))
            return seq

    def latest(self, run_id: str) -> dict | None:
        with session(self.path, readonly=True) as conn:
            found = rows(conn.execute("SELECT * FROM checkpoints WHERE run_id = ? ORDER BY seq DESC LIMIT 1", (run_id,)))
        return found[0] if found else None

    def history(self, run_id: str) -> list[dict]:
        with session(self.path, readonly=True) as conn:
            return rows(conn.execute(
                "SELECT seq, node, next_node, status, created_at FROM checkpoints WHERE run_id = ? ORDER BY seq", (run_id,)))

    # ------------------------------------------------------- approvals
    def create_approval(self, approval_id: str, run_id: str, invoice_id: str, amount: float, summary: str) -> None:
        with session(self.path, wal=True) as conn:
            conn.execute("INSERT OR IGNORE INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (approval_id, run_id, invoice_id, amount, summary, "pending", None, None, _now(), None))

    def decide_approval(self, approval_id: str, approved: bool, decided_by: str, comment: str) -> dict | None:
        """Atomically claim a pending approval. Raises ValueError if it was already decided."""
        with session(self.path, wal=True) as conn:
            cur = conn.execute(
                "UPDATE approvals SET status = ?, decided_by = ?, comment = ?, decided_at = ? WHERE approval_id = ? "
                "AND status = 'pending'",
                ("approved" if approved else "rejected", decided_by, comment, _now(), approval_id),
            )
            claimed = cur.rowcount == 1
        appr = self.approval(approval_id)
        if appr is not None and not claimed:
            raise ValueError(f"approval {approval_id} is already {appr['status']}")
        return appr

    def approval(self, approval_id: str) -> dict | None:
        with session(self.path, readonly=True) as conn:
            found = rows(conn.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)))
        return found[0] if found else None

    def approvals(self, status: str | None = "pending") -> list[dict]:
        sql, params = "SELECT * FROM approvals", ()
        if status:
            sql, params = sql + " WHERE status = ?", (status,)
        with session(self.path, readonly=True) as conn:
            return rows(conn.execute(sql + " ORDER BY created_at DESC", params))
