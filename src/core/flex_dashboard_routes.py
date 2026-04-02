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
from typing import Any, Dict, List
from flask import Blueprint, render_template, jsonify, request, make_response

# Shared threshold: tokens below this market cap are excluded from homepage and live systems.
MIN_LIVE_MARKET_CAP = 5000
from src.core.shared_vault_classifier import get_classifier

logger = logging.getLogger(__name__)

DB_PATH = 'database/flex_complete_database.db'
VALID_BEHAVIOUR_CATEGORIES = {
    'immediate_rug', 'rug', 'slow_rug', 'runner', 'choppy_runner', 'faded_runner', 'unknown'
}
VALID_TRACKING_QUALITY = {'good', 'possibly_late', 'likely_late'}

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
    - min_snapshots: Minimum live snapshot count for data quality (default 8)
    - limit: Max results returned (default 100)

    Returns: {"tokens": [...], "total": N, "category_filter": "...", "min_confidence": N, "min_snapshots": N}
    """
    try:
        category = request.args.get('category', None)
        min_confidence = float(request.args.get('min_confidence', 0.0))
        min_snapshots = int(request.args.get('min_snapshots', 8))
        limit = min(int(request.args.get('limit', 100)), 500)

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row

        # Join the pre-computed summary table — O(1) per row, never scans token_price_snapshots.
        # token_snapshot_counts is kept in sync by price_service._store_snapshot on every write.
        query = """
            SELECT
                tb.*,
                COALESCE(tsc.snap_count, 0) AS live_snapshot_count
            FROM token_behavior tb
            LEFT JOIN token_snapshot_counts tsc ON tsc.mint = tb.mint
            WHERE COALESCE(tsc.snap_count, 0) >= ?
        """
        params: List[Any] = [min_snapshots]

        if category and category != 'all':
            query += " AND tb.category = ?"
            params.append(category)

        if min_confidence > 0:
            query += " AND tb.confidence >= ?"
            params.append(min_confidence)

        query += " ORDER BY tb.confidence DESC, tb.classified_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        conn.close()

        tokens = []
        for row in rows:
            tokens.append({
                'mint': row['mint'],
                'category': row['category'],
                'confidence': round(row['confidence'], 3) if row['confidence'] is not None else None,
                'price_observed_start': round(row['initial_price_observed_usd'], 8) if row['initial_price_observed_usd'] else None,
                'price_robust_start': round(row['initial_price_robust_usd'], 8) if row['initial_price_robust_usd'] else None,
                'price_peak': round(row['peak_price_usd'], 8) if row['peak_price_usd'] else None,
                'max_return_observed': row['max_return_multiple_observed'],
                'max_return_robust': row['max_return_multiple'],
                'drawdown_from_peak': round(row['drawdown_from_peak'], 3) if row['drawdown_from_peak'] else None,
                'snapshot_count': row['live_snapshot_count'],
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
        conn = sqlite3.connect(DB_PATH, timeout=5)
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
        tpa_cols = {r[1] for r in conn.execute("PRAGMA table_info(token_pool_accounts)").fetchall()}
        extra = ", vault_discovery_strategy, vault_discovery_time_secs, pool_address" if 'vault_discovery_strategy' in tpa_cols else ""
        vault_row = conn.execute(
            f"SELECT created_at, vault_validation_status, discovery_method, "
            f"last_vault_validation_at{extra} FROM token_pool_accounts WHERE mint = ? "
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
            # Prefer explicit discovery time; fall back to timestamp diff
            explicit_secs = vault_row['vault_discovery_time_secs'] if 'vault_discovery_time_secs' in vault_row.keys() else None
            if explicit_secs is not None:
                vault_discovery_secs = explicit_secs
            elif (vault_row['vault_validation_status'] == 'validated'
                  and vault_row['created_at']
                  and vault_row['last_vault_validation_at']):
                vault_discovery_secs = max(0, vault_row['last_vault_validation_at'] - vault_row['created_at'])
            else:
                vault_discovery_secs = None

            strategy = vault_row['vault_discovery_strategy'] if 'vault_discovery_strategy' in vault_row.keys() else None
            pool_address = vault_row['pool_address'] if 'pool_address' in vault_row.keys() else None

            vault_metadata = {
                'validation_status': vault_row['vault_validation_status'],
                'discovery_method': strategy or vault_row['discovery_method'] or 'unknown',
                'discovery_secs': vault_discovery_secs,
                'pool_address': pool_address,
                'created_at': vault_row['created_at'],
                'last_validation_at': vault_row['last_vault_validation_at'],
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
        conn = sqlite3.connect(DB_PATH, timeout=5)

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


@dashboard_routes.route('/api/token-behaviour/all', methods=['GET'])
def api_token_behaviour_all():
    """
    Single endpoint that returns stats + top tokens for every category in one DB round-trip.

    Replaces the 6 sequential per-category fetches the frontend was making.
    Uses token_snapshot_counts summary table — never scans token_price_snapshots.

    Query params:
    - per_category: tokens per category (default 10)
    - min_confidence: minimum confidence (default 0.1)
    - min_snapshots: minimum live snapshot count (default 8)
    """
    try:
        per_category = min(int(request.args.get('per_category', 10)), 50)
        min_confidence = float(request.args.get('min_confidence', 0.1))
        min_snapshots = int(request.args.get('min_snapshots', 8))

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row

        CATEGORIES = ['immediate_rug', 'runner', 'faded_runner', 'choppy_runner', 'rug', 'slow_rug']

        # Stats (cheap — 386 rows)
        stat_rows = conn.execute("""
            SELECT category, COUNT(*) AS count, ROUND(AVG(confidence), 3) AS avg_confidence
            FROM token_behavior
            GROUP BY category
            ORDER BY count DESC
        """).fetchall()
        total = sum(r['count'] for r in stat_rows)
        by_category = {
            r['category']: {
                'count': r['count'],
                'avg_confidence': r['avg_confidence'],
                'pct': round(100.0 * r['count'] / total, 1) if total > 0 else 0,
            }
            for r in stat_rows
        }

        # All qualifying tokens in one query, ranked per category with ROW_NUMBER
        rows = conn.execute("""
            SELECT tb.*, COALESCE(tsc.snap_count, 0) AS live_snapshot_count,
                   ROW_NUMBER() OVER (
                       PARTITION BY tb.category
                       ORDER BY tb.confidence DESC, tb.classified_at DESC
                   ) AS rn
            FROM token_behavior tb
            LEFT JOIN token_snapshot_counts tsc ON tsc.mint = tb.mint
            WHERE tb.category IN ('immediate_rug','runner','faded_runner','choppy_runner','rug','slow_rug')
              AND COALESCE(tsc.snap_count, 0) >= ?
              AND (? = 0 OR tb.confidence >= ?)
        """, (min_snapshots, min_confidence, min_confidence)).fetchall()

        category_tokens: Dict[str, list] = {cat: [] for cat in CATEGORIES}
        for row in rows:
            if row['rn'] > per_category:
                continue
            cat = row['category']
            if cat not in category_tokens:
                continue
            category_tokens[cat].append({
                'mint': row['mint'],
                'category': cat,
                'confidence': round(row['confidence'], 3) if row['confidence'] is not None else None,
                'price_observed_start': round(row['initial_price_observed_usd'], 8) if row['initial_price_observed_usd'] else None,
                'price_robust_start': round(row['initial_price_robust_usd'], 8) if row['initial_price_robust_usd'] else None,
                'price_peak': round(row['peak_price_usd'], 8) if row['peak_price_usd'] else None,
                'max_return_observed': row['max_return_multiple_observed'],
                'max_return_robust': row['max_return_multiple'],
                'drawdown_from_peak': round(row['drawdown_from_peak'], 3) if row['drawdown_from_peak'] else None,
                'snapshot_count': row['live_snapshot_count'],
                'lifetime_secs': row['lifetime_secs'],
                'tracking_quality': row['tracking_quality'],
                'classified_at': row['classified_at'],
            })

        conn.close()

        return no_cache_json({
            'stats': {
                'total_classified': total,
                'by_category': by_category,
            },
            'category_tokens': category_tokens,
            'last_updated': int(time.time()),
        })

    except Exception as e:
        logger.error(f"Error fetching token behaviour all: {e}", exc_info=True)
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


@dashboard_routes.route('/snapshots', methods=['GET'])
def snapshots_page():
    """Render Snapshots page — latest price snapshot per token."""
    return render_template('snapshots.html', active_page='snapshots')


def _get_snapshots_conn():
    """Open a fresh connection per request — correlated subquery needs up-to-date WAL view."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


@dashboard_routes.route('/api/snapshots', methods=['GET'])
def api_snapshots():
    """Return latest snapshot per token, ordered by most recently snapshotted."""
    import time as _time
    try:
        conn = _get_snapshots_conn()
        # Read price/market_cap directly from token_price_snapshots (latest row per mint).
        # Avoids stale/corrupt values in token_analysis written by listener paths.
        now = int(_time.time())
        rows = conn.execute("""
            SELECT tsc.mint, tsc.snap_count, tsc.last_updated,
                   tps.price_usd, tps.market_cap, tps.source,
                   COALESCE(tt.symbol, mc.symbol) AS symbol,
                   mc.name,
                   ta.created_at
            FROM token_snapshot_counts tsc
            LEFT JOIN token_price_snapshots tps ON tps.snapshot_id = (
                SELECT snapshot_id FROM token_price_snapshots
                WHERE mint = tsc.mint
                ORDER BY captured_at DESC
                LIMIT 1
            )
            LEFT JOIN tracked_tokens tt ON tt.mint = tsc.mint
            LEFT JOIN metadata_cache mc ON mc.mint = tsc.mint
            LEFT JOIN token_analysis ta ON ta.mint = tsc.mint
            ORDER BY (tsc.last_updated > ?) DESC, tsc.last_updated DESC
        """, (now - 60,)).fetchall()
        data = []
        backfill = []  # (mint, symbol) pairs to write into tracked_tokens
        for r in rows:
            last_ts = r['last_updated'] or 0
            price = r['price_usd']
            mc = r['market_cap']
            # Reject clearly bad values before sending to UI
            if price is not None and price <= 0:
                price = None
            if mc is not None and mc <= 0:
                mc = None
            if mc is None or mc < MIN_LIVE_MARKET_CAP:
                continue
            symbol = r['symbol'] or None
            # Backfill tracked_tokens.symbol if it came from metadata_cache (was NULL in tracked_tokens)
            if symbol and not r['symbol']:
                backfill.append((symbol, r['mint']))
            data.append({
                'mint': r['mint'],
                'symbol': symbol,
                'name': r['name'] or None,
                'price_usd': price,
                'market_cap': mc,
                'source': r['source'] or 'pool',
                'last_snapshot': last_ts,
                'snap_count': r['snap_count'],
                'age_seconds': now - last_ts if last_ts else 99999,
                'created_at': r['created_at'] or None,
            })

        if backfill:
            try:
                conn.executemany(
                    "UPDATE tracked_tokens SET symbol = ? WHERE mint = ? AND (symbol IS NULL OR symbol = '')",
                    backfill
                )
                conn.commit()
            except Exception:
                pass  # Non-critical; best-effort only

        return jsonify({'data': data, 'total': len(data)})
    except Exception as e:
        logger.error(f"Error fetching snapshots: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@dashboard_routes.route('/api/boost-tokens', methods=['POST'])
def api_boost_tokens():
    """Temporarily boost listed tokens to HIGH-priority refresh."""
    try:
        body = request.get_json(silent=True) or {}
        mints = body.get('mints', [])
        ttl = int(body.get('ttl', 30))

        if not mints or not isinstance(mints, list):
            return jsonify({'error': 'mints must be a non-empty list'}), 400
        if ttl < 1 or ttl > 300:
            return jsonify({'error': 'ttl must be 1–300 seconds'}), 400

        import src.core.price_worker as _pw
        worker = _pw._price_worker
        if worker is None:
            return jsonify({'error': 'price worker not running'}), 503

        boosted = worker.registry.boost_tokens(mints, ttl)
        return jsonify({'boosted': boosted, 'ttl': ttl})
    except Exception as e:
        logger.error(f"Error boosting tokens: {e}", exc_info=True)
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
def _table_columns(conn: sqlite3.Connection, table_name: str) -> set:
    """Return the set of column names for a table."""
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    """Check whether a table contains a given column."""
    return column_name in _table_columns(conn, table_name)


def _get_first_seen_by_mint(conn: sqlite3.Connection, mints: List[str]) -> Dict[str, int]:
    """Bulk fetch MIN(captured_at) per mint from token_price_snapshots."""
    if not mints:
        return {}
    unique_mints = list({m for m in mints if m})
    if not unique_mints:
        return {}
    placeholders = ",".join("?" * len(unique_mints))
    rows = conn.execute(
        f"SELECT mint, MIN(captured_at) AS first_seen FROM token_price_snapshots "
        f"WHERE mint IN ({placeholders}) GROUP BY mint",
        unique_mints,
    ).fetchall()
    return {row["mint"]: row["first_seen"] for row in rows}


def _get_snapshot_counts_by_mint(conn: sqlite3.Connection, mints: List[str]) -> Dict[str, int]:
    """
    Bulk fetch live snapshot counts from token_snapshot_counts summary table.

    token_snapshot_counts is maintained by price_service._store_snapshot on every write,
    so it is always current without scanning the 2.9M-row token_price_snapshots table.
    """
    if not mints:
        return {}

    unique_mints = list({m for m in mints if m})
    if not unique_mints:
        return {}

    placeholders = ",".join("?" * len(unique_mints))
    rows = conn.execute(
        f"SELECT mint, snap_count AS cnt FROM token_snapshot_counts "
        f"WHERE mint IN ({placeholders})",
        unique_mints,
    ).fetchall()
    return {row["mint"]: row["cnt"] for row in rows}


def _format_nullable_float(value, digits: int = 3):
    """Format a nullable float to fixed decimal places."""
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _normalize_category(value):
    """Return category as-is if non-empty; only null/empty becomes None."""
    if value is None or value == '':
        return None
    return value


def _normalize_tracking_quality(value):
    """Validate tracking quality against approved list."""
    if value in VALID_TRACKING_QUALITY:
        return value
    return None


def _build_vaults_select(conn: sqlite3.Connection) -> str:
    """
    Build a SELECT that works whether or not the newer explicit vault-discovery
    columns have been migrated yet.
    """
    tpa_cols = _table_columns(conn, 'token_pool_accounts')
    tb_cols = _table_columns(conn, 'token_behavior')

    def tpa_col(name: str, fallback_sql: str = "NULL"):
        return f"tpa.{name}" if name in tpa_cols else fallback_sql

    def tb_col(name: str, fallback_sql: str = "NULL"):
        return f"tb.{name}" if name in tb_cols else fallback_sql

    # Prefer explicit persisted discovery time, otherwise derive from timestamps.
    explicit_discovery_time = tpa_col('vault_discovery_time_secs')
    discovery_time_sql = f"""
        CASE
            WHEN {explicit_discovery_time} IS NOT NULL THEN {explicit_discovery_time}
            WHEN {tpa_col('last_vault_validation_at')} IS NOT NULL
                 AND {tpa_col('created_at')} IS NOT NULL
            THEN CAST(({tpa_col('last_vault_validation_at')} - {tpa_col('created_at')}) AS REAL)
            ELSE NULL
        END
    """

    # Prefer explicit strategy, otherwise fall back to discovery_method.
    strategy_sql = f"""
        COALESCE(
            {tpa_col('vault_discovery_strategy')},
            {tpa_col('discovery_method')},
            NULL
        )
    """

    attempts_sql = tpa_col('vault_discovery_attempts')
    resolution_state_sql = f"""
        COALESCE(
            {tpa_col('vault_resolution_state')},
            CASE
                WHEN {tpa_col('vault_validation_status')} = 'validated' THEN 'resolved'
                WHEN {tpa_col('vault_validation_status')} = 'pending' THEN 'pending'
                WHEN {tpa_col('vault_validation_status')} = 'rejected' THEN 'rejected'
                ELSE NULL
            END
        )
    """

    resolved_at_sql = f"""
        COALESCE(
            {tpa_col('vault_resolved_at')},
            {tpa_col('last_vault_validation_at')}
        )
    """

    # pool_address only - no fallback to base_account
    # base_account is a separate field (may be shared vault)
    pool_address_sql = tpa_col('pool_address')

    return f"""
        SELECT
            tpa.mint                                      AS mint,
            {pool_address_sql}                            AS pool_address,
            {tpa_col('base_account')}                     AS base_account,
            {tpa_col('quote_account')}                    AS quote_account,
            {tpa_col('base_token')}                       AS base_token,
            {tpa_col('quote_token')}                      AS quote_token,
            {tpa_col('base_decimals')}                    AS base_decimals,
            {tpa_col('quote_decimals')}                   AS quote_decimals,
            {tpa_col('pool_program')}                     AS pool_program,

            {tpa_col('vault_validation_status')}          AS vault_validation_status,
            {resolution_state_sql}                        AS vault_resolution_state,
            {strategy_sql}                                AS vault_discovery_strategy,
            {tpa_col('discovery_method')}                 AS vault_discovery_method,
            {attempts_sql}                                AS vault_discovery_attempts,
            {discovery_time_sql}                          AS vault_discovery_time_secs,
            {tpa_col('created_at')}                       AS created_at,
            {tpa_col('last_vault_validation_at')}         AS last_vault_validation_at,
            {resolved_at_sql}                             AS vault_resolved_at,

            {tb_col('initial_price_observed_usd')}        AS initial_price_observed_usd,
            {tb_col('initial_price_robust_usd')}          AS initial_price_robust_usd,
            {tb_col('peak_price_usd')}                    AS peak_price_usd,
            {tb_col('latest_price_usd')}                  AS latest_price_usd,
            {tb_col('max_return_multiple')}               AS max_return_multiple,
            {tb_col('max_return_multiple_observed')}      AS max_return_multiple_observed,
            {tb_col('tracking_quality')}                  AS tracking_quality,
            {tb_col('category')}                          AS category,
            {tb_col('confidence')}                        AS confidence,
            {tb_col('drawdown_from_peak')}                AS drawdown_from_peak,
            {tb_col('recovery_ratio')}                    AS recovery_ratio,
            {tb_col('time_to_peak_secs')}                 AS time_to_peak_secs,
            {tb_col('snapshot_count')}                    AS snapshot_count,
            {tb_col('lifetime_secs')}                     AS lifetime_secs,
            {tb_col('classified_at')}                     AS classified_at

        FROM token_pool_accounts tpa
        LEFT JOIN token_behavior tb
            ON tb.mint = tpa.mint
    """


def _vault_row_to_dict(
    row: sqlite3.Row,
    base_account_counts: Dict[str, int] = None,
    snap_counts: Dict[str, int] = None,
) -> Dict[str, Any]:
    """
    Normalize one merged vault/token row into API-safe JSON.
    Includes account type classification for shared vaults.
    """
    raw_category = _normalize_category(row['category'])
    if raw_category is None:
        _snap = snap_counts.get(row['mint'], 0) if snap_counts is not None else 0
        if _snap == 0:
            category = 'no_data'
        elif _snap < 8:
            category = 'collecting'
        else:
            category = None
    else:
        category = raw_category
    tracking_quality = _normalize_tracking_quality(row['tracking_quality'])

    strategy = row['vault_discovery_strategy'] or row['vault_discovery_method']
    attempts = row['vault_discovery_attempts']
    discovery_time = _format_nullable_float(row['vault_discovery_time_secs'], 3)
    confidence = _format_nullable_float(row['confidence'], 3)

    # Avoid misleading defaults: unknown/0/N/A should reflect actual state.
    if attempts is None:
        attempts_out = None
    else:
        try:
            attempts_out = int(attempts)
        except (TypeError, ValueError):
            attempts_out = None

    # Classify the base_account using pre-computed counts (no per-row DB calls)
    pool_address = row['pool_address']
    base_account = row['base_account']
    _ACCOUNT_TYPE_LABELS = {
        'shared_vault_signature': 'Shared Vault (pump.fun)',
        'shared_program_vault': 'Shared Vault (Program)',
        'token_vault': 'Token Vault',
        'unknown': 'Unknown',
    }
    account_type = 'unknown'
    if base_account and base_account_counts is not None:
        cnt = base_account_counts.get(base_account, 0)
        if cnt >= 10:
            account_type = 'shared_vault_signature'
        elif cnt >= 5:
            account_type = 'shared_program_vault'
        elif cnt >= 1:
            account_type = 'token_vault'
    elif base_account:
        # Single-row path (detail endpoint) — use classifier
        try:
            classifier = get_classifier()
            account_type = classifier.classify_account(base_account)
        except Exception:
            pass
    account_type_label = _ACCOUNT_TYPE_LABELS.get(account_type, 'Unknown')

    return {
        'mint': row['mint'],
        'pool_address': pool_address,
        'base_account': row['base_account'],
        'base_account_type': account_type,
        'base_account_type_label': account_type_label,
        'quote_account': row['quote_account'],
        'base_token': row['base_token'],
        'quote_token': row['quote_token'],
        'base_decimals': row['base_decimals'],
        'quote_decimals': row['quote_decimals'],
        'pool_program': row['pool_program'],

        'vault_validation_status': row['vault_validation_status'],
        'vault_resolution_state': row['vault_resolution_state'],
        'vault_discovery_strategy': strategy,
        'vault_discovery_method': row['vault_discovery_method'],
        'vault_discovery_attempts': attempts_out,
        'vault_discovery_time_secs': discovery_time,
        'created_at': row['created_at'],
        'last_vault_validation_at': row['last_vault_validation_at'],
        'vault_resolved_at': row['vault_resolved_at'],

        'tracking_quality': tracking_quality,
        'initial_price_observed_usd': _format_nullable_float(row['initial_price_observed_usd'], 12),
        'initial_price_robust_usd': _format_nullable_float(row['initial_price_robust_usd'], 12),
        'peak_price_usd': _format_nullable_float(row['peak_price_usd'], 12),
        'latest_price_usd': _format_nullable_float(row['latest_price_usd'], 12),
        'max_return_multiple': _format_nullable_float(row['max_return_multiple'], 3),
        'max_return_multiple_observed': _format_nullable_float(row['max_return_multiple_observed'], 3),

        'category': category,
        'confidence': confidence,
        'drawdown_from_peak': _format_nullable_float(row['drawdown_from_peak'], 3),
        'recovery_ratio': _format_nullable_float(row['recovery_ratio'], 3),
        'time_to_peak_secs': row['time_to_peak_secs'],
        'snapshot_count': (
            snap_counts.get(row['mint'], 0)
            if snap_counts is not None
            else row['snapshot_count']
        ),
        'lifetime_secs': row['lifetime_secs'],
        'classified_at': row['classified_at'],
    }


def _build_vault_debug(row: sqlite3.Row) -> dict:
    """Derive debug/health flags from a vault row for the detail API."""
    strategy_val = row['vault_discovery_strategy']
    time_val     = row['vault_discovery_time_secs']
    created_at   = row['created_at']
    last_val     = row['last_vault_validation_at']

    has_explicit   = bool(strategy_val and strategy_val != 'unknown')
    fallback_strat = not has_explicit
    fallback_time  = time_val is None
    row_updated    = bool(last_val and created_at and last_val > created_at)

    return {
        'has_explicit_discovery_data': has_explicit,
        'using_fallback_strategy':     fallback_strat,
        'using_fallback_time':         fallback_time,
        'row_updated':                 row_updated,
        'raw': {
            'vault_discovery_strategy':  strategy_val,
            'vault_discovery_attempts':  row['vault_discovery_attempts'],
            'vault_discovery_time_secs': time_val,
            'vault_resolution_state':    row['vault_resolution_state'],
            'vault_resolved_at':         row['vault_resolved_at'],
            'discovery_method':          row['vault_discovery_method'],
            'vault_validation_status':   row['vault_validation_status'],
            'created_at':                created_at,
            'last_vault_validation_at':  last_val,
        },
    }


@dashboard_routes.route('/api/vaults', methods=['GET'])
def api_vaults():
    """
    List vault/token rows for the Vaults page.

    Query params:
    - status: validated | pending | rejected | all
    - strategy: tx_parsing | rpc | ... | all
    - tracking_quality: good | possibly_late | likely_late | all
    - category: behaviour category or all
    - mint: substring match on mint
    - pool: substring match on pool/base account
    - limit: default 100
    - sort_by: discovery_time | confidence | classified_at | created_at
    - sort_dir: asc | desc
    """
    try:

        status = request.args.get('status', 'all')
        strategy = request.args.get('strategy', 'all')
        tracking_quality = request.args.get('tracking_quality', 'all')
        category = request.args.get('category', 'all')
        mint = request.args.get('mint', '').strip()
        pool = request.args.get('pool', '').strip()
        limit = min(int(request.args.get('limit', 100)), 500)
        sort_by = request.args.get('sort_by', 'created_at')
        sort_dir = request.args.get('sort_dir', 'desc').lower()

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row

        base_sql = _build_vaults_select(conn)
        query = f"SELECT * FROM ({base_sql}) v WHERE 1=1"
        params: List[Any] = []

        if status != 'all':
            query += " AND v.vault_validation_status = ?"
            params.append(status)

        if strategy != 'all':
            query += " AND COALESCE(v.vault_discovery_strategy, '') = ?"
            params.append(strategy)

        if tracking_quality != 'all':
            query += " AND COALESCE(v.tracking_quality, '') = ?"
            params.append(tracking_quality)

        if category != 'all':
            query += " AND COALESCE(v.category, '') = ?"
            params.append(category)

        if mint:
            query += " AND v.mint LIKE ?"
            params.append(f"%{mint}%")

        if pool:
            query += " AND COALESCE(v.pool_address, '') LIKE ?"
            params.append(f"%{pool}%")

        sortable = {
            'discovery_time': 'v.vault_discovery_time_secs',
            'confidence': 'v.confidence',
            'classified_at': 'v.classified_at',
            'created_at': 'v.created_at',
        }
        order_col = sortable.get(sort_by, 'v.created_at')
        order_dir = 'ASC' if sort_dir == 'asc' else 'DESC'

        # Keep NULLs at the end for discovery/confidence sorting.
        query += f" ORDER BY ({order_col} IS NULL) ASC, {order_col} {order_dir}, v.mint ASC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

        # Live snapshot counts (replaces stale token_behavior.snapshot_count)
        mints = [r['mint'] for r in rows if r['mint']]
        snap_counts = _get_snapshot_counts_by_mint(conn, mints)
        first_seen_map = _get_first_seen_by_mint(conn, mints)

        # Pre-compute base_account usage counts in one query (avoids N per-row DB calls)
        base_accounts = [r['base_account'] for r in rows if r['base_account']]
        base_account_counts: Dict[str, int] = {}
        if base_accounts:
            placeholders = ','.join('?' * len(base_accounts))
            cnt_rows = conn.execute(
                f"SELECT base_account, COUNT(DISTINCT mint) AS cnt FROM token_pool_accounts "
                f"WHERE base_account IN ({placeholders}) GROUP BY base_account",
                base_accounts,
            ).fetchall()
            base_account_counts = {r['base_account']: r['cnt'] for r in cnt_rows}

        conn.close()

        data = [_vault_row_to_dict(row, base_account_counts, snap_counts) for row in rows]

        # Sort by first_seen DESC (tokens with snapshots first, newest at top)
        data.sort(key=lambda d: (first_seen_map.get(d['mint']) is None, -(first_seen_map.get(d['mint']) or 0)))


        return no_cache_json({
            'vaults': data,
            'total': len(data),
            'filters': {
                'status': status,
                'strategy': strategy,
                'tracking_quality': tracking_quality,
                'category': category,
                'mint': mint,
                'pool': pool,
                'limit': limit,
                'sort_by': sort_by,
                'sort_dir': order_dir.lower(),
            },
            'last_updated': int(time.time()),
        })

    except Exception as e:
        logger.error(f"Error fetching vaults: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/api/vaults/stats/summary', methods=['GET'])
def api_vaults_stats_summary():
    """
    Summary stats for Vaults page cards.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row

        base_sql = _build_vaults_select(conn)

        totals_row = conn.execute(f"""
            SELECT
                COUNT(*) AS total_records,
                SUM(CASE WHEN vault_validation_status = 'validated' THEN 1 ELSE 0 END) AS validated_count,
                SUM(CASE WHEN vault_validation_status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN vault_validation_status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
                ROUND(AVG(vault_discovery_time_secs), 1) AS avg_discovery_time_secs,
                ROUND(AVG(vault_discovery_attempts), 2) AS avg_attempts
            FROM ({base_sql}) v
        """).fetchone()

        quality_rows = conn.execute(f"""
            SELECT
                tracking_quality,
                COUNT(*) AS count
            FROM ({base_sql}) v
            WHERE tracking_quality IS NOT NULL
            GROUP BY tracking_quality
        """).fetchall()

        conn.close()

        total_records = totals_row['total_records'] or 0
        quality_counts = {
            'good': 0,
            'possibly_late': 0,
            'likely_late': 0,
        }
        for row in quality_rows:
            q = row['tracking_quality']
            if q in quality_counts:
                quality_counts[q] = row['count']

        late_denominator = sum(quality_counts.values())

        return no_cache_json({
            'total_records': total_records,
            'validated_count': totals_row['validated_count'] or 0,
            'pending_count': totals_row['pending_count'] or 0,
            'rejected_count': totals_row['rejected_count'] or 0,
            'avg_discovery_time_secs': _format_nullable_float(totals_row['avg_discovery_time_secs'], 1),
            'avg_attempts': _format_nullable_float(totals_row['avg_attempts'], 2),
            'tracking_quality': {
                'good_count': quality_counts['good'],
                'possibly_late_count': quality_counts['possibly_late'],
                'likely_late_count': quality_counts['likely_late'],
                'possibly_late_pct': round(100.0 * quality_counts['possibly_late'] / late_denominator, 1) if late_denominator else 0.0,
                'likely_late_pct': round(100.0 * quality_counts['likely_late'] / late_denominator, 1) if late_denominator else 0.0,
            },
            'last_updated': int(time.time()),
        })

    except Exception as e:
        logger.error(f"Error fetching vault summary stats: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/api/vaults/stats/discovery-health', methods=['GET'])
def api_vaults_stats_discovery_health():
    """Aggregated discovery health stats overall and by strategy."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        tpa_cols = _table_columns(conn, 'token_pool_accounts')

        # Guard: return empty response if key columns missing
        required = {'vault_resolution_state', 'vault_discovery_strategy',
                    'vault_discovery_attempts', 'vault_discovery_time_secs'}
        if not required.issubset(tpa_cols):
            conn.close()
            return no_cache_json({
                'overall': {}, 'by_strategy': [],
                'last_updated': int(time.time())
            })

        overall_row = conn.execute("""
            SELECT
                COUNT(*) AS total_records,
                SUM(CASE WHEN vault_resolution_state = 'resolved' THEN 1 ELSE 0 END) AS resolved_count,
                SUM(CASE WHEN vault_resolution_state = 'pending'  THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN vault_resolution_state = 'rejected' THEN 1 ELSE 0 END) AS rejected_count,
                ROUND(AVG(vault_discovery_attempts), 2)    AS avg_attempts,
                ROUND(AVG(vault_discovery_time_secs), 1)   AS avg_resolution_time_secs,
                SUM(CASE WHEN vault_discovery_strategy IS NOT NULL
                          AND vault_discovery_strategy != 'unknown' THEN 1 ELSE 0 END) AS explicit_data_count,
                SUM(CASE WHEN vault_discovery_strategy IS NULL
                          OR  vault_discovery_strategy  = 'unknown' THEN 1 ELSE 0 END) AS fallback_only_count
            FROM token_pool_accounts
        """).fetchone()

        strategy_rows = conn.execute("""
            SELECT
                COALESCE(vault_discovery_strategy, discovery_method, 'unknown') AS strategy,
                COUNT(*) AS total,
                SUM(CASE WHEN vault_resolution_state = 'resolved' THEN 1 ELSE 0 END) AS resolved,
                SUM(CASE WHEN vault_resolution_state = 'pending'  THEN 1 ELSE 0 END) AS pending,
                SUM(CASE WHEN vault_resolution_state = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                ROUND(AVG(vault_discovery_attempts), 2)  AS avg_attempts,
                ROUND(AVG(vault_discovery_time_secs), 1) AS avg_resolution_time_secs,
                ROUND(100.0 * SUM(CASE WHEN vault_resolution_state = 'rejected' THEN 1 ELSE 0 END)
                              / COUNT(*), 1)             AS failure_rate
            FROM token_pool_accounts
            GROUP BY COALESCE(vault_discovery_strategy, discovery_method, 'unknown')
            ORDER BY total DESC
        """).fetchall()

        conn.close()

        return no_cache_json({
            'overall': {
                'total_records':            overall_row['total_records'] or 0,
                'resolved_count':           overall_row['resolved_count'] or 0,
                'pending_count':            overall_row['pending_count'] or 0,
                'rejected_count':           overall_row['rejected_count'] or 0,
                'avg_attempts':             _format_nullable_float(overall_row['avg_attempts'], 2),
                'avg_resolution_time_secs': _format_nullable_float(overall_row['avg_resolution_time_secs'], 1),
                'explicit_data_count':      overall_row['explicit_data_count'] or 0,
                'fallback_only_count':      overall_row['fallback_only_count'] or 0,
            },
            'by_strategy': [
                {
                    'strategy':                r['strategy'],
                    'total':                   r['total'],
                    'resolved':                r['resolved'] or 0,
                    'pending':                 r['pending'] or 0,
                    'rejected':                r['rejected'] or 0,
                    'avg_attempts':            _format_nullable_float(r['avg_attempts'], 2),
                    'avg_resolution_time_secs': _format_nullable_float(r['avg_resolution_time_secs'], 1),
                    'failure_rate':            _format_nullable_float(r['failure_rate'], 1),
                }
                for r in strategy_rows
            ],
            'last_updated': int(time.time()),
        })

    except Exception as e:
        logger.error(f"Error fetching discovery health stats: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/api/vaults/shared-vaults', methods=['GET'])
def api_shared_vaults():
    """
    List all shared vault accounts and their token counts.

    Returns:
        - account_address
        - token_count
        - classification (shared_vault_signature / shared_program_vault / token_vault)
        - label (human-readable)
    """
    try:
        classifier = get_classifier()
        min_reuse = request.args.get('min_reuse', default=5, type=int)

        vaults = classifier.get_shared_vaults(min_reuse=min_reuse)

        return no_cache_json({
            'shared_vaults': vaults,
            'total': len(vaults),
            'filters': {
                'min_reuse': min_reuse,
            },
            'last_updated': int(time.time()),
        })

    except Exception as e:
        logger.error(f"Error fetching shared vaults: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/api/vaults/shared-vaults/<vault_address>/tokens', methods=['GET'])
def api_vault_tokens(vault_address):
    """
    Get all tokens using a specific shared vault.

    Returns:
        - mint
        - discovery_method
        - created_at
    """
    try:
        classifier = get_classifier()
        tokens = classifier.get_tokens_by_shared_vault(vault_address)

        return no_cache_json({
            'vault_address': vault_address,
            'tokens': tokens,
            'total': len(tokens),
            'last_updated': int(time.time()),
        })

    except Exception as e:
        logger.error(f"Error fetching tokens for vault {vault_address}: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/api/vaults/launch-clusters', methods=['GET'])
def api_launch_clusters():
    """
    Detect and return token clusters (coordinated launches via shared vault).

    Returns:
        - vault_address
        - vault_label
        - token_count
        - time_window_minutes (first to last token creation)
        - first_token_created_at
        - last_token_created_at
        - tokens (list)
    """
    try:
        classifier = get_classifier()
        clusters = classifier.detect_launch_clusters()

        return no_cache_json({
            'clusters': clusters,
            'total': len(clusters),
            'last_updated': int(time.time()),
        })

    except Exception as e:
        logger.error(f"Error detecting launch clusters: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


@dashboard_routes.route('/api/vaults/<mint>', methods=['GET'])
def api_vault_detail(mint):
    """
    Detail endpoint for a single token/vault merged view.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row

        base_sql = _build_vaults_select(conn)
        row = conn.execute(
            f"SELECT * FROM ({base_sql}) v WHERE v.mint = ? LIMIT 1",
            (mint,)
        ).fetchone()

        if not row:
            conn.close()
            return jsonify({'error': 'Vault/token not found', 'mint': mint}), 404

        snap_counts = _get_snapshot_counts_by_mint(conn, [mint])
        data = _vault_row_to_dict(row, {}, snap_counts)
        data['debug'] = _build_vault_debug(row)

        # Optional lightweight history if behaviour history exists.
        history = []
        if _has_column(conn, 'token_behavior_history', 'mint'):
            history_rows = conn.execute("""
                SELECT
                    category,
                    confidence,
                    max_return_multiple,
                    drawdown_from_peak,
                    recovery_ratio,
                    time_to_peak_secs,
                    lifetime_secs,
                    snapshot_count,
                    classified_at
                FROM token_behavior_history
                WHERE mint = ?
                ORDER BY classified_at DESC
                LIMIT 20
            """, (mint,)).fetchall()

            history = [
                {
                    'category': _normalize_category(r['category']),
                    'confidence': _format_nullable_float(r['confidence'], 3),
                    'max_return_multiple': _format_nullable_float(r['max_return_multiple'], 3),
                    'drawdown_from_peak': _format_nullable_float(r['drawdown_from_peak'], 3),
                    'recovery_ratio': _format_nullable_float(r['recovery_ratio'], 3),
                    'time_to_peak_secs': r['time_to_peak_secs'],
                    'lifetime_secs': r['lifetime_secs'],
                    'snapshot_count': r['snapshot_count'],
                    'classified_at': r['classified_at'],
                }
                for r in history_rows
            ]

        conn.close()

        return no_cache_json({
            'mint': mint,
            'vault': data,
            'history': history,
            'last_updated': int(time.time()),
        })

    except Exception as e:
        logger.error(f"Error fetching vault detail for {mint}: {e}", exc_info=True)
        return no_cache_json({'error': str(e)}), 500


def register_dashboard_routes(app):
    """Register dashboard routes with Flask app."""
    app.register_blueprint(dashboard_routes)
    logger.info("[DASHBOARD] Dashboard routes registered successfully")
