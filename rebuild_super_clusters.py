#!/usr/bin/env python3
"""
Rebuild super-clusters by reading creator memberships and filtering out infra/cex accounts.

This script:
1. Reads creator_super_cluster_membership table
2. For each cluster, identifies root operators (funders with 2+ creators)
3. Filters out INFRASTRUCTURE_ACCOUNTS and CEX_ACCOUNTS
4. Updates super_clusters table with corrected root operators

Usage:
    python3 rebuild_super_clusters.py
"""

import sqlite3
import sys
from collections import defaultdict
from infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS

DB_PATH = "pumpswap_tokens.db"

def rebuild_super_clusters():
    """Rebuild super-clusters with infra/cex filtering"""

    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    infra_and_cex = set(INFRASTRUCTURE_ACCOUNTS.keys()) | set(CEX_ACCOUNTS.keys())

    print(f"Filtering out {len(infra_and_cex)} infra/cex accounts\n")

    # Get all cluster memberships
    cursor.execute("""
        SELECT DISTINCT super_cluster_id, creator_address
        FROM creator_super_cluster_membership
        ORDER BY super_cluster_id
    """)

    memberships = cursor.fetchall()
    print(f"Total creator memberships: {len(memberships)}\n")

    # Group creators by cluster
    clusters = defaultdict(set)
    for row in memberships:
        clusters[row['super_cluster_id']].add(row['creator_address'])

    print(f"Total clusters: {len(clusters)}\n")

    # For each cluster, find root operators
    updated_count = 0

    for cluster_id in sorted(clusters.keys()):
        creators = list(clusters[cluster_id])

        # Get funders for these creators
        placeholders = ','.join(['?'] * len(creators))
        cursor.execute(f"""
            SELECT
                funder_address,
                COUNT(DISTINCT creator_address) as creators_funded,
                SUM(amount_sol) as total_sol_sent
            FROM creator_funders
            WHERE creator_address IN ({placeholders})
            GROUP BY funder_address
            ORDER BY total_sol_sent DESC
        """, creators)

        funders = cursor.fetchall()

        # Filter: only root operators (2+ creators) and not infra/cex
        root_ops = [
            f['funder_address']
            for f in funders
            if f['creators_funded'] > 1 and f['funder_address'] not in infra_and_cex
        ]

        root_ops_str = ','.join(root_ops) if root_ops else ''

        # Get old root operators for comparison
        cursor.execute("SELECT root_addresses FROM super_clusters WHERE super_cluster_id = ?", (cluster_id,))
        old_row = cursor.fetchone()
        old_root_ops = set(old_row['root_addresses'].split(',')) if old_row and old_row['root_addresses'] else set()
        new_root_ops = set(root_ops)

        # Check if changed
        if old_root_ops != new_root_ops:
            cursor.execute(
                "UPDATE super_clusters SET root_addresses = ? WHERE super_cluster_id = ?",
                (root_ops_str, cluster_id)
            )
            updated_count += 1

            removed = old_root_ops - new_root_ops
            added = new_root_ops - old_root_ops

            print(f"✓ {cluster_id}")
            print(f"  Creators: {len(creators)}")
            if removed:
                print(f"  Removed (infra): {', '.join(r[:16] + '...' for r in removed)}")
            if added:
                print(f"  Added: {', '.join(a[:16] + '...' for a in added)}")
            if not removed and not added:
                print(f"  Root ops: {len(root_ops)}")
            print()

    conn.commit()
    conn.close()

    print(f"\n✅ Updated {updated_count} clusters")

if __name__ == "__main__":
    rebuild_super_clusters()
