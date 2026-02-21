#!/usr/bin/env python3
"""
ops_dashboard.py — Cross-Funding Cluster Ops Dashboard (SQLite)

Prints a small, fast ops/monitoring dashboard from your pumpswap_tokens.db.

Highlights:
- Correct (non-inflated) cluster volume aggregation
- Top clusters (size + volume)
- FUNDERS_1 creator watchlist size + optional sample
- Recipient hub top list
- Unified cluster risk summary (if table exists)
- Sanity checks (inflated vs correct sums, cluster count drift)

Usage:
  python ops_dashboard.py --db pumpswap_tokens.db
  python ops_dashboard.py --db pumpswap_tokens.db --top 15
  python ops_dashboard.py --db pumpswap_tokens.db --creator HYWo71Wk...   # lookup creator cluster
  python ops_dashboard.py --db pumpswap_tokens.db --show-funders1-creators 20
"""

import argparse
import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_DB = "pumpswap_tokens.db"


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,))
    return cur.fetchone() is not None


def json_each_supported(conn: sqlite3.Connection) -> bool:
    """
    Returns True if JSON1 functions are available (json_each).
    If not available, we can still do Python-side parsing for many things.
    """
    cur = conn.cursor()
    try:
        cur.execute("SELECT value FROM json_each('[\"x\"]') LIMIT 1;")
        row = cur.fetchone()
        return bool(row)
    except Exception:
        return False


def ensure_view(conn: sqlite3.Connection) -> None:
    """
    Creates a view that safely summarizes funder clusters without the inflation trap.
    """
    cur = conn.cursor()
    cur.execute("""
        CREATE VIEW IF NOT EXISTS v_funder_clusters_summary AS
        SELECT
          cluster_id,
          COUNT(*) AS funders,
          MAX(network_size) AS network_size,
          MAX(total_volume_sol) AS cluster_volume_sol
        FROM funder_networks
        WHERE cluster_id IS NOT NULL
        GROUP BY cluster_id;
    """)
    conn.commit()


def fmt_num(x: Any, decimals: int = 2) -> str:
    try:
        v = float(x)
        return f"{v:,.{decimals}f}"
    except Exception:
        return str(x)


def print_section(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def fetch_all(conn: sqlite3.Connection, sql: str, params: Tuple = ()) -> List[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchall()


def fetch_one(conn: sqlite3.Connection, sql: str, params: Tuple = ()) -> Optional[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute(sql, params)
    return cur.fetchone()


# -----------------------------
# Dashboard panels
# -----------------------------

def panel_top_clusters(conn: sqlite3.Connection, top_n: int) -> None:
    rows = fetch_all(conn, """
        SELECT cluster_id, funders, cluster_volume_sol
        FROM v_funder_clusters_summary
        ORDER BY funders DESC, cluster_volume_sol DESC
        LIMIT ?;
    """, (top_n,))
    if not rows:
        print("No clusters found in v_funder_clusters_summary.")
        return

    print(f"{'cluster_id':<12} {'funders':>8} {'cluster_volume_sol':>20}")
    print("-" * 44)
    for r in rows:
        print(f"{r['cluster_id']:<12} {int(r['funders']):>8} {fmt_num(r['cluster_volume_sol']):>20}")


def panel_cluster_totals(conn: sqlite3.Connection) -> Dict[str, float]:
    # Correct total
    r1 = fetch_one(conn, "SELECT SUM(cluster_volume_sol) AS total FROM v_funder_clusters_summary;")
    correct_total = float(r1["total"] or 0.0) if r1 else 0.0

    # Inflated sum (for sanity)
    r2 = fetch_one(conn, "SELECT SUM(total_volume_sol) AS inflated FROM funder_networks WHERE cluster_id IS NOT NULL;")
    inflated_total = float(r2["inflated"] or 0.0) if r2 else 0.0

    # Cluster count
    r3 = fetch_one(conn, "SELECT COUNT(*) AS clusters FROM v_funder_clusters_summary;")
    cluster_count = int(r3["clusters"] or 0) if r3 else 0

    print(f"Clusters: {cluster_count}")
    print(f"Total SOL across clusters (correct): {fmt_num(correct_total)}")
    print(f"Total SOL across funder rows (inflated, don't use): {fmt_num(inflated_total)}")

    return {"correct_total": correct_total, "inflated_total": inflated_total, "cluster_count": float(cluster_count)}


def panel_cluster_distribution(conn: sqlite3.Connection) -> None:
    rows = fetch_all(conn, """
        SELECT
          CASE
            WHEN funders >= 50 THEN '50+'
            WHEN funders >= 20 THEN '20-49'
            WHEN funders >= 10 THEN '10-19'
            WHEN funders >= 3  THEN '3-9'
            ELSE '2'
          END AS funder_band,
          COUNT(*) AS clusters
        FROM v_funder_clusters_summary
        GROUP BY funder_band
        ORDER BY
          CASE funder_band
            WHEN '50+' THEN 1
            WHEN '20-49' THEN 2
            WHEN '10-19' THEN 3
            WHEN '3-9' THEN 4
            ELSE 5
          END;
    """)
    if not rows:
        print("No clusters to summarize.")
        return
    for r in rows:
        print(f"{r['funder_band']:>6}: {int(r['clusters'])} clusters")


def panel_funders1_creators(conn: sqlite3.Connection, show_n: int = 0) -> None:
    # Prefer SQL json_each if available; otherwise fall back to Python parsing.
    if json_each_supported(conn):
        r = fetch_one(conn, """
            SELECT COUNT(DISTINCT json_each.value) AS creators
            FROM funder_networks fn, json_each(fn.creators_served)
            WHERE fn.cluster_id = 'FUNDERS_1';
        """)
        creators_cnt = int(r["creators"] or 0) if r else 0
        print(f"FUNDERS_1 creators (distinct): {creators_cnt}")

        if show_n and show_n > 0:
            rows = fetch_all(conn, """
                SELECT DISTINCT json_each.value AS creator
                FROM funder_networks fn, json_each(fn.creators_served)
                WHERE fn.cluster_id = 'FUNDERS_1'
                ORDER BY creator
                LIMIT ?;
            """, (show_n,))
            print(f"Sample creators (first {show_n}):")
            for rr in rows:
                print(f"  {rr['creator']}")
    else:
        # Python fallback: parse creators_served JSON from all FUNDERS_1 rows
        rows = fetch_all(conn, """
            SELECT creators_served
            FROM funder_networks
            WHERE cluster_id = 'FUNDERS_1';
        """)
        creators: set = set()
        for row in rows:
            try:
                arr = json.loads(row["creators_served"] or "[]")
                for c in arr:
                    creators.add(c)
            except Exception:
                pass
        print(f"FUNDERS_1 creators (distinct): {len(creators)}")
        if show_n and show_n > 0:
            print(f"Sample creators (first {show_n}):")
            for c in sorted(creators)[:show_n]:
                print(f"  {c}")


def panel_recipient_hubs(conn: sqlite3.Connection, top_n: int) -> None:
    if not table_exists(conn, "network_coordinators"):
        print("network_coordinators table not found.")
        return

    rows = fetch_all(conn, """
        SELECT coordinator_address, creator_count, total_sol_moved, network_confidence, is_cex
        FROM network_coordinators
        ORDER BY creator_count DESC, total_sol_moved DESC
        LIMIT ?;
    """, (top_n,))
    if not rows:
        print("No recipient hubs found.")
        return

    print(f"{'coordinator':<46} {'creators':>7} {'sol':>12} {'conf':>7} {'cex':>5}")
    print("-" * 84)
    for r in rows:
        addr = r["coordinator_address"] or ""
        addr = addr[:43] + "..." if len(addr) > 46 else addr
        print(
            f"{addr:<46} {int(r['creator_count'] or 0):>7} {fmt_num(r['total_sol_moved']):>12} "
            f"{(r['network_confidence'] or ''):>7} {int(r['is_cex'] or 0):>5}"
        )


def panel_unified_clusters(conn: sqlite3.Connection, top_n: int) -> None:
    if not table_exists(conn, "unified_creator_clusters"):
        print("unified_creator_clusters table not found (optional panel).")
        return

    rows = fetch_all(conn, """
        SELECT target_creator, risk_level, score, updated_at
        FROM unified_creator_clusters
        ORDER BY score DESC
        LIMIT ?;
    """, (top_n,))
    if not rows:
        print("No unified_creator_clusters rows found.")
        return

    print(f"{'creator':<46} {'risk':>9} {'score':>8} {'updated_at':>22}")
    print("-" * 90)
    for r in rows:
        addr = r["target_creator"] or ""
        addr = addr[:43] + "..." if len(addr) > 46 else addr
        print(f"{addr:<46} {(r['risk_level'] or ''):>9} {fmt_num(r['score'], 2):>8} {(r['updated_at'] or ''):>22}")

    rows2 = fetch_all(conn, """
        SELECT risk_level, COUNT(*) AS creators
        FROM unified_creator_clusters
        GROUP BY risk_level
        ORDER BY creators DESC;
    """)
    print("\nRisk distribution:")
    for r in rows2:
        print(f"  {r['risk_level']}: {int(r['creators'])}")


def lookup_creator_cluster(conn: sqlite3.Connection, creator: str) -> None:
    """
    Find cluster_id for a creator using either json_each (SQL) or Python scan fallback.
    """
    if not table_exists(conn, "funder_networks"):
        print("funder_networks table not found.")
        return

    if json_each_supported(conn):
        row = fetch_one(conn, """
            SELECT cluster_id, network_size, total_volume_sol
            FROM funder_networks fn
            WHERE fn.cluster_id IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM json_each(fn.creators_served)
                WHERE json_each.value = ?
              )
            LIMIT 1;
        """, (creator,))
        if not row:
            print(f"Creator not found in any cluster: {creator}")
            return
        print(f"Creator {creator} is in cluster: {row['cluster_id']}")
        print(f"  network_size: {int(row['network_size'] or 0)}")
        print(f"  cluster_volume_sol: {fmt_num(row['total_volume_sol'])}")
        return

    # Python fallback scan
    rows = fetch_all(conn, """
        SELECT cluster_id, network_size, total_volume_sol, creators_served
        FROM funder_networks
        WHERE cluster_id IS NOT NULL;
    """)
    for r in rows:
        try:
            creators = json.loads(r["creators_served"] or "[]")
            if creator in creators:
                print(f"Creator {creator} is in cluster: {r['cluster_id']}")
                print(f"  network_size: {int(r['network_size'] or 0)}")
                print(f"  cluster_volume_sol: {fmt_num(r['total_volume_sol'])}")
                return
        except Exception:
            continue

    print(f"Creator not found in any cluster: {creator}")


# -----------------------------
# Alerts / thresholds
# -----------------------------

def alert_checks(conn: sqlite3.Connection, expected_clusters: Optional[int]) -> None:
    """
    Minimal alerting:
    - cluster count changed unexpectedly
    - FUNDERS_1 disappeared
    - inflated sum equals correct sum (would indicate schema/data issue)
    """
    print_section("ALERT CHECKS")

    # cluster count
    row = fetch_one(conn, "SELECT COUNT(*) AS clusters FROM v_funder_clusters_summary;")
    clusters = int(row["clusters"] or 0) if row else 0
    if expected_clusters is not None and clusters != expected_clusters:
        print(f"⚠️  ALERT: cluster count changed (expected {expected_clusters}, got {clusters})")
    else:
        print(f"✅ cluster count: {clusters}")

    # FUNDERS_1 present?
    row = fetch_one(conn, "SELECT 1 AS ok FROM v_funder_clusters_summary WHERE cluster_id='FUNDERS_1' LIMIT 1;")
    if row is None:
        print("⚠️  ALERT: FUNDERS_1 not present")
    else:
        print("✅ FUNDERS_1 present")

    # inflation sanity
    row = fetch_one(conn, """
        SELECT
          (SELECT SUM(total_volume_sol) FROM funder_networks WHERE cluster_id IS NOT NULL) AS inflated,
          (SELECT SUM(cluster_volume_sol) FROM v_funder_clusters_summary) AS correct
    """)
    inflated = float(row["inflated"] or 0.0) if row else 0.0
    correct = float(row["correct"] or 0.0) if row else 0.0
    if abs(inflated - correct) < 1e-6 and correct > 0:
        print("⚠️  ALERT: inflated sum equals correct sum (unexpected; check data/model)")
    else:
        print(f"✅ inflation check OK (inflated={fmt_num(inflated)} vs correct={fmt_num(correct)})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB, help="Path to SQLite DB (default: pumpswap_tokens.db)")
    ap.add_argument("--top", type=int, default=10, help="Top N rows for tables (default: 10)")
    ap.add_argument("--creator", default=None, help="Lookup: find cluster for this creator address")
    ap.add_argument("--show-funders1-creators", type=int, default=0,
                    help="Show first N creators in FUNDERS_1 (default: 0 = don't list)")
    ap.add_argument("--expected-clusters", type=int, default=None,
                    help="Alert if cluster count != expected value (e.g. 9)")
    args = ap.parse_args()

    conn = connect(args.db)

    if not table_exists(conn, "funder_networks"):
        print("ERROR: funder_networks table not found. Point --db to the correct database.")
        conn.close()
        raise SystemExit(2)

    ensure_view(conn)

    print_section("OPS DASHBOARD")
    print(f"DB: {args.db}")
    print(f"Generated: {datetime.utcnow().isoformat()}Z")
    print(f"JSON1 available (json_each): {'YES' if json_each_supported(conn) else 'NO (Python fallback)'}")

    if args.creator:
        print_section("CREATOR LOOKUP")
        lookup_creator_cluster(conn, args.creator)

    print_section("CLUSTER TOTALS")
    panel_cluster_totals(conn)

    print_section("TOP CLUSTERS")
    panel_top_clusters(conn, args.top)

    print_section("CLUSTER DISTRIBUTION")
    panel_cluster_distribution(conn)

    print_section("FUNDERS_1 CREATORS")
    panel_funders1_creators(conn, show_n=args.show_funders1_creators)

    print_section("RECIPIENT HUBS (TOP)")
    panel_recipient_hubs(conn, args.top)

    print_section("UNIFIED CREATOR CLUSTERS (OPTIONAL)")
    panel_unified_clusters(conn, args.top)

    alert_checks(conn, expected_clusters=args.expected_clusters)

    conn.close()


if __name__ == "__main__":
    main()
