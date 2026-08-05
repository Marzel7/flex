"""X76.2 Phase 6 — Historical backfill of wt_treasury_review_actions.

Reconstructs an immutable audit event for every EXISTING decided
wt_treasury_review row that lacks one, using ONLY evidence already
present:

  - treasury, status, reviewed_by, reviewed_at  (from wt_treasury_review
    itself -- present for all 117 decided rows)
  - action, reviewer, notes, created_at         (from wt_treasury_approval_
    audit, when a matching row exists -- 76 of 117)

Every reconstructed row is marked explicitly (result_json contains
"reconstructed": true and a "source" field naming which table(s) supplied
the evidence) so it can never be mistaken for a live-recorded decision.
Original timestamps (reviewed_at / the older audit table's created_at)
are always preserved -- this script never invents a timestamp.

For the 48 rows with NO corresponding wt_treasury_approval_audit entry,
the reason field cannot be determined with confidence -- per the task's
explicit instruction ("If reconstructed... Never fabricate history"),
this script records that fact honestly (reason =
"Reason not recorded at the time of this decision (pre-dates any audit
trail); reconstructed from wt_treasury_review's own status/reviewed_by/
reviewed_at columns only.") rather than inventing a plausible-sounding
reason.

Idempotent: skips any treasury that already has a wt_treasury_review_actions
row (whether from this backfill or from a live write since X76.2 shipped).
Safe to re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
import sqlite3
import uuid

from src.core.db import OPS_DB_PATH

_ACTION_FOR_STATUS = {
    "CONFIRMED": "APPROVE_TREASURY",
    "APPROVED": "APPROVE_TREASURY",
    "REJECTED": "REJECT_TREASURY",
}

_NO_REASON_RECORDED = (
    "Reason not recorded at the time of this decision (pre-dates any audit "
    "trail); reconstructed from wt_treasury_review's own status/reviewed_by/"
    "reviewed_at columns only."
)


def backfill(conn: sqlite3.Connection) -> dict:
    before = conn.execute("SELECT COUNT(*) FROM wt_treasury_review_actions").fetchone()[0]

    decided = conn.execute(
        "SELECT treasury, status, reviewed_by, reviewed_at FROM wt_treasury_review "
        "WHERE status IN ('CONFIRMED','APPROVED','REJECTED') AND reviewed_at IS NOT NULL"
    ).fetchall()

    reconstructed_with_old_audit = 0
    reconstructed_without_old_audit = 0
    already_present = 0
    skipped_no_action_mapping = 0

    for row in decided:
        treasury, status, reviewed_by, reviewed_at = row
        action = _ACTION_FOR_STATUS.get(status)
        if not action:
            skipped_no_action_mapping += 1
            continue

        existing = conn.execute(
            "SELECT 1 FROM wt_treasury_review_actions WHERE treasury=? AND action=?",
            (treasury, action),
        ).fetchone()
        if existing:
            already_present += 1
            continue

        old_audit_action = "APPROVED" if action == "APPROVE_TREASURY" else "REJECTED"
        old_audit = conn.execute(
            "SELECT reviewer, notes, created_at FROM wt_treasury_approval_audit "
            "WHERE treasury=? AND action=? ORDER BY created_at ASC LIMIT 1",
            (treasury, old_audit_action),
        ).fetchone()

        if old_audit:
            reviewer, notes, created_at = old_audit
            reason = notes.strip() if notes and notes.strip() else _NO_REASON_RECORDED
            analyst = reviewer or reviewed_by or "human"
            timestamp = created_at or reviewed_at
            source = "wt_treasury_review + wt_treasury_approval_audit"
            reconstructed_with_old_audit += 1
        else:
            analyst = reviewed_by or "human"
            reason = _NO_REASON_RECORDED
            timestamp = reviewed_at
            source = "wt_treasury_review only (no matching wt_treasury_approval_audit row found)"
            reconstructed_without_old_audit += 1

        action_id = str(uuid.uuid4())
        result = {
            "reconstructed": True,
            "source": source,
            "backfilled_by": "scripts/maintenance/x76_2_backfill_treasury_review_actions.py",
        }
        conn.execute(
            "INSERT INTO wt_treasury_review_actions "
            "(action_id, treasury, action, analyst, reason, evidence_revision, result_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (action_id, treasury, action, analyst, reason, f"backfill:x76_2:{timestamp}",
             json.dumps(result), timestamp),
        )

    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM wt_treasury_review_actions").fetchone()[0]

    return {
        "before": before,
        "after": after,
        "decided_rows_scanned": len(decided),
        "reconstructed_with_old_audit_evidence": reconstructed_with_old_audit,
        "reconstructed_without_old_audit_evidence": reconstructed_without_old_audit,
        "already_present": already_present,
        "skipped_no_action_mapping": skipped_no_action_mapping,
    }


def main() -> None:
    conn = sqlite3.connect(str(OPS_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        result = backfill(conn)
    finally:
        conn.close()
    print(f"[X76.2 backfill] wt_treasury_review_actions before={result['before']} after={result['after']}")
    print(f"[X76.2 backfill] decided rows scanned: {result['decided_rows_scanned']}")
    print(f"[X76.2 backfill] reconstructed with old-audit-table reason: {result['reconstructed_with_old_audit_evidence']}")
    print(f"[X76.2 backfill] reconstructed WITHOUT old-audit-table reason (honest placeholder used): {result['reconstructed_without_old_audit_evidence']}")
    print(f"[X76.2 backfill] already present (idempotent no-op): {result['already_present']}")
    print(f"[X76.2 backfill] skipped (no action mapping for status): {result['skipped_no_action_mapping']}")


if __name__ == "__main__":
    main()
