"""Process-wide metrics with a Prometheus text exposition (GET /metrics)."""

from __future__ import annotations

import threading
from collections import defaultdict

BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple, float] = defaultdict(float)
        self._hist: dict[tuple, list[float]] = defaultdict(list)
        self._help: dict[str, str] = {}

    @staticmethod
    def _key(name: str, labels: dict) -> tuple:
        return (name, tuple(sorted((k, str(v)) for k, v in labels.items())))

    def inc(self, name: str, value: float = 1.0, **labels) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += value

    def observe(self, name: str, value: float, **labels) -> None:
        with self._lock:
            self._hist[self._key(name, labels)].append(value)

    def counter(self, name: str, **labels) -> float:
        with self._lock:
            return self._counters.get(self._key(name, labels), 0.0)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._hist.clear()

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"{name}{_fmt(labels)} {value:g}")
            for (name, labels), values in sorted(self._hist.items()):
                cum = 0
                for b in BUCKETS:
                    cum = sum(1 for v in values if v <= b)
                    lines.append(f"{name}_bucket{_fmt(labels + (('le', str(b)),))} {cum}")
                lines.append(f"{name}_bucket{_fmt(labels + (('le', '+Inf'),))} {len(values)}")
                lines.append(f"{name}_sum{_fmt(labels)} {sum(values):.6f}")
                lines.append(f"{name}_count{_fmt(labels)} {len(values)}")
        return "\n".join(lines) + "\n"


def _fmt(labels: tuple) -> str:
    if not labels:
        return ""
    return "{" + ",".join(f'{k}="{v}"' for k, v in labels) + "}"


METRICS = Metrics()
