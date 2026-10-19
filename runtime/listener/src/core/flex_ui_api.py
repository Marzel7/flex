"""
FLEX UI API Layer

REST API endpoints for the FLEX intelligence dashboard.

Provides access to:
- Dashboard overview and alerts
- Organization intelligence
- Launch predictions and waves
- Dev farm clusters
- Wallet intelligence
- Predictive signals

Endpoint Patterns:
- GET /api/dashboard — System overview
- GET /api/launch-leaderboard — Top launch predictions
- GET /api/organizations — All organizations
- GET /api/organization/<id> — Single organization detail
- GET /api/launch-waves — Detected waves
- GET /api/dev-clusters — Farm clusters
- GET /api/wallet/<address> — Wallet intelligence
- GET /api/signals/<org_id> — Predictive signals
"""

import logging
from flask import Blueprint, request, jsonify
from src.core.flex_ui_services import (
    DashboardService,
    OrganizationService,
    LaunchService,
    ClusterService,
    WalletService,
    SignalService
)

logger = logging.getLogger(__name__)

flex_ui_api = Blueprint('flex_ui_api', __name__, url_prefix='/api')
_DB_PATH = None


def register_flex_ui_api(app, db_path: str):
    """Register the FLEX UI API blueprint with Flask app."""
    global _DB_PATH
    _DB_PATH = db_path
    app.register_blueprint(flex_ui_api)


# ============================================================================
# Dashboard Endpoints
# ============================================================================

@flex_ui_api.route('/dashboard', methods=['GET'])
def get_dashboard():
    """
    GET /api/dashboard

    Return high-level system status and top alerts.

    Response:
    {
        "critical_alerts": 5,
        "high_alerts": 18,
        "organizations_monitored": 4217,
        "latest_wave_detected": "wave_82",
        "top_launch_candidates": [
            {
                "organization_id": 123,
                "operator_wallet": "wallet_abc",
                "master_launch_score": 0.85,
                "alert_level": "CRITICAL",
                "token_count": 12,
                "creator_count": 4
            }
        ],
        "status": "operational"
    }
    """
    try:
        service = DashboardService(_DB_PATH)
        data = service.get_dashboard_overview()
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Dashboard error: {e}", exc_info=True)
        return jsonify({'error': str(e), 'status': 'error'}), 500


@flex_ui_api.route('/dashboard/recent-tokens', methods=['GET'])
def get_dashboard_recent_tokens():
    """GET /api/dashboard/recent-tokens — Most recently seen tokens."""
    try:
        limit = int(request.args.get('limit', 25))
        service = DashboardService(_DB_PATH)
        data = service.get_recent_tokens(limit=limit)
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Recent tokens error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Launch Leaderboard Endpoints
# ============================================================================

@flex_ui_api.route('/launch-leaderboard', methods=['GET'])
def get_launch_leaderboard():
    """
    GET /api/launch-leaderboard

    Return organizations ranked by master launch score.

    Query params:
    - limit: max results (default 50)

    Response:
    [
        {
            "organization_id": 123,
            "operator_wallet": "wallet_abc",
            "master_launch_score": 0.85,
            "launch_probability": 0.82,
            "launch_wave_score": 0.71,
            "seed_concentration": 0.94,
            "funder_overlap_score": 0.79,
            "organization_momentum": 0.68,
            "creator_reuse_score": 0.61,
            "operator_activity_score": 0.73,
            "reputation_adjustment": 0.44,
            "alert_level": "CRITICAL",
            "token_count": 12,
            "creator_count": 4
        }
    ]
    """
    try:
        limit = int(request.args.get('limit', 50))
        service = LaunchService(_DB_PATH)
        data = service.get_launch_leaderboard(limit=limit)
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Launch leaderboard error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Organization Endpoints
# ============================================================================

@flex_ui_api.route('/organizations', methods=['GET'])
def get_organizations():
    """
    GET /api/organizations

    List all detected developer organizations.

    Query params:
    - limit: max results (default 100)
    - min_score: minimum organization score 0-1 (default 0.0)

    Response:
    [
        {
            "organization_id": 123,
            "operator_wallet": "wallet_abc",
            "cluster_size": 18,
            "wallet_count": 42,
            "creator_count": 8,
            "token_count": 15,
            "organization_score": 0.72,
            "master_launch_score": 0.68,
            "alert_level": "HIGH"
        }
    ]
    """
    try:
        limit = int(request.args.get('limit', 100))
        min_score = float(request.args.get('min_score', 0.0))
        service = OrganizationService(_DB_PATH)
        data = service.get_all_organizations(limit=limit, min_score=min_score)
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Organizations error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@flex_ui_api.route('/organization/<int:org_id>', methods=['GET'])
def get_organization_detail(org_id):
    """
    GET /api/organization/<organization_id>

    Return complete intelligence profile for a developer organization.

    Response:
    {
        "organization_id": 123,
        "operator_wallet": "wallet_abc",
        "cluster_size": 18,
        "wallet_count": 42,
        "creator_count": 8,
        "token_count": 15,
        "organization_score": 0.72,
        "members": [
            {
                "member_address": "addr1",
                "member_type": "creator"
            }
        ],
        "signals": {
            "launch_probability": 0.82,
            "launch_wave_score": 0.71,
            "seed_concentration": 0.94,
            "funder_overlap_score": 0.79,
            "organization_momentum": 0.68,
            "creator_reuse_score": 0.61,
            "operator_activity_score": 0.73,
            "reputation_adjustment": 0.44,
            "master_launch_score": 0.85,
            "alert_level": "CRITICAL"
        },
        "risk": {
            "risk_score": 72,
            "rug_probability": 0.65,
            "confidence": 0.89
        },
        "tokens": ["mint1", "mint2"],
        "creators": ["creator1", "creator2"],
        "wallets": ["wallet1", "wallet2"]
    }
    """
    try:
        service = OrganizationService(_DB_PATH)
        data = service.get_organization_detail(org_id)
        if not data:
            return jsonify({'error': 'Organization not found'}), 404
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Organization detail error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Launch Wave Endpoints
# ============================================================================

@flex_ui_api.route('/launch-waves', methods=['GET'])
def get_launch_waves():
    """
    GET /api/launch-waves

    Return currently detected coordinated launch preparation waves.

    Query params:
    - limit: max results (default 50)

    Response:
    [
        {
            "wave_id": "wave_72",
            "organization_count": 4,
            "creator_count": 7,
            "avg_wave_score": 0.79,
            "wave_type": "pump_fun",
            "wave_detected_at": 1709000000
        }
    ]
    """
    try:
        limit = int(request.args.get('limit', 50))
        service = LaunchService(_DB_PATH)
        data = service.get_launch_waves(limit=limit)
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Launch waves error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Dev Farm Cluster Endpoints
# ============================================================================

@flex_ui_api.route('/dev-clusters', methods=['GET'])
def get_dev_clusters():
    """
    GET /api/dev-clusters

    Return detected developer farm clusters.

    Query params:
    - limit: max results (default 50)

    Response:
    [
        {
            "cluster_id": "cluster_22",
            "cluster_strength": 0.85,
            "wallet_count": 18,
            "creator_count": 7,
            "token_count": 13,
            "average_rug_probability": 0.72,
            "first_seen": 1708900000,
            "last_updated": 1709000000
        }
    ]
    """
    try:
        limit = int(request.args.get('limit', 50))
        service = ClusterService(_DB_PATH)
        data = service.get_dev_clusters(limit=limit)
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Dev clusters error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Wallet Intelligence Endpoints
# ============================================================================

@flex_ui_api.route('/wallet/<wallet_address>', methods=['GET'])
def get_wallet_intelligence(wallet_address):
    """
    GET /api/wallet/<wallet_address>

    Return intelligence profile for a wallet.

    Response:
    {
        "wallet_address": "wallet_abc",
        "organization_id": 123,
        "member_type": "creator",
        "reputation": {
            "wallet": "wallet_abc",
            "tokens_launched": 12,
            "rug_rate": 0.25,
            "success_rate": 0.42,
            "reputation_score": 0.59
        },
        "tokens": [
            {
                "mint": "mint1",
                "earliest_tx_creator": "wallet_abc",
                "rug_probability": 0.35,
                "created_at": 1708900000
            }
        ]
    }
    """
    try:
        service = WalletService(_DB_PATH)
        data = service.get_wallet_intelligence(wallet_address)
        if not data:
            return jsonify({'error': 'Wallet not found'}), 404
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Wallet intelligence error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Signal Endpoints
# ============================================================================

@flex_ui_api.route('/signals/<int:org_id>', methods=['GET'])
def get_organization_signals(org_id):
    """
    GET /api/signals/<organization_id>

    Return detailed predictive signals used for launch scoring.

    Response:
    {
        "launch_probability": 0.82,
        "launch_wave_score": 0.71,
        "seed_concentration": 0.94,
        "funder_overlap_score": 0.79,
        "organization_momentum": 0.68,
        "creator_reuse_score": 0.61,
        "operator_activity_score": 0.73,
        "reputation_adjustment": 0.44,
        "master_launch_score": 0.85,
        "alert_level": "CRITICAL",
        "computed_at": 1709000000
    }
    """
    try:
        service = SignalService(_DB_PATH)
        data = service.get_organization_signals(org_id)
        if not data:
            return jsonify({'error': 'Signals not found for organization'}), 404
        return jsonify(data), 200
    except Exception as e:
        logger.error(f"Organization signals error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500
