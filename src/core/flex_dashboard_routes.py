"""
FLEX Intelligence Dashboard Routes

Flask routes for serving the dashboard UI pages.
Provides rendered HTML templates that consume the FLEX UI API endpoints.

Routes:
- GET / → main dashboard
- GET /launch-radar → launch prediction leaderboard
- GET /organization/<id> → organization detail page
- GET /launch-waves → wave detection timeline
- GET /dev-clusters → cluster visualization
- GET /wallet/<address> → wallet intelligence
"""

import logging
from flask import Blueprint, render_template, jsonify, request

logger = logging.getLogger(__name__)

dashboard_routes = Blueprint('dashboard', __name__, url_prefix='')


@dashboard_routes.route('/', methods=['GET'])
def dashboard_home():
    """
    Render main FLEX Intelligence Dashboard.
    Loads system overview and top alerts.
    """
    try:
        return render_template('flex_dashboard.html', page='dashboard')
    except Exception as e:
        logger.error(f"Error rendering dashboard: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/launch-radar', methods=['GET'])
def launch_radar():
    """
    Render Launch Radar page.
    Shows organizations ranked by master launch score with all signals.
    """
    try:
        return render_template('flex_dashboard.html', page='radar')
    except Exception as e:
        logger.error(f"Error rendering launch radar: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/organization/<int:org_id>', methods=['GET'])
def organization_detail(org_id):
    """
    Render Organization Intelligence page.
    Shows complete org profile with signals, members, risk scores.
    """
    try:
        return render_template(
            'flex_dashboard.html',
            page='organization',
            org_id=org_id
        )
    except Exception as e:
        logger.error(f"Error rendering organization detail: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/launch-waves', methods=['GET'])
def launch_waves_page():
    """
    Render Launch Waves page.
    Shows detected coordinated launch preparation waves.
    """
    try:
        return render_template('flex_dashboard.html', page='waves')
    except Exception as e:
        logger.error(f"Error rendering launch waves: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/dev-clusters', methods=['GET'])
def dev_clusters_page():
    """
    Render Dev Clusters page.
    Shows detected developer farm clusters.
    """
    try:
        return render_template('flex_dashboard.html', page='clusters')
    except Exception as e:
        logger.error(f"Error rendering dev clusters: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/wallet/<wallet_address>', methods=['GET'])
def wallet_intelligence(wallet_address):
    """
    Render Wallet Intelligence page.
    Shows wallet-level intelligence including creators funded and org membership.
    """
    try:
        return render_template(
            'flex_dashboard.html',
            page='wallet',
            wallet_address=wallet_address
        )
    except Exception as e:
        logger.error(f"Error rendering wallet intelligence: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/org-explorer', methods=['GET'])
def org_explorer():
    """
    Render Organization Explorer page.
    Searchable table of all detected organizations.
    """
    try:
        return render_template('flex_dashboard.html', page='org_explorer')
    except Exception as e:
        logger.error(f"Error rendering org explorer: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/signal-explorer', methods=['GET'])
def signal_explorer():
    """
    Render Signal Explorer page.
    Interactive visualization of all 8 predictive signals.
    """
    try:
        return render_template('flex_dashboard.html', page='signal_explorer')
    except Exception as e:
        logger.error(f"Error rendering signal explorer: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/fingerprint/<int:org_id>', methods=['GET'])
def developer_fingerprint(org_id):
    """
    Render Developer Fingerprint page.
    Shows behavioral patterns and similar organizations.
    """
    try:
        return render_template(
            'flex_dashboard.html',
            page='fingerprint',
            org_id=org_id
        )
    except Exception as e:
        logger.error(f"Error rendering developer fingerprint: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/api-reference', methods=['GET'])
def api_reference():
    """
    Render API Reference page.
    Comprehensive documentation of all FLEX UI API endpoints with examples.
    """
    try:
        return render_template('flex_dashboard.html', page='api_reference')
    except Exception as e:
        logger.error(f"Error rendering API reference: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/early-signals', methods=['GET'])
def early_signals_page():
    """
    Render Early Signal Predictions page (Phase 1).
    Shows tokens with early rug/runner predictions at 5-15 minutes.
    """
    try:
        return render_template('flex_dashboard.html', page='early_signals')
    except Exception as e:
        logger.error(f"Error rendering early signals: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


def register_dashboard_routes(app):
    """Register dashboard routes with Flask app."""
    app.register_blueprint(dashboard_routes)
    logger.info("[DASHBOARD] Dashboard routes registered successfully")
