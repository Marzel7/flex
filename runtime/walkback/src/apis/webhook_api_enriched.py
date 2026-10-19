"""
FLEX Webhook-Enhanced API Endpoints
Serves creator risk scores alongside webhook activity data

Integrates webhook_creator_ranker into the existing API responses

Author: Claude Code
Date: 2026-03-03
"""

from flask import jsonify
import sqlite3
from src.utils.db_locking import db_connect
import os
from datetime import datetime
from src.core.webhook_creator_ranker import (
    enrich_creator_with_risk_score,
    get_top_risk_creators,
    get_ranker_db,
)


DB_PATH = os.getenv("FLEX_DB_PATH", "flex_complete_database.db")


# ============================================================================
# ENRICHED ENDPOINTS
# ============================================================================

def get_creator_recent_checks_enriched(limit: int = 15):
    """
    Enhanced version of /api/creator-recent-checks with risk scores.

    Returns same structure as original, plus:
    - risk_score: Numerical score
    - risk_level: critical/elevated/moderate/low
    - component_scores: Breakdown by factor
    - risk_reasons: Why this score

    Sorted by: risk_score DESC (highest risk first)
    """
    try:
        conn = db_connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get creators from sol_transfers (most recently extracted)
        cursor.execute("""
            SELECT DISTINCT source as creator_address
            FROM sol_transfers
            ORDER BY received_at DESC
            UNION
            SELECT DISTINCT destination as creator_address
            FROM sol_transfers
            ORDER BY received_at DESC
        """)

        addresses = list(set([row[0] for row in cursor.fetchall()]))[:limit]

        recent_checks = []

        for creator in addresses:
            # Get token count
            cursor.execute("""
                SELECT COUNT(*) as token_count FROM token_analysis
                WHERE earliest_tx_creator = ?
            """, (creator,))
            token_count = cursor.fetchone()['token_count'] or 0

            # Get funder count
            cursor.execute("""
                SELECT COUNT(DISTINCT funder_address) as funder_count
                FROM creator_funders
                WHERE creator_address = ?
            """, (creator,))
            funder_count = cursor.fetchone()['funder_count'] or 0

            # Get outgoing transfer count and latest scan time
            cursor.execute("""
                SELECT COUNT(*) as outgoing_count, MAX(received_at) as latest_scan
                FROM sol_transfers
                WHERE source = ?
            """, (creator,))
            result = cursor.fetchone()
            outgoing_count = result['outgoing_count'] or 0
            latest_scan = result['latest_scan']

            # Convert to datetime string
            last_scanned = None
            if latest_scan:
                try:
                    last_scanned = datetime.fromisoformat(latest_scan).strftime('%Y-%m-%d %H:%M:%S')
                except:
                    last_scanned = str(latest_scan)

            # Get funding chain count
            cursor.execute("""
                SELECT COUNT(*) as chain_count FROM funding_chains
                WHERE source_creator = ? OR dest_creator = ?
            """, (creator, creator))
            chain_count = cursor.fetchone()['chain_count'] or 0

            # Build findings (traditional)
            findings = []

            # Self-funding check
            cursor.execute("""
                SELECT is_self_funding, self_funding_intermediates, total_funders
                FROM creator_self_funding
                WHERE creator_address = ?
            """, (creator,))
            self_fund_row = cursor.fetchone()
            if self_fund_row and self_fund_row['is_self_funding']:
                self_fund_count = self_fund_row['self_funding_intermediates'] or 0
                total_funders = self_fund_row['total_funders'] or 1
                pct = (self_fund_count / total_funders * 100) if total_funders > 0 else 0
                findings.append(f'🚩 SELF-FUNDING ({pct:.0f}%)')

            # Distribution pattern
            if outgoing_count > 0:
                cursor.execute("""
                    SELECT COUNT(DISTINCT destination) as recipient_count
                    FROM sol_transfers
                    WHERE source = ?
                """, (creator,))
                recipient_row = cursor.fetchone()
                recipient_count = recipient_row['recipient_count'] if recipient_row else 0

                if recipient_count > funder_count * 5 and funder_count < 20:
                    findings.append(f'⚠️ DISTRIBUTION_PATTERN')

            # Coordinated
            cursor.execute("""
                SELECT COUNT(*) as coordinated_count FROM coordinated_creator_edges
                WHERE creator_a = ? OR creator_b = ?
            """, (creator, creator))
            coordinated_count = cursor.fetchone()['coordinated_count'] or 0
            if coordinated_count > 0:
                findings.append('⚠️ COORDINATED')

            # C2C Network
            cursor.execute("""
                SELECT DISTINCT network_name FROM creator_to_creator_networks
                WHERE creator_address = ?
                LIMIT 1
            """, (creator,))
            c2c_network = cursor.fetchone()
            if c2c_network and c2c_network['network_name']:
                findings.append(f"⚠️ C2C_NETWORK")

            if not findings:
                findings.append('✅ CLEAN')

            # Build base creator data
            creator_data = {
                'creator_address': creator,
                'token_count': token_count,
                'funder_count': funder_count,
                'chain_count': chain_count,
                'outgoing_count': outgoing_count,
                'last_scanned': last_scanned,
                'networks': [],
                'findings': findings
            }

            # Enrich with risk scores
            ranker_conn = get_ranker_db()
            creator_data = enrich_creator_with_risk_score(ranker_conn, creator_data)
            ranker_conn.close()

            recent_checks.append(creator_data)

        # Sort by risk score (highest/riskiest first)
        recent_checks.sort(key=lambda x: x.get('risk_score', 0), reverse=True)

        conn.close()

        return jsonify({
            'recent_checks': recent_checks,
            'enriched': True,
            'sorted_by': 'risk_score DESC',
            'generated_at': datetime.utcnow().isoformat()
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def get_top_risk_creators_endpoint():
    """
    GET /api/creators/top-risk
    Get the highest-risk creators based on webhook activity and scoring.

    Returns:
        List of creators sorted by risk_score DESC
    """
    try:
        creators = get_top_risk_creators(limit=25)

        return jsonify({
            'top_risk_creators': creators,
            'count': len(creators),
            'sorted_by': 'risk_score DESC',
            'generated_at': datetime.utcnow().isoformat()
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def get_creator_risk_details(creator_address: str):
    """
    GET /api/creator/<address>/risk-details
    Get detailed risk score breakdown for a specific creator.

    Returns:
        - risk_score (numerical)
        - risk_level (categorical)
        - component_scores (breakdown)
        - risk_reasons (detailed explanations)
        - activity_stats (from sol_transfers)
        - token_stats
        - network_stats
    """
    try:
        conn = get_ranker_db()
        cursor = conn.cursor()

        # Get risk score
        from webhook_creator_ranker import compute_creator_risk_score
        risk_data = compute_creator_risk_score(conn, creator_address)

        # Get activity stats
        cursor.execute("""
            SELECT
                COUNT(*) as total_transfers,
                SUM(amount_sol) as total_sol,
                COUNT(DISTINCT source) as unique_sources,
                COUNT(DISTINCT destination) as unique_destinations,
                MIN(received_at) as first_seen,
                MAX(received_at) as last_seen
            FROM sol_transfers
            WHERE source = ? OR destination = ?
        """, (creator_address, creator_address))

        activity = cursor.fetchone()
        activity_stats = {
            'total_transfers': activity[0] or 0,
            'total_sol': activity[1] or 0.0,
            'unique_sources': activity[2] or 0,
            'unique_destinations': activity[3] or 0,
            'first_seen': activity[4],
            'last_seen': activity[5],
        } if activity else {}

        # Get token stats
        cursor.execute("""
            SELECT
                COUNT(DISTINCT mint) as total_tokens,
                SUM(CASE WHEN risk_level = 'critical' THEN 1 ELSE 0 END) as critical_tokens,
                SUM(CASE WHEN risk_level IN ('critical', 'high') THEN 1 ELSE 0 END) as risky_tokens
            FROM token_analysis
            WHERE earliest_tx_creator = ?
        """, (creator_address,))

        tokens = cursor.fetchone()
        token_stats = {
            'total_tokens': tokens[0] or 0,
            'critical_tokens': tokens[1] or 0,
            'risky_tokens': tokens[2] or 0,
        } if tokens else {}

        # Get network stats
        cursor.execute("""
            SELECT
                COUNT(DISTINCT network_id) as funding_networks,
                COUNT(DISTINCT network_name) as c2c_networks,
                COUNT(*) as coordinated_edges
            FROM (
                SELECT DISTINCT network_id, NULL as network_name FROM funding_network_members WHERE funder_address = ?
                UNION
                SELECT NULL, DISTINCT network_name FROM creator_to_creator_networks WHERE creator_address = ?
                UNION
                SELECT NULL, NULL FROM coordinated_creator_edges WHERE creator_a = ? OR creator_b = ?
            )
        """, (creator_address, creator_address, creator_address, creator_address))

        networks = cursor.fetchone()
        network_stats = {
            'funding_networks': networks[0] or 0,
            'c2c_networks': networks[1] or 0,
            'coordinated_edges': networks[2] or 0,
        } if networks else {}

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'risk_score': risk_data['score'],
            'risk_level': risk_data['risk_level'],
            'component_scores': risk_data['component_scores'],
            'risk_reasons': risk_data['reasons'],
            'activity_stats': activity_stats,
            'token_stats': token_stats,
            'network_stats': network_stats,
            'computed_at': risk_data['computed_at'],
        }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# FLASK APP INTEGRATION
# ============================================================================

def setup_enriched_routes(app):
    """
    Register enriched webhook API endpoints with Flask app.

    Call during app initialization:
        setup_enriched_routes(app)
    """

    @app.route('/api/creator-recent-checks/enriched', methods=['GET'])
    def creator_recent_checks_enriched():
        """GET /api/creator-recent-checks/enriched - Recent creators with risk scores"""
        return get_creator_recent_checks_enriched(limit=15)

    @app.route('/api/creators/top-risk', methods=['GET'])
    def top_risk_creators():
        """GET /api/creators/top-risk - Highest-risk creators"""
        return get_top_risk_creators_endpoint()

    @app.route('/api/creator/<creator_address>/risk-details', methods=['GET'])
    def creator_risk_details(creator_address):
        """GET /api/creator/<address>/risk-details - Detailed risk breakdown"""
        return get_creator_risk_details(creator_address)

    print("[WEBHOOK_API] Enriched routes registered: /creator-recent-checks/enriched, /creators/top-risk, /creator/<>/risk-details", flush=True)
