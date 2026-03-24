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
    - category: Filter by category (immediate_rug, runner, faded_runner, choppy_runner, rug, slow_rug, insufficient_history, unknown)
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
                'price_observed_start': round(row['initial_price_observed_usd'], 8) if row['initial_price_observed_usd'] else None,
                'price_robust_start': round(row['initial_price_robust_usd'], 8) if row['initial_price_robust_usd'] else None,
                'price_peak': round(row['peak_price_usd'], 8) if row['peak_price_usd'] else None,
                'max_return_observed': row['max_return_multiple_observed'],
                'max_return_robust': row['max_return_multiple'],
                'drawdown_from_peak': round(row['drawdown_from_peak'], 3) if row['drawdown_from_peak'] else None,
                'snapshot_count': row['snapshot_count'],
                'lifetime_secs': row['lifetime_secs'],
                'tracking_quality': row['tracking_quality'],
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
        "features": {...},
        "vault": {
            "validation_status": "validated",
            "discovery_method": "rpc",
            "discovery_secs": 35,
            "created_at": 1710000000,
            "last_validation_at": 1710000035
        },
        "history": [...]
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
        
        # Get vault discovery info from token_pool_accounts
        vault_row = conn.execute(
            "SELECT created_at, vault_validation_status, discovery_method, "
            "last_vault_validation_at FROM token_pool_accounts WHERE mint = ? "
            "ORDER BY created_at ASC LIMIT 1",
            (mint,)
        ).fetchone()

        # Get first recorded market cap from price snapshots
        first_market_cap_row = conn.execute(
            "SELECT market_cap FROM token_price_snapshots WHERE mint = ? "
            "ORDER BY captured_at ASC LIMIT 1",
            (mint,)
        ).fetchone()

        # Get peak market cap from price snapshots (more reliable than token_analysis)
        peak_market_cap_row = conn.execute(
            "SELECT market_cap, captured_at FROM token_price_snapshots WHERE mint = ? "
            "ORDER BY market_cap DESC LIMIT 1",
            (mint,)
        ).fetchone()

        # Get history
        history_rows = conn.execute(
            "SELECT category, confidence, classified_at FROM token_behavior_history WHERE mint = ? ORDER BY classified_at DESC LIMIT 10",
            (mint,)
        ).fetchall()

        conn.close()
        
        # Build vault metadata
        vault_metadata = None
        if vault_row:
            vault_discovery_secs = None
            if (vault_row['vault_validation_status'] == 'validated' 
                and vault_row['created_at'] 
                and vault_row['last_vault_validation_at']):
                vault_discovery_secs = max(0, vault_row['last_vault_validation_at'] - vault_row['created_at'])
            
            vault_metadata = {
                'validation_status': vault_row['vault_validation_status'],
                'discovery_method': vault_row['discovery_method'] or 'unknown',
                'discovery_secs': vault_discovery_secs,
                'created_at': vault_row['created_at'],
                'last_validation_at': vault_row['last_vault_validation_at']
            }
        
        return jsonify({
            'mint': row['mint'],
            'category': row['category'],
            'confidence': round(row['confidence'], 3),
            'tracking_quality': row['tracking_quality'],
            'prices': {
                'observed_start': round(row['initial_price_observed_usd'], 8) if row['initial_price_observed_usd'] else None,
                'robust_start': round(row['initial_price_robust_usd'], 8) if row['initial_price_robust_usd'] else None,
                'peak': round(row['peak_price_usd'], 8) if row['peak_price_usd'] else None,
                'latest': round(row['latest_price_usd'], 8) if row['latest_price_usd'] else None,
            },
            'market_cap': {
                'first': first_market_cap_row['market_cap'] if first_market_cap_row and first_market_cap_row['market_cap'] else None,
                'peak': peak_market_cap_row['market_cap'] if peak_market_cap_row and peak_market_cap_row['market_cap'] else None,
                'peak_at': peak_market_cap_row['captured_at'] * 1000 if peak_market_cap_row and peak_market_cap_row['captured_at'] else None,
            },
            'returns': {
                'max_observed': row['max_return_multiple_observed'],
                'max_robust': row['max_return_multiple'],
            },
            'features': {
                'drawdown_from_peak': round(row['drawdown_from_peak'], 3) if row['drawdown_from_peak'] else None,
                'recovery_ratio': round(row['recovery_ratio'], 3) if row['recovery_ratio'] else None,
                'time_to_peak_secs': row['time_to_peak_secs'],
                'lifetime_secs': row['lifetime_secs'],
                'snapshot_count': row['snapshot_count'],
                'volatility': round(row['volatility'], 3) if row['volatility'] else None,
                'slope_early': round(row['slope_early'], 6) if row['slope_early'] else None,
                'slope_total': round(row['slope_total'], 6) if row['slope_total'] else None,
            },
            'vault': vault_metadata,
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


@dashboard_routes.route('/vaults', methods=['GET'])
def vaults_page():
    """
    Render Vaults Discovery page.
    Shows vault discovery, validation, and latency information.
    """
    try:
        return render_template('flex_dashboard.html', page='vaults')
    except Exception as e:
        logger.error(f"Error rendering vaults page: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/api/vaults/stats/summary', methods=['GET'])
def api_vaults_stats():
    """
    Get summary statistics on vault discovery.

    Returns: {
        "total_vaults": N,
        "validated": N,
        "pending": N,
        "rejected": N,
        "avg_discovery_time_secs": X,
        "avg_discovery_attempts": X,
        "pct_possibly_late": X,
        "pct_likely_late": X
    }
    """
    try:
        conn = sqlite3.connect('database/flex_complete_database.db')
        conn.row_factory = sqlite3.Row

        # Get vault counts by status
        stats = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN vault_validation_status = 'validated' THEN 1 ELSE 0 END) as validated,
                SUM(CASE WHEN vault_validation_status = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN vault_validation_status = 'rejected' THEN 1 ELSE 0 END) as rejected,
                AVG(CASE WHEN vault_discovery_time_secs IS NOT NULL THEN vault_discovery_time_secs
                         ELSE (last_vault_validation_at - created_at) END) as avg_discovery_time,
                AVG(COALESCE(vault_discovery_attempts, 0)) as avg_attempts
            FROM token_pool_accounts
        """).fetchone()

        # Get tracking quality percentages
        quality_stats = conn.execute("""
            SELECT
                COUNT(DISTINCT tpa.mint) as total_with_tracking,
                SUM(CASE WHEN tb.tracking_quality = 'possibly_late' THEN 1 ELSE 0 END) as possibly_late,
                SUM(CASE WHEN tb.tracking_quality = 'likely_late' THEN 1 ELSE 0 END) as likely_late
            FROM token_pool_accounts tpa
            LEFT JOIN token_behavior tb ON tpa.mint = tb.mint
            WHERE tpa.vault_validation_status = 'validated'
        """).fetchone()

        conn.close()

        total = stats['total'] or 0
        total_tracking = quality_stats['total_with_tracking'] or 0

        return no_cache_json({
            'total_vaults': total,
            'validated': stats['validated'] or 0,
            'pending': stats['pending'] or 0,
            'rejected': stats['rejected'] or 0,
            'avg_discovery_time_secs': round(stats['avg_discovery_time'], 1) if stats['avg_discovery_time'] else None,
            'avg_discovery_attempts': round(stats['avg_attempts'], 1) if stats['avg_attempts'] else None,
            'pct_possibly_late': round(100.0 * (quality_stats['possibly_late'] or 0) / total_tracking, 1) if total_tracking > 0 else 0,
            'pct_likely_late': round(100.0 * (quality_stats['likely_late'] or 0) / total_tracking, 1) if total_tracking > 0 else 0,
        })

    except Exception as e:
        logger.error(f"Error fetching vaults stats: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/api/vaults', methods=['GET'])
def api_vaults_list():
    """
    Get list of vaults with discovery and token information.

    Query params:
    - status: Filter by validation status (validated, pending, rejected)
    - strategy: Filter by discovery strategy
    - mint: Search by token mint
    - pool: Search by pool address
    - tracking_quality: Filter by tracking quality (good, possibly_late, likely_late)
    - category: Filter by token behaviour category
    - sort_by: Sort field (discovery_time, attempts, category) - default discovery_time
    - limit: Max results (default 100)
    """
    try:
        status = request.args.get('status', None)
        strategy = request.args.get('strategy', None)
        mint = request.args.get('mint', None)
        pool = request.args.get('pool', None)
        tracking_quality = request.args.get('tracking_quality', None)
        category = request.args.get('category', None)
        sort_by = request.args.get('sort_by', 'discovery_time')
        limit = int(request.args.get('limit', 100))

        conn = sqlite3.connect('database/flex_complete_database.db')
        conn.row_factory = sqlite3.Row

        # Build query
        query = """
            SELECT
                tpa.mint,
                tpa.pool_address,
                tpa.base_account,
                tpa.quote_account,
                tpa.vault_validation_status,
                tpa.vault_resolution_state,
                tpa.vault_discovery_strategy,
                tpa.discovery_method,
                tpa.vault_discovery_attempts,
                COALESCE(tpa.vault_discovery_time_secs, (tpa.last_vault_validation_at - tpa.created_at)) as vault_discovery_time_secs,
                tpa.created_at,
                tpa.last_vault_validation_at,
                tpa.vault_resolved_at,
                tb.tracking_quality,
                tb.initial_price_observed_usd,
                tb.initial_price_robust_usd,
                tb.peak_price_usd,
                tb.latest_price_usd,
                tb.max_return_multiple_observed,
                tb.max_return_multiple,
                tb.category,
                tb.confidence
            FROM token_pool_accounts tpa
            LEFT JOIN token_behavior tb ON tpa.mint = tb.mint
            WHERE 1=1
        """
        params = []

        if status:
            query += " AND tpa.vault_validation_status = ?"
            params.append(status)

        if strategy:
            query += " AND tpa.vault_discovery_strategy = ?"
            params.append(strategy)

        if mint:
            query += " AND tpa.mint LIKE ?"
            params.append(f"%{mint}%")

        if pool:
            query += " AND tpa.pool_address LIKE ?"
            params.append(f"%{pool}%")

        if tracking_quality:
            query += " AND tb.tracking_quality = ?"
            params.append(tracking_quality)

        if category:
            query += " AND tb.category = ?"
            params.append(category)

        # Sort
        if sort_by == 'attempts':
            query += " ORDER BY tpa.vault_discovery_attempts DESC"
        elif sort_by == 'category':
            query += " ORDER BY tb.category ASC"
        else:  # discovery_time
            query += " ORDER BY vault_discovery_time_secs DESC NULLS LAST"

        query += f" LIMIT {limit}"

        rows = conn.execute(query, params).fetchall()
        conn.close()

        vaults = []
        for row in rows:
            vaults.append({
                'mint': row['mint'],
                'pool_address': row['pool_address'],
                'base_account': row['base_account'],
                'quote_account': row['quote_account'],
                'vault_validation_status': row['vault_validation_status'],
                'vault_resolution_state': row['vault_resolution_state'],
                'vault_discovery_strategy': row['vault_discovery_strategy'],
                'discovery_method': row['discovery_method'],
                'vault_discovery_attempts': row['vault_discovery_attempts'],
                'vault_discovery_time_secs': row['vault_discovery_time_secs'],
                'created_at': row['created_at'],
                'last_vault_validation_at': row['last_vault_validation_at'],
                'vault_resolved_at': row['vault_resolved_at'],
                'tracking_quality': row['tracking_quality'],
                'initial_price_observed_usd': round(row['initial_price_observed_usd'], 8) if row['initial_price_observed_usd'] else None,
                'initial_price_robust_usd': round(row['initial_price_robust_usd'], 8) if row['initial_price_robust_usd'] else None,
                'peak_price_usd': round(row['peak_price_usd'], 8) if row['peak_price_usd'] else None,
                'latest_price_usd': round(row['latest_price_usd'], 8) if row['latest_price_usd'] else None,
                'max_return_observed': row['max_return_multiple_observed'],
                'max_return_robust': row['max_return_multiple'],
                'category': row['category'],
                'confidence': round(row['confidence'], 3) if row['confidence'] else None,
            })

        return no_cache_json({
            'vaults': vaults,
            'total': len(vaults),
            'filters': {
                'status': status,
                'strategy': strategy,
                'tracking_quality': tracking_quality,
                'category': category,
            }
        })

    except Exception as e:
        logger.error(f"Error fetching vaults: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/api/vaults/<mint>', methods=['GET'])
def api_vaults_detail(mint):
    """
    Get detailed vault and token information for a specific mint.
    """
    try:
        conn = sqlite3.connect('database/flex_complete_database.db')
        conn.row_factory = sqlite3.Row

        vault_row = conn.execute("""
            SELECT
                mint,
                pool_address,
                base_account,
                quote_account,
                base_token,
                quote_token,
                base_decimals,
                quote_decimals,
                vault_validation_status,
                vault_resolution_state,
                vault_discovery_strategy,
                discovery_method,
                vault_discovery_attempts,
                COALESCE(vault_discovery_time_secs, (last_vault_validation_at - created_at)) as vault_discovery_time_secs,
                created_at,
                last_vault_validation_at,
                vault_resolved_at
            FROM token_pool_accounts
            WHERE mint = ?
            LIMIT 1
        """, (mint,)).fetchone()

        if not vault_row:
            conn.close()
            return jsonify({'error': 'Vault not found', 'mint': mint}), 404

        token_row = conn.execute("""
            SELECT
                mint,
                category,
                confidence,
                tracking_quality,
                initial_price_observed_usd,
                initial_price_robust_usd,
                peak_price_usd,
                latest_price_usd,
                max_return_multiple_observed,
                max_return_multiple,
                drawdown_from_peak,
                recovery_ratio,
                time_to_peak_secs,
                lifetime_secs,
                snapshot_count
            FROM token_behavior
            WHERE mint = ?
        """, (mint,)).fetchone()

        conn.close()

        return jsonify({
            'mint': vault_row['mint'],
            'vault': {
                'pool_address': vault_row['pool_address'],
                'base_account': vault_row['base_account'],
                'quote_account': vault_row['quote_account'],
                'base_token': vault_row['base_token'],
                'quote_token': vault_row['quote_token'],
                'base_decimals': vault_row['base_decimals'],
                'quote_decimals': vault_row['quote_decimals'],
                'validation_status': vault_row['vault_validation_status'],
                'resolution_state': vault_row['vault_resolution_state'],
                'discovery_strategy': vault_row['vault_discovery_strategy'],
                'discovery_method': vault_row['discovery_method'],
                'discovery_attempts': vault_row['vault_discovery_attempts'],
                'discovery_time_secs': vault_row['vault_discovery_time_secs'],
                'created_at': vault_row['created_at'],
                'last_validation_at': vault_row['last_vault_validation_at'],
                'resolved_at': vault_row['vault_resolved_at'],
            },
            'token': {
                'category': token_row['category'] if token_row else None,
                'confidence': round(token_row['confidence'], 3) if token_row and token_row['confidence'] else None,
                'tracking_quality': token_row['tracking_quality'] if token_row else None,
                'initial_price_observed_usd': round(token_row['initial_price_observed_usd'], 8) if token_row and token_row['initial_price_observed_usd'] else None,
                'initial_price_robust_usd': round(token_row['initial_price_robust_usd'], 8) if token_row and token_row['initial_price_robust_usd'] else None,
                'peak_price_usd': round(token_row['peak_price_usd'], 8) if token_row and token_row['peak_price_usd'] else None,
                'latest_price_usd': round(token_row['latest_price_usd'], 8) if token_row and token_row['latest_price_usd'] else None,
                'max_return_observed': token_row['max_return_multiple_observed'] if token_row else None,
                'max_return_robust': token_row['max_return_multiple'] if token_row else None,
                'drawdown_from_peak': round(token_row['drawdown_from_peak'], 3) if token_row and token_row['drawdown_from_peak'] else None,
                'recovery_ratio': round(token_row['recovery_ratio'], 3) if token_row and token_row['recovery_ratio'] else None,
                'time_to_peak_secs': token_row['time_to_peak_secs'] if token_row else None,
                'lifetime_secs': token_row['lifetime_secs'] if token_row else None,
                'snapshot_count': token_row['snapshot_count'] if token_row else None,
            } if token_row else None
        })

    except Exception as e:
        logger.error(f"Error fetching vault detail: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


def register_dashboard_routes(app):
    """Register dashboard routes with Flask app."""
    app.register_blueprint(dashboard_routes)
    logger.info("[DASHBOARD] Dashboard routes registered successfully")
