#!/usr/bin/env python3
"""
Rebuild creator reuse metrics for super-clusters.

This script calculates:
- creators_in_multiple_clusters: How many creators in THIS cluster also appear in other clusters
- creator_reuse_ratio_across_clusters: The percentage of creators that are reused
- creator_reuse_level: HIGH, MEDIUM, LOW, or NONE based on reuse metrics
- creator_reuse_tag: Human-readable tag (STRONG, SHARED, WEAK, INDEPENDENT_CREATORS)
"""

import sqlite3
from collections import defaultdict

DB_PATH = "pumpswap_tokens.db"

def rebuild_creator_reuse():
    """Rebuild creator reuse metrics for all super-clusters"""

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get all creator-cluster memberships
    cursor.execute("""
        SELECT creator_address, super_cluster_id
        FROM creator_super_cluster_membership
        ORDER BY creator_address
    """)

    memberships = cursor.fetchall()

    # Build a mapping of creator -> list of clusters
    creator_clusters = defaultdict(set)
    for row in memberships:
        creator_clusters[row['creator_address']].add(row['super_cluster_id'])

    # For each cluster, count how many creators are in multiple clusters
    cluster_reuse_data = defaultdict(lambda: {'creators': set(), 'reused_creators': set()})

    for creator, clusters in creator_clusters.items():
        for cluster in clusters:
            cluster_reuse_data[cluster]['creators'].add(creator)

            # If creator is in multiple clusters, mark as reused
            if len(clusters) > 1:
                cluster_reuse_data[cluster]['reused_creators'].add(creator)

    # Update super_clusters table with reuse metrics
    updated_count = 0
    for cluster_id, reuse_info in cluster_reuse_data.items():
        creators_unique = len(reuse_info['creators'])
        creators_in_multiple = len(reuse_info['reused_creators'])

        # Calculate ratio
        reuse_ratio = creators_in_multiple / creators_unique if creators_unique > 0 else 0

        # Determine reuse level based on thresholds
        if creators_unique >= 10 and creators_in_multiple >= 5 and reuse_ratio >= 0.5:
            reuse_level = 'HIGH'
            reuse_tag = 'STRONG'
        elif creators_unique >= 5 and creators_in_multiple >= 2 and reuse_ratio >= 0.3:
            reuse_level = 'MEDIUM'
            reuse_tag = 'SHARED'
        elif creators_in_multiple >= 1:
            reuse_level = 'LOW'
            reuse_tag = 'WEAK'
        else:
            reuse_level = 'NONE'
            reuse_tag = 'INDEPENDENT_CREATORS'

        # Update the cluster
        cursor.execute("""
            UPDATE super_clusters
            SET
                creators_in_multiple_clusters = ?,
                creator_reuse_ratio_across_clusters = ?,
                creator_reuse_level = ?,
                creator_reuse_tag = ?
            WHERE super_cluster_id = ?
        """, (creators_in_multiple, reuse_ratio, reuse_level, reuse_tag, cluster_id))

        updated_count += 1

        if creators_in_multiple > 0:
            print(f"✓ {cluster_id}: {creators_unique} creators, {creators_in_multiple} reused ({reuse_ratio:.1%}) → {reuse_tag}")

    conn.commit()
    conn.close()

    print(f"\n✅ Updated {updated_count} clusters")

if __name__ == "__main__":
    rebuild_creator_reuse()
