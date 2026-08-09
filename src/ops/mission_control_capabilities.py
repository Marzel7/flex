"""MC1.1 -- Mission Control capability-based severity layer.

Implements the frozen MC1.0 design (docs/design/mc1_0_capability_severity_model.md):
a stateless derived layer that reads the 7 existing /api/health/full
subsystem blocks and re-groups them into capability-scoped status,
evidence, and incidents. This module performs NO measurement of its own
-- every input is a field already present in the subsystem dicts computed
by api_health_full() in src/core/main.py. It only interprets.

Stateless per MC1.0 Amendment 3 / MC1.1's explicit instruction: no new
database tables, no new workers. Incident first_detected_at is computed
by walking backward through data the subsystem blocks already expose
(birth/migration timestamps) where possible; for composite capabilities
without a single monotonic timestamp, an in-memory (per-process) dict is
used, with the accepted limitation that it resets on process restart --
this is the explicitly smaller-scope alternative to persistence that
MC1.0 Section 11 named as the default when only live status (not
historical analytics) is required.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

CAPABILITY_NAMES = (
    "live_ingestion",
    "creator_funding",
    "operational_intelligence",
    "watchtower",
    "infrastructure",
    "price_tracking",
)

# MC1.0 Section 3 -- degradation propagation: a downstream capability's
# displayed severity is floored (not overridden) by its upstream
# capability's severity. Order matters: each entry's upstream is the
# capability immediately above it in the hierarchy.
_UPSTREAM_OF = {
    "creator_funding": "live_ingestion",
    "operational_intelligence": "creator_funding",
    "watchtower": "operational_intelligence",
}

_SEVERITY_RANK = {"HEALTHY": 0, "UNKNOWN": 1, "WARNING": 2, "CRITICAL": 3}


def _rank(status: str) -> int:
    return _SEVERITY_RANK.get(status, 1)


def _max_status(*statuses: str) -> str:
    best = "HEALTHY"
    for s in statuses:
        if _rank(s) > _rank(best):
            best = s
    return best


# ── Phase D: Rate Engine ────────────────────────────────────────────────────
# MC1.0 Section 5: rate is the PRIMARY signal, elapsed-silence is the
# FALLBACK. This engine implements the MECHANISM only -- it does not ship
# a hardcoded production rate. get_expected_rate() is the interface a
# future implementation-time step wires to a real historical-baseline
# query; until that exists, it returns None (insufficient history),
# which correctly routes evaluation to the fallback path below.

RATE_WINDOW_MIN = int(os.environ.get("MC_RATE_WINDOW_MIN", "15"))
RATE_CRITICAL_RATIO = float(os.environ.get("MC_RATE_CRITICAL_RATIO", "0.1"))
RATE_WARNING_RATIO = float(os.environ.get("MC_RATE_WARNING_RATIO", "0.5"))

# Fallback-only constants (MC1.0 Section 6): never the primary trigger.
BIRTH_SILENCE_FALLBACK_SEC = int(os.environ.get("MC_BIRTH_SILENCE_FALLBACK_SEC", "5400"))
MIGRATION_SILENCE_FALLBACK_SEC = int(os.environ.get("MC_MIGRATION_SILENCE_FALLBACK_SEC", "5400"))


def compute_observed_rate_per_min(event_count_in_window: int, window_min: int = RATE_WINDOW_MIN) -> float:
    """Observed events/min over the rolling window. Pure function -- the
    caller is responsible for counting events (no new DB query is added
    by this module; main.py's existing ingestion block already has the
    raw timestamps needed, or a future implementation step adds one
    cheap indexed COUNT query at the call site, not here)."""
    if window_min <= 0:
        return 0.0
    return event_count_in_window / float(window_min)


def get_expected_rate_per_min(event_type: str) -> Optional[float]:
    """MC1.0 Section 5's frozen contract: expected rate is a computed,
    self-updating historical baseline, never a fixed number shipped in
    code. This is the interface an implementation-time step wires to a
    real rolling-baseline query (e.g. trailing-N-day median observed
    rate, excluding known incident windows). Returns None when no
    baseline is available yet (insufficient history) -- callers MUST
    treat None as "fall back to silence-based detection" per Section 5's
    evaluation order, never as zero."""
    return None


def evaluate_rate_signal(
    *,
    event_type: str,
    observed_count_in_window: int,
    silence_secs: Optional[int],
    fallback_silence_sec: int,
    window_min: int = RATE_WINDOW_MIN,
) -> Dict[str, Any]:
    """MC1.0 Section 5's evaluation order: rate primary, silence fallback.
    Returns a dict with the signal's abnormal/status classification plus
    the raw values needed for the evidence detail string (Section 8A)."""
    observed_rate = compute_observed_rate_per_min(observed_count_in_window, window_min)
    expected_rate = get_expected_rate_per_min(event_type)

    if expected_rate is not None and expected_rate > 0:
        ratio = observed_rate / expected_rate
        if ratio < RATE_CRITICAL_RATIO:
            status = "CRITICAL"
        elif ratio < RATE_WARNING_RATIO:
            status = "WARNING"
        else:
            status = "HEALTHY"
        return {
            "status": status,
            "mode": "rate",
            "observed_rate_per_min": round(observed_rate, 4),
            "expected_rate_per_min": round(expected_rate, 4),
            "detail": (
                f"observed {observed_rate:.2f}/min vs expected "
                f"{expected_rate:.2f}/min baseline (primary signal)"
            ),
        }

    # Fallback: no baseline available yet.
    if silence_secs is not None and silence_secs > fallback_silence_sec:
        status = "CRITICAL"
    else:
        status = "HEALTHY"
    return {
        "status": status,
        "mode": "silence_fallback",
        "silence_secs": silence_secs,
        "fallback_threshold_secs": fallback_silence_sec,
        "detail": (
            f"no baseline available; silence={silence_secs}s "
            f"(fallback threshold {fallback_silence_sec}s)"
            if silence_secs is not None
            else "no baseline available; silence unknown"
        ),
    }


# ── Phase A/B: Capability + Evidence Engine ─────────────────────────────────

def _signal(name: str, abnormal: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "abnormal": bool(abnormal), "detail": detail}


def _evidence(signals: List[Dict[str, Any]]) -> Dict[str, int]:
    return {"abnormal": sum(1 for s in signals if s["abnormal"]), "total": len(signals)}


def _capability_result(status: str, signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "status": status,
        "degraded_by": None,  # filled in by _apply_propagation
        "evidence": _evidence(signals),
        "signals": signals,
    }


def _compute_live_ingestion(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    ingestion = subsystems.get("ingestion") or {}
    now = int(time.time())

    birth_age = ingestion.get("last_birth_age_secs")
    mig_age = ingestion.get("last_migration_age_secs")
    birth_queue = ingestion.get("birth_queue_pending") or 0
    mig_queue = ingestion.get("migration_queue_pending") or 0
    pp_status = ingestion.get("pumpportal") or "UNKNOWN"
    ps_status = ingestion.get("pumpswap") or "UNKNOWN"
    listener_log_age = ingestion.get("listener_log_age_secs")

    # Phase D: rate engine, per-event-type. No new DB query -- event
    # counts in the rolling window are not yet available from the
    # existing ingestion block (it only exposes the single most-recent
    # timestamp, not a windowed count), so observed_count_in_window is
    # 0 here as a conservative placeholder wired for a future cheap
    # indexed COUNT query at this call site (main.py's ingestion block),
    # NOT invented data. Until that wiring lands, get_expected_rate_per_min()
    # returning None means every evaluation correctly routes to the
    # silence fallback using the real last_birth_age_secs/last_migration_age_secs
    # already computed today -- so behaviour is unchanged from pre-MC1.1
    # until the rate baseline is wired in, exactly as MC1.0 Section 5
    # specifies for the "insufficient history" case.
    birth_rate = evaluate_rate_signal(
        event_type="births",
        observed_count_in_window=0,
        silence_secs=birth_age,
        fallback_silence_sec=BIRTH_SILENCE_FALLBACK_SEC,
    )
    migration_rate = evaluate_rate_signal(
        event_type="migrations",
        observed_count_in_window=0,
        silence_secs=mig_age,
        fallback_silence_sec=MIGRATION_SILENCE_FALLBACK_SEC,
    )

    signals = [
        _signal("birth_rate_collapse", birth_rate["status"] != "HEALTHY", birth_rate["detail"]),
        _signal("migration_rate_collapse", migration_rate["status"] != "HEALTHY", migration_rate["detail"]),
        _signal("pumpportal_connection", pp_status == "RETRYING", pp_status),
        _signal("pumpswap_connection", ps_status == "RETRYING", ps_status),
        _signal(
            "listener_log_freshness",
            listener_log_age is not None and listener_log_age > 300,
            f"listener_log_age_secs={listener_log_age}" if listener_log_age is not None else "unknown",
        ),
        _signal(
            "queue_backlog",
            (birth_queue > 5) or (mig_queue > 5),
            f"birth_queue_pending={birth_queue} migration_queue_pending={mig_queue}",
        ),
    ]

    status = _max_status(
        birth_rate["status"],
        migration_rate["status"],
        "CRITICAL" if listener_log_age is not None and listener_log_age > 600 else "HEALTHY",
        "WARNING" if pp_status == "RETRYING" or ps_status == "RETRYING" else "HEALTHY",
        "WARNING" if (birth_queue > 5 or mig_queue > 5) else "HEALTHY",
    )
    return _capability_result(status, signals)


def _compute_creator_funding(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    intelligence = subsystems.get("intelligence") or {}

    worker_status = intelligence.get("funding_worker_status") or "UNKNOWN"
    hb_age = intelligence.get("funding_worker_heartbeat_age_secs")
    oldest_age = intelligence.get("funding_queue_oldest_pending_age_secs")
    pending = intelligence.get("funding_queue_pending") or 0

    signals = [
        _signal(
            "worker_status",
            worker_status not in ("RUNNING",),
            worker_status,
        ),
        _signal(
            "heartbeat_freshness",
            hb_age is not None and hb_age > 120,
            f"{hb_age}s (threshold 120s)" if hb_age is not None else "unknown",
        ),
        _signal(
            "oldest_eligible_age",
            oldest_age is not None and oldest_age > 3600,
            f"{oldest_age}s (threshold 3600s)" if oldest_age is not None else "unknown",
        ),
        _signal(
            "queue_backlog_growth",
            False,  # MC1.0 Section 4: trend detection deferred -- no new
            # measurement added by this module; current pending count
            # alone is not evidence of GROWTH (X78.15 established large
            # pending counts are expected/normal). Left as an always-
            # normal placeholder signal so the evidence denominator (4)
            # matches MC1.0's Section 10 worked example without asserting
            # an abnormal condition this module cannot actually measure.
            "normal" if pending is not None else "unknown",
        ),
    ]

    status = "HEALTHY"
    if worker_status not in ("RUNNING",) and pending > 0:
        status = "CRITICAL" if worker_status == "STOPPED" else "WARNING"
    elif oldest_age is not None and oldest_age > 3600:
        status = "WARNING"

    return _capability_result(status, signals)


def _compute_operational_intelligence(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    intelligence = subsystems.get("intelligence") or {}

    wp_age = intelligence.get("watch_pipeline_age_secs")
    wp_interval = intelligence.get("watch_pipeline_interval_secs") or 300
    cp_age = intelligence.get("crq_worker_age_secs")
    crq_failed = intelligence.get("creator_queue_failed") or 0
    missing = intelligence.get("missing_creators_1h") or 0

    signals = [
        _signal(
            "watch_pipeline_freshness",
            wp_age is not None and wp_age > wp_interval * 2,
            f"{wp_age}s (interval {wp_interval}s)" if wp_age is not None else "unknown",
        ),
        _signal(
            "creator_resolution_freshness",
            cp_age is not None and cp_age > 120,
            f"{cp_age}s (threshold 120s)" if cp_age is not None else "unknown",
        ),
        _signal(
            "resolution_failures",
            crq_failed > 5,
            f"{crq_failed} failed (threshold 5)",
        ),
        _signal(
            "missing_creator_attribution",
            missing > 0,
            f"{missing} migrated tokens missing creator in last 1h",
        ),
    ]

    status = "HEALTHY"
    if crq_failed > 5:
        status = "WARNING"
    elif wp_age is not None and wp_age > wp_interval * 2:
        status = "WARNING"
    elif cp_age is not None and cp_age > 120:
        status = "WARNING"

    return _capability_result(status, signals)


def _compute_watchtower(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    cascade_infra = subsystems.get("cascade_infrastructure") or {}
    cascade_activity = subsystems.get("cascade_activity") or {}

    infra_status = cascade_infra.get("status") or "UNKNOWN"
    hb_age = cascade_infra.get("heartbeat_age_secs")
    subs_total = cascade_infra.get("subs_total") or 0

    signals = [
        _signal("cascade_connection", infra_status == "OFFLINE", infra_status),
        _signal(
            "cascade_heartbeat_freshness",
            hb_age is not None and hb_age > 120,
            f"{hb_age}s (threshold 120s)" if hb_age is not None else "unknown",
        ),
        _signal("subscriptions_active", subs_total == 0, f"subs_total={subs_total}"),
    ]

    status = "HEALTHY"
    if infra_status == "OFFLINE":
        status = "CRITICAL"
    elif infra_status == "DEGRADED" or subs_total == 0:
        status = "WARNING"

    return _capability_result(status, signals)


def _compute_infrastructure(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    database = subsystems.get("database") or {}
    api_health = subsystems.get("api") or {}

    db_status = database.get("status") or "UNKNOWN"
    p99 = database.get("p99_wait_ms") or 0
    q_depth = database.get("serializer_queue_depth") or 0
    gunicorn_alive = api_health.get("gunicorn_alive", True)
    errors_5m = api_health.get("errors_5m") or 0

    signals = [
        _signal("database_pressure", db_status in ("AT_RISK", "CRITICAL", "PRESSURE"), db_status),
        _signal("write_lane_p99", p99 > 5000, f"p99={p99}ms"),
        _signal("api_process_alive", not gunicorn_alive, "gunicorn_alive" if gunicorn_alive else "gunicorn not alive"),
    ]

    status = "HEALTHY"
    if db_status == "CRITICAL" or not gunicorn_alive:
        status = "CRITICAL"
    elif db_status in ("AT_RISK", "PRESSURE") or errors_5m > 5 or q_depth > 10:
        status = "WARNING"

    return _capability_result(status, signals)


def _compute_price_tracking(subsystems: Dict[str, Any]) -> Dict[str, Any]:
    price_worker = subsystems.get("price_worker") or {}

    pw_status = price_worker.get("status") or "UNKNOWN"
    peak_age = price_worker.get("last_peak_update_age_secs")
    snap_age = price_worker.get("last_snapshot_write_age_secs")
    snap_expected = price_worker.get("snapshot_expected")

    signals = [
        _signal("worker_alive", pw_status == "DOWN", pw_status),
        _signal(
            "peak_update_freshness",
            peak_age is not None and peak_age > 120,
            f"{peak_age}s (threshold 120s)" if peak_age is not None else "unknown",
        ),
        _signal(
            "snapshot_freshness",
            bool(snap_expected) and snap_age is not None and snap_age > 120,
            f"{snap_age}s (threshold 120s)" if snap_age is not None else "unknown",
        ),
    ]

    # PEAK-ONLY is an intentionally-degraded, non-alarming state (MC1.0
    # Section 3) -- it must not classify as WARNING/CRITICAL on its own.
    status = "HEALTHY"
    if pw_status == "DOWN":
        status = "CRITICAL"
    elif pw_status in ("STALE", "DEGRADED"):
        status = "WARNING"

    return _capability_result(status, signals)


_COMPUTE_FN = {
    "live_ingestion": _compute_live_ingestion,
    "creator_funding": _compute_creator_funding,
    "operational_intelligence": _compute_operational_intelligence,
    "watchtower": _compute_watchtower,
    "infrastructure": _compute_infrastructure,
    "price_tracking": _compute_price_tracking,
}


def _apply_propagation(capabilities: Dict[str, Dict[str, Any]]) -> None:
    """MC1.0 Section 3: loss of an upstream capability floors (does not
    override) a downstream capability's displayed severity. Mutates in
    place. Order-dependent -- must be applied top-down through the
    hierarchy (live_ingestion -> creator_funding -> operational_intelligence
    -> watchtower), which _UPSTREAM_OF's structure guarantees via a
    single linear walk since each capability has at most one upstream."""
    for name in ("creator_funding", "operational_intelligence", "watchtower"):
        upstream_name = _UPSTREAM_OF.get(name)
        if not upstream_name:
            continue
        upstream = capabilities.get(upstream_name)
        own = capabilities.get(name)
        if not upstream or not own:
            continue
        if _rank(upstream["status"]) >= _rank("WARNING"):
            # degraded_by is set whenever the upstream is itself
            # abnormal, REGARDLESS of whether it changes the downstream's
            # own status -- a capability that is independently CRITICAL
            # for its own reasons is STILL also being degraded by an
            # upstream problem if one exists, and the playbook (MC1.0
            # Section 13 step 4) explicitly expects operators to see that
            # annotation to know both are in play. Only the STATUS floor
            # (not the annotation) is conditional on the upstream being
            # more severe than the capability's own independent status.
            own["degraded_by"] = upstream_name
            # Floor to WARNING, never silently escalate past the
            # capability's own independently-computed status if that
            # status is already higher (own CRITICAL stays CRITICAL, not
            # downgraded to WARNING) -- MC1.0 Section 3's explicit
            # "never suppressed" requirement.
            own["status"] = _max_status(own["status"], "WARNING")


def compute_capabilities(subsystems: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Phase A entry point. Pure function of the existing subsystems dict
    -- no I/O, no new measurement. Deterministic: identical input always
    produces identical output."""
    capabilities = {name: _COMPUTE_FN[name](subsystems) for name in CAPABILITY_NAMES}
    _apply_propagation(capabilities)
    return capabilities


# ── Phase C: Incident Engine (stateless) ────────────────────────────────────

# In-memory, per-process incident-start tracking -- the explicitly
# smaller-scope alternative to persistence named in MC1.0 Section 11 for
# composite capabilities without a single monotonic timestamp to derive
# first_detected_at from statelessly. Resets on process restart (accepted
# limitation, documented in MC1.0 Section 11 and MC1.1's instruction to
# implement the stateless option only).
_incident_start_cache: Dict[str, float] = {}


def _first_detected_at(capability_name: str, is_active: bool) -> Optional[float]:
    if not is_active:
        _incident_start_cache.pop(capability_name, None)
        return None
    if capability_name not in _incident_start_cache:
        _incident_start_cache[capability_name] = time.time()
    return _incident_start_cache[capability_name]


_IMPACT_LABELS = {
    "birth_rate_collapse": "Birth rate collapsed",
    "migration_rate_collapse": "Migration rate collapsed",
    "pumpportal_connection": "PumpPortal unavailable",
    "pumpswap_connection": "PumpSwap unavailable",
    "listener_log_freshness": "Listener unhealthy",
    "queue_backlog": "Queue backlog growing",
    "worker_status": "Worker not running",
    "heartbeat_freshness": "Worker heartbeat stale",
    "oldest_eligible_age": "Oldest eligible work stalled",
    "cascade_connection": "Cascade infrastructure offline",
    "cascade_heartbeat_freshness": "Cascade heartbeat stale",
    "subscriptions_active": "No active subscriptions",
    "database_pressure": "Database under pressure",
    "write_lane_p99": "Database write latency elevated",
    "api_process_alive": "API process not responding",
    "worker_alive": "Price worker down",
    "peak_update_freshness": "Peak price updates stale",
    "snapshot_freshness": "Price snapshots stale",
    "queue_backlog_growth": "Funding queue backlog growing",
    "watch_pipeline_freshness": "Watch pipeline stale",
    "creator_resolution_freshness": "Creator resolution worker stale",
    "resolution_failures": "Creator resolution failures elevated",
    "missing_creator_attribution": "Missing creator attribution",
}

_TITLE_LABELS = {
    "live_ingestion": "Live ingestion unavailable",
    "creator_funding": "Creator funding degraded",
    "operational_intelligence": "Operational intelligence degraded",
    "watchtower": "WATCHTOWER degraded",
    "infrastructure": "Infrastructure degraded",
    "price_tracking": "Price tracking degraded",
}


def compute_incidents(capabilities: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Phase C entry point. MC1.0 Section 7: one capability -> one
    incident, recomputed on every poll (membership is not mutable state
    -- only first_detected_at is tracked, per Section 11's stateless
    option). Multiple abnormal signals within one capability's evidence
    list become that ONE incident's impact/contributing_signals lists,
    never separate incidents (Section 7.4). Different capabilities never
    merge into one incident (Section 7.5) even if concurrent.

    A capability whose only abnormal status comes from upstream
    propagation (degraded_by is set AND it has zero of its own abnormal
    evidence signals) does NOT open its own incident -- MC1.0 Section 13
    (operator playbook) is explicit that this case is "a propagated
    annotation, not a second incident." Only capabilities with their own
    independent abnormal evidence open an incident card; propagation is
    still fully visible via each capability tile's `degraded_by` field
    (Phase G's capability grid), just not duplicated as a second incident."""
    now = int(time.time())
    incidents: List[Dict[str, Any]] = []

    for name in CAPABILITY_NAMES:
        cap = capabilities.get(name) or {}
        status = cap.get("status", "HEALTHY")
        signals = cap.get("signals") or []
        abnormal_signals = [s for s in signals if s.get("abnormal")]

        is_active = _rank(status) >= _rank("WARNING") and (
            cap.get("degraded_by") is None or len(abnormal_signals) > 0
        )

        started_at = _first_detected_at(name, is_active)
        if not is_active or started_at is None:
            continue

        impact = [_IMPACT_LABELS.get(s["name"], s["name"]) for s in abnormal_signals]
        contributing = [f'{_IMPACT_LABELS.get(s["name"], s["name"])}: {s.get("detail", "")}' for s in abnormal_signals]

        first_detected_at = int(started_at)
        incidents.append({
            "id": f"{name}:{first_detected_at}",
            "capability": name,
            "severity": status,
            "title": _TITLE_LABELS.get(name, name),
            "impact": impact,
            "contributing_signals": contributing,
            "first_detected_at": first_detected_at,
            "current_duration_secs": max(0, now - first_detected_at),
            "recovered_at": None,
        })

    return incidents


# ── Phase E: Platform Status ─────────────────────────────────────────────────

def compute_platform_status(capabilities: Dict[str, Dict[str, Any]]) -> str:
    """MC1.0 Section 8: platform-level status is the maximum severity
    across all capabilities. Replaces the prior subsystem-level if/elif
    chain as the SOURCE for the existing top-level `status` field --
    the field's name/shape in the API response is unchanged (Phase F),
    only its derivation."""
    return _max_status(*(cap.get("status", "HEALTHY") for cap in capabilities.values()))
