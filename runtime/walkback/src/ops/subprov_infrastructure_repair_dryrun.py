"""X26.3 Phase 9 — dry-run repair report for wt_discovered_subprovs rows that
match the known-infrastructure registry (INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS,
CUSTOM_ACCOUNTS via src.utils.infra_mapping.is_known_account).

READ-ONLY. Performs no writes. Reports every affected row and every
downstream table that references it, so a human can review before any
actual reclassification is approved and executed separately.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OPS_DB_PATH = os.environ.get("OPS_V2_DB_PATH", os.path.join(_REPO_ROOT, "database", "wt_ops_v2.db"))

# Downstream tables to check for references to an affected subprov wallet,
# as (table, column) pairs. Read-only existence probing only.
_DOWNSTREAM_REFS = (
    ("wt_wrap_close_candidates", "subprov_wallet"),
    ("wt_active_subprov_sessions", "subprov_wallet"),
    ("wt_candidate_websocket_watches", "subprov_wallet"),
    ("wt_provisioning_sessions", "subprov"),
    ("wt_subprov_evidence", "subprov"),
    ("watchtower_token_attribution", "matched_subprov"),
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())


def build_dry_run_report(ops_db_path: str = OPS_DB_PATH) -> dict[str, Any]:
    """Read-only. Returns a report of every wt_discovered_subprovs row that
    matches the known-infrastructure registry, with its current state,
    discovery source, creator count, and every downstream table that
    references it. Performs no writes."""
    if not os.path.exists(ops_db_path):
        return {"rows": [], "total_affected": 0}
    from src.utils.infra_mapping import is_known_account

    conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        all_rows = conn.execute("SELECT * FROM wt_discovered_subprovs").fetchall()
        affected = []
        for r in all_rows:
            wallet = r["subprov"]
            if not is_known_account(wallet):
                continue
            downstream_refs = {}
            for table, col in _DOWNSTREAM_REFS:
                if not _table_exists(conn, table):
                    continue
                try:
                    n = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {col}=?", (wallet,)
                    ).fetchone()[0]
                except sqlite3.Error:
                    n = 0
                if n:
                    downstream_refs[table] = n
            already_rejected = str(r["state"] or "").startswith("REJECTED")
            affected.append({
                "wallet": wallet,
                "current_state": r["state"],
                "discovery_source": r["discovery_source"],
                "creator_count": r["creator_count"],
                "treasury": r["treasury"],
                "wrap_close_count": r["wrap_close_count"],
                "seeded_account_count": r["seeded_account_count"],
                "rejected_reason": r["rejected_reason"],
                "already_rejected": already_rejected,
                "proposed_new_state": r["state"] if already_rejected else "REJECTED_INFRASTRUCTURE",
                "proposed_rejected_reason": r["rejected_reason"] or "known infrastructure wallet",
                "downstream_tables_referencing": downstream_refs,
            })
        return {
            "rows": affected,
            "total_affected": len(affected),
            "total_scanned": len(all_rows),
            "already_rejected_count": sum(1 for a in affected if a["already_rejected"]),
            "would_change_count": sum(1 for a in affected if not a["already_rejected"]),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    print(json.dumps(build_dry_run_report(), indent=2, default=str))
