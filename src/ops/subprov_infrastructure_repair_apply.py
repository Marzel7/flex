"""X26.3.1 Phase 2 — apply the historical reclassification identified by
src.ops.subprov_infrastructure_repair_dryrun.

Updates ONLY (state, rejected_reason) on the exact wallet set the dry-run
report flags, inside a single transaction. Every other column
(creator_count, timestamps, treasury, discovery_source, funding_mechanism,
confidence, etc.) is left untouched. Does not touch wt_subprov_evidence,
wt_walkback_queue, or watchtower_token_attribution — raw evidence and
attribution rows are preserved unconditionally.

This module performs a real write. It is intentionally separate from the
read-only dry-run module so the read-only guarantee of that module is never
put at risk by this one.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

from src.ops.subprov_infrastructure_repair_dryrun import OPS_DB_PATH, build_dry_run_report

NEW_STATE = "REJECTED_INFRASTRUCTURE"
NEW_REASON = "KNOWN_INFRASTRUCTURE_REGISTRY_MATCH"


def apply_repair(ops_db_path: str = OPS_DB_PATH) -> dict[str, Any]:
    report = build_dry_run_report(ops_db_path)
    targets = [r["wallet"] for r in report["rows"] if not r["already_rejected"]]

    conn = sqlite3.connect(ops_db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        before_rows = {
            r["subprov"]: dict(r) for r in conn.execute("SELECT * FROM wt_discovered_subprovs")
        }

        conn.execute("BEGIN IMMEDIATE")
        changed = 0
        for wallet in targets:
            cur = conn.execute(
                "UPDATE wt_discovered_subprovs SET state=?, rejected_reason=? "
                "WHERE subprov=? AND COALESCE(state,'') NOT LIKE 'REJECTED%'",
                (NEW_STATE, NEW_REASON, wallet),
            )
            changed += cur.rowcount
        conn.commit()

        after_rows = {
            r["subprov"]: dict(r) for r in conn.execute("SELECT * FROM wt_discovered_subprovs")
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Diff every row, not just the targets, to prove nothing else moved.
    unexpected_changes = []
    expected_changed = set(targets)
    for wallet, after in after_rows.items():
        before = before_rows.get(wallet)
        if before is None:
            unexpected_changes.append({"wallet": wallet, "issue": "row appeared"})
            continue
        if before == after:
            continue
        if wallet not in expected_changed:
            unexpected_changes.append({"wallet": wallet, "before": before, "after": after})
            continue
        diff_keys = {k for k in after if after[k] != before.get(k)}
        if diff_keys - {"state", "rejected_reason"}:
            unexpected_changes.append({
                "wallet": wallet,
                "issue": "unexpected column changed",
                "diff_keys": sorted(diff_keys),
            })

    return {
        "targets": targets,
        "rows_changed": changed,
        "unexpected_changes": unexpected_changes,
        "before_rows": {w: before_rows[w] for w in targets},
        "after_rows": {w: after_rows[w] for w in targets},
    }


if __name__ == "__main__":
    import json
    result = apply_repair()
    print(json.dumps({
        "targets_count": len(result["targets"]),
        "rows_changed": result["rows_changed"],
        "unexpected_changes": result["unexpected_changes"],
    }, indent=2, default=str))
