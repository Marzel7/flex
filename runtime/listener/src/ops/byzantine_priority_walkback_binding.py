"""Operation-specific, post-commit scheduling adapter for the generic priority lane.

The adapter is intentionally limited to local operations-DB reads plus compact
writes.  It supplies scheduling context only; it neither evaluates strict
evidence nor writes canonical membership.
"""
from __future__ import annotations

import sqlite3
import os
from typing import Optional

from src.core.walkback_queue import enqueue_migration
from src.ops.operation_evidence_priority import (
    apply_walkback_request, enabled, record_post_commit_request, request_id,
)

OPERATION_ID = "d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334"
OPERATION_VERSION = "P3R_063E_BYZC_CURRENT"
REASON_VERSION = "KNOWN_OPERATION_CREATOR_CANDIDATE.v1"


def creator_cohort_snapshot(conn: sqlite3.Connection, *, operation_id: str = OPERATION_ID) -> dict:
    """Read the current qualified creator cohort locally and bind its high-water."""
    rows = conn.execute(
        "SELECT m.mint,m.assigned_at,q.creator FROM operator_launch_membership m "
        "JOIN wt_walkback_queue q USING(mint) WHERE m.operator_id=? "
        "AND COALESCE(q.creator,'')<>''", (operation_id,)
    ).fetchall()
    canonical_mints = conn.execute(
        "SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (operation_id,)
    ).fetchone()[0]
    creators = {str(r["creator"]) for r in rows}
    high_water = max((int(r["assigned_at"] or 0) for r in rows), default=0)
    return {"operation_id": operation_id, "canonical_mints": canonical_mints,
            "creator_resolved": len(rows), "creators": creators,
            "high_water": high_water,
            "version": f"OPERATION_ASSOCIATED_CREATOR_COHORT.v1:{high_water}"}


def bind_committed_launch(conn: sqlite3.Connection, *, mint: str, creator: Optional[str],
                          create_signature: str, create_slot: Optional[int],
                          launch_committed_at: int) -> dict:
    """Apply the optional acceleration only after a durable launch commit.

    This is called through the operations write service after the CREATE ledger
    transaction returned successfully.  A miss is a no-op and any caller
    exception is intended to be isolated by the listener.
    """
    if not enabled():
        return {"action": "DISABLED"}
    if not creator or not create_signature or not launch_committed_at:
        return {"action": "INELIGIBLE"}
    cohort = creator_cohort_snapshot(conn)
    if creator not in cohort["creators"]:
        return {"action": "MISS", "cohort_version": cohort["version"]}
    observation_id = os.environ.get("OPERATION_EVIDENCE_PRIORITY_OBSERVATION_ID")
    if observation_id:
        # The deterministic request identity is known before any request/queue write.
        from src.ops.bounded_observation_admission import admit
        candidate_id = request_id(operation_id=OPERATION_ID, operation_version=OPERATION_VERSION,
                                  entity_type="mint", entity_id=mint,
                                  evidence_pipeline="WALKBACK", reason_version=REASON_VERSION)
        try:
            admission = admit(conn, observation_id, candidate_id, mint)
        except Exception:
            # Observation control must not impair the already committed launch.
            return {"action": "OBSERVATION_GATE_UNAVAILABLE"}
        if admission == "REJECTED_CAP":
            return {"action": "OBSERVATION_CANDIDATE_CAP_REACHED", "observation_id": observation_id}
    request = record_post_commit_request(
        conn, operation_id=OPERATION_ID, operation_version=OPERATION_VERSION,
        mint=mint, source_launch_id=create_signature,
        source_launch_committed_at=launch_committed_at,
        creator_context_ref=creator, cohort_version=cohort["version"],
        reason_version=REASON_VERSION,
    )
    state = apply_walkback_request(
        conn, request=request, enqueue=enqueue_migration, creator=creator,
        create_signature=create_signature, create_slot=create_slot,
        create_block_time=launch_committed_at,
    )
    if observation_id:
        from src.ops.walkback_observation_attribution import bind
        bind(conn, mint, observation_id, request, request)
    return {"action": state, "request_id": request,
            "cohort_version": cohort["version"], "cohort_high_water": cohort["high_water"]}


def submit_committed_launch(*, mint: str, creator: Optional[str], create_signature: str,
                            create_slot: Optional[int], launch_committed_at: int) -> dict:
    """Use the existing serialized operations lane after the source commit."""
    from src.core.database_write_service import database_write_service
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path = os.environ.get("WT_OPS_DB_PATH", os.path.join(root, "database", "wt_ops_v2.db"))
    selector = f"operations:{os.path.realpath(path)}"
    database_write_service.register_database(selector, path)
    return database_write_service.submit(
        selector, "committed-launch-priority-request",
        lambda conn: bind_committed_launch(
            conn, mint=mint, creator=creator, create_signature=create_signature,
            create_slot=create_slot, launch_committed_at=launch_committed_at,
        ),
    )
