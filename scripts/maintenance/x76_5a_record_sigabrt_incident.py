#!/usr/bin/env python3
"""X76.5A -- Record the X76.5 SIGABRT incident in the walkback recovery log.

During X76.5's root-cause investigation, an accidental `os.kill(pid,
SIGABRT)` was sent to the live walkback_worker process (pid 53760) at
2026-08-06 00:05:36 UTC while checking for stack-trace tooling
availability -- not gated behind a confirmation check. Supervisor's own
log (logs/supervisor/supervisord.log) confirms:

    2026-08-06 00:05:36,404 WARN exited: walkback_worker (terminated by
        SIGABRT; not expected)
    2026-08-06 00:05:37,407 INFO spawned: 'walkback_worker' with pid 59618
    2026-08-06 00:05:42,648 INFO success: walkback_worker entered RUNNING
        state, process has stayed up for > than 5 seconds (startsecs)

This predates X76.5A's own self-kill-logging code (walkback_worker.py's
_check_stuck_lease did not yet call record_self_kill at the time this
incident occurred), so there is no organic row for it -- it must be
entered explicitly, and per the milestone's own instruction, labelled
"Manual / external process termination", never "Stale lease self-kill".

Idempotent: does nothing if a row for this exact detected_at already
exists.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "../..")))

from src.utils.db_locking import db_connect
from src.ops.walkback_recovery_log import ensure_schema, record_manual_termination

OPS_DB_PATH = os.environ.get(
    "WT_OPS_DB_PATH",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "../../database/wt_ops_v2.db")),
)

_DETECTED_AT = 1785974736   # 2026-08-06 00:05:36 UTC -- SIGABRT (supervisord.log)
_RESTARTED_AT = 1785974737  # 2026-08-06 00:05:37 UTC -- respawned as pid 59618
_HEALTHY_AT = 1785974742    # 2026-08-06 00:05:42 UTC -- entered RUNNING state


def backfill() -> dict:
    conn = db_connect(OPS_DB_PATH, timeout=30)
    try:
        ensure_schema(conn)
        existing = conn.execute(
            "SELECT event_id FROM wt_walkback_recovery_events "
            "WHERE worker='walkback_worker' AND event_kind='manual_external_termination' "
            "AND detected_at=?",
            (_DETECTED_AT,),
        ).fetchone()
        if existing:
            return {"already_present": True, "event_id": existing[0]}
        event_id = record_manual_termination(
            conn,
            worker="walkback_worker",
            reason="os.kill(pid, SIGABRT) sent during X76.5 investigation debugging "
                   "(not gated behind a confirmation check) -- NOT the worker's own "
                   "stale-lease self-kill guard, which did not yet exist at this time.",
            detected_at=_DETECTED_AT,
            restarted_at=_RESTARTED_AT,
            healthy_at=_HEALTHY_AT,
            notes="Confirmed via logs/supervisor/supervisord.log: 'exited: walkback_worker "
                  "(terminated by SIGABRT; not expected)' at 2026-08-06 00:05:36,404 UTC. "
                  "No data loss or automatic approvals occurred as a result.",
        )
        return {"already_present": False, "event_id": event_id}
    finally:
        conn.close()


if __name__ == "__main__":
    result = backfill()
    print(result)
