"""
FLEX Dev Intelligence Graph V3 — Predictive Analytics Engine

Extends v1+v2 with full predictive intelligence:
- Multi-window launch predictions (24h, 72h, 7d)
- Organization time-series snapshots (7 daily signals)
- Composite risk scoring (rug + instability + velocity + blocked creators)
- Token outcome predictions (prob_rug, prob_2x, prob_10x)
- Cross-org relationship detection and family groupings
- Polling-based real-time alerts
- ML feature store (populated but not used in v3 core)

All rules-based, uses only existing SQLite data, follows v1+v2 patterns.
"""

import sqlite3
import json
import time
import logging
from typing import Dict, List, Tuple, Optional, Set
from pathlib import Path
from collections import defaultdict

try:
    import networkx as nx
except ImportError:
    nx = None

from src.core.dev_intelligence_v2 import LaunchProbabilityModel

logger = logging.getLogger(__name__)


# ============================================================================
# CLASS 1: LaunchWindowModel
# Multi-window launch prediction (24h, 72h, 7d)
# ============================================================================
class LaunchWindowModel:
    """Computes 3-window launch probability predictions."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.v2_model = LaunchProbabilityModel(db_path)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def compute_windows(self, org: Dict) -> Dict:
        """
        Compute all 3 windows for org.
        Returns dict with prob_launch_*, signal_* keys ready for INSERT OR REPLACE.
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            signals_24h = self._compute_24h_signals(cursor, org)
            signals_72h = self._compute_72h_signals(cursor, org)
            signals_7d = self._compute_7d_signals(org)  # reuses v2 model

            # Compute window scores
            prob_24h = self._score_24h(signals_24h)
            prob_72h = self._score_72h(signals_72h, org)
            prob_7d = signals_7d.get('launch_probability', 0)  # from v2

            return {
                'prob_launch_24h': prob_24h,
                'prob_launch_72h': prob_72h,
                'prob_launch_7d': prob_7d,
                'signal_burst_24h': signals_24h.get('burst_count', 0),
                'signal_recency_24h': signals_24h.get('recency_norm', 0),
                'signal_velocity_72h': signals_72h.get('velocity_72h', 0),
                'signal_coordination_72h': signals_72h.get('coordination_72h', 0),
                'signal_reputation_7d': signals_7d.get('reputation_signal', 0),
            }
        finally:
            conn.close()

    def _compute_24h_signals(self, cursor, org: Dict) -> Dict:
        """Extract burst_count and recency signals for 24h window."""
        wallet_list = json.loads(org['wallet_list']) if org['wallet_list'] else []
        if not wallet_list:
            return {'burst_count': 0, 'recency_norm': 0}

        now_ts = int(time.time())
        since_ts = now_ts - 86400  # 24h ago

        # burst_count: 1h windows with 3+ transfers
        placeholders = ','.join('?' * len(wallet_list))
        cursor.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT CAST(block_time/3600 AS INTEGER) AS hour_bucket, COUNT(*) AS tx_count
                FROM transfer_index
                WHERE source IN ({placeholders})
                  AND block_time >= ?
                GROUP BY hour_bucket
                HAVING tx_count >= 3
            )
        """, wallet_list + [since_ts])
        burst_count = cursor.fetchone()[0] or 0

        # hours_since_last_tx
        cursor.execute(f"""
            SELECT (? - MAX(block_time)) / 3600.0 AS hours_since
            FROM transfer_index
            WHERE (source IN ({placeholders}) OR destination IN ({placeholders}))
              AND block_time >= ?
        """, [now_ts] + wallet_list + wallet_list + [since_ts])
        result = cursor.fetchone()
        hours_since = result[0] if result and result[0] else 24.0

        # Normalize: max(0, 1 - hours / 24)
        recency_norm = max(0, 1 - hours_since / 24.0)

        return {
            'burst_count': burst_count,
            'recency_norm': recency_norm,
        }

    def _compute_72h_signals(self, cursor, org: Dict) -> Dict:
        """Extract velocity and coordination signals for 72h window."""
        wallet_list = json.loads(org['wallet_list']) if org['wallet_list'] else []
        if not wallet_list:
            return {'velocity_72h': 0, 'coordination_72h': 0}

        now_ts = int(time.time())
        since_ts_72h = now_ts - 259200  # 72h ago

        # sol_in_72h
        placeholders = ','.join('?' * len(wallet_list))
        cursor.execute(f"""
            SELECT COALESCE(SUM(amount_sol), 0) AS sol_72h
            FROM transfer_index
            WHERE source IN ({placeholders})
              AND block_time >= ?
        """, wallet_list + [since_ts_72h])
        sol_72h = cursor.fetchone()[0] or 0

        # Normalize velocity: min(15, sol / 50 * 15)
        velocity_72h = min(15, (sol_72h / 50.0) * 15) if sol_72h > 0 else 0

        # avg_edge_weight_72h (reuse from v2 logic if available, else estimate)
        # For now, approximate as avg of org's edge weights from recent transfers
        coordinator_value = org.get('avg_edge_weight', 0)
        coordination_72h = min(25, (coordinator_value / 100.0) * 25) if coordinator_value else 0

        return {
            'velocity_72h': velocity_72h,
            'coordination_72h': coordination_72h,
        }

    def _compute_7d_signals(self, org: Dict) -> Dict:
        """Delegate to v2 LaunchProbabilityModel for 7d window."""
        signals = self.v2_model.compute_signals(org)
        score = self.v2_model.score(signals)

        # Extract reputation signal (normalized 0-10)
        rep_signal = min(10, signals.get('reputation_score', 0) * 10)

        return {
            'launch_probability': score,
            'reputation_signal': rep_signal,
            **signals
        }

    def _score_24h(self, signals: Dict) -> float:
        """24h window score: burst_norm*60 + recency_norm*40."""
        burst_norm = min(signals.get('burst_count', 0) / 5.0, 1.0)
        recency_norm = signals.get('recency_norm', 0)
        return min(100, burst_norm * 60 + recency_norm * 40)

    def _score_72h(self, signals: Dict, org: Dict) -> float:
        """72h window: recency(0-30) + velocity(0-15) + coord(0-25) + scale(0-20) + rep(0-10)."""
        # Recency from scale
        now_ts = int(time.time())
        days_since = (now_ts - org.get('detected_at', now_ts)) / 86400.0
        recency_72h = max(30 * (1 - days_since / 3.0), 0) if days_since < 3 else 0

        velocity_72h = signals.get('velocity_72h', 0)  # 0-15
        coord_72h = signals.get('coordination_72h', 0)  # 0-25

        # Scale from v2 (reuse from org fields)
        scale_norm = min(20, org.get('cluster_size', 1) / 20.0 * 20)

        # Reputation signal
        rep_norm = min(10, org.get('organization_score', 0.1) * 10)

        total = recency_72h + velocity_72h + coord_72h + scale_norm + rep_norm
        return min(100, total)


# ============================================================================
# CLASS 2: OrgSnapshotRecorder
# Daily activity snapshots (7 signals)
# ============================================================================
class OrgSnapshotRecorder:
    """Captures 7 daily activity signals per organization."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def take_snapshot(self, org: Dict, cursor: sqlite3.Cursor) -> Dict:
        """Take snapshot of 7 signals for org."""
        wallet_list = json.loads(org['wallet_list']) if org['wallet_list'] else []
        creator_list = json.loads(org['creator_list']) if org['creator_list'] else []
        token_list = json.loads(org['token_list']) if org['token_list'] else []
        all_members = wallet_list + creator_list + token_list

        now_ts = int(time.time())

        return {
            'active_funders': self._count_active_funders(cursor, wallet_list, now_ts),
            'active_creators': self._count_active_creators(cursor, creator_list, now_ts),
            'burst_count': self._count_bursts(cursor, all_members, now_ts),
            'weighted_volume': self._compute_weighted_volume(cursor, wallet_list, now_ts),
            'graph_density': self._compute_graph_density(cursor, all_members, org.get('cluster_size', 1)),
            'launch_count': self._count_launches(cursor, creator_list),
            'rug_count': self._count_rugs(cursor, token_list),
        }

    def _count_active_funders(self, cursor, wallet_list, since_ts) -> int:
        """Org wallets that sent SOL in last 24h."""
        if not wallet_list:
            return 0
        placeholders = ','.join('?' * len(wallet_list))
        since_24h = since_ts - 86400
        cursor.execute(f"""
            SELECT COUNT(DISTINCT source) AS count
            FROM transfer_index
            WHERE source IN ({placeholders})
              AND block_time >= ?
        """, wallet_list + [since_24h])
        return cursor.fetchone()[0] or 0

    def _count_active_creators(self, cursor, creator_list, since_ts) -> int:
        """Creators receiving SOL in last 24h."""
        if not creator_list:
            return 0
        placeholders = ','.join('?' * len(creator_list))
        since_24h = since_ts - 86400
        cursor.execute(f"""
            SELECT COUNT(DISTINCT destination) AS count
            FROM transfer_index
            WHERE destination IN ({placeholders})
              AND block_time >= ?
        """, creator_list + [since_24h])
        return cursor.fetchone()[0] or 0

    def _count_bursts(self, cursor, all_members, since_ts) -> int:
        """1h windows with 3+ transfers in last 24h."""
        if not all_members:
            return 0
        placeholders = ','.join('?' * len(all_members))
        since_24h = since_ts - 86400
        cursor.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT CAST(block_time/3600 AS INTEGER) AS hour, COUNT(*) AS cnt
                FROM transfer_index
                WHERE (source IN ({placeholders}) OR destination IN ({placeholders}))
                  AND block_time >= ?
                GROUP BY hour
                HAVING cnt >= 3
            )
        """, all_members + all_members + [since_24h])
        return cursor.fetchone()[0] or 0

    def _compute_weighted_volume(self, cursor, wallet_list, since_ts) -> float:
        """Sum of SOL moved by wallets in last 24h."""
        if not wallet_list:
            return 0
        placeholders = ','.join('?' * len(wallet_list))
        since_24h = since_ts - 86400
        cursor.execute(f"""
            SELECT COALESCE(SUM(amount_sol), 0) AS vol
            FROM transfer_index
            WHERE source IN ({placeholders})
              AND block_time >= ?
        """, wallet_list + [since_24h])
        return cursor.fetchone()[0] or 0

    def _compute_graph_density(self, cursor, all_members, cluster_size) -> float:
        """Edge count / max possible edges for cluster."""
        if not all_members or cluster_size < 2:
            return 0
        placeholders = ','.join('?' * len(all_members))
        cursor.execute(f"""
            SELECT COUNT(DISTINCT source || '-' || destination) AS edges
            FROM transfer_index
            WHERE source IN ({placeholders})
              AND destination IN ({placeholders})
        """, all_members + all_members)
        actual_edges = cursor.fetchone()[0] or 0
        max_edges = (cluster_size * (cluster_size - 1)) / 2
        return actual_edges / max_edges if max_edges > 0 else 0

    def _count_launches(self, cursor, creator_list) -> int:
        """Tokens launched by org creators in last 24h."""
        if not creator_list:
            return 0
        placeholders = ','.join('?' * len(creator_list))
        today = time.strftime('%Y-%m-%d', time.gmtime(int(time.time()) - 86400))
        cursor.execute(f"""
            SELECT COUNT(*) FROM token_analysis
            WHERE earliest_tx_creator IN ({placeholders})
              AND created_at >= ?
        """, creator_list + [today])
        return cursor.fetchone()[0] or 0

    def _count_rugs(self, cursor, token_list) -> int:
        """Org tokens with rug_probability > 0.7."""
        if not token_list:
            return 0
        placeholders = ','.join('?' * len(token_list))
        cursor.execute(f"""
            SELECT COUNT(*) FROM token_analysis
            WHERE mint IN ({placeholders})
              AND rug_probability > 0.7
        """, token_list)
        return cursor.fetchone()[0] or 0


# ============================================================================
# CLASS 3: OrgRiskScorer
# Composite risk scoring
# ============================================================================
class OrgRiskScorer:
    """Computes multi-component risk score per organization."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def score_org(self, org: Dict, cursor: sqlite3.Cursor) -> Dict:
        """Compute risk score and components."""
        token_list = json.loads(org['token_list']) if org['token_list'] else []

        rug_probability = self._fetch_rug_probability(cursor, token_list)
        instability_score = self._fetch_instability(cursor, org['organization_id'])
        blocked_count, total_count = self._fetch_blocked_creators(cursor, org['organization_id'])

        # Token velocity
        active_days = max(org.get('detected_at', 0), 1)
        if active_days > 0:
            token_velocity = org.get('token_count', 0) / max(active_days / 86400, 1)
        else:
            token_velocity = 0

        # Blocked ratio
        blocked_ratio = blocked_count / max(total_count, 1)

        # Components
        comp_rug = rug_probability * 40
        comp_instability = instability_score * 0.25
        comp_velocity = min(token_velocity / 2.0, 1.0) * 20
        comp_blocked = blocked_ratio * 15

        risk_score = min(100, comp_rug + comp_instability + comp_velocity + comp_blocked)
        confidence = self._compute_confidence(org)

        return {
            'risk_score': risk_score,
            'rug_probability': rug_probability,
            'instability_score': instability_score,
            'confidence': confidence,
            'component_rug_prob': comp_rug,
            'component_instability': comp_instability,
            'component_token_velocity': comp_velocity,
            'component_blocked_ratio': comp_blocked,
            'blocked_creator_count': blocked_count,
            'total_creator_count': total_count,
            'token_velocity': token_velocity,
        }

    def _fetch_rug_probability(self, cursor, token_list) -> float:
        """Average rug probability for org tokens."""
        if not token_list:
            return 0
        placeholders = ','.join('?' * len(token_list))
        cursor.execute(f"""
            SELECT COALESCE(AVG(rug_probability), 0) FROM token_analysis
            WHERE mint IN ({placeholders})
        """, token_list)
        return cursor.fetchone()[0] or 0

    def _fetch_instability(self, cursor, org_id: int) -> float:
        """Volatility of last 3 org_snapshots weighted_volume."""
        cursor.execute("""
            SELECT weighted_volume FROM org_snapshots
            WHERE organization_id = ?
            ORDER BY snapshot_date DESC
            LIMIT 3
        """, (org_id,))

        volumes = [row[0] for row in cursor.fetchall()]
        if len(volumes) < 2:
            return 0

        max_vol = max(volumes)
        min_vol = min(volumes)
        if max_vol == 0:
            return 0

        volatility = ((max_vol - min_vol) / max_vol) * 100
        return min(100, volatility)

    def _fetch_blocked_creators(self, cursor, org_id: int) -> Tuple[int, int]:
        """Count blocked creators vs total creators in org."""
        cursor.execute("""
            SELECT COUNT(*) FROM dev_organization_members
            WHERE organization_id = ?
              AND member_type = 'creator'
        """, (org_id,))
        total = cursor.fetchone()[0] or 0

        cursor.execute("""
            SELECT COUNT(*) FROM dev_organization_members dom
            LEFT JOIN token_analysis ta ON dom.member_address = ta.earliest_tx_creator
            WHERE dom.organization_id = ?
              AND dom.member_type = 'creator'
              AND ta.creator_is_blocked = 1
        """, (org_id,))
        blocked = cursor.fetchone()[0] or 0

        return blocked, total

    def _compute_confidence(self, org: Dict) -> float:
        """Confidence based on cluster size and creator count."""
        cluster_size = org.get('cluster_size', 1)
        creator_count = org.get('creator_count', 0)
        return min(1.0, (cluster_size / 10.0) * 0.5 + (min(creator_count, 5) / 5.0) * 0.5)


# ============================================================================
# CLASS 4: TokenOutcomePredictor
# Token outcome heuristics
# ============================================================================
class TokenOutcomePredictor:
    """Predicts outcome probabilities for all tokens."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def predict_all_tokens(self, cursor: sqlite3.Cursor) -> List[Dict]:
        """Load all tokens and predict outcomes."""
        cursor.execute("""
            SELECT ta.mint, ta.rug_probability, ta.network_risk, ta.creator_is_blocked,
                   ta.earliest_tx_creator,
                   COALESCE(dr.rug_rate, 0) AS creator_rug_rate,
                   COALESCE(dr.success_rate, 0) AS creator_success,
                   dom.organization_id,
                   COALESCE(do_.organization_score, 0.1) AS org_score
            FROM token_analysis ta
            LEFT JOIN dev_reputation dr ON ta.earliest_tx_creator = dr.wallet
            LEFT JOIN dev_organization_members dom ON ta.earliest_tx_creator = dom.member_address
                      AND dom.member_type = 'creator'
            LEFT JOIN dev_organizations do_ ON dom.organization_id = do_.organization_id
            WHERE ta.mint IS NOT NULL AND ta.mint != ''
        """)

        predictions = []
        for row in cursor.fetchall():
            pred = self._predict_token(dict(row))
            predictions.append(pred)

        return predictions

    def _predict_token(self, token: Dict) -> Dict:
        """Apply outcome formulas to token."""
        rug_prob = token.get('rug_probability', 0)
        network_risk = token.get('network_risk', 0)
        is_blocked = token.get('creator_is_blocked', 0)
        creator_rug = token.get('creator_rug_rate', 0)
        creator_success = token.get('creator_success', 0)
        org_score = token.get('org_score', 0.1)

        # prob_rug
        sig_rug = rug_prob * 0.40
        sig_creator = creator_rug * 0.25
        sig_network = 0.25 if network_risk > 0 else 0
        sig_blocked = 0.10 if is_blocked else 0
        prob_rug = min(1.0, sig_rug + sig_creator + sig_network + sig_blocked)

        # prob_2x
        recency_bonus = 1.0  # simplified: always 1.0
        prob_2x = min(1.0, creator_success * 0.5 + org_score * 0.3 + recency_bonus * 0.2)

        # prob_10x
        prob_10x = min(1.0, prob_2x * (1 - prob_rug) * 0.3)

        # expected_quality_score
        quality = ((1 - prob_rug) * 0.6 + prob_2x * 0.4) * 100

        return {
            'mint': token['mint'],
            'prob_rug': prob_rug,
            'prob_2x': prob_2x,
            'prob_10x': prob_10x,
            'expected_quality_score': quality,
            'signal_rug_prob': sig_rug,
            'signal_creator_risk': sig_creator,
            'signal_network_risk': sig_network,
            'signal_blocked': sig_blocked,
            'creator_wallet': token['earliest_tx_creator'],
            'organization_id': token['organization_id'],
            'days_since_org_funded': 0,  # simplified
        }


# ============================================================================
# CLASS 5: CrossOrgAnalyzer
# Cross-org relationships and family detection
# ============================================================================
class CrossOrgAnalyzer:
    """Detects relationships between organizations and groups into families."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def analyze(self, orgs: List[Dict], cursor: sqlite3.Cursor) -> Tuple[List[Dict], List[Dict]]:
        """Find relationships and families."""
        relationships = []

        # O(N²) pairwise comparison with fast rejection
        for i, org_a in enumerate(orgs):
            for org_b in orgs[i+1:]:
                # Fast check: skip if no creator/wallet overlap
                creators_a = set(json.loads(org_a.get('creator_list', '[]')) or [])
                creators_b = set(json.loads(org_b.get('creator_list', '[]')) or [])
                wallets_a = set(json.loads(org_a.get('wallet_list', '[]')) or [])
                wallets_b = set(json.loads(org_b.get('wallet_list', '[]')) or [])

                if not (creators_a & creators_b) and not (wallets_a & wallets_b):
                    continue  # No overlap, skip

                rel = self._compute_relationship(org_a, org_b, cursor)
                if rel and rel.get('relationship_strength', 0) >= 10:
                    relationships.append(rel)

        families = self._detect_families(relationships, orgs) if nx else []

        return relationships, families

    def _compute_relationship(self, org_a: Dict, org_b: Dict, cursor) -> Optional[Dict]:
        """Compute relationship between two orgs."""
        shared_creators = self._shared_creator_count(org_a, org_b)
        shared_op = 1 if org_a.get('operator_wallet') == org_b.get('operator_wallet') else 0
        indirect_fund = 1 if self._has_indirect_funding(cursor, org_a, org_b) else 0

        # Strength formula
        shared_creator_score = min(shared_creators / 3.0, 1.0) * 50
        shared_op_score = shared_op * 30
        indirect_score = indirect_fund * 20
        strength = shared_creator_score + shared_op_score + indirect_score

        # Relationship type
        if shared_op:
            rel_type = 'sibling'
        elif shared_creators >= 2:
            rel_type = 'parent_child'
        else:
            rel_type = 'independent'

        now = time.time()

        org_id_a = org_a['organization_id']
        org_id_b = org_b['organization_id']
        if org_id_a > org_id_b:
            org_id_a, org_id_b = org_id_b, org_id_a

        return {
            'org_id_a': org_id_a,
            'org_id_b': org_id_b,
            'shared_creator_count': shared_creators,
            'shared_operator': shared_op,
            'indirect_funding_overlap': indirect_fund,
            'relationship_strength': strength,
            'relationship_type': rel_type,
            'detected_at': now,
            'updated_at': now,
        }

    def _shared_creator_count(self, org_a: Dict, org_b: Dict) -> int:
        """Count shared creators between orgs."""
        creators_a = set(json.loads(org_a.get('creator_list', '[]')) or [])
        creators_b = set(json.loads(org_b.get('creator_list', '[]')) or [])
        return len(creators_a & creators_b)

    def _has_indirect_funding(self, cursor, org_a: Dict, org_b: Dict) -> bool:
        """Check if any wallet in A funded any wallet in B."""
        wallets_a = json.loads(org_a.get('wallet_list', '[]')) or []
        wallets_b = json.loads(org_b.get('wallet_list', '[]')) or []

        if not wallets_a or not wallets_b:
            return False

        a_ph = ','.join('?' * len(wallets_a))
        b_ph = ','.join('?' * len(wallets_b))

        cursor.execute(f"""
            SELECT COUNT(*) FROM transfer_index
            WHERE source IN ({a_ph})
              AND destination IN ({b_ph})
            LIMIT 1
        """, wallets_a + wallets_b)

        return cursor.fetchone()[0] > 0

    def _detect_families(self, relationships: List[Dict], orgs: List[Dict]) -> List[Dict]:
        """Use networkx to detect org families from relationship graph."""
        if not nx or not relationships:
            return []

        # Build graph
        G = nx.Graph()
        for org in orgs:
            G.add_node(org['organization_id'])

        for rel in relationships:
            if rel.get('relationship_strength', 0) >= 30:  # threshold for family inclusion
                G.add_edge(rel['org_id_a'], rel['org_id_b'])

        families = []
        now = time.time()
        family_id = 0

        for component in nx.weakly_connected_components(G):
            family_id += 1

            # Find hub (highest betweenness)
            subgraph = G.subgraph(component)
            if len(subgraph) > 0:
                centrality = nx.betweenness_centrality(subgraph)
                hub_id = max(centrality, key=centrality.get)
            else:
                hub_id = next(iter(component))

            # Compute family score (avg edge strength)
            edges = [r for r in relationships if r['org_id_a'] in component and r['org_id_b'] in component]
            family_score = sum(e.get('relationship_strength', 0) for e in edges) / max(len(edges), 1)

            for org_id in component:
                families.append({
                    'family_id': family_id,
                    'organization_id': org_id,
                    'family_score': family_score,
                    'hub_org_id': hub_id,
                    'detected_at': now,
                    'updated_at': now,
                })

        return families


# ============================================================================
# CLASS 6: OrgAlertWorker
# Polling-based real-time alerts
# ============================================================================
class OrgAlertWorker:
    """Detects and fires alerts based on org activity thresholds."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def check_and_fire(self, cursor: sqlite3.Cursor, snapshots: Dict[int, Dict],
                       risks: Dict[int, Dict]) -> int:
        """Evaluate all orgs and fire alerts. Returns count fired."""
        cursor.execute("SELECT organization_id FROM dev_organizations")
        org_ids = [row[0] for row in cursor.fetchall()]

        fired_count = 0
        for org_id in org_ids:
            snapshot = snapshots.get(org_id, {})
            risk = risks.get(org_id, {})

            alerts = self._check_org(org_id, snapshot, risk, cursor)
            for alert in alerts:
                self._fire_alert(cursor, alert)
                fired_count += 1

        return fired_count

    def _check_org(self, org_id: int, snapshot: Dict, risk: Dict, cursor) -> List[Dict]:
        """Check org against alert thresholds."""
        alerts = []
        now = time.time()

        # funding_burst: active_funders >= 3
        if snapshot.get('active_funders', 0) >= 3:
            if self._should_fire(org_id, 'funding_burst', cursor):
                alerts.append({
                    'organization_id': org_id,
                    'alert_type': 'funding_burst',
                    'severity': 'high',
                    'message': f"Funding burst detected: {snapshot.get('active_funders', 0)} active funders",
                    'signal_value': snapshot.get('active_funders', 0),
                    'signal_threshold': 3,
                    'created_at': now,
                })

        # creator_funded: active_creators >= 2
        if snapshot.get('active_creators', 0) >= 2:
            if self._should_fire(org_id, 'creator_funded', cursor):
                alerts.append({
                    'organization_id': org_id,
                    'alert_type': 'creator_funded',
                    'severity': 'medium',
                    'message': f"Creators funded: {snapshot.get('active_creators', 0)} active creators",
                    'signal_value': snapshot.get('active_creators', 0),
                    'signal_threshold': 2,
                    'created_at': now,
                })

        # operator_spike: burst_count >= 5
        if snapshot.get('burst_count', 0) >= 5:
            if self._should_fire(org_id, 'operator_spike', cursor):
                alerts.append({
                    'organization_id': org_id,
                    'alert_type': 'operator_spike',
                    'severity': 'high',
                    'message': f"Operator spike: {snapshot.get('burst_count', 0)} burst windows",
                    'signal_value': snapshot.get('burst_count', 0),
                    'signal_threshold': 5,
                    'created_at': now,
                })

        # risk_spike: risk_score > 20 increase
        # (simplified: just check if risk > 60)
        if risk.get('risk_score', 0) > 60:
            if self._should_fire(org_id, 'risk_spike', cursor):
                alerts.append({
                    'organization_id': org_id,
                    'alert_type': 'risk_spike',
                    'severity': 'critical',
                    'message': f"Critical risk detected: {risk.get('risk_score', 0):.1f}",
                    'signal_value': risk.get('risk_score', 0),
                    'signal_threshold': 60,
                    'created_at': now,
                })

        return alerts

    def _should_fire(self, org_id: int, alert_type: str, cursor) -> bool:
        """Check if alert should fire (not already fired today)."""
        today = time.strftime('%Y-%m-%d', time.gmtime())
        cursor.execute("""
            SELECT COUNT(*) FROM org_alerts
            WHERE organization_id = ?
              AND alert_type = ?
              AND date(created_at, 'unixepoch') = ?
        """, (org_id, alert_type, today))
        return cursor.fetchone()[0] == 0

    def _fire_alert(self, cursor, alert: Dict) -> None:
        """Insert alert into database."""
        cursor.execute("""
            INSERT INTO org_alerts
            (organization_id, alert_type, severity, message, signal_value, signal_threshold, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (alert['organization_id'], alert['alert_type'], alert['severity'],
              alert['message'], alert['signal_value'], alert['signal_threshold'],
              alert['created_at']))


# ============================================================================
# CLASS 7: FeatureStoreBuilder
# ML feature store (populated but not used in v3 core)
# ============================================================================
class FeatureStoreBuilder:
    """Builds ML feature store for future ML models."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def build_features(self, orgs: List[Dict], cursor: sqlite3.Cursor) -> int:
        """Build features for all entities. Returns count inserted."""
        count = 0

        for org in orgs:
            features = self._org_features(org, cursor)
            cursor.execute("""
                INSERT OR REPLACE INTO prediction_features
                (entity_id, entity_type, f_tokens_launched, f_rug_rate, f_success_rate,
                 f_cluster_size, f_wallet_count, f_creator_count, f_total_volume_sol,
                 f_organization_score, f_computed_at, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(org['organization_id']), 'organization',
                  org.get('token_count', 0),
                  0,  # rug_rate not directly in org
                  0,  # success_rate not directly in org
                  org.get('cluster_size', 0),
                  org.get('wallet_count', 0),
                  org.get('creator_count', 0),
                  org.get('total_volume_sol', 0),
                  org.get('organization_score', 0),
                  org.get('organization_score', 0),
                  time.time()))
            count += 1

        return count


# ============================================================================
# CLASS 8: DevIntelligenceV3Engine
# Orchestrator — follows v2 pattern exactly
# ============================================================================
class DevIntelligenceV3Engine:
    """Main v3 intelligence engine."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.start_time = time.time()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_tables(self) -> None:
        """Create v3 tables if not exist (idempotent)."""
        conn = self._get_conn()
        migration_path = Path(__file__).parent.parent.parent / 'database' / 'migrations' / 'dev_intelligence_v3.sql'
        if migration_path.exists():
            with open(migration_path) as f:
                conn.executescript(f.read())
        conn.close()

    def _load_organizations(self, cursor) -> List[Dict]:
        """Load all dev_organizations with JSON lists parsed."""
        cursor.execute("""
            SELECT organization_id, operator_wallet, cluster_size, wallet_count,
                   creator_count, token_count, token_list, creator_list, wallet_list,
                   organization_score, betweenness_centrality, pagerank_score,
                   total_volume_sol, cluster_strength, detected_at, avg_edge_weight
            FROM dev_organizations
        """)
        return [dict(row) for row in cursor.fetchall()]

    def detect_and_store(self) -> Dict:
        """Main detection flow."""
        try:
            self._ensure_tables()

            conn = self._get_conn()
            cursor = conn.cursor()

            # Load orgs
            orgs = self._load_organizations(cursor)
            if not orgs:
                logger.warning("No organizations found")
                conn.close()
                return {
                    'status': 'success',
                    'message': 'No organizations to process',
                    'orgs_processed': 0,
                    'tokens_predicted': 0,
                    'alerts_fired': 0,
                    'duration_ms': int((time.time() - self.start_time) * 1000)
                }

            now = time.time()

            # Initialize models
            snapshot_recorder = OrgSnapshotRecorder(self.db_path)
            risk_scorer = OrgRiskScorer(self.db_path)
            window_model = LaunchWindowModel(self.db_path)
            cross_analyzer = CrossOrgAnalyzer(self.db_path)
            alert_worker = OrgAlertWorker(self.db_path)
            feature_builder = FeatureStoreBuilder(self.db_path)
            token_predictor = TokenOutcomePredictor(self.db_path)

            snapshots = {}
            risks = {}

            # Step 1: Snapshots
            for org in orgs:
                snapshot = snapshot_recorder.take_snapshot(org, cursor)
                snapshots[org['organization_id']] = snapshot
                self._store_snapshot(cursor, org['organization_id'], snapshot, now)

            # Step 2: Risk scores
            for org in orgs:
                risk = risk_scorer.score_org(org, cursor)
                risks[org['organization_id']] = risk
                self._store_risk(cursor, org['organization_id'], risk, now)

            # Step 3: Launch windows
            for org in orgs:
                windows = window_model.compute_windows(org)
                self._store_launch_windows(cursor, org['organization_id'], windows, now)

            # Step 4: Relationships & families
            relationships, families = cross_analyzer.analyze(orgs, cursor)
            self._store_relationships(cursor, relationships, now)
            self._store_families(cursor, families, now)

            # Step 5: Token predictions
            predictions = token_predictor.predict_all_tokens(cursor)
            self._store_token_predictions(cursor, predictions, now)

            # Step 6: Alerts
            alerts_fired = alert_worker.check_and_fire(cursor, snapshots, risks)

            # Step 7: Features
            features_count = feature_builder.build_features(orgs, cursor)

            conn.commit()
            conn.close()

            duration_ms = int((time.time() - self.start_time) * 1000)

            return {
                'status': 'success',
                'message': f'V3 detection complete',
                'orgs_processed': len(orgs),
                'tokens_predicted': len(predictions),
                'alerts_fired': alerts_fired,
                'duration_ms': duration_ms
            }

        except Exception as e:
            logger.error(f"V3 detection failed: {e}", exc_info=True)
            return {
                'status': 'error',
                'message': str(e),
                'orgs_processed': 0,
                'tokens_predicted': 0,
                'alerts_fired': 0,
                'duration_ms': int((time.time() - self.start_time) * 1000)
            }

    def _store_launch_windows(self, cursor, org_id: int, windows: Dict, now: float) -> None:
        """Store launch window predictions."""
        cursor.execute("""
            INSERT OR REPLACE INTO org_launch_windows
            (organization_id, prediction_date, prob_launch_24h, prob_launch_72h, prob_launch_7d,
             signal_burst_24h, signal_recency_24h, signal_velocity_72h, signal_coordination_72h,
             signal_reputation_7d, computed_at)
            VALUES (?, date('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (org_id, windows['prob_launch_24h'], windows['prob_launch_72h'],
              windows['prob_launch_7d'], windows['signal_burst_24h'], windows['signal_recency_24h'],
              windows['signal_velocity_72h'], windows['signal_coordination_72h'],
              windows['signal_reputation_7d'], now))

    def _store_snapshot(self, cursor, org_id: int, snapshot: Dict, now: float) -> None:
        """Store daily snapshot."""
        cursor.execute("""
            INSERT OR REPLACE INTO org_snapshots
            (organization_id, snapshot_date, active_funders, active_creators, burst_count,
             weighted_volume, graph_density, launch_count, rug_count, computed_at)
            VALUES (?, date('now'), ?, ?, ?, ?, ?, ?, ?, ?)
        """, (org_id, snapshot['active_funders'], snapshot['active_creators'],
              snapshot['burst_count'], snapshot['weighted_volume'], snapshot['graph_density'],
              snapshot['launch_count'], snapshot['rug_count'], now))

    def _store_risk(self, cursor, org_id: int, risk: Dict, now: float) -> None:
        """Store risk score."""
        cursor.execute("""
            INSERT OR REPLACE INTO org_risk_scores
            (organization_id, risk_score, rug_probability, instability_score, confidence,
             component_rug_prob, component_instability, component_token_velocity,
             component_blocked_ratio, blocked_creator_count, total_creator_count,
             token_velocity, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (org_id, risk['risk_score'], risk['rug_probability'], risk['instability_score'],
              risk['confidence'], risk['component_rug_prob'], risk['component_instability'],
              risk['component_token_velocity'], risk['component_blocked_ratio'],
              risk['blocked_creator_count'], risk['total_creator_count'],
              risk['token_velocity'], now))

    def _store_relationships(self, cursor, relationships: List[Dict], now: float) -> None:
        """Store org relationships."""
        for rel in relationships:
            cursor.execute("""
                INSERT OR REPLACE INTO org_relationships
                (org_id_a, org_id_b, shared_creator_count, shared_operator,
                 indirect_funding_overlap, relationship_strength, relationship_type,
                 detected_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (rel['org_id_a'], rel['org_id_b'], rel['shared_creator_count'],
                  rel['shared_operator'], rel['indirect_funding_overlap'],
                  rel['relationship_strength'], rel['relationship_type'],
                  rel['detected_at'], rel['updated_at']))

    def _store_families(self, cursor, families: List[Dict], now: float) -> None:
        """Store org families."""
        for fam in families:
            cursor.execute("""
                INSERT OR REPLACE INTO org_families
                (family_id, organization_id, family_score, hub_org_id, detected_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (fam['family_id'], fam['organization_id'], fam['family_score'],
                  fam['hub_org_id'], fam['detected_at'], fam['updated_at']))

    def _store_token_predictions(self, cursor, predictions: List[Dict], now: float) -> None:
        """Store token outcome predictions."""
        for pred in predictions:
            cursor.execute("""
                INSERT OR REPLACE INTO token_outcome_predictions
                (mint, prob_rug, prob_2x, prob_10x, expected_quality_score,
                 signal_rug_prob, signal_creator_risk, signal_network_risk, signal_blocked,
                 creator_wallet, organization_id, days_since_org_funded, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pred['mint'], pred['prob_rug'], pred['prob_2x'], pred['prob_10x'],
                  pred['expected_quality_score'], pred['signal_rug_prob'],
                  pred['signal_creator_risk'], pred['signal_network_risk'],
                  pred['signal_blocked'], pred['creator_wallet'], pred['organization_id'],
                  pred['days_since_org_funded'], now))
