#!/usr/bin/env python3
"""
Cluster risk checking — lookup creator in wallet clusters to assess risk.

Uses wallet_cluster_nodes table to determine if a creator is part of a known
cluster (coordinated funding group) and returns risk information.
"""

import sqlite3
import os

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")


def check_creator(creator_address: str) -> dict:
    """
    Check if a creator is part of a cluster (coordinated funding group).

    Args:
        creator_address: Wallet address to check

    Returns:
        {
            'in_cluster': bool,
            'cluster_id': str or None,
            'cluster_name': str or None,
            'risk_multiplier': float (1.0 = no additional risk),
            'cluster_size': int,
            'network_depth': int
        }
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Look up in wallet_cluster_nodes
        cursor.execute("""
            SELECT root_creator, cluster_id, cluster_name, network_depth
            FROM wallet_cluster_nodes
            WHERE wallet = ?
            LIMIT 1
        """, (creator_address,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            # Not in any cluster
            return {
                'in_cluster': False,
                'cluster_id': None,
                'cluster_name': None,
                'risk_multiplier': 1.0,
                'cluster_size': 0,
                'network_depth': 0
            }

        root_creator, cluster_id, cluster_name, network_depth = row

        # Count cluster size
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(DISTINCT wallet)
            FROM wallet_cluster_nodes
            WHERE root_creator = ?
        """, (root_creator,))

        cluster_size = cursor.fetchone()[0]
        conn.close()

        # Risk multiplier based on cluster characteristics
        # Larger clusters = higher risk (more coordinated)
        risk_multiplier = min(1.0 + (cluster_size / 100), 3.0)

        return {
            'in_cluster': True,
            'cluster_id': cluster_id or root_creator,
            'cluster_name': cluster_name or f"Cluster_{root_creator[:8]}",
            'risk_multiplier': risk_multiplier,
            'cluster_size': cluster_size,
            'network_depth': network_depth or 1
        }

    except Exception as e:
        # Return safe default on error
        return {
            'in_cluster': False,
            'cluster_id': None,
            'cluster_name': None,
            'risk_multiplier': 1.0,
            'cluster_size': 0,
            'network_depth': 0,
            'error': str(e)
        }
