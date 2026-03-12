"""
FLEX UI Service Layer

Provides data access logic for dashboard API endpoints.
Encapsulates database queries and signal calculations.

Services:
- DashboardService: System overview and alerts
- OrganizationService: Organization intelligence
- LaunchService: Launch prediction and waves
- WalletService: Wallet intelligence
- SignalService: Predictive signals
"""

import sqlite3
import json
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class DashboardService:
    """Provides dashboard overview data."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def get_dashboard_overview(self) -> Dict:
        """
        Return high-level system status and top alerts.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Count critical and high alerts
            cursor.execute("""
                SELECT
                    COUNT(CASE WHEN alert_level = 'CRITICAL' THEN 1 END) as critical_count,
                    COUNT(CASE WHEN alert_level = 'HIGH' THEN 1 END) as high_count
                FROM master_launch_signals
                WHERE alert_level IN ('CRITICAL', 'HIGH')
            """)
            alert_row = cursor.fetchone()
            critical_alerts = dict(alert_row)['critical_count'] if alert_row else 0
            high_alerts = dict(alert_row)['high_count'] if alert_row else 0

            # Count monitored organizations
            cursor.execute("SELECT COUNT(*) as count FROM dev_organizations")
            org_row = cursor.fetchone()
            organizations_monitored = dict(org_row)['count'] if org_row else 0

            # Get latest waves
            cursor.execute("""
                SELECT wave_id FROM organization_launch_waves
                ORDER BY wave_detected_at DESC LIMIT 1
            """)
            wave_row = cursor.fetchone()
            latest_wave = dict(wave_row)['wave_id'] if wave_row else None

            # Get top launch candidates (highest master_launch_score)
            cursor.execute("""
                SELECT
                    mls.organization_id,
                    do_.operator_wallet,
                    mls.master_launch_score,
                    mls.alert_level,
                    do_.token_count,
                    do_.creator_count
                FROM master_launch_signals mls
                JOIN dev_organizations do_ ON mls.organization_id = do_.organization_id
                WHERE mls.alert_level IN ('CRITICAL', 'HIGH')
                ORDER BY mls.master_launch_score DESC
                LIMIT 10
            """)
            top_candidates = [dict(row) for row in cursor.fetchall()]

            conn.close()

            return {
                'critical_alerts': critical_alerts,
                'high_alerts': high_alerts,
                'organizations_monitored': organizations_monitored,
                'latest_wave_detected': latest_wave,
                'top_launch_candidates': top_candidates,
                'status': 'operational'
            }
        except Exception as e:
            logger.error(f"Error getting dashboard overview: {e}", exc_info=True)
            return {
                'error': str(e),
                'critical_alerts': 0,
                'high_alerts': 0,
                'organizations_monitored': 0,
                'latest_wave_detected': None,
                'top_launch_candidates': [],
                'status': 'error'
            }


class OrganizationService:
    """Provides organization intelligence data."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def get_all_organizations(self, limit: int = 100, min_score: float = 0.0) -> List[Dict]:
        """
        List all detected developer organizations.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    do_.organization_id,
                    do_.operator_wallet,
                    do_.cluster_size,
                    do_.wallet_count,
                    do_.creator_count,
                    do_.token_count,
                    do_.organization_score,
                    mls.master_launch_score,
                    mls.alert_level
                FROM dev_organizations do_
                LEFT JOIN master_launch_signals mls ON do_.organization_id = mls.organization_id
                WHERE do_.organization_score >= ?
                ORDER BY do_.organization_score DESC
                LIMIT ?
            """, (min_score, limit))

            result = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return result
        except Exception as e:
            logger.error(f"Error fetching organizations: {e}", exc_info=True)
            return []

    def get_organization_detail(self, org_id: int) -> Optional[Dict]:
        """
        Return complete intelligence profile for a developer organization.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Get org basic info
            cursor.execute("""
                SELECT * FROM dev_organizations WHERE organization_id = ?
            """, (org_id,))
            org_row = cursor.fetchone()
            if not org_row:
                conn.close()
                return None

            org = dict(org_row)

            # Get members
            cursor.execute("""
                SELECT member_address, member_type FROM dev_organization_members
                WHERE organization_id = ?
            """, (org_id,))
            members = [dict(row) for row in cursor.fetchall()]
            org['members'] = members

            # Get latest signals
            cursor.execute("""
                SELECT
                    launch_probability,
                    launch_wave_score,
                    seed_concentration,
                    funder_overlap_score,
                    organization_momentum,
                    creator_reuse_score,
                    operator_activity_score,
                    reputation_adjustment,
                    master_launch_score,
                    alert_level
                FROM master_launch_signals
                WHERE organization_id = ?
                LIMIT 1
            """, (org_id,))
            signal_row = cursor.fetchone()
            if signal_row:
                org['signals'] = dict(signal_row)
            else:
                org['signals'] = {}

            # Get risk score
            cursor.execute("""
                SELECT risk_score, rug_probability, confidence
                FROM org_risk_scores
                WHERE organization_id = ?
                LIMIT 1
            """, (org_id,))
            risk_row = cursor.fetchone()
            if risk_row:
                org['risk'] = dict(risk_row)
            else:
                org['risk'] = {}

            # Parse JSON arrays
            if 'token_list' in org and org['token_list']:
                org['tokens'] = json.loads(org['token_list'])
            else:
                org['tokens'] = []

            if 'creator_list' in org and org['creator_list']:
                org['creators'] = json.loads(org['creator_list'])
            else:
                org['creators'] = []

            if 'wallet_list' in org and org['wallet_list']:
                org['wallets'] = json.loads(org['wallet_list'])
            else:
                org['wallets'] = []

            conn.close()
            return org
        except Exception as e:
            logger.error(f"Error fetching organization detail: {e}", exc_info=True)
            return None


class LaunchService:
    """Provides launch prediction and wave data."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def get_launch_leaderboard(self, limit: int = 50) -> List[Dict]:
        """
        Return organizations ranked by master launch score.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    mls.organization_id,
                    do_.operator_wallet,
                    mls.master_launch_score,
                    mls.launch_probability,
                    mls.launch_wave_score,
                    mls.seed_concentration,
                    mls.funder_overlap_score,
                    mls.organization_momentum,
                    mls.creator_reuse_score,
                    mls.operator_activity_score,
                    mls.reputation_adjustment,
                    mls.alert_level,
                    mls.computed_at,
                    do_.token_count,
                    do_.creator_count
                FROM master_launch_signals mls
                JOIN dev_organizations do_ ON mls.organization_id = do_.organization_id
                ORDER BY mls.master_launch_score DESC
                LIMIT ?
            """, (limit,))

            result = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return result
        except Exception as e:
            logger.error(f"Error fetching launch leaderboard: {e}", exc_info=True)
            return []

    def get_launch_waves(self, limit: int = 50) -> List[Dict]:
        """
        Return currently detected coordinated launch preparation waves.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    olw.wave_id,
                    COUNT(DISTINCT olw.organization_id) as organization_count,
                    COUNT(DISTINCT lwc.creator_wallet) as creator_count,
                    AVG(olw.wave_score) as avg_wave_score,
                    olw.wave_type,
                    olw.wave_detected_at
                FROM organization_launch_waves olw
                LEFT JOIN launch_wave_creators lwc ON olw.wave_id = lwc.wave_id
                GROUP BY olw.wave_id
                ORDER BY olw.wave_detected_at DESC
                LIMIT ?
            """, (limit,))

            result = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return result
        except Exception as e:
            logger.error(f"Error fetching launch waves: {e}", exc_info=True)
            return []


class ClusterService:
    """Provides dev farm cluster data."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def get_dev_clusters(self, limit: int = 50) -> List[Dict]:
        """
        Return detected developer farm clusters.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    cluster_id,
                    cluster_strength,
                    node_count as wallet_count,
                    creator_count,
                    token_count,
                    average_rug_probability,
                    first_seen,
                    last_updated
                FROM dev_organizations
                WHERE cluster_id IS NOT NULL
                ORDER BY cluster_strength DESC
                LIMIT ?
            """, (limit,))

            result = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return result
        except Exception as e:
            logger.error(f"Error fetching dev clusters: {e}", exc_info=True)
            return []


class WalletService:
    """Provides wallet intelligence data."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def get_wallet_intelligence(self, wallet_address: str) -> Optional[Dict]:
        """
        Return intelligence profile for a wallet.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # Get organization membership
            cursor.execute("""
                SELECT organization_id, member_type FROM dev_organization_members
                WHERE member_address = ?
                LIMIT 1
            """, (wallet_address,))
            member_row = cursor.fetchone()

            org_id = None
            member_type = None
            if member_row:
                org_id = dict(member_row)['organization_id']
                member_type = dict(member_row)['member_type']

            # Get creator reputation if applicable
            reputation = None
            cursor.execute("""
                SELECT * FROM dev_reputation WHERE wallet = ? LIMIT 1
            """, (wallet_address,))
            rep_row = cursor.fetchone()
            if rep_row:
                reputation = dict(rep_row)

            # Get creator details
            cursor.execute("""
                SELECT * FROM token_analysis
                WHERE earliest_tx_creator = ?
                LIMIT 10
            """, (wallet_address,))
            tokens = [dict(row) for row in cursor.fetchall()]

            conn.close()

            return {
                'wallet_address': wallet_address,
                'organization_id': org_id,
                'member_type': member_type,
                'reputation': reputation,
                'tokens': tokens
            }
        except Exception as e:
            logger.error(f"Error fetching wallet intelligence: {e}", exc_info=True)
            return None


class SignalService:
    """Provides predictive signal data."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=60)
        conn.row_factory = sqlite3.Row
        return conn

    def get_organization_signals(self, org_id: int) -> Optional[Dict]:
        """
        Return detailed predictive signals for an organization.
        """
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    launch_probability,
                    launch_wave_score,
                    seed_concentration,
                    funder_overlap_score,
                    organization_momentum,
                    creator_reuse_score,
                    operator_activity_score,
                    reputation_adjustment,
                    master_launch_score,
                    alert_level,
                    computed_at
                FROM master_launch_signals
                WHERE organization_id = ?
                LIMIT 1
            """, (org_id,))

            row = cursor.fetchone()
            conn.close()

            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.error(f"Error fetching organization signals: {e}", exc_info=True)
            return None
