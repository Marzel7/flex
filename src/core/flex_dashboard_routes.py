"""FLEX dashboard routes: current non-price surfaces and vault discovery APIs."""

import logging
import os
import sqlite3
import time
from typing import Any, Dict, List

from flask import Blueprint, jsonify, make_response, render_template, request
from src.core.shared_vault_classifier import get_classifier
from src.utils.db_locking import db_connect

logger = logging.getLogger(__name__)
_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))
_DEFAULT_DB_PATH = os.path.join(_REPO_ROOT, "database", "flex_complete_database.db")
DB_PATH = os.path.abspath(os.environ.get("DB_PATH", _DEFAULT_DB_PATH))
VALID_TRACKING_QUALITY = {"good", "possibly_late", "likely_late"}
dashboard_routes = Blueprint("dashboard", __name__, url_prefix="")

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
            return jsonify({'boosted': 0, 'ttl': ttl, 'note': 'worker not ready'}), 200

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


def register_dashboard_routes(app):
    """Register dashboard routes with Flask app."""
    app.register_blueprint(dashboard_routes)
    logger.info("[DASHBOARD] Dashboard routes registered successfully")



def _table_columns(conn: sqlite3.Connection, table_name: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    return column_name in _table_columns(conn, table_name)


def _format_nullable_float(value, digits: int = 3):
    try:
        return round(float(value), digits) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_category(value):
    return value or None


def _normalize_tracking_quality(value):
    return value if value in VALID_TRACKING_QUALITY else None


def _build_vaults_select(conn: sqlite3.Connection) -> str:
    """Current vault/pool projection; intentionally contains no price-history joins."""
    cols = _table_columns(conn, "token_pool_accounts")
    def col(name, fallback="NULL"):
        return f"tpa.{name}" if name in cols else fallback
    return f"""
        SELECT tpa.mint AS mint,
               {col('pool_address')} AS pool_address,
               {col('base_account')} AS base_account,
               {col('quote_account')} AS quote_account,
               {col('base_token')} AS base_token,
               {col('quote_token')} AS quote_token,
               {col('base_decimals')} AS base_decimals,
               {col('quote_decimals')} AS quote_decimals,
               {col('pool_program')} AS pool_program,
               {col('vault_validation_status')} AS vault_validation_status,
               COALESCE({col('vault_resolution_state')}, CASE
                   WHEN {col('vault_validation_status')} = 'validated' THEN 'resolved'
                   WHEN {col('vault_validation_status')} = 'pending' THEN 'pending'
                   WHEN {col('vault_validation_status')} = 'rejected' THEN 'rejected'
                   ELSE NULL END) AS vault_resolution_state,
               COALESCE({col('vault_discovery_strategy')}, {col('discovery_method')}) AS vault_discovery_strategy,
               {col('discovery_method')} AS vault_discovery_method,
               {col('vault_discovery_attempts')} AS vault_discovery_attempts,
               CASE WHEN {col('vault_discovery_time_secs')} IS NOT NULL THEN {col('vault_discovery_time_secs')}
                    WHEN {col('last_vault_validation_at')} IS NOT NULL AND {col('created_at')} IS NOT NULL
                    THEN CAST(({col('last_vault_validation_at')} - {col('created_at')}) AS REAL) ELSE NULL END AS vault_discovery_time_secs,
               {col('created_at')} AS created_at,
               {col('last_vault_validation_at')} AS last_vault_validation_at,
               COALESCE({col('vault_resolved_at')}, {col('last_vault_validation_at')}) AS vault_resolved_at
        FROM token_pool_accounts tpa
    """


def _vault_row_to_dict(row: sqlite3.Row, base_account_counts: Dict[str, int] = None) -> Dict[str, Any]:
    base = row['base_account']; account_type = 'unknown'
    if base and base_account_counts is not None:
        count = base_account_counts.get(base, 0)
        account_type = 'shared_vault_signature' if count >= 10 else 'shared_program_vault' if count >= 5 else 'token_vault' if count else 'unknown'
    elif base:
        try: account_type = get_classifier().classify_account(base)
        except Exception: pass
    labels = {'shared_vault_signature':'Shared Vault (pump.fun)','shared_program_vault':'Shared Vault (Program)','token_vault':'Token Vault','unknown':'Unknown'}
    attempts = row['vault_discovery_attempts']
    try: attempts = int(attempts) if attempts is not None else None
    except (TypeError, ValueError): attempts = None
    return {'mint':row['mint'],'pool_address':row['pool_address'],'base_account':base,'base_account_type':account_type,'base_account_type_label':labels.get(account_type,'Unknown'),'quote_account':row['quote_account'],'base_token':row['base_token'],'quote_token':row['quote_token'],'base_decimals':row['base_decimals'],'quote_decimals':row['quote_decimals'],'pool_program':row['pool_program'],'vault_validation_status':row['vault_validation_status'],'vault_resolution_state':row['vault_resolution_state'],'vault_discovery_strategy':row['vault_discovery_strategy'] or row['vault_discovery_method'],'vault_discovery_method':row['vault_discovery_method'],'vault_discovery_attempts':attempts,'vault_discovery_time_secs':_format_nullable_float(row['vault_discovery_time_secs'],3),'created_at':row['created_at'],'last_vault_validation_at':row['last_vault_validation_at'],'vault_resolved_at':row['vault_resolved_at']}


def _build_vault_debug(row: sqlite3.Row) -> dict:
    strategy, created, validated = row['vault_discovery_strategy'], row['created_at'], row['last_vault_validation_at']
    return {'has_explicit_discovery_data': bool(strategy and strategy != 'unknown'),'using_fallback_strategy': not bool(strategy and strategy != 'unknown'),'using_fallback_time':row['vault_discovery_time_secs'] is None,'row_updated':bool(validated and created and validated > created),'raw':{key:row[key] for key in ('vault_discovery_strategy','vault_discovery_attempts','vault_discovery_time_secs','vault_resolution_state','vault_resolved_at','vault_discovery_method','vault_validation_status','created_at','last_vault_validation_at')}}


def _vault_rows_with_counts(conn, rows):
    bases=[r['base_account'] for r in rows if r['base_account']]
    counts={}
    if bases:
        p=','.join('?' * len(bases))
        counts={r['base_account']:r['cnt'] for r in conn.execute(f"SELECT base_account, COUNT(DISTINCT mint) AS cnt FROM token_pool_accounts WHERE base_account IN ({p}) GROUP BY base_account", bases).fetchall()}
    return [_vault_row_to_dict(r, counts) for r in rows]


@dashboard_routes.route('/api/vaults', methods=['GET'])
def api_vaults():
    try:
        conn=db_connect(DB_PATH, timeout=5); conn.row_factory=sqlite3.Row
        status=request.args.get('status','all'); strategy=request.args.get('strategy','all'); mint=request.args.get('mint','').strip(); pool=request.args.get('pool','').strip(); limit=min(int(request.args.get('limit',100)),500)
        query=f"SELECT * FROM ({_build_vaults_select(conn)}) v WHERE 1=1"; params=[]
        if status != 'all': query += ' AND v.vault_validation_status = ?'; params.append(status)
        if strategy != 'all': query += " AND COALESCE(v.vault_discovery_strategy, '') = ?"; params.append(strategy)
        if mint: query += ' AND v.mint LIKE ?'; params.append(f'%{mint}%')
        if pool: query += " AND COALESCE(v.pool_address, '') LIKE ?"; params.append(f'%{pool}%')
        query += ' ORDER BY v.created_at DESC, v.mint ASC LIMIT ?'; params.append(limit)
        data=_vault_rows_with_counts(conn, conn.execute(query,params).fetchall()); conn.close()
        return no_cache_json({'vaults':data,'total':len(data),'filters':{'status':status,'strategy':strategy,'mint':mint,'pool':pool,'limit':limit},'last_updated':int(time.time())})
    except Exception as e:
        logger.error('Error fetching vaults: %s', e, exc_info=True); return no_cache_json({'error':str(e)}),500


@dashboard_routes.route('/api/vaults/stats/summary', methods=['GET'])
def api_vaults_stats_summary():
    try:
        conn=db_connect(DB_PATH, timeout=5); conn.row_factory=sqlite3.Row
        row=conn.execute(f"SELECT COUNT(*) total_records, SUM(CASE WHEN vault_validation_status='validated' THEN 1 ELSE 0 END) validated_count, SUM(CASE WHEN vault_validation_status='pending' THEN 1 ELSE 0 END) pending_count, SUM(CASE WHEN vault_validation_status='rejected' THEN 1 ELSE 0 END) rejected_count, ROUND(AVG(vault_discovery_time_secs),1) avg_discovery_time_secs, ROUND(AVG(vault_discovery_attempts),2) avg_attempts FROM ({_build_vaults_select(conn)}) v").fetchone(); conn.close()
        return no_cache_json({'total_records':row['total_records'] or 0,'validated_count':row['validated_count'] or 0,'pending_count':row['pending_count'] or 0,'rejected_count':row['rejected_count'] or 0,'avg_discovery_time_secs':_format_nullable_float(row['avg_discovery_time_secs'],1),'avg_attempts':_format_nullable_float(row['avg_attempts'],2),'last_updated':int(time.time())})
    except Exception as e:
        logger.error('Error fetching vault stats: %s',e,exc_info=True); return no_cache_json({'error':str(e)}),500


@dashboard_routes.route('/api/vaults/stats/discovery-health', methods=['GET'])
def api_vaults_stats_discovery_health():
    try:
        conn=db_connect(DB_PATH,timeout=5); conn.row_factory=sqlite3.Row; cols=_table_columns(conn,'token_pool_accounts')
        required={'vault_resolution_state','vault_discovery_strategy','vault_discovery_attempts','vault_discovery_time_secs'}
        if not required.issubset(cols): conn.close(); return no_cache_json({'overall':{},'by_strategy':[],'last_updated':int(time.time())})
        rows=conn.execute("SELECT COALESCE(vault_discovery_strategy, discovery_method, 'unknown') strategy, COUNT(*) total, SUM(CASE WHEN vault_resolution_state='resolved' THEN 1 ELSE 0 END) resolved, SUM(CASE WHEN vault_resolution_state='pending' THEN 1 ELSE 0 END) pending, SUM(CASE WHEN vault_resolution_state='rejected' THEN 1 ELSE 0 END) rejected, ROUND(AVG(vault_discovery_attempts),2) avg_attempts, ROUND(AVG(vault_discovery_time_secs),1) avg_resolution_time_secs FROM token_pool_accounts GROUP BY COALESCE(vault_discovery_strategy, discovery_method, 'unknown') ORDER BY total DESC").fetchall(); conn.close()
        return no_cache_json({'overall':{'total_records':sum(r['total'] for r in rows),'resolved_count':sum(r['resolved'] or 0 for r in rows),'pending_count':sum(r['pending'] or 0 for r in rows),'rejected_count':sum(r['rejected'] or 0 for r in rows)},'by_strategy':[dict(r) for r in rows],'last_updated':int(time.time())})
    except Exception as e:
        logger.error('Error fetching discovery health: %s',e,exc_info=True); return no_cache_json({'error':str(e)}),500


@dashboard_routes.route('/api/vaults/<mint>', methods=['GET'])
def api_vault_detail(mint):
    try:
        conn=db_connect(DB_PATH,timeout=5); conn.row_factory=sqlite3.Row
        row=conn.execute(f"SELECT * FROM ({_build_vaults_select(conn)}) v WHERE v.mint=? LIMIT 1",(mint,)).fetchone()
        if not row: conn.close(); return jsonify({'error':'Vault/token not found','mint':mint}),404
        data=_vault_row_to_dict(row, {}); data['debug']=_build_vault_debug(row); conn.close()
        return no_cache_json({'mint':mint,'vault':data,'last_updated':int(time.time())})
    except Exception as e:
        logger.error('Error fetching vault detail: %s',e,exc_info=True); return no_cache_json({'error':str(e)}),500
