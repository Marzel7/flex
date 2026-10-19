"""X76.5A -- Walkback Candidate Generation health composition.

X76.5 restored live Treasury Review candidate generation and added a
self-kill guard for a stuck write lease. Self-healing by itself is not
sufficient: this module composes everything Mission Control needs to
distinguish HEALTHY progress from an IDLE-but-fine worker, a DEGRADED
worker, a STALLED write lease, an in-progress RECOVERING restart, or a
fully STOPPED process -- and exposes candidate-generation freshness and
recovery-event history alongside it.

Pure read-only composition over:
  - src/ops/walkback_health.py::build_walkback_health() -- queue depth,
    completion rate, heartbeat age (already existed, unchanged).
  - src/ops/watchtower_recovery_diagnostics.py::_candidate_generation_metrics()
    -- the X76.5 candidate-freshness metrics (already existed, unchanged).
  - database/wt_ops_v2.db.write.lock.owner -- the CROSS-PROCESS lease file
    (database_write_service.py's own on-disk lease record), the only way
    a different process (this Flask app) can see whether ANOTHER process
    (walkback_worker) currently holds the write lane, and for how long.
  - src/ops/walkback_recovery_log.py -- the X76.5A self-kill/manual-
    termination event history.
  - Supervisor's own process status (via supervisorctl, best-effort).

Never writes to any attribution/reconciliation/resolver/promotion table.
Never changes candidate-selection semantics. Diagnostics only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from typing import Any

# Same threshold walkback_worker.py's own self-kill guard uses, kept in
# sync via the same env var rather than a second hardcoded constant.
SAFE_LEASE_AGE_SECONDS = int(os.environ.get("WALKBACK_SAFE_LEASE_AGE_SECONDS", "120"))
SELF_KILL_THRESHOLD_SECONDS = int(os.environ.get("WALKBACK_MAX_LEASE_STUCK_SECONDS", "600"))
RECOVERING_WINDOW_SECONDS = int(os.environ.get("WALKBACK_RECOVERING_WINDOW_SECONDS", "300"))
HEARTBEAT_STALE_SECONDS = int(os.environ.get("WALKBACK_HEARTBEAT_STALE_SECONDS", "180"))


def _read_lease_owner(ops_db_path: str) -> dict[str, Any] | None:
    """Cross-process lease introspection via the on-disk owner file --
    the ONLY way a different process can see this. Absence of the file
    means no write is currently in flight (healthy, not an error)."""
    owner_path = f"{os.path.realpath(ops_db_path)}.write.lock.owner"
    try:
        with open(owner_path, encoding="utf-8") as f:
            owner = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    acquired_at = owner.get("acquired_at")
    age = (time.time() - acquired_at) if acquired_at else None
    return {
        "command": owner.get("command"),
        "transaction_id": owner.get("transaction_id"),
        "process_pid": owner.get("process_pid"),
        "acquired_at": acquired_at,
        "age_seconds": age,
    }


def _supervisor_status(process_name: str = "walkback_worker") -> dict[str, Any]:
    """Best-effort Supervisor process status. Never raises -- a
    supervisorctl failure must not take down the health page."""
    try:
        conf = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "../../config/supervisor/supervisord.conf"
        ))
        result = subprocess.run(
            ["supervisorctl", "-c", conf, "status", process_name],
            capture_output=True, text=True, timeout=5,
        )
        line = (result.stdout or "").strip()
        if not line:
            return {"available": False, "running": None, "raw": None}
        parts = line.split()
        state = parts[1] if len(parts) > 1 else None
        pid = None
        uptime = None
        for i, tok in enumerate(parts):
            if tok == "pid" and i + 1 < len(parts):
                pid = parts[i + 1].rstrip(",")
            if tok == "uptime" and i + 1 < len(parts):
                uptime = parts[i + 1]
        return {"available": True, "running": state == "RUNNING", "state": state,
                "pid": pid, "uptime": uptime, "raw": line}
    except Exception:
        return {"available": False, "running": None, "raw": None}


def _determine_status(
    *, supervisor: dict[str, Any], heartbeat_age: float | None,
    lease: dict[str, Any] | None, walkback_health: dict[str, Any],
    candidate_generation: dict[str, Any], recent_self_kill: bool,
) -> tuple[str, list[str]]:
    """One clear primary state, per the milestone's own definitions,
    evaluated in a fixed precedence order so exactly one state is ever
    returned (never ambiguous, never "healthy AND stalled")."""
    reasons: list[str] = []

    # STOPPED -- checked first: nothing else is meaningful if the process
    # isn't running at all.
    if supervisor.get("available") and supervisor.get("running") is False:
        return "STOPPED", ["walkback_worker process is not running"]

    # RECOVERING -- a self-kill fired recently; give the fresh process a
    # grace window before judging it on steady-state criteria, so a
    # restart that is progressing normally isn't immediately relabelled
    # STALLED again by residual lease-file staleness from the OLD process.
    if recent_self_kill:
        reasons.append("self-kill guard fired recently; worker restarting")
        return "RECOVERING", reasons

    # STALLED -- a lease is held past the self-kill threshold (should be
    # rare/transient in practice, since the worker's own guard fires
    # first, but Mission Control must show this state honestly if
    # observed, e.g. mid-window before the worker's own next cycle check).
    if lease and lease.get("age_seconds") is not None and lease["age_seconds"] > SELF_KILL_THRESHOLD_SECONDS:
        reasons.append(f"write lease held {lease['age_seconds']:.0f}s (self-kill threshold {SELF_KILL_THRESHOLD_SECONDS}s)")
        return "STALLED", reasons
    if lease and lease.get("age_seconds") is not None and lease["age_seconds"] > SAFE_LEASE_AGE_SECONDS:
        reasons.append(f"write lease held {lease['age_seconds']:.0f}s (safe threshold {SAFE_LEASE_AGE_SECONDS}s) -- candidate generation may be blocked")
        return "STALLED", reasons
    if heartbeat_age is not None and heartbeat_age > HEARTBEAT_STALE_SECONDS:
        # Heartbeat gone stale without an explained lease -- the worker
        # has stopped making progress, which is the STALLED definition
        # even without a currently-held lease file (e.g. it died between
        # cycles without ever holding a write lease at that moment).
        reasons.append(f"heartbeat stale ({heartbeat_age:.0f}s, threshold {HEARTBEAT_STALE_SECONDS}s) -- useful work has stopped")
        return "STALLED", reasons

    # DEGRADED -- worker IS progressing (heartbeat current, no stale
    # lease) but showing real friction: nested-write-failure evidence,
    # stalled running rows, or candidate generation delayed while
    # eligible work clearly exists.
    if walkback_health.get("nested_write_failures_last_hour", 0) > 0:
        reasons.append(f"{walkback_health['nested_write_failures_last_hour']} nested write failure(s) in the last hour")
    if walkback_health.get("stalled_running_jobs", 0) > 0:
        reasons.append(f"{walkback_health['stalled_running_jobs']} walkback job(s) stalled in running state")
    pending = walkback_health.get("pending", 0)
    completed_per_min = walkback_health.get("completed_per_minute", 0)
    if pending > 0 and completed_per_min == 0:
        reasons.append("pending walkback work exists but nothing completed in the last minute")
    # Candidate-generation silence: only unhealthy if walkback IS
    # progressing (so we know eligible LINEAGE_GAP outcomes are being
    # produced) but nothing has reached wt_treasury_review -- a zero here
    # while walkback itself is idle/healthy is NOT an error (per spec:
    # "do not classify zero candidates as unhealthy when ... no eligible
    # unknown treasury was found").
    if candidate_generation.get("stalled") and walkback_health.get("completed_last_hour", 0) > 0:
        reasons.append("walkback completed work in the last hour but no Treasury Review candidate was generated")
    if reasons:
        return "DEGRADED", reasons

    # IDLE vs HEALTHY -- both require heartbeat current + no stale lease
    # + no errors (already established above). The only distinction is
    # whether there is anything to do right now.
    if pending == 0 and walkback_health.get("running", 0) == 0 and walkback_health.get("completed_last_hour", 0) == 0:
        return "IDLE", ["no pending work, no recent completions -- worker is idle, not unhealthy"]

    return "HEALTHY", []


def build_walkback_candidate_health(
    ops_db_path: str, core_db_path: str, *, now: int | None = None,
) -> dict[str, Any]:
    now = int(now or time.time())

    conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        from src.ops.walkback_health import build_walkback_health
        from src.ops.watchtower_recovery_diagnostics import _candidate_generation_metrics
        from src.ops.walkback_recovery_log import recent_events, counts_in_window

        walkback_health = build_walkback_health(conn, now=now)
        candidate_generation = _candidate_generation_metrics(conn, now=now)
        recovery_events = recent_events(conn, worker="walkback_worker", limit=5)
        recovery_counts_1h = counts_in_window(conn, worker="walkback_worker", window_seconds=3600, now=now)
        recovery_counts_24h = counts_in_window(conn, worker="walkback_worker", window_seconds=86400, now=now)
    finally:
        conn.close()

    lease = _read_lease_owner(ops_db_path)
    supervisor = _supervisor_status("walkback_worker")

    recent_self_kill = bool(
        recovery_events
        and recovery_events[0].get("detected_at")
        and (now - recovery_events[0]["detected_at"]) <= RECOVERING_WINDOW_SECONDS
        and recovery_events[0].get("healthy_at") is None
    )

    status, reasons = _determine_status(
        supervisor=supervisor,
        heartbeat_age=walkback_health.get("heartbeat_age_seconds"),
        lease=lease,
        walkback_health=walkback_health,
        candidate_generation=candidate_generation,
        recent_self_kill=recent_self_kill,
    )

    warnings: list[str] = []
    if lease and lease.get("age_seconds") is not None and lease["age_seconds"] > SAFE_LEASE_AGE_SECONDS:
        warnings.append("Walkback write lease is stale. Candidate generation may be blocked.")
    if recent_self_kill:
        warnings.append("Worker recovered automatically after a stale write lease.")

    summary_by_status = {
        "HEALTHY": "Walkback candidate generation healthy.",
        "IDLE": "Walkback candidate generation idle (no pending work).",
        "DEGRADED": f"Walkback candidate generation degraded: {reasons[0] if reasons else 'see detail'}.",
        "STALLED": "Walkback stalled; Treasury Review candidates are not being generated.",
        "RECOVERING": "Walkback recovering after stale write lease.",
        "STOPPED": "Walkback worker is not running; candidate generation unavailable.",
    }

    return {
        "ok": True,
        "generated_at": now,
        "status": status,
        "reasons": reasons,
        "summary": summary_by_status.get(status, status),
        "warnings": warnings,
        "worker": {
            "supervisor": supervisor,
            "heartbeat_age_seconds": walkback_health.get("heartbeat_age_seconds"),
        },
        "walkback": {
            "pending": walkback_health.get("pending"),
            "running": walkback_health.get("running"),
            "completed_last_hour": walkback_health.get("completed_last_hour"),
            "completed_per_minute": walkback_health.get("completed_per_minute"),
            "average_completion_latency_seconds": walkback_health.get("average_completion_latency_seconds"),
            "oldest_pending_age_seconds": walkback_health.get("oldest_pending_age_seconds"),
            "stalled_running_jobs": walkback_health.get("stalled_running_jobs"),
            "nested_write_failures_last_hour": walkback_health.get("nested_write_failures_last_hour"),
        },
        "candidate_generation": candidate_generation,
        "lease": {
            "held": lease is not None,
            "owner_command": lease.get("command") if lease else None,
            "owner_transaction_id": lease.get("transaction_id") if lease else None,
            "owner_pid": lease.get("process_pid") if lease else None,
            "acquired_at": lease.get("acquired_at") if lease else None,
            "age_seconds": lease.get("age_seconds") if lease else None,
            "safe_threshold_seconds": SAFE_LEASE_AGE_SECONDS,
            "self_kill_threshold_seconds": SELF_KILL_THRESHOLD_SECONDS,
        },
        "recovery": {
            "self_kill_last_hour": recovery_counts_1h["self_kill"],
            "self_kill_last_day": recovery_counts_24h["self_kill"],
            "manual_termination_last_hour": recovery_counts_1h["manual_termination"],
            "manual_termination_last_day": recovery_counts_24h["manual_termination"],
            "last_event_at": recovery_events[0]["detected_at"] if recovery_events else None,
            "events": recovery_events,
        },
        "links": {
            "treasury_review": "/intelligence/treasury-review",
            "discovery": "/discovery",
            "walkback_queue": "/api/ops/walkback-queue",
        },
    }
