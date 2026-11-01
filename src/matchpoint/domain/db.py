"""Small SQLite helpers. One short-lived connection per unit of work keeps the
code thread-safe without a shared connection pool."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def connect(path: str | Path, *, readonly: bool = False, wal: bool = False) -> sqlite3.Connection:
    path = Path(path)
    if readonly:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15, check_same_thread=False)
    else:
        conn = sqlite3.connect(str(path), timeout=15, check_same_thread=False)
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def session(path: str | Path, *, readonly: bool = False, wal: bool = False) -> Iterator[sqlite3.Connection]:
    conn = connect(path, readonly=readonly, wal=wal)
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


def rows(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]
