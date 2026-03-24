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
import sqlite3
import time
from flask import Blueprint, render_template, jsonify, request, make_response

logger = logging.getLogger(__name__)

dashboard_routes = Blueprint('dashboard', __name__, url_prefix='')


def no_cache_json(data):
    """Return JSON response with no-cache headers."""
    response = make_response(jsonify(data))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


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



@dashboard_routes.route('/api/token-behaviour', methods=['GET'])
def api_token_behaviour():
    """
    Get classified tokens by behaviour category.

    Query params:
    - category: Filter by category (immediate_rug, rug, slow_rug, runner, choppy_runner, insufficient_history, unknown)
    - min_confidence: Minimum confidence threshold (0-1)
    - min_snapshots: Minimum snapshot count for data quality (default 8, early classification tier)
    - limit: Max results (default 100)

    Returns: {"tokens": [...], "total": N, "category_filter": "...", "min_confidence": N, "min_snapshots": N}
    """
    try:
        category = request.args.get('category', None)
        min_confidence = float(request.args.get('min_confidence', 0.0))
        min_snapshots = int(request.args.get('min_snapshots', 8))
        limit = int(request.args.get('limit', 100))

        conn = sqlite3.connect('database/flex_complete_database.db')
        conn.row_factory = sqlite3.Row

        query = "SELECT * FROM token_behavior WHERE 1=1"
        params = []

        if category and category != 'all':
            query += " AND category = ?"
            params.append(category)

        if min_confidence > 0:
            query += " AND confidence >= ?"
            params.append(min_confidence)

        query += " AND snapshot_count >= ?"
        params.append(min_snapshots)

        query += " ORDER BY confidence DESC, classified_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        conn.close()

        tokens = []
        for row in rows:
            tokens.append({
                'mint': row['mint'],
                'category': row['category'],
                'confidence': round(row['confidence'], 3),
                'max_return_multiple': row['max_return_multiple'],
                'drawdown_from_peak': round(row['drawdown_from_peak'], 3) if row['drawdown_from_peak'] else None,
                'snapshot_count': row['snapshot_count'],
                'lifetime_secs': row['lifetime_secs'],
                'classified_at': row['classified_at'],
            })

        return no_cache_json({
            'tokens': tokens,
            'total': len(tokens),
            'category_filter': category,
            'min_confidence': min_confidence,
            'min_snapshots': min_snapshots,
        })

    except Exception as e:
        logger.error(f"Error fetching token behaviour: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/api/token-behaviour/<mint>', methods=['GET'])
def api_token_behaviour_detail(mint):
    """
    Get detailed behaviour classification for a specific token.
    
    Returns: {
        "mint": "...",
        "category": "...",
        "confidence": 0.85,
        "features": {
            "initial_price_usd": 0.001,
            "peak_price_usd": 0.010,
            ...
        },
        "history": [
            {"category": "...", "confidence": ..., "classified_at": ...}
        ]
    }
    """
    try:
        conn = sqlite3.connect('database/flex_complete_database.db')
        conn.row_factory = sqlite3.Row
        
        # Get current classification
        row = conn.execute(
            "SELECT * FROM token_behavior WHERE mint = ?",
            (mint,)
        ).fetchone()
        
        if not row:
            conn.close()
            return jsonify({'error': 'Token not classified', 'mint': mint}), 404
        
        # Get history
        history_rows = conn.execute(
            "SELECT category, confidence, classified_at FROM token_behavior_history WHERE mint = ? ORDER BY classified_at DESC LIMIT 10",
            (mint,)
        ).fetchall()
        
        conn.close()
        
        return jsonify({
            'mint': row['mint'],
            'category': row['category'],
            'confidence': round(row['confidence'], 3),
            'features': {
                'initial_price_usd': row['initial_price_usd'],
                'peak_price_usd': row['peak_price_usd'],
                'latest_price_usd': row['latest_price_usd'],
                'max_return_multiple': row['max_return_multiple'],
                'drawdown_from_peak': round(row['drawdown_from_peak'], 3) if row['drawdown_from_peak'] else None,
                'recovery_ratio': round(row['recovery_ratio'], 3) if row['recovery_ratio'] else None,
                'time_to_peak_secs': row['time_to_peak_secs'],
                'lifetime_secs': row['lifetime_secs'],
                'snapshot_count': row['snapshot_count'],
                'volatility': round(row['volatility'], 3) if row['volatility'] else None,
                'slope_early': round(row['slope_early'], 6) if row['slope_early'] else None,
                'slope_total': round(row['slope_total'], 6) if row['slope_total'] else None,
            },
            'history': [
                {
                    'category': h['category'],
                    'confidence': round(h['confidence'], 3),
                    'classified_at': h['classified_at'],
                }
                for h in history_rows
            ],
            'classified_at': row['classified_at'],
            'created_at': row['created_at'],
        })
    
    except Exception as e:
        logger.error(f"Error fetching token behaviour detail: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/api/token-behaviour/stats/summary', methods=['GET'])
def api_token_behaviour_stats():
    """
    Get summary statistics on token behaviour classifications.
    
    Returns: {
        "total_classified": N,
        "by_category": {
            "immediate_rug": {"count": N, "avg_confidence": 0.7, "pct": 2.1},
            ...
        }
    }
    """
    try:
        conn = sqlite3.connect('database/flex_complete_database.db')
        
        # Get summary by category
        rows = conn.execute("""
            SELECT 
                category,
                COUNT(*) as count,
                ROUND(AVG(confidence), 3) as avg_confidence
            FROM token_behavior
            GROUP BY category
            ORDER BY count DESC
        """).fetchall()
        
        total = sum(row[1] for row in rows)
        
        by_category = {}
        for row in rows:
            cat, count, avg_conf = row
            by_category[cat] = {
                'count': count,
                'avg_confidence': avg_conf,
                'pct': round(100.0 * count / total, 1) if total > 0 else 0,
            }
        
        conn.close()

        return no_cache_json({
            'total_classified': total,
            'by_category': by_category,
            'last_updated': int(time.time()),
        })

    except Exception as e:
        logger.error(f"Error fetching token behaviour stats: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/token-behaviour', methods=['GET'])
def token_behaviour_page():
    """
    Render Token Behaviour page.
    Shows tokens classified by their historical price behaviour.
    """
    try:
        return render_template('flex_dashboard.html', page='token_behaviour')
    except Exception as e:
        logger.error(f"Error rendering token behaviour page: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

def register_dashboard_routes(app):
    """Register dashboard routes with Flask app."""
    app.register_blueprint(dashboard_routes)
    logger.info("[DASHBOARD] Dashboard routes registered successfully")
