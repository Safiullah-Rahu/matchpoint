"""Tracing: nested spans for runs, graph nodes, agents, LLM calls, tools, guards.

Span names and attributes follow the OpenTelemetry GenAI semantic conventions
where they exist (gen_ai.request.model, gen_ai.usage.input_tokens, ...), so the
optional OTLP bridge can ship them to any OTel backend (X-Ray via ADOT, Azure
Monitor, Cloud Trace, Datadog, Honeycomb, Langfuse) without remapping. The
default exporter writes to SQLite so the built-in UI can render a waterfall
with no infrastructure at all.

trace_id == run_id, so a run that pauses for human approval and resumes days
later still lands in one trace.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.db import rows, session

_CURRENT: ContextVar[Span | None] = ContextVar("matchpoint_current_span", default=None)
_EPOCH, _PERF0 = time.time(), time.perf_counter()


def _now() -> float:
    """Wall-clock seconds with perf_counter resolution (time.time() is ~15 ms on Windows)."""
    return _EPOCH + (time.perf_counter() - _PERF0)


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    kind: str
    start: float
    end: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)
    status: str = "ok"
    error: str | None = None

    def set(self, **attrs: Any) -> None:
        self.attributes.update(attrs)

    def event(self, name: str, **attrs: Any) -> None:
        self.events.append({"name": name, "ts": _now(), **attrs})

    @property
    def duration_ms(self) -> float:
        return round(((self.end or _now()) - self.start) * 1000, 2)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind,
            "start": self.start,
            "end": self.end,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "attributes": self.attributes,
            "events": self.events,
        }


class Tracer:
    def __init__(self, exporters: list | None = None):
        self.exporters = list(exporters or [])

    @staticmethod
    def current() -> Span | None:
        return _CURRENT.get()

    @contextmanager
    def span(self, name: str, kind: str = "internal", *, trace_id: str | None = None, **attrs: Any) -> Iterator[Span]:
        parent = _CURRENT.get()
        span = Span(
            trace_id=trace_id or (parent.trace_id if parent else uuid.uuid4().hex),
            span_id=uuid.uuid4().hex[:16],
            parent_id=parent.span_id if parent and (trace_id is None or parent.trace_id == trace_id) else None,
            name=name,
            kind=kind,
            start=_now(),
            attributes=dict(attrs),
        )
        token = _CURRENT.set(span)
        for ex in self.exporters:
            _safe(ex, "on_start", span)
        try:
            yield span
        except BaseException as exc:
            span.status = "error"
            span.error = f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            span.end = _now()
            _CURRENT.reset(token)
            for ex in self.exporters:
                _safe(ex, "on_end", span)


def _safe(exporter: Any, method: str, span: Span) -> None:
    fn = getattr(exporter, method, None)
    if fn is None:
        return
    try:
        fn(span)
    except Exception:  # telemetry must never break the workload
        pass


SPANS_SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    trace_id TEXT, span_id TEXT PRIMARY KEY, parent_id TEXT, name TEXT, kind TEXT,
    start REAL, end_ts REAL, duration_ms REAL, status TEXT, error TEXT, attributes TEXT, events TEXT
);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id, start);
"""


class SpanStore:
    """SQLite span exporter. Buffered (evals): flush when the root span ends.
    Unbuffered (API): write every span as it ends so the UI can follow a live run."""

    def __init__(self, path: str | Path, *, buffered: bool = True):
        self.path = Path(path)
        self.buffered = buffered
        self._lock = threading.Lock()
        self._buffer: dict[str, list[Span]] = defaultdict(list)
        with session(self.path, wal=True) as conn:
            conn.executescript(SPANS_SCHEMA)

    def on_end(self, span: Span) -> None:
        with self._lock:
            self._buffer[span.trace_id].append(span)
            flush = not self.buffered or span.parent_id is None or len(self._buffer[span.trace_id]) >= 500
            batch = self._buffer.pop(span.trace_id) if flush else None
        if batch:
            self._write(batch)

    def _write(self, spans: list[Span]) -> None:
        with session(self.path, wal=True) as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO spans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (s.trace_id, s.span_id, s.parent_id, s.name, s.kind, s.start, s.end, s.duration_ms, s.status, s.error,
                     json.dumps(s.attributes, default=str), json.dumps(s.events, default=str))
                    for s in spans
                ],
            )

    def get_trace(self, trace_id: str) -> list[dict]:
        with session(self.path, readonly=True) as conn:
            out = rows(conn.execute("SELECT * FROM spans WHERE trace_id = ? ORDER BY start, rowid", (trace_id,)))
        for r in out:
            r["attributes"] = json.loads(r["attributes"] or "{}")
            r["events"] = json.loads(r["events"] or "[]")
            r["end"] = r.pop("end_ts")
        return out


class JSONLExporter:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def on_end(self, span: Span) -> None:
        line = json.dumps(span.to_dict(), default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


class OTelExporter:
    """Bridge to OpenTelemetry when the SDK is installed and an OTLP endpoint is set.

    Enabled by OTEL_EXPORTER_OTLP_ENDPOINT. Each Matchpoint span is mirrored as
    an OTel span with the same parent/child structure and attributes.
    """

    def __init__(self) -> None:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", "matchpoint")}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        self._trace = trace
        self._tracer = provider.get_tracer("matchpoint")
        self._live: dict[str, Any] = {}

    def on_start(self, span: Span) -> None:
        parent = self._live.get(span.parent_id) if span.parent_id else None
        ctx = self._trace.set_span_in_context(parent) if parent else None
        self._live[span.span_id] = self._tracer.start_span(span.name, context=ctx, start_time=int(span.start * 1e9))

    def on_end(self, span: Span) -> None:
        otel = self._live.pop(span.span_id, None)
        if otel is None:
            return
        for k, v in span.attributes.items():
            otel.set_attribute(k, v if isinstance(v, str | int | float | bool) else json.dumps(v, default=str))
        otel.set_attribute("matchpoint.kind", span.kind)
        for ev in span.events:
            otel.add_event(ev["name"], {k: str(v) for k, v in ev.items() if k not in ("name", "ts")})
        if span.status == "error":
            otel.set_status(self._trace.Status(self._trace.StatusCode.ERROR, span.error or ""))
        otel.end(end_time=int((span.end or time.time()) * 1e9))


def build_tracer(app_db: Path, *, jsonl: Path | None = None, buffered: bool = True) -> tuple[Tracer, SpanStore]:
    store = SpanStore(app_db, buffered=buffered)
    exporters: list = [store]
    if jsonl:
        exporters.append(JSONLExporter(jsonl))
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        try:
            exporters.append(OTelExporter())
        except ImportError:
            pass
    return Tracer(exporters), store
