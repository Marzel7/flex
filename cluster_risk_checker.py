import sqlite3
import json
from typing import Dict, Optional

DB_PATH = "pumpswap_tokens.db"

CLUSTER_RISK_MULTIPLIERS = {
    "FUNDERS_1": 3.0,    # 3x multiplier - CRITICAL network
    "FUNDERS_9": 2.0,    # 2x multiplier - HIGH risk network
    "FUNDERS_3": 1.5,    # 1.5x multiplier - MEDIUM risk network
}

CLUSTER_NAMES = {
    "FUNDERS_1": "The Syndicate",
    "FUNDERS_9": "The Network",
    "FUNDERS_3": "The Trio",
    "FUNDERS_2": "Pair Alpha",
    "FUNDERS_4": "Pair Beta",
    "FUNDERS_5": "Pair Gamma",
    "FUNDERS_6": "Pair Delta",
    "FUNDERS_7": "Pair Epsilon",
    "FUNDERS_8": "Pair Zeta",
}

CLUSTER_RISK_LABELS = {
    "FUNDERS_1": "🚨 CRITICAL - Coordinated Network (95 funders)",
    "FUNDERS_9": "⚠️ HIGH - Secondary Network (20 funders)",
    "FUNDERS_3": "🟡 MEDIUM - Small Network (3 funders)",
}


class ClusterRiskChecker:
    """Check if a creator is part of a known funder cluster."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._creator_to_cluster: Optional[Dict[str, Dict]] = None

    def _load_cache(self) -> None:
        """
        Build mapping of creator -> cluster info by scanning funder_networks once.
        Since funder_networks is small (~130 rows), this is fast.
        """
        mapping: Dict[str, Dict] = {}
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute("""
            SELECT cluster_id, network_size, total_volume_sol, creators_served
            FROM funder_networks
            WHERE cluster_id IS NOT NULL
        """)
        rows = cur.fetchall()
        conn.close()

        for cluster_id, network_size, total_volume_sol, creators_json in rows:
            try:
                creators = json.loads(creators_json) if creators_json else []
            except Exception:
                creators = []

            for creator in creators:
                # If creator appears in multiple clusters (rare), keep highest multiplier
                prev = mapping.get(creator)
                mult = CLUSTER_RISK_MULTIPLIERS.get(cluster_id, 1.0)
                prev_mult = prev["risk_multiplier"] if prev else 0.0
                if mult >= prev_mult:
                    mapping[creator] = {
                        "cluster_id": cluster_id,
                        "network_size": int(network_size or 0),
                        "network_volume_sol": float(total_volume_sol or 0.0),
                        "risk_multiplier": float(mult),
                        "risk_label": CLUSTER_RISK_LABELS.get(cluster_id, f"Network {cluster_id}"),
                    }

        self._creator_to_cluster = mapping

    def check_creator_cluster(self, creator_address: str) -> Dict:
        """
        Check if creator is in any funder cluster.

        Returns:
            {
                'in_cluster': bool,
                'cluster_id': str or None,
                'risk_multiplier': float,
                'risk_label': str,
                'network_size': int,
                'network_volume_sol': float,
            }
        """
        if self._creator_to_cluster is None:
            self._load_cache()

        entry = self._creator_to_cluster.get(creator_address)
        if not entry:
            return {
                'in_cluster': False,
                'cluster_id': None,
                'risk_multiplier': 1.0,
                'risk_label': '✅ No cluster detected',
                'network_size': 0,
                'network_volume_sol': 0.0,
            }

        return {
            'in_cluster': True,
            **entry,
        }

    def get_all_cluster_creators(self, cluster_id: str) -> list:
        """Get all creators in a specific cluster."""
        if self._creator_to_cluster is None:
            self._load_cache()

        return [
            creator
            for creator, info in self._creator_to_cluster.items()
            if info["cluster_id"] == cluster_id
        ]


# Global instance
_checker: Optional[ClusterRiskChecker] = None


def get_checker() -> ClusterRiskChecker:
    """Get or create the global checker instance."""
    global _checker
    if _checker is None:
        _checker = ClusterRiskChecker()
    return _checker


def check_creator(creator_address: str) -> Dict:
    """Quick function to check a creator's cluster status."""
    return get_checker().check_creator_cluster(creator_address)
