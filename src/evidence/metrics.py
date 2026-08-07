from __future__ import annotations

import threading
from collections import Counter, defaultdict, deque
from typing import Any


class EvidenceMetrics:
    """Process-local metrics. Observation never writes a database."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=5000))

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] += amount

    def observe(self, name: str, value: float) -> None:
        with self._lock:
            self._samples[name].append(float(value))

    @staticmethod
    def _percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        return round(ordered[round((len(ordered) - 1) * fraction)], 3)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(sorted(self._counters.items()))
            samples = {key: list(value) for key, value in self._samples.items()}
        distributions = {}
        for key, values in sorted(samples.items()):
            distributions[key] = {
                "count": len(values),
                "average": round(sum(values) / len(values), 3) if values else None,
                "p50": self._percentile(values, 0.5),
                "p95": self._percentile(values, 0.95),
                "maximum": round(max(values), 3) if values else None,
            }
        return {"counters": counters, "distributions": distributions}
