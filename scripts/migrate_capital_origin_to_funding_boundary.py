"""X29.3 migration: rename wt_capital_origin -> wt_funding_boundary in place.

Renames the table and every origin_* column to boundary_*, moving the
existing 529 rows exactly as they are (no re-derivation, no new RPC).
Safe to re-run: no-ops if wt_capital_origin no longer exists and
wt_funding_boundary already has rows.

Usage:
    python3 scripts/migrate_capital_origin_to_funding_boundary.py [ops_db_path]
"""
from __future__ import annotations

import sqlite3
import sys

DEFAULT_OPS_DB = "database/wt_ops_v2.db"


def migrate(ops_db_path: str) -> dict:
    conn = sqlite3.connect(ops_db_path)
    cur = conn.cursor()

    old_exists = bool(cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_capital_origin'"
    ).fetchone())
    new_exists = bool(cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wt_funding_boundary'"
    ).fetchone())

    if not old_exists:
        conn.close()
        return {"status": "no-op", "reason": "wt_capital_origin does not exist"}

    if new_exists:
        existing_rows = cur.execute("SELECT COUNT(*) FROM wt_funding_boundary").fetchone()[0]
        if existing_rows > 0:
            conn.close()
            return {"status": "no-op", "reason": f"wt_funding_boundary already has {existing_rows} rows"}
        cur.execute("DROP TABLE wt_funding_boundary")

    from src.ops.funding_boundary import DDL
    cur.executescript(DDL)

    old_count = cur.execute("SELECT COUNT(*) FROM wt_capital_origin").fetchone()[0]

    cur.execute(
        """INSERT INTO wt_funding_boundary (
            id, launch_mint, subject_wallet, boundary_status, boundary_type, boundary_wallet,
            boundary_entity, boundary_signature, boundary_block_time, boundary_age_at_launch_seconds,
            boundary_hop_depth, boundary_transfer_lamports, boundary_transfer_sol,
            transactions_inspected, rpc_calls_used, oldest_inspected_signature,
            oldest_inspected_block_time, history_exhausted, pagination_limit_reached,
            resolution_reason, provenance, created_at, updated_at
        )
        SELECT
            id, launch_mint, subject_wallet, origin_status, origin_type, origin_wallet,
            origin_entity, origin_signature, origin_block_time, origin_age_at_launch_seconds,
            origin_hop_depth, origin_transfer_lamports, origin_transfer_sol,
            transactions_inspected, rpc_calls_used, oldest_inspected_signature,
            oldest_inspected_block_time, history_exhausted, pagination_limit_reached,
            resolution_reason, provenance, created_at, updated_at
        FROM wt_capital_origin
        """
    )

    new_count = cur.execute("SELECT COUNT(*) FROM wt_funding_boundary").fetchone()[0]

    if new_count != old_count:
        conn.rollback()
        conn.close()
        return {"status": "error", "reason": f"row count mismatch: old={old_count} new={new_count}, rolled back"}

    cur.execute("DROP TABLE wt_capital_origin")
    conn.commit()
    conn.close()

    return {"status": "ok", "rows_migrated": new_count}


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_OPS_DB
    result = migrate(path)
    print(result)
