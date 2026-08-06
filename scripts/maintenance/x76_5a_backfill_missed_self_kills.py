#!/usr/bin/env python3
"""X76.5A -- Backfill the 4 real self-kill events missed by the original
logging bug.

Live validation (with the user's explicit go-ahead to let the guard fire
naturally rather than intervene manually) proved _check_stuck_lease()
correctly detects and self-kills a genuinely stuck lease -- confirmed 4
times in a row by walkback_worker.py's own CRITICAL_STUCK_LEASE log lines
and supervisord.log's own exited/spawned/entered-RUNNING sequence. But
the FIRST attempt at record_self_kill() itself failed every single time
with NestedDatabaseWriteError: db_connect() returns a TrackedConnection,
and _check_stuck_lease runs on a thread whose _thread_write_lease is, BY
DEFINITION, already poisoned by the very stuck lease it is reporting on
-- so the tracked write immediately self-nested against itself. Fixed in
the same commit (walkback_worker.py now uses the raw, unpatched
sqlite3.connect for this one write). This script backfills the 4 events
that predate that fix, using data already captured in
logs/supervisor/walkback_worker.log (CRITICAL_STUCK_LEASE lines, held
seconds, transaction IDs) and logs/supervisor/supervisord.log (precise
exited/spawned/entered-RUNNING timestamps).

Idempotent: skipped if a row for the same transaction_id already exists.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), "../..")))

from src.utils.db_locking import db_connect
from src.ops.walkback_recovery_log import ensure_schema, record_self_kill, mark_restarted, mark_healthy

OPS_DB_PATH = os.environ.get(
    "WT_OPS_DB_PATH",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "../../database/wt_ops_v2.db")),
)

# Each row: (held_seconds, transaction_id, detected_at, restarted_at, healthy_at)
# detected_at/restarted_at taken from supervisord.log's own "exited"/"spawned"
# lines (within milliseconds of each other -- Supervisor respawns
# immediately); healthy_at from "entered RUNNING state" (5s startsecs later).
_EVENTS = [
    (602.0, "a82ad79f-e6b5-4879-ac25-1a26cf853ff3", 1785976381, 1785976381, 1785976386),
    (602.0, "b4d84afa-8742-4cfb-aed8-34233c2daeb7", 1785977116, 1785977116, 1785977122),
    (602.0, "35f4875d-6c89-4144-abc4-8fe00f089e8d", 1785977722, 1785977722, 1785977727),
    (637.0, "93858d12-ce79-400f-835d-ada6151ec31d", 1785978448, 1785978448, 1785978453),
]


def backfill() -> dict:
    conn = db_connect(OPS_DB_PATH, timeout=30)
    try:
        ensure_schema(conn)
        inserted, skipped = 0, 0
        for held, txid, detected_at, restarted_at, healthy_at in _EVENTS:
            existing = conn.execute(
                "SELECT event_id FROM wt_walkback_recovery_events WHERE lease_transaction_id=?",
                (txid,),
            ).fetchone()
            if existing:
                skipped += 1
                continue
            event_id = record_self_kill(
                conn, worker="walkback_worker",
                reason=f"stale write lease held {held:.0f}s (threshold 600s) "
                       "-- backfilled from log lines; the original real-time logging "
                       "attempt itself failed (NestedDatabaseWriteError, self-nesting "
                       "on the very lease it was reporting), fixed in the same commit.",
                lease_age_seconds=held, lease_command="walkback_worker.py:482 in _ops_conn",
                lease_transaction_id=txid,
            )
            mark_restarted(conn, event_id, restarted_at=restarted_at, outcome="restarted successfully")
            mark_healthy(conn, event_id, healthy_at=healthy_at)
            inserted += 1
        return {"inserted": inserted, "skipped_already_present": skipped}
    finally:
        conn.close()


if __name__ == "__main__":
    print(backfill())
