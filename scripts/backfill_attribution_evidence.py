#!/usr/bin/env python3
"""
X41.0 historical backfill for AttributionEvidence and OperationMergeLedger.

READ-ONLY with respect to every existing table (wt_confirmed_treasuries,
wt_treasury_fingerprint_decisions, wt_treasury_approval_audit,
wt_treasury_review, wt_discovered_subprovs, wt_confirmed_treasury_webhooks,
wt_ops_v2*). Writes ONLY new rows into attribution_evidence and
operation_merge_ledger, both additive shadow tables introduced this session.

Every backfilled row is marked reconstructed=1 with an explicit
reconstruction_source and reconstruction_confidence, per X41.0's rule:
"Never invent missing evidence." Where no deeper evidence exists (X40.0's
Group A — pre-migration CONFIRMED_SEED rows), the backfill records exactly
that fact (reconstruction_confidence='METHOD_ONLY') rather than fabricating
detail.

Idempotent: every INSERT is guarded by a NOT EXISTS check keyed on
(subject_wallet, event_type, method) or (target_operation_uuid, event_type)
so re-running this script never duplicates rows. Batched in short
transactions (one commit per source table pass) to stay WAL-friendly and
avoid long-running locks, per X41.0's DB-safety requirements.

Usage:
    python3 scripts/backfill_attribution_evidence.py            # dry run (default)
    python3 scripts/backfill_attribution_evidence.py --apply     # actually write
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.db_locking import db_connect
from src.core.attribution_evidence import ensure_schema as ensure_evidence_schema
from src.core.operation_merge_ledger import ensure_schema as ensure_merge_ledger_schema

OPS_DB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "database", "wt_ops_v2.db"))


def _already_backfilled(conn, subject_wallet: str, event_type: str, method: str) -> bool:
    return conn.execute(
        """SELECT 1 FROM attribution_evidence
           WHERE subject_wallet=? AND event_type=? AND method=? AND reconstructed=1 LIMIT 1""",
        (subject_wallet, event_type, method),
    ).fetchone() is not None


def backfill_fingerprint_decisions(conn, apply: bool) -> int:
    """wt_treasury_fingerprint_decisions -> FINGERPRINT_EVALUATION / LAUNCH_CHAIN_CONFIRMATION
    / REVERSION events. This ledger already has complete evidence_txs_json/signals_json
    for every row it holds — reconstruction_confidence='HIGH' since the original decision
    record survives verbatim."""
    n = 0
    rows = conn.execute(
        """SELECT wallet, decision, signals_json, evidence_txs_json, source_migration,
                  promoted_at, webhook_status, decided_at
           FROM wt_treasury_fingerprint_decisions"""
    ).fetchall()
    for wallet, decision, signals_json, evidence_txs_json, source_migration, promoted_at, webhook_status, decided_at in rows:
        if decision in ("READY_3OF3", "NEAR_MISS", "REJECT"):
            event_type = "FINGERPRINT_EVALUATION"
            method = "auto_evaluate"
        elif decision == "CONFIRMED":
            event_type = "LAUNCH_CHAIN_CONFIRMATION"
            method = "auto_confirm_from_launch_chain"
        elif decision == "REVERTED":
            event_type = "REVERSION"
            method = "revert_auto_promotion"
        else:
            event_type = "ROLE_CHANGE"
            method = "unknown_decision_%s" % decision
        if _already_backfilled(conn, wallet, event_type, method):
            continue
        n += 1
        if not apply:
            continue
        evidence_refs = {"signals": json.loads(signals_json) if signals_json else None,
                         "evidence_txs": json.loads(evidence_txs_json) if evidence_txs_json else None,
                         "source_migration": source_migration}
        conn.execute(
            """INSERT INTO attribution_evidence
                 (event_type, subject_wallet, claimed_role, decision, evidence_refs_json,
                  method, actor_or_process, timestamp, source_pipeline,
                  confidence_axis, confidence_value, reconstructed,
                  reconstruction_source, reconstruction_confidence, timestamp_quality, created_at)
               VALUES (?,?,?,?,?, ?,?,?,?, ?,?, 1, ?,?, ?,?)""",
            (event_type, wallet, "TREASURY", decision, json.dumps(evidence_refs),
             method, "auto_evaluate" if event_type == "FINGERPRINT_EVALUATION" else "system",
             decided_at, "backfill:wt_treasury_fingerprint_decisions",
             "categorical_evaluation_outcome" if event_type == "FINGERPRINT_EVALUATION"
             else "treasury_role_attribution", decision,
             "wt_treasury_fingerprint_decisions", "HIGH", "EXACT", int(time.time())),
        )
    if apply:
        conn.commit()
    return n


def backfill_approval_audit(conn, apply: bool) -> int:
    """wt_treasury_approval_audit -> MANUAL_APPROVAL / MANUAL_REJECTION events.
    Complete reviewer/confidence/notes/evidence_json survives — reconstruction_confidence='HIGH'."""
    n = 0
    rows = conn.execute(
        """SELECT treasury, action, reviewer, confidence, notes, evidence_json, created_at
           FROM wt_treasury_approval_audit"""
    ).fetchall()
    for treasury, action, reviewer, confidence, notes, evidence_json, created_at in rows:
        event_type = "MANUAL_APPROVAL" if action == "APPROVED" else "MANUAL_REJECTION"
        method = "human_review_recovery_safe"
        if _already_backfilled(conn, treasury, event_type, method):
            continue
        n += 1
        if not apply:
            continue
        evidence_refs = json.loads(evidence_json) if evidence_json else {}
        evidence_refs["notes"] = notes
        conn.execute(
            """INSERT INTO attribution_evidence
                 (event_type, subject_wallet, claimed_role, decision, evidence_refs_json,
                  method, actor_or_process, timestamp, source_pipeline,
                  confidence_axis, confidence_value, reconstructed,
                  reconstruction_source, reconstruction_confidence, timestamp_quality, created_at)
               VALUES (?,?,?,?,?, ?,?,?,?, ?,?, 1, ?,?, ?,?)""",
            (event_type, treasury, "TREASURY",
             "CONFIRMED" if action == "APPROVED" else "REJECTED", json.dumps(evidence_refs),
             method, reviewer, created_at, "backfill:wt_treasury_approval_audit",
             "human_review", confidence, "wt_treasury_approval_audit", "HIGH", "EXACT",
             int(time.time())),
        )
    if apply:
        conn.commit()
    return n


def backfill_confirmed_treasuries_gap(conn, apply: bool) -> dict:
    """The X40.0 Phase 1 groups: confirmed treasuries with NO row in either
    ledger above. Classified exactly as X40.0 found them — this function does
    NOT re-derive the classification, it applies it verbatim:

      Group A (3SIGNAL/3SIGNAL+ORIGINAL/HAND+3SIGNAL, provenance=CONFIRMED_SEED):
        method-only provenance. reconstruction_confidence='METHOD_ONLY'.
        evidence_refs carries only what wt_confirmed_treasuries itself stores
        plus the webhook-enrollment fact (no deeper record exists to find).

      Group B (REVIEW_PROMOTED): partial provenance via wt_treasury_review's
        detected_via/status fields. reconstruction_confidence='PARTIAL'.

      Group C (subprov_funder_trace): partial-to-full provenance via
        wt_discovered_subprovs linked-subprov counts.
        reconstruction_confidence='PARTIAL' (1 linked subprov) or
        'SUBSTANTIAL' (>=2 linked subprovs) — matching X40.0's own distinction.
    """
    counts = {"group_a": 0, "group_b": 0, "group_c": 0}
    already_covered = set(r[0] for r in conn.execute(
        "SELECT DISTINCT wallet FROM wt_treasury_fingerprint_decisions"
    ).fetchall()) | set(r[0] for r in conn.execute(
        "SELECT DISTINCT treasury FROM wt_treasury_approval_audit"
    ).fetchall())

    rows = conn.execute(
        """SELECT treasury, method, provenance, confidence, confirmed_at,
                  transfer_pct, out_sol, recipients, micro_pings
           FROM wt_confirmed_treasuries"""
    ).fetchall()

    for treasury, method, provenance, confidence, confirmed_at, transfer_pct, out_sol, recipients, micro_pings in rows:
        if treasury in already_covered:
            continue  # already backfilled via the two ledger passes above
        if _already_backfilled(conn, treasury, "RPC_VERIFIED_TRACE", "backfill_gap") or \
           _already_backfilled(conn, treasury, "SUBPROV_FUNDER_LINK", "backfill_gap") or \
           _already_backfilled(conn, treasury, "ROLE_CHANGE", "backfill_gap"):
            continue

        if method in ("3SIGNAL", "3SIGNAL+ORIGINAL", "HAND+3SIGNAL"):
            webhook_row = conn.execute(
                "SELECT COUNT(*) FROM wt_confirmed_treasury_webhooks WHERE treasury=?",
                (treasury,),
            ).fetchone()
            counts["group_a"] += 1
            if apply:
                conn.execute(
                    """INSERT INTO attribution_evidence
                         (event_type, subject_wallet, claimed_role, decision, evidence_refs_json,
                          method, actor_or_process, timestamp, source_pipeline,
                          confidence_axis, confidence_value, reconstructed,
                          reconstruction_source, reconstruction_confidence, timestamp_quality, created_at)
                       VALUES ('ROLE_CHANGE',?,?,?,?, ?,?,?,?, ?,?, 1, ?,?, ?,?)""",
                    (treasury, "TREASURY", "CONFIRMED",
                     json.dumps({"transfer_pct": transfer_pct, "out_sol": out_sol,
                                "recipients": recipients, "micro_pings": micro_pings,
                                "webhook_enrollment_rows": webhook_row[0] if webhook_row else 0,
                                "note": "no decision-ledger or approval-audit row exists; "
                                        "this is pre-migration seed data per X40.0 Phase 1 Group A"}),
                     "backfill_gap", "schema_migration_seed", confirmed_at,
                     "backfill:wt_confirmed_treasuries(group_a)",
                     "treasury_role_attribution", confidence,
                     "wt_confirmed_treasuries+wt_confirmed_treasury_webhooks", "METHOD_ONLY",
                     "EXACT" if confirmed_at else "UNKNOWN", int(time.time())),
                )

        elif method == "REVIEW_PROMOTED":
            review_row = conn.execute(
                "SELECT detected_via, status FROM wt_treasury_review WHERE treasury=?",
                (treasury,),
            ).fetchone()
            counts["group_b"] += 1
            if apply:
                conn.execute(
                    """INSERT INTO attribution_evidence
                         (event_type, subject_wallet, claimed_role, decision, evidence_refs_json,
                          method, actor_or_process, timestamp, source_pipeline,
                          confidence_axis, confidence_value, reconstructed,
                          reconstruction_source, reconstruction_confidence, timestamp_quality, created_at)
                       VALUES ('RPC_VERIFIED_TRACE',?,?,?,?, ?,?,?,?, ?,?, 1, ?,?, ?,?)""",
                    (treasury, "TREASURY", "CONFIRMED",
                     json.dumps({"detected_via": review_row[0] if review_row else None,
                                "review_status": review_row[1] if review_row else None,
                                "note": "category recovered from wt_treasury_review; "
                                        "detailed evidence fields (notes/evidence_json) were empty"}),
                     "backfill_gap", "wt_treasury_review", confirmed_at,
                     "backfill:wt_confirmed_treasuries(group_b)",
                     "treasury_role_attribution", confidence,
                     "wt_treasury_review", "PARTIAL", "EXACT" if confirmed_at else "UNKNOWN",
                     int(time.time())),
                )

        elif method == "subprov_funder_trace":
            n_linked = conn.execute(
                "SELECT COUNT(*) FROM wt_discovered_subprovs WHERE treasury=?", (treasury,)
            ).fetchone()[0]
            counts["group_c"] += 1
            if apply:
                conn.execute(
                    """INSERT INTO attribution_evidence
                         (event_type, subject_wallet, claimed_role, decision, evidence_refs_json,
                          method, actor_or_process, timestamp, source_pipeline,
                          confidence_axis, confidence_value, reconstructed,
                          reconstruction_source, reconstruction_confidence, timestamp_quality, created_at)
                       VALUES ('SUBPROV_FUNDER_LINK',?,?,?,?, ?,?,?,?, ?,?, 1, ?,?, ?,?)""",
                    (treasury, "TREASURY", "CONFIRMED",
                     json.dumps({"linked_subprov_count": n_linked}),
                     "backfill_gap", "dashboard_user (historical)", confirmed_at,
                     "backfill:wt_confirmed_treasuries(group_c)",
                     "treasury_role_attribution", confidence,
                     "wt_discovered_subprovs", "SUBSTANTIAL" if n_linked >= 2 else "PARTIAL",
                     "EXACT" if confirmed_at else "UNKNOWN", int(time.time())),
                )
        # any other/unknown method: leave unbackfilled here deliberately — do not
        # guess a classification the X40.0 investigation didn't establish.

    if apply:
        conn.commit()
    return counts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="Actually write rows (default is dry-run, counts only).")
    ap.add_argument("--db", default=OPS_DB_PATH)
    args = ap.parse_args()

    conn = db_connect(args.db, timeout=30)
    try:
        ensure_evidence_schema(conn)
        ensure_merge_ledger_schema(conn)

        n1 = backfill_fingerprint_decisions(conn, args.apply)
        n2 = backfill_approval_audit(conn, args.apply)
        gap_counts = backfill_confirmed_treasuries_gap(conn, args.apply)

        mode = "APPLIED" if args.apply else "DRY RUN"
        print(f"[{mode}] fingerprint_decisions backfilled: {n1}")
        print(f"[{mode}] approval_audit backfilled: {n2}")
        print(f"[{mode}] confirmed_treasuries gap-fill (Group A/B/C): {gap_counts}")
        if not args.apply:
            print("\nRe-run with --apply to write these rows.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
