"""
Dev Intelligence Graph API

REST API endpoints for querying developer organizations, members, and tokens.

Endpoints (v1):
- GET /api/orgs — all organizations
- GET /api/orgs/<id>/members — organization members
- GET /api/orgs/operator/<wallet> — organization by operator wallet
- GET /api/orgs/<id>/tokens — tokens launched by organization
- GET /api/wallets/<wallet>/org — which organization a wallet belongs to

Endpoints (v2):
- GET /api/orgs/predictions — all orgs by launch probability
- GET /api/orgs/at-risk — orgs with high rug rate
- GET /api/orgs/<id>/prediction — launch probability prediction for org
- GET /api/orgs/<id>/reputation — reputation metrics for org

Endpoints (v3):
- GET /api/orgs/windows — all orgs with 3-window predictions
- GET /api/orgs/<id>/windows — 3-window prediction for single org
- GET /api/orgs/<id>/snapshots — time-series snapshots for org
- GET /api/orgs/<id>/risk — risk score with components
- GET /api/orgs/families — org family groupings
- GET /api/orgs/<id>/alerts — alerts for org
- GET /api/tokens/<mint>/outcome — token outcome prediction
"""

import sqlite3
import json
import logging
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

dev_intelligence_api = Blueprint('dev_intelligence_api', __name__, url_prefix='/api')
_DB_PATH = None


def get_db_conn(db_path=None):
    """Get database connection."""
    path = db_path or _DB_PATH
    if not path:
        raise RuntimeError("Database path not configured")
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


@dev_intelligence_api.route('/orgs', methods=['GET'])
def get_organizations():
    """
    Get all organizations sorted by score.

    Query params:
    - min_score: minimum organization score (0-1), default 0.3
    - limit: max results, default 50
    """
    try:
        min_score = float(request.args.get('min_score', 0.3))
        limit = int(request.args.get('limit', 50))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT organization_id, operator_wallet, cluster_size, wallet_count,
                   creator_count, token_count, token_list, organization_score,
                   betweenness_centrality, pagerank_score, total_volume_sol,
                   cluster_strength, detected_at
            FROM dev_organizations
            WHERE organization_score >= ?
            ORDER BY organization_score DESC
            LIMIT ?
        """, (min_score, limit))

        rows = cursor.fetchall()
        conn.close()

        result = []
        for row in rows:
            org = dict(row)
            # Parse JSON arrays
            org['tokens'] = json.loads(org['token_list']) if org['token_list'] else []
            org['creators'] = []  # Can be loaded if needed
            org['wallets'] = []   # Can be loaded if needed
            del org['token_list']
            result.append(org)

        return jsonify(result), 200

    except Exception as e:
        logger.error(f"Error fetching organizations: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================================================
# V2 ENDPOINTS: Launch Predictions & Organization Reputation
# ============================================================================

@dev_intelligence_api.route('/orgs/predictions', methods=['GET'])
def get_org_predictions():
    """
    Get all organizations sorted by launch_probability DESC.
    Returns the latest prediction for each org.

    Query params:
    - min_probability: minimum launch probability (0-100), default 30
    - limit: max results, default 50
    """
    try:
        min_prob = float(request.args.get('min_probability', 30))
        limit = int(request.args.get('limit', 50))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT olp.organization_id, do_.operator_wallet, do_.token_count,
                   do_.creator_count, olp.launch_probability, olp.prediction_date,
                   olp.signal_recency, olp.signal_scale, olp.signal_launch_rate,
                   olp.days_since_last_funding, olp.avg_rug_probability
            FROM org_launch_predictions olp
            JOIN dev_organizations do_ ON olp.organization_id = do_.organization_id
            WHERE olp.launch_probability >= ?
              AND olp.prediction_date = (
                  SELECT MAX(olp2.prediction_date)
                  FROM org_launch_predictions olp2
                  WHERE olp2.organization_id = olp.organization_id
              )
            ORDER BY olp.launch_probability DESC
            LIMIT ?
        """, (min_prob, limit))

        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        return jsonify(rows), 200

    except Exception as e:
        logger.error(f"Error fetching org predictions: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/at-risk', methods=['GET'])
def get_at_risk_orgs():
    """
    Get organizations with high rug_rate, sorted by rug_rate DESC.

    Query params:
    - min_rug_rate: minimum rug rate (0-1), default 0.5
    - limit: max results, default 50
    """
    try:
        min_rug = float(request.args.get('min_rug_rate', 0.5))
        limit = int(request.args.get('limit', 50))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT orep.organization_id, do_.operator_wallet, do_.token_count,
                   orep.total_tokens_launched, orep.rug_count, orep.rug_rate,
                   orep.success_rate, orep.reputation_score, orep.computed_at
            FROM org_reputation orep
            JOIN dev_organizations do_ ON orep.organization_id = do_.organization_id
            WHERE orep.rug_rate >= ?
            ORDER BY orep.rug_rate DESC
            LIMIT ?
        """, (min_rug, limit))

        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        return jsonify(rows), 200

    except Exception as e:
        logger.error(f"Error fetching at-risk orgs: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/<int:org_id>/prediction', methods=['GET'])
def get_org_prediction(org_id):
    """
    Get launch probability prediction for an organization with signal breakdown.
    Returns the most recent prediction for the org.
    """
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT olp.*, do_.operator_wallet, do_.token_count, do_.creator_count
            FROM org_launch_predictions olp
            JOIN dev_organizations do_ ON olp.organization_id = do_.organization_id
            WHERE olp.organization_id = ?
            ORDER BY olp.prediction_date DESC
            LIMIT 1
        """, (org_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Prediction not found for organization'}), 404

        return jsonify(dict(row)), 200

    except Exception as e:
        logger.error(f"Error fetching org prediction: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/<int:org_id>/reputation', methods=['GET'])
def get_org_reputation(org_id):
    """Get reputation metrics for an organization."""
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT orep.*, do_.operator_wallet
            FROM org_reputation orep
            JOIN dev_organizations do_ ON orep.organization_id = do_.organization_id
            WHERE orep.organization_id = ?
        """, (org_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Reputation not found for organization'}), 404

        return jsonify(dict(row)), 200

    except Exception as e:
        logger.error(f"Error fetching org reputation: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================================================
# V3 ENDPOINTS: Multi-Window Predictions, Snapshots, Risk, Families, Alerts, Tokens
# ============================================================================

@dev_intelligence_api.route('/orgs/windows', methods=['GET'])
def get_org_windows():
    """
    Get all organizations with 3-window launch predictions (24h, 72h, 7d).
    Returns latest prediction for each org.

    Query params:
    - min_prob_24h: minimum 24h probability (0-100), default 30
    - limit: max results, default 50
    """
    try:
        min_prob = float(request.args.get('min_prob_24h', 30))
        limit = int(request.args.get('limit', 50))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT olw.organization_id, do_.operator_wallet, do_.token_count,
                   do_.creator_count, olw.prob_launch_24h, olw.prob_launch_72h,
                   olw.prob_launch_7d, olw.prediction_date,
                   olw.signal_burst_24h, olw.signal_recency_24h, olw.signal_velocity_72h,
                   olw.signal_coordination_72h, olw.signal_reputation_7d
            FROM org_launch_windows olw
            JOIN dev_organizations do_ ON olw.organization_id = do_.organization_id
            WHERE olw.prob_launch_24h >= ?
              AND olw.prediction_date = (SELECT MAX(olw2.prediction_date)
                                         FROM org_launch_windows olw2
                                         WHERE olw2.organization_id = olw.organization_id)
            ORDER BY olw.prob_launch_24h DESC
            LIMIT ?
        """, (min_prob, limit))

        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        return jsonify(rows), 200

    except Exception as e:
        logger.error(f"Error fetching org windows: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/<int:org_id>/windows', methods=['GET'])
def get_org_window(org_id):
    """Get 3-window prediction for single organization."""
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM org_launch_windows
            WHERE organization_id = ?
            ORDER BY prediction_date DESC
            LIMIT 1
        """, (org_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Windows not found for organization'}), 404

        return jsonify(dict(row)), 200

    except Exception as e:
        logger.error(f"Error fetching org window: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/<int:org_id>/snapshots', methods=['GET'])
def get_org_snapshots(org_id):
    """
    Get time-series snapshots for organization.

    Query params:
    - days: number of past days, default 7
    """
    try:
        days = int(request.args.get('days', 7))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT snapshot_id, organization_id, snapshot_date, active_funders,
                   active_creators, burst_count, weighted_volume, graph_density,
                   launch_count, rug_count, computed_at
            FROM org_snapshots
            WHERE organization_id = ?
              AND snapshot_date >= date('now', ?)
            ORDER BY snapshot_date DESC
        """, (org_id, f'-{days} days'))

        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        return jsonify(rows), 200

    except Exception as e:
        logger.error(f"Error fetching org snapshots: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/<int:org_id>/risk', methods=['GET'])
def get_org_risk(org_id):
    """Get composite risk score with components for organization."""
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM org_risk_scores
            WHERE organization_id = ?
        """, (org_id,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Risk not found for organization'}), 404

        return jsonify(dict(row)), 200

    except Exception as e:
        logger.error(f"Error fetching org risk: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/families', methods=['GET'])
def get_org_families():
    """
    Get organization family groupings (connected components on relationship graph).

    Query params:
    - min_family_score: minimum family score (0-100), default 10
    - limit: max results, default 100
    """
    try:
        min_score = float(request.args.get('min_family_score', 10))
        limit = int(request.args.get('limit', 100))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT family_id, GROUP_CONCAT(organization_id) AS org_ids,
                   AVG(family_score) AS avg_family_score, MAX(family_score) AS max_family_score,
                   MAX(CASE WHEN organization_id = hub_org_id THEN 1 ELSE 0 END) AS hub_present,
                   MIN(hub_org_id) AS hub_org_id
            FROM org_families
            GROUP BY family_id
            HAVING avg_family_score >= ?
            ORDER BY max_family_score DESC
            LIMIT ?
        """, (min_score, limit))

        families = []
        for row in cursor.fetchall():
            families.append({
                'family_id': row[0],
                'organization_ids': [int(x) for x in row[1].split(',')],
                'avg_family_score': row[2],
                'max_family_score': row[3],
                'hub_org_id': row[5],
            })

        conn.close()

        return jsonify(families), 200

    except Exception as e:
        logger.error(f"Error fetching org families: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/<int:org_id>/alerts', methods=['GET'])
def get_org_alerts(org_id):
    """
    Get alerts for organization.

    Query params:
    - limit: max results, default 20
    - unacked_only: return only unacknowledged alerts (true/false), default false
    """
    try:
        limit = int(request.args.get('limit', 20))
        unacked_only = request.args.get('unacked_only', 'false').lower() == 'true'

        conn = get_db_conn()
        cursor = conn.cursor()

        if unacked_only:
            cursor.execute("""
                SELECT * FROM org_alerts
                WHERE organization_id = ?
                  AND acknowledged_at IS NULL
                ORDER BY created_at DESC
                LIMIT ?
            """, (org_id, limit))
        else:
            cursor.execute("""
                SELECT * FROM org_alerts
                WHERE organization_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (org_id, limit))

        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        return jsonify(rows), 200

    except Exception as e:
        logger.error(f"Error fetching org alerts: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/tokens/<mint>/outcome', methods=['GET'])
def get_token_outcome(mint):
    """Get outcome prediction for token (prob_rug, prob_2x, prob_10x)."""
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT * FROM token_outcome_predictions
            WHERE mint = ?
        """, (mint,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Token not found'}), 404

        return jsonify(dict(row)), 200

    except Exception as e:
        logger.error(f"Error fetching token outcome: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/<int:org_id>/members', methods=['GET'])
def get_org_members(org_id):
    """Get members of an organization with centrality scores."""
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        # Verify org exists
        cursor.execute("SELECT * FROM dev_organizations WHERE organization_id = ?", (org_id,))
        org_row = cursor.fetchone()
        if not org_row:
            conn.close()
            return jsonify({'error': 'Organization not found'}), 404

        org = dict(org_row)

        # Get members
        cursor.execute("""
            SELECT member_address, member_type, degree_centrality,
                   betweenness_centrality, pagerank_score, token_count,
                   total_volume_sol, role_confidence, detected_at
            FROM dev_organization_members
            WHERE organization_id = ?
            ORDER BY betweenness_centrality DESC
        """, (org_id,))

        members = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Parse JSON arrays
        org['tokens'] = json.loads(org['token_list']) if org['token_list'] else []
        org['creators'] = json.loads(org['creator_list']) if org['creator_list'] else []
        org['wallets'] = json.loads(org['wallet_list']) if org['wallet_list'] else []
        del org['token_list']
        del org['creator_list']
        del org['wallet_list']

        return jsonify({'organization': org, 'members': members}), 200

    except Exception as e:
        logger.error(f"Error fetching org members: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/operator/<wallet>', methods=['GET'])
def get_org_by_operator(wallet):
    """Get organization for a specific operator wallet."""
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM dev_organizations WHERE operator_wallet = ?", (wallet,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Organization not found'}), 404

        org = dict(row)
        org['tokens'] = json.loads(org['token_list']) if org['token_list'] else []
        org['creators'] = json.loads(org['creator_list']) if org['creator_list'] else []
        org['wallets'] = json.loads(org['wallet_list']) if org['wallet_list'] else []
        del org['token_list']
        del org['creator_list']
        del org['wallet_list']

        return jsonify(org), 200

    except Exception as e:
        logger.error(f"Error fetching org by operator: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/orgs/<int:org_id>/tokens', methods=['GET'])
def get_org_tokens(org_id):
    """Get tokens launched by an organization."""
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        # Verify org exists
        cursor.execute("SELECT organization_id FROM dev_organizations WHERE organization_id = ?", (org_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({'error': 'Organization not found'}), 404

        # Get tokens
        cursor.execute("""
            SELECT dom.member_address AS mint, ta.market_cap_highest,
                   ta.rug_probability, ta.risk_level, ta.created_at
            FROM dev_organization_members dom
            LEFT JOIN token_analysis ta ON dom.member_address = ta.mint
            WHERE dom.organization_id = ?
              AND dom.member_type = 'token'
            ORDER BY ta.market_cap_highest DESC NULLS LAST
        """, (org_id,))

        tokens = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify(tokens), 200

    except Exception as e:
        logger.error(f"Error fetching org tokens: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dev_intelligence_api.route('/wallets/<wallet>/org', methods=['GET'])
def get_wallet_org(wallet):
    """Get which organization a wallet belongs to."""
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT dom.organization_id, dom.member_type,
                   dom.degree_centrality, dom.betweenness_centrality,
                   dom.pagerank_score,
                   do_.operator_wallet, do_.organization_score,
                   do_.cluster_size, do_.token_count
            FROM dev_organization_members dom
            JOIN dev_organizations do_ ON dom.organization_id = do_.organization_id
            WHERE dom.member_address = ?
            LIMIT 1
        """, (wallet,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'error': 'Wallet not found in any organization'}), 404

        return jsonify(dict(row)), 200

    except Exception as e:
        logger.error(f"Error fetching wallet org: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


def register_dev_intelligence_api(app, db_path='database/flex_complete_database.db'):
    """Register the dev intelligence API blueprint with the Flask app."""
    global _DB_PATH
    _DB_PATH = db_path
    app.register_blueprint(dev_intelligence_api)
    logger.info(f"Dev intelligence API registered with DB path: {db_path}")
