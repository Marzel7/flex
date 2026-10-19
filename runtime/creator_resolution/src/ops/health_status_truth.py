"""Pure health-classification helpers shared by lightweight/API views."""
from __future__ import annotations

from typing import Any, Dict, Iterable


DEFAULT_REQUIRED_WORKERS = frozenset(
    {"creator-funding", "creator-resolution"}
)


def classify_worker_heartbeats(
    rows: Dict[str, Dict[str, Any]], required_workers: Iterable[str]
) -> Dict[str, Any]:
    required = frozenset(required_workers)
    active = {name: value for name, value in rows.items() if name in required}
    inactive = {name: value for name, value in rows.items() if name not in required}
    return {
        "stale_workers": sorted(name for name, value in active.items() if value.get("stale")),
        "missing_workers": sorted(required - set(active)),
        "required_workers": sorted(required),
        "inactive_workers": sorted(inactive),
    }


def classify_database_pressure(p99_wait_ms: float, queue_depth: int) -> str:
    """Classify current pressure only; cumulative historical errors cannot
    keep a recovered system permanently critical."""
    if p99_wait_ms > 30000:
        return "CRITICAL"
    if p99_wait_ms > 5000 or queue_depth > 10:
        return "AT_RISK"
    if p99_wait_ms > 1000 or queue_depth > 6:
        return "PRESSURE"
    return "HEALTHY"
