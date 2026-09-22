"""One derived read model for the four current system-health indicators.

The input is the authoritative ``/api/health/full`` subsystem payload.  This
module performs no I/O and deliberately does not measure capture coverage.
"""
from __future__ import annotations

from typing import Any

METRIC_ORDER = ("platform", "live_ingestion", "intelligence", "infrastructure")


def _status(value: object) -> str:
    status = str(value or "UNKNOWN").upper()
    if status in {"HEALTHY", "AVAILABLE", "CONNECTED", "RUNNING", "ACTIVE", "IDLE", "WATCHING"}:
        return "HEALTHY"
    if status in {"DEGRADED", "WARNING", "PRESSURE", "AT_RISK", "RETRYING", "ERRORS"}:
        return "DEGRADED"
    if status in {"DOWN", "OFFLINE", "CRITICAL", "STOPPED"}:
        return "DOWN"
    return "UNKNOWN"


def build_shared_health_metrics(
    subsystems: dict[str, Any], *, acquisition_availability: dict[str, Any] | None = None,
    db_write_degradation: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Project the existing Health-page authorities without independent state."""
    api = subsystems.get("api") or {}
    ingestion = subsystems.get("ingestion") or {}
    intelligence = subsystems.get("intelligence") or {}
    database = subsystems.get("database") or {}
    has_acquisition = bool(acquisition_availability)
    acquisition_availability = acquisition_availability or {}
    birth = _status((acquisition_availability.get("birth") or {}).get("current_state"))
    migration = _status((acquisition_availability.get("migration") or {}).get("current_state"))
    base_ingestion = _status(ingestion.get("status"))
    if has_acquisition and "DOWN" in {birth, migration}:
        ingestion_status = "DOWN"
    elif has_acquisition and "UNKNOWN" in {birth, migration}:
        ingestion_status = "UNKNOWN" if base_ingestion != "DOWN" else "DOWN"
    elif has_acquisition and "DEGRADED" in {birth, migration}:
        ingestion_status = "DEGRADED"
    else:
        ingestion_status = base_ingestion
    db_state = _status((db_write_degradation or {}).get("current_state"))
    infrastructure_status = "DEGRADED" if db_write_degradation and db_state == "DEGRADED" else _status(database.get("status"))
    platform_status = _status(api.get("status"))
    if has_acquisition and ingestion_status == "DOWN":
        platform_status = "DOWN"
    elif has_acquisition and ingestion_status == "UNKNOWN" and platform_status == "HEALTHY":
        platform_status = "UNKNOWN"
    elif (has_acquisition and ingestion_status == "DEGRADED" or db_write_degradation and infrastructure_status == "DEGRADED") and platform_status == "HEALTHY":
        platform_status = "DEGRADED"
    return {
        "platform": {"label": "Platform", "source": "api + current acquisition/write state", "status": platform_status,
                     "freshness_seconds": None, "detail": f"API errors/5m: {api.get('errors_5m', 'unknown')}"},
        "live_ingestion": {"label": "Live Ingestion", "source": "ingestion + durable acquisition transitions", "status": ingestion_status,
                           "freshness_seconds": ingestion.get("listener_log_age_secs"),
                           "detail": f"birth age={ingestion.get('last_birth_age_secs')}s; migration age={ingestion.get('last_migration_age_secs')}s"},
        "intelligence": {"label": "Intelligence", "source": "intelligence", "status": _status(intelligence.get("status")),
                         "freshness_seconds": intelligence.get("crq_worker_age_secs"),
                         "detail": f"pending attribution={intelligence.get('pending_attribution', 'unknown')}"},
        "infrastructure": {"label": "Infrastructure", "source": "database + durable write incidents", "status": infrastructure_status,
                            "freshness_seconds": None,
                            "detail": f"queue={database.get('serializer_queue_depth', 'unknown')}; p99={database.get('p99_wait_ms', 'unknown')}ms"},
    }
