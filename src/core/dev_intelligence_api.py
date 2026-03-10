"""
Dev Intelligence Graph API

REST API endpoints for querying developer organizations, members, and tokens.

Endpoints:
- GET /api/orgs — all organizations
- GET /api/orgs/<id>/members — organization members
- GET /api/orgs/operator/<wallet> — organization by operator wallet
- GET /api/orgs/<id>/tokens — tokens launched by organization
- GET /api/wallets/<wallet>/org — which organization a wallet belongs to
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
