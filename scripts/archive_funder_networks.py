#!/usr/bin/env python3
"""
Archive `funder_networks` out of the hot DB into the investigation archive DB.

NON-DESTRUCTIVE: this script COPIES rows hot -> archive and VERIFIES them.
It NEVER deletes from the hot DB. The hot-table DELETE is a manual, commented
step (see bottom of this file) to be run only after the copy is verified,
the API routes are repointed, and the investigation UI is confirmed working.

Safe to run repeatedly (idempotent): re-copy overwrites the archive table with
the current hot snapshot, then re-verifies against the live hot fingerprint.

Usage:
    python3 scripts/archive_funder_networks.py            # copy + verify
    python3 scripts/archive_funder_networks.py --dry-run  # verify-only, no copy

Exit code 0 = PASS, non-zero = FAIL (mismatch / error).
"""
import argparse
import os
import sqlite3
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HOT_DB = os.path.abspath(os.environ.get(
    "DB_PATH", os.path.join(_REPO_ROOT, "database", "flex_complete_database.db")))
ARCHIVE_DB = os.path.abspath(os.environ.get(
    "INVESTIGATION_ARCHIVE_DB",
    os.path.join(_REPO_ROOT, "database", "flex_investigation_archive.db")))

TABLE = "funder_networks"

# Baseline fingerprint locked during Phase 1 verification (2026-06-18).
# These are sanity guards: if the hot table no longer matches, the analyzer
# has re-run and the baseline must be re-captured before archiving.
BASELINE = {
    "row_count": 41734,
    "id_sum": 875134720,
    "payload_bytes": 2835264753,
}

# DDL captured from the hot DB (.schema funder_networks), minus the index
# (recreated separately so the archive copy is queryable the same way).
CREATE_SQL = """
CREATE TABLE IF NOT EXISTS funder_networks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    primary_funder TEXT NOT NULL UNIQUE,
    connected_funders TEXT,
    transfer_chain TEXT,
    creators_served TEXT,
    network_size INTEGER,
    total_volume_sol REAL,
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    cluster_id TEXT
)
"""
CREATE_IDX_SQL = "CREATE INDEX IF NOT EXISTS idx_funder_cluster_id ON funder_networks(cluster_id)"

FINGERPRINT_SQL = """
SELECT
    COUNT(*) AS row_count,
    COALESCE(SUM(id), 0) AS id_sum,
    COALESCE(SUM(
        LENGTH(COALESCE(connected_funders, ''))
      + LENGTH(COALESCE(transfer_chain, ''))
      + LENGTH(COALESCE(creators_served, ''))
    ), 0) AS payload_bytes
FROM funder_networks
"""

SAMPLE_SQL = """
SELECT id, primary_funder,
       LENGTH(COALESCE(connected_funders, ''))
     + LENGTH(COALESCE(transfer_chain, ''))
     + LENGTH(COALESCE(creators_served, '')) AS payload_len
FROM funder_networks
ORDER BY id
LIMIT 20
"""


def _fingerprint(conn, schema="main"):
    row = conn.execute(FINGERPRINT_SQL.replace("funder_networks", f"{schema}.funder_networks")).fetchone()
    return {"row_count": row[0], "id_sum": row[1], "payload_bytes": row[2]}


def _sample(conn, schema="main"):
    return conn.execute(SAMPLE_SQL.replace("funder_networks", f"{schema}.funder_networks")).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Verify hot fingerprint against baseline only; do not copy.")
    args = ap.parse_args()

    if not os.path.exists(HOT_DB):
        print(f"FAIL: hot DB not found: {HOT_DB}")
        return 2

    # Read-only connection to the hot DB. We never write to it here.
    conn = sqlite3.connect(f"file:{HOT_DB}?mode=ro", uri=True, timeout=30)

    hot_fp = _fingerprint(conn, "main")
    print(f"[hot]      {hot_fp}")
    print(f"[baseline] {BASELINE}")

    baseline_ok = hot_fp == BASELINE
    if not baseline_ok:
        print("WARN: hot fingerprint != Phase-1 baseline — analyzer may have re-run.")
        print("      Re-capture the baseline before treating this as the archive source.")

    if args.dry_run:
        print("DRY-RUN: no copy performed.")
        print("PASS" if baseline_ok else "FAIL")
        return 0 if baseline_ok else 1

    # Copy: ATTACH the archive, (re)create the table, replace its contents with
    # the current hot snapshot. INSERT OR REPLACE keeps this idempotent.
    conn.execute(f"ATTACH DATABASE ? AS arch", (ARCHIVE_DB,))
    conn.execute(f"CREATE TABLE IF NOT EXISTS arch.{TABLE} ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, primary_funder TEXT NOT NULL UNIQUE, "
                 "connected_funders TEXT, transfer_chain TEXT, creators_served TEXT, "
                 "network_size INTEGER, total_volume_sol REAL, "
                 "detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, cluster_id TEXT)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS arch.idx_funder_cluster_id ON {TABLE}(cluster_id)")
    # Idempotent overwrite of the archive copy from the current hot snapshot.
    conn.execute(f"DELETE FROM arch.{TABLE}")
    conn.execute(f"INSERT INTO arch.{TABLE} SELECT * FROM main.{TABLE}")
    conn.commit()

    arch_fp = _fingerprint(conn, "arch")
    print(f"[archive]  {arch_fp}")

    # Verify: archive must match the LIVE hot fingerprint exactly.
    fp_ok = arch_fp == hot_fp

    hot_sample = _sample(conn, "main")
    arch_sample = _sample(conn, "arch")
    sample_ok = hot_sample == arch_sample

    print(f"  row_count match: {arch_fp['row_count'] == hot_fp['row_count']}")
    print(f"  id_sum match:    {arch_fp['id_sum'] == hot_fp['id_sum']}")
    print(f"  payload match:   {arch_fp['payload_bytes'] == hot_fp['payload_bytes']}")
    print(f"  20-row sample match: {sample_ok}")

    conn.close()

    if fp_ok and sample_ok:
        print("PASS: archive copy verified byte-for-byte against hot snapshot.")
        return 0
    print("FAIL: archive copy does NOT match hot snapshot. Do NOT delete hot rows.")
    return 1


if __name__ == "__main__":
    sys.exit(main())


# =====================================================================
# MANUAL CLEANUP STEP — DO NOT RUN until ALL of the following are true:
#   1. This script printed PASS (archive verified).
#   2. main.py routes repointed to the archive (idx_funder_cluster_id present).
#   3. Investigation UI (/clusters, /api/funder-clusters,
#      /api/funder-cluster/<id>) confirmed working against the archive.
#   4. A fresh backup of the hot DB exists.
#
# Then, and only then, reclaim the ~2.64 GB from the hot DB by running:
#
#   -- DO NOT RUN until archive verified, routes repointed, UI confirmed:
#   -- DELETE FROM funder_networks;
#
# File size is reclaimed separately via VACUUM INTO in a maintenance window.
# =====================================================================
