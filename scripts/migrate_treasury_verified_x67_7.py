"""X67.7 — Migrate legacy TREASURY_VERIFIED candidates to direct Canonical
WATCHTOWER promotion.

Runs EVERY current TREASURY_VERIFIED row through the exact same evaluator
(`evaluate_candidate_for_canonical_promotion` / `promote_eligible_candidate`)
new candidates use -- no per-mint special-casing, per the task's explicit
constraint. Dry-run by default; --apply performs the actual write via the
existing canonical registry writer.

Usage:
    python3 scripts/migrate_treasury_verified_x67_7.py            # dry run
    python3 scripts/migrate_treasury_verified_x67_7.py --apply    # writes
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.ops.provisioning_candidates_workflow import (
    ensure_schema, evaluate_candidate_for_canonical_promotion, promote_eligible_candidate,
    is_confirmed_in_model1,
)
import json

OPS_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database", "wt_ops_v2.db")


def run(apply: bool) -> None:
    conn = sqlite3.connect(OPS_DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    now = int(time.time())

    rows = conn.execute(
        "SELECT * FROM wt_provisioning_candidate_workflow WHERE workflow_state='TREASURY_VERIFIED'"
    ).fetchall()
    print(f"Found {len(rows)} legacy TREASURY_VERIFIED row(s)")

    for r in rows:
        row = dict(r)
        mint = row["mint"]
        evidence = {}
        if row.get("evidence_json"):
            try:
                evidence = json.loads(row["evidence_json"])
            except (TypeError, ValueError):
                evidence = {}
        treasury = row.get("verified_treasury") or evidence.get("treasury")
        subprov = row.get("subprov_wallet")
        gap = row.get("lineage_gap_seconds")
        wrap_close_sig = evidence.get("wrap_close_signature")

        print(f"\n=== {mint} ===")
        print(f"  already_canonical: {is_confirmed_in_model1(conn, mint)}")
        print(f"  treasury: {treasury}")
        print(f"  subprov: {subprov}")
        print(f"  lineage_gap_seconds: {gap}")
        print(f"  wrap_close_signature: {wrap_close_sig}")

        decision = evaluate_candidate_for_canonical_promotion(
            conn, mint=mint, treasury=treasury, subprov_wallet=subprov,
            wrap_close_signature=wrap_close_sig, lineage_gap_seconds=gap,
            verification_evidence=evidence,
        )
        print(f"  eligible: {decision['eligible']}")
        print(f"  reason_code: {decision['reason_code']}")
        print(f"  missing_requirements: {decision['missing_requirements']}")
        print(f"  conflicting_evidence: {decision['conflicting_evidence']}")

        if not apply:
            expected_action = "promoted" if decision["eligible"] else "not_eligible"
            print(f"  DRY-RUN expected action: {expected_action}")
            continue

        result = promote_eligible_candidate(
            conn, mint=mint, treasury=treasury, subprov_wallet=subprov,
            wrap_close_signature=wrap_close_sig, lineage_gap_seconds=gap,
            verification_evidence=evidence, now=now,
        )
        print(f"  APPLY result: {result}")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry-run)")
    args = parser.parse_args()
    run(apply=args.apply)
