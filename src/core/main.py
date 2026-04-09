#!/usr/bin/env python3
"""
Pump.Fun → PumpSwap Migration Tracking UI

Displays tokens that have migrated from Pump.Fun to PumpSwap with:
- Post-migration risk scores
- Token analysis metrics
- Detection times
- Current live prices
"""

import sqlite3
import json
import requests
import threading
from datetime import datetime
from flask import Flask, jsonify, render_template, render_template_string, request, Response, abort
from flask_compress import Compress
from typing import Dict, List, Optional
import os
import time
import logging
from src.utils.infra_mapping import highlight_infra_in_funding
from src.core.flex_dashboard_routes import MIN_LIVE_MARKET_CAP

# Webhook system - M5 webhook-first low-RPC architecture
try:
    from src.apis.webhook_integration import init_webhook_system
    from src.apis.webhook_api_enriched import setup_enriched_routes
    WEBHOOK_ENABLED = True
except ImportError as e:
    WEBHOOK_ENABLED = False
    print(f"[WARNING] Webhook system not available: {e}")

# Database
DB_PATH = os.environ.get('DB_PATH', 'database/flex_complete_database.db')

# Flask app - set template folder to project root templates/
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
app = Flask(__name__, template_folder=os.path.join(PROJECT_ROOT, 'templates'), static_folder=os.path.join(PROJECT_ROOT, 'static'))
Compress(app)  # gzip all text/html and application/json responses automatically

from flask_sock import Sock as _Sock
sock = _Sock(app)

# Suppress Werkzeug request logging
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# Analysis result cache for background operations
app.funder_analysis_cache = {}

# Database capability flags (checked on app startup)
app.has_networks_release = None  # Set to True/False on first request

# =========================================================================
# WEBHOOK SYSTEM INITIALIZATION (M5)
# =========================================================================
if WEBHOOK_ENABLED:
    try:
        init_webhook_system(app)
        setup_enriched_routes(app)
        print("[WEBHOOK] M5 Webhook-First Low-RPC Architecture initialized successfully")
    except Exception as e:
        import traceback
        print(f"[ERROR] Failed to initialize webhook system: {e}")
        traceback.print_exc()
        WEBHOOK_ENABLED = False

# =========================================================================
# HELIUS OPTIMIZATION API INITIALIZATION
# =========================================================================
try:
    from http_instrumentation.optimization_api import register_optimization_routes
    register_optimization_routes(app, db_path=DB_PATH)
    print("[OPTIMIZATION] Helius optimization metrics API routes registered successfully")
except ImportError as e:
    print(f"[WARNING] Optimization API not available: {e}")
except Exception as e:
    print(f"[ERROR] Failed to initialize optimization API: {e}")

# =========================================================================
# RPC SAVINGS & EFFICIENCY DASHBOARD APIS (Phase 2 & 3)
# =========================================================================
try:
    from src.apis.rpc_savings_api import (
        get_dashboard_data,
        query_daily_savings,
        query_dashboard_24h,
        query_section_breakdown,
    )
    from src.apis.rpc_efficiency_api import (
        query_daily_efficiency,
        query_efficiency_24h,
        query_efficiency_all_time,
        query_efficiency_by_section,
        query_health_status,
        get_efficiency_dashboard,
    )
    import src.apis.rpc_metrics_api
    from dataclasses import asdict

    # RPC Savings Dashboard Routes
    @app.route('/api/rpc-savings/dashboard')
    def api_rpc_savings_dashboard():
        try:
            days = request.args.get('days', 30, type=int)
            data = get_dashboard_data(days)
            return jsonify(data)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/rpc-savings/24h')
    def api_rpc_savings_24h():
        try:
            data = query_dashboard_24h()
            if data:
                return jsonify(asdict(data))
            return jsonify({})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/rpc-savings/daily')
    def api_rpc_savings_daily():
        try:
            days = request.args.get('days', 30, type=int)
            metrics = query_daily_savings(days)
            return jsonify([asdict(m) for m in metrics])
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/rpc-savings/by-section')
    def api_rpc_savings_by_section():
        try:
            days = request.args.get('days', 30, type=int)
            breakdown = query_section_breakdown(days)
            return jsonify([asdict(b) for b in breakdown])
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    # RPC Efficiency Score Routes
    @app.route('/api/rpc-efficiency/dashboard')
    def api_rpc_efficiency_dashboard():
        try:
            days = request.args.get('days', 30, type=int)
            data = get_efficiency_dashboard(days)
            return jsonify(data)
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/rpc-efficiency/24h')
    def api_rpc_efficiency_24h():
        try:
            data = query_efficiency_24h()
            if data:
                return jsonify(asdict(data))
            return jsonify({})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/rpc-efficiency/daily')
    def api_rpc_efficiency_daily():
        try:
            days = request.args.get('days', 30, type=int)
            metrics = query_daily_efficiency(days)
            return jsonify([asdict(m) for m in metrics])
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/rpc-efficiency/all-time')
    def api_rpc_efficiency_all_time():
        try:
            data = query_efficiency_all_time()
            if data:
                return jsonify(asdict(data))
            return jsonify({})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/rpc-efficiency/by-section')
    def api_rpc_efficiency_by_section():
        try:
            days = request.args.get('days', 30, type=int)
            metrics = query_efficiency_by_section(days)
            return jsonify([asdict(m) for m in metrics])
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/rpc-efficiency/health')
    def api_rpc_efficiency_health():
        try:
            days = request.args.get('days', 7, type=int)
            reports = query_health_status(days)
            return jsonify([asdict(r) for r in reports])
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    print("[RPC_SAVINGS] RPC savings and efficiency dashboard APIs registered successfully")
except ImportError as e:
    print(f"[WARNING] RPC savings/efficiency APIs not available: {e}")
except Exception as e:
    print(f"[ERROR] Failed to initialize RPC savings/efficiency APIs: {e}")

# =========================================================================
# DATABASE CAPABILITY CHECK
# =========================================================================

def check_networks_release_capability() -> bool:
    """
    Check if networks_release table exists in the database.

    Used for safe Phase 2A rollout:
    - If table exists → use new network release paths
    - If not → use legacy paths
    - Allows rollback by deploying older DB schema

    Returns:
        bool: True if networks_release table exists, False otherwise
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='networks_release'"
        )
        result = cursor.fetchone() is not None
        conn.close()
        return result
    except Exception as e:
        # On error, assume old path (conservative fallback)
        print(f"[CAPABILITY_CHECK] Error checking networks_release: {e}")
        return False


@app.before_request
def initialize_capability_check():
    """
    Initialize database capability check on first request.
    Cached in app.has_networks_release to avoid repeated queries.
    """
    if app.has_networks_release is None:
        app.has_networks_release = check_networks_release_capability()
        status = "ENABLED" if app.has_networks_release else "DISABLED"
        print(f"[CAPABILITY_CHECK] Phase 2A networks_release: {status}")

# =========================================================================
# PHASE 2C HELPERS
# =========================================================================

def get_db_conn():
    """
    Open database connection with row_factory configured.

    Returns:
        tuple: (conn, cursor) - configured connection and cursor
    """
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    return conn, cursor


def get_networks_release_list(include_evidence=False):
    """
    Get all networks from networks_release table.

    Args:
        include_evidence (bool): If True, LEFT JOIN network_evidence

    Returns:
        list: List of dict rows from networks_release
    """
    conn, cursor = get_db_conn()

    if include_evidence:
        cursor.execute("""
            SELECT
                nr.network_name,
                nr.network_size,
                nr.network_risk_level,
                nr.network_type,
                nr.has_cex_funder,
                nr.has_infra_funder,
                nr.cex_funder_count,
                nr.infra_funder_count,
                nr.stability_state,
                nr.build_version,
                nr.last_built_at,
                COALESCE(ne.total_edges, 0) as evidence_edges,
                COALESCE(ne.average_confidence, 0) as evidence_confidence,
                COALESCE(ne.evidence_risk_score, 0) as evidence_risk_score
            FROM networks_release nr
            LEFT JOIN network_evidence ne ON nr.network_name = ne.network_name
            ORDER BY nr.network_size DESC, nr.network_name ASC
        """)
    else:
        cursor.execute("""
            SELECT * FROM networks_release
            ORDER BY network_size DESC, network_name ASC
        """)

    networks = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return networks


def get_network_release_by_name(network_name, include_evidence=False):
    """
    Get single network from networks_release by name.

    Args:
        network_name (str): Name of network
        include_evidence (bool): If True, LEFT JOIN network_evidence

    Returns:
        dict or None: Network row as dict, or None if not found
    """
    conn, cursor = get_db_conn()

    if include_evidence:
        cursor.execute("""
            SELECT
                nr.network_name,
                nr.network_size,
                nr.network_risk_level,
                nr.network_type,
                nr.has_cex_funder,
                nr.has_infra_funder,
                nr.cex_funder_count,
                nr.infra_funder_count,
                nr.stability_state,
                nr.build_version,
                nr.last_built_at,
                COALESCE(ne.total_edges, 0) as evidence_edges,
                COALESCE(ne.average_confidence, 0) as evidence_confidence,
                COALESCE(ne.evidence_risk_score, 0) as evidence_risk_score
            FROM networks_release nr
            LEFT JOIN network_evidence ne ON nr.network_name = ne.network_name
            WHERE nr.network_name = ?
        """, (network_name,))
    else:
        cursor.execute("""
            SELECT * FROM networks_release WHERE network_name = ?
        """, (network_name,))

    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_network_score(network_name: str) -> dict:
    """
    Retrieve precomputed network score for UI display.
    
    Returns dict with:
    - score: 0-100 integer
    - score_version: version of scoring model
    - components: dict with {connectivity, lifecycle, evidence} breakdown
    - score_badge: 'high' (70+), 'medium' (30-69), or 'low' (0-29)
    """
    try:
        conn, cursor = get_db_conn()
        cursor.execute('''
            SELECT 
              score,
              score_version,
              score_components_json
            FROM network_scores
            WHERE network_name = ?
        ''', (network_name,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return {
                'score': None,
                'score_version': None,
                'components': None,
                'score_badge': None,
            }
        
        score = row['score']
        components = json.loads(row['score_components_json']) if row['score_components_json'] else {}
        
        # Determine risk badge
        if score >= 70:
            badge = 'high'
        elif score >= 30:
            badge = 'medium'
        else:
            badge = 'low'
        
        return {
            'score': score,
            'score_version': row['score_version'],
            'components': components,
            'score_badge': badge,
        }
    except Exception as e:
        print(f"[ERROR] get_network_score: {e}")
        return {
            'score': None,
            'score_version': None,
            'components': None,
            'score_badge': None,
        }


def get_latest_alerts(limit: int = 100) -> list:
    """
    Get latest network alerts for monitoring dashboard.

    Returns list of dicts with:
    - network_name
    - alert_type (SCORE_SPIKE, NEW_HIGH_RISK, TYPE_FLIP, LIFECYCLE_FLIP)
    - severity (low, medium, high)
    - message
    - details_json (parsed as dict)
    - created_at
    """
    try:
        conn, cursor = get_db_conn()
        cursor.execute('''
            SELECT
              network_name,
              alert_type,
              severity,
              message,
              details_json,
              created_at
            FROM network_alerts
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))

        alerts = []
        for row in cursor.fetchall():
            alerts.append({
                'network_name': row['network_name'],
                'alert_type': row['alert_type'],
                'severity': row['severity'],
                'message': row['message'],
                'details': json.loads(row['details_json']) if row['details_json'] else {},
                'created_at': row['created_at'],
            })

        conn.close()
        return alerts
    except Exception as e:
        print(f"[ERROR] get_latest_alerts: {e}")
        return []


def get_top_risky_networks(limit: int = 50) -> list:
    """
    Get current top risky networks by score.

    Returns list of dicts with:
    - network_name
    - score
    - score_badge (high/medium/low)
    """
    try:
        conn, cursor = get_db_conn()
        cursor.execute('''
            SELECT
              network_name,
              score
            FROM network_scores
            ORDER BY score DESC
            LIMIT ?
        ''', (limit,))

        networks = []
        for row in cursor.fetchall():
            score = row['score']
            badge = 'high' if score >= 70 else ('medium' if score >= 30 else 'low')
            networks.append({
                'network_name': row['network_name'],
                'score': score,
                'score_badge': badge,
            })

        conn.close()
        return networks
    except Exception as e:
        print(f"[ERROR] get_top_risky_networks: {e}")
        return []


def get_biggest_score_movers(limit: int = 50) -> list:
    """
    Get networks with biggest score changes in the last build.

    Returns list of dicts with:
    - network_name
    - delta (change in score)
    - prev_score
    - curr_score
    """
    try:
        conn, cursor = get_db_conn()
        cursor.execute('''
            SELECT
              h.network_name,
              (h.score - p.score) AS delta,
              p.score AS prev_score,
              h.score AS curr_score
            FROM network_score_history h
            JOIN network_score_history p
              ON p.network_name = h.network_name
              AND p.build_version = h.build_version - 1
            WHERE h.build_version = (SELECT MAX(build_version) FROM network_score_history)
            ORDER BY delta DESC
            LIMIT ?
        ''', (limit,))

        movers = []
        for row in cursor.fetchall():
            movers.append({
                'network_name': row['network_name'],
                'delta': row['delta'],
                'prev_score': row['prev_score'],
                'curr_score': row['curr_score'],
            })

        conn.close()
        return movers
    except Exception as e:
        print(f"[ERROR] get_biggest_score_movers: {e}")
        return []


def get_network_members(network_name):
    """
    Get member creators for a network from network_membership.

    Args:
        network_name (str): Name of network

    Returns:
        list: List of dict rows with creator_address
    """
    conn, cursor = get_db_conn()

    cursor.execute("""
        SELECT creator_address
        FROM network_membership
        WHERE network_name = ?
        ORDER BY creator_address
    """, (network_name,))

    members = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return members


def get_network_name_from_id(network_id):
    """
    Convert numeric network_id to network_name using deterministic ordering.

    Uses ORDER BY network_name ASC to ensure consistent 1-based index mapping.
    Prefers networks_release if available, falls back to creator_networks.

    Args:
        network_id (int): Numeric network ID (1-based index)

    Returns:
        str or None: Network name, or None if ID out of range
    """
    conn, cursor = get_db_conn()

    # Try networks_release first (new path)
    try:
        cursor.execute("""
            SELECT network_name
            FROM networks_release
            ORDER BY network_name ASC
        """)
        all_networks = [row['network_name'] for row in cursor.fetchall()]
    except sqlite3.OperationalError:
        # Fall back to creator_networks (legacy path)
        cursor.execute("""
            SELECT DISTINCT network_name
            FROM creator_networks
            WHERE network_name IS NOT NULL
            ORDER BY network_name ASC
        """)
        all_networks = [row['network_name'] for row in cursor.fetchall()]

    conn.close()

    if network_id < 1 or network_id > len(all_networks):
        return None

    return all_networks[network_id - 1]


def route_phase2c(endpoint_name, new_fn, legacy_fn):
    """
    Route Phase 2C endpoint to new or legacy implementation based on capability.

    Handles:
    - Logging path selection
    - Exception handling for HTML responses
    - JSON/HTML response formatting
    - Response object type handling

    Args:
        endpoint_name (str): Name of endpoint for logging
        new_fn (callable): Function to call if networks_release exists
                          Must return (response_obj, status_code)
                          response_obj can be dict/list/Response/HTML string
        legacy_fn (callable): Function to call if networks_release missing
                             Must return (response_obj, status_code)

    Returns:
        Response: Flask response (HTML or JSON)
    """
    from collections.abc import Mapping

    # PHASE3A: Optional force mode for benchmarking (isolated, easy to remove)
    force_mode = os.environ.get('PHASE2C_FORCE_MODE', '').lower()
    use_new_path = app.has_networks_release
    if force_mode == 'new':
        use_new_path = True
    elif force_mode == 'legacy':
        use_new_path = False

    try:
        if use_new_path:
            print(f"[PHASE2C] {endpoint_name} using networks_release path", flush=True)
            result, status_code = new_fn()
        else:
            print(f"[PHASE2C] {endpoint_name} using legacy path", flush=True)
            result, status_code = legacy_fn()

        # Handle Flask Response objects first (check before dict/list)
        if isinstance(result, Response):
            result.status_code = status_code
            return result
        
        # Handle JSON responses (dict or list)
        if isinstance(result, Mapping) or isinstance(result, list):
            return jsonify(result), status_code
        
        # Handle string/HTML responses or None
        if result is None:
            # Graceful fallback for None
            if endpoint_name.startswith('/api'):
                return jsonify({'error': 'No response generated'}), 500
            else:
                return f"<h1>Error</h1><p>No response generated</p>", 500
        
        # Handle string responses (HTML, etc.)
        return result, status_code

    except Exception as e:
        print(f"[PHASE2C_ERROR] {endpoint_name}: {e}", flush=True)
        if endpoint_name.startswith('/api'):
            return jsonify({'error': str(e)}), 500
        else:
            return f"<h1>Error</h1><p>{str(e)}</p>", 500


# =========================================================================
# DATABASE QUERIES
# =========================================================================

def get_migrated_tokens(limit: int = 25, light: bool = True) -> List[Dict]:
    """
    Get recent migrated tokens for UI display.

    light=True:
        Fast homepage path. One main query only, no per-token enrichment queries.
    light=False:
        Full enrichment path for detail-heavy pages.
    """
    try:
        from src.utils.infra_mapping import CEX_ACCOUNTS

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        now_ts = int(time.time())
        cursor.execute("""
            SELECT
                ta.mint,
                ta.analyzed_at,
                ta.created_at,
                ta.events_parsed,
                ta.rug_probability,
                ta.risk_level,
                ta.post_migration_coverage,
                ta.price_current,
                ta.price_highest,
                ta.market_cap_current,
                ta.market_cap_highest,
                ta.market_cap_highest_at,
                ta.rug_indicator,
                ta.earliest_tx_creator,
                ta.creator_is_blocked,
                ta.network_risk,
                ta.connected_malicious_count,
                ta.cluster_id,
                ta.cluster_name,
                ta.cluster_risk_multiplier,
                ta.network_funder_address,
                COALESCE(cn.network_name, ta.network_name) as network_name,
                ta.network_tier,
                ta.network_is_cex,
                COALESCE(tsc.last_updated, 0) as snap_last_updated,
                tps.price_usd as snap_price_usd,
                tps.market_cap as snap_market_cap,
                tmp.peak_market_cap as peaks_market_cap,
                tmp.peak_market_cap_at as peaks_market_cap_at
            FROM token_analysis ta
            LEFT JOIN creator_networks cn
                ON ta.earliest_tx_creator = cn.creator_address
            LEFT JOIN token_snapshot_counts tsc
                ON tsc.mint = ta.mint
            LEFT JOIN token_market_cap_peaks tmp
                ON tmp.mint = ta.mint
            LEFT JOIN token_price_snapshots tps
                ON tps.snapshot_id = (
                    SELECT snapshot_id FROM token_price_snapshots
                    WHERE mint = ta.mint
                    ORDER BY captured_at DESC LIMIT 1
                )
            WHERE ta.mint IS NOT NULL
              AND (
                  COALESCE(tps.market_cap, 0) >= ?
                  OR CAST(COALESCE(
                      CASE WHEN CAST(ta.created_at AS REAL) > 1000000000
                           THEN CAST(ta.created_at AS INTEGER)
                           ELSE CAST(strftime('%s', ta.created_at) AS INTEGER)
                      END, 0) AS INTEGER) >= ?
              )
            ORDER BY
                CAST(COALESCE(
                    CASE WHEN CAST(ta.created_at AS REAL) > 1000000000
                         THEN CAST(ta.created_at AS INTEGER)
                         ELSE CAST(strftime('%s', ta.created_at) AS INTEGER)
                    END, 0) AS INTEGER) >= ? DESC,
                (COALESCE(tsc.last_updated, 0) > ?) DESC,
                COALESCE(tsc.last_updated, 0) DESC,
                COALESCE(tps.market_cap, 0) DESC,
                ta.created_at DESC
            LIMIT ?
        """, (MIN_LIVE_MARKET_CAP, now_ts - 900, now_ts - 1800, now_ts - 60, limit,))

        rows = cursor.fetchall()

        if light:
            tokens = []
            for row in rows:
                tokens.append({
                    'mint': row['mint'],
                    'analyzed_at': row['analyzed_at'],
                    'created_at': row['created_at'],
                    'rug_probability': row['rug_probability'] if row['rug_probability'] else 0,
                    'risk_level': row['risk_level'],
                    'total_txs': 0,
                    'total_events': row['events_parsed'] if row['events_parsed'] else 0,
                    'coverage': row['post_migration_coverage'] if row['post_migration_coverage'] else 0,
                    'price_current': row['snap_price_usd'] or None,
                    'price_highest': row['price_highest'] if row['price_highest'] else None,
                    'market_cap_current': row['snap_market_cap'] or None,
                    'market_cap_highest': row['peaks_market_cap'] or row['market_cap_highest'] or None,
                    'market_cap_highest_at': row['market_cap_highest_at'] if row['market_cap_highest_at'] else None,
                    'rug_indicator': row['rug_indicator'],
                    'creator': row['earliest_tx_creator'] if row['earliest_tx_creator'] else None,
                    'creator_is_blocked': bool(row['creator_is_blocked']) if row['creator_is_blocked'] else False,
                    'network_risk': bool(row['network_risk']) if row['network_risk'] else False,
                    'connected_malicious_count': row['connected_malicious_count'] if row['connected_malicious_count'] else 0,
                    'creator_infra_tags': [],
                    'top_funder': None,
                    'funding_checked': False,
                    'funding_progress': {
                        'status': 'skipped',
                        'progress_percent': 0,
                        'funder_count': 0,
                        'sources_extracted': 0,
                        'completion_ratio': '0/0',
                    },
                    'network_name': row['cluster_name'],
                    'network_id': row['cluster_id'],
                    'cluster_id': row['cluster_id'] if row['cluster_id'] else None,
                    'cluster_name': row['cluster_name'] if row['cluster_name'] else None,
                    'cluster_risk_multiplier': row['cluster_risk_multiplier'] if row['cluster_risk_multiplier'] else 1.0,
                    'atomic_network_name': row['network_name'] if row['network_name'] else None,
                    'atomic_network_tier': row['network_tier'] if row['network_tier'] else None,
                    'atomic_network_is_cex': bool(row['network_is_cex']) if row['network_is_cex'] else False,
                    'snap_age': (now_ts - row['snap_last_updated']) if row['snap_last_updated'] else 99999,
                })
            conn.close()
            return tokens

        # Full enrichment path
        tokens = []
        for row in rows:
            creator_infra_tags = []
            top_funder = None
            funding_checked = False

            if row['earliest_tx_creator']:
                seen_tags = set()

                cursor.execute("""
                    SELECT tag, description, amount_sol FROM creator_tags
                    WHERE creator_address = ? AND tag IN (?, ?, ?, ?)
                """, (row['earliest_tx_creator'], 'uses_jitotip', 'uses_axiom', 'uses_debridge', 'uses_meteora'))
                for tag_row in cursor.fetchall():
                    tag_name = tag_row[0]
                    if tag_name not in seen_tags:
                        seen_tags.add(tag_name)
                        creator_infra_tags.append({'tag': tag_name, 'description': tag_row[1], 'amount_sol': tag_row[2]})

                cursor.execute("""
                    SELECT DISTINCT tag FROM creator_service_history
                    WHERE creator_address = ?
                """, (row['earliest_tx_creator'],))
                for service_tag_row in cursor.fetchall():
                    tag_name = service_tag_row[0]
                    if tag_name not in seen_tags:
                        seen_tags.add(tag_name)
                        tag_desc = {
                            'uses_jitotip': 'Uses Jito tips on CREATE transaction',
                            'uses_jitotip_other': 'Uses Jito MEV tips on transactions',
                            'uses_meteora': 'Uses Meteora DLMM liquidity',
                            'uses_debridge': 'Uses deBridge cross-chain transfers',
                            'uses_axiom': 'Uses Axiom for verification'
                        }.get(tag_name, f'Uses {tag_name}')
                        creator_infra_tags.append({'tag': tag_name, 'description': tag_desc, 'amount_sol': None})

                cursor.execute("""
                    SELECT COUNT(DISTINCT cf.funder_address) as coordinated_count
                    FROM creator_funders cf
                    WHERE cf.creator_address = ?
                      AND cf.is_cex = 0
                      AND cf.funder_address IN (SELECT funder_address FROM coordinated_funders)
                """, (row['earliest_tx_creator'],))
                coordinated_result = cursor.fetchone()
                if coordinated_result and coordinated_result[0] > 0 and 'Multi-Funder' not in seen_tags:
                    seen_tags.add('Multi-Funder')
                    creator_infra_tags.append({'tag': 'Multi-Funder', 'description': 'Funded by account(s) supporting multiple creators', 'amount_sol': None})

                cursor.execute("""
                    SELECT funder_address, cex_exchange, cex_type, amount_sol, is_cex
                    FROM creator_funders
                    WHERE creator_address = ?
                    ORDER BY amount_sol DESC
                    LIMIT 1
                """, (row['earliest_tx_creator'],))
                funder_row = cursor.fetchone()
                if funder_row:
                    funder_addr = funder_row[0]
                    is_cex = bool(funder_row[4])
                    cex_exchange = funder_row[1]
                    cex_type = funder_row[2]
                    if not is_cex and funder_addr in CEX_ACCOUNTS:
                        is_cex = True
                        cex_info = CEX_ACCOUNTS[funder_addr]
                        cex_exchange = cex_info.get('exchange', cex_info.get('name', 'CEX'))
                        cex_type = cex_info.get('category', 'Wallet')
                    top_funder = {
                        'address': funder_addr,
                        'cex_exchange': cex_exchange,
                        'cex_type': cex_type,
                        'amount_sol': funder_row[3],
                        'is_cex': is_cex
                    }

                cursor.execute("""
                    SELECT COUNT(*) as funding_count FROM creator_funders
                    WHERE creator_address = ?
                """, (row['earliest_tx_creator'],))
                funding_result = cursor.fetchone()
                funding_checked = funding_result[0] > 0 if funding_result else False

            funding_progress = (
                calculate_funding_progress(row['earliest_tx_creator'])
                if row['earliest_tx_creator']
                else {
                    'status': 'unknown',
                    'progress_percent': 0,
                    'funder_count': 0,
                    'sources_extracted': 0,
                    'completion_ratio': '0/0'
                }
            )

            tokens.append({
                'mint': row['mint'],
                'analyzed_at': row['analyzed_at'],
                'created_at': row['created_at'],
                'rug_probability': row['rug_probability'] if row['rug_probability'] else 0,
                'risk_level': row['risk_level'],
                'total_txs': 0,
                'total_events': row['events_parsed'] if row['events_parsed'] else 0,
                'coverage': row['post_migration_coverage'] if row['post_migration_coverage'] else 0,
                'price_current': row['snap_price_usd'] or None,
                'price_highest': row['price_highest'] if row['price_highest'] else None,
                'market_cap_current': row['snap_market_cap'] or None,
                'market_cap_highest': row['peaks_market_cap'] or row['market_cap_highest'] or None,
                'market_cap_highest_at': row['market_cap_highest_at'] if row['market_cap_highest_at'] else None,
                'rug_indicator': row['rug_indicator'],
                'creator': row['earliest_tx_creator'] if row['earliest_tx_creator'] else None,
                'creator_is_blocked': bool(row['creator_is_blocked']) if row['creator_is_blocked'] else False,
                'network_risk': bool(row['network_risk']) if row['network_risk'] else False,
                'connected_malicious_count': row['connected_malicious_count'] if row['connected_malicious_count'] else 0,
                'creator_infra_tags': creator_infra_tags,
                'top_funder': top_funder,
                'funding_checked': funding_checked,
                'funding_progress': funding_progress,
                'network_name': row['cluster_name'],
                'network_id': row['cluster_id'],
                'cluster_id': row['cluster_id'] if row['cluster_id'] else None,
                'cluster_name': row['cluster_name'] if row['cluster_name'] else None,
                'cluster_risk_multiplier': row['cluster_risk_multiplier'] if row['cluster_risk_multiplier'] else 1.0,
                'atomic_network_name': row['network_name'] if row['network_name'] else None,
                'atomic_network_tier': row['network_tier'] if row['network_tier'] else None,
                'atomic_network_is_cex': bool(row['network_is_cex']) if row['network_is_cex'] else False
            })

        conn.close()
        return tokens
    except Exception as e:
        import traceback
        print(f"[DB] Error fetching analyzed tokens: {e}")
        traceback.print_exc()
        return []


def calculate_funding_progress(creator_address: str) -> Dict:
    """
    Calculate funding extraction progress for a creator
    Progress tracks how many of the direct funders have had their sources extracted
    Returns: {
        'status': 'complete' | 'in_progress' | 'pending',
        'progress_percent': 0-100,
        'funder_count': int (total direct funders),
        'sources_extracted': int (how many funders have had sources traced),
        'completion_ratio': 'X/Y' (e.g., '3/10')
    }

    Progress calculation:
    - 0%: No funders identified yet
    - X%: (sources_extracted / funder_count) * 100
    - 100%: All funders' sources have been extracted
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get direct funders count
        cursor.execute("""
            SELECT COUNT(*) as count FROM creator_funders
            WHERE creator_address = ?
        """, (creator_address,))
        result = cursor.fetchone()
        funder_count = result[0] if result else 0

        # Get list of funders
        cursor.execute("""
            SELECT DISTINCT funder_address FROM creator_funders
            WHERE creator_address = ?
        """, (creator_address,))
        funder_addresses = [row[0] for row in cursor.fetchall()]

        # For each funder, check if they have:
        # 1. Incoming transfers extracted
        # 2. Are INFRA endpoints (outgoing but no incoming)
        # 3. Have been analyzed even if no transfers found (last_analyzed IS NOT NULL)
        sources_count = 0
        if funder_addresses:
            placeholders = ','.join(['?' for _ in funder_addresses])

            # Count funders with incoming transfers extracted
            cursor.execute(f"""
                SELECT COUNT(DISTINCT funder_address) as count
                FROM funder_incoming_transfers
                WHERE funder_address IN ({placeholders})
            """, funder_addresses)
            result = cursor.fetchone()
            sources_count = result[0] if result else 0

            # Count INFRA terminal endpoints (they have outgoing but no incoming - traced to endpoint)
            cursor.execute(f"""
                SELECT COUNT(DISTINCT funder_address) as count
                FROM funder_outgoing_transfers
                WHERE funder_address IN ({placeholders})
                AND funder_address NOT IN (
                    SELECT DISTINCT funder_address FROM funder_incoming_transfers
                    WHERE funder_address IN ({placeholders})
                )
            """, funder_addresses + funder_addresses)
            result = cursor.fetchone()
            infra_count = result[0] if result else 0

            # Count funders that have been analyzed but found no transfers
            # (These are empty wallets or wallets with no transaction history)
            cursor.execute(f"""
                SELECT COUNT(DISTINCT cf.funder_address) as count
                FROM creator_funders cf
                WHERE cf.creator_address = ?
                AND cf.last_analyzed IS NOT NULL
                AND cf.funder_address NOT IN (
                    SELECT DISTINCT funder_address FROM funder_incoming_transfers
                    WHERE funder_address IN ({placeholders})
                )
                AND cf.funder_address NOT IN (
                    SELECT DISTINCT funder_address FROM funder_outgoing_transfers
                    WHERE funder_address IN ({placeholders})
                )
            """, (creator_address,) + tuple(funder_addresses) + tuple(funder_addresses))
            result = cursor.fetchone()
            empty_analyzed_count = result[0] if result else 0

            # Total completed = funders with sources + INFRA endpoints + analyzed empty wallets
            sources_count += infra_count + empty_analyzed_count

        # Check if extraction is currently in progress
        # This happens when we have funders but none have been analyzed yet (last_analyzed is NULL)
        extraction_in_progress = False
        if funder_count > 0 and sources_count == 0:
            cursor.execute("""
                SELECT COUNT(*) FROM creator_funders
                WHERE creator_address = ? AND last_analyzed IS NULL
            """, (creator_address,))
            result_check = cursor.fetchone()
            unanalyzed_count = result_check[0] if result_check else 0
            extraction_in_progress = unanalyzed_count > 0

        conn.close()

        # Calculate progress as percentage of funders with sources extracted
        if funder_count == 0:
            status = 'pending'
            progress = 0
            completion_ratio = '0/0'
        else:
            progress = int((sources_count / funder_count) * 100)
            completion_ratio = f'{sources_count}/{funder_count}'

            if progress == 0:
                # Check if extraction is running or truly pending
                status = 'extracting' if extraction_in_progress else 'pending'
            elif progress == 100:
                status = 'complete'
            else:
                status = 'in_progress'

        return {
            'status': status,
            'progress_percent': progress,
            'funder_count': funder_count,
            'sources_extracted': sources_count,
            'completion_ratio': completion_ratio
        }
    except Exception as e:
        print(f"[PROGRESS] Error calculating funding progress: {e}")
        return {
            'status': 'unknown',
            'progress_percent': 0,
            'funder_count': 0,
            'sources_extracted': 0,
            'completion_ratio': '0/0'
        }



def get_cex_infra_label(address: str) -> Optional[str]:
    """Get CEX/INFRA label for an address from cex_wallets table"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT exchange_name, wallet_type FROM cex_wallets
            WHERE cex_address = ?
            LIMIT 1
        """, (address,))

        result = cursor.fetchone()
        conn.close()

        if result:
            wallet_type = result['wallet_type']
            exchange_name = result['exchange_name']

            # Don't include "Hot Wallet" or "Exchange Wallet" in the UI
            if wallet_type in ('Hot Wallet', 'Exchange Wallet'):
                return exchange_name
            else:
                return f"{exchange_name} ({wallet_type})"
        return None
    except Exception:
        return None


def format_cross_references_display(cross_refs_data: Dict) -> str:
    """
    Format cross-reference data for display/CLI output.
    Shows INBOUND and OUTBOUND connections clearly.
    
    Args:
        cross_refs_data: Dict with 'inbound' and 'outbound' keys containing cross-reference lists
    
    Returns:
        Formatted string showing cross-references
    """
    output = []
    inbound = cross_refs_data.get('inbound', [])
    outbound = cross_refs_data.get('outbound', [])
    
    if inbound:
        output.append("\n📥 INBOUND CROSS-REFERENCES (Shared Funders):")
        output.append("=" * 80)
        for ref in inbound:
            output.append(f"\n  Funder: {ref['address'][:16]}...")
            output.append(f"    ├─ Shared with {ref['creator_count']} other creator(s)")
            output.append(f"    └─ {ref['description']}")
            if ref['other_creators'] and len(ref['other_creators']) <= 5:
                for other_creator in ref['other_creators'][:5]:
                    output.append(f"      • {other_creator[:16]}...")
    
    if outbound:
        output.append("\n📤 OUTBOUND CROSS-REFERENCES (Shared Recipients):")
        output.append("=" * 80)
        for ref in outbound:
            output.append(f"\n  Recipient: {ref['address'][:16]}...")
            output.append(f"    ├─ Shared with {ref['creator_count']} other creator(s)")
            output.append(f"    └─ {ref['description']}")
            if ref['other_creators'] and len(ref['other_creators']) <= 5:
                for other_creator in ref['other_creators'][:5]:
                    output.append(f"      • {other_creator[:16]}...")
    
    if not inbound and not outbound:
        return "  ✓ No cross-creator links detected"
    
    return "\n".join(output)


def build_network_key(funder_address: str, is_cex: bool, upstream_sender: Optional[str] = None, funding_time: Optional[int] = None) -> tuple:
    """
    Build network key for grouping creators.
    Returns: (network_key_type, network_key, upstream_sender, time_bucket)

    Option A (best): CEX + upstream sender (depositing wallet)
    Option B (fallback): CEX + time bucket window (1 hour buckets)
    """
    CEX_TIME_BUCKET_SECONDS = 3600  # 1 hour buckets

    # Organic funders are grouped by funder address only
    if not is_cex:
        return ('ORGANIC_FUNDER', funder_address, None, None)

    # CEX funders: prefer upstream sender if available
    if upstream_sender:
        network_key = f"CEX_UPSTREAM:{funder_address}::{upstream_sender}"
        return ('CEX_UPSTREAM', network_key, upstream_sender, None)

    # Fallback to time bucket if timestamps exist
    if funding_time:
        time_bucket = funding_time - (funding_time % CEX_TIME_BUCKET_SECONDS)
        network_key = f"CEX_BATCH:{funder_address}::bucket:{time_bucket}"
        return ('CEX_BATCH', network_key, None, time_bucket)

    # Last resort: do NOT form a network (too broad)
    network_key = f"CEX_TAG_ONLY:{funder_address}"
    return ('CEX_TAG_ONLY', network_key, None, None)


def get_network_key_for_funder(funder_address: str) -> tuple:
    """Get the network key for a funder by analyzing its incoming transfers and creation time"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Check if this is a CEX funder
        cursor.execute("""
            SELECT is_cex FROM creator_funders
            WHERE funder_address = ?
            LIMIT 1
        """, (funder_address,))

        result = cursor.fetchone()
        is_cex = bool(result['is_cex']) if result else False

        if not is_cex:
            conn.close()
            return build_network_key(funder_address, False)

        # For CEX funders, get upstream sender (most common one)
        cursor.execute("""
            SELECT sender_address, block_time
            FROM funder_incoming_transfers
            WHERE funder_address = ?
            ORDER BY block_time DESC
            LIMIT 1
        """, (funder_address,))

        transfer = cursor.fetchone()
        upstream_sender = transfer['sender_address'] if transfer else None
        funding_time = transfer['block_time'] if transfer else None

        # Get earliest creator funding time as fallback
        if not funding_time:
            cursor.execute("""
                SELECT MIN(CAST(strftime('%s', first_detected_at) AS INTEGER)) as earliest_time
                FROM creator_funders
                WHERE funder_address = ?
            """, (funder_address,))

            time_result = cursor.fetchone()
            funding_time = time_result['earliest_time'] if time_result else None

        conn.close()
        return build_network_key(funder_address, is_cex, upstream_sender, funding_time)

    except Exception as e:
        print(f"[ERROR] get_network_key_for_funder: {e}")
        return build_network_key(funder_address, False)

def get_sidebar_css(bg_color: str = "rgba(20, 20, 30, 0.9)") -> str:
    """Generate sidebar CSS styling with matching background color"""
    return f"""
        /* Sidebar Navigation */
        .sidebar {{
            position: fixed;
            top: 0;
            left: 0;
            width: 180px;
            height: 100vh;
            background: {bg_color};
            border-right: 1px solid var(--border-color, rgba(167, 139, 250, 0.3));
            display: flex;
            flex-direction: column;
            padding: 20px 0;
            z-index: 100;
        }}

        .sidebar-logo {{
            padding: 0 16px 20px;
            border-bottom: 1px solid var(--border-color, rgba(167, 139, 250, 0.3));
            font-size: 16px;
            font-weight: 700;
            color: var(--accent-cyan, #06b6d4);
            letter-spacing: 1px;
        }}

        .sidebar-nav {{
            flex: 1;
            display: flex;
            flex-direction: column;
            padding: 12px 0;
            gap: 2px;
        }}

        .sidebar-item {{
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 16px;
            color: var(--text-secondary, #9ca3af);
            cursor: pointer;
            border-radius: 6px;
            margin: 0 8px;
            font-size: 13px;
            font-weight: 500;
            text-decoration: none;
            transition: background 0.15s, color 0.15s;
            border: none;
            background: none;
            width: calc(100% - 16px);
            text-align: left;
        }}

        .sidebar-item:hover {{
            background: rgba(255,255,255,0.07);
            color: var(--text-primary, #e5e7eb);
        }}

        .sidebar-item.active {{
            background: rgba(6, 182, 212, 0.15);
            color: var(--accent-cyan, #06b6d4);
            border-left: 3px solid var(--accent-cyan, #06b6d4);
            padding-left: 13px;
        }}

        .sidebar-item.green {{
            color: #22c55e;
        }}

        .sidebar-item.green:hover {{
            background: rgba(34, 197, 94, 0.1);
        }}

        /* Push main content right of sidebar */
        body {{
            padding-left: 196px;
            margin: 0;
        }}

        /* Consistent content container alignment */
        .container {{
            width: 100%;
            max-width: 100%;
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
    """


def get_sidebar_html(active_page: str = "tokens") -> str:
    """Generate the left sidebar navigation HTML"""
    pages = {
        "tokens": ("Tokens", "/"),
        "networks": ("Networks", "/networks"),
        "clusters": ("Clusters", "/clusters"),
        "coordinated-funders": ("Coordinated Funders", "/coordinated-funders"),
        "hubs": ("Hubs", "/top-funding-hubs"),
        "creator-analysis": ("Creator Analysis", "/creator-analysis"),
        "webhook": ("Transfers", "/webhook-monitor"),
        "rpc-savings": ("RPC", "/rpc-savings-dashboard"),
        "early-signals": ("🧠 Early Predictions", "/early-signals"),
    }

    items = ""
    for page_key, (label, url) in pages.items():
        is_active = "active" if page_key == active_page else ""
        style_class = "green" if page_key == "rpc-savings" else ""
        extra_class = f"{is_active} {style_class}".strip()
        items += f'<a class="sidebar-item {extra_class}" href="{url}">{label}</a>\n            '

    return f"""
    <!-- Left Sidebar Navigation -->
    <div class="sidebar">
        <div class="sidebar-logo">FLEX</div>
        <nav class="sidebar-nav">
            {items}
        </nav>
    </div>
    """


# =========================================================================
# FLASK ROUTES
# =========================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pump.Fun → PumpSwap Migration </title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        :root {
            /* Primary Colors - SolanaFM Professional Dark */
            --primary: #7c3aed;
            --primary-dark: #6d28d9;
            --primary-light: rgba(124, 58, 237, 0.15);

            /* Text Colors - Professional Gray Scale */
            --text-primary: #e5e7eb;
            --text-secondary: #9ca3af;
            --text-dark: #1f2937;
            --text-light: #f3f4f6;

            /* Risk & Reuse Levels - Professional Palette */
            --color-critical: #ef4444;
            --color-high: #f97316;
            --color-medium: #eab308;
            --color-low: #22c55e;
            --color-none: #6b7280;

            /* Backgrounds - SolanaFM Dark Navy/Purple */
            --bg-primary: #1a1a24;
            --bg-secondary: rgba(20, 20, 32, 0.85);
            --bg-overlay: rgba(124, 58, 237, 0.08);

            /* Accents - SolanaFM Vibrant */
            --accent-cyan: #06b6d4;
            --accent-green: #22c55e;
            --accent-purple: #a78bfa;
            --color-purple: #a78bfa;

            /* Address/Mint Colors - Light Purple */
            --address-color: #a78bfa;
            --border-color: rgba(167, 139, 250, 0.3);
        }

        html {
            height: 100%;
            background: linear-gradient(135deg, #0a0a0e 0%, #0d0d15 100%);
            background-attachment: fixed;
            background-repeat: no-repeat;
            overflow-y: scroll;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
            background: transparent;
            color: var(--text-primary);
            padding: 30px;
            margin: 0;
            padding-left: 196px;
            min-height: 100vh;
        }

        .container {
            max-width: 100%;
            margin: 0;
            padding: 0;
            width: 100%;
            box-sizing: border-box;
        }

        .header {
            background: var(--bg-secondary);
            padding: 30px;
            border-radius: 12px;
            margin-bottom: 30px;
            border-left: 4px solid var(--accent-cyan);
            margin-left: 0;
        }

        .header h1 {
            font-size: 28px;
            margin-bottom: 10px;
            color: #a78bfa;
        }

        .header p {
            color: var(--text-secondary);
            font-size: 14px;
        }

        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
        }

        .stat-card {
            background: var(--bg-secondary);
            padding: 20px;
            border-radius: 8px;
            border: 1px solid rgba(6, 182, 212, 0.2);
        }

        .stat-label {
            color: var(--text-secondary);
            font-size: 12px;
            text-transform: uppercase;
            margin-bottom: 8px;
        }

        .stat-value {
            font-size: 28px;
            font-weight: bold;
            color: #06b6d4;
        }

        .tokens-table {
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-secondary);
            border-radius: 8px;
            overflow: hidden;
        }

        .tokens-table thead {
            background: var(--bg-secondary);
            border-bottom: 2px solid rgba(124, 58, 237, 0.3);
        }

        .tokens-table th {
            padding: 15px;
            text-align: left;
            font-size: 13px;
            color: #a78bfa;
            font-weight: bold;
        }

        .tokens-table th.sortable {
            cursor: pointer;
            user-select: none;
            position: relative;
            transition: background-color 0.2s;
        }

        .tokens-table th.sortable:hover {
            background: rgba(124, 58, 237, 0.1);
        }

        .tokens-table th.sorted-asc::after {
            content: ' ↑';
            font-size: 12px;
            margin-left: 5px;
        }

        .tokens-table th.sorted-desc::after {
            content: ' ↓';
            font-size: 12px;
            margin-left: 5px;
        }

        .tokens-table td {
            padding: 15px;
            border-bottom: 1px solid rgba(6, 182, 212, 0.1);
            font-size: 12px;
        }


        .tokens-table tbody tr:hover {
            background: rgba(6, 182, 212, 0.05);
        }

        .tokens-table td {
            transition: opacity 0.2s ease;
            opacity: 1;
        }

        .mint {
            font-family: 'Courier New', monospace;
            font-size: 11px;
            color: var(--accent-cyan);
            max-width: 350px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }

        .risk-score {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 600;
        }

        .risk-low {
            background: rgba(34, 197, 94, 0.2);
            color: var(--color-low);
        }

        .risk-medium {
            background: rgba(234, 179, 8, 0.2);
            color: var(--color-medium);
        }

        .risk-high {
            background: rgba(239, 68, 68, 0.2);
            color: var(--color-critical);
        }

        .rug-badge {
            display: inline-block;
            background: rgba(239, 68, 68, 0.25);
            color: var(--color-critical);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            border: 1px solid rgba(239, 68, 68, 0.5);
        }

        .safe-badge {
            display: inline-block;
            background: rgba(34, 197, 94, 0.15);
            color: var(--color-low);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }

        .creator-pump_fun_official {
            display: inline-block;
            background: rgba(59, 130, 246, 0.15);
            color: var(--color-none);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            border: 1px solid rgba(59, 130, 246, 0.5);
        }

        .creator-malicious {
            display: inline-block;
            background: rgba(239, 68, 68, 0.25);
            color: var(--color-critical);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            border: 1px solid rgba(239, 68, 68, 0.5);
        }

        .creator-unknown {
            display: inline-block;
            background: rgba(156, 163, 175, 0.15);
            color: var(--text-secondary);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }

        .creator-blocked {
            display: inline-block;
            background: rgba(239, 68, 68, 0.3);
            color: var(--color-critical);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            border: 1px solid rgba(239, 68, 68, 0.7);
            animation: pulse 2s infinite;
        }

        .network-risk {
            display: inline-block;
            background: rgba(249, 115, 22, 0.3);
            color: var(--color-high);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
            border: 1px solid rgba(249, 115, 22, 0.7);
            margin-right: 4px;
        }

        @keyframes pulse {
            0%, 100% {
                opacity: 1;
            }
            50% {
                opacity: 0.8;
            }
        }

        .price-positive {
            color: var(--color-low);
        }

        .price-negative {
            color: var(--color-critical);
        }

        .time-badge {
            background: rgba(124, 58, 237, 0.1);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            color: var(--accent-cyan);
        }

        .loading {
            text-align: center;
            padding: 40px;
            color: var(--text-secondary);
        }

        .no-data {
            text-align: center;
            padding: 40px;
            color: var(--text-secondary);
            background: var(--bg-secondary);
            border-radius: 8px;
            margin-top: 20px;
        }

        .refresh-info {
            color: var(--text-secondary);
            font-size: 12px;
            margin-top: 20px;
            text-align: center;
        }

        /* Mint cell with embedded creator */
        .mint-with-creator {
            display: flex;
            flex-direction: column;
            gap: 4px;
            align-items: flex-start;
        }

        /* Creator address embedded under mint */
        .creator-address-embedded {
            font-family: 'Courier New', monospace;
            font-size: 10px;
            color: var(--text-secondary);
            word-break: break-all;
            max-width: 250px;
            line-height: 1.4;
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 3px;
            flex-wrap: wrap;
        }

        /* Creator address link - clickable text, not a badge */
        .creator-address-link {
            display: inline !important;
            padding: 0 !important;
            background: none !important;
            border: none !important;
            color: var(--accent-cyan) !important;
            text-decoration: underline !important;
            cursor: pointer !important;
            font-family: 'Courier New', monospace !important;
            font-size: 10px !important;
        }

        .creator-address-link:hover {
            color: var(--accent-cyan);
            text-decoration-thickness: 2px;
        }

        /* Creator tags container - styles handled by inline styles on wrapper div */
        .creator-tags {
            /* Table cell - flex styles applied via wrapper div */
        }

        /* Base creator tag styling */
        .creator-tag {
            display: inline-block;
            padding: 3px 6px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            white-space: nowrap;
            border: 1px solid rgba(6, 182, 212, 0.3);
            background: rgba(6, 182, 212, 0.15);
            color: var(--accent-cyan);
        }

        /* Network size tag (cyan) */
        .tag-network {
            background: rgba(6, 182, 212, 0.15);
            color: var(--accent-cyan);
            border: 1px solid rgba(6, 182, 212, 0.3);
        }

        /* Funding tag (cyan) */
        .tag-funding {
            background: rgba(6, 182, 212, 0.15);
            color: var(--accent-cyan);
            border: 1px solid rgba(6, 182, 212, 0.3);
        }

        /* Repeat launcher tag (cyan) */
        .tag-repeat {
            background: rgba(6, 182, 212, 0.15);
            color: var(--accent-cyan);
            border: 1px solid rgba(6, 182, 212, 0.3);
        }

        /* Blocked tag (cyan) */
        .tag-blocked {
            background: rgba(6, 182, 212, 0.15);
            color: var(--accent-cyan);
            border: 1px solid rgba(239, 68, 68, 0.3);
            font-weight: 700;
        }

        /* Creator infrastructure tags container */
        .creator-infra-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 5px;
            margin: 0;
            align-items: center;
        }

        /* Small infrastructure tag for creator address display */
        .creator-infra-tags .infra-tag {
            display: inline-block;
            padding: 2px 5px;
            border-radius: 2px;
            font-size: 9px;
            font-weight: 600;
            border: 1px solid;
        }

        .creator-infra-tags .tag {
            display: inline-block;
            padding: 1px 4px;
            border-radius: 2px;
            font-size: 8px;
            background: rgba(0, 0, 0, 0.3);
            color: var(--text-secondary);
        }

        /* Category-specific colors for creator display */
        .creator-infra-tags .infra-automation {
            background: rgba(168, 85, 247, 0.2);
            color: var(--accent-purple);
            border-color: rgba(168, 85, 247, 0.3);
        }

        .creator-infra-tags .infra-cex {
            background: rgba(34, 197, 94, 0.2);
            color: var(--color-low);
            border-color: rgba(34, 197, 94, 0.3);
        }

        .creator-infra-tags .infra-system {
            background: rgba(107, 114, 128, 0.2);
            color: var(--text-primary);
            border-color: rgba(107, 114, 128, 0.3);
        }

        .creator-infra-tags .infra-validator {
            background: rgba(59, 130, 246, 0.2);
            color: var(--color-none);
            border-color: rgba(59, 130, 246, 0.3);
        }

        .creator-infra-tags .infra-bridge {
            background: rgba(249, 115, 22, 0.2);
            color: var(--color-high);
            border-color: rgba(249, 115, 22, 0.3);
        }

        .creator-infra-tags .infra-relay {
            background: rgba(249, 115, 22, 0.2);
            color: var(--color-high);
            border-color: rgba(249, 115, 22, 0.3);
        }

        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0, 0, 0, 0.8);
            animation: fadeIn 0.3s;
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        .modal-content {
            background-color: var(--bg-primary);
            margin: 5% auto;
            padding: 30px;
            border: 1px solid rgba(6, 182, 212, 0.3);
            border-radius: 12px;
            width: 90%;
            max-width: 800px;
            max-height: 80vh;
            overflow-y: auto;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
        }

        .modal-content h2 {
            color: var(--accent-cyan);
            margin-bottom: 20px;
            font-size: 20px;
        }

        .modal-content h3 {
            color: var(--accent-cyan);
            margin-top: 20px;
            margin-bottom: 10px;
            font-size: 14px;
            text-transform: uppercase;
        }

        .close {
            color: var(--text-secondary);
            float: right;
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
            line-height: 20px;
        }

        .close:hover,
        .close:focus {
            color: var(--accent-cyan);
        }

        .address {
            color: var(--address-color);
            font-family: monospace;
            font-size: 12px;
            word-break: break-all;
        }

        .mint {
            color: var(--address-color);
            font-family: monospace;
            font-size: 12px;
            word-break: break-all;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .metric {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            border-radius: 8px;
            border-left: 3px solid var(--accent-cyan);
        }

        .metric label {
            display: block;
            color: var(--text-secondary);
            font-size: 11px;
            text-transform: uppercase;
            margin-bottom: 8px;
        }

        .metric span {
            display: block;
            color: var(--accent-cyan);
            font-size: 16px;
            font-weight: 600;
            font-family: 'Courier New', monospace;
        }

        .risk-section {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 15px;
        }

        .risk-section p {
            margin: 8px 0;
            color: var(--text-primary);
        }

        .risk-section label {
            color: var(--text-secondary);
            font-size: 12px;
            text-transform: uppercase;
        }

        .risk-value {
            color: var(--accent-cyan);
            font-weight: 600;
            margin-left: 10px;
        }

        .mint-link {
            cursor: pointer;
            color: var(--address-color) !important;
            text-decoration: none;
            border-bottom: 1px dotted var(--address-color);
            transition: all 0.2s;
        }

        .mint-link.creator-address-link {
            text-decoration: underline !important;
            border-bottom: none !important;
        }

        .mint-link:hover {
            text-decoration: none;
            opacity: 0.8;
        }

        /* Network indicator badges */
        .network-badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 12px;
            margin-left: 5px;
            font-weight: 600;
        }

        .network-badge.network-high {
            background: rgba(239, 68, 68, 0.2);
            color: var(--color-critical);
            border: 1px solid rgba(239, 68, 68, 0.4);
            cursor: help;
        }

        .network-badge.network-medium {
            background: rgba(245, 158, 11, 0.2);
            color: var(--color-medium);
            border: 1px solid rgba(245, 158, 11, 0.4);
            cursor: help;
        }

        .network-badge.network-low {
            background: rgba(59, 130, 246, 0.2);
            color: var(--color-none);
            border: 1px solid rgba(59, 130, 246, 0.4);
            cursor: help;
        }

        .shared-badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 3px;
            font-size: 12px;
            margin-left: 5px;
            background: rgba(124, 58, 237, 0.2);
            color: var(--accent-purple);
            border: 1px solid rgba(124, 58, 237, 0.4);
            cursor: help;
        }

        /* Highlight rows with network connections */
        .row-network-coordinator {
            background: rgba(239, 68, 68, 0.05) !important;
            border-left: 2px solid rgba(239, 68, 68, 0.3) !important;
        }

        .row-shared-recipient {
            background: rgba(124, 58, 237, 0.05) !important;
            border-left: 2px solid rgba(124, 58, 237, 0.3) !important;
        }

        /* Creator stats grid */
        .creator-stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .stat-box {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            border-radius: 8px;
            border-left: 3px solid var(--accent-cyan);
            text-align: center;
        }

        .stat-box label {
            display: block;
            color: var(--text-secondary);
            font-size: 12px;
            margin-bottom: 8px;
            text-transform: uppercase;
        }

        .stat-box span {
            display: block;
            color: var(--accent-cyan);
            font-size: 18px;
            font-weight: bold;
        }

        /* Super-cluster tab buttons */
        .sc-tab-button {
            padding: 10px 20px !important;
            background: none !important;
            border: none !important;
            color: var(--text-secondary) !important;
            cursor: pointer !important;
            font-size: 14px !important;
            transition: color 0.2s ease !important;
        }

        .sc-tab-button:hover {
            color: var(--accent-cyan) !important;
        }

        .sc-tab-button.active {
            color: var(--accent-cyan) !important;
            border-bottom: 2px solid var(--accent-cyan) !important;
        }

        #creatorTagsContainer {
            margin-bottom: 20px;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .creator-tag {
            background: rgba(34, 197, 94, 0.15);
            border: 1px solid rgba(34, 197, 94, 0.3);
            padding: 5px 9px;
            border-radius: 6px;
            cursor: help;
        }

        .tag-label {
            color: var(--color-low);
            font-size: 13px;
            font-weight: 600;
        }

        /* Tokens launched table */
        .tokens-launched-container {
            max-height: 300px;
            overflow-y: auto;
            margin-bottom: 20px;
        }

        .tokens-launched-table {
            width: 100%;
            border-collapse: collapse;
        }

        .tokens-launched-table th {
            background: rgba(0, 0, 0, 0.3);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: var(--text-secondary);
            border-bottom: 1px solid rgba(6, 182, 212, 0.2);
        }

        .tokens-launched-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .tokens-launched-table tr:hover {
            background: rgba(6, 182, 212, 0.05);
        }

        /* Top funders table */
        .top-funders-container {
            max-height: 200px;
            overflow-y: auto;
            margin-bottom: 20px;
        }

        .top-funders-table {
            width: 100%;
            border-collapse: collapse;
        }

        .top-funders-table th {
            background: rgba(0, 0, 0, 0.3);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: var(--text-secondary);
            border-bottom: 1px solid rgba(6, 182, 212, 0.2);
        }

        .top-funders-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        /* Cluster info */
        .cluster-info {
            background: rgba(0, 0, 0, 0.3);
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
        }

        .cluster-info p {
            margin: 5px 0;
            color: var(--text-primary);
            font-size: 12px;
        }

        /* CEX badge */
        .cex-badge {
            display: inline-block;
            padding: 2px 6px;
            border-radius: 3px;
            background: rgba(34, 197, 94, 0.2);
            color: var(--color-low);
            font-size: 10px;
            font-weight: 600;
            margin-left: 5px;
        }

        /* CEX Funders Section */
        .cex-funders-container {
            max-height: 250px;
            overflow-y: auto;
            margin-bottom: 20px;
            padding: 15px;
            background: rgba(34, 197, 94, 0.05);
            border-left: 3px solid #4ade80;
            border-radius: 4px;
        }

        .cex-funders-table {
            width: 100%;
            border-collapse: collapse;
        }

        .cex-funders-table th {
            background: rgba(34, 197, 94, 0.15);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: var(--color-low);
            border-bottom: 2px solid rgba(34, 197, 94, 0.3);
            font-weight: 600;
        }

        .cex-funders-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(34, 197, 94, 0.1);
            color: var(--text-primary);
        }

        .cex-funders-table tr:hover {
            background: rgba(34, 197, 94, 0.1);
        }

        /* Multi-creator funders styling */
        .multi-creator-container {
            background: rgba(239, 68, 68, 0.05);
            padding: 15px;
            border-radius: 8px;
            border: 1px solid rgba(239, 68, 68, 0.2);
        }

        .multi-creator-container .cex-funders-table th {
            background: rgba(239, 68, 68, 0.15);
            color: var(--color-critical);
            border-bottom: 2px solid rgba(239, 68, 68, 0.3);
        }

        .multi-creator-container .cex-funders-table td {
            border-bottom: 1px solid rgba(239, 68, 68, 0.1);
        }

        .multi-creator-container .cex-funders-table tr:hover {
            background: rgba(239, 68, 68, 0.1);
        }

        .cex-exchange-name {
            color: var(--color-low);
            font-weight: 600;
            display: flex;
            align-items: center;
        }

        .cex-exchange-name::before {
            content: '🏛️';
            margin-right: 5px;
        }

        /* Jito Tips Table */
        .jitotips-table {
            width: 100%;
            border-collapse: collapse;
        }

        .jitotips-table th {
            background: rgba(0, 0, 0, 0.3);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: var(--text-secondary);
            border-bottom: 1px solid rgba(6, 182, 212, 0.2);
        }

        .jitotips-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .jitotips-table tr:hover {
            background: rgba(6, 182, 212, 0.05);
        }

        /* Funder stats grid (multi-creator funders modal) */
        .funder-stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        /* Funders table */
        .funders-container {
            max-height: 400px;
            overflow-y: auto;
            margin-bottom: 20px;
            border: 1px solid rgba(124, 58, 237, 0.2);
            border-radius: 6px;
            padding: 10px;
        }

        .funders-table {
            width: 100%;
            border-collapse: collapse;
        }

        .funders-table th {
            background: rgba(124, 58, 237, 0.15);
            padding: 10px;
            text-align: left;
            font-size: 12px;
            color: var(--accent-purple);
            border-bottom: 2px solid rgba(124, 58, 237, 0.3);
            font-weight: 600;
        }

        .funders-table td {
            padding: 10px;
            font-size: 12px;
            border-bottom: 1px solid rgba(124, 58, 237, 0.1);
            color: var(--text-primary);
        }

        .funders-table tr:hover {
            background: rgba(124, 58, 237, 0.1);
        }

        .funders-table a {
            color: var(--accent-purple);
            text-decoration: none;
            cursor: pointer;
        }

        .funders-table a:hover {
            text-decoration: underline;
        }

        /* Source type badges */
        .original-sender-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            background: rgba(34, 197, 94, 0.2);
            color: var(--color-low);
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        }

        .intermediary-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            background: rgba(59, 130, 246, 0.2);
            color: var(--color-none);
            font-size: 11px;
            font-weight: 600;
            white-space: nowrap;
        }

        /* Infrastructure tags */
        .infra-tag {
            display: inline-block;
            padding: 3px 7px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            margin-right: 4px;
            white-space: nowrap;
        }

        .infra-automation {
            background: rgba(168, 85, 247, 0.2);
            color: var(--accent-purple);
            border: 1px solid rgba(168, 85, 247, 0.3);
        }

        .infra-cex {
            background: rgba(34, 197, 94, 0.2);
            color: var(--color-low);
            border: 1px solid rgba(34, 197, 94, 0.3);
        }

        .infra-system {
            background: rgba(107, 114, 128, 0.2);
            color: var(--text-primary);
            border: 1px solid rgba(107, 114, 128, 0.3);
        }

        .infra-validator {
            background: rgba(59, 130, 246, 0.2);
            color: var(--color-none);
            border: 1px solid rgba(59, 130, 246, 0.3);
        }

        .infra-bridge {
            background: rgba(249, 115, 22, 0.2);
            color: var(--color-high);
            border: 1px solid rgba(249, 115, 22, 0.3);
        }

        .infra-relay {
            background: rgba(249, 115, 22, 0.2);
            color: var(--color-high);
            border: 1px solid rgba(249, 115, 22, 0.3);
        }

        /* Domain tags (SNS domains) */
        .domain-tag {
            display: inline-block;
            padding: 3px 7px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            margin-right: 4px;
            background: rgba(59, 130, 246, 0.2);
            color: var(--color-none);
            border: 1px solid rgba(59, 130, 246, 0.3);
            white-space: nowrap;
        }

        /* Other address tags */
        .address-tag {
            display: inline-block;
            padding: 3px 7px;
            border-radius: 3px;
            font-size: 10px;
            font-weight: 600;
            margin-right: 4px;
            background: rgba(124, 58, 237, 0.2);
            color: var(--accent-purple);
            border: 1px solid rgba(124, 58, 237, 0.3);
            white-space: nowrap;
        }

        .tag {
            display: inline-block;
            padding: 2px 5px;
            border-radius: 2px;
            font-size: 9px;
            margin-right: 2px;
            background: rgba(0, 0, 0, 0.3);
            color: var(--text-secondary);
        }

        .tag-infra {
            background: rgba(168, 85, 247, 0.15);
            color: var(--accent-purple);
        }

        .tag-automation {
            background: rgba(168, 85, 247, 0.15);
            color: var(--accent-purple);
        }

        .tag-oracle {
            background: rgba(34, 197, 94, 0.15);
            color: var(--color-low);
        }

        /* CREATE tx link */
        .create-tx-link {
            color: var(--accent-cyan);
            text-decoration: none;
            font-family: monospace;
        }

        .create-tx-link:hover {
            text-decoration: underline;
        }

        .controls-panel {
            background: rgba(0, 20, 40, 0.8);
            border: 1px solid rgba(6, 182, 212, 0.3);
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
            display: flex;
            gap: 30px;
            align-items: center;
            flex-wrap: wrap;
        }

        .control-group {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .control-label {
            color: var(--text-secondary);
            font-size: 14px;
            font-weight: 500;
        }

        .toggle-switch {
            position: relative;
            display: inline-block;
            width: 50px;
            height: 24px;
            background-color: var(--bg-secondary);
            border-radius: 12px;
            cursor: pointer;
            transition: background-color 0.3s;
            border: 1px solid rgba(6, 182, 212, 0.2);
        }

        .toggle-switch.active {
            background-color: var(--accent-cyan);
        }

        .toggle-slider {
            position: absolute;
            top: 2px;
            left: 2px;
            width: 20px;
            height: 20px;
            background-color: var(--bg-secondary);
            border-radius: 50%;
            transition: left 0.3s;
        }

        .toggle-switch.active .toggle-slider {
            left: 28px;
        }

        .status-indicator {
            display: inline-block;
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background-color: var(--color-critical);
            margin-left: 8px;
        }

        .status-indicator.active {
            background-color: var(--color-low);
        }

        .action-button {
            padding: 8px 14px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            transition: all 0.3s;
            white-space: nowrap;
        }

        .action-button.danger {
            background-color: rgba(239, 68, 68, 0.2);
            color: var(--color-critical);
            border: 1px solid rgba(239, 68, 68, 0.4);
        }

        .action-button.danger:hover {
            background-color: rgba(239, 68, 68, 0.35);
            border-color: rgba(239, 68, 68, 0.7);
            box-shadow: 0 0 10px rgba(239, 68, 68, 0.2);
        }

        .action-button.danger:active {
            background-color: rgba(239, 68, 68, 0.5);
        }

        /* Sidebar Navigation */
        .sidebar {
            position: fixed;
            top: 0;
            left: 0;
            width: 180px;
            height: 100vh;
            background: var(--bg-secondary);
            border-right: 1px solid var(--border-color);
            display: flex;
            flex-direction: column;
            padding: 20px 0;
            z-index: 100;
        }

        .sidebar-logo {
            padding: 0 16px 20px;
            border-bottom: 1px solid var(--border-color);
            font-size: 16px;
            font-weight: 700;
            color: var(--accent-cyan);
            letter-spacing: 1px;
        }

        .sidebar-nav {
            flex: 1;
            display: flex;
            flex-direction: column;
            padding: 12px 0;
            gap: 2px;
        }

        .sidebar-item {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 16px;
            color: var(--text-secondary);
            cursor: pointer;
            border-radius: 6px;
            margin: 0 8px;
            font-size: 13px;
            font-weight: 500;
            text-decoration: none;
            transition: background 0.15s, color 0.15s;
            border: none;
            background: none;
            width: calc(100% - 16px);
            text-align: left;
        }

        .sidebar-item:hover {
            background: rgba(255,255,255,0.07);
            color: var(--text-primary);
        }

        .sidebar-item.active {
            background: rgba(6, 182, 212, 0.15);
            color: var(--accent-cyan);
            border-left: 3px solid var(--accent-cyan);
            padding-left: 13px;
        }

        .sidebar-item.green {
            color: var(--text-secondary);
        }

        .sidebar-item.green:hover {
            background: rgba(255,255,255,0.07);
        }


        /* CEX View Styles */
        .cex-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .cex-exchange-card {
            background: rgba(34, 197, 94, 0.05);
            border: 1px solid rgba(34, 197, 94, 0.3);
            border-left: 3px solid #4ade80;
            border-radius: 6px;
            padding: 15px;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .cex-exchange-card:hover {
            background: rgba(34, 197, 94, 0.1);
            border-left: 4px solid #4ade80;
            box-shadow: 0 0 10px rgba(34, 197, 94, 0.1);
        }

        .cex-exchange-card h4 {
            color: var(--color-low);
            margin: 0 0 10px 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .cex-exchange-card .stat {
            display: flex;
            justify-content: space-between;
            margin: 8px 0;
            font-size: 13px;
            color: var(--text-primary);
        }

        .cex-exchange-card .stat-label {
            color: var(--text-secondary);
        }

        .cex-exchange-card .stat-value {
            color: var(--accent-cyan);
            font-weight: 600;
        }
    </style>
</head>
<body>
    <!-- Left Sidebar Navigation -->
    <div class="sidebar">
        <div class="sidebar-logo">FLEX</div>
        <nav class="sidebar-nav">
            <button class="sidebar-item active" id="tokensTabBtn" onclick="switchToTokensTab()">Tokens</button>
            <a class="sidebar-item" href="/networks">Networks</a>
            <a class="sidebar-item" href="/clusters">Clusters</a>
            <a class="sidebar-item" href="/coordinated-funders">Coordinated Funders</a>
            <a class="sidebar-item" href="/top-funding-hubs">Hubs</a>
            <a class="sidebar-item" href="/creator-analysis">Creator Analysis</a>
            <a class="sidebar-item" href="/webhook-monitor">Transfers</a>
            <a class="sidebar-item green" href="/rpc-savings-dashboard">RPC</a>
            <a class="sidebar-item" href="/early-signals" style="background: rgba(167, 139, 250, 0.15); color: #a78bfa; font-weight: bold;">🧠 Early Predictions</a>
            <a class="sidebar-item" href="/token-behaviour" style="background: rgba(59, 130, 246, 0.15); color: #3b82f6; font-weight: bold;">📊 Token Behaviour</a>
            <a class="sidebar-item" href="/system-health" style="background: rgba(34, 197, 94, 0.15); color: #22c55e; font-weight: bold;">💚 System Health</a>
            <hr style="margin: 10px 0; border: none; border-top: 1px solid rgba(255,255,255,0.1);">
            <a class="sidebar-item" href="/launch-radar" style="background: linear-gradient(135deg, #3b82f6, #8b5cf6); color: white; font-weight: bold;">📊 Intelligence</a>
        </nav>
    </div>

    <div class="container">
        <div class="stats" id="stats">
            <div class="stat-card">
                <div class="stat-label">Total Migrations</div>
                <div class="stat-value" id="total-migrations">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">With Pre-Analysis</div>
                <div class="stat-value" id="with-analysis">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">High Risk</div>
                <div class="stat-value" id="high-risk">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Low Risk</div>
                <div class="stat-value" id="low-risk">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Unique Creators</div>
                <div class="stat-value" id="unique-creators">0</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Repeat Launchers</div>
                <div class="stat-value" id="repeat-launchers">0</div>
            </div>
        </div>

        <div class="controls-panel">
            <div class="control-group">
                <span class="control-label">Token History Check</span>
                <div class="toggle-switch" id="tokenHistoryToggle" onclick="toggleTokenHistory()">
                    <div class="toggle-slider"></div>
                </div>
                <span class="status-indicator" id="tokenHistoryStatus"></span>
            </div>
            <div class="control-group" style="border-left: 1px solid rgba(6, 182, 212, 0.3); margin-left: 12px; padding-left: 12px;">
                <span class="control-label">Token Launch</span>
                <div class="toggle-switch" id="listenLaunchesToggle" onclick="toggleListenLaunches()">
                    <div class="toggle-slider"></div>
                </div>
                <span class="status-indicator" id="listenLaunchesStatus"></span>
            </div>
            <div class="control-group" style="border-left: 1px solid rgba(239, 68, 68, 0.3); margin-left: 12px; padding-left: 12px;">
                <span class="control-label">Auto Extract Funders</span>
                <div class="toggle-switch" id="autoExtractFundersToggle" onclick="toggleAutoExtractFunders()">
                    <div class="toggle-slider"></div>
                </div>
                <span class="status-indicator" id="autoExtractFundersStatus"></span>
            </div>
        </div>

        <div id="tokens-container">
            <div class="loading">Loading...</div>
        </div>

        <!-- CEX Funders View -->
        <div id="cex-container" style="display: none;">
            <div style="padding: 20px;">
                <h2 style="color: var(--color-low); margin-bottom: 20px;">🏛️ CEX Funders Activity</h2>

                <!-- CEX Exchanges Summary -->
                <div style="margin-bottom: 30px;">
                    <h3 style="color: var(--accent-cyan); margin-bottom: 15px;">Exchanges Funding Creators</h3>
                    <div id="cexExchangesContainer" class="cex-grid">
                        <div class="loading">Loading CEX exchanges...</div>
                    </div>
                </div>

                <!-- Top CEX Funder Wallets -->
                <div style="margin-bottom: 30px;">
                    <h3 style="color: var(--accent-cyan); margin-bottom: 15px;">Top CEX Wallet Funders</h3>
                    <div id="topCexFundersContainer" style="overflow-x: auto;">
                        <table class="tokens-table" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th>CEX Address</th>
                                    <th>Exchange</th>
                                    <th>Wallet Type</th>
                                    <th>Creators Funded</th>
                                    <th>Total SOL</th>
                                </tr>
                            </thead>
                            <tbody id="topCexFundersBody">
                                <tr><td colspan="5" style="text-align: center; color: var(--text-secondary);">Loading...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- CEX-Funded Creators -->
                <div>
                    <h3 style="color: var(--accent-cyan); margin-bottom: 15px;">Creators Funded by CEX</h3>
                    <div id="cexFundedCreatorsContainer" style="overflow-x: auto;">
                        <table class="tokens-table" style="width: 100%;">
                            <thead>
                                <tr>
                                    <th>Creator Address</th>
                                    <th>Exchanges Funding</th>
                                    <th>Total CEX Funding (SOL)</th>
                                    <th>Action</th>
                                </tr>
                            </thead>
                            <tbody id="cexFundedCreatorsBody">
                                <tr><td colspan="4" style="text-align: center; color: var(--text-secondary);">Loading...</td></tr>
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>

        <div class="refresh-info">Auto-refreshing every 5 seconds</div>
    </div>

    <!-- Metrics Modal -->
    <div id="metricsModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeTokenMetrics()">&times;</span>
            <h2>Token Metrics - <span id="modalMint" style="font-family: monospace; font-size: 14px; color: var(--address-color);"></span></h2>

            <h3>Risk Metrics</h3>
            <div class="metrics-grid" id="metricsGrid">
                <!-- Populated by JavaScript -->
            </div>

            <h3>Risk Scores</h3>
            <div class="risk-section" id="riskSection">
                <!-- Populated by JavaScript -->
            </div>

            <h3>Pools & Vaults</h3>
            <div id="firstPriceLatencyRow" style="margin-bottom: 12px; display: none;">
                <span style="color: var(--text-secondary); font-size: 12px;">1st Price Latency:</span>
                <span id="firstPriceLatencyValue" style="font-weight: bold; margin-left: 8px; font-family: monospace;"></span>
                <span id="firstPriceSourceBadge" style="margin-left: 8px; padding: 2px 6px; border-radius: 4px; font-size: 11px;"></span>
            </div>
            <div id="poolsSection" style="margin-bottom: 20px;">
                <table class="cex-funders-table">
                    <thead>
                        <tr>
                            <th>Pool Address</th>
                            <th>Base Vault</th>
                            <th>Quote Vault</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody id="poolsBody">
                        <tr><td colspan="4" style="text-align: center; color: var(--text-secondary);">Loading pools...</td></tr>
                    </tbody>
                </table>
            </div>

            <div style="margin-top: 20px; padding-top: 20px; border-top: 1px solid rgba(6, 182, 212, 0.2);">
                <p style="color: var(--text-secondary); font-size: 12px;">
                    💡 <strong>Tip:</strong> Click "DexTools" link below to view live trading data
                </p>
                <a id="dextoolsLink" href="#" target="_blank" style="color: var(--accent-cyan); margin-top: 10px; display: inline-block;">
                    → View on DexTools
                </a>
            </div>
        </div>
    </div>

    <!-- Creator Details Modal -->
    <div id="creatorModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeCreatorDetails()">&times;</span>
            <h2>Creator Details - <span id="modalCreator" style="font-family: monospace; font-size: 14px; color: var(--address-color);"></span></h2>

            <!-- Creator Stats Summary -->
            <div class="creator-stats-grid">
                <div class="stat-box">
                    <label>Total Tokens</label>
                    <span id="creatorTotalTokens">—</span>
                </div>
                <div class="stat-box">
                    <label>Funding Received</label>
                    <span id="creatorTotalFunding">—</span>
                </div>
                <div class="stat-box">
                    <label>Funders</label>
                    <span id="creatorTotalFunders">—</span>
                </div>
                <div class="stat-box">
                    <label>Network Size</label>
                    <span id="creatorNetworkSize">—</span>
                </div>
                <div class="stat-box">
                    <label>Atomic Network</label>
                    <span id="creatorNetworkName" style="color: var(--accent-purple); font-weight: bold;">—</span>
                </div>
            </div>

            <!-- Analysis Buttons -->
            <div style="margin: 20px 0; text-align: center; display: flex; gap: 10px; justify-content: center; flex-wrap: wrap;">
                <button onclick="showFundingNetwork3Tier(document.getElementById('modalCreator').textContent.split(' ')[0])" style="background: rgba(239, 68, 68, 0.2); color: var(--color-critical); border: 1px solid rgba(239, 68, 68, 0.5); padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: bold;">View Funding Patterns</button>
                <button onclick="window.location.href = '/coordinated-funder-analysis/' + document.getElementById('modalCreator').textContent.split(' ')[0]" style="background: rgba(249, 115, 22, 0.2); color: var(--color-high); border: 1px solid rgba(249, 115, 22, 0.5); padding: 10px 20px; border-radius: 4px; cursor: pointer; font-weight: bold;">Coordinated Network</button>
            </div>

            <!-- Creator Tags -->
            <div id="creatorTagsContainer"></div>

            <!-- Tokens Launched -->
            <h3>Tokens Launched</h3>
            <div class="tokens-launched-container">
                <table class="tokens-launched-table">
                    <thead>
                        <tr>
                            <th>Token Mint</th>
                            <th>Created</th>
                            <th>Risk</th>
                            <th>Market Cap</th>
                            <th>CREATE Tx</th>
                        </tr>
                    </thead>
                    <tbody id="tokensLaunchedBody">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>

            <!-- Tokens Funded Section -->
            <div id="tokensFundedSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: var(--accent-cyan);">💰 Tokens Funded (As Funder)</h3>
                <div class="tokens-funded-container">
                    <table class="tokens-launched-table">
                        <thead>
                            <tr>
                                <th>Token Mint</th>
                                <th>Creator</th>
                                <th>Funding Amount (SOL)</th>
                                <th>Created</th>
                                <th>Risk</th>
                            </tr>
                        </thead>
                        <tbody id="tokensFundedBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Jito Tips History Section -->
            <div id="jitotipsSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: var(--accent-cyan);">💸 Jito Tips History</h3>
                <div class="jitotips-container">
                    <table class="jitotips-table">
                        <thead>
                            <tr>
                                <th>Token Mint</th>
                                <th>Tip Amount (SOL)</th>
                                <th>% of Cost</th>
                                <th>Type</th>
                                <th>Date</th>
                            </tr>
                        </thead>
                        <tbody id="jitotipsBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- CEX Funders Section -->
            <div id="cexFundersSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: var(--accent-cyan);">🏛️ CEX Funders</h3>
                <div class="cex-funders-container">
                    <table class="cex-funders-table">
                        <thead>
                            <tr>
                                <th>CEX Funder</th>
                                <th>Address</th>
                                <th>Amount (SOL)</th>
                            </tr>
                        </thead>
                        <tbody id="cexFundersBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Multi-Creator Funders Section (Coordination Risk) -->
            <div id="multiCreatorFundersSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: var(--color-critical);">⚠️ Multi-Creator Funders (Coordination Risk)</h3>
                <div class="multi-creator-container">
                    <div id="multiCreatorRiskBanner" style="background: rgba(239, 68, 68, 0.1); border-left: 4px solid var(--color-critical); padding: 15px; margin-bottom: 15px; border-radius: 4px;">
                        <p style="color: var(--color-critical); margin: 0; font-size: 13px;">
                            <strong>⚠️ Alert:</strong> This funder is also funding other token creators. This could indicate coordinated activity.
                        </p>
                    </div>
                    <table class="cex-funders-table" style="border-left: 4px solid var(--color-critical);">
                        <thead>
                            <tr>
                                <th>Funder Address</th>
                                <th>Creators Funded</th>
                                <th>Total SOL Sent</th>
                                <th>First Funding</th>
                                <th>Last Funding</th>
                            </tr>
                        </thead>
                        <tbody id="tokenMetricsMultiCreatorFundersBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Other Labeled Funders Section -->
            <div id="otherFundersSection" style="display: none; margin-bottom: 20px;">
                <h3 style="color: var(--accent-cyan);">🏛️ Other Labeled Funders</h3>
                <div class="cex-funders-container">
                    <table class="cex-funders-table">
                        <thead>
                            <tr>
                                <th>Funder Name</th>
                                <th>Category</th>
                                <th>Amount (SOL)</th>
                            </tr>
                        </thead>
                        <tbody id="otherFundersBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- All Funders Section -->
            <div id="allFundersSection" style="margin-bottom: 20px;">
                <h3 style="color: var(--accent-cyan);">💰 All Funders</h3>
                <div class="cex-funders-container">
                    <table class="cex-funders-table">
                        <thead>
                            <tr>
                                <th>Funder Address</th>
                                <th>Amount (SOL)</th>
                                <th>Type</th>
                            </tr>
                        </thead>
                        <tbody id="allFundersBody">
                            <!-- Populated by JavaScript -->
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Top Recipients (Outgoing Transfers) -->
            <h3 style="margin-top: 20px;">Recipients (SOL Sent Out)</h3>
            <div class="top-recipients-container">
                <table class="top-funders-table">
                    <thead>
                        <tr>
                            <th>Recipient Address</th>
                            <th>Amount (SOL)</th>
                            <th>Type</th>
                        </tr>
                    </thead>
                    <tbody id="topRecipientsBody">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>

            <!-- Cross-Creator Network -->
            <h3 style="margin-top: 20px;">Cross-Creator Network Connections</h3>
            <div id="crossReferencesContainer" style="background: rgba(0, 0, 0, 0.3); border: 1px solid rgba(124, 58, 237, 0.3); border-radius: 6px; padding: 15px;">
                <!-- Populated by JavaScript -->
            </div>

            <!-- Wallet Cluster -->
            <h3 style="margin-top: 20px;">Wallet Network</h3>
            <div class="cluster-info" id="clusterInfo" style="background: rgba(6, 182, 212, 0.05); border: 1px solid rgba(6, 182, 212, 0.2); border-radius: 6px; padding: 15px;">
                <!-- Populated by JavaScript -->
            </div>
        </div>
    </div>

    <!-- Multi-Creator Funders Modal -->
    <div id="multiCreatorFundersModal" class="modal">
        <div class="modal-content" style="max-width: 1000px; max-height: 90vh; overflow-y: auto;">
            <span class="close" onclick="closeMultiCreatorFunders()">&times;</span>
            <h2>🔗 Coordinated Funder Analysis</h2>

            <!-- Statistics Summary -->
            <div class="funder-stats-grid" id="funderStatsGrid">
                <div class="stat-box">
                    <label>Suspicious Multi-Creator</label>
                    <span id="suspiciousFundersCount">—</span>
                </div>
                <div class="stat-box">
                    <label>Safe (INFRA/CEX)</label>
                    <span id="safeFundersCount">—</span>
                </div>
                <div class="stat-box">
                    <label>Total Funders</label>
                    <span id="totalFundersCount">—</span>
                </div>
            </div>

            <!-- Suspicious Multi-Creator Funders Table -->
            <h3>⚠️ Suspicious Multi-Creator Funders</h3>
            <div class="funders-container">
                <table class="funders-table">
                    <thead>
                        <tr>
                            <th>Funder Address</th>
                            <th>Network</th>
                            <th>Creators Funded</th>
                            <th>Total SOL</th>
                            <th>Funding Records</th>
                            <th>Activity Period</th>
                        </tr>
                    </thead>
                    <tbody id="multiCreatorFundersBody">
                        <tr><td colspan="6" style="text-align: center; color: var(--text-secondary);">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Funder Details (Expandable) -->
            <div id="funderDetailsContainer" style="margin-top: 30px; display: none;">
                <h3>Funder Details</h3>
                <div id="funderDetailsList">
                    <!-- Populated by JavaScript -->
                </div>
            </div>

        </div>
    </div>

    <!-- Transaction Viewer Modal -->
    <div id="txViewerModal" class="modal">
        <div class="modal-content" style="max-width: 900px; max-height: 90vh; overflow-y: auto;">
            <span class="close" onclick="closeTxViewer()">&times;</span>
            <h2>Transaction Details - <span id="txViewerSig" style="font-family: monospace; font-size: 12px;"></span></h2>

            <div style="margin-bottom: 20px;">
                <a id="txSolscanLink" href="#" target="_blank" style="color: var(--accent-cyan); text-decoration: none; margin-right: 15px;">
                    🔗 View on Solscan
                </a>
                <button onclick="copyToClipboard(document.getElementById('txViewerSig').textContent)" style="background: rgba(6, 182, 212, 0.2); color: var(--accent-cyan); border: 1px solid var(--accent-cyan); padding: 5px 12px; border-radius: 4px; cursor: pointer; font-family: monospace; font-size: 12px;">
                    📋 Copy Signature
                </button>
            </div>

            <h3>Account Keys (jsonParsed)</h3>
            <div style="background: rgba(0, 0, 0, 0.5); border: 1px solid rgba(6, 182, 212, 0.2); border-radius: 6px; padding: 15px; overflow-x: auto;">
                <pre id="txViewerAccountKeys" style="color: var(--text-primary); font-size: 11px; margin: 0; white-space: pre-wrap; word-wrap: break-word;"></pre>
            </div>

            <h3 style="margin-top: 20px;">Fee Payer (Creator)</h3>
            <div style="background: rgba(34, 197, 94, 0.1); border: 2px solid rgba(34, 197, 94, 0.3); border-radius: 6px; padding: 15px; margin-bottom: 20px;">
                <div style="font-family: monospace; font-size: 12px; color: var(--color-low); word-break: break-all;">
                    <span id="txViewerFeePayer">—</span>
                </div>
                <div style="color: var(--text-secondary); font-size: 11px; margin-top: 8px;">
                    ✓ Fee payer (always first signer at accountKeys[0]) = transaction creator
                </div>
            </div>
        </div>
    </div>

    <!-- Transaction Validation Modal -->
    <div id="validationModal" class="modal">
        <div class="modal-content" style="max-width: 800px;">
            <span class="close" onclick="closeValidationModal()">&times;</span>
            <h2>🔍 Transaction Validation</h2>

            <div style="margin-bottom: 20px;">
                <label style="display: block; color: var(--text-secondary); font-size: 12px; margin-bottom: 8px; text-transform: uppercase;">Transaction Signature</label>
                <input
                    type="text"
                    id="validationInput"
                    placeholder="Paste transaction signature (e.g., 2NcBKN1RV35onHE1fP7wmjfb8PWrmhBgvsvemPaoVt2DkcV5...)"
                    style="width: 100%; padding: 12px; background: rgba(0, 0, 0, 0.5); border: 1px solid rgba(6, 182, 212, 0.3); border-radius: 6px; color: var(--text-primary); font-family: monospace; font-size: 12px; box-sizing: border-box;"
                >
            </div>

            <div style="display: flex; gap: 10px; margin-bottom: 20px;">
                <button
                    onclick="validateTransaction()"
                    style="flex: 1; padding: 12px; background: rgba(59, 130, 246, 0.2); color: var(--color-none); border: 1px solid rgba(59, 130, 246, 0.5); border-radius: 6px; cursor: pointer; font-weight: bold; transition: all 0.2s;"
                    onmouseover="this.style.background='rgba(59, 130, 246, 0.4)'"
                    onmouseout="this.style.background='rgba(59, 130, 246, 0.2)'"
                >
                    ✅ Validate
                </button>
                <button
                    onclick="closeValidationModal()"
                    style="flex: 1; padding: 12px; background: rgba(100, 100, 100, 0.2); color: var(--text-secondary); border: 1px solid rgba(100, 100, 100, 0.5); border-radius: 6px; cursor: pointer;"
                >
                    Cancel
                </button>
            </div>

            <div id="validationResults" style="display: none;">
                <div style="background: rgba(6, 182, 212, 0.05); border: 1px solid rgba(6, 182, 212, 0.2); border-radius: 6px; padding: 20px;">

                    <!-- Loading State -->
                    <div id="validationLoading" style="text-align: center; color: var(--accent-cyan);">
                        <div style="font-size: 24px; margin-bottom: 10px;">⏳</div>
                        <div>Validating transaction...</div>
                    </div>

                    <!-- Results State -->
                    <div id="validationSuccess" style="display: none;">
                        <div style="color: var(--color-low); font-weight: bold; margin-bottom: 15px;">✅ PUMP.FUN CREATE TRANSACTION CONFIRMED</div>

                        <div style="margin-bottom: 15px;">
                            <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Token Mint</div>
                            <div style="color: var(--address-color); font-family: monospace; font-size: 12px; word-break: break-all;" id="resultMint">—</div>
                        </div>

                        <div style="margin-bottom: 15px;">
                            <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Creator (Fee Payer)</div>
                            <div style="color: var(--color-low); font-family: monospace; font-size: 12px; word-break: break-all;" id="resultCreator">—</div>
                        </div>

                        <div style="margin-bottom: 15px;">
                            <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Timestamp</div>
                            <div style="color: var(--text-primary); font-size: 12px;" id="resultTimestamp">—</div>
                        </div>

                        <div style="background: rgba(0, 0, 0, 0.3); padding: 12px; border-radius: 4px; border-left: 3px solid var(--accent-cyan);">
                            <div style="color: var(--text-secondary); font-size: 10px; text-transform: uppercase; margin-bottom: 8px;">Evidence</div>
                            <div id="resultEvidence" style="color: var(--text-primary); font-size: 11px; line-height: 1.6;">—</div>
                        </div>

                        <div style="margin-top: 15px;">
                            <a id="resultSolscanLink" href="#" target="_blank" style="color: var(--color-none); text-decoration: none; font-size: 12px;">
                                🔗 View on Solscan →
                            </a>
                        </div>
                    </div>

                    <!-- Error State -->
                    <div id="validationError" style="display: none; color: var(--color-critical);">
                        <div style="font-weight: bold; margin-bottom: 10px;">❌ Validation Failed</div>
                        <div id="errorMessage" style="font-size: 12px; color: var(--color-critical);">—</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- 3-Tier Funding Network Modal -->
    <div id="fundingNetwork3TierModal" class="modal">
        <div class="modal-content" style="max-width: 800px;">
            <span class="close" onclick="closeFundingNetwork3Tier()">&times;</span>
            <h2>Funding Patterns</h2>

            <!-- 3-Tier Network Visualization -->
            <div style="background: var(--bg-secondary); border-radius: 8px; padding: 15px;">
                <div id="fn3tNetworkBody" style="font-family: monospace; font-size: 12px; line-height: 2; color: var(--text-primary); max-height: 500px; overflow-y: auto;">
                    <div style="color: var(--text-secondary);">Loading network...</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Coordinated Funder Analysis Modal -->
    <div id="coordinatedFunderAnalysisModal" class="modal">
        <div class="modal-content" style="max-width: 900px;">
            <span class="close" onclick="closeCoordinatedFunderAnalysis()">&times;</span>
            <h2>Coordinated Funder Analysis</h2>

            <!-- Network Risk Summary -->
            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 15px; margin-bottom: 20px;">
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid var(--color-critical);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 5px;">NETWORK RISK</div>
                    <div id="cfaRiskLevel" style="color: var(--color-critical); font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid var(--color-medium);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 5px;">CONNECTED CREATORS</div>
                    <div id="cfaConnectedCount" style="color: var(--color-medium); font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid var(--color-high);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 5px;">SHARED DESTINATIONS</div>
                    <div id="cfaSharedDests" style="color: var(--color-high); font-size: 18px; font-weight: bold;">—</div>
                </div>
            </div>

            <!-- Connected Creators List -->
            <h3 style="color: var(--text-primary); margin-top: 20px;">Connected Creators</h3>
            <div style="background: var(--bg-secondary); border-radius: 8px; padding: 15px; max-height: 300px; overflow-y: auto;">
                <div id="cfaConnectedCreators" style="font-family: monospace; font-size: 12px; line-height: 1.8; color: var(--text-primary);">
                    <div style="color: var(--text-secondary);">Loading...</div>
                </div>
            </div>

            <!-- Shared Destinations List -->
            <h3 style="color: var(--text-primary); margin-top: 20px;">Shared Destinations</h3>
            <div style="background: var(--bg-secondary); border-radius: 8px; padding: 15px; max-height: 300px; overflow-y: auto;">
                <div id="cfaSharedDestinations" style="font-family: monospace; font-size: 12px; line-height: 1.8; color: var(--text-primary);">
                    <div style="color: var(--text-secondary);">Loading...</div>
                </div>
            </div>

            <!-- Analysis Timestamp -->
            <div style="color: var(--text-secondary); font-size: 10px; margin-top: 15px;">
                <div>Analyzed: <span id="cfaDetectedAt">—</span></div>
            </div>
        </div>
    </div>

    <!-- Funder Transfer Details Modal -->
    <div id="funderDetailsModal" class="modal">
        <div class="modal-content" style="max-width: 1000px;">
            <span class="close" onclick="closeFunderDetails()">&times;</span>
            <h2>Funder: <span id="fdFunderAddr" style="font-family: monospace; font-size: 14px;">—</span></h2>

            <!-- Summary Stats -->
            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 20px;">
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid var(--color-medium);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 5px;">CREATORS FUNDED</div>
                    <div id="fdCreatorCount" style="color: var(--color-medium); font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid #4ade80;">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 5px;">INCOMING SOL</div>
                    <div id="fdIncomingTotal" style="color: var(--color-low); font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid var(--color-critical);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 5px;">OUTGOING SOL</div>
                    <div id="fdOutgoingTotal" style="color: var(--color-critical); font-size: 18px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; text-align: center; border-left: 3px solid var(--color-high);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 5px;">NET FLOW</div>
                    <div id="fdNetFlow" style="color: var(--color-high); font-size: 18px; font-weight: bold;">—</div>
                </div>
            </div>

            <!-- Incoming Transfers -->
            <h3 style="color: var(--text-primary); margin-top: 20px;">Incoming Transfers (Senders)</h3>
            <div style="background: var(--bg-secondary); border-radius: 8px; padding: 0; max-height: 350px; overflow-y: auto;">
                <table style="width: 100%; font-size: 12px; border-collapse: collapse;">
                    <thead style="position: sticky; top: 0; background: var(--bg-secondary);">
                        <tr style="border-bottom: 1px solid rgba(6, 182, 212, 0.2);">
                            <th style="padding: 10px; text-align: left; color: var(--text-secondary);">Sender Address</th>
                            <th style="padding: 10px; text-align: right; color: var(--text-secondary);">SOL</th>
                            <th style="padding: 10px; text-align: center; color: var(--text-secondary);">Txs</th>
                            <th style="padding: 10px; text-align: left; color: var(--text-secondary);">Classification</th>
                        </tr>
                    </thead>
                    <tbody id="fdIncomingBody">
                        <tr><td colspan="4" style="padding: 20px; text-align: center; color: var(--text-secondary);">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Outgoing Transfers -->
            <h3 style="color: var(--text-primary); margin-top: 20px;">Outgoing Transfers (Recipients)</h3>
            <div style="background: var(--bg-secondary); border-radius: 8px; padding: 0; max-height: 350px; overflow-y: auto;">
                <table style="width: 100%; font-size: 12px; border-collapse: collapse;">
                    <thead style="position: sticky; top: 0; background: var(--bg-secondary);">
                        <tr style="border-bottom: 1px solid rgba(6, 182, 212, 0.2);">
                            <th style="padding: 10px; text-align: left; color: var(--text-secondary);">Recipient Address</th>
                            <th style="padding: 10px; text-align: right; color: var(--text-secondary);">SOL</th>
                            <th style="padding: 10px; text-align: center; color: var(--text-secondary);">Txs</th>
                            <th style="padding: 10px; text-align: left; color: var(--text-secondary);">Classification</th>
                        </tr>
                    </thead>
                    <tbody id="fdOutgoingBody">
                        <tr><td colspan="4" style="padding: 20px; text-align: center; color: var(--text-secondary);">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Super-Cluster Details Modal -->
    <div id="superClusterModal" class="modal">
        <div class="modal-content" style="max-width: 1000px;">
            <span class="close" onclick="closeSuperCluster()">&times;</span>
            <h2>Super-Cluster Details - <span id="scModalId" style="font-size: 16px; color: var(--accent-cyan);"></span></h2>

            <!-- Risk Badge & Toggle -->
            <div style="display: flex; align-items: center; gap: 15px; margin-bottom: 20px;">
                <span id="scRiskBadge" style="padding: 8px 16px; border-radius: 6px; font-weight: bold; font-size: 14px;">—</span>
                <button id="scToggleCexInfra" onclick="toggleCexInfraView()" style="padding: 8px 14px; border-radius: 6px; border: 1px solid rgba(124, 58, 237, 0.5); background: rgba(124, 58, 237, 0.1); color: var(--primary); font-size: 12px; font-weight: bold; cursor: pointer; transition: all 0.3s;">
                    ✓ Show CEX/INFRA
                </button>
            </div>

            <!-- Cluster Stats -->
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 25px;">
                <div style="background: rgba(59, 130, 246, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid var(--color-none);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Networks</div>
                    <div id="scNetworkCount" style="color: var(--color-none); font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(245, 158, 11, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid var(--color-high);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Creators</div>
                    <div id="scCreatorCount" style="color: var(--color-high); font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(59, 130, 246, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid var(--color-none);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Tokens</div>
                    <div id="scTokenCount" style="color: var(--color-none); font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(74, 222, 128, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #4ade80;">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Funders</div>
                    <div id="scFunderCount" style="color: var(--color-low); font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(168, 85, 247, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid var(--accent-purple);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Total SOL</div>
                    <div id="scTotalSol" style="color: var(--accent-purple); font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(168, 85, 247, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid var(--accent-purple);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">CEX Funders</div>
                    <div id="scCexCount" style="color: var(--accent-purple); font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(34, 197, 94, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid #22c55e;">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Coordinated</div>
                    <div id="scCoordinatedCount" style="color: #22c55e; font-size: 20px; font-weight: bold;">—</div>
                </div>
                <div style="background: rgba(239, 68, 68, 0.1); padding: 15px; border-radius: 8px; border-left: 3px solid var(--color-critical);">
                    <div style="color: var(--text-secondary); font-size: 11px; margin-bottom: 8px; text-transform: uppercase;">Reuse Tag</div>
                    <div id="scReuseTag" style="color: var(--color-critical); font-size: 14px; font-weight: bold;">—</div>
                </div>
            </div>

            <!-- Root Operators & Relationship -->
            <div style="background: rgba(0, 0, 0, 0.3); padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <h4 style="color: var(--text-secondary); font-size: 12px; margin-bottom: 15px; text-transform: uppercase;">Root Operators & Cluster Relationship</h4>
                <div id="scRootAddresses" style="display: flex; flex-direction: column; gap: 12px;">
                    <!-- Populated by JS -->
                </div>
                <!-- Relationship Diagram -->
                <div id="scRelationshipDiagram" style="margin-top: 20px; padding-top: 20px; border-top: 1px solid rgba(124, 58, 237, 0.2);">
                    <!-- Populated by JS -->
                </div>
            </div>

            <!-- Tabs -->
            <div style="margin-bottom: 20px; border-bottom: 1px solid rgba(6, 182, 212, 0.2);">
                <button onclick="switchSuperClusterTab('networks')" class="sc-tab-button active" data-tab="networks">
                    Networks
                </button>
                <button onclick="switchSuperClusterTab('creators')" class="sc-tab-button" data-tab="creators">
                    Creators
                </button>
                <button onclick="switchSuperClusterTab('tokens')" class="sc-tab-button" data-tab="tokens">
                    Tokens
                </button>
            </div>

            <!-- Networks Tab -->
            <div id="scNetworksTab" class="sc-tab-content" style="display: none; margin-bottom: 20px;">
                <!-- Networks Tab Toggle -->
                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid rgba(6, 182, 212, 0.2);">
                    <span style="color: var(--text-secondary); font-size: 12px; text-transform: uppercase; font-weight: bold;">Filter Networks:</span>
                    <button id="scNetworksToggleCexInfra" onclick="toggleNetworksVisibility()" style="padding: 6px 12px; border-radius: 4px; border: 1px solid rgba(124, 58, 237, 0.5); background: rgba(124, 58, 237, 0.1); color: var(--primary); font-size: 11px; font-weight: bold; cursor: pointer; transition: all 0.3s;">
                        ✓ Show All
                    </button>
                </div>
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="border-bottom: 1px solid rgba(6, 182, 212, 0.2);">
                            <th style="text-align: left; padding: 10px; color: var(--text-secondary); font-size: 12px;">Network</th>
                            <th style="text-align: right; padding: 10px; color: var(--text-secondary); font-size: 12px;">Members</th>
                            <th style="text-align: right; padding: 10px; color: var(--text-secondary); font-size: 12px;">SOL</th>
                            <th style="text-align: center; padding: 10px; color: var(--text-secondary); font-size: 12px;">Status</th>
                        </tr>
                    </thead>
                    <tbody id="scNetworksList">
                        <tr><td colspan="4" style="padding: 20px; text-align: center; color: var(--text-secondary);">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Creators Tab -->
            <div id="scCreatorsTab" class="sc-tab-content" style="display: block; max-height: 400px; overflow-y: auto; margin-bottom: 20px;">
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="border-bottom: 1px solid rgba(6, 182, 212, 0.2);">
                            <th style="text-align: left; padding: 10px; color: var(--text-secondary); font-size: 12px;">Creator Address</th>
                            <th style="text-align: left; padding: 10px; color: var(--text-secondary); font-size: 12px;">Tokens</th>
                        </tr>
                    </thead>
                    <tbody id="scCreatorsList">
                        <tr><td colspan="2" style="padding: 20px; text-align: center; color: var(--text-secondary);">Loading...</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Tokens Tab -->
            <div id="scTokensTab" class="sc-tab-content" style="display: none; max-height: 400px; overflow-y: auto; margin-bottom: 20px;">
                <table style="width: 100%; border-collapse: collapse;">
                    <thead>
                        <tr style="border-bottom: 1px solid rgba(6, 182, 212, 0.2);">
                            <th style="text-align: left; padding: 10px; color: var(--text-secondary); font-size: 12px;">Token Mint</th>
                            <th style="text-align: left; padding: 10px; color: var(--text-secondary); font-size: 12px;">Creator</th>
                            <th style="text-align: right; padding: 10px; color: var(--text-secondary); font-size: 12px;">Risk</th>
                            <th style="text-align: right; padding: 10px; color: var(--text-secondary); font-size: 12px;">Peak MC</th>
                        </tr>
                    </thead>
                    <tbody id="scTokensList">
                        <tr><td colspan="4" style="padding: 20px; text-align: center; color: var(--text-secondary);">Loading...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <!-- Creator Pool Tag Definitions Modal -->
    <div id="tagDefinitionModal" class="modal" style="display: none;">
        <div class="modal-content" style="max-width: 600px;">
            <span class="close" onclick="document.getElementById('tagDefinitionModal').style.display='none'">&times;</span>
            <h2 id="tagDefTitle" style="margin-bottom: 20px;">Definition</h2>

            <div id="tagDefContent" style="color: var(--text-primary); line-height: 1.6;">
                <!-- Content populated by JS -->
            </div>

            <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid rgba(124, 58, 237, 0.2);">
                <h4 style="color: var(--text-secondary); margin-bottom: 10px;">How It Works:</h4>
                <ul style="margin-left: 20px; color: var(--text-secondary); font-size: 12px;">
                    <li>Measures how many creators appear in <strong>multiple clusters</strong></li>
                    <li>Denominator: Total unique creators in THIS cluster</li>
                    <li>Numerator: Creators that also appear in other clusters</li>
                    <li>Ratio: numerator ÷ denominator = reuse intensity</li>
                    <li>Uses <strong>minimum-support thresholds</strong> to avoid false positives</li>
                </ul>
            </div>
        </div>
    </div>

    <!-- Comprehensive Definitions Guide Modal -->
    <div id="definitionsGuideModal" class="modal" style="display: none;">
        <div class="modal-content" style="max-width: 900px; max-height: 80vh; overflow-y: auto;">
            <span class="close" onclick="document.getElementById('definitionsGuideModal').style.display='none'">&times;</span>
            <h2 style="margin-bottom: 30px;">Creator Pool Tag Definitions & Guide</h2>

            <div style="background: rgba(124, 58, 237, 0.05); padding: 20px; border-radius: 8px; border-left: 4px solid var(--primary); margin-bottom: 30px;">
                <h3 style="color: var(--primary); margin-top: 0;">What Are Creator Pools?</h3>
                <p style="color: var(--text-primary); line-height: 1.7;">
                    A <strong>creator pool</strong> is a set of wallet addresses that are reused across multiple funding networks and clusters.
                    Instead of using unique wallets for each token launch, coordinated operations systematically reuse the same creators,
                    creating linkages between supposedly independent clusters. These tags identify the strength of creator pool coordination.
                </p>
                <div style="background: rgba(234, 179, 8, 0.1); border: 1px solid rgba(255, 193, 7, 0.3); padding: 12px; border-radius: 4px; margin-top: 15px;">
                    <p style="color: var(--color-medium); margin: 0; font-size: 12px;">
                        <strong>⚠️ Important:</strong> Tags are based on <strong>wallet reuse patterns and structural signals</strong>.
                        They indicate <strong>coordination likelihood</strong>, not intent or ownership. Use as a risk indicator, not definitive proof.
                    </p>
                </div>
            </div>

            <!-- INDEPENDENT -->
            <div style="background: rgba(107, 207, 127, 0.1); padding: 20px; border-radius: 8px; border-left: 4px solid var(--color-low); margin-bottom: 20px;">
                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 15px;">
                    <span style="background: var(--color-low); color: var(--text-dark); padding: 6px 12px; border-radius: 4px; font-weight: bold; font-size: 12px;">INDEPENDENT</span>
                    <span style="color: var(--text-secondary); font-size: 12px;">(Green)</span>
                </div>
                <div style="color: var(--text-primary);">
                    <p><strong>No creators reused across clusters</strong><br>
                    This cluster's creators appear only in this cluster and nowhere else. Each creator wallet is independent and not shared with other coordinated operations.</p>
                    <p style="margin-bottom: 10px;"><strong>Key Indicators:</strong></p>
                    <ul style="margin: 0 0 10px 20px; color: var(--text-secondary); font-size: 13px;">
                        <li>creators_in_multiple_clusters = 0</li>
                        <li>All creators unique to this cluster</li>
                        <li>No connection to other clusters via shared creators</li>
                    </ul>
                    <p style="color: var(--text-secondary); font-size: 12px;">🟢 <strong>Risk:</strong> Low - Isolated operation</p>
                </div>
            </div>

            <!-- WEAK -->
            <div style="background: rgba(234, 179, 8, 0.1); padding: 20px; border-radius: 8px; border-left: 4px solid var(--color-medium); margin-bottom: 20px;">
                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 15px;">
                    <span style="background: var(--color-medium); color: var(--text-dark); padding: 6px 12px; border-radius: 4px; font-weight: bold; font-size: 12px;">CREATOR POOL - WEAK</span>
                    <span style="color: var(--text-secondary); font-size: 12px;">(Yellow)</span>
                </div>
                <div style="color: var(--text-primary);">
                    <p><strong>Minimal creator reuse</strong><br>
                    Some creators appear in multiple clusters, but the coordination signal is weak. Either few creators are reused, or the reuse ratio is below our minimum-support threshold.</p>
                    <p style="margin-bottom: 10px;"><strong>Minimum Support Requirements:</strong></p>
                    <ul style="margin: 0 0 10px 20px; color: var(--text-secondary); font-size: 13px;">
                        <li>creators_in_multiple_clusters ≥ 1</li>
                        <li>Below thresholds for SHARED or STRONG</li>
                        <li>Marginal coordination signal detected</li>
                    </ul>
                    <p style="color: var(--text-secondary); font-size: 12px;">🟡 <strong>Risk:</strong> Medium - Monitor for escalation</p>
                </div>
            </div>

            <!-- SHARED -->
            <div style="background: rgba(249, 115, 22, 0.1); padding: 20px; border-radius: 8px; border-left: 4px solid var(--color-medium); margin-bottom: 20px;">
                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 15px;">
                    <span style="background: var(--color-medium); color: var(--text-dark); padding: 6px 12px; border-radius: 4px; font-weight: bold; font-size: 12px;">CREATOR POOL - SHARED</span>
                    <span style="color: var(--text-secondary); font-size: 12px;">(Orange)</span>
                </div>
                <div style="color: var(--text-primary);">
                    <p><strong>Solid creator pool coordination</strong><br>
                    Multiple clusters share a pool of creators with consistent coordination. Clear pattern of reusing the same launcher wallets across different funding networks.</p>
                    <p style="margin-bottom: 10px;"><strong>Minimum Support Requirements:</strong></p>
                    <ul style="margin: 0 0 10px 20px; color: var(--text-secondary); font-size: 13px;">
                        <li>✓ Min <strong>5+ unique creators</strong> in cluster</li>
                        <li>✓ Min <strong>2+ creators</strong> reused in other clusters</li>
                        <li>✓ Min <strong>30%+ reuse ratio</strong> (2 ÷ 5)</li>
                    </ul>
                    <p style="color: var(--text-secondary); font-size: 12px;">🟠 <strong>Risk:</strong> High - Clear coordinated operations</p>
                </div>
            </div>

            <!-- STRONG -->
            <div style="background: rgba(239, 68, 68, 0.1); padding: 20px; border-radius: 8px; border-left: 4px solid var(--color-critical); margin-bottom: 20px;">
                <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 15px;">
                    <span style="background: var(--color-critical); color: var(--text-light); padding: 6px 12px; border-radius: 4px; font-weight: bold; font-size: 12px;">CREATOR POOL - STRONG</span>
                    <span style="color: var(--text-secondary); font-size: 12px;">(Red)</span>
                </div>
                <div style="color: var(--text-primary);">
                    <p><strong>Highly coordinated creator ecosystem</strong><br>
                    Strong evidence of an organized operation reusing creators systematically. Industrial-scale creator pool management with high concentration of reuse.</p>
                    <p style="margin-bottom: 10px;"><strong>Minimum Support Requirements:</strong></p>
                    <ul style="margin: 0 0 10px 20px; color: var(--text-secondary); font-size: 13px;">
                        <li>✓ Min <strong>10+ unique creators</strong> in cluster</li>
                        <li>✓ Min <strong>5+ creators</strong> reused in other clusters</li>
                        <li>✓ Min <strong>50%+ reuse ratio</strong> (5 ÷ 10)</li>
                    </ul>
                    <p style="color: var(--text-secondary); font-size: 12px;">🚨 <strong>Risk:</strong> CRITICAL - Industrialized operations</p>
                </div>
            </div>

            <!-- Key Metrics Explained -->
            <div style="background: var(--bg-secondary); padding: 20px; border-radius: 8px; margin-top: 30px;">
                <h3 style="color: var(--text-secondary); margin-top: 0;">Key Metrics Explained</h3>
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                    <div>
                        <p style="margin-bottom: 5px;"><strong style="color: var(--primary);">creators_unique</strong></p>
                        <p style="color: var(--text-secondary); font-size: 12px; margin: 0;">The actual count of distinct creator wallets in THIS cluster (denominator for reuse ratio)</p>
                    </div>
                    <div>
                        <p style="margin-bottom: 5px;"><strong style="color: var(--primary);">creators_in_multiple_clusters</strong></p>
                        <p style="color: var(--text-secondary); font-size: 12px; margin: 0;">How many of those creators also appear in OTHER clusters (numerator for reuse ratio)</p>
                    </div>
                    <div>
                        <p style="margin-bottom: 5px;"><strong style="color: var(--primary);">reuse_ratio</strong></p>
                        <p style="color: var(--text-secondary); font-size: 12px; margin: 0;">creators_in_multiple_clusters ÷ creators_unique = coordination intensity</p>
                    </div>
                    <div>
                        <p style="margin-bottom: 5px;"><strong style="color: var(--primary);">max_clusters_per_creator</strong></p>
                        <p style="color: var(--text-secondary); font-size: 12px; margin: 0;">The creator with highest reuse appears in this many clusters</p>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        async function loadTokens() {
            try {
                const response = await fetch('/api/migrated-tokens');
                const data = await response.json();
                console.log('Loaded tokens:', data.tokens?.length || 0, 'tokens');
                console.log('Token data keys:', Object.keys(data));
                console.log('First token:', data.tokens?.[0]);

                if (!data.tokens || data.tokens.length === 0) {
                    console.error('No tokens in response!');
                    document.getElementById('tokens-container').innerHTML =
                        '<div class="no-data">No migrations recorded yet. Monitoring Pump.Fun...</div>';
                    return;
                }

                // Extract unique creator addresses
                const creators = [...new Set(data.tokens.map(t => t.creator).filter(c => c))];

                // Fetch all creator data in one batch call
                let creatorData = {};
                if (creators.length > 0) {
                    try {
                        const creatorResp = await fetch('/api/creators-batch', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({"creators": creators})
                        });
                        creatorData = await creatorResp.json();
                    } catch (e) {
                        console.error('Error loading creator data:', e);
                    }
                }

                // Load infrastructure mapping (separate infrastructure and CEX)
                window.infraMapping = { infrastructure: {}, cex: {} };
                try {
                    const infraResp = await fetch('/api/infrastructure-mapping');
                    if (infraResp.ok) {
                        window.infraMapping = await infraResp.json();
                    }
                } catch (e) {
                    console.log('Infrastructure mapping not available');
                }

                // Enrich tokens with creator data
                const enrichedTokens = data.tokens.map(token => ({
                    ...token,
                    creatorData: creatorData[token.creator] || {}
                }));

                // Filter out tokens with market cap < $2000 entirely
                // But allow very new tokens (created in last 60 minutes) to display even if low market cap
                const minMarketCap = 2000;
                const now = Math.floor(Date.now() / 1000);
                const filteredTokens = enrichedTokens.filter(t => {
                    const tokenAgeSeconds = now - new Date(t.created_at).getTime() / 1000;
                    const isNewToken = tokenAgeSeconds < 3600; // Less than 60 minutes old
                    // Display if: market cap >= $2k OR unknown market cap OR very new token
                    return t.market_cap_current >= minMarketCap || !t.market_cap_current || isNewToken;
                });

                // Display only top 25 of the filtered tokens
                const displayTokens = filteredTokens.slice(0, 25);

                // Check if token list changed (by comparing mints) to avoid unnecessary DOM rebuilds
                const currentMints = (window.currentTokens || []).map(t => t.mint).join(',');
                const newMints = displayTokens.map(t => t.mint).join(',');
                const tokensChanged = currentMints !== newMints;

                window.currentTokens = displayTokens;

                // Register all displayed tokens for price tracking
                const mints = displayTokens.map(t => t.mint).filter(m => m);

                if (mints.length > 0) {
                    fetch('/api/price/batch/register', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({mints: mints})
                    }).then(r => r.json()).then(res => {
                        console.log(`Registered ${res.registered}/${res.total} tokens with market cap >= $${minMarketCap}`);
                    }).catch(e => {
                        console.error('Error registering tokens for price tracking:', e);
                    });
                }

                // Update stats
                updateStats({tokens: displayTokens});

                // Only rebuild table if token list changed
                if (tokensChanged) {
                    buildTable(displayTokens);
                    console.log('Rebuilt table - token list changed');
                }

                // Fetch and display price sources for all tokens (icon only)
                
        }

        function updateStats(data) {
            const tokens = data.tokens;
            const highRisk = tokens.filter(t => t.risk_level?.includes('HIGH')).length;
            const mediumRisk = tokens.filter(t => t.risk_level?.includes('MEDIUM')).length;
            const lowRisk = tokens.filter(t => t.risk_level?.includes('LOW')).length;

            // Calculate unique creators
            const uniqueCreators = new Set(tokens.map(t => t.creator).filter(c => c)).size;

            // Calculate repeat launchers
            const creatorTokenCounts = {};
            tokens.forEach(token => {
                if (token.creator) {
                    creatorTokenCounts[token.creator] = (creatorTokenCounts[token.creator] || 0) + 1;
                }
            });
            const repeatLaunchers = Object.values(creatorTokenCounts).filter(count => count > 1).length;

            document.getElementById('total-migrations').textContent = tokens.length;
            document.getElementById('with-analysis').textContent = tokens.length;
            document.getElementById('high-risk').textContent = highRisk;
            document.getElementById('low-risk').textContent = lowRisk;
            document.getElementById('unique-creators').textContent = uniqueCreators;
            document.getElementById('repeat-launchers').textContent = repeatLaunchers;
        }

        let sortConfig = {
            column: 'created_at',
            direction: 'desc'
        };

        function buildTable(tokens) {
            // Sort tokens based on current sort config
            const sortedTokens = [...tokens].sort((a, b) => {
                const aVal = a[sortConfig.column];
                const bVal = b[sortConfig.column];

                // Handle null/undefined values
                if (aVal == null && bVal == null) return 0;
                if (aVal == null) return sortConfig.direction === 'asc' ? 1 : -1;
                if (bVal == null) return sortConfig.direction === 'asc' ? -1 : 1;

                // Numeric comparison
                if (typeof aVal === 'number' && typeof bVal === 'number') {
                    return sortConfig.direction === 'asc' ? aVal - bVal : bVal - aVal;
                }

                // Timestamp comparison for analyzed_at and created_at
                if (sortConfig.column === 'analyzed_at' || sortConfig.column === 'created_at') {
                    const aTime = new Date(String(aVal)).getTime();
                    const bTime = new Date(String(bVal)).getTime();
                    return sortConfig.direction === 'asc' ? aTime - bTime : bTime - aTime;
                }

                // String comparison
                return sortConfig.direction === 'asc'
                    ? String(aVal).localeCompare(String(bVal))
                    : String(bVal).localeCompare(String(aVal));
            });

            const html = `
                <table class="tokens-table">
                    <thead>
                        <tr>
                            <th onclick="sortBy('mint')" class="sortable ${sortConfig.column === 'mint' ? 'sorted-' + sortConfig.direction : ''}">Token Mint</th>
                            <th style="min-width: 80px;">Symbol</th>
                            <th></th>
                            <th onclick="sortBy('network_name')" class="sortable ${sortConfig.column === 'network_name' ? 'sorted-' + sortConfig.direction : ''}">Network</th>
                            <th onclick="sortBy('cluster_name')" class="sortable ${sortConfig.column === 'cluster_name' ? 'sorted-' + sortConfig.direction : ''}">Cluster</th>
                            <th>Live Price</th>
                            <th onclick="sortBy('market_cap_current')" class="sortable ${sortConfig.column === 'market_cap_current' ? 'sorted-' + sortConfig.direction : ''}">Market Cap</th>
                            <th onclick="sortBy('market_cap_highest')" class="sortable ${sortConfig.column === 'market_cap_highest' ? 'sorted-' + sortConfig.direction : ''}">Peak MC</th>
                            <th onclick="sortBy('market_cap_highest_at')" class="sortable ${sortConfig.column === 'market_cap_highest_at' ? 'sorted-' + sortConfig.direction : ''}">Peak Timing</th>
                            <th onclick="sortBy('total_events')" class="sortable ${sortConfig.column === 'total_events' ? 'sorted-' + sortConfig.direction : ''}">Events</th>
                            <th onclick="sortBy('coverage')" class="sortable ${sortConfig.column === 'coverage' ? 'sorted-' + sortConfig.direction : ''}">Coverage</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${sortedTokens.map(token => {
                            const creatorData = token.creatorData || {};
                            const tags = [];

                            // Tags for the "Creator Tags" column (non-infrastructure)
                            const columnTags = [];

                            // Network size tag (show if > 10 wallets)
                            if (creatorData.network_size > 10) {
                                const hop0 = creatorData.cluster_hops?.hop0 || 0;
                                const hop1 = creatorData.cluster_hops?.hop1 || 0;
                                columnTags.push(`<span class="creator-tag tag-network" title="Wallet cluster: ${hop0} hop-0, ${hop1} hop-1">${creatorData.network_size} wallets</span>`);
                            }

                            // Funding tag (show if > 10 SOL)
                            if (creatorData.inbound_sol > 10) {
                                const sources = creatorData.inbound_sources || 0;
                                columnTags.push(`<span class="creator-tag tag-funding" title="Pre-launch funding">${creatorData.inbound_sol.toFixed(1)} SOL from ${sources} source${sources > 1 ? 's' : ''}</span>`);
                            }

                            // Repeat launcher tag (show if > 1 token)
                            if (creatorData.token_count > 1) {
                                columnTags.push(`<span class="creator-tag tag-repeat" title="Repeat launcher">Multi-token (${creatorData.token_count})</span>`);
                            }

                            // Blocked tag
                            if (creatorData.is_blocked || token.creator_is_blocked) {
                                columnTags.push('<span class="creator-tag tag-blocked" title="On blocklist">BLOCKED</span>');
                            }

                            // CEX/Infrastructure funders - add to Creator Tags column
                            let funderLabels = [];
                            if (creatorData.funders && creatorData.funders.length > 0) {
                                for (let funder of creatorData.funders) {
                                    // Check if this funder is marked as CEX and has an exchange name
                                    if (funder.is_cex && (funder.display_name || funder.cex_exchange)) {
                                        // Use enriched display_name if available, otherwise fallback to cex_exchange
                                        const displayName = funder.display_name || funder.cex_exchange;
                                        // Only add if not already in our list
                                        const already = funderLabels.some(f => f.name === displayName);
                                        if (!already) {
                                            funderLabels.push({
                                                name: displayName,
                                                category: 'cex',
                                                description: `Funded by ${displayName}`
                                            });
                                        }
                                        // Only show first 2 CEX funders to avoid clutter
                                        if (funderLabels.length >= 2) break;
                                    }
                                }
                            }

                            // Add funder labels to columnTags (all cyan)
                            for (let label of funderLabels) {
                                columnTags.push(`<span class="creator-tag tag-funding" title="${label.description}">${label.name}</span>`);
                            }

                            // Service tags (uses_axiom, uses_jitotip, uses_meteora, uses_debridge, Multi-Funder, etc.)
                            // Use creatorData.tags if available (from batch API), otherwise fall back to token.creator_infra_tags
                            const serviceTags = (creatorData.tags && creatorData.tags.length > 0) ? creatorData.tags : (token.creator_infra_tags || []);
                            if (serviceTags && serviceTags.length > 0) {
                                // Deduplicate and filter out address-like tags
                                const seenServiceTags = new Set();
                                for (let serviceTag of serviceTags) {
                                    const tagName = serviceTag.tag.toLowerCase();
                                    // Skip if already seen and skip address-like tags
                                    if (!seenServiceTags.has(tagName) && !serviceTag.tag.match(/^[1-9A-HJ-NP-Za-km-z]{30,}\.?$/)) {
                                        seenServiceTags.add(tagName);

                                        // Custom display names for service tags
                                        let displayName = serviceTag.tag.replace('uses_', '');
                                        if (serviceTag.tag === 'uses_jitotip') {
                                            displayName = 'JitoTip (CREATE)';
                                        } else if (serviceTag.tag === 'uses_jitotip_other') {
                                            displayName = 'JitoTip';
                                        }
                                        // Keep Multi-Funder as-is

                                        columnTags.push(`<span class="creator-tag tag-funding" title="${serviceTag.description}">${displayName}</span>`);
                                    }
                                }
                            }

                            const creatorShort = token.creator ? token.creator.substring(0, 8) + '...' : 'N/A';
                            const creatorTitle = token.creator || 'Unknown';

                            // Get infrastructure tags for creator or funders
                            let infraTags = '';
                            let displayName = null;
                            let displayCategory = null;
                            let displayDescription = '';
                            let creatorIsLabeled = false;

                            // Check if creator itself is infrastructure or CEX - show account name only
                            if (token.creator && window.infraMapping) {
                                if (window.infraMapping.infrastructure && window.infraMapping.infrastructure[token.creator]) {
                                    const info = window.infraMapping.infrastructure[token.creator];
                                    displayName = info.name;
                                    displayCategory = info.category;
                                    displayDescription = info.description;
                                    creatorIsLabeled = true;
                                }
                                if (!displayName && window.infraMapping.cex && window.infraMapping.cex[token.creator]) {
                                    const info = window.infraMapping.cex[token.creator];
                                    displayName = info.name;
                                    displayCategory = info.category;
                                    displayDescription = info.description;
                                    creatorIsLabeled = true;
                                }
                            }

                            // infraTags is now only for showing creator as labeled (CEX/Infrastructure address)
                            // Funder labels are displayed in Creator Tags column instead

                            // Only show infraTags if creator itself is labeled as CEX/Infrastructure
                            if (displayName && displayCategory && !displayName.includes('1111111111') && displayName.length < 50 && !displayName.match(/^[1-9A-HJ-NP-Z]{32,}$/)) {
                                infraTags = `<div class="creator-infra-tags">
                                    <span class="infra-tag infra-${displayCategory}" title="${displayDescription}">${displayName}</span>
                                </div>`;
                            }

                            // Create creator element - ALWAYS show creator address (either label or address)
                            // The creator address is essential for accessing the creator modal
                            let creatorElement;
                            if (!token.creator) {
                                creatorElement = `<span class="mint-link" style="opacity:0.4">N/A</span>`;
                            } else if (creatorIsLabeled && displayName && !displayName.match(/^[1-9A-HJ-NP-Z]{32,}$/)) {
                                // Creator itself is labeled (CEX/Infrastructure) - show label name with clickable link to details
                                creatorElement = `<a href="#" onclick="showCreatorDetails('${token.creator}'); return false;" class="mint-link creator-address-link" title="Creator: ${creatorTitle}">${displayName}</a>`;
                            } else {
                                // No direct label on creator - always show creator address so user can click to modal
                                creatorElement = `<a href="#" onclick="showCreatorDetails('${token.creator}'); return false;" class="mint-link creator-address-link" title="Creator: ${creatorTitle}">${creatorShort}</a>`;
                            }

                            // Build creator infrastructure tags (deBridge, Meteora, Axiom) with deduplication
                            // NOTE: Service tags are now shown in the "Creator Tags" column, so skip them here to avoid duplication
                            let infraTagsHTML = '';
                            if (token.creator_infra_tags && token.creator_infra_tags.length > 0) {
                                // Skip displaying if the infrastructure service is already shown as a funder badge
                                // (e.g., don't show "uses_debridge" tag if deBridge is already displayed as a funder)
                                const skipIfFunder = displayName && displayName.toLowerCase().includes('debridge');

                                // Deduplicate tags (in case of duplicates in the array)
                                const seenTags = new Set();
                                const uniqueInfraTags = [];

                                for (let infraTag of token.creator_infra_tags) {
                                    const tagName = infraTag.tag.toLowerCase();
                                    if (!seenTags.has(tagName)) {
                                        seenTags.add(tagName);
                                        uniqueInfraTags.push(infraTag);
                                    }
                                }

                                for (let infraTag of uniqueInfraTags) {
                                    // Skip deBridge tag if deBridge is already shown as a funder
                                    if (infraTag.tag.includes('debridge') && skipIfFunder) continue;

                                    // SKIP if this looks like a Solana address (base58 characters, 30+ chars, may have period at end)
                                    // Solana base58 alphabet: [1-9A-HJ-NP-Za-km-z] (excludes 0, O, I, l)
                                    // Examples: "BmxK7bhPsfq4p4FTrXkUSKG26JeW2bT32DqqmkHD8rtJ" or "BmxK7bhPsfq4p4FTrXkUSKG26JeW2bT32DqqmkHD8rtJ."
                                    if (infraTag.tag.match(/^[1-9A-HJ-NP-Za-km-z]{30,}\.?$/)) {
                                        continue;  // Skip addresses, only show service names
                                    }

                                    // SKIP service tags (uses_axiom, uses_jitotip, uses_meteora, uses_debridge)
                                    // and coordination tags (Multi-Funder)
                                    // They are displayed in the "Creator Tags" column to avoid duplication
                                    if (infraTag.tag.match(/^uses_/) || infraTag.tag === 'Multi-Funder') {
                                        continue;
                                    }

                                    let tagColor, bgColor;
                                    if (infraTag.tag.includes('debridge')) {
                                        tagColor = '#ff9500';
                                        bgColor = 'rgba(255, 149, 0, 0.15)';
                                    } else if (infraTag.tag.includes('meteora')) {
                                        tagColor = 'var(--accent-cyan)';
                                        bgColor = 'rgba(6, 182, 212, 0.15)';
                                    } else if (infraTag.tag.includes('axiom')) {
                                        tagColor = '#9333ea';
                                        bgColor = 'rgba(147, 51, 234, 0.15)';
                                    } else if (infraTag.tag.includes('jito')) {
                                        tagColor = 'var(--color-medium)';
                                        bgColor = 'rgba(251, 191, 36, 0.15)';
                                    } else {
                                        tagColor = '#4ade80';
                                        bgColor = 'rgba(74, 222, 128, 0.15)';
                                    }
                                    infraTagsHTML += `<span class="creator-tag" style="border-color: ${tagColor}; color: ${tagColor}; background-color: ${bgColor}; display: inline-block; margin-right: 5px;" title="${infraTag.description}">${infraTag.tag.replace('uses_', '')}</span>`;
                                }
                            }

                            // Append creator infrastructure tags to infraTags
                            if (infraTagsHTML) {
                                infraTags += `<div style="display: flex; flex-wrap: wrap; gap: 5px; margin-top: 3px;">${infraTagsHTML}</div>`;
                            }

                            return `
                                <tr>
                                    <td class="mint-with-creator">
                                        <a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="Click for metrics">${token.mint}</a>
                                        <div class="creator-address-embedded">
                                            ${creatorElement}
                                            ${infraTags}
                                        </div>
                                    </td>
                                    <td id="symbol-${token.mint}" style="color: var(--accent-purple); font-weight: bold; font-size: 12px;">
                                        <span style="opacity: 0.5;">...</span>
                                    </td>
                                    <td class="creator-tags"><div style="display: flex; flex-wrap: wrap; gap: 5px; align-items: center;">${columnTags.join('')}</div></td>
                                    <td class="network-name">
                                        ${token.atomic_network_name ? `<a href="/networks?network=${encodeURIComponent(token.atomic_network_name)}" style="color: var(--accent-purple); font-weight: bold; font-size: 12px; cursor: pointer; text-decoration: none; border-bottom: 1px dotted var(--accent-purple);" title="${token.atomic_network_tier || ''}">${token.atomic_network_name}</a>` : ''}
                                    </td>
                                    <td class="cluster-name">
                                        ${token.cluster_name ? `<span style="color: var(--accent-orange); font-size: 12px;" title="${token.cluster_id ? 'Risk multiplier: ' + token.cluster_risk_multiplier + 'x' : ''}">${token.cluster_name}</span>` : ''}
                                    </td>
                                    <td id="price-${token.mint}" style="color: var(--accent-cyan); font-size: 12px; transition: color 0.2s ease; min-width: 120px;">
                                        <div>
                                            ${token.price_current && token.price_current > 0 ? `$${token.price_current.toFixed(8)}` : '<span style="opacity: 0.5;">...</span>'}
                                        </div>
                                        <div id="source-${token.mint}" style="font-size: 14px; margin-top: 2px; min-height: 14px;">
                                        </div>
                                    </td>
                                    <td id="mc-${token.mint}" style="transition: color 0.2s ease; min-width: 60px;">
                                        ${token.market_cap_current ? '$' + formatMarketCap(token.market_cap_current) : '<span style="opacity: 0.5;">...</span>'}
                                    </td>
                                    <td id="peak-mc-${token.mint}" style="transition: color 0.2s ease; min-width: 60px;">
                                        ${token.market_cap_highest ? '$' + formatMarketCap(token.market_cap_highest) : '<span style="opacity: 0.5;">...</span>'}
                                    </td>
                                    <td>
                                        ${token.market_cap_highest_at ? getTimeToPeak(token.created_at, token.market_cap_highest_at) : ''}
                                    </td>
                                    <td>
                                        ${token.total_events > 0 ? token.total_events : ''}
                                    </td>
                                    <td>
                                        ${token.coverage ? token.coverage.toFixed(1) + '%' : ''}
                                    </td>
                                    <td title="Launched: ${formatDate(token.created_at)}" style="font-size: 11px;">
                                        ${timeSinceLaunch(token.created_at)}
                                    </td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            `;

            document.getElementById('tokens-container').innerHTML = html;

            // Load symbols once (don't reload on every refresh)
            const minMarketCap = 2000;
            tokens.forEach((token, index) => {
                setTimeout(() => {
                    loadSymbol(token.mint);
                }, index * 20);  // Minimal stagger (20ms)
            });

            // DISABLED: Price polling endpoints no longer available
            // The wallet page uses SSE for real-time prices instead
            //
            // // Load initial prices
            // tokens.forEach((token, index) => {
            //     setTimeout(() => {
            //         // Only fetch price if market cap >= $2k or unknown
            //         if (token.market_cap_current >= minMarketCap || !token.market_cap_current) {
            //             loadPrice(token.mint);
            //         }
            //     }, index * 20);  // Minimal stagger (20ms) for smoother initial load
            // });

            // // Set up 5-second price refresh for qualified tokens (real-time feel)
            // if (priceRefreshInterval) clearInterval(priceRefreshInterval);
            // priceRefreshInterval = setInterval(() => {
            //     // Batch all price refreshes with minimal stagger for cleaner updates
            //     tokens.forEach((token, index) => {
            //         setTimeout(() => {
            //             // Only refresh prices for tokens with market cap >= $2k or unknown
            //             if (token.market_cap_current >= minMarketCap || !token.market_cap_current) {
            //                 loadPrice(token.mint);
            //             }
            //         }, index * 10);  // Minimal stagger (10ms) between requests
            //     });
            // }, 5000);  // Refresh every 5 seconds for near real-time updates
        }

        async function loadPrice(mint) {
            try {
                // Try cached price first
                let response = await fetch(`/api/price/${mint}/full`, {
                    signal: priceLoadController.signal
                });
                let data = response.ok ? await response.json() : null;

                // If no price data or error, fetch immediately for new tokens
                if (!data || (!data.price_usd && !data.price_sol) || data.error) {
                    response = await fetch(`/api/price/${mint}/fetch-now`, {
                        method: 'POST',
                        signal: priceLoadController.signal
                    });
                    if (response.ok) {
                        data = await response.json();
                    } else {
                        // Token doesn't have price data available - this is normal for new/illiquid tokens
                        return;
                    }
                }

                // API returns price_usd, price_sol, or price_usdc
                const price = data.price_usd || data.price_sol || data.price_usdc;
                if (price !== null && price > 0) {
                    const priceElement = document.getElementById(`price-${mint}`);
                    if (priceElement) {
                        // Use textContent for better performance and fade transition
                        priceElement.style.opacity = '0.5';
                        priceElement.textContent = `$${price.toFixed(8)}`;
                        // Fade to full opacity
                        setTimeout(() => {
                            priceElement.style.opacity = '1';
                        }, 10);
                    }

                    // Use market cap from API response (from Dexscreener/Jupiter)
                    // Don't calculate fallback - API already has correct market cap
                    let marketCap = data.market_cap || data.market_cap_sol;

                    const mcElement = document.getElementById(`mc-${mint}`);
                    if (mcElement) {
                        const formattedMC = '$' + formatMarketCap(marketCap);
                        const currentText = mcElement.textContent;

                        // Only update if value changed
                        if (currentText !== formattedMC) {
                            mcElement.style.opacity = '0.5';
                            mcElement.textContent = formattedMC;
                            // Fade to full opacity
                            setTimeout(() => {
                                mcElement.style.opacity = '1';
                            }, 10);
                        }
                    }

                    // Update peak market cap if it's a new high
                    const peakMcElement = document.getElementById(`peak-mc-${mint}`);
                    if (peakMcElement && marketCap) {
                        const currentText = peakMcElement.textContent;
                        // Extract current peak value for comparison (remove $ and formatting)
                        const currentPeak = parseFloat(currentText.replace(/[^0-9.]/g, ''));
                        if (!currentPeak || marketCap > currentPeak) {
                            const formattedPeakMC = '$' + formatMarketCap(marketCap);

                            // Only update if value actually changed
                            if (currentText !== formattedPeakMC) {
                                peakMcElement.style.opacity = '0.5';
                                peakMcElement.textContent = formattedPeakMC;
                                // Fade to full opacity
                                setTimeout(() => {
                                    peakMcElement.style.opacity = '1';
                                }, 10);
                            }
                        }
                    }
                }
            } catch (error) {
                if (error.name !== 'AbortError') {
                    console.error(`Error loading price for ${mint}:`, error);
                }
            }
        }

        async function loadSymbol(mint) {
            try {
                const symbolElement = document.getElementById(`symbol-${mint}`);
                if (!symbolElement) return;

                // Fetch from backend proxy (avoids CORS and rate limiting)
                const response = await fetch(`/api/price/symbol/${mint}`);

                if (response.ok) {
                    const data = await response.json();
                    symbolElement.textContent = data.symbol || mint.substring(0, 8).toUpperCase();
                    symbolElement.title = data.name || 'Token';
                } else {
                    // Fallback: show first 8 chars
                    const fallbackSymbol = mint.substring(0, 8).toUpperCase();
                    symbolElement.textContent = fallbackSymbol;
                    symbolElement.title = 'Token';
                }
            } catch (error) {
                // Silently ignore errors - use fallback
                const fallbackSymbol = mint.substring(0, 8).toUpperCase();
                const symbolElement = document.getElementById(`symbol-${mint}`);
                if (symbolElement) {
                    symbolElement.textContent = fallbackSymbol;
                    symbolElement.title = 'Token';
                }
            }
        }

        function sortBy(column) {
            // If clicking same column, toggle direction
            if (sortConfig.column === column) {
                sortConfig.direction = sortConfig.direction === 'asc' ? 'desc' : 'asc';
            } else {
                sortConfig.column = column;
                sortConfig.direction = 'desc';  // Default to descending for new column
            }
            // Rebuild table with current tokens
            const tokens = window.currentTokens || [];
            buildTable(tokens);
        }

        function getRiskClass(riskLevel) {
            if (!riskLevel) return 'risk-medium';
            if (riskLevel.includes('HIGH')) return 'risk-high';
            if (riskLevel.includes('LOW')) return 'risk-low';
            return 'risk-medium';
        }

        function formatDate(timestamp) {
            if (!timestamp) return '-';
            const date = new Date(timestamp * 1000);
            return date.toLocaleString('en-US', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true });
        }

        function formatDateISO(isoString) {
            if (!isoString) return '-';
            const date = new Date(isoString);
            if (isNaN(date.getTime())) return '-';
            return date.toLocaleString('en-US', { month: '2-digit', day: '2-digit', year: '2-digit' });
        }

        function formatTime(seconds) {
            if (!seconds) return '-';
            const minutes = Math.floor(seconds / 60);
            const secs = seconds % 60;
            return `${minutes}m ${secs}s`;
        }

        function timeSinceLaunch(createdAtTimestamp) {
            if (!createdAtTimestamp) return '-';

            const now = Math.floor(Date.now() / 1000);
            let createdSeconds;

            // Handle multiple input formats
            if (typeof createdAtTimestamp === 'string') {
                // ISO format string: "2026-02-10T10:38:19Z"
                const parsedDate = new Date(createdAtTimestamp);
                if (isNaN(parsedDate.getTime())) {
                    return '-'; // Invalid date string
                }
                createdSeconds = Math.floor(parsedDate.getTime() / 1000);
            } else if (typeof createdAtTimestamp === 'number') {
                // Numeric timestamp
                if (createdAtTimestamp > 10000000000) {
                    // Milliseconds
                    createdSeconds = Math.floor(createdAtTimestamp / 1000);
                } else {
                    // Unix seconds
                    createdSeconds = createdAtTimestamp;
                }
            } else {
                return '-'; // Invalid type
            }

            const secondsAgo = now - createdSeconds;

            // Validate that result makes sense
            if (secondsAgo < 0 || secondsAgo > 315360000) {
                return '-'; // Invalid timestamp (more than 10 years in future)
            }

            if (secondsAgo < 60) {
                return `${Math.floor(secondsAgo)}s`;
            } else if (secondsAgo < 3600) {
                const minutes = Math.floor(secondsAgo / 60);
                const seconds = Math.floor(secondsAgo % 60);
                return `${minutes}m${seconds}s`;
            } else if (secondsAgo < 86400) {
                const hours = Math.floor(secondsAgo / 3600);
                const minutes = Math.floor((secondsAgo % 3600) / 60);
                return `${hours}h${minutes}m`;
            } else {
                const days = Math.floor(secondsAgo / 86400);
                const hours = Math.floor((secondsAgo % 86400) / 3600);
                return `${days}d${hours}h`;
            }
        }

        function formatMarketCap(value) {
            if (!value) return '-';
            if (value >= 1000000) {
                return (value / 1000000).toFixed(2) + 'M';
            } else if (value >= 1000) {
                return (value / 1000).toFixed(2) + 'K';
            }
            return value.toFixed(0);
        }

        function getTimeToPeak(migrationTime, peakTime) {
            if (!migrationTime || !peakTime) return '';
            try {
                const migration = new Date(migrationTime);
                const peak = new Date(peakTime);
                const diffSeconds = (peak - migration) / 1000;

                if (diffSeconds < 0) return '';
                if (diffSeconds < 60) return `${Math.floor(diffSeconds)}s`;
                if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}m`;
                const hours = diffSeconds / 3600;
                if (hours < 24) return `${hours.toFixed(1)}h`;
                return `${(hours / 24).toFixed(1)}d`;
            } catch (e) {
                return '';
            }
        }

        // Abort controller for price loading - allows canceling requests when modal opens
        let priceLoadController = new AbortController();
        let priceRefreshInterval = null;

        // Migration feature toggles
        let tokenHistoryEnabled = true;

        function toggleTokenHistory() {
            console.clear();
            tokenHistoryEnabled = !tokenHistoryEnabled;
            const toggle = document.getElementById('tokenHistoryToggle');
            const status = document.getElementById('tokenHistoryStatus');
            toggle.classList.toggle('active');
            status.classList.toggle('active');

            const state = tokenHistoryEnabled ? 'ENABLED' : 'DISABLED';
            console.log('🔧 [TOGGLE] Token History Check: ' + state);
            console.log('State: ' + state + ' | Value: ' + tokenHistoryEnabled);

            // Send to backend to enable/disable
            fetch('/api/migration-settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    token_history_check: tokenHistoryEnabled
                })
            }).then(resp => resp.json()).then(data => {
                console.log('✅ [SETTINGS] Updated - Token History: ' + state);
                console.log('Response:', data);
            }).catch(e => console.error('❌ Error updating settings:', e));
        }

// Listener feature toggles
        let listenLaunchesEnabled = false;  // Will be overridden by initializeSettings()
        let autoExtractFundersEnabled = false;

        function toggleListenLaunches() {
            listenLaunchesEnabled = !listenLaunchesEnabled;
            const toggle = document.getElementById('listenLaunchesToggle');
            const status = document.getElementById('listenLaunchesStatus');
            toggle.classList.toggle('active');
            status.classList.toggle('active');

            const state = listenLaunchesEnabled ? 'ENABLED' : 'DISABLED';
            console.log('🚀 [LISTENER] Token Launch: ' + state);

            // Send to backend
            fetch('/api/listener-settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    listen_to_launches: listenLaunchesEnabled
                })
            }).then(resp => resp.json()).then(data => {
                console.log('✅ [LISTENER] Updated - Launches: ' + state);
            }).catch(e => console.error('❌ Error updating listener settings:', e));
        }

        function toggleAutoExtractFunders() {
            autoExtractFundersEnabled = !autoExtractFundersEnabled;
            const toggle = document.getElementById('autoExtractFundersToggle');
            const status = document.getElementById('autoExtractFundersStatus');
            toggle.classList.toggle('active');
            status.classList.toggle('active');

            const state = autoExtractFundersEnabled ? 'ENABLED' : 'DISABLED';
            console.log('🔄 [LISTENER] Auto Extract Funders: ' + state);

            // Send to backend
            fetch('/api/listener-settings', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    auto_extract_funders: autoExtractFundersEnabled
                })
            }).then(resp => resp.json()).then(data => {
                console.log('✅ [LISTENER] Updated - Auto Extract: ' + state);
            }).catch(e => console.error('❌ Error updating listener settings:', e));
        }

        // Toggle between token table and CEX view
        function toggleFundingNetworkView() {
            const tokensContainer = document.getElementById('tokens-container');
            const fundingNetworkContainer = document.getElementById('funding-network-container');

            if (fundingNetworkContainer.style.display === 'none') {
                // Switch to Funding Network view
                tokensContainer.style.display = 'none';
                fundingNetworkContainer.style.display = 'block';
                loadFundingNetworkData();
            } else {
                // Switch back to token view
                fundingNetworkContainer.style.display = 'none';
                tokensContainer.style.display = 'block';
            }
        }

        // Load and display suspicious funding network coordination
function switchToTokensTab() {
            const tokensContainer = document.getElementById('tokens-container');
            tokensContainer.style.display = 'block';

            // Update sidebar active state
            document.querySelectorAll('.sidebar-item').forEach(item => item.classList.remove('active'));
            document.getElementById('tokensTabBtn').classList.add('active');
        }

        function toggleCEXView() {
            const tokensContainer = document.getElementById('tokens-container');
            const cexContainer = document.getElementById('cex-container');

            if (cexContainer.style.display === 'none') {
                // Switch to CEX view
                tokensContainer.style.display = 'none';
                cexContainer.style.display = 'block';
                loadCEXData();
            } else {
                // Switch back to token view
                cexContainer.style.display = 'none';
                tokensContainer.style.display = 'block';
            }
        }

        // Load CEX data and populate the view
        async function loadCEXData() {
            try {
                const response = await fetch('/api/cex-funders');
                const data = await response.json();

                if (data.error) {
                    document.getElementById('cexExchangesContainer').innerHTML = '<p style="color: var(--color-critical);">Error loading CEX data: ' + data.error + '</p>';
                    return;
                }

                // Populate exchanges grid
                const exchangesContainer = document.getElementById('cexExchangesContainer');
                if (data.exchanges && data.exchanges.length > 0) {
                    exchangesContainer.innerHTML = data.exchanges.map(ex => `
                        <div class="cex-exchange-card">
                            <h4>🏛️ ${ex.cex_exchange}</h4>
                            <div class="stat">
                                <span class="stat-label">Creators Funded:</span>
                                <span class="stat-value">${ex.creator_count}</span>
                            </div>
                            <div class="stat">
                                <span class="stat-label">Wallets:</span>
                                <span class="stat-value">${ex.funder_count}</span>
                            </div>
                            <div class="stat">
                                <span class="stat-label">Total SOL:</span>
                                <span class="stat-value">${(ex.total_sol || 0).toFixed(2)}</span>
                            </div>
                        </div>
                    `).join('');
                } else {
                    exchangesContainer.innerHTML = '<p style="color: var(--text-secondary);">No CEX funders found</p>';
                }

                // Populate top CEX funders table
                const cexFundersBody = document.getElementById('topCexFundersBody');
                if (data.top_cex_funders && data.top_cex_funders.length > 0) {
                    cexFundersBody.innerHTML = data.top_cex_funders.map(funder => {
                        // Use enriched display_name from API, fallback to database fields
                        const displayName = funder.display_name || `${funder.cex_exchange || 'Unknown'} ${funder.cex_type || 'Wallet'}`;
                        return `
                            <tr>
                                <td style="font-family: monospace; font-size: 12px; word-break: break-all;" title="${funder.funder_address}">${funder.funder_address}</td>
                                <td><span class="cex-exchange-name">${displayName}</span></td>
                                <td>${funder.creators_funded}</td>
                                <td style="text-align: right; color: var(--color-low); font-weight: 600;">${(funder.total_sol || 0).toFixed(2)}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    cexFundersBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary);">No top CEX funders found</td></tr>';
                }

                // Populate CEX-funded creators table
                const creatorsBody = document.getElementById('cexFundedCreatorsBody');
                if (data.cex_funded_creators && data.cex_funded_creators.length > 0) {
                    creatorsBody.innerHTML = data.cex_funded_creators.map(creator => `
                        <tr>
                            <td style="font-family: monospace; font-size: 12px; word-break: break-all;" title="${creator.creator_address}">${creator.creator_address}</td>
                            <td style="text-align: center; color: var(--accent-cyan); font-weight: 600;">${creator.exchanges_funding}</td>
                            <td style="text-align: right; color: var(--color-low); font-weight: 600;">${(creator.total_cex_funding || 0).toFixed(2)}</td>
                            <td>
                                <a href="#" onclick="showCreatorDetails('${creator.creator_address}'); toggleCEXView(); return false;" style="color: var(--accent-cyan); text-decoration: none;">View Creator →</a>
                            </td>
                        </tr>
                    `).join('');
                } else {
                    creatorsBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary);">No CEX-funded creators found</td></tr>';
                }

            } catch (error) {
                console.error('Error loading CEX data:', error);
                document.getElementById('cexExchangesContainer').innerHTML = '<p style="color: var(--color-critical);">Error loading CEX data</p>';
            }
        }

        // Initialize settings from backend on page load
        async function initializeSettings() {
            try {
                // Load migration settings
                const respMig = await fetch('/api/migration-settings');
                const migSettings = await respMig.json();

                tokenHistoryEnabled = migSettings.token_history_check;

                // Load listener settings
                const respListener = await fetch('/api/listener-settings');
                const listenerSettings = await respListener.json();

                listenLaunchesEnabled = listenerSettings.listen_to_launches;
                autoExtractFundersEnabled = listenerSettings.auto_extract_funders;

                // Update migration toggle switch states
                const tokenHistoryToggle = document.getElementById('tokenHistoryToggle');
                const tokenHistoryStatus = document.getElementById('tokenHistoryStatus');

                if (!tokenHistoryEnabled) {
                    tokenHistoryToggle.classList.remove('active');
                    tokenHistoryStatus.classList.remove('active');
                } else {
                    tokenHistoryToggle.classList.add('active');
                    tokenHistoryStatus.classList.add('active');
                }

                // Update listener toggle switch states
                const listenLaunchesToggle = document.getElementById('listenLaunchesToggle');
                const listenLaunchesStatus = document.getElementById('listenLaunchesStatus');

                if (!listenLaunchesEnabled) {
                    listenLaunchesToggle.classList.remove('active');
                    listenLaunchesStatus.classList.remove('active');
                } else {
                    listenLaunchesToggle.classList.add('active');
                    listenLaunchesStatus.classList.add('active');
                }

                // Update auto extract funders toggle switch states
                const autoExtractFundersToggle = document.getElementById('autoExtractFundersToggle');
                const autoExtractFundersStatus = document.getElementById('autoExtractFundersStatus');

                if (autoExtractFundersEnabled) {
                    autoExtractFundersToggle.classList.add('active');
                    autoExtractFundersStatus.classList.add('active');
                }

                const historyState = tokenHistoryEnabled ? '✅ ON' : '❌ OFF';
                const launchState = listenLaunchesEnabled ? '✅ ON' : '❌ OFF';
                const extractState = autoExtractFundersEnabled ? '✅ ON' : '❌ OFF';
                console.log('📋 [SETTINGS LOADED] Migration - Token History: ' + historyState);
                console.log('📋 [SETTINGS LOADED] Listener - Token Launch: ' + launchState);
                console.log('📋 [SETTINGS LOADED] Listener - Auto Extract Funders: ' + extractState);
            } catch (e) {
                console.error('❌ Error loading settings:', e);
            }
        }

        // Initialize SSE price streaming
        function initDashboardPriceStream() {
            console.log('[SSE_PRICE] Connecting to price stream...');
            const es = new EventSource('/api/price-stream');
            let eventCount = 0;

            es.onopen = () => {
                console.log('[SSE_PRICE] ✅ Connected');
            };

            es.onmessage = (event) => {
                try {
                    const update = JSON.parse(event.data);
                    if (update.type === 'price_update') {
                        eventCount++;
                        const mint = update.mint;
                        const priceElem = document.getElementById(`price-${mint}`);
                        const mcElem = document.getElementById(`mc-${mint}`);

                        if (priceElem) {
                            priceElem.textContent = `$${update.price_usd.toFixed(8)}`;
                        }
                        if (mcElem && update.market_cap) {
                            const formattedMC = '$' + formatMarketCap(update.market_cap);
                            mcElem.textContent = formattedMC;
                        }
                    } else if (update.type === 'pool_registered') {
                        console.log(`[SSE] Pool registered for ${update.mint?.substring(0, 16)}... (${update.elapsed_secs}s) — registering for price tracking + refreshing token list`);
                        // Register immediately for price tracking so snapshots start before loadTokens fires
                        if (update.mint) {
                            fetch('/api/price/batch/register', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({mints: [update.mint]})
                            }).catch(() => {});
                        }
                        loadTokens();
                    }
                } catch (error) {
                    console.error('[SSE_PRICE] Parse error:', error);
                }
            };

            es.onerror = () => {
                console.log('[SSE_PRICE] Connection closed, will auto-reconnect...');
                es.close();
            };
        }

        // Load tokens immediately and then every 10 seconds
        (async () => {
            await initializeSettings();
            loadTokens();
            initDashboardPriceStream();  // Start SSE price stream
            setInterval(loadTokens, 60000);  // Reload token list every 60s (not constantly)
        })();

        // Metrics Modal Functions
        async function showTokenMetrics(mint) {
            // Cancel ongoing price fetches to free up bandwidth for modal
            priceLoadController.abort();
            priceLoadController = new AbortController();
            const modal = document.getElementById('metricsModal');
            document.getElementById('modalMint').textContent = mint;

            // Load pools for this token
            loadTokenPools(mint);

            try {
                const response = await fetch(`/api/token-metrics/${mint}`);
                const data = await response.json();

                if (data.error) {
                    alert('Token metrics not found');
                    return;
                }

                // Populate metrics grid
                const metricsGrid = document.getElementById('metricsGrid');
                const metrics = data.metrics;
                const metricLabels = {
                    'mint_concentration': 'Mint Concentration',
                    'unique_minters_ratio': 'Unique Minters',
                    'sell_suppression_ratio': 'Sell Suppression',
                    'mint_velocity_sec': 'Mint Velocity (per sec)',
                    'buy_size_variance': 'Buy Size Variance',
                    'sell_volume_concentration': 'Sell Volume Concentration',
                    'creator_activity_ratio': 'Creator Activity'
                };

                metricsGrid.innerHTML = '';

                // Build HTML string first, then set it once
                let metricsHTML = metricsGrid.innerHTML;
                Object.keys(metricLabels).forEach(key => {
                    const value = metrics[key] !== null && metrics[key] !== undefined ? metrics[key].toFixed(4) : '';
                    metricsHTML += `
                        <div class="metric">
                            <label>${metricLabels[key]}</label>
                            <span>${value}</span>
                        </div>
                    `;
                });

                // Add coverage metric
                let coverage = '';
                if (data.coverage !== null && data.coverage !== undefined) {
                    coverage = data.coverage.toFixed(1);
                }

                metricsHTML += `
                    <div class="metric">
                        <label>Analysis Coverage</label>
                        <span>${coverage}%</span>
                    </div>
                `;

                // Add market cap metrics
                let marketCapCurrent = '';
                let marketCapHighest = '';
                if (data.market_cap && data.market_cap.current) {
                    marketCapCurrent = '$' + formatMarketCap(data.market_cap.current);
                }
                if (data.market_cap && data.market_cap.highest) {
                    marketCapHighest = '$' + formatMarketCap(data.market_cap.highest);
                }

                metricsHTML += `
                    <div class="metric">
                        <label>Market Cap</label>
                        <span>${marketCapCurrent}</span>
                    </div>
                    <div class="metric">
                        <label>Peak MC</label>
                        <span>${marketCapHighest}</span>
                    </div>
                `;

                // Add price source indicator
                let priceSourceDisplay = '';
                if (data.price && data.price.source) {
                    const source = data.price.source;
                    let sourceIcon = '';
                    let sourceLabel = source;

                    if (source === 'pool') {
                        sourceIcon = '📡';
                        sourceLabel = 'WebSocket (Real-time)';
                    } else if (source.includes('pool')) {
                        sourceIcon = '📊';
                        sourceLabel = source.charAt(0).toUpperCase() + source.slice(1);
                    } else {
                        sourceIcon = '⚪';
                    }

                    priceSourceDisplay = `
                        <div class="metric">
                            <label>Price Source</label>
                            <span>${sourceIcon} ${sourceLabel}</span>
                        </div>
                    `;
                    metricsHTML += priceSourceDisplay;
                }

                metricsGrid.innerHTML = metricsHTML;

                // Populate first-price latency
                const latencyRow = document.getElementById('firstPriceLatencyRow');
                const latencyVal = document.getElementById('firstPriceLatencyValue');
                const latencyBadge = document.getElementById('firstPriceSourceBadge');
                if (data.first_price_latency && data.first_price_latency !== 'unknown') {
                    latencyVal.textContent = data.first_price_latency;
                    const src = data.first_price_source || '';
                    if (src === 'pool') {
                        latencyBadge.textContent = 'pool';
                        latencyBadge.style.cssText = 'margin-left:8px; padding:2px 6px; border-radius:4px; font-size:11px; background:rgba(74,222,128,0.2); color:#4ade80;';
                    } else if (src === 'cached') {
                        latencyBadge.textContent = 'cached';
                        latencyBadge.style.cssText = 'margin-left:8px; padding:2px 6px; border-radius:4px; font-size:11px; background:rgba(251,146,60,0.2); color:#fda34b;';
                    } else {
                        latencyBadge.textContent = src;
                        latencyBadge.style.cssText = 'margin-left:8px; padding:2px 6px; border-radius:4px; font-size:11px; background:rgba(148,163,184,0.2); color:#94a3b8;';
                    }
                    latencyRow.style.display = 'block';
                } else {
                    latencyRow.style.display = 'none';
                }

                // Populate risk section
                const riskSection = document.getElementById('riskSection');
                const risk = data.risk;
                const rugProbability = (risk.rug_probability * 100).toFixed(1);

                riskSection.innerHTML = `
                    <p>
                        <label>Rug Probability:</label>
                        <span class="risk-value">${rugProbability}%</span>
                        <span style="color: var(--text-secondary); margin-left: 10px;">${risk.risk_level || ''}</span>
                    </p>
                `;

                // Set DexTools link
                document.getElementById('dextoolsLink').href = `https://www.dextools.io/app/solana/token/${mint}`;

                // Show modal
                modal.style.display = 'block';
            } catch (error) {
                console.error('Error loading metrics:', error);
                alert('Failed to load token metrics');
            }
        }

        function closeTokenMetrics() {
            document.getElementById('metricsModal').style.display = 'none';
        }

        async function loadTokenPools(mint) {
            try {
                const response = await fetch(`/api/token/${mint}/pools`);
                const data = await response.json();

                const poolsBody = document.getElementById('poolsBody');

                if (!data.pools || data.pools.length === 0) {
                    poolsBody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-secondary);">No pools found</td></tr>';
                    return;
                }

                poolsBody.innerHTML = data.pools.map(pool => `
                    <tr>
                        <td><code style="font-size: 10px; color: var(--accent-cyan); word-break: break-all;">${pool.pool_address}</code></td>
                        <td><code style="font-size: 10px; color: var(--accent-purple); word-break: break-all;">${pool.base_account}</code></td>
                        <td><code style="font-size: 10px; color: var(--accent-green); word-break: break-all;">${pool.quote_account}</code></td>
                        <td>
                            <span style="
                                padding: 4px 8px;
                                border-radius: 4px;
                                font-size: 11px;
                                font-weight: bold;
                                white-space: nowrap;
                                ${pool.vault_validation_status === 'validated' ? 'background: rgba(74, 222, 128, 0.2); color: #4ade80;' : 'background: rgba(251, 146, 60, 0.2); color: #fda34b;'}
                            ">
                                ${pool.vault_validation_status}
                            </span>
                        </td>
                    </tr>
                `).join('');
            } catch (error) {
                console.error('Error loading pools:', error);
                document.getElementById('poolsBody').innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--color-critical);">Error loading pools</td></tr>';
            }
        }

        async function showCreatorDetails(creatorAddress) {
            if (!creatorAddress || creatorAddress.length < 30) {
                alert('Creator address not available for this token yet');
                return;
            }
            // Cancel ongoing price fetches to free up bandwidth for modal
            priceLoadController.abort();
            priceLoadController = new AbortController();

            const modal = document.getElementById('creatorModal');

            // Display creator address with domain if available
            let creatorDisplay = creatorAddress;
            // Will be updated after API response

            try {
                const response = await fetch(`/api/creator-details/${creatorAddress}`);
                const data = await response.json();

                if (data.error) {
                    console.error('Creator details error:', data);
                    let errorMsg = 'Creator details not found';
                    if (data.details) {
                        console.error('Details:', data.details);
                        errorMsg = errorMsg + ' - Server error, check console for details';
                    }
                    alert(errorMsg);
                    return;
                }

                // Display creator address with domain tag
                creatorDisplay = creatorAddress;
                if (data.creator_address_tags && data.creator_address_tags.domain) {
                    const domains = data.creator_address_tags.domain;
                    creatorDisplay = `<div style="word-break: break-all;">${creatorAddress} <span class="domain-tag" style="font-size: 11px; margin-left: 8px;">🌐 ${domains[0]}</span></div>`;
                }
                document.getElementById('modalCreator').innerHTML = creatorDisplay;

                // Populate creator stats with styled badges
                const tokenCount = data.tokens.length;
                document.getElementById('creatorTotalTokens').innerHTML = `<span class="creator-tag tag-repeat" style="display: inline-block;">${tokenCount} token${tokenCount !== 1 ? 's' : ''}</span>`;

                const fundingAmount = (data.funding.total_sol !== null ? data.funding.total_sol.toFixed(2) : '0.00');
                document.getElementById('creatorTotalFunding').innerHTML = `<span class="creator-tag tag-funding" style="display: inline-block;">${fundingAmount} SOL</span>`;

                // Show CEX funders if any
                let fundersText = data.funding.total_funders || '0';
                if (data.funding.cex_funders > 0) {
                    fundersText = '🏛️ ' + data.funding.cex_funders + ' CEX + ' + ((data.funding.total_funders || 0) - data.funding.cex_funders) + ' other';
                }
                document.getElementById('creatorTotalFunders').textContent = fundersText;

                document.getElementById('creatorNetworkSize').textContent = (data.cluster.total_wallets || 0) + ' wallets';

                // Display network name if available with CEX/INFRA badges
                if (data.network_name) {
                    let networkBadges = '';
                    if (data.network_type === 'cex_connected') {
                        networkBadges = '<span style="background: #ef4444; color: white; padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: 700; margin-left: 8px;">🏦 CEX</span>';
                    } else if (data.network_type === 'infra_connected') {
                        networkBadges = '<span style="background: #f97316; color: white; padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: 700; margin-left: 8px;">🔧 INFRA</span>';
                    } else if (data.network_type === 'mixed') {
                        networkBadges = '<span style="background: #d97706; color: white; padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: 700; margin-left: 8px;">⚠️ MIXED</span>';
                    } else if (data.network_type === 'organic') {
                        networkBadges = '<span style="background: #22c55e; color: white; padding: 2px 8px; border-radius: 3px; font-size: 10px; font-weight: 700; margin-left: 8px;">✓ ORGANIC</span>';
                    }
                    document.getElementById('creatorNetworkName').innerHTML = `<a href="/creator-network/${encodeURIComponent(data.network_name)}" style="color: var(--accent-purple); text-decoration: none; cursor: pointer; border-bottom: 1px dotted var(--accent-purple);" title="View network">${data.network_name}</a>${networkBadges}`;
                } else {
                    document.getElementById('creatorNetworkName').textContent = 'Unassigned';
                }

                // Display creator tags (remove 'uses_' prefix for cleaner display, deduplicate, filter addresses)
                const tagsContainer = document.getElementById('creatorTagsContainer');
                if (data.tags && data.tags.length > 0) {
                    // Deduplicate tags and strip 'uses_' prefix for display
                    const seenTags = new Set();
                    const uniqueTags = [];

                    for (const t of data.tags) {
                        // SKIP if this looks like a Solana address (base58 characters, 30+ chars, may have period)
                        // Matches: standard addresses like "ABC123...", or "ABC123...." (with period for domain-like format)
                        if (t.tag.match(/^[1-9A-HJ-NP-Za-km-z]{30,}\.?$/)) {
                            continue;  // Skip addresses, only show service names
                        }

                        // Also skip if tag contains "interacted with" - these are auto-generated account tags
                        if (t.description && t.description.toLowerCase().includes('interacted with')) {
                            continue;
                        }

                        const displayTag = t.tag.replace('uses_', '').toLowerCase();
                        if (!seenTags.has(displayTag)) {
                            seenTags.add(displayTag);
                            uniqueTags.push({
                                display: displayTag,
                                original: t.tag,
                                description: t.description,
                                amount_sol: t.amount_sol
                            });
                        }
                    }

                    const tagsHTML = uniqueTags.map(t => {
                        // For jitotip, display amount prominently
                        let tagContent = t.display;
                        if (t.original === 'uses_jitotip' && t.amount_sol) {
                            tagContent = `${t.display} (${t.amount_sol.toFixed(6)} SOL)`;
                        } else if ((t.original === 'uses_meteora' || t.original === 'uses_axiom' || t.original === 'uses_debridge') && t.amount_sol) {
                            tagContent = `${t.display} (${t.amount_sol.toFixed(4)} SOL)`;
                        }
                        return `
                            <div class="creator-tag" title="${t.description}">
                                <span class="tag-label">${tagContent}</span>
                            </div>
                        `;
                    }).join('');
                    tagsContainer.innerHTML = tagsHTML;
                } else {
                    tagsContainer.innerHTML = '';
                }

                // Populate tokens launched table
                const tokensBody = document.getElementById('tokensLaunchedBody');
                if (data.tokens.length > 0) {
                    tokensBody.innerHTML = data.tokens.map(token => {
                        const createTxShort = token.create_tx_signature ? token.create_tx_signature.substring(0, 16) + '...' : 'N/A';
                        const createTxCell = token.create_tx_signature
                            ? `<a href="https://solscan.io/tx/${token.create_tx_signature}" target="_blank" class="create-tx-link" title="${token.create_tx_signature}">${createTxShort}</a>
                                <div style="display: inline-block; margin-left: 8px;">
                                    <button onclick="viewTransaction('${token.create_tx_signature}')" style="background: rgba(6, 182, 212, 0.2); color: var(--accent-cyan); border: 1px solid var(--accent-cyan); padding: 3px 10px; border-radius: 3px; cursor: pointer; font-size: 11px; font-family: monospace;">View Raw</button>
                                </div>`
                            : 'N/A';

                        return `
                            <tr>
                                <td><a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="${token.mint}">${token.mint.substring(0, 16)}...</a></td>
                                <td>${formatDateISO(token.created_at)}</td>
                                <td><span class="risk-score risk-${token.risk_level ? token.risk_level.toLowerCase() : 'medium'}">${token.risk_level || 'N/A'}</span></td>
                                <td>${formatMarketCap(token.market_cap_current)}</td>
                                <td>${createTxCell}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    tokensBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-secondary);">No tokens launched yet</td></tr>';
                }

                // Separate CEX and non-CEX funders
                // Use enriched data from API: is_cex from infra_mapping lookup, not database flag
                const cexFunders = (data.top_funders || []).filter(f => f.is_cex);
                const nonCexFunders = (data.top_funders || []).filter(f => !f.is_cex);

                // Show/hide CEX funders section
                const cexSection = document.getElementById('cexFundersSection');
                if (cexFunders.length > 0) {
                    cexSection.style.display = 'block';
                    const cexBody = document.getElementById('cexFundersBody');
                    cexBody.innerHTML = cexFunders.map(funder => {
                        const amountStr = funder.amount_sol < 0.01
                            ? funder.amount_sol.toFixed(6)
                            : funder.amount_sol.toFixed(2);
                        // Use enriched display_name from infra_mapping, fall back to database fields
                        const displayName = funder.display_name || `${funder.cex_exchange || 'Unknown'} ${funder.cex_type || 'Hot Wallet'}`;
                        return `
                            <tr>
                                <td><span class="cex-exchange-name">${displayName}</span></td>
                                <td title="${funder.funder_address}" style="font-family: monospace; word-break: break-all;">${funder.funder_address}</td>
                                <td>${amountStr} SOL</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    cexSection.style.display = 'none';
                }

                // Check for multi-creator funders (coordination risk)
                try {
                    const multiCreatorResponse = await fetch('/api/multi-creator-funders');
                    const multiCreatorData = await multiCreatorResponse.json();

                    if (multiCreatorData.multi_creator_funders && multiCreatorData.multi_creator_funders.length > 0) {
                        // Check if any of this creator's funders are in the multi-creator list
                        const thisCreatorFunders = new Set(data.top_funders.map(f => f.funder_address));
                        const matchingMultiCreatorFunders = multiCreatorData.multi_creator_funders.filter(mf =>
                            thisCreatorFunders.has(mf.funder_address)
                        );

                        if (matchingMultiCreatorFunders.length > 0) {
                            const multiSection = document.getElementById('multiCreatorFundersSection');
                            // Filter out INFRA and CEX accounts - only show suspicious multi-creator funders
                            const suspiciousMatchers = matchingMultiCreatorFunders.filter(f => !f.is_infrastructure && !f.is_cex_account);

                            if (suspiciousMatchers.length > 0) {
                                multiSection.style.display = 'block';
                            } else {
                                multiSection.style.display = 'none';
                            }

                            const multiBody = document.getElementById('tokenMetricsMultiCreatorFundersBody');

                            multiBody.innerHTML = suspiciousMatchers.map(funder => {
                                const firstFundingDate = funder.first_funding_at ? new Date(funder.first_funding_at).toLocaleDateString() : 'N/A';
                                const lastFundingDate = funder.last_funding_at ? new Date(funder.last_funding_at).toLocaleDateString() : 'N/A';
                                const totalSol = funder.total_sol_sent ? funder.total_sol_sent.toFixed(2) : '0.00';

                                return `
                                    <tr>
                                        <td title="${funder.funder_address}" style="font-family: monospace;">
                                            <a href="/funding-hub/${funder.funder_address}" style="color: var(--color-critical); text-decoration: none; cursor: pointer;">
                                                ${funder.funder_address}
                                            </a>
                                        </td>
                                        <td><strong>${funder.creator_count}</strong></td>
                                        <td>${totalSol} SOL</td>
                                        <td>${firstFundingDate}</td>
                                        <td>${lastFundingDate}</td>
                                    </tr>
                                `;
                            }).join('');
                        } else {
                            document.getElementById('multiCreatorFundersSection').style.display = 'none';
                        }
                    } else {
                        document.getElementById('multiCreatorFundersSection').style.display = 'none';
                    }
                } catch (error) {
                    console.error('Error loading multi-creator funders:', error);
                    document.getElementById('multiCreatorFundersSection').style.display = 'none';
                }

                // Show/hide other labeled funders section
                const otherSection = document.getElementById('otherFundersSection');
                const labeledNonCexFunders = nonCexFunders.filter(f => f.display_name);
                if (labeledNonCexFunders.length > 0) {
                    otherSection.style.display = 'block';
                    const otherBody = document.getElementById('otherFundersBody');
                    otherBody.innerHTML = labeledNonCexFunders.map(funder => {
                        const amountStr = funder.amount_sol < 0.01
                            ? funder.amount_sol.toFixed(6)
                            : funder.amount_sol.toFixed(2);
                        return `
                            <tr>
                                <td><span style="color: var(--color-low); font-weight: 600;">${funder.display_name}</span></td>
                                <td>${funder.category || 'unknown'}</td>
                                <td>${amountStr} SOL</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    otherSection.style.display = 'none';
                }

                // Populate all funders table (complete list)
                const allFundersBody = document.getElementById('allFundersBody');
                if (data.top_funders && data.top_funders.length > 0) {
                    allFundersBody.innerHTML = data.top_funders.map(funder => {
                        const amountStr = funder.amount_sol < 0.01
                            ? funder.amount_sol.toFixed(6)
                            : funder.amount_sol.toFixed(2);

                        let funderType = 'Wallet';
                        if (funder.is_cex) {
                            // Use display_name from enriched data if available, otherwise fallback
                            if (funder.display_name) {
                                funderType = funder.display_name;
                            } else {
                                funderType = `${funder.cex_exchange || 'CEX'} ${funder.cex_type ? `(${funder.cex_type})` : ''}`.trim();
                            }
                        } else if (funder.display_name) {
                            funderType = funder.display_name;
                        }

                        return `
                            <tr>
                                <td title="${funder.funder_address}" style="font-family: monospace; color: var(--address-color); word-break: break-all;">${funder.funder_address}</td>
                                <td>${amountStr} SOL</td>
                                <td>${funderType}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    allFundersBody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--text-secondary);">No funders found</td></tr>';
                }

                // Load tokens funded (if this address is a funder)
                try {
                    const fundedResponse = await fetch(`/api/funder-tokens/${creatorAddress}`);
                    const fundedData = await fundedResponse.json();

                    if (fundedData.tokens_funded && fundedData.tokens_funded.length > 0) {
                        document.getElementById('tokensFundedSection').style.display = 'block';
                        const fundedBody = document.getElementById('tokensFundedBody');

                        fundedBody.innerHTML = fundedData.tokens_funded.map(token => {
                            return `
                                <tr>
                                    <td style="word-break: break-all;"><a href="#" onclick="showTokenMetrics('${token.mint}'); return false;" class="mint-link" title="${token.mint}">${token.mint}</a></td>
                                    <td style="font-family: monospace; font-size: 11px; word-break: break-all;">
                                        <a href="#" onclick="showCreatorDetails('${token.creator_address}'); return false;" title="${token.creator_address}">${token.creator_address}</a>
                                    </td>
                                    <td>${(token.funding_amount_sol || 0).toFixed(2)} SOL</td>
                                    <td>${formatDateISO(token.created_at)}</td>
                                    <td><span class="risk-score risk-${token.risk_level ? token.risk_level.toLowerCase() : 'medium'}">${token.risk_level || 'N/A'}</span></td>
                                </tr>
                            `;
                        }).join('');
                    } else {
                        document.getElementById('tokensFundedSection').style.display = 'none';
                    }
                } catch (error) {
                    console.error('Error loading funded tokens:', error);
                    document.getElementById('tokensFundedSection').style.display = 'none';
                }

                // Populate top recipients table (where creator sent SOL)
                const recipientsBody = document.getElementById('topRecipientsBody');
                if (data.top_recipients && data.top_recipients.length > 0) {
                    // Show ALL recipients (not just labeled ones)
                    recipientsBody.innerHTML = data.top_recipients.map(recipient => {
                        // Check if this recipient is connected to other creators
                        let networkIndicator = '';
                        let networkTooltip = '';

                        if (recipient.is_network_coordinator) {
                            const info = recipient.coordinator_info;
                            networkIndicator = `<span class="network-badge network-${info.confidence}" title="Network Coordinator">🔗</span>`;
                            networkTooltip = `Linked to ${info.creator_count} creators | Confidence: ${info.confidence}`;
                        } else if (recipient.shared_with_creators) {
                            networkIndicator = `<span class="shared-badge" title="Shared with ${recipient.shared_creator_count} other creators">👥</span>`;
                            networkTooltip = `Also linked to: ${recipient.shared_with_creators.slice(0, 2).map(c => c.substring(0, 8) + '...').join(', ')}${recipient.shared_with_creators.length > 2 ? ' +' + (recipient.shared_with_creators.length - 2) + ' more' : ''}`;
                        }

                        // Format amount: show more decimals for small amounts
                        const recipientAmountStr = recipient.amount_sol < 0.01
                            ? recipient.amount_sol.toFixed(6)
                            : recipient.amount_sol.toFixed(2);

                        // Display label name if available, otherwise use address
                        const displayLabel = recipient.display_name || recipient.recipient_address;

                        return `
                            <tr class="${recipient.is_network_coordinator ? 'row-network-coordinator' : recipient.shared_with_creators ? 'row-shared-recipient' : ''}">
                                <td title="${recipient.recipient_address}" style="font-family: monospace; font-size: 12px; word-break: break-all;">
                                    ${displayLabel}
                                    ${networkIndicator ? `<div style="margin-top: 3px; font-size: 10px; color: var(--text-secondary);">${networkTooltip}</div>` : ''}
                                </td>
                                <td>${recipientAmountStr} SOL</td>
                                <td>${networkIndicator || (recipient.is_infrastructure ? recipient.category : 'Wallet')}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    recipientsBody.innerHTML = '<tr><td colspan="3" style="text-align: center; color: var(--text-secondary);">No outgoing transfers</td></tr>';
                }

                // Populate cross-creator references
                const crossRefsContainer = document.getElementById('crossReferencesContainer');
                if (data.cross_references && data.cross_references.length > 0) {
                    let crossRefsHTML = '';

                    // Handle both new structure (with inbound/outbound) and legacy structure (flat array)
                    const inbound = data.cross_references.inbound || [];
                    const outbound = data.cross_references.outbound || [];
                    const legacyRefs = Array.isArray(data.cross_references) ? data.cross_references : [];

                    // Display INBOUND cross-references (shared funders)
                    if (inbound.length > 0) {
                        crossRefsHTML += '<div style="margin-bottom: 20px;">';
                        crossRefsHTML += '<div style="color: var(--color-high); font-weight: bold; margin-bottom: 10px; font-size: 12px; text-transform: uppercase;">📥 Inbound (Shared Funders)</div>';
                        for (const ref of inbound) {
                            const creatorList = ref.other_creators
                                .slice(0, 3)
                                .map(c => `<span style="background: rgba(239, 68, 68, 0.2); padding: 2px 6px; border-radius: 3px; margin: 2px; display: inline-block; font-size: 10px; font-family: monospace; word-break: break-all;">${c}</span>`)
                                .join('');
                            const moreCreators = ref.other_creators.length > 3 ? `<span style="color: var(--text-secondary); font-size: 10px;"> +${ref.other_creators.length - 3} more</span>` : '';

                            crossRefsHTML += `
                                <div style="margin-bottom: 12px; padding: 10px; background: rgba(239, 68, 68, 0.1); border-left: 3px solid rgba(239, 68, 68, 0.4); border-radius: 4px;">
                                    <div style="font-family: monospace; font-size: 11px; color: var(--accent-cyan); word-break: break-all; margin-bottom: 5px;">
                                        ${ref.address}
                                    </div>
                                    <div style="font-size: 11px; color: var(--text-secondary);">
                                        <strong>🔴 Funds this creator AND ${ref.creator_count} other creator${ref.creator_count > 1 ? 's' : ''}</strong>
                                    </div>
                                    <div style="margin-top: 5px;">
                                        ${creatorList} ${moreCreators}
                                    </div>
                                </div>
                            `;
                        }
                        crossRefsHTML += '</div>';
                    }

                    // Display OUTBOUND cross-references (shared recipients)
                    if (outbound.length > 0) {
                        crossRefsHTML += '<div style="margin-bottom: 20px;">';
                        crossRefsHTML += '<div style="color: var(--color-medium); font-weight: bold; margin-bottom: 10px; font-size: 12px; text-transform: uppercase;">📤 Outbound (Shared Recipients)</div>';
                        for (const ref of outbound) {
                            const creatorList = ref.other_creators
                                .slice(0, 3)
                                .map(c => `<span style="background: rgba(124, 58, 237, 0.2); padding: 2px 6px; border-radius: 3px; margin: 2px; display: inline-block; font-size: 10px; font-family: monospace; word-break: break-all;">${c}</span>`)
                                .join('');
                            const moreCreators = ref.other_creators.length > 3 ? `<span style="color: var(--text-secondary); font-size: 10px;"> +${ref.other_creators.length - 3} more</span>` : '';

                            crossRefsHTML += `
                                <div style="margin-bottom: 12px; padding: 10px; background: rgba(124, 58, 237, 0.05); border-left: 3px solid rgba(124, 58, 237, 0.3); border-radius: 4px;">
                                    <div style="font-family: monospace; font-size: 11px; color: var(--accent-cyan); word-break: break-all; margin-bottom: 5px;">
                                        ${ref.address}
                                    </div>
                                    <div style="font-size: 11px; color: var(--accent-purple);">
                                        <strong>⚠️ Receives from this creator AND ${ref.creator_count} other creator${ref.creator_count > 1 ? 's' : ''}</strong>
                                    </div>
                                    <div style="margin-top: 5px;">
                                        ${creatorList} ${moreCreators}
                                    </div>
                                </div>
                            `;
                        }
                        crossRefsHTML += '</div>';
                    }

                    // Fallback: handle legacy flat array format
                    if (legacyRefs.length > 0 && inbound.length === 0 && outbound.length === 0) {
                        for (const crossRef of legacyRefs) {
                            const creatorList = crossRef.other_creators
                                .slice(0, 3)
                                .map(c => `<span style="background: rgba(124, 58, 237, 0.2); padding: 2px 6px; border-radius: 3px; margin: 2px; display: inline-block; font-size: 10px; font-family: monospace; word-break: break-all;">${c}</span>`)
                                .join('');
                            const moreCreators = crossRef.other_creators.length > 3 ? `<span style="color: var(--text-secondary); font-size: 10px;"> +${crossRef.other_creators.length - 3} more</span>` : '';

                            crossRefsHTML += `
                                <div style="margin-bottom: 12px; padding: 10px; background: rgba(124, 58, 237, 0.05); border-left: 3px solid rgba(124, 58, 237, 0.3); border-radius: 4px;">
                                    <div style="font-family: monospace; font-size: 11px; color: var(--accent-cyan); word-break: break-all; margin-bottom: 5px;">
                                        ${crossRef.recipient_address}
                                    </div>
                                    <div style="font-size: 11px; color: var(--accent-purple);">
                                        <strong>⚠️ Also linked to ${crossRef.creator_count} other creator${crossRef.creator_count > 1 ? 's' : ''}:</strong>
                                    </div>
                                    <div style="margin-top: 5px;">
                                        ${creatorList} ${moreCreators}
                                    </div>
                                </div>
                            `;
                        }
                    }

                    if (crossRefsHTML) {
                        crossRefsContainer.innerHTML = crossRefsHTML;
                    } else {
                        crossRefsContainer.innerHTML = '<p style="color: var(--text-secondary); text-align: center; margin: 0;">No cross-creator connections detected ✓</p>';
                    }
                } else {
                    crossRefsContainer.innerHTML = '<p style="color: var(--text-secondary); text-align: center; margin: 0;">No cross-creator connections detected ✓</p>';
                }

                // Populate cluster info
                const clusterInfo = document.getElementById('clusterInfo');
                if (data.cluster.total_wallets > 0) {
                    clusterInfo.innerHTML = `
                        <p><strong>Total Network Wallets:</strong> ${data.cluster.total_wallets}</p>
                        <p><strong>Direct Connections (Hop 0):</strong> ${data.cluster.hop0}</p>
                        <p><strong>Secondary Connections (Hop 1):</strong> ${data.cluster.hop1}</p>
                        <p><strong>Tertiary Connections (Hop 2):</strong> ${data.cluster.hop2}</p>
                    `;
                } else {
                    clusterInfo.innerHTML = '<p style="color: var(--text-secondary);">No wallet network data available</p>';
                }

                // Fetch and populate Jito tips history
                try {
                    const historyResponse = await fetch(`/api/creator-service-history/${creatorAddress}`);
                    const historyData = await historyResponse.json();

                    const jitotipsSection = document.getElementById('jitotipsSection');
                    const jitotipsBody = document.getElementById('jitotipsBody');

                    if (historyData.history && historyData.history.length > 0) {
                        // Filter jitotip records (both CREATE and other txs)
                        const jitotips = historyData.history.filter(h => h.tag === 'uses_jitotip' || h.tag === 'uses_jitotip_other');

                        if (jitotips.length > 0) {
                            jitotipsSection.style.display = 'block';
                            jitotipsBody.innerHTML = jitotips.map(tip => {
                                const mintDisplay = tip.mint ? tip.mint.substring(0, 16) + '...' : 'N/A';
                                const mintTitle = tip.mint ? tip.mint : '';
                                const dateStr = tip.created_at ? new Date(tip.created_at).toLocaleDateString() : 'N/A';
                                const percentageStr = tip.tip_percentage ? tip.tip_percentage.toFixed(1) + '%' : 'N/A';
                                const txSig = tip.tx_signature || '';

                                // Show actual transaction type with styling and Solscan link
                                const txType = tip.tx_type || (tip.tag === 'uses_jitotip' ? 'Create' : 'Unknown');
                                const isCreate = tip.tag === 'uses_jitotip' || txType.toLowerCase() === 'create';
                                const typeColor = isCreate ? '#4ade80' : 'var(--color-medium)';

                                let typeIndicator;
                                if (txSig) {
                                    typeIndicator = `<a href="https://solscan.io/tx/${txSig}" target="_blank" style="color: ${typeColor}; font-weight: 600; font-size: 10px; text-decoration: none; cursor: pointer;" title="View on Solscan: ${txSig}">${txType}</a>`;
                                } else {
                                    typeIndicator = `<span style="color: ${typeColor}; font-weight: 600; font-size: 10px;">${txType}</span>`;
                                }

                                return `
                                    <tr>
                                        <td title="${mintTitle}" style="font-family: monospace;">${mintDisplay}</td>
                                        <td>${tip.amount_sol ? tip.amount_sol.toFixed(6) : 'N/A'} SOL</td>
                                        <td>${percentageStr}</td>
                                        <td>${typeIndicator}</td>
                                        <td>${dateStr}</td>
                                    </tr>
                                `;
                            }).join('');
                        } else {
                            jitotipsSection.style.display = 'none';
                        }
                    } else {
                        jitotipsSection.style.display = 'none';
                    }
                } catch (error) {
                    console.error('Error loading Jito tips history:', error);
                    document.getElementById('jitotipsSection').style.display = 'none';
                }


                // Show modal
                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading creator details:', error);
                alert('Failed to load creator details');
            }
        }

        async function viewTransaction(signature) {
            // Validate signature
            if (!signature) {
                alert('No transaction signature provided');
                return;
            }

            // Show loading state
            document.getElementById('txViewerModal').style.display = 'block';
            document.getElementById('txViewerAccountKeys').textContent = 'Loading transaction...';
            document.getElementById('txViewerFeePayer').textContent = 'Fetching...';

            try {
                // Use backend endpoint to avoid CORS issues
                const response = await fetch(`/api/transaction/${signature}`, {
                    method: 'GET',
                    headers: {'Content-Type': 'application/json'}
                });
                const data = await response.json();

                // Check for API errors
                if (data.error) {
                    document.getElementById('txViewerAccountKeys').textContent = `Error: ${data.error}`;
                    return;
                }

                if (!data.account_keys) {
                    document.getElementById('txViewerAccountKeys').textContent = 'Transaction not found or no account keys';
                    return;
                }

                const accountKeys = data.account_keys;

                // Display transaction details
                document.getElementById('txViewerSig').textContent = signature;
                document.getElementById('txSolscanLink').href = `https://solscan.io/tx/${signature}`;
                document.getElementById('txViewerAccountKeys').textContent = JSON.stringify(accountKeys, null, 2);

                // Extract and highlight fee payer (first account, must be signer)
                let feePayer = '';
                let feePayerValid = false;
                if (accountKeys.length > 0) {
                    const firstKey = accountKeys[0];
                    if (typeof firstKey === 'string') {
                        feePayer = firstKey;
                        feePayerValid = true;
                    } else if (firstKey.pubkey) {
                        feePayer = firstKey.pubkey;
                        feePayerValid = firstKey.signer === true;
                    }
                }

                // Display fee payer with validation indicator
                const feePayerElement = document.getElementById('txViewerFeePayer');
                if (feePayerValid) {
                    feePayerElement.textContent = feePayer;
                    feePayerElement.parentElement.style.borderColor = 'rgba(34, 197, 94, 0.5)';
                } else {
                    feePayerElement.textContent = feePayer;
                    feePayerElement.parentElement.style.borderColor = 'rgba(239, 68, 68, 0.3)';
                }

            } catch (error) {
                console.error('Error fetching transaction:', error);
                document.getElementById('txViewerAccountKeys').textContent = `Error: ${error.message || 'Failed to fetch transaction'}`;
            }
        }

        function closeTxViewer() {
            document.getElementById('txViewerModal').style.display = 'none';
        }

        function copyToClipboard(text) {
            navigator.clipboard.writeText(text).then(() => {
                alert('Copied to clipboard!');
            }).catch(err => {
                console.error('Failed to copy:', err);
            });
        }

        async function showMultiCreatorFunders() {
            // Cancel ongoing price fetches to free up bandwidth for modal
            priceLoadController.abort();
            priceLoadController = new AbortController();

            const modal = document.getElementById('multiCreatorFundersModal');

            try {
                const response = await fetch('/api/multi-creator-funders');
                const data = await response.json();

                if (data.error) {
                    alert('Failed to load multi-creator funder analysis');
                    return;
                }

                // Populate statistics
                const suspiciousCount = data.suspicious_only ? data.suspicious_only.length : 0;
                const safeCount = data.statistics.funding_multiple_creators - suspiciousCount;

                document.getElementById('suspiciousFundersCount').textContent = suspiciousCount;
                document.getElementById('suspiciousFundersCount').style.color = suspiciousCount > 0 ? 'var(--color-critical)' : '#4ade80';

                document.getElementById('safeFundersCount').textContent = safeCount;
                document.getElementById('safeFundersCount').style.color = '#4ade80';

                document.getElementById('totalFundersCount').textContent = data.statistics.total_funders;

                // Populate funders table - filter out INFRA/CEX accounts
                const fundersBody = document.getElementById('multiCreatorFundersBody');
                if (data.multi_creator_funders && data.multi_creator_funders.length > 0) {
                    // Filter out INFRA and CEX accounts - they are safe
                    const suspiciousFunders = data.multi_creator_funders.filter(f => !f.is_infrastructure && !f.is_cex_account);

                    if (suspiciousFunders.length > 0) {
                        fundersBody.innerHTML = suspiciousFunders.map((funder, idx) => {
                            const startDate = new Date(funder.first_funding_at).toLocaleDateString();
                            const endDate = new Date(funder.last_funding_at).toLocaleDateString();
                            const period = startDate === endDate ? startDate : `${startDate} - ${endDate}`;

                            // Build network display - show all networks this funder belongs to
                            let networkDisplay = '';
                            if (funder.networks && funder.networks.length > 0) {
                                const networkNames = funder.networks.map(n => {
                                    const typeBadge = n.network_type === 'single_funder' ? ' 🎯' : '';
                                    return n.network_name + typeBadge;
                                });
                                networkDisplay = networkNames.join(', ');
                            } else {
                                networkDisplay = '';
                            }

                            return `
                                <tr style="cursor: pointer;" onclick="showFunderDetails('${funder.funder_address}')" title="Click to view funder details">
                                    <td style="font-family: monospace; font-size: 12px; color: var(--color-critical);">
                                        ${funder.funder_address}
                                    </td>
                                    <td style="color: var(--accent-cyan); font-weight: 500; font-size: 12px; white-space: nowrap;">${networkDisplay}</td>
                                    <td><strong style="color: var(--color-critical);">${funder.creator_count}</strong></td>
                                    <td>${(funder.total_sol_sent || 0).toFixed(2)} SOL</td>
                                    <td>${funder.funding_record_count}</td>
                                    <td style="font-size: 11px;">${period}</td>
                                    <td onclick="event.stopPropagation();">
                                        <button onclick="analyzeFunderTransfers('${funder.funder_address}')" style="padding: 4px 8px; font-size: 11px; background: rgba(34, 197, 94, 0.2); color: var(--color-low); border: 1px solid rgba(34, 197, 94, 0.5); border-radius: 3px; cursor: pointer; white-space: nowrap;">Analyze</button>
                                    </td>
                                </tr>
                            `;
                        }).join('');
                    } else {
                        fundersBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--color-low);">✅ All multi-creator funders are known INFRA/CEX accounts (safe)</td></tr>';
                    }
                } else {
                    fundersBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--color-low);">✅ No coordinated funders detected</td></tr>';
                }

                // Show modal
                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading multi-creator funders:', error);
                alert('Failed to load multi-creator funder analysis');
            }
        }

        function closeMultiCreatorFunders() {
            document.getElementById('multiCreatorFundersModal').style.display = 'none';
        }

        // Analyze funder transfers in background and show status
        async function analyzeFunderTransfers(funderAddress) {
            const btn = event.target;
            const originalText = btn.textContent;
            btn.textContent = 'Analyzing...';
            btn.disabled = true;
            btn.style.opacity = '0.5';

            try {
                const response = await fetch('/api/analyze-funder-transfers', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({funder_address: funderAddress})
                });

                const data = await response.json();

                if (data.status === 'queued') {
                    btn.textContent = 'Queued ✓';
                    btn.style.background = 'rgba(245, 158, 11, 0.2)';
                    btn.style.color = 'var(--color-medium)';
                    btn.style.borderColor = 'rgba(245, 158, 11, 0.5)';

                    console.log(`✅ Analysis queued for: ${funderAddress}`);

                    // Poll for results
                    let pollCount = 0;
                    const pollInterval = setInterval(async () => {
                        pollCount++;

                        // Stop polling after 30 seconds
                        if (pollCount > 30) {
                            clearInterval(pollInterval);
                            btn.textContent = originalText;
                            btn.disabled = false;
                            btn.style.opacity = '1';
                            btn.style.background = 'rgba(34, 197, 94, 0.2)';
                            btn.style.color = '#4ade80';
                            btn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
                            alert('⏱️ Analysis timed out. Try again later.');
                            return;
                        }

                        try {
                            const statusResponse = await fetch(`/api/funder-analysis-status/${funderAddress}`);
                            const statusData = await statusResponse.json();

                            if (statusData.status === 'completed') {
                                clearInterval(pollInterval);

                                // Show results (handle both naming conventions)
                                const incoming = statusData.result.incoming_found || statusData.result.incoming_count || 0;
                                const outgoing = statusData.result.outgoing_found || statusData.result.outgoing_count || 0;
                                const totalSol = (statusData.result.total_sol || 0).toFixed(2);

                                btn.textContent = `Done: ${incoming} IN / ${outgoing} OUT`;
                                btn.style.background = 'rgba(34, 197, 94, 0.3)';
                                btn.style.color = '#4ade80';
                                btn.style.borderColor = 'rgba(34, 197, 94, 0.7)';

                                alert(`✅ Analysis Complete\n\nIncoming: ${incoming}\nOutgoing: ${outgoing}\nTotal SOL: ${totalSol}`);

                                // Reset button after delay
                                setTimeout(() => {
                                    btn.textContent = originalText;
                                    btn.disabled = false;
                                    btn.style.opacity = '1';
                                    btn.style.background = 'rgba(34, 197, 94, 0.2)';
                                    btn.style.color = '#4ade80';
                                    btn.style.borderColor = 'rgba(34, 197, 94, 0.5)';
                                }, 3000);
                            }
                        } catch (e) {
                            console.error('Error checking analysis status:', e);
                        }
                    }, 1000);  // Poll every 1 second
                } else if (data.status === 'completed') {
                    const incoming = data.result.incoming_found || data.result.incoming_count || 0;
                    const outgoing = data.result.outgoing_found || data.result.outgoing_count || 0;
                    btn.textContent = `Done: ${incoming} IN / ${outgoing} OUT`;
                    btn.style.background = 'rgba(34, 197, 94, 0.3)';
                    btn.style.color = '#4ade80';
                    btn.style.borderColor = 'rgba(34, 197, 94, 0.7)';

                    // Show results
                    const msg = `✅ Analysis Complete\n\nIncoming: ${incoming}\nOutgoing: ${outgoing}\nTotal SOL: ${(data.result.total_sol || 0).toFixed(4)}`;
                    alert(msg);
                } else {
                    alert(`❌ Error: ${data.error || 'Unknown error'}`);
                    btn.textContent = originalText;
                    btn.disabled = false;
                    btn.style.opacity = '1';
                }
            } catch (error) {
                console.error('Error analyzing funder:', error);
                alert(`❌ Error analyzing funder: ${error.message}`);
                btn.textContent = originalText;
                btn.disabled = false;
                btn.style.opacity = '1';
            }
        }

        function closeCreatorDetails() {
            document.getElementById('creatorModal').style.display = 'none';
        }

        // Close modal when clicking outside
        window.onclick = function(event) {
            const metricsModal = document.getElementById('metricsModal');
            const creatorModal = document.getElementById('creatorModal');
            const txViewerModal = document.getElementById('txViewerModal');
            const multiCreatorFundersModal = document.getElementById('multiCreatorFundersModal');
            const validationModal = document.getElementById('validationModal');
            const fundingNetwork3TierModal = document.getElementById('fundingNetwork3TierModal');
            const coordinatedFunderAnalysisModal = document.getElementById('coordinatedFunderAnalysisModal');
            const funderDetailsModal = document.getElementById('funderDetailsModal');

            if (event.target === metricsModal) {
                metricsModal.style.display = 'none';
            }
            if (event.target === creatorModal) {
                creatorModal.style.display = 'none';
            }
            if (event.target === multiCreatorFundersModal) {
                multiCreatorFundersModal.style.display = 'none';
            }
            if (event.target === txViewerModal) {
                txViewerModal.style.display = 'none';
            }
            if (event.target === validationModal) {
                validationModal.style.display = 'none';
            }
            if (event.target === fundingNetwork3TierModal) {
                fundingNetwork3TierModal.style.display = 'none';
            }
            if (event.target === coordinatedFunderAnalysisModal) {
                coordinatedFunderAnalysisModal.style.display = 'none';
            }
            if (event.target === funderDetailsModal) {
                funderDetailsModal.style.display = 'none';
            }
        }

        // Close modal when pressing Escape
        document.addEventListener('keydown', function(event) {
            if (event.key === 'Escape') {
                closeTokenMetrics();
                closeCreatorDetails();
                closeMultiCreatorFunders();
                closeCoordinatedFunderAnalysis();
                closeTxViewer();
                closeValidationModal();
                closeFundingNetwork3Tier();
                closeFunderDetails();
            }
        });

        // ===== TRANSACTION VALIDATION FUNCTIONS =====

        function openValidationModal() {
            document.getElementById('validationModal').style.display = 'block';
            document.getElementById('validationInput').focus();
            document.getElementById('validationInput').value = '';
            document.getElementById('validationResults').style.display = 'none';
        }

        function closeValidationModal() {
            document.getElementById('validationModal').style.display = 'none';
        }

        // 3-Tier Funding Network Functions
        function promptFundingNetwork3Tier() {
            const creatorAddress = prompt('Enter creator address to view funding network: ' + String.fromCharCode(10) + String.fromCharCode(10) + 'Example: HYWo71Wk9PNDe5sBaRKazPnVyGnQDiwgXCFKvgAQ1ENp');
            if (creatorAddress && creatorAddress.trim().length > 0) {
                showFundingNetwork3Tier(creatorAddress.trim());
            }
        }

        async function showFundingNetwork3Tier(creatorAddress) {
            const modal = document.getElementById('fundingNetwork3TierModal');

            try {
                // Check extraction status first
                const statusResponse = await fetch(`/api/creator-funder-extraction-status/${creatorAddress}`);
                const statusData = await statusResponse.json();

                const response = await fetch(`/api/funding-network-3tier/${creatorAddress}`);
                const data = await response.json();

                if (data.error) {
                    document.getElementById('fn3tNetworkBody').innerHTML = '<div style="color: var(--color-critical);">Error loading network</div>';
                    return;
                }

                // Add extraction status indicator
                let statusIndicator = '';
                if (statusData.is_complete) {
                    statusIndicator = '<div style="color: var(--color-low); font-weight: bold; margin-bottom: 15px; font-size: 13px;">✅ Funding complete</div>';
                } else if (statusData.status === 'pending') {
                    statusIndicator = '<div style="color: var(--color-medium); font-weight: bold; margin-bottom: 15px; font-size: 13px;">⏳ Extraction in progress...</div>';
                }

                // Build 3-tier network visualization - concise version
                let networkHTML = '<div style="font-family: monospace; font-size: 12px; line-height: 2.2;">';

                data.network_tiers.forEach((tier, tierIdx) => {
                    const funderAddr = tier.funder_address;
                    const funderType = tier.funder_type || 'unknown';
                    const funderLabel = tier.funder_label;
                    const totalToCreator = tier.total_to_creator.toFixed(2);
                    const senderCount = tier.sender_count || tier.senders.length;

                    // Funder type styling
                    let funderColor = '#4ade80';  // Default green for regular
                    let funderTypeLabel = '';
                    if (funderType === 'cex') {
                        funderColor = 'var(--color-critical)';  // Red for CEX
                        funderTypeLabel = funderLabel ? ` [${funderLabel}]` : ' [CEX]';
                    } else if (funderType === 'infra') {
                        funderColor = 'var(--color-high)';  // Orange for INFRA
                        funderTypeLabel = funderLabel ? ` [${funderLabel}]` : ' [INFRA]';
                    }

                    networkHTML += `<div style="color: ${funderColor}; margin-bottom: 12px; font-family: monospace; font-size: 11px; word-break: break-all;">`;
                    networkHTML += `Funder: ${funderAddr}${funderTypeLabel}</div>`;

                    // Show "Terminal" indicator for CEX/INFRA (no sender tracing)
                    if (tier.is_terminal) {
                        networkHTML += `<div style="color: var(--color-medium); margin-left: 20px; margin-bottom: 6px; font-size: 10px; font-style: italic;">(Terminal endpoint - ${totalToCreator} SOL, not traced)</div>`;
                    }

                    if (senderCount > 0) {
                        const knownCount = tier.known_sender_count || 0;
                        const unknownCount = senderCount - knownCount;
                        let senderSummary = `← ${senderCount} senders → ${totalToCreator} SOL`;
                        if (knownCount > 0) {
                            // Count risky vs trusted identified accounts
                            const riskyCount = tier.senders.filter(s => s.risk_level === 'high').length;
                            if (riskyCount > 0) {
                                senderSummary += ` <span style="color: var(--color-critical);">(${riskyCount} risky ⚠️)</span>`;
                            }
                            const trustedCount = tier.senders.filter(s => s.risk_level === 'neutral' || s.risk_level === 'low').length;
                            if (trustedCount > 0) {
                                senderSummary += ` <span style="color: var(--color-low);">(${trustedCount} trusted ✓)</span>`;
                            }
                        }
                        networkHTML += `<div style="color: var(--color-medium); margin-left: 20px; margin-bottom: 6px;">${senderSummary}</div>`;

                        // Always show known senders (prioritized), then unknowns up to 5 total
                        if (tier.senders.length > 0) {
                            // Separate known and unknown senders
                            const knownSenders = tier.senders.filter(s => s.is_known);
                            const unknownSenders = tier.senders.filter(s => !s.is_known);

                            // Show all known senders first, then unknown senders up to fill remaining space
                            const displayCount = Math.min(5, tier.senders.length);
                            const sendersToShow = knownSenders.concat(unknownSenders).slice(0, displayCount);

                            sendersToShow.forEach((sender) => {
                                const label = sender.label;
                                const riskLevel = sender.risk_level || 'unknown';

                                // Color code by risk level
                                let senderColor = 'var(--color-medium)';  // unknown (yellow) - neutral
                                let badge = '';

                                if (riskLevel === 'high') {
                                    senderColor = 'var(--color-critical)';  // RED - suspicious (CEX hot wallets, risky accounts)
                                    badge = ' ⚠️';  // Warning emoji
                                } else if (riskLevel === 'neutral' || riskLevel === 'low') {
                                    senderColor = '#4ade80';  // GREEN - trusted infrastructure (Axiom, safe accounts)
                                    badge = ' ✓';  // Checkmark for trusted
                                } else if (riskLevel === 'medium') {
                                    senderColor = 'var(--color-medium)';  // YELLOW - moderate risk
                                    badge = '';
                                }

                                const senderAmount = sender.amount_to_funder.toFixed(2);
                                const labelText = label ? ` [${label}]` : '';
                                networkHTML += `<div style="color: ${senderColor}; margin-left: 40px; font-size: 11px; font-family: monospace; word-break: break-all;">• ${sender.sender_address}${labelText}${badge} → ${senderAmount} SOL</div>`;
                            });

                            // Show remaining count if there are more
                            if (tier.senders.length > displayCount) {
                                networkHTML += `<div style="color: var(--text-secondary); margin-left: 40px; font-size: 11px;">... and ${tier.senders.length - displayCount} more senders</div>`;
                            }
                        }
                    } else {
                        networkHTML += `<div style="color: var(--text-secondary); margin-left: 20px; margin-bottom: 6px;">→ ${totalToCreator} SOL (no tracked sources)</div>`;
                    }

                    networkHTML += `</div>`;
                });

                networkHTML += '</div>';

                // If no funders with senders were found, show helpful message
                if (networkHTML === '<div style="font-family: monospace; font-size: 12px; line-height: 2.2;"></div>') {
                    if (data.total_funders > 0) {
                        networkHTML = `<div style="color: var(--color-medium); padding: 20px; text-align: center;">
                            This creator has ${data.total_funders} funder(s) but no tracked pre-migration senders.<br>
                            <span style="color: var(--text-secondary); font-size: 11px;">Funding source data extraction pending.</span>
                        </div>`;
                    } else {
                        networkHTML = '<div style="color: var(--text-secondary); padding: 20px; text-align: center;">No funding data available for this creator.</div>';
                    }
                }

                // Prepend status indicator to the network HTML
                document.getElementById('fn3tNetworkBody').innerHTML = statusIndicator + networkHTML;
                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading 3-tier network:', error);
                document.getElementById('fn3tNetworkBody').innerHTML = '<div style="color: var(--color-critical);">Error loading network data</div>';
            }
        }

        function closeFundingNetwork3Tier() {
            document.getElementById('fundingNetwork3TierModal').style.display = 'none';
        }

        async function showCoordinatedFunderAnalysis(creatorAddress) {
            const modal = document.getElementById('coordinatedFunderAnalysisModal');

            try {
                const response = await fetch(`/api/coordinated-funder-analysis/${creatorAddress}`);
                const data = await response.json();

                if (response.status === 404) {
                    document.getElementById('cfaConnectedCreators').innerHTML =
                        '<div style="color: var(--color-medium); text-align: center; padding: 20px;">Not yet analyzed. Run Coordinated Funder Analysis first.</div>';
                    document.getElementById('cfaSharedDestinations').innerHTML = '';
                    document.getElementById('cfaRiskLevel').textContent = 'PENDING';
                    document.getElementById('cfaRiskLevel').style.color = 'var(--color-medium)';
                    document.getElementById('cfaConnectedCount').textContent = '0';
                    document.getElementById('cfaSharedDests').textContent = '0';
                    modal.style.display = 'block';
                    return;
                }

                if (data.error) {
                    alert('Error loading analysis: ' + data.error);
                    return;
                }

                // Display risk level with color coding
                let riskColor = '#4ade80';  // LOW - green
                if (data.network_risk_level === 'HIGH') riskColor = 'var(--color-medium)';  // orange
                if (data.network_risk_level === 'CRITICAL') riskColor = 'var(--color-critical)';  // red

                document.getElementById('cfaRiskLevel').textContent = data.network_risk_level || 'UNKNOWN';
                document.getElementById('cfaRiskLevel').style.color = riskColor;
                document.getElementById('cfaConnectedCount').textContent = data.connected_creators_count || 0;
                document.getElementById('cfaSharedDests').textContent = data.shared_destinations_count || 0;

                // Display connected creators
                let creatorsList = '';
                if (data.connected_creators && data.connected_creators.length > 0) {
                    data.connected_creators.forEach((cc, idx) => {
                        const riskStyle = cc.risk_level === 'CRITICAL' ? 'color: var(--color-critical);' :
                                         cc.risk_level === 'HIGH' ? 'color: var(--color-medium);' :
                                         'color: var(--color-low);';
                        creatorsList += `
                            <div style="margin-bottom: 8px; ${riskStyle}; word-break: break-all;">
                                ${idx + 1}. ${cc.creator_address}
                                <span style="font-size: 10px; color: var(--text-secondary);">
                                    [${cc.risk_level}] Rug: ${(cc.rug_probability * 100).toFixed(0)}%
                                </span>
                            </div>
                        `;
                    });
                    if (data.connected_creators_count > 10) {
                        creatorsList += `<div style="color: var(--text-secondary); font-size: 11px;">... and ${data.connected_creators_count - 10} more</div>`;
                    }
                } else {
                    creatorsList = '<div style="color: var(--text-secondary);">No connected creators found</div>';
                }
                document.getElementById('cfaConnectedCreators').innerHTML = creatorsList;

                // Display shared destinations
                let destsList = '';
                if (data.shared_destinations && data.shared_destinations.length > 0) {
                    data.shared_destinations.slice(0, 20).forEach((dest, idx) => {
                        destsList += `<div style="margin-bottom: 6px; color: var(--color-medium); font-size: 11px;">${idx + 1}. ${dest}</div>`;
                    });
                    if (data.shared_destinations_count > 20) {
                        destsList += `<div style="color: var(--text-secondary); font-size: 11px;">... and ${data.shared_destinations_count - 20} more</div>`;
                    }
                } else {
                    destsList = '<div style="color: var(--text-secondary);">No shared destinations found</div>';
                }
                document.getElementById('cfaSharedDestinations').innerHTML = destsList;

                // Display timestamp
                const detectedDate = new Date(data.detected_at).toLocaleString();
                document.getElementById('cfaDetectedAt').textContent = detectedDate;

                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading coordinated funder analysis:', error);
                alert('Failed to load analysis');
            }
        }

        function closeCoordinatedFunderAnalysis() {
            document.getElementById('coordinatedFunderAnalysisModal').style.display = 'none';
        }

        async function showFunderDetails(funderAddress) {
            const modal = document.getElementById('funderDetailsModal');
            document.getElementById('fdFunderAddr').textContent = funderAddress;

            try {
                const response = await fetch(`/api/funder-transfer-details/${funderAddress}`);
                const data = await response.json();

                if (data.error) {
                    alert('Error loading funder details: ' + data.error);
                    return;
                }

                // Update summary stats
                document.getElementById('fdCreatorCount').textContent = data.creators_funded;
                document.getElementById('fdIncomingTotal').textContent = data.incoming_transfers.total_sol.toFixed(2) + ' SOL';
                document.getElementById('fdOutgoingTotal').textContent = data.outgoing_transfers.total_sol.toFixed(2) + ' SOL';
                document.getElementById('fdNetFlow').textContent = data.net_flow.toFixed(2) + ' SOL';

                // Display incoming transfers
                let incomingHTML = '';
                if (data.incoming_transfers.senders && data.incoming_transfers.senders.length > 0) {
                    data.incoming_transfers.senders.forEach((sender) => {
                        const label = sender.label ? `[${sender.label}]` : `[${sender.category || 'Unknown'}]`;
                        const labelColor = sender.is_known ? '#4ade80' : 'var(--color-medium)';
                        const badge = sender.is_known ? ' ✓' : '';
                        incomingHTML += `
                            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                <td style="padding: 10px; color: var(--text-primary); font-family: monospace; font-size: 11px; word-break: break-all;">${sender.address}</td>
                                <td style="padding: 10px; text-align: right; color: var(--color-low);">${sender.amount_sol.toFixed(2)}</td>
                                <td style="padding: 10px; text-align: center; color: var(--text-secondary);">${sender.transaction_count}</td>
                                <td style="padding: 10px; color: ${labelColor};">${label}${badge}</td>
                            </tr>
                        `;
                    });
                } else {
                    incomingHTML = '<tr><td colspan="4" style="padding: 20px; text-align: center; color: var(--text-secondary);">No incoming transfers recorded</td></tr>';
                }
                document.getElementById('fdIncomingBody').innerHTML = incomingHTML;

                // Display outgoing transfers
                let outgoingHTML = '';
                if (data.outgoing_transfers.recipients && data.outgoing_transfers.recipients.length > 0) {
                    data.outgoing_transfers.recipients.forEach((recipient) => {
                        const label = recipient.label ? `[${recipient.label}]` : `[${recipient.category || 'Unknown'}]`;
                        const labelColor = recipient.is_known ? '#4ade80' : 'var(--color-medium)';
                        const badge = recipient.is_known ? ' ✓' : '';
                        outgoingHTML += `
                            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                <td style="padding: 10px; color: var(--text-primary); font-family: monospace; font-size: 11px; word-break: break-all;">${recipient.address}</td>
                                <td style="padding: 10px; text-align: right; color: var(--color-critical);">${recipient.amount_sol.toFixed(2)}</td>
                                <td style="padding: 10px; text-align: center; color: var(--text-secondary);">${recipient.transaction_count}</td>
                                <td style="padding: 10px; color: ${labelColor};">${label}${badge}</td>
                            </tr>
                        `;
                    });
                } else {
                    outgoingHTML = '<tr><td colspan="4" style="padding: 20px; text-align: center; color: var(--text-secondary);">No outgoing transfers recorded</td></tr>';
                }
                document.getElementById('fdOutgoingBody').innerHTML = outgoingHTML;

                modal.style.display = 'block';

            } catch (error) {
                console.error('Error loading funder details:', error);
                alert('Failed to load funder details');
            }
        }

        function closeFunderDetails() {
            document.getElementById('funderDetailsModal').style.display = 'none';
        }

        async function validateTransaction() {
            const sig = document.getElementById('validationInput').value.trim();

            if (!sig) {
                alert('Please enter a transaction signature');
                return;
            }

            // Show loading state
            document.getElementById('validationResults').style.display = 'block';
            document.getElementById('validationLoading').style.display = 'block';
            document.getElementById('validationSuccess').style.display = 'none';
            document.getElementById('validationError').style.display = 'none';

            try {
                const response = await fetch('/api/validate-transaction', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ signature: sig })
                });

                const result = await response.json();

                // Hide loading
                document.getElementById('validationLoading').style.display = 'none';

                if (result.error) {
                    // Show error
                    document.getElementById('validationError').style.display = 'block';
                    document.getElementById('errorMessage').textContent = result.error;
                } else {
                    // Show success
                    document.getElementById('validationSuccess').style.display = 'block';

                    // Populate results
                    document.getElementById('resultMint').textContent = result.mint || '';
                    document.getElementById('resultCreator').textContent = result.creator || '';
                    document.getElementById('resultTimestamp').textContent = result.timestamp || '';

                    // Build evidence list
                    const evidence = [];
                    if (result.has_system_create) evidence.push('✅ System.createAccount (5 instances)');
                    if (result.has_init_mint) evidence.push('✅ initializeMint2 instruction');
                    if (result.pump_program) evidence.push('✅ Pump.fun program involved');
                    if (result.confirmed) evidence.push('✅ Confirmed on-chain');
                    evidence.push(`✅ Instructions: ${result.instruction_count} top-level + ${result.inner_instruction_count} inner`);

                    document.getElementById('resultEvidence').innerHTML = evidence.join('<br>');

                    // Set Solscan link
                    document.getElementById('resultSolscanLink').href = `https://solscan.io/tx/${sig}`;
                }
            } catch (error) {
                console.error('Validation error:', error);
                document.getElementById('validationLoading').style.display = 'none';
                document.getElementById('validationError').style.display = 'block';
                document.getElementById('errorMessage').textContent = `Network error: ${error.message}`;
            }
        }

        // Allow Enter key to validate
        document.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && document.getElementById('validationModal').style.display === 'block') {
                validateTransaction();
            }
        });

        // Cluster details functions removed - use dedicated dashboards instead
        async function loadFunderClustersInNetworkView() {
            const containerEl = document.getElementById('funder-clusters-container');
            if (!containerEl) return;

            try {
                const response = await fetch('/api/funder-clusters');
                const data = await response.json();

                if (data.error) {
                    containerEl.innerHTML = '<div style="text-align: center; padding: 30px; color: var(--color-critical);">Error loading funder clusters: ' + data.error + '</div>';
                    return;
                }

                const clusters = data.clusters || [];

                // Update statistics
                document.getElementById('fcTotalCount').textContent = clusters.length;
                document.getElementById('fcTotalFunders').textContent = clusters.reduce((sum, c) => sum + (c.funder_count || 0), 0);
                document.getElementById('fcTotalVolume').textContent = '$' + (data.total_volume_sol || 0).toFixed(2);
                document.getElementById('fcTotalCreators').textContent = clusters.reduce((sum, c) => sum + (c.creator_count || 0), 0);

                // Render cluster cards
                let html = '';
                clusters.forEach((cluster, index) => {
                    const riskColor = cluster.risk_level === 'CRITICAL' ? 'var(--color-critical)' :
                                     cluster.risk_level === 'HIGH' ? 'var(--color-high)' :
                                     cluster.risk_level === 'MEDIUM' ? 'var(--color-medium)' : 'var(--color-low)';

                    const riskIcon = cluster.risk_level === 'CRITICAL' ? '🚨' :
                                    cluster.risk_level === 'HIGH' ? '⚠️' :
                                    cluster.risk_level === 'MEDIUM' ? '🟡' : '✅';

                    // Cluster names mapping (v2.2: CEX-exclusive clusters)
                    const clusterNames = {
                        'FUNDERS_14': 'NexusCerberus',
                        'FUNDERS_20': 'CrimsonRaven',
                        'FUNDERS_17': 'StellarDragon',
                        'FUNDERS_6': 'IvoryWarden',
                        'FUNDERS_10': 'OnyxRaven',
                        'FUNDERS_8': 'SilentViper',
                        'FUNDERS_16': 'PhantomWolf',
                        'FUNDERS_9': 'EtherealEagle',
                        'FUNDERS_1': 'CosmicLion',
                        'FUNDERS_11': 'PhoenixAscend',
                        'FUNDERS_13': 'ShadowNova',
                        'FUNDERS_2': 'VortexMind',
                        'FUNDERS_3': 'IceShield',
                        'FUNDERS_4': 'StormBringer',
                        'FUNDERS_5': 'NightHunter',
                        'FUNDERS_7': 'FrostByte',
                        'FUNDERS_12': 'VortexFlow',
                        'FUNDERS_15': 'IceVenom',
                        'FUNDERS_18': 'ShadowBolt',
                        'FUNDERS_19': 'VortexKing',
                    };

                    const clusterName = clusterNames[cluster.cluster_id] || cluster.cluster_id;

                    html += `
                        <div style="background: rgba(124, 58, 237, 0.05); border: 1px solid rgba(124, 58, 237, 0.2); border-radius: 8px; padding: 20px; margin-bottom: 20px;">
                            <!-- Cluster Header -->
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                                <div style="flex: 1;">
                                    <h3 style="margin: 0; color: var(--text-primary); font-size: 18px;">${clusterName}</h3>
                                    <div style="color: var(--text-secondary); font-size: 13px; margin-top: 5px;">${cluster.risk_label}</div>
                                </div>
                                <div style="text-align: right;">
                                    <div style="font-size: 24px;">${riskIcon}</div>
                                    <div style="color: ${riskColor}; font-weight: bold; font-size: 16px;">${cluster.risk_multiplier.toFixed(1)}x</div>
                                </div>
                            </div>

                            <!-- Stats Grid -->
                            <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 15px;">
                                <div style="background: var(--bg-secondary); padding: 12px; border-radius: 6px;">
                                    <div style="color: var(--text-secondary); font-size: 12px; margin-bottom: 5px;">Funders</div>
                                    <div style="font-weight: bold; font-size: 18px; color: var(--color-primary);">${cluster.funder_count}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 12px; border-radius: 6px;">
                                    <div style="color: var(--text-secondary); font-size: 12px; margin-bottom: 5px;">Creators</div>
                                    <div style="font-weight: bold; font-size: 18px; color: var(--color-primary);">${cluster.creator_count}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 12px; border-radius: 6px;">
                                    <div style="color: var(--text-secondary); font-size: 12px; margin-bottom: 5px;">Volume (SOL)</div>
                                    <div style="font-weight: bold; font-size: 18px; color: var(--color-primary);">${cluster.total_volume_sol.toFixed(2)}</div>
                                </div>
                                <div style="background: var(--bg-secondary); padding: 12px; border-radius: 6px;">
                                    <div style="color: var(--text-secondary); font-size: 12px; margin-bottom: 5px;">Network Size</div>
                                    <div style="font-weight: bold; font-size: 18px; color: var(--color-primary);">${cluster.network_size || cluster.funder_count}</div>
                                </div>
                            </div>

                            <!-- Load Full Details Button -->
                            <button onclick="loadClusterFullDetails('${cluster.cluster_id}', this)" style="width: 100%; padding: 10px; background: rgba(124, 58, 237, 0.2); border: 1px solid rgba(124, 58, 237, 0.3); border-radius: 6px; color: var(--color-primary); font-weight: bold; cursor: pointer; transition: all 0.3s ease;">
                                📋 View Funders & Creators
                            </button>

                            <!-- Details Section (Hidden by default) -->
                            <div id="cluster-details-${cluster.cluster_id}" style="display: none; margin-top: 15px; padding-top: 15px; border-top: 1px solid rgba(124, 58, 237, 0.2);">
                                <div id="cluster-details-content-${cluster.cluster_id}"></div>
                            </div>
                        </div>
                    `;
                });

                containerEl.innerHTML = html || '<div style="text-align: center; padding: 30px; color: var(--text-secondary);">No clusters found</div>';

            } catch(e) {
                console.error('Error loading funder clusters:', e);
                containerEl.innerHTML = '<div style="text-align: center; padding: 30px; color: var(--color-critical);">Error: ' + e.message + '</div>';
            }
        }

        // Load full cluster details when button is clicked
        async function loadClusterFullDetails(clusterId, buttonEl) {
            const detailsEl = document.getElementById(`cluster-details-${clusterId}`);
            const contentEl = document.getElementById(`cluster-details-content-${clusterId}`);

            if (!detailsEl || !contentEl) return;

            // Toggle visibility
            if (detailsEl.style.display !== 'none') {
                detailsEl.style.display = 'none';
                buttonEl.innerHTML = '📋 View Funders & Creators';
                return;
            }

            // Show loading state
            detailsEl.style.display = 'block';
            contentEl.innerHTML = '<div style="text-align: center; padding: 20px; color: var(--text-secondary);">Loading details...</div>';
            buttonEl.innerHTML = '⏳ Loading...';

            try {
                const response = await fetch(`/api/funder-cluster/${clusterId}`);
                const data = await response.json();

                if (data.error) {
                    contentEl.innerHTML = `<div style="color: var(--color-critical);">Error: ${data.error}</div>`;
                    buttonEl.innerHTML = '📋 View Funders & Creators';
                    return;
                }

                // Render funders and creators in two columns
                let detailsHtml = `
                    <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                        <!-- Funders Column -->
                        <div>
                            <h4 style="margin: 0 0 10px 0; color: var(--text-primary);">Funders (${data.funder_count})</h4>
                            <div style="background: var(--bg-secondary); border-radius: 6px; max-height: 300px; overflow-y: auto; padding: 10px;">
                                ${data.funders && data.funders.length > 0 ? `
                                    <div style="font-size: 12px; line-height: 1.6;">
                                        ${data.funders.map((f, i) => `
                                            <div style="padding: 6px; border-bottom: 1px solid var(--border-color); font-family: monospace; color: var(--text-secondary); word-break: break-all;">
                                                ${i + 1}. ${f.funder_address}
                                            </div>
                                        `).join('')}
                                    </div>
                                ` : '<div style="color: var(--text-secondary); padding: 10px;">No funders found</div>'}
                            </div>
                        </div>

                        <!-- Creators Column -->
                        <div>
                            <h4 style="margin: 0 0 10px 0; color: var(--text-primary);">Creators (${data.creator_count})</h4>
                            <div style="background: var(--bg-secondary); border-radius: 6px; max-height: 300px; overflow-y: auto; padding: 10px;">
                                ${data.creators && data.creators.length > 0 ? `
                                    <div style="font-size: 12px; line-height: 1.6;">
                                        ${data.creators.slice(0, 50).map((c, i) => `
                                            <div style="padding: 6px; border-bottom: 1px solid var(--border-color); font-family: monospace; color: var(--text-secondary); word-break: break-all;">
                                                ${i + 1}. ${c}
                                            </div>
                                        `).join('')}
                                        ${data.creators.length > 50 ? `<div style="padding: 10px; color: var(--text-secondary); text-align: center;">+ ${data.creators.length - 50} more creators...</div>` : ''}
                                    </div>
                                ` : '<div style="color: var(--text-secondary); padding: 10px;">No creators found</div>'}
                            </div>
                        </div>
                    </div>
                `;

                contentEl.innerHTML = detailsHtml;
                buttonEl.innerHTML = '⬆️ Hide Funders & Creators';

            } catch (error) {
                console.error('Error loading cluster details:', error);
                contentEl.innerHTML = `<div style="color: var(--color-critical);">Error: ${error.message}</div>`;
                buttonEl.innerHTML = '📋 View Funders & Creators';
            }
        }

        // Cluster Details Modal Functions
        async function showClusterDetails(clusterId) {
            const modal = document.getElementById('clusterDetailsModal');
            if (!modal) {
                console.error('Cluster details modal not found');
                return;
            }

            modal.style.display = 'flex';
            const detailsEl = document.getElementById('clusterDetailsContent');
            detailsEl.innerHTML = '<div style="text-align: center; padding: 40px; color: var(--text-secondary);">Loading cluster details...</div>';

            try {
                const response = await fetch(`/api/funder-cluster/${clusterId}`);
                const data = await response.json();

                if (data.error) {
                    detailsEl.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--color-critical);">Error: ${data.error}</div>`;
                    return;
                }

                const riskColor = data.risk_level === 'CRITICAL' ? 'var(--color-critical)' :
                                 data.risk_level === 'HIGH' ? 'var(--color-high)' :
                                 data.risk_level === 'MEDIUM' ? 'var(--color-medium)' : 'var(--color-low)';

                let html = `
                    <div style="background: rgba(124, 58, 237, 0.05); border-radius: 8px; padding: 20px; margin-bottom: 20px;">
                        <div style="display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 15px;">
                            <div>
                                <div style="color: var(--text-secondary); font-size: 12px; margin-bottom: 5px;">Cluster ID</div>
                                <div style="font-weight: bold; font-size: 18px;">${data.cluster_id}</div>
                            </div>
                            <div>
                                <div style="color: var(--text-secondary); font-size: 12px; margin-bottom: 5px;">Total Funders</div>
                                <div style="font-weight: bold; font-size: 18px;">${data.funder_count}</div>
                            </div>
                            <div>
                                <div style="color: var(--text-secondary); font-size: 12px; margin-bottom: 5px;">Total Creators</div>
                                <div style="font-weight: bold; font-size: 18px;">${data.creator_count}</div>
                            </div>
                            <div>
                                <div style="color: var(--text-secondary); font-size: 12px; margin-bottom: 5px;">Total Volume (SOL)</div>
                                <div style="font-weight: bold; font-size: 18px;">${data.total_volume_sol.toFixed(2)}</div>
                            </div>
                        </div>
                    </div>

                    <div style="background: rgba(124, 58, 237, 0.05); border-radius: 8px; padding: 20px; margin-bottom: 20px;">
                        <div style="margin-bottom: 10px;">
                            <span style="font-weight: bold;">Risk Level: </span>
                            <span style="color: ${riskColor}; font-weight: bold; font-size: 16px;">${data.risk_label}</span>
                        </div>
                        <div>
                            <span style="font-weight: bold;">Risk Multiplier: </span>
                            <span style="color: ${riskColor}; font-weight: bold; font-size: 16px;">${data.risk_multiplier}x</span>
                        </div>
                    </div>

                    <div style="margin-bottom: 20px;">
                        <h3 style="margin: 0 0 15px 0; color: var(--text-primary);">Funders (${data.funder_count})</h3>
                        <div style="background: var(--bg-secondary); border-radius: 8px; max-height: 300px; overflow-y: auto; padding: 12px;">
                            ${data.funders.length > 0 ? `
                                <table style="width: 100%; font-size: 13px; border-collapse: collapse;">
                                    <tbody>
                                        ${data.funders.map((f, i) => `
                                            <tr style="border-bottom: 1px solid var(--border-color); padding: 8px 0;">
                                                <td style="padding: 8px; word-break: break-all; font-family: monospace; color: var(--text-secondary);">${f.funder_address}</td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            ` : '<div style="color: var(--text-secondary);">No funders found</div>'}
                        </div>
                    </div>

                    <div style="margin-bottom: 20px;">
                        <h3 style="margin: 0 0 15px 0; color: var(--text-primary);">Creators (${data.creator_count})</h3>
                        <div style="background: var(--bg-secondary); border-radius: 8px; max-height: 300px; overflow-y: auto; padding: 12px;">
                            ${data.creators && data.creators.length > 0 ? `
                                <table style="width: 100%; font-size: 13px; border-collapse: collapse;">
                                    <tbody>
                                        ${data.creators.map((c, i) => `
                                            <tr style="border-bottom: 1px solid var(--border-color); padding: 8px 0;">
                                                <td style="padding: 8px; word-break: break-all; font-family: monospace; color: var(--text-secondary);">${c}</td>
                                            </tr>
                                        `).join('')}
                                    </tbody>
                                </table>
                            ` : '<div style="color: var(--text-secondary);">No creators found</div>'}
                        </div>
                    </div>
                `;

                detailsEl.innerHTML = html;

            } catch (error) {
                console.error('Error loading cluster details:', error);
                detailsEl.innerHTML = `<div style="text-align: center; padding: 40px; color: var(--color-critical);">Error: ${error.message}</div>`;
            }
        }

        function closeClusterDetails() {
            const modal = document.getElementById('clusterDetailsModal');
            if (modal) {
                modal.style.display = 'none';
            }
        }

        // Coordinator data storage
        let allCoordinatorsData = [];
        let showCoordinatorsCexInfra = false;  // Toggle for CEX/INFRA display

        // Load cross-funder coordinators
        async function loadCoordinators() {
            try {
                const response = await fetch('/api/network-coordinators');
                const data = await response.json();

                if (data.error) {
                    document.getElementById('coordinatorsList').innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 20px; color: var(--color-critical);">Error loading coordinators: ' + data.error + '</td></tr>';
                    return;
                }

                allCoordinatorsData = data.coordinators || [];

                // Update statistics
                const highCount = allCoordinatorsData.filter(c => c.confidence === 'high').length;
                const mediumCount = allCoordinatorsData.filter(c => c.confidence === 'medium').length;
                const lowCount = allCoordinatorsData.filter(c => c.confidence === 'low').length;

                document.getElementById('coordTotalCount').textContent = allCoordinatorsData.length;
                document.getElementById('coordHighCount').textContent = highCount;
                document.getElementById('coordMediumCount').textContent = mediumCount;
                document.getElementById('coordLowCount').textContent = lowCount;

                filterCoordinators();

            } catch(e) {
                console.error('Error loading coordinators:', e);
                document.getElementById('coordinatorsList').innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 20px; color: var(--color-critical);">Error: ' + e.message + '</td></tr>';
            }
        }

        // Toggle CEX/INFRA filter for coordinators
        function toggleCoordinatorCexInfraFilter() {
            showCoordinatorsCexInfra = !showCoordinatorsCexInfra;
            const btn = document.getElementById('coordToggleCexInfra');
            if (showCoordinatorsCexInfra) {
                btn.textContent = '✓ Show All';
                btn.style.background = 'rgba(124, 58, 237, 0.2)';
            } else {
                btn.textContent = '✓ Hide CEX/INFRA';
                btn.style.background = 'rgba(124, 58, 237, 0.1)';
            }
            filterCoordinators();
        }

        // Filter and render coordinators
        function filterCoordinators() {
            const confidenceFilter = document.getElementById('coordinatorConfidenceFilter')?.value || '';
            const reachFilter = document.getElementById('coordinatorReachFilter')?.value || '';

            let filtered = allCoordinatorsData;

            // Apply CEX/INFRA filter
            if (!showCoordinatorsCexInfra) {
                filtered = filtered.filter(c => !c.is_cex);
            }

            if (confidenceFilter) {
                filtered = filtered.filter(c => c.confidence === confidenceFilter);
            }

            if (reachFilter) {
                if (reachFilter === 'mega') {
                    filtered = filtered.filter(c => c.creator_count >= 50);
                } else if (reachFilter === 'large') {
                    filtered = filtered.filter(c => c.creator_count >= 20 && c.creator_count < 50);
                } else if (reachFilter === 'organized') {
                    filtered = filtered.filter(c => c.creator_count >= 6 && c.creator_count < 20);
                } else if (reachFilter === 'small') {
                    filtered = filtered.filter(c => c.creator_count >= 2 && c.creator_count < 6);
                } else if (reachFilter === 'single') {
                    filtered = filtered.filter(c => c.creator_count === 1);
                }
            }

            renderCoordinators(filtered);
        }

        // Render coordinators table
        function renderCoordinators(coordinators) {
            const tableBody = document.getElementById('coordinatorsList');

            if (coordinators.length === 0) {
                tableBody.innerHTML = '<tr><td colspan="5" style="text-align: center; padding: 30px; color: var(--text-secondary);">No coordinators match the filter</td></tr>';
                return;
            }

            let html = '';
            coordinators.forEach(coord => {
                const confidence = coord.confidence || 'unknown';
                const confidenceColor = confidence === 'high' ? 'var(--color-high)' :
                                       confidence === 'medium' ? 'var(--color-medium)' :
                                       'var(--text-secondary)';

                const creatorCount = coord.creator_count || 0;
                let reachTier = '';
                if (creatorCount >= 50) reachTier = '🔴 MEGA';
                else if (creatorCount >= 20) reachTier = '🟠 LARGE';
                else if (creatorCount >= 10) reachTier = '🟡 MEDIUM';
                else if (creatorCount >= 6) reachTier = '🟢 ORGANIZED';
                else if (creatorCount >= 2) reachTier = '⚪ DUAL';
                else reachTier = '⚫ SINGLE';

                const solMoved = (coord.total_sol || 0).toFixed(2);

                html += `
                    <tr style="border-bottom: 1px solid rgba(124, 58, 237, 0.1);">
                        <td style="padding: 12px; word-break: break-all;">
                            <code style="background: rgba(124, 58, 237, 0.1); padding: 4px 8px; border-radius: 3px; font-size: 11px; display: block;">
                                ${coord.address}
                            </code>
                        </td>
                        <td style="text-align: center; padding: 12px;">
                            <strong>${reachTier}</strong><br>
                            <span style="color: var(--text-secondary); font-size: 11px;">${creatorCount} creators</span>
                        </td>
                        <td style="text-align: center; padding: 12px;">
                            <strong style="color: var(--primary);">${solMoved} SOL</strong>
                        </td>
                        <td style="text-align: center; padding: 12px;">
                            <span style="background: ${confidenceColor}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 10px; font-weight: bold; text-transform: uppercase;">
                                ${confidence}
                            </span>
                        </td>
                        <td style="text-align: center; padding: 12px;">
                            <button onclick="showCoordinatorDetails('${coord.address}', ${creatorCount})" style="padding: 4px 10px; border-radius: 4px; border: 1px solid var(--primary); background: rgba(124, 58, 237, 0.1); color: var(--primary); font-size: 10px; font-weight: bold; cursor: pointer; transition: all 0.2s;" title="View creators funded by this coordinator">
                                View
                            </button>
                        </td>
                    </tr>
                `;
            });

            tableBody.innerHTML = html;
        }

        // Show coordinator details
        function showCoordinatorDetails(coordAddress, creatorCount) {
            const coordinator = allCoordinatorsData.find(c => c.address === coordAddress);
            if (!coordinator) return;

            const creators = coordinator.creators || [];
            const flags = coordinator.flags || [];

            let modalHtml = `
                <div style="position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0, 0, 0, 0.7); display: flex; align-items: center; justify-content: center; z-index: 10000; padding: 20px;">
                    <div style="background: var(--bg-primary); border: 1px solid rgba(124, 58, 237, 0.3); border-radius: 12px; max-width: 800px; width: 100%; max-height: 80vh; overflow-y: auto; padding: 30px;">
                        <div style="display: flex; justify-content: space-between; align-items: start; margin-bottom: 20px;">
                            <div>
                                <h2 style="color: var(--color-critical); margin: 0 0 10px 0;">🎭 Coordinator Details</h2>
                                <code style="background: rgba(124, 58, 237, 0.1); padding: 4px 8px; border-radius: 4px; font-size: 12px; word-break: break-all;">
                                    ${coordAddress}
                                </code>
                            </div>
                            <button onclick="this.closest('div[style*=\\"position: fixed\\"]').remove()" style="background: rgba(124, 58, 237, 0.2); border: 1px solid rgba(124, 58, 237, 0.5); color: var(--primary); width: 30px; height: 30px; border-radius: 50%; cursor: pointer; font-weight: bold; font-size: 18px;">×</button>
                        </div>

                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 25px;">
                            <div style="background: rgba(124, 58, 237, 0.1); padding: 15px; border-radius: 6px; border-left: 3px solid var(--primary);">
                                <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 6px;">Total Creators</div>
                                <div style="font-size: 28px; font-weight: bold; color: var(--primary);">${creatorCount}</div>
                            </div>
                            <div style="background: rgba(124, 58, 237, 0.1); padding: 15px; border-radius: 6px; border-left: 3px solid var(--primary);">
                                <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 6px;">Total SOL</div>
                                <div style="font-size: 28px; font-weight: bold; color: var(--primary);">${(coordinator.total_sol || 0).toFixed(2)}</div>
                            </div>
                            <div style="background: rgba(124, 58, 237, 0.1); padding: 15px; border-radius: 6px; border-left: 3px solid var(--primary);">
                                <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 6px;">Confidence</div>
                                <div style="font-size: 20px; font-weight: bold; color: ${coordinator.confidence === 'high' ? 'var(--color-high)' : coordinator.confidence === 'medium' ? 'var(--color-medium)' : 'var(--text-secondary)'};text-transform: uppercase;">
                                    ${coordinator.confidence}
                                </div>
                            </div>
                        </div>

                        ${flags.length > 0 ? `
                            <div style="margin-bottom: 25px;">
                                <h3 style="color: var(--accent-cyan); margin: 0 0 12px 0;">🚩 Suspicious Flags</h3>
                                <div style="display: flex; flex-wrap: wrap; gap: 8px;">
                                    ${flags.map(flag => `
                                        <span style="background: var(--color-critical); color: white; padding: 6px 12px; border-radius: 4px; font-size: 11px; font-weight: bold;">
                                            ${flag}
                                        </span>
                                    `).join('')}
                                </div>
                            </div>
                        ` : ''}

                        <div>
                            <h3 style="color: var(--accent-cyan); margin: 0 0 12px 0;">👥 Funded Creators (${creators.length})</h3>
                            <div style="display: grid; gap: 8px; max-height: 300px; overflow-y: auto;">
                                ${creators.map(creator => `
                                    <div style="background: rgba(124, 58, 237, 0.05); padding: 10px; border-radius: 4px; border-left: 2px solid var(--primary); cursor: pointer; transition: all 0.2s;"
                                         onclick="document.querySelector('input[placeholder*=\\"Search\\"]').value = '${creator}'; searchTokens();">
                                        <code style="font-size: 11px; word-break: break-all; color: var(--primary);">
                                            ${creator}
                                        </code>
                                        <div style="font-size: 10px; color: var(--text-secondary); margin-top: 4px;">Click to search tokens by this creator</div>
                                    </div>
                                `).join('')}
                            </div>
                        </div>

                        <div style="text-align: right; margin-top: 20px;">
                            <button onclick="this.closest('div[style*=\\"position: fixed\\"]').remove()" style="padding: 8px 16px; border-radius: 6px; border: 1px solid var(--primary); background: rgba(124, 58, 237, 0.1); color: var(--primary); font-size: 12px; font-weight: bold; cursor: pointer;">
                                Close
                            </button>
                        </div>
                    </div>
                </div>
            `;

            document.body.insertAdjacentHTML('beforeend', modalHtml);
        }

        // Real-time polling for network updates
        let networkPollingInterval = null;
        let lastNetworkDataHash = null;

        function startNetworkPolling() {
            // Only poll if not already polling
            if (networkPollingInterval) return;

            console.log('[Networks] Starting real-time polling');

            networkPollingInterval = setInterval(async () => {
                try {
                    const response = await fetch('/api/funding-networks-list');
                    const data = await response.json();

                    if (!data.error && data.networks) {
                        // Create a hash of the current data to detect changes
                        const currentHash = JSON.stringify(data.networks);

                        if (currentHash !== lastNetworkDataHash) {
                            console.log('[Networks] Changes detected, refreshing display');
                            lastNetworkDataHash = currentHash;

                            // Refresh the grid if we're on the main networks view
                            const gridEl = document.getElementById('funding-networks-grid');

                            if (gridEl && !gridEl.innerHTML.includes('Network Details')) {
                                // We're on the main networks list, refresh it
                                await loadFundingNetworks();
                            }
                        }
                    }
                } catch(e) {
                    console.error('[Networks] Polling error:', e);
                }
            }, 10000); // Poll every 10 seconds
        }

        function stopNetworkPolling() {
            if (networkPollingInterval) {
                console.log('[Networks] Stopping polling');
                clearInterval(networkPollingInterval);
                networkPollingInterval = null;
                lastNetworkDataHash = null;
            }
        }

        // =====================================================================
        // SUPER-CLUSTER FUNCTIONS
        // =====================================================================

        // Track CEX/INFRA visibility state
        let showCexInfra = true;
        let showNetworksWithCexInfra = true;

        function toggleNetworksVisibility() {
            showNetworksWithCexInfra = !showNetworksWithCexInfra;
            const button = document.getElementById('scNetworksToggleCexInfra');

            if (showNetworksWithCexInfra) {
                button.textContent = '✓ Show All';
                button.style.background = 'rgba(124, 58, 237, 0.1)';
                button.style.color = 'var(--primary)';
                button.style.borderColor = 'rgba(124, 58, 237, 0.5)';
            } else {
                button.textContent = '✗ Hide CEX/INFRA';
                button.style.background = 'rgba(239, 68, 68, 0.1)';
                button.style.color = 'var(--color-critical)';
                button.style.borderColor = 'rgba(239, 68, 68, 0.5)';
            }

            // Re-render networks with updated visibility
            if (currentSuperClusterData) {
                renderNetworks(currentSuperClusterData);
            }
        }

        function toggleCexInfraView() {
            showCexInfra = !showCexInfra;
            const button = document.getElementById('scToggleCexInfra');

            if (showCexInfra) {
                button.textContent = '✓ Show CEX/INFRA';
                button.style.background = 'rgba(124, 58, 237, 0.1)';
                button.style.color = 'var(--primary)';
                button.style.borderColor = 'rgba(124, 58, 237, 0.5)';
            } else {
                button.textContent = '✗ Hide CEX/INFRA';
                button.style.background = 'rgba(239, 68, 68, 0.1)';
                button.style.color = 'var(--color-critical)';
                button.style.borderColor = 'rgba(239, 68, 68, 0.5)';
            }

            // Re-render the root operators with updated visibility
            if (currentSuperClusterData) {
                renderRootOperators(currentSuperClusterData);
                renderRelationshipDiagram(currentSuperClusterData);
                renderNetworks(currentSuperClusterData);
            }
        }

        function renderNetworks(data) {
            const networksContainer = document.getElementById('scNetworksList');
            const toggleButton = document.getElementById('scNetworksToggleCexInfra');

            // Update toggle button state
            if (toggleButton) {
                if (showNetworksWithCexInfra) {
                    toggleButton.textContent = '✓ Show All';
                    toggleButton.style.background = 'rgba(124, 58, 237, 0.1)';
                    toggleButton.style.color = 'var(--primary)';
                    toggleButton.style.borderColor = 'rgba(124, 58, 237, 0.5)';
                } else {
                    toggleButton.textContent = '✗ Hide CEX/INFRA';
                    toggleButton.style.background = 'rgba(239, 68, 68, 0.1)';
                    toggleButton.style.color = 'var(--color-critical)';
                    toggleButton.style.borderColor = 'rgba(239, 68, 68, 0.5)';
                }
            }

            // Filter networks based on the Networks tab toggle (not the header toggle)
            let visibleNetworks = data.networks;
            if (!showNetworksWithCexInfra && data.network_root_operator_status) {
                visibleNetworks = data.networks.filter(net => {
                    // Check status by both network_id as string and number
                    const hasInfraOrCex = data.network_root_operator_status[net.network_id] || data.network_root_operator_status[String(net.network_id)];
                    // When toggle is OFF (showNetworksWithCexInfra=false), only show networks without CEX/INFRA
                    return !hasInfraOrCex;
                });
            }

            if (visibleNetworks.length === 0) {
                networksContainer.innerHTML = '<tr><td colspan="4" style="padding: 20px; text-align: center; color: var(--text-secondary);">No networks found</td></tr>';
                return;
            }

            networksContainer.innerHTML = visibleNetworks.map(net => {
                const networkName = net.network_name || `Network_${net.network_id}`;
                const isCexInfra = data.network_root_operator_status && (data.network_root_operator_status[net.network_id] || data.network_root_operator_status[String(net.network_id)]);
                const statusBadge = isCexInfra ?
                    '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(239, 68, 68, 0.1); color: var(--color-critical); font-size: 10px; font-weight: bold;">CEX/INFRA</span>' :
                    '<span style="padding: 4px 8px; border-radius: 4px; background: rgba(16, 185, 129, 0.1); color: var(--color-low); font-size: 10px; font-weight: bold;">CLEAN</span>';

                return `
                    <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                        <td style="padding: 10px; color: var(--text-primary);">
                            <a href="#" onclick="showNetworkDetails(${net.network_id}); return false;"
                               style="color: var(--accent-cyan); text-decoration: none; cursor: pointer; font-weight: 500;">
                                ${networkName}
                            </a>
                        </td>
                        <td style="padding: 10px; text-align: right; color: var(--text-secondary);">${net.total_members}</td>
                        <td style="padding: 10px; text-align: right; color: var(--accent-purple); font-weight: bold;">${net.total_sol.toFixed(2)}</td>
                        <td style="padding: 10px; text-align: center;">${statusBadge}</td>
                    </tr>
                `;
            }).join('');
        }

        function renderRootOperators(data) {
            const rootsContainer = document.getElementById('scRootAddresses');
            const flowsByOperator = {};

            if (data.root_operator_flows && data.root_operator_flows.length > 0) {
                data.root_operator_flows.forEach(flow => {
                    flowsByOperator[flow.root_operator] = flow;
                });
            }

            // Filter root addresses based on toggle
            const root_addresses = data.root_addresses.filter(addr => {
                if (!showCexInfra) {
                    // Hide CEX and INFRA
                    return !addr.includes('(CEX)') && !addr.includes('(INFRA)');
                }
                return true;
            });

            rootsContainer.innerHTML = root_addresses.map((addr, idx) => {
                const flow = flowsByOperator[addr];
                let flowsHTML = '';

                if (flow && flow.example_flows && flow.example_flows.length > 0) {
                    flowsHTML = flow.example_flows.map((ex) => {
                        let flowHTML = '<div style="font-family: monospace; font-size: 11px; color: var(--text-primary); padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); line-height: 1.6; background: var(--bg-secondary); border-radius: 3px; margin-bottom: 6px;">';

                        if (ex.sender) {
                            flowHTML += '<div style="color: var(--color-none); margin-bottom: 6px;"><strong>📤 Sender:</strong></div>' +
                            '<div style="font-size: 12px; font-weight: bold; color: var(--color-none); word-break: break-all; padding: 6px; background: rgba(96, 165, 250, 0.1); border-radius: 2px; margin-bottom: 8px;">' + ex.sender + '</div>' +
                            '<div style="color: var(--text-secondary); margin-left: 8px; font-size: 10px; margin-bottom: 6px;">⬇ to Funder</div>';
                        }

                        flowHTML += '<div style="color: var(--primary); margin-bottom: 6px;"><strong>💰 Root Op:</strong></div>' +
                        '<div style="font-size: 12px; font-weight: bold; color: var(--primary); word-break: break-all; padding: 6px; background: rgba(129, 140, 248, 0.1); border-radius: 2px; margin-bottom: 8px;">' + ex.funder + '</div>' +
                        '<div style="color: var(--text-secondary); margin-left: 8px; font-size: 10px; margin-bottom: 6px;">⬇ ' + ex.sol_to_creator.toFixed(2) + ' SOL funds Creator</div>';

                        flowHTML += '<div style="color: var(--color-high); margin-bottom: 6px;"><strong>👤 Creator:</strong></div>' +
                        '<div style="font-size: 12px; font-weight: bold; color: var(--color-medium); word-break: break-all; padding: 6px; background: rgba(251, 191, 36, 0.1); border-radius: 2px; margin-bottom: 8px;">' + ex.creator + '</div>';

                        if (ex.mint) {
                            flowHTML += '<div style="color: var(--text-secondary); margin-left: 8px; font-size: 10px; margin-bottom: 6px;">⬇ creates Token</div>' +
                            '<div style="color: var(--color-low);"><strong>🎫 Token:</strong></div>' +
                            '<div style="font-size: 12px; font-weight: bold; color: var(--color-low); word-break: break-all; padding: 6px; background: rgba(134, 239, 172, 0.1); border-radius: 2px;">' + ex.mint + '</div>';
                        }

                        flowHTML += '</div>';
                        return flowHTML;
                    }).join('');

                    if (flow && flow.downstream_creators && flow.downstream_creators.length > 0) {
                        const creatorMap = {};
                        const creatorOrder = [];

                        flow.downstream_creators.forEach(dc => {
                            if (!creatorMap[dc.creator_address]) {
                                creatorMap[dc.creator_address] = 0;
                                creatorOrder.push(dc.creator_address);
                            }
                            creatorMap[dc.creator_address]++;
                        });

                        const creatorsList = creatorOrder
                            .map((creator) => {
                                const tokenCount = creatorMap[creator];
                                return `<div style="font-family: monospace; font-size: 10px; color: var(--text-primary); padding: 6px; background: rgba(245, 158, 11, 0.05); border-radius: 2px; margin-bottom: 4px; display: flex; justify-content: space-between; word-break: break-all;"><span title="${creator}">${creator}</span><span style="color: var(--color-medium); font-weight: bold; flex-shrink: 0; margin-left: 10px;">${tokenCount} token${tokenCount > 1 ? 's' : ''}</span></div>`;
                            })
                            .join('');

                        flowsHTML += '<div style="margin-top: 10px; font-size: 9px; color: var(--text-secondary); margin-bottom: 6px;">ALL CREATORS FUNDED:</div>' +
                            '<div style="background: rgba(245, 158, 11, 0.05); border-radius: 4px; padding: 8px;">' +
                            creatorsList +
                            '</div>';
                    }
                }

                return '<div style="background: rgba(124, 58, 237, 0.08); padding: 12px; border-radius: 6px; border-left: 3px solid var(--primary); margin-bottom: 12px;">' +
                    '<div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 8px;">ROOT OPERATOR #' + (idx + 1) + '</div>' +
                    '<div style="font-family: monospace; font-size: 11px; color: var(--primary); word-break: break-all; margin-bottom: 8px; padding: 6px; background: rgba(124, 58, 237, 0.1); border-radius: 4px;">' + addr + '</div>' +
                    (flow ? '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; font-size: 10px;">' +
                        '<div><div style="color: var(--text-secondary);">CREATORS FUNDED</div><div style="color: var(--color-high); font-weight: bold;">' + flow.creators_funded + '</div></div>' +
                        '<div><div style="color: var(--text-secondary);">TOTAL SOL</div><div style="color: var(--color-low); font-weight: bold;">' + flow.total_sol_sent.toFixed(2) + '</div></div>' +
                        '</div>' : '') +
                    (flowsHTML ? '<div style="margin-top: 10px; font-size: 9px; color: var(--text-secondary); margin-bottom: 6px;">EXAMPLE FLOWS: Sender → Funder → Creator</div>' +
                        '<div style="background: rgba(0, 0, 0, 0.3); border-radius: 4px; padding: 6px;">' + flowsHTML + '</div>' : '') +
                    '</div>';
            }).join('');
        }

        function renderRelationshipDiagram(data) {
            const relationshipDiv = document.getElementById('scRelationshipDiagram');

            // Count visible root operators
            const visibleRootOps = showCexInfra ?
                data.root_addresses.length :
                data.root_addresses.filter(addr => !addr.includes('(CEX)') && !addr.includes('(INFRA)')).length;

            // Filter networks based on visibility
            let visibleNetworks = data.networks;
            if (!showCexInfra && data.network_root_operator_status) {
                // Exclude networks that have CEX/INFRA as root operators
                visibleNetworks = data.networks.filter(net => {
                    // Check status by both network_id as string and number
                    const hasInfraOrCex = data.network_root_operator_status[net.network_id] || data.network_root_operator_status[String(net.network_id)];
                    // Include network only if it does NOT have CEX/INFRA (hasInfraOrCex === false)
                    return !hasInfraOrCex;
                });
            }

            // Calculate metrics based on visible networks
            const visibleNetworkCount = visibleNetworks.length;
            const avgCreatorsPerNetwork = visibleNetworkCount > 0 ? (data.creators_unique / visibleNetworkCount).toFixed(1) : 0;
            const creatorReuseFactor = (data.creators_unique > 5 ? 'HIGH' : data.creators_unique > 2 ? 'MEDIUM' : 'LOW');
            const creatorReuseColor = creatorReuseFactor === 'HIGH' ? 'var(--color-critical)' : creatorReuseFactor === 'MEDIUM' ? 'var(--color-medium)' : 'var(--color-none)';

            const relationshipHTML = `
                <div style="font-size: 12px; color: var(--text-secondary);">
                    <div style="margin-bottom: 16px;">
                        <div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 8px; text-transform: uppercase; font-weight: bold;">📊 SUPER-NETWORK STRUCTURE</div>

                        <div style="background: var(--bg-secondary); border-radius: 6px; padding: 12px; margin-bottom: 12px;">
                            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-bottom: 10px;">
                                <div>
                                    <div style="color: var(--primary); font-size: 11px; margin-bottom: 4px;">TOTAL NETWORKS</div>
                                    <div style="color: var(--primary); font-weight: bold; font-size: 16px;">${visibleNetworkCount}</div>
                                    <div style="color: var(--text-secondary); font-size: 9px; margin-top: 4px;">
                                        ${visibleNetworks.length > 0 ? visibleNetworks.map(n => n.network_name || `Network_${n.network_id}`).join(', ') : 'N/A'}
                                    </div>
                                </div>
                                <div>
                                    <div style="color: var(--accent-cyan); font-size: 11px; margin-bottom: 4px;">UNIQUE CREATORS</div>
                                    <div style="color: var(--text-primary); font-weight: bold; font-size: 16px;">${data.creators_unique}</div>
                                </div>
                                <div>
                                    <div style="color: var(--accent-green); font-size: 11px; margin-bottom: 4px;">REUSE FACTOR</div>
                                    <div style="color: ${creatorReuseColor}; font-weight: bold; font-size: 16px;">${avgCreatorsPerNetwork}x/net</div>
                                </div>
                            </div>

                            <div style="padding: 10px; background: rgba(0, 0, 0, 0.3); border-radius: 4px; border-left: 2px solid ${creatorReuseColor}; margin-bottom: 10px;">
                                <div style="color: ${creatorReuseColor}; font-size: 11px; font-weight: bold; margin-bottom: 4px;">⚠️ CREATOR REUSE: ${creatorReuseFactor}</div>
                                <div style="color: var(--text-secondary); font-size: 10px;">
                                    ${data.creators_unique} creators across ${visibleNetworkCount} networks = <strong style="color: ${creatorReuseColor};">${avgCreatorsPerNetwork} creators per network average</strong>
                                    <br>This indicates a <strong>coordinated operation</strong> reusing launcher wallets.
                                </div>
                            </div>
                        </div>
                    </div>

                    <div style="margin-bottom: 12px;">
                        <div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 8px; text-transform: uppercase; font-weight: bold;">🔗 FUNDING FLOW</div>

                        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px;">
                            <div style="background: rgba(59, 130, 246, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid var(--color-none); color: var(--color-none); flex: 1;">
                                <div style="font-size: 10px; color: var(--text-secondary);">Upstream</div>
                                <strong>${data.funder_stats.total_funders}</strong> Senders
                            </div>
                            <div style="color: var(--text-secondary); font-weight: bold;">➜</div>
                            <div style="background: rgba(124, 58, 237, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid var(--primary); color: var(--primary); flex: 1;">
                                <div style="font-size: 10px; color: var(--text-secondary);">Root Operators</div>
                                <strong>${visibleRootOps}</strong> Ops
                            </div>
                            <div style="color: var(--text-secondary); font-weight: bold;">➜</div>
                            <div style="background: rgba(245, 158, 11, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid var(--color-high); color: var(--color-high); flex: 1;">
                                <div style="font-size: 10px; color: var(--text-secondary);">Creators</div>
                                <strong>${data.creators_unique}</strong> Wallets
                            </div>
                            <div style="color: var(--text-secondary); font-weight: bold;">➜</div>
                            <div style="background: rgba(74, 222, 128, 0.1); padding: 8px 12px; border-radius: 4px; border-left: 2px solid #4ade80; color: var(--color-low); flex: 1;">
                                <div style="font-size: 10px; color: var(--text-secondary);">Tokens</div>
                                <strong>${data.tokens.length}</strong> Launch
                            </div>
                        </div>
                    </div>

                    <div style="padding: 10px; background: rgba(124, 58, 237, 0.05); border-radius: 4px; border: 1px solid rgba(124, 58, 237, 0.2);">
                        <div style="font-size: 11px; line-height: 1.6;">
                            <strong>💡 What ties these ${data.network_count} networks together:</strong><br>
                            <span style="color: var(--text-secondary);">
                                Multiple funding networks consolidated into one super-cluster because they share <strong>${data.creators_unique} creators</strong> across <strong>${data.network_count} networks</strong>.
                                This indicates <strong>coordinated operations</strong> using the same launcher infrastructure.
                            </span>
                            <br><br>
                            <strong>📈 Tracked funding:</strong> <span style="color: var(--accent-purple);">${data.funder_stats.total_sol.toFixed(2)} SOL</span> flowing through this ecosystem
                        </div>
                    </div>
                </div>
            `;
            relationshipDiv.innerHTML = relationshipHTML;
        }

        function showDefinitionsGuide() {
            document.getElementById('definitionsGuideModal').style.display = 'block';
        }

        function showTagDefinition(tag, event) {
            event.stopPropagation();
            const modal = document.getElementById('tagDefinitionModal');
            const titleEl = document.getElementById('tagDefTitle');
            const contentEl = document.getElementById('tagDefContent');

            const definitions = {
                'INDEPENDENT': {
                    title: 'INDEPENDENT (Sage)',
                    color: '#6b8d7a',
                    definition: '<strong>No creators reused across clusters</strong><br><br>' +
                        "This cluster's creators appear only in this cluster and nowhere else. " +
                        'Each creator wallet is independent and not shared with other coordinated operations.',
                    metrics: '<strong>Characteristics:</strong><br>' +
                        '• creators_in_multiple_clusters = 0<br>' +
                        '• All creators unique to this cluster<br>' +
                        '• No connection to other clusters via shared creators',
                    risk: 'LOW - Isolated operation'
                },
                'CREATOR_POOL_WEAK': {
                    title: 'CREATOR POOL - WEAK (Warm)',
                    color: '#a89e6b',
                    definition: '<strong>Minimal creator reuse</strong><br><br>' +
                        'Some creators appear in multiple clusters, but the coordination signal is weak. ' +
                        'Either few creators are reused, or the reuse ratio is below our minimum-support threshold.',
                    metrics: '<strong>Characteristics:</strong><br>' +
                        '• creators_in_multiple_clusters ≥ 1<br>' +
                        '• Below thresholds for SHARED or STRONG<br>' +
                        '• Marginal coordination signal<br>' +
                        '• Minimum-support rules: (min creator_count, min reused, min ratio)',
                    risk: 'MEDIUM - Monitor for escalation'
                },
                'CREATOR_POOL_SHARED': {
                    title: 'CREATOR POOL - SHARED (Brown)',
                    color: '#a68b6b',
                    definition: '<strong>Solid creator pool coordination</strong><br><br>' +
                        'Multiple clusters share a pool of creators with consistent coordination. ' +
                        'Clear pattern of reusing the same launcher wallets across different funding networks.',
                    metrics: '<strong>Characteristics:</strong><br>' +
                        '• Min 5 unique creators in cluster<br>' +
                        '• Min 2+ creators reused in other clusters<br>' +
                        '• Min 30% reuse ratio (2+ / 5+)<br>' +
                        '• Solid coordination pattern detected',
                    risk: 'HIGH - Coordinated operations'
                },
                'CREATOR_POOL_STRONG': {
                    title: 'CREATOR POOL - STRONG (Mauve)',
                    color: '#9d7070',
                    definition: '<strong>Highly coordinated creator ecosystem</strong><br><br>' +
                        'Strong evidence of an organized operation reusing creators systematically. ' +
                        'Industrial-scale creator pool management with high concentration of reuse.',
                    metrics: '<strong>Characteristics:</strong><br>' +
                        '• Min 10 unique creators in cluster<br>' +
                        '• Min 5+ creators reused in other clusters<br>' +
                        '• Min 50% reuse ratio (5+ / 10+)<br>' +
                        '• Very strong coordination signal',
                    risk: '🚨 CRITICAL - Industrialized operation'
                }
            };

            const def = definitions[tag];
            if (def) {
                titleEl.textContent = def.title;
                contentEl.innerHTML = `
                    <div style="margin-bottom: 20px;">
                        <p>${def.definition}</p>
                    </div>

                    <div style="background: rgba(124, 58, 237, 0.05); padding: 15px; border-radius: 6px; border-left: 4px solid ${def.color}; margin-bottom: 15px;">
                        <strong>Thresholds & Metrics:</strong><br><br>
                        ${def.metrics}
                    </div>

                    <div style="background: rgba(239, 68, 68, 0.05); padding: 15px; border-radius: 6px; border-left: 4px solid var(--color-critical);">
                        <strong>Risk Assessment:</strong><br><br>
                        ${def.risk}
                    </div>
                `;
                modal.style.display = 'block';
            }
        }


        function renderNetworkRootOperators(data) {
            if (!data.root_operator_flows || data.root_operator_flows.length === 0) {
                return '<div style="color: var(--text-secondary); font-size: 12px; text-align: center; padding: 20px;">No root operators found</div>';
            }

            return data.root_operator_flows.map((flow, idx) => {
                // Build example flows HTML
                let flowsHTML = '';
                if (flow.example_flows && flow.example_flows.length > 0) {
                    flowsHTML = flow.example_flows.map((ex) => {
                        let flowHTML = '<div style="font-family: monospace; font-size: 11px; color: var(--text-primary); padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); line-height: 1.6; background: var(--bg-secondary); border-radius: 3px; margin-bottom: 6px;">';

                        if (ex.sender) {
                            flowHTML += '<div style="color: var(--color-none); margin-bottom: 6px;"><strong>📤 Sender:</strong></div>' +
                            '<div style="font-size: 12px; font-weight: bold; color: var(--color-none); word-break: break-all; padding: 6px; background: rgba(96, 165, 250, 0.1); border-radius: 2px; margin-bottom: 8px;">' + ex.sender + '</div>' +
                            '<div style="color: var(--text-secondary); margin-left: 8px; font-size: 10px; margin-bottom: 6px;">⬇ to Funder</div>';
                        }

                        flowHTML += '<div style="color: var(--primary); margin-bottom: 6px;"><strong>💰 Root Op:</strong></div>' +
                        '<div style="font-size: 12px; font-weight: bold; color: var(--primary); word-break: break-all; padding: 6px; background: rgba(129, 140, 248, 0.1); border-radius: 2px; margin-bottom: 8px;">' + ex.funder + '</div>' +
                        '<div style="color: var(--text-secondary); margin-left: 8px; font-size: 10px; margin-bottom: 6px;">⬇ ' + ex.sol_to_creator.toFixed(2) + ' SOL funds Creator</div>';

                        flowHTML += '<div style="color: var(--color-high); margin-bottom: 6px;"><strong>👤 Creator:</strong></div>' +
                        '<div style="font-size: 12px; font-weight: bold; color: var(--color-medium); word-break: break-all; padding: 6px; background: rgba(251, 191, 36, 0.1); border-radius: 2px;">' + ex.creator + '</div>';

                        flowHTML += '</div>';
                        return flowHTML;
                    }).join('');
                }

                return '<div style="background: rgba(124, 58, 237, 0.08); padding: 12px; border-radius: 6px; border-left: 3px solid var(--primary); margin-bottom: 12px;">' +
                    '<div style="font-size: 10px; color: var(--text-secondary); margin-bottom: 8px;">ROOT OPERATOR #' + (idx + 1) + '</div>' +
                    '<div style="font-family: monospace; font-size: 11px; color: var(--primary); word-break: break-all; margin-bottom: 8px; padding: 6px; background: rgba(124, 58, 237, 0.1); border-radius: 4px;">' + flow.root_operator + '</div>' +
                    '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; font-size: 10px;">' +
                        '<div><div style="color: var(--text-secondary);">CREATORS FUNDED</div><div style="color: var(--color-high); font-weight: bold;">' + flow.creators_funded + '</div></div>' +
                        '<div><div style="color: var(--text-secondary);">TOTAL SOL</div><div style="color: var(--color-low); font-weight: bold;">' + flow.total_sol_sent.toFixed(2) + '</div></div>' +
                    '</div>' +
                    (flowsHTML ? '<div style="margin-top: 10px; font-size: 9px; color: var(--text-secondary); margin-bottom: 6px;">EXAMPLE FLOWS: Sender → Funder → Creator</div>' +
                        '<div style="background: rgba(0, 0, 0, 0.3); border-radius: 4px; padding: 6px;">' + flowsHTML + '</div>' : '') +
                    '</div>';
            }).join('');
        }

        async function showNetworkDetails(networkId) {
            try {
                const response = await fetch(`/api/funding-network-details/${networkId}`);
                const data = await response.json();

                if (data.error) {
                    alert('Network details not found: ' + data.error);
                    return;
                }

                // Create a modal/popup to show network details with address flows
                const modalHtml = `
                    <div id="networkDetailsOverlay" style="position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.7); z-index: 10000; display: flex; align-items: center; justify-content: center;" onclick="document.getElementById('networkDetailsOverlay').remove();">
                        <div style="background: var(--bg-primary); border: 1px solid rgba(124, 58, 237, 0.3); border-radius: 12px; padding: 30px; max-width: 900px; max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3);" onclick="event.stopPropagation();">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                                <h2 style="color: var(--primary); margin: 0;">${data.network_name} Details</h2>
                                <button onclick="document.getElementById('networkDetailsOverlay').remove()" style="background: transparent; border: none; color: var(--text-secondary); font-size: 24px; cursor: pointer;">×</button>
                            </div>

                            <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-bottom: 25px;">
                                <div style="background: rgba(59, 130, 246, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid var(--color-none);">
                                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 6px;">Senders</div>
                                    <div style="font-size: 20px; font-weight: bold; color: var(--color-none);">${data.senders}</div>
                                </div>
                                <div style="background: rgba(139, 92, 246, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid var(--primary);">
                                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 6px;">Funders</div>
                                    <div style="font-size: 20px; font-weight: bold; color: var(--primary);">${data.funders}</div>
                                </div>
                                <div style="background: rgba(245, 158, 11, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid var(--color-high);">
                                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 6px;">Creators</div>
                                    <div style="font-size: 20px; font-weight: bold; color: var(--color-high);">${data.creators}</div>
                                </div>
                                <div style="background: rgba(168, 85, 247, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid var(--accent-purple);">
                                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 6px;">Tokens</div>
                                    <div style="font-size: 20px; font-weight: bold; color: var(--accent-purple);">${data.tokens}</div>
                                </div>
                                <div style="background: rgba(74, 222, 128, 0.1); padding: 12px; border-radius: 8px; border-left: 3px solid var(--color-low); grid-column: 1 / -1;">
                                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 6px;">Total SOL</div>
                                    <div style="font-size: 20px; font-weight: bold; color: var(--color-low);">${(data.total_sol || 0).toFixed(2)}</div>
                                </div>
                            </div>

                            <div style="margin-bottom: 20px;">
                                <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 12px; text-transform: uppercase; font-weight: bold;">🔗 Funding Flow (Sender → Funder → Creators)</div>
                                <div style="border: 1px solid rgba(124, 58, 237, 0.2); border-radius: 8px; padding: 15px; background: rgba(0, 0, 0, 0.2);" id="networkRootOpsContainer">
                                    Loading root operators...
                                </div>
                            </div>

                            <div style="text-align: center; color: var(--text-secondary); font-size: 12px;">
                                Click outside to close
                            </div>
                        </div>
                    </div>
                `;

                // Append to body
                const div = document.createElement('div');
                div.innerHTML = modalHtml;
                const modalElement = div.firstElementChild;
                document.body.appendChild(modalElement);

                // Render root operators asynchronously
                const rootOpsContainer = document.getElementById('networkRootOpsContainer');
                rootOpsContainer.innerHTML = renderNetworkRootOperators(data);

            } catch (error) {
                console.error('Error loading network details:', error);
                alert('Failed to load network details: ' + error.message);
            }
        }

    </script>
</body>
</html>
"""


def highlight_infra_in_funding(funders_list):
    """
    Add infrastructure/CEX/tag information to funders list.
    Enrich each funder with is_infrastructure, category, tags, display_name, and address_tags.
    """
    from src.utils.infra_mapping import get_account_info, get_cex_info
    from src.utils.address_tags import get_address_tags, get_domain_tag
    
    enriched_funders = []
    
    for funder in funders_list:
        funder_copy = funder.copy()
        funder_address = funder.get('funder_address')
        
        if not funder_address:
            funder_copy['is_infrastructure'] = False
            funder_copy['category'] = None
            funder_copy['tags'] = []
            funder_copy['display_name'] = None
            funder_copy['address_tags'] = {}
            enriched_funders.append(funder_copy)
            continue
        
        # Get address tags (domains, etc.)
        address_tags = get_address_tags(funder_address)
        
        # Check infrastructure first
        infra_info = get_account_info(funder_address)
        if infra_info:
            funder_copy['is_infrastructure'] = True
            funder_copy['category'] = infra_info.get('category')
            funder_copy['tags'] = infra_info.get('tags', [])
            funder_copy['display_name'] = infra_info.get('name')
            funder_copy['address_tags'] = address_tags
            enriched_funders.append(funder_copy)
            continue
        
        # Check CEX
        cex_info = get_cex_info(funder_address)
        if cex_info:
            funder_copy['is_infrastructure'] = False  # CEX is not infrastructure
            funder_copy['category'] = cex_info.get('category')
            funder_copy['tags'] = cex_info.get('tags', [])
            funder_copy['display_name'] = cex_info.get('name')
            funder_copy['address_tags'] = address_tags
            enriched_funders.append(funder_copy)
            continue
        
        # Neither infrastructure nor CEX
        funder_copy['is_infrastructure'] = False
        funder_copy['category'] = None
        funder_copy['tags'] = []
        funder_copy['display_name'] = None
        funder_copy['address_tags'] = address_tags
        enriched_funders.append(funder_copy)
    
    return enriched_funders

@app.route('/')
def index():
    """Serve the migration tracking dashboard"""
    return render_template('dashboard_home.html', active_page='tokens')


@app.route('/coordinated-funder-analysis/<creator_address>')
def coordinated_funder_analysis_view(creator_address: str):
    """Serve a full webview for coordinated funder analysis results"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get coordinated funder analysis data
        cursor.execute("""
            SELECT
                creator_address,
                connected_creators,
                shared_destinations,
                network_size,
                network_risk_level,
                detected_at,
                updated_at
            FROM creator_networks
            WHERE creator_address = ?
        """, (creator_address,))

        result = cursor.fetchone()

        if not result:
            conn.close()
            return f"""
            <html>
                <head>
                    <title>Coordinated Funder Analysis</title>
                    <style>
                        body {{ background: #0a0e27; color: var(--text-primary); font-family: 'Segoe UI', sans-serif; margin: 0; padding: 20px; }}
                        .container {{ max-width: 1200px; margin: 0 auto; }}
                        h1 {{ color: var(--accent-cyan); margin-bottom: 30px; }}
                        .not-analyzed {{ background: rgba(0, 0, 0, 0.3); padding: 30px; border-radius: 8px; text-align: center; border-left: 3px solid var(--color-medium); }}
                        .back-link {{ margin-bottom: 20px; }}
                        .back-link a {{ color: var(--accent-cyan); text-decoration: none; }}
                        .back-link a:hover {{ text-decoration: underline; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Coordinated Funder Analysis</h1>
                        <div class="not-analyzed">
                            <h2 style="color: var(--color-medium);">Not Yet Analyzed</h2>
                            <p>Coordinated funder analysis has not been performed for this creator yet.</p>
                            <p style="color: var(--text-secondary); font-size: 14px;">Run the coordinated funder analysis script to generate results.</p>
                        </div>
                    </div>
                </body>
            </html>
            """

        # Parse JSON data
        import json
        connected_creators = json.loads(result['connected_creators']) if result['connected_creators'] else []
        shared_destinations = json.loads(result['shared_destinations']) if result['shared_destinations'] else []

        # Get details about connected creators
        connected_creator_details = []
        for cc_addr in connected_creators[:20]:
            cursor.execute("""
                SELECT
                    earliest_tx_creator,
                    risk_level,
                    rug_probability,
                    market_cap_highest,
                    created_at
                FROM token_analysis
                WHERE earliest_tx_creator = ?
                LIMIT 1
            """, (cc_addr,))

            cc_info = cursor.fetchone()
            if cc_info:
                connected_creator_details.append({
                    'address': cc_addr,
                    'risk_level': cc_info['risk_level'],
                    'rug_probability': cc_info['rug_probability'],
                    'market_cap': cc_info['market_cap_highest']
                })

        conn.close()

        # Determine risk color
        risk_colors = {
            'CRITICAL': 'var(--color-critical)',
            'HIGH': 'var(--color-medium)',
            'MEDIUM': 'var(--color-high)',
            'LOW': '#4ade80'
        }
        risk_color = risk_colors.get(result['network_risk_level'], '#a0a0a0')

        # Build connected creators HTML
        connected_html = ''
        for i, cc in enumerate(connected_creator_details[:20], 1):
            risk_color_cc = risk_colors.get(cc['risk_level'], '#a0a0a0')
            connected_html += f"""
            <div style="padding: 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); font-size: 13px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <div style="font-family: monospace; color: {risk_color_cc};">{i}. {cc['address']}</div>
                    <div style="display: flex; gap: 20px; color: var(--text-secondary);">
                        <span style="color: {risk_color_cc};">{cc['risk_level']}</span>
                        <span>{(cc['rug_probability'] * 100):.0f}% rug</span>
                    </div>
                </div>
            </div>
            """

        # Build shared destinations HTML
        destinations_html = ''
        for i, dest in enumerate(shared_destinations[:30], 1):
            destinations_html += f"""
            <div style="padding: 10px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); font-family: monospace; font-size: 12px; color: var(--color-medium);">
                {i}. {dest}
            </div>
            """

        html = f"""
        <html>
            <head>
                <title>Coordinated Funder Analysis - {creator_address[:16]}...</title>
                <style>
                    body {{
                        background: #0a0e27;
                        color: var(--text-primary);
                        font-family: 'Segoe UI', sans-serif;
                        margin: 0;
                        padding: 20px;
                    }}
                    .container {{
                        max-width: 1400px;
                        margin: 0 auto;
                    }}
                    h1 {{
                        color: var(--accent-cyan);
                        margin-bottom: 10px;
                    }}
                    .creator-addr {{
                        font-family: monospace;
                        font-size: 12px;
                        color: var(--text-secondary);
                        margin-bottom: 30px;
                    }}
                    .back-link {{
                        margin-bottom: 20px;
                    }}
                    .back-link a {{
                        color: var(--accent-cyan);
                        text-decoration: none;
                    }}
                    .back-link a:hover {{
                        text-decoration: underline;
                    }}
                    .stats-grid {{
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                        gap: 15px;
                        margin-bottom: 30px;
                    }}
                    .stat-box {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 20px;
                        border-radius: 8px;
                        border-left: 3px solid {risk_color};
                    }}
                    .stat-label {{
                        color: var(--text-secondary);
                        font-size: 11px;
                        text-transform: uppercase;
                        margin-bottom: 10px;
                    }}
                    .stat-value {{
                        font-size: 24px;
                        font-weight: bold;
                        color: {risk_color};
                    }}
                    .section {{
                        background: var(--bg-secondary);
                        border-radius: 8px;
                        margin-bottom: 30px;
                        overflow: hidden;
                    }}
                    .section-title {{
                        background: var(--bg-secondary);
                        padding: 15px;
                        border-bottom: 1px solid rgba(6, 182, 212, 0.2);
                        font-weight: 600;
                        color: var(--accent-cyan);
                    }}
                    .section-content {{
                        max-height: 600px;
                        overflow-y: auto;
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>Coordinated Funder Analysis</h1>
                    <div class="creator-addr">Creator: {creator_address}</div>

                    <div class="stats-grid">
                        <div class="stat-box">
                            <div class="stat-label">Network Risk Level</div>
                            <div class="stat-value">{result['network_risk_level']}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Network Size</div>
                            <div class="stat-value">{result['network_size']}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Connected Creators</div>
                            <div class="stat-value">{len(connected_creators)}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Shared Destinations</div>
                            <div class="stat-value">{len(shared_destinations)}</div>
                        </div>
                    </div>

                    <div class="section">
                        <div class="section-title">Connected Creators ({len(connected_creator_details)} shown)</div>
                        <div class="section-content">
                            {connected_html if connected_html else '<div style="padding: 20px; color: var(--text-secondary);">No connected creators found</div>'}
                        </div>
                    </div>

                    <div class="section">
                        <div class="section-title">Shared Destination Wallets ({len(shared_destinations)} total)</div>
                        <div class="section-content">
                            {destinations_html if destinations_html else '<div style="padding: 20px; color: var(--text-secondary);">No shared destinations found</div>'}
                        </div>
                    </div>

                    <div style="color: var(--text-secondary); font-size: 12px; margin-top: 30px;">
                        <p>Analysis performed: {result['detected_at']}</p>
                        <p>Last updated: {result['updated_at']}</p>
                    </div>
                </div>
            </body>
        </html>
        """
        return html

    except Exception as e:
        return f"<html><body style='background: #0a0e27; color: var(--text-primary);'><h1>Error</h1><p>{str(e)}</p></body></html>", 500


@app.route('/api/migrated-tokens')
def api_migrated_tokens():
    """Get all migrated tokens with analysis data"""
    tokens = get_migrated_tokens(limit=25, light=True)
    response = jsonify({'tokens': tokens})
    # Disable caching to ensure fresh data
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/api/token-metrics/<token_mint>')
def api_token_metrics(token_mint: str):
    """Get detailed risk metrics for a specific token"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Query post-migration analysis data
        cursor.execute("""
            SELECT
                mint,
                events_parsed as total_events,
                post_migration_mint_concentration,
                post_migration_unique_minters_ratio,
                post_migration_sell_suppression_ratio,
                post_migration_mint_velocity_sec,
                post_migration_buy_size_variance,
                post_migration_sell_volume_concentration,
                post_migration_creator_activity_ratio,
                rug_probability,
                risk_level,
                post_migration_coverage as coverage,
                price_current,
                price_highest,
                market_cap_current,
                market_cap_highest
            FROM token_analysis
            WHERE mint = ?
        """, (token_mint,))

        row = cursor.fetchone()

        if not row:
            conn.close()
            return jsonify({'error': 'Token not found'}), 404

        # Get most recent price source from token_price_snapshots
        cursor.execute("""
            SELECT source
            FROM token_price_snapshots
            WHERE mint = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (token_mint,))

        price_source_row = cursor.fetchone()
        if price_source_row:
            price_source = price_source_row['source']
        elif row['price_current'] and row['price_current'] > 0:
            # Token has a price but no snapshot history — it's cached/historical
            price_source = 'cached'
        else:
            # Token has no price
            price_source = 'none'
        conn.close()

        # Read first-price latency from launch_price.log
        import src.core.launch_price_logger as _lpl
        first_price_latency = None
        first_price_source = None
        try:
            with open(_lpl._LOG_PATH, 'r', encoding='utf-8') as _f:
                for _line in _f:
                    _parts = _line.strip().split('\t')
                    if len(_parts) >= 9 and _parts[0] == 'FIRST_PRICE' and _parts[8] == token_mint:
                        first_price_latency = _parts[4]   # e.g. "1.9s" or "unknown"
                        first_price_source = _parts[7]    # e.g. "pool" or "cached"
                        break
        except Exception:
            pass

        # Format response for post-migration analysis only
        response = jsonify({
            'mint': row['mint'],
            'total_txs': 0,
            'total_events': row['total_events'] if row['total_events'] else 0,
            'metrics': {
                'mint_concentration': row['post_migration_mint_concentration'] if row['post_migration_mint_concentration'] else 0,
                'unique_minters_ratio': row['post_migration_unique_minters_ratio'] if row['post_migration_unique_minters_ratio'] else 0,
                'sell_suppression_ratio': row['post_migration_sell_suppression_ratio'] if row['post_migration_sell_suppression_ratio'] else 0,
                'mint_velocity_sec': row['post_migration_mint_velocity_sec'] if row['post_migration_mint_velocity_sec'] else 0,
                'buy_size_variance': row['post_migration_buy_size_variance'] if row['post_migration_buy_size_variance'] else 0,
                'sell_volume_concentration': row['post_migration_sell_volume_concentration'] if row['post_migration_sell_volume_concentration'] else 0,
                'creator_activity_ratio': row['post_migration_creator_activity_ratio'] if row['post_migration_creator_activity_ratio'] else 0
            },
            'risk': {
                'rug_probability': row['rug_probability'] if row['rug_probability'] else 0,
                'risk_level': row['risk_level']
            },
            'price': {
                'current': row['price_current'] if row['price_current'] else 0,
                'highest': row['price_highest'] if row['price_highest'] else 0,
                'source': price_source
            },
            'market_cap': {
                'current': row['market_cap_current'] if row['market_cap_current'] else 0,
                'highest': row['market_cap_highest'] if row['market_cap_highest'] else 0
            },
            'coverage': row['coverage'] if row['coverage'] else 0,
            'first_price_latency': first_price_latency,
            'first_price_source': first_price_source
        })
        return response
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-details/<creator_address>')
def api_creator_details(creator_address: str):
    """Get detailed information about a creator"""
    try:
        # Validate creator address format
        if not creator_address or len(creator_address) < 30:
            return jsonify({'error': 'Invalid creator address format'}), 400

        from src.utils.address_tags import get_address_tags

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # 1. Get all tokens launched by this creator
        cursor.execute("""
            SELECT
                mint,
                created_at,
                bonding_curve_pda,
                create_tx_signature,
                rug_probability,
                risk_level,
                market_cap_current,
                market_cap_highest,
                creator_is_blocked
            FROM token_analysis
            WHERE earliest_tx_creator = ?
            ORDER BY created_at DESC
        """, (creator_address,))
        tokens = [dict(row) for row in cursor.fetchall()]

        # Add CEX/INFRA labels for tokens if creator is in CEX/INFRA
        creator_label = get_cex_infra_label(creator_address)
        if creator_label:
            for token in tokens:
                token['creator_label'] = creator_label

        # 2. Get funding data - MERGED from both sources (funders + outgoing transfers from tx_ledger)
        # Pre-migration funders (excluding CEX and INFRA)
        cursor.execute("""
            SELECT
                COUNT(DISTINCT funder_address) as funder_count,
                SUM(amount_sol) as total_sol,
                SUM(CASE WHEN is_cex = 1 THEN 1 ELSE 0 END) as cex_funder_count
            FROM creator_funders
            WHERE creator_address = ? AND is_cex = 0
        """, (creator_address,))
        funders_row = cursor.fetchone()
        
        # Post-migration outgoing transfers from creator_receivers
        cursor.execute("""
            SELECT
                COUNT(DISTINCT receiver_address) as recipient_count,
                SUM(amount_sol) as total_sol_out
            FROM creator_receivers
            WHERE creator_address = ?
        """, (creator_address,))
        recipients_row = cursor.fetchone()

        # Combine both sources
        funder_count = (funders_row['funder_count'] or 0) if funders_row else 0
        funders_sol = (funders_row['total_sol'] or 0) if funders_row else 0
        recipient_count = (recipients_row['recipient_count'] or 0) if recipients_row else 0
        recipients_sol = (recipients_row['total_sol_out'] or 0) if recipients_row else 0
        
        funding = {
            'total_funders': funder_count,
            'total_sol_in': funders_sol,
            'total_recipients': recipient_count,
            'total_sol_out': recipients_sol,
            'total_accounts': funder_count + recipient_count,
            'total_sol': funders_sol + recipients_sol
        }

        # 3. Get ALL funders (not limited to top 10) and sort by relevance (tags > amount)
        cursor.execute("""
            SELECT
                funder_address,
                amount_sol,
                is_cex,
                cex_exchange,
                cex_type,
                COALESCE(source_type, 'original_sender') as source_type
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
        """, (creator_address,))
        all_funders = [dict(row) for row in cursor.fetchall()]

        # Add CEX/INFRA labels and security tags to funders
        for funder in all_funders:
            funder['funder_label'] = get_cex_infra_label(funder['funder_address'])
            funder['labels'] = []

        # Add infrastructure highlighting to funders
        all_funders = highlight_infra_in_funding(all_funders)

        # Add security tags (circular funding, network membership, etc.)
        for funder in all_funders:
            # Check for DIRECT circular funding: funder received from creator AND sent back to creator
            cursor.execute("""
                SELECT COUNT(*) as direct_circular FROM (
                    SELECT recipient_address FROM creator_outgoing_transfers
                    WHERE creator_address = ? AND recipient_address = ?
                    INTERSECT
                    SELECT funder_address FROM creator_funders
                    WHERE creator_address = ? AND funder_address = ?
                )
            """, (creator_address, funder['funder_address'], creator_address, funder['funder_address']))
            direct_circ = cursor.fetchone()
            if direct_circ and direct_circ['direct_circular'] > 0:
                if 'labels' not in funder:
                    funder['labels'] = []
                funder['labels'].append('⚠️ CIRCULAR_FUNDING(direct)')

            # Check if funder is in a network
            cursor.execute("SELECT network_name FROM creator_networks WHERE creator_address = ?", (funder['funder_address'],))
            net = cursor.fetchone()
            if net:
                funder['network'] = net['network_name']
                if 'labels' not in funder:
                    funder['labels'] = []
                funder['labels'].append(f'NETWORK_MEMBER')

        # Sort by relevance: funders with tags first, then by amount
        def relevance_score(funder):
            score = 0
            labels = funder.get('labels', [])
            # Circular funding is most important (higher score = more relevant)
            if any('CIRCULAR_FUNDING' in label for label in labels):
                score += 10000
            if any('NETWORK_MEMBER' in label for label in labels):
                score += 5000
            if funder.get('is_infrastructure'):
                score += 2000
            if funder.get('funder_label'):
                score += 1000
            # Then sort by amount (secondary)
            score += funder.get('amount_sol', 0)
            return score

        all_funders_sorted = sorted(all_funders, key=relevance_score, reverse=True)
        top_funders = all_funders_sorted[:15]  # Show top 15 instead of 10 to include more tagged funders

        # 4. Get top recipients from creator_receivers (post-migration outgoing transfers)
        cursor.execute("""
            SELECT
                receiver_address as recipient_address,
                amount_sol,
                receiver_type,
                receiver_name
            FROM creator_receivers
            WHERE creator_address = ?
            ORDER BY amount_sol DESC
            LIMIT 10
        """, (creator_address,))
        top_recipients = [dict(row) for row in cursor.fetchall()]

        # Add infrastructure highlighting to recipients (use recipient_address field)
        for recipient in top_recipients:
            recipient_addr = recipient["recipient_address"]
            recipient_info = highlight_infra_in_funding([{"funder_address": recipient_addr, "amount_sol": recipient["amount_sol"]}])[0]
            recipient.update({
                "is_infrastructure": recipient_info["is_infrastructure"],
                "category": recipient_info["category"],
                "tags": recipient_info["tags"],
                "display_name": recipient_info["display_name"],
                "address_tags": recipient_info.get("address_tags", {}),
                "recipient_label": get_cex_infra_label(recipient_addr),
            })

        # 5. Get wallet cluster size (includes coordinated funders and recipients)
        cursor.execute("""
            SELECT COUNT(DISTINCT wallet_addr) as total_wallets,
                   SUM(CASE WHEN hop = 0 THEN 1 ELSE 0 END) as hop0_count,
                   SUM(CASE WHEN hop = 1 THEN 1 ELSE 0 END) as hop1_count,
                   SUM(CASE WHEN hop = 2 THEN 1 ELSE 0 END) as hop2_count
            FROM (
                SELECT wallet as wallet_addr, hop FROM wallet_cluster_nodes WHERE root_creator = ?
                UNION
                SELECT funder_address as wallet_addr, 0 as hop FROM creator_funders WHERE creator_address = ?
                UNION
                SELECT DISTINCT receiver_address as wallet_addr, 0 as hop FROM creator_receivers
                    WHERE creator_address = ?
            )
        """, (creator_address, creator_address, creator_address))
        cluster_row = cursor.fetchone()

        cluster = {
            'total_wallets': cluster_row['total_wallets'] or 0,
            'hop0': cluster_row['hop0_count'] or 0,
            'hop1': cluster_row['hop1_count'] or 0,
            'hop2': cluster_row['hop2_count'] or 0
        }

        # 6. Check blocklist status
        is_blocked = bool(tokens[0]['creator_is_blocked']) if tokens else False

        # If no data found for this creator, still return basic info rather than error
        if not tokens and not funding['total_funders'] and not funding['total_recipients']:
            print(f"[CREATOR_DETAILS] No creator data found for {creator_address}", flush=True)

        # 7. Get creator tags (from creator_tags table)
        cursor.execute("""
            SELECT tag, description, amount_sol
            FROM creator_tags
            WHERE creator_address = ?
        """, (creator_address,))
        tags = [{'tag': row[0], 'description': row[1], 'amount_sol': row[2]} for row in cursor.fetchall()]

        # 8. Get creator's address tags (domains, etc. from address_tags table)
        creator_address_tags = get_address_tags(creator_address)

        # 9. Get OUTBOUND cross-creator references (recipients this creator shares with others)
        outbound_cross_refs = []
        try:
            from unified_recipient_tracker import UnifiedRecipientTracker
            tracker = UnifiedRecipientTracker()
            shared = tracker.find_shared_recipients(creator_address)
            for recipient, other_creators in shared.items():
                if other_creators:
                    outbound_cross_refs.append({
                        'address': recipient,
                        'other_creators': other_creators,
                        'creator_count': len(other_creators),
                        'direction': 'OUTBOUND',
                        'type': 'shared_recipient',
                        'description': f'This creator sends SOL to {recipient[:8]}..., which also receives from {len(other_creators)} other creator(s)'
                    })
            outbound_cross_refs.sort(key=lambda x: x['creator_count'], reverse=True)
        except Exception as e:
            outbound_cross_refs = []

        # 10. Get INBOUND cross-creator references (funders that also fund other creators)
        inbound_cross_refs = []
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT cf.funder_address, COUNT(DISTINCT cf2.creator_address) as other_creator_count
                FROM creator_funders cf
                JOIN creator_funders cf2 ON cf.funder_address = cf2.funder_address
                WHERE cf.creator_address = ?
                AND cf2.creator_address != ?
                GROUP BY cf.funder_address
                ORDER BY other_creator_count DESC
            """, (creator_address, creator_address))

            for row in cursor.fetchall():
                funder_addr, other_count = row
                cursor.execute("""
                    SELECT DISTINCT creator_address FROM creator_funders
                    WHERE funder_address = ? AND creator_address != ?
                """, (funder_addr, creator_address))
                other_creators = [r[0] for r in cursor.fetchall()]

                if other_creators:
                    inbound_cross_refs.append({
                        'address': funder_addr,
                        'other_creators': other_creators,
                        'creator_count': len(other_creators),
                        'direction': 'INBOUND',
                        'type': 'shared_funder',
                        'description': f'This funder ({funder_addr[:8]}...) funds this creator AND {len(other_creators)} other creator(s)'
                    })

            inbound_cross_refs.sort(key=lambda x: x['creator_count'], reverse=True)
        except Exception as e:
            inbound_cross_refs = []

        # 11. Check if any recipients are network coordinators
        coordinator_flags = {}
        try:
            from unified_recipient_tracker import UnifiedRecipientTracker
            tracker = UnifiedRecipientTracker()
            coordinators = tracker.get_network_coordinators(min_creators=2)
            for coord in coordinators:
                if coord.address in [r.get('recipient_address') for r in top_recipients]:
                    coordinator_flags[coord.address] = {
                        'creator_count': coord.creator_count,
                        'confidence': coord.network_confidence,
                        'suspicious_flags': coord.suspicious_flags
                    }
        except Exception as e:
            coordinator_flags = {}

        # 12. Get network assignment for this creator
        network_name = None
        network_type = None
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT network_name FROM creator_networks
                WHERE creator_address = ?
            """, (creator_address,))
            network_row = cursor.fetchone()
            if network_row:
                network_name = network_row[0]
                # Get network type
                cursor.execute("""
                    SELECT network_type FROM network_cex_infra_flags
                    WHERE network_name = ?
                """, (network_name,))
                net_type_row = cursor.fetchone()
                if net_type_row:
                    network_type = net_type_row[0]
        except Exception as e:
            pass

        conn.close()

        # Enhance top_recipients with OUTBOUND cross-reference info
        for recipient in top_recipients:
            recipient_addr = recipient.get('recipient_address')
            if recipient_addr in coordinator_flags:
                recipient['is_network_coordinator'] = True
                recipient['coordinator_info'] = coordinator_flags[recipient_addr]
            else:
                recipient['is_network_coordinator'] = False
            for cross_ref in outbound_cross_refs:
                if cross_ref['address'] == recipient_addr:
                    recipient['shared_with_creators'] = cross_ref['other_creators']
                    recipient['shared_creator_count'] = cross_ref['creator_count']
                    recipient['cross_ref_direction'] = 'OUTBOUND'
                    break

        # Enhance top_funders with INBOUND cross-reference info
        for funder in top_funders:
            funder_addr = funder.get('funder_address')
            for cross_ref in inbound_cross_refs:
                if cross_ref['address'] == funder_addr:
                    funder['shared_with_creators'] = cross_ref['other_creators']
                    funder['shared_creator_count'] = cross_ref['creator_count']
                    funder['cross_ref_direction'] = 'INBOUND'
                    break

        return jsonify({
            'creator_address': creator_address,
            'creator_address_tags': creator_address_tags,
            'tokens': tokens,
            'funding': funding,
            'top_funders': top_funders,
            'top_recipients': top_recipients,
            'cross_references': {
                'inbound': inbound_cross_refs,
                'outbound': outbound_cross_refs,
                'total_inbound_links': len(inbound_cross_refs),
                'total_outbound_links': len(outbound_cross_refs)
            },
            'cluster': cluster,
            'is_blocked': is_blocked,
            'tags': tags,
            'network_name': network_name,
            'network_type': network_type
        })

    except Exception as e:
        import traceback
        print(f"[CREATOR_DETAILS_ERROR] {creator_address}: {str(e)}", flush=True)
        print(traceback.format_exc(), flush=True)
        return jsonify({'error': str(e), 'details': traceback.format_exc()}), 500


@app.route('/api/funder-tokens/<funder_address>')
def api_funder_tokens(funder_address: str):
    """Get tokens that a funder (account) has supported"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all tokens where this address was a funder (excluding CEX and INFRA)
        cursor.execute("""
            SELECT DISTINCT
                ta.mint,
                ta.earliest_tx_creator as creator_address,
                ta.created_at,
                ta.risk_level,
                ta.market_cap_current,
                cf.amount_sol
            FROM creator_funders cf
            JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            WHERE cf.funder_address = ? AND cf.is_cex = 0
            ORDER BY ta.created_at DESC
        """, (funder_address,))

        tokens = []
        for row in cursor.fetchall():
            creator_addr = row['creator_address']
            tokens.append({
                'mint': row['mint'],
                'creator_address': creator_addr,
                'creator_label': get_cex_infra_label(creator_addr),
                'created_at': row['created_at'],
                'risk_level': row['risk_level'],
                'market_cap_current': row['market_cap_current'],
                'funding_amount_sol': row['amount_sol']
            })

        conn.close()
        return jsonify({
            'funder_address': funder_address,
            'tokens_funded': tokens,
            'total_tokens_funded': len(tokens)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cex-funders')
def api_cex_funders():
    """Get all CEX funders and their activity"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all CEX exchanges with statistics
        cursor.execute("""
            SELECT
                cex_exchange,
                COUNT(DISTINCT creator_address) as creator_count,
                COUNT(DISTINCT funder_address) as funder_count,
                SUM(amount_sol) as total_sol
            FROM creator_funders
            WHERE is_cex = 1
            GROUP BY cex_exchange
            ORDER BY total_sol DESC
        """)
        exchanges = [dict(row) for row in cursor.fetchall()]

        # Get top CEX funders across all exchanges
        cursor.execute("""
            SELECT
                funder_address,
                cex_exchange,
                cex_type,
                COUNT(DISTINCT creator_address) as creators_funded,
                SUM(amount_sol) as total_sol
            FROM creator_funders
            WHERE is_cex = 1
            GROUP BY funder_address, cex_exchange
            ORDER BY total_sol DESC
            LIMIT 50
        """)
        top_cex_funders = [dict(row) for row in cursor.fetchall()]

        # Get all creators funded by CEX
        cursor.execute("""
            SELECT
                creator_address,
                COUNT(DISTINCT cex_exchange) as exchanges_funding,
                SUM(amount_sol) as total_cex_funding
            FROM creator_funders
            WHERE is_cex = 1
            GROUP BY creator_address
            ORDER BY total_cex_funding DESC
            LIMIT 100
        """)
        cex_funded_creators = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'exchanges': exchanges,
            'top_cex_funders': top_cex_funders,
            'cex_funded_creators': cex_funded_creators,
            'total_cex_funders': len(top_cex_funders),
            'total_cex_funded_creators': len(cex_funded_creators)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Funding Network / Coordination Detection Endpoints ---

@app.route('/api/funding-network')
def api_funding_network():
    """Get suspicious funding coordination networks"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # REAL coordination indicator: Same sender funding multiple different creators
        # This query finds senders that appear in funder_incoming_transfers AND 
        # those funders in turn funded multiple creators
        cursor.execute("""
            SELECT sender_address,
                   COUNT(DISTINCT creator_address) as creator_count,
                   COUNT(DISTINCT funder_address) as funder_count,
                   COUNT(*) as transaction_count,
                   SUM(amount_sol) as total_sol
            FROM (
                SELECT fit.sender_address,
                       cf.creator_address,
                       fit.funder_address,
                       fit.amount_sol
                FROM funder_incoming_transfers fit
                JOIN creator_funders cf ON fit.funder_address = cf.funder_address
                GROUP BY fit.sender_address, cf.creator_address, fit.funder_address
            )
            GROUP BY sender_address
            HAVING creator_count >= 2
            ORDER BY creator_count DESC, funder_count DESC
        """)
        
        true_coordinators = [dict(row) for row in cursor.fetchall()]

        # Get funder analysis statistics
        cursor.execute("""
            SELECT COUNT(DISTINCT funder_address) as analyzed_funders,
                   COUNT(DISTINCT creator_address) as creators_with_funders,
                   SUM(total_inflows) as total_inflows,
                   SUM(total_outflows) as total_outflows
            FROM creator_funders
            WHERE last_analyzed IS NOT NULL
        """)
        stats_row = cursor.fetchone()
        stats = dict(stats_row) if stats_row else {}

        # Build networks - only true coordinators
        networks = []
        hub_addresses = set()

        for coordinator in true_coordinators:
            networks.append({
                'address': coordinator['sender_address'],
                'creator_count': coordinator['creator_count'],
                'total_sol': coordinator['total_sol'] or 0,
                'transactions': coordinator['transaction_count'],
                'funder_count': coordinator['funder_count'],
                'risk_type': 'multi_creator' if coordinator['creator_count'] >= 3 else 'dual_creator'
            })
            
            # Hub = coordinates 3+ creators
            if coordinator['creator_count'] >= 3:
                hub_addresses.add(coordinator['sender_address'])

        conn.close()

        return jsonify({
            'networks': networks,
            'hub_addresses': list(hub_addresses),
            'total_sol': stats.get('total_inflows', 0) or 0,
            'analyzed_funders': stats.get('analyzed_funders', 0),
            'creators_with_funders': stats.get('creators_with_funders', 0),
            'coordination_found': len(true_coordinators) > 0,
            'suspicious_coordinator_count': len(networks)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# --- Creator SOL Watch Endpoints ---

@app.route('/api/creator-sol-stats/<creator_address>')
def api_creator_sol_stats(creator_address: str):
    """Get SOL in/out summary for a creator - DEPRECATED
    
    CreatorWatchManager has been removed. Use /api/creator-outgoing-analysis/<creator_address>
    or /api/creator-details/<creator_address> instead for updated creator analysis.
    """
    return jsonify({'error': 'This endpoint is deprecated. Use /api/creator-details/<creator_address> instead'}), 410

@app.route('/api/infrastructure-mapping')
def api_infrastructure_mapping():
    """Get infrastructure account mapping for UI highlighting (infrastructure + CEX separate)"""
    try:
        from src.utils.infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS

        mapping = {
            "infrastructure": {},
            "cex": {}
        }

        # Infrastructure accounts
        for address, info in INFRASTRUCTURE_ACCOUNTS.items():
            mapping["infrastructure"][address] = {
                "name": info["name"],
                "category": info["category"],
                "description": info["description"],
                "tags": info.get("tags", []),
                "risk_level": info["risk_level"],
            }

        # CEX accounts
        for address, info in CEX_ACCOUNTS.items():
            mapping["cex"][address] = {
                "name": info["name"],
                "category": info["category"],
                "exchange": info.get("exchange"),
                "description": info["description"],
                "tags": info.get("tags", []),
                "risk_level": info["risk_level"],
            }

        return jsonify(mapping)

    except Exception as e:
        return jsonify({"infrastructure": {}, "cex": {}}), 200


@app.route('/api/network-coordinators')
def api_network_coordinators():
    """Get all identified cross-funder coordinators"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all coordinators ordered by confidence and creator reach
        cursor.execute("""
            SELECT
                coordinator_address,
                creator_count,
                creators_linked,
                total_sol_moved,
                network_confidence,
                is_cex,
                cex_exchange,
                suspicious_flags,
                detection_timestamp,
                last_updated
            FROM network_coordinators
            ORDER BY
                CASE WHEN network_confidence = 'high' THEN 1
                     WHEN network_confidence = 'medium' THEN 2
                     ELSE 3 END,
                creator_count DESC
        """)

        coordinators = []
        for row in cursor.fetchall():
            creators = json.loads(row['creators_linked']) if row['creators_linked'] else []
            flags = json.loads(row['suspicious_flags']) if row['suspicious_flags'] else []

            coordinators.append({
                'address': row['coordinator_address'],
                'creator_count': row['creator_count'],
                'creators': creators,
                'total_sol': row['total_sol_moved'],
                'confidence': row['network_confidence'],
                'is_cex': bool(row['is_cex']),
                'cex_exchange': row['cex_exchange'],
                'flags': flags,
                'detected_at': row['detection_timestamp'],
                'updated_at': row['last_updated']
            })

        conn.close()

        return jsonify({
            'total': len(coordinators),
            'high_confidence': sum(1 for c in coordinators if c['confidence'] == 'high'),
            'medium_confidence': sum(1 for c in coordinators if c['confidence'] == 'medium'),
            'coordinators': coordinators
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funding-network-3tier/<creator_address>')
def api_funding_network_3tier(creator_address: str):
    """Get 3-tier funding network (Sender → Funder → Creator) for a specific creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Step 1: Find all funders funding this creator
        cursor.execute("""
            SELECT DISTINCT funder_address, SUM(amount_sol) as total_to_creator
            FROM creator_funders
            WHERE creator_address = ?
            GROUP BY funder_address
            ORDER BY total_to_creator DESC
        """, (creator_address,))

        funders = cursor.fetchall()

        # Step 2: For each funder, find all senders funding them
        network_3tier = []

        for funder_row in funders:
            funder_addr = funder_row['funder_address']
            funder_total = funder_row['total_to_creator']

            # Check funder type (CEX or INFRA)
            funder_type = 'unknown'
            funder_label = None
            is_cex_or_infra = False
            try:
                from src.utils.infra_mapping import get_cex_info, get_account_info

                # Check if funder is CEX
                cex_info = get_cex_info(funder_addr)
                if cex_info:
                    funder_type = 'cex'
                    funder_label = cex_info.get('name', 'Unknown CEX')
                    is_cex_or_infra = True

                # Check if funder is infrastructure
                if not cex_info:
                    infra_info = get_account_info(funder_addr)
                    if infra_info:
                        funder_type = 'infra'
                        funder_label = infra_info.get('name', 'Infrastructure')
                        is_cex_or_infra = True
            except:
                pass

            # For CEX/INFRA funders, show them but don't trace senders
            if is_cex_or_infra:
                funder_info = {
                    'funder_address': funder_addr,
                    'funder_type': funder_type,
                    'funder_label': funder_label,
                    'total_to_creator': funder_total,
                    'sender_count': 0,  # Don't count senders for CEX/INFRA
                    'known_sender_count': 0,
                    'senders': [],  # Empty senders list for CEX/INFRA
                    'is_terminal': True  # Mark as terminal endpoint
                }
                network_3tier.append(funder_info)
                continue

            # Get senders for this funder
            cursor.execute("""
                SELECT
                    sender_address,
                    SUM(amount_sol) as amount_to_funder,
                    sender_type,
                    is_cex,
                    cex_exchange,
                    cex_type
                FROM funder_incoming_transfers
                WHERE funder_address = ?
                GROUP BY sender_address, sender_type
                ORDER BY amount_to_funder DESC
            """, (funder_addr,))

            senders = cursor.fetchall()

            # Enrich senders with labels and sort known accounts first
            from src.utils.infra_mapping import get_account_info, get_cex_info

            enriched_senders = []
            for s in senders:
                sender_addr = s['sender_address']
                sender_info = {
                    'sender_address': sender_addr,
                    'amount_to_funder': s['amount_to_funder'],
                    'sender_type': s['sender_type'],
                    'is_known': False,
                    'label': None,
                    'risk_level': 'unknown'  # NEW: track risk level
                }

                # Check if it's a CEX account
                if s['is_cex']:
                    sender_info['is_known'] = True
                    sender_info['label'] = s['cex_exchange'] or 'CEX'
                    sender_info['risk_level'] = 'high'  # CEX hot wallets are risky
                else:
                    # Check infrastructure mapping
                    infra_info = get_account_info(sender_addr)
                    if infra_info:
                        sender_info['is_known'] = True
                        sender_info['label'] = infra_info.get('name', 'Infrastructure')
                        sender_info['risk_level'] = infra_info.get('risk_level', 'neutral')  # NEW: get actual risk level
                    else:
                        # Check CEX info
                        cex_info = get_cex_info(sender_addr)
                        if cex_info:
                            sender_info['is_known'] = True
                            sender_info['label'] = cex_info.get('name', 'CEX')
                            sender_info['risk_level'] = cex_info.get('risk_level', 'high')

                enriched_senders.append(sender_info)

            # Sort: known accounts first (by amount), then unknown (by amount)
            known_senders = sorted([s for s in enriched_senders if s['is_known']],
                                 key=lambda x: x['amount_to_funder'], reverse=True)
            unknown_senders = sorted([s for s in enriched_senders if not s['is_known']],
                                    key=lambda x: x['amount_to_funder'], reverse=True)
            sorted_senders = known_senders + unknown_senders

            funder_info = {
                'funder_address': funder_addr,
                'funder_type': funder_type,
                'funder_label': funder_label,
                'total_to_creator': funder_total,
                'sender_count': len(sorted_senders),
                'known_sender_count': len(known_senders),
                'senders': sorted_senders
            }
            network_3tier.append(funder_info)

        # Step 3: Get creator info
        cursor.execute("""
            SELECT
                earliest_tx_creator,
                risk_level,
                rug_probability,
                market_cap_highest,
                created_at
            FROM token_analysis
            WHERE earliest_tx_creator = ?
            LIMIT 1
        """, (creator_address,))

        creator_info = cursor.fetchone()

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'creator_info': {
                'risk_level': creator_info['risk_level'] if creator_info else 'UNKNOWN',
                'rug_probability': creator_info['rug_probability'] if creator_info else 0,
                'market_cap_highest': creator_info['market_cap_highest'] if creator_info else 0,
                'created_at': creator_info['created_at'] if creator_info else None
            } if creator_info else None,
            'total_funders': len(funders),
            'total_senders': sum(len(f['senders']) for f in network_3tier),
            'network_tiers': network_3tier
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-funder-extraction-status/<creator_address>')
def api_creator_funder_extraction_status(creator_address: str):
    """Check if funder extraction is complete for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Check if any funders for this creator have been analyzed
        cursor.execute("""
            SELECT
                COUNT(*) as total_funders,
                SUM(CASE WHEN last_analyzed IS NOT NULL THEN 1 ELSE 0 END) as analyzed_funders,
                MAX(last_analyzed) as last_analyzed_at
            FROM creator_funders
            WHERE creator_address = ?
        """, (creator_address,))

        result = cursor.fetchone()
        conn.close()

        if result['total_funders'] == 0:
            return jsonify({
                'creator_address': creator_address,
                'is_complete': False,
                'status': 'no_funders',
                'message': 'No funders found for this creator'
            })

        # Extraction is complete if all funders have been analyzed
        is_complete = result['analyzed_funders'] == result['total_funders'] and result['analyzed_funders'] > 0

        return jsonify({
            'creator_address': creator_address,
            'is_complete': is_complete,
            'status': 'complete' if is_complete else 'pending',
            'analyzed_funders': result['analyzed_funders'],
            'total_funders': result['total_funders'],
            'last_analyzed_at': result['last_analyzed_at']
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/coordinated-funder-analysis/<creator_address>')
def api_coordinated_funder_analysis(creator_address: str):
    """Get coordinated funder analysis results for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Check creator_networks table for coordinated funding results
        cursor.execute("""
            SELECT
                creator_address,
                connected_creators,
                shared_destinations,
                network_size,
                network_risk_level,
                detected_at,
                updated_at
            FROM creator_networks
            WHERE creator_address = ?
        """, (creator_address,))

        result = cursor.fetchone()

        if not result:
            conn.close()
            return jsonify({
                'creator_address': creator_address,
                'status': 'not_analyzed',
                'message': 'Coordinated funder analysis not yet performed for this creator'
            }), 404

        # Parse JSON arrays
        import json
        connected_creators = json.loads(result['connected_creators']) if result['connected_creators'] else []
        shared_destinations = json.loads(result['shared_destinations']) if result['shared_destinations'] else []

        # Get more details about connected creators
        connected_creator_details = []
        for cc_addr in connected_creators[:10]:  # Limit to 10 for performance
            cursor.execute("""
                SELECT
                    earliest_tx_creator,
                    risk_level,
                    rug_probability,
                    market_cap_highest,
                    created_at
                FROM token_analysis
                WHERE earliest_tx_creator = ?
                LIMIT 1
            """, (cc_addr,))

            cc_info = cursor.fetchone()
            if cc_info:
                connected_creator_details.append({
                    'creator_address': cc_addr,
                    'risk_level': cc_info['risk_level'],
                    'rug_probability': cc_info['rug_probability'],
                    'market_cap_highest': cc_info['market_cap_highest'],
                    'created_at': cc_info['created_at']
                })

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'status': 'analyzed',
            'network_size': result['network_size'],
            'network_risk_level': result['network_risk_level'],
            'connected_creators_count': len(connected_creators),
            'shared_destinations_count': len(shared_destinations),
            'connected_creators': connected_creator_details,
            'shared_destinations': shared_destinations[:20],  # Limit to 20
            'detected_at': result['detected_at'],
            'updated_at': result['updated_at']
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-sol-ledger/<creator_address>')
def api_creator_sol_ledger(creator_address: str):
    """Get recent SOL transactions for a creator - DEPRECATED
    
    CreatorWatchManager has been removed. Use /api/creator-outgoing-analysis/<creator_address>
    for updated transaction analysis.
    """
    return jsonify({'error': 'This endpoint is deprecated. Use /api/creator-outgoing-analysis/<creator_address> instead'}), 410


@app.route('/api/transaction/<signature>')
def api_transaction(signature: str):
    """Fetch transaction details from Solana RPC"""
    try:
        import aiohttp
        import asyncio
        from src.metrics.rpc_metrics_recorder import record_request

        async def fetch_tx():
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post("https://api.mainnet-beta.solana.com", json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    data = await resp.json()
                    return data

        # Run async function
        data = asyncio.run(fetch_tx())

        # Record RPC call
        record_request(
            section="transaction_lookup",
            provider="solana",
            method="getTransaction",
            status_code=200 if data.get("result") else 400,
            latency_ms=0,  # Already completed
            source_file="main"
        )

        # Check for RPC errors
        if data.get("error"):
            return jsonify({'error': f"RPC Error: {data['error'].get('message', 'Unknown error')}"}), 400

        if not data.get("result"):
            return jsonify({'error': 'Transaction not found'}), 404

        tx = data["result"]

        # Extract account keys
        try:
            message = tx.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            return jsonify({
                'signature': signature,
                'account_keys': account_keys,
                'success': True
            })
        except Exception as e:
            return jsonify({'error': f'Failed to parse transaction: {str(e)}'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-cluster/<creator_address>')
def api_creator_cluster(creator_address: str):
    """Get wallet cluster data for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get cluster size and hop breakdown
        cursor.execute("""
            SELECT
                COUNT(*) as total_wallets,
                SUM(CASE WHEN hop = 0 THEN 1 ELSE 0 END) as hop0_count,
                SUM(CASE WHEN hop = 1 THEN 1 ELSE 0 END) as hop1_count,
                SUM(CASE WHEN hop = 2 THEN 1 ELSE 0 END) as hop2_count,
                AVG(confidence) as avg_confidence
            FROM wallet_cluster_nodes
            WHERE root_creator = ?
        """, (creator_address,))

        cluster_row = cursor.fetchone()

        # Get token count for this creator
        cursor.execute("""
            SELECT COUNT(*) as token_count
            FROM token_analysis
            WHERE earliest_tx_creator = ?
        """, (creator_address,))

        token_row = cursor.fetchone()
        conn.close()

        return jsonify({
            'creator': creator_address,
            'cluster_size': cluster_row['total_wallets'] if cluster_row and cluster_row['total_wallets'] else 0,
            'hop0': cluster_row['hop0_count'] if cluster_row and cluster_row['hop0_count'] else 0,
            'hop1': cluster_row['hop1_count'] if cluster_row and cluster_row['hop1_count'] else 0,
            'hop2': cluster_row['hop2_count'] if cluster_row and cluster_row['hop2_count'] else 0,
            'token_count': token_row['token_count'] if token_row else 0,
            'avg_confidence': round(cluster_row['avg_confidence'], 2) if cluster_row and cluster_row['avg_confidence'] else 0
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creators-batch', methods=['POST'])
def api_creators_batch():
    """Get creator enrichment data for multiple creators in one batch call"""
    creator_addresses = request.json.get('creators', []) if request.json else []
    if not creator_addresses:
        return jsonify({})

    try:
        from src.utils.infra_mapping import CEX_ACCOUNTS, INFRASTRUCTURE_ACCOUNTS
        
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Token counts per creator
        placeholders = ','.join('?' * len(creator_addresses))
        cursor.execute(f"""
            SELECT earliest_tx_creator, COUNT(*) as token_count
            FROM token_analysis
            WHERE earliest_tx_creator IN ({placeholders})
            GROUP BY earliest_tx_creator
        """, creator_addresses)
        token_counts = {row['earliest_tx_creator']: row['token_count'] for row in cursor.fetchall()}

        # Funding data per creator (INBOUND only)
        cursor.execute(f"""
            SELECT
                creator_address,
                COUNT(*) as sources,
                SUM(amount_sol) as total_sol
            FROM creator_sol_flows
            WHERE creator_address IN ({placeholders}) AND flow_type = 'INBOUND'
            GROUP BY creator_address
        """, creator_addresses)
        funding_data = {}
        for row in cursor.fetchall():
            funding_data[row['creator_address']] = {
                'sources': row['sources'],
                'sol': row['total_sol'] if row['total_sol'] else 0
            }

        # Wallet cluster size per creator
        cursor.execute(f"""
            SELECT
                root_creator,
                COUNT(*) as total_wallets,
                SUM(CASE WHEN hop = 0 THEN 1 ELSE 0 END) as hop0,
                SUM(CASE WHEN hop = 1 THEN 1 ELSE 0 END) as hop1
            FROM wallet_cluster_nodes
            WHERE root_creator IN ({placeholders})
            GROUP BY root_creator
        """, creator_addresses)
        cluster_data = {}
        for row in cursor.fetchall():
            cluster_data[row['root_creator']] = {
                'size': row['total_wallets'],
                'hop0': row['hop0'],
                'hop1': row['hop1']
            }

        # Blocklist status
        cursor.execute(f"""
            SELECT DISTINCT earliest_tx_creator, creator_is_blocked
            FROM token_analysis
            WHERE earliest_tx_creator IN ({placeholders})
        """, creator_addresses)
        blocked_data = {row['earliest_tx_creator']: bool(row['creator_is_blocked']) for row in cursor.fetchall()}

        # Top funders per creator (for infrastructure tagging)
        cursor.execute(f"""
            SELECT
                creator_address,
                funder_address,
                amount_sol,
                is_cex,
                cex_exchange,
                cex_type
            FROM creator_funders
            WHERE creator_address IN ({placeholders})
            AND funder_address != creator_address
            ORDER BY amount_sol DESC
        """, creator_addresses)
        funders_data = {}
        for row in cursor.fetchall():
            creator = row['creator_address']
            if creator not in funders_data:
                funders_data[creator] = []

            funder_addr = row['funder_address']

            # Check if funder is in our known CEX or Infrastructure mappings
            is_cex = bool(row['is_cex'])
            cex_exchange = row['cex_exchange']
            cex_type = row['cex_type']
            display_name = None

            # Check live mapping for enriched display names (both new and existing CEX entries)
            if funder_addr in CEX_ACCOUNTS:
                is_cex = True
                cex_info = CEX_ACCOUNTS[funder_addr]
                # Use 'name' field for display (e.g., "Bybit Wallet 10"), fallback to exchange
                display_name = cex_info.get('name')
                cex_exchange = cex_info.get('exchange', cex_info.get('name', 'CEX'))
                cex_type = None  # Set to None since name field already includes type

            funders_data[creator].append({
                'address': funder_addr,
                'amount_sol': row['amount_sol'],
                'is_cex': is_cex,
                'cex_exchange': cex_exchange,
                'cex_type': cex_type,
                'display_name': display_name
            })

        # Creator service tags from creator_tags table (domain/infrastructure tags)
        cursor.execute(f"""
            SELECT creator_address, tag, description, amount_sol
            FROM creator_tags
            WHERE creator_address IN ({placeholders})
        """, creator_addresses)
        tags_data = {}
        for row in cursor.fetchall():
            creator = row['creator_address']
            if creator not in tags_data:
                tags_data[creator] = []
            tags_data[creator].append({
                'tag': row['tag'],
                'description': row['description'],
                'amount_sol': row['amount_sol']
            })

        # Creator service tags from creator_service_history (uses_jitotip, uses_meteora, etc.)
        cursor.execute(f"""
            SELECT DISTINCT creator_address, tag, 'Service tag' as description, NULL as amount_sol
            FROM creator_service_history
            WHERE creator_address IN ({placeholders})
        """, creator_addresses)
        for row in cursor.fetchall():
            creator = row['creator_address']
            if creator not in tags_data:
                tags_data[creator] = []

            # Build description for service tags
            tag_descriptions = {
                'uses_jitotip': 'Uses Jito tips on CREATE transaction',
                'uses_jitotip_other': 'Uses Jito MEV tips on transactions',
                'uses_meteora': 'Uses Meteora DLMM liquidity',
                'uses_debridge': 'Uses deBridge cross-chain transfers',
                'uses_axiom': 'Uses Axiom for verification'
            }

            tag_name = row['tag']
            description = tag_descriptions.get(tag_name, f'Uses {tag_name.replace("uses_", "")}')

            # Check if this tag already exists (avoid duplicates)
            if not any(t['tag'] == tag_name for t in tags_data[creator]):
                tags_data[creator].append({
                    'tag': tag_name,
                    'description': description,
                    'amount_sol': None
                })

        # Creator INFRA/CEX interactions (from creator_infra_interactions table)
        try:
            cursor.execute(f"""
                SELECT creator_address, account_type, account_name
                FROM creator_infra_interactions
                WHERE creator_address IN ({placeholders})
                GROUP BY creator_address, account_type, account_name
            """, creator_addresses)

            for row in cursor.fetchall():
                creator = row['creator_address']
                account_type = row['account_type']
                account_name = row['account_name']

                if creator not in tags_data:
                    tags_data[creator] = []

                # Create tag for INFRA/CEX interaction
                tag_key = f"uses_{account_name.lower().replace(' ', '_').replace('-', '_')}"
                description = f'Interacted with {account_name} ({account_type.upper()})'

                # Check if this tag already exists
                if not any(t['tag'] == tag_key for t in tags_data[creator]):
                    tags_data[creator].append({
                        'tag': tag_key,
                        'description': description,
                        'amount_sol': None
                    })
        except Exception as e:
            # Table may not exist yet, that's OK
            pass

        # Multi-Funder detection for batch API
        # Only tag if funder is NOT CEX/INFRA (exclude exchanges and infrastructure)
        cursor.execute(f"""
            SELECT cf.creator_address, COUNT(DISTINCT cf.funder_address) as coordinated_count
            FROM creator_funders cf
            WHERE cf.creator_address IN ({placeholders})
            AND cf.is_cex = 0
            AND cf.funder_address IN (SELECT funder_address FROM coordinated_funders)
            GROUP BY cf.creator_address
        """, creator_addresses)
        for row in cursor.fetchall():
            creator = row['creator_address']
            if row['coordinated_count'] > 0:
                if creator not in tags_data:
                    tags_data[creator] = []
                if not any(t['tag'] == 'Multi-Funder' for t in tags_data[creator]):
                    tags_data[creator].append({
                        'tag': 'Multi-Funder',
                        'description': 'Funded by account(s) supporting multiple creators',
                        'amount_sol': None
                    })

        # Network membership is now indicated by network_name field, not a tag
        # No need for Network-Coordinator tag since all network creators show their network name

        conn.close()

        # Build response
        result = {}
        for creator in creator_addresses:
            result[creator] = {
                'token_count': token_counts.get(creator, 0),
                'inbound_sources': funding_data.get(creator, {}).get('sources', 0),
                'inbound_sol': funding_data.get(creator, {}).get('sol', 0),
                'network_size': cluster_data.get(creator, {}).get('size', 0),
                'cluster_hops': {
                    'hop0': cluster_data.get(creator, {}).get('hop0', 0),
                    'hop1': cluster_data.get(creator, {}).get('hop1', 0)
                },
                'is_blocked': blocked_data.get(creator, False),
                'funders': funders_data.get(creator, []),
                'tags': tags_data.get(creator, [])
            }

        return jsonify(result)
    except Exception as e:
        print(f"[API] Error in creators-batch: {e}")
        return jsonify({})


# Migration settings (stored in file for persistence)
SETTINGS_FILE = "migration_settings.json"

def load_migration_settings():
    """Load migration settings from file"""
    import os
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                return json.load(f)
        except:
            pass
    # Default settings
    return {
        'token_history_check': True
    }

def save_migration_settings(settings):
    """Save migration settings to file"""
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f)
    except Exception as e:
        print(f"[SETTINGS] Error saving settings: {e}")

migration_settings = load_migration_settings()


def get_migration_setting(key: str, default=True) -> bool:
    """Get a migration setting value (for use by listener)"""
    global migration_settings
    # Reload from file to get latest changes
    migration_settings = load_migration_settings()
    return migration_settings.get(key, default)


@app.route('/api/migration-settings', methods=['POST', 'GET'])
def api_migration_settings():
    """Get or update migration feature settings"""
    global migration_settings

    if request.method == 'POST':
        data = request.json or {}
        old_settings = migration_settings.copy()

        # Track which settings changed
        changes = []

        if 'token_history_check' in data:
            old_val = old_settings.get('token_history_check', True)
            new_val = bool(data['token_history_check'])
            migration_settings['token_history_check'] = new_val
            if old_val != new_val:
                changes.append(f"Token History: {('✅ ON' if old_val else '❌ OFF')} → {('✅ ON' if new_val else '❌ OFF')}")

        # Persist to file
        save_migration_settings(migration_settings)

        # Log detailed state changes
        if changes:
            for change in changes:
                print(f"[SETTINGS] TOGGLED - {change}", flush=True)

        history_state = '✅ ON' if migration_settings['token_history_check'] else '❌ OFF'
        print(f"[SETTINGS] Current State - Token History: {history_state}", flush=True)

        return jsonify({
            'status': 'updated',
            'settings': migration_settings
        })

    # GET - return current settings
    history_state = '✅ ON' if migration_settings['token_history_check'] else '❌ OFF'
    print(f"[SETTINGS] Retrieved - Token History: {history_state}", flush=True)
    return jsonify(migration_settings)


@app.route('/api/cex-wallets', methods=['GET', 'POST', 'DELETE'])
def api_cex_wallets():
    """Manage CEX wallet mappings
    
    GET: List all known CEX wallets
    POST: Add a new CEX wallet
    DELETE: Remove a CEX wallet
    """
    try:
        if request.method == 'GET':
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get all active CEX wallets
            cursor.execute("""
                SELECT cex_address, exchange_name, wallet_type, confidence_level, discovered_date, discovery_source, notes
                FROM cex_wallets
                WHERE is_active = 1
                ORDER BY exchange_name, wallet_type
            """)
            
            wallets = []
            for row in cursor.fetchall():
                wallets.append({
                    'address': row['cex_address'],
                    'exchange': row['exchange_name'],
                    'type': row['wallet_type'],
                    'confidence': row['confidence_level'],
                    'discovered': row['discovered_date'],
                    'source': row['discovery_source'],
                    'notes': row['notes']
                })
            
            conn.close()
            return jsonify({'wallets': wallets, 'total': len(wallets)})
        
        elif request.method == 'POST':
            data = request.json or {}
            
            # Validate required fields
            required = ['address', 'exchange', 'type']
            if not all(data.get(field) for field in required):
                return jsonify({'error': f'Missing required fields: {required}'}), 400
            
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            
            try:
                cursor.execute("""
                    INSERT OR REPLACE INTO cex_wallets
                    (cex_address, exchange_name, wallet_type, confidence_level, discovery_source, notes, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                """, (
                    data['address'],
                    data['exchange'],
                    data['type'],
                    data.get('confidence', 95),
                    data.get('source', 'Manual'),
                    data.get('notes', '')
                ))
                conn.commit()
                print(f"[CEX] ✅ Added {data['exchange']} wallet: {data['address'][:16]}...", flush=True)
                return jsonify({'status': 'added', 'address': data['address']}), 201
            finally:
                conn.close()
        
        elif request.method == 'DELETE':
            data = request.json or {}
            address = data.get('address')
            
            if not address:
                return jsonify({'error': 'Missing address parameter'}), 400
            
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            
            try:
                cursor.execute("UPDATE cex_wallets SET is_active = 0 WHERE cex_address = ?", (address,))
                conn.commit()
                
                if cursor.rowcount > 0:
                    print(f"[CEX] ✅ Deactivated wallet: {address[:16]}...", flush=True)
                    return jsonify({'status': 'deleted', 'address': address})
                else:
                    return jsonify({'error': 'Wallet not found'}), 404
            finally:
                conn.close()
    
    except Exception as e:
        print(f"[CEX_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/listener-settings', methods=['GET', 'POST'])
def api_listener_settings():
    """Get or update listener settings (token launch listening, auto funder extraction)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if request.method == 'POST':
            from datetime import datetime
            data = request.json or {}
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Update listen_to_launches setting
            if 'listen_to_launches' in data:
                old_val = None
                try:
                    cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('listen_to_launches',))
                    row = cursor.fetchone()
                    if row:
                        old_val = row['setting_value'] == 'true'
                except Exception as e:
                    print(f"[LISTENER] Error reading old listen_to_launches: {e}", flush=True)

                new_val = 'true' if data['listen_to_launches'] else 'false'
                try:
                    cursor.execute("""
                        UPDATE listener_settings
                        SET setting_value = ?, last_updated = ?
                        WHERE setting_key = ?
                    """, (new_val, now, 'listen_to_launches'))
                    rows_affected = cursor.rowcount
                    print(f"[LISTENER] Executed update for listen_to_launches = {new_val} (rows affected: {rows_affected})", flush=True)
                except Exception as e:
                    print(f"[LISTENER] Error executing update for listen_to_launches: {e}", flush=True)
                    import traceback
                    traceback.print_exc()

                if old_val is not None and old_val != data['listen_to_launches']:
                    status = '✅ ON' if data['listen_to_launches'] else '❌ OFF'
                    print(f"[LISTENER] TOGGLED - Token Launch: {status}", flush=True)

            # Update auto_extract_funders setting
            if 'auto_extract_funders' in data:
                old_val = None
                try:
                    cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('auto_extract_funders',))
                    row = cursor.fetchone()
                    if row:
                        old_val = row['setting_value'] == 'true'
                except Exception as e:
                    print(f"[LISTENER] Error reading old auto_extract_funders: {e}", flush=True)

                new_val = 'true' if data['auto_extract_funders'] else 'false'
                try:
                    cursor.execute("""
                        UPDATE listener_settings
                        SET setting_value = ?, last_updated = ?
                        WHERE setting_key = ?
                    """, (new_val, now, 'auto_extract_funders'))
                    print(f"[LISTENER] Executed update for auto_extract_funders = {new_val}", flush=True)
                except Exception as e:
                    print(f"[LISTENER] Error executing update for auto_extract_funders: {e}", flush=True)

                if old_val is not None and old_val != data['auto_extract_funders']:
                    status = '✅ ON' if data['auto_extract_funders'] else '❌ OFF'
                    print(f"[LISTENER] TOGGLED - Auto Extract Funders: {status}", flush=True)

            try:
                conn.commit()
                print(f"[LISTENER] Database commit successful", flush=True)
            except Exception as e:
                print(f"[LISTENER] ERROR - Database commit failed: {e}", flush=True)

            # Verify the update worked
            cursor.execute("SELECT setting_value, last_updated FROM listener_settings WHERE setting_key = ?", ('listen_to_launches',))
            verify_row = cursor.fetchone()
            if verify_row:
                print(f"[LISTENER] VERIFY: listen_to_launches = {verify_row[0]}, updated at {verify_row[1]}", flush=True)

            # Get current settings
            cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('listen_to_launches',))
            row = cursor.fetchone()
            listen_launches = row['setting_value'] == 'true' if row else True

            cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('auto_extract_funders',))
            row = cursor.fetchone()
            auto_extract_funders = row['setting_value'] == 'true' if row else False

            conn.close()
            return jsonify({
                'status': 'updated',
                'listen_to_launches': listen_launches,
                'auto_extract_funders': auto_extract_funders
            })

        else:  # GET
            cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('listen_to_launches',))
            row = cursor.fetchone()
            listen_launches = row['setting_value'] == 'true' if row else True

            cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('auto_extract_funders',))
            row = cursor.fetchone()
            auto_extract_funders = row['setting_value'] == 'true' if row else False

            conn.close()
            return jsonify({
                'listen_to_launches': listen_launches,
                'auto_extract_funders': auto_extract_funders
            })

    except Exception as e:
        print(f"[LISTENER_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


# --- Unified Recipient Tracking Endpoints ---

@app.route('/api/creator-recipients/<creator_address>')
def api_creator_recipients(creator_address: str):
    """Get all recipient links for a creator (unified tracking)"""
    try:
        from unified_recipient_tracker import UnifiedRecipientTracker

        tracker = UnifiedRecipientTracker()
        links = tracker.get_recipient_links_for_creator(creator_address)

        recipients = []
        for link in links:
            recipients.append({
                'recipient_address': link.recipient_address,
                'total_sol_sent': link.total_sol_sent,
                'transfer_count': link.transfer_count,
                'confidence': link.confidence,
                'source': link.source,
                'is_cex': link.is_cex,
                'cex_exchange': link.cex_exchange,
                'is_suspicious': link.is_suspicious
            })

        return jsonify({
            'creator_address': creator_address,
            'recipients': recipients,
            'total_recipients': len(recipients),
            'total_sol_sent': sum(r['total_sol_sent'] for r in recipients)
        })

    except ImportError:
        return jsonify({'error': 'Unified recipient tracker not available'}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-cross-references/<creator_address>')
def api_creator_cross_references(creator_address: str):
    """Find other creators that share recipient addresses with this creator"""
    try:
        from unified_recipient_tracker import UnifiedRecipientTracker

        tracker = UnifiedRecipientTracker()
        shared = tracker.find_shared_recipients(creator_address)

        shared_recipients = []
        for recipient, other_creators in shared.items():
            shared_recipients.append({
                'recipient_address': recipient,
                'other_creators': other_creators,
                'creator_count': len(other_creators)
            })

        # Sort by creator count (most suspicious first)
        shared_recipients.sort(key=lambda x: x['creator_count'], reverse=True)

        return jsonify({
            'creator_address': creator_address,
            'shared_recipients': shared_recipients,
            'total_shared': len(shared_recipients),
            'cross_creator_links': sum(r['creator_count'] for r in shared_recipients)
        })

    except ImportError:
        return jsonify({'error': 'Unified recipient tracker not available'}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500@app.route('/api/creator-funding-history/<creator_address>')


@app.route('/api/creator-cross-references-directional/<creator_address>')
def api_creator_cross_references_directional(creator_address: str):
    """Get cross-creator references with direction labels (INBOUND/OUTBOUND)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # OUTBOUND: Recipients this creator shares with other creators
        outbound_refs = []
        try:
            from unified_recipient_tracker import UnifiedRecipientTracker
            tracker = UnifiedRecipientTracker()
            shared = tracker.find_shared_recipients(creator_address)
            for recipient, other_creators in shared.items():
                if other_creators:
                    outbound_refs.append({
                        'address': recipient,
                        'other_creators': other_creators[:10],  # Limit display
                        'creator_count': len(other_creators),
                        'direction': 'OUTBOUND',
                        'type': 'shared_recipient',
                        'description': f'Sends SOL to {recipient[:8]}... which receives from {len(other_creators)} other creator(s)'
                    })
            outbound_refs.sort(key=lambda x: x['creator_count'], reverse=True)
        except Exception as e:
            outbound_refs = []

        # INBOUND: Funders that also fund other creators
        inbound_refs = []
        try:
            cursor.execute("""
                SELECT DISTINCT cf.funder_address, COUNT(DISTINCT cf2.creator_address) as other_creator_count
                FROM creator_funders cf
                JOIN creator_funders cf2 ON cf.funder_address = cf2.funder_address
                WHERE cf.creator_address = ?
                AND cf2.creator_address != ?
                GROUP BY cf.funder_address
                ORDER BY other_creator_count DESC
                LIMIT 50
            """, (creator_address, creator_address))

            for row in cursor.fetchall():
                funder_addr, other_count = row
                cursor.execute("""
                    SELECT DISTINCT creator_address FROM creator_funders
                    WHERE funder_address = ? AND creator_address != ?
                    LIMIT 10
                """, (funder_addr, creator_address))
                other_creators = [r[0] for r in cursor.fetchall()]

                if other_creators:
                    inbound_refs.append({
                        'address': funder_addr,
                        'other_creators': other_creators,
                        'creator_count': len(other_creators),
                        'direction': 'INBOUND',
                        'type': 'shared_funder',
                        'description': f'Funder {funder_addr[:8]}... funds this creator AND {len(other_creators)} other creator(s)'
                    })

            inbound_refs.sort(key=lambda x: x['creator_count'], reverse=True)
        except Exception as e:
            inbound_refs = []

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'cross_references': {
                'inbound': inbound_refs,
                'outbound': outbound_refs,
                'total_inbound_links': len(inbound_refs),
                'total_outbound_links': len(outbound_refs),
                'total_cross_creator_links': len(inbound_refs) + len(outbound_refs)
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
def api_creator_funding_history(creator_address: str):
    """Get unified funding history for a creator (both incoming and outgoing transfers)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get incoming funders
        cursor.execute("""
            SELECT
                creator_address,
                funder_address as address,
                amount_sol,
                first_detected_at as timestamp,
                'incoming' as direction,
                is_cex,
                cex_exchange
            FROM creator_funders
            WHERE creator_address = ?
            ORDER BY first_detected_at DESC
        """, (creator_address,))

        incoming = [dict(row) for row in cursor.fetchall()]

        # Get outgoing recipients
        cursor.execute("""
            SELECT
                creator_address,
                recipient_address as address,
                SUM(amount_sol) as amount_sol,
                MAX(block_time) as block_timestamp,
                'outgoing' as direction,
                is_cex,
                cex_exchange
            FROM creator_outgoing_transfers
            WHERE creator_address = ?
            GROUP BY recipient_address
            ORDER BY MAX(block_time) DESC
        """, (creator_address,))

        outgoing = [dict(row) for row in cursor.fetchall()]

        # Combine and sort by timestamp
        all_transfers = incoming + outgoing
        all_transfers.sort(key=lambda x: x.get('timestamp') or x.get('block_timestamp') or '9999-12-31', reverse=True)

        conn.close()

        # Enrich transfers with infrastructure/CEX information
        enriched_transfers = []
        for transfer in all_transfers:
            transfer_copy = transfer.copy()
            address = transfer.get('address')

            if address:
                from src.utils.infra_mapping import get_account_info, get_cex_info

                # Check infrastructure first
                infra_info = get_account_info(address)
                if infra_info:
                    transfer_copy['display_name'] = infra_info.get('name')
                    transfer_copy['category'] = infra_info.get('category')
                    transfer_copy['is_infrastructure'] = True
                else:
                    # Check CEX if not infrastructure
                    cex_info = get_cex_info(address)
                    if cex_info:
                        transfer_copy['display_name'] = cex_info.get('name')
                        transfer_copy['category'] = cex_info.get('category')
                        transfer_copy['is_infrastructure'] = False
                    else:
                        transfer_copy['display_name'] = None
                        transfer_copy['category'] = None
                        transfer_copy['is_infrastructure'] = False
            else:
                transfer_copy['display_name'] = None
                transfer_copy['category'] = None
                transfer_copy['is_infrastructure'] = False

            enriched_transfers.append(transfer_copy)

        return jsonify({
            'creator_address': creator_address,
            'transfers': enriched_transfers,
            'incoming_count': len(incoming),
            'outgoing_count': len(outgoing),
            'total_transfers': len(enriched_transfers),
            'total_incoming_sol': sum(t.get('amount_sol', 0) for t in incoming),
            'total_outgoing_sol': sum(t.get('amount_sol', 0) for t in outgoing)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/api/multi-creator-funders')
def api_multi_creator_funders():
    """Get funders that are funding multiple token creators (potential coordination risk)

    Shows all multi-creator funders with flags for infrastructure/CEX accounts.
    """
    try:
        from src.utils.infra_mapping import get_account_info, get_cex_info, get_pumpfun_creator_info, get_suspicious_wallet_info

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get funders funding multiple creators
        cursor.execute("""
            SELECT
                funder_address,
                COUNT(DISTINCT creator_address) as creator_count,
                COUNT(*) as funding_record_count,
                SUM(amount_sol) as total_sol_sent,
                MIN(first_detected_at) as first_funding_at,
                MAX(first_detected_at) as last_funding_at,
                MAX(is_cex) as is_cex_flag
            FROM creator_funders
            GROUP BY funder_address
            HAVING COUNT(DISTINCT creator_address) > 1
            ORDER BY creator_count DESC, total_sol_sent DESC
        """)

        all_multi_funders = [dict(row) for row in cursor.fetchall()]

        # Classify and tag infrastructure/CEX accounts
        multi_funders = []
        suspicious_multi_funders = []

        for funder in all_multi_funders:
            funder_address = funder['funder_address']
            funder_data = dict(funder)
            funder_data['is_infrastructure'] = False
            funder_data['is_cex_account'] = False
            funder_data['account_info'] = None
            funder_data['networks'] = []

            # Check if already marked as CEX in database
            if funder['is_cex_flag']:
                funder_data['is_cex_account'] = True

            # Check if it's a known infrastructure account
            infra_info = get_account_info(funder_address)
            if infra_info:
                funder_data['is_infrastructure'] = True
                funder_data['account_info'] = infra_info

            # Check if it's a known CEX wallet
            cex_info = get_cex_info(funder_address)
            if cex_info:
                funder_data['is_cex_account'] = True
                funder_data['account_info'] = cex_info

            # Check if it's a PumpFun token creator (don't exclude from suspicious)
            pumpfun_info = get_pumpfun_creator_info(funder_address)
            if pumpfun_info and not funder_data['account_info']:
                funder_data['account_info'] = pumpfun_info

            # Check if it's a suspicious wallet type (don't exclude from suspicious)
            suspicious_wallet_info = get_suspicious_wallet_info(funder_address)
            if suspicious_wallet_info and not funder_data['account_info']:
                funder_data['account_info'] = suspicious_wallet_info

            # Get networks this funder belongs to (both shared-token and single-funder)
            cursor.execute("""
                SELECT
                    fn.network_id,
                    fn.network_name,
                    COALESCE(fn.network_type, 'shared') as network_type,
                    COUNT(DISTINCT fnt.mint) as token_count
                FROM funding_network_members fnm
                JOIN funding_networks fn ON fnm.network_id = fn.network_id
                LEFT JOIN funding_network_shared_tokens fnt ON fn.network_id = fnt.network_id
                WHERE fnm.funder_address = ?
                GROUP BY fn.network_id, fn.network_name, fn.network_type
                ORDER BY fn.network_id
            """, (funder_address,))

            networks = [dict(row) for row in cursor.fetchall()]
            funder_data['networks'] = networks

            # Add to appropriate list
            multi_funders.append(funder_data)

            # Suspicious = neither infrastructure nor CEX
            if not (funder_data['is_infrastructure'] or funder_data['is_cex_account']):
                suspicious_multi_funders.append(funder_data)

        # Get statistics
        cursor.execute("""
            SELECT
                COUNT(DISTINCT funder_address) as total_funders,
                COUNT(DISTINCT CASE WHEN (SELECT COUNT(DISTINCT creator_address) FROM creator_funders cf2 WHERE cf2.funder_address = creator_funders.funder_address) > 1 THEN funder_address END) as multi_creator_funders
            FROM creator_funders
        """)

        stats = dict(cursor.fetchone())

        conn.close()

        return jsonify({
            'multi_creator_funders': multi_funders,
            'suspicious_only': suspicious_multi_funders,
            'statistics': {
                'total_funders': stats['total_funders'],
                'funding_multiple_creators': len(multi_funders),
                'suspicious_funders': len(suspicious_multi_funders),
                'infra_funders': len([f for f in multi_funders if f['is_infrastructure']]),
                'cex_funders': len([f for f in multi_funders if f['is_cex_account']]),
                'funding_single_creator': stats['total_funders'] - len(multi_funders),
                'percentage_multi_creator': (len(multi_funders) / stats['total_funders'] * 100) if stats['total_funders'] > 0 else 0,
                'coordination_risk': 'HIGH' if len(suspicious_multi_funders) > 0 else 'MEDIUM' if len(multi_funders) > 0 else 'LOW'
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# HTML template for coordinated funders page
coordinated_funders_html = '''
<html>
<head>
    <title>Coordinated Funders</title>
    <style>
        body {
            background: #0f0f1e;
            color: var(--text-primary);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
            padding: 40px;
            margin: 0;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: rgba(124, 58, 237, 0.05);
            border: 1px solid rgba(124, 58, 237, 0.2);
            padding: 40px;
            border-radius: 12px;
        }
        h1 {
            color: var(--primary);
            margin-top: 0;
            font-size: 32px;
        }
        .section {
            margin: 30px 0;
            padding: 20px;
            background: rgba(0, 0, 0, 0.3);
            border-radius: 8px;
            border-left: 3px solid var(--primary);
        }
        .button {
            background: var(--primary);
            color: var(--text-light);
            padding: 12px 24px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            margin-top: 20px;
            transition: background 0.3s;
        }
        .button:hover {
            background: var(--primary);
        }
        ul {
            line-height: 1.8;
            color: var(--text-secondary);
        }
        li {
            margin-bottom: 8px;
        }
        .status {
            background: rgba(74, 222, 128, 0.1);
            border: 1px solid #4ade80;
            color: var(--color-low);
            padding: 15px;
            border-radius: 6px;
            margin-bottom: 20px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔗 Coordinated Funders Analysis</h1>

        <div class="status">
            ✅ Use the <strong>🔗 Networks</strong> tab in the main dashboard for full coordinated funders analysis
        </div>

        <div class="section">
            <h2 style="color: var(--color-high); margin-top: 0;">What is Coordinated Funders?</h2>
            <p>Coordinated funders are wallets that fund multiple creators, indicating potential coordination or network relationships. This analysis helps identify:</p>
            <ul>
                <li><strong>Funding Networks:</strong> Groups of funders working together</li>
                <li><strong>Coordinated Activity:</strong> Suspicious patterns of multi-creator funding</li>
                <li><strong>Risk Clusters:</strong> Related accounts with elevated risk profiles</li>
                <li><strong>Funding Flow:</strong> How SOL moves through the network</li>
            </ul>
        </div>

        <div class="section">
            <h2 style="color: var(--color-none); margin-top: 0;">Features in Networks Tab</h2>
            <ul>
                <li>View all funding networks in the cluster</li>
                <li>See root operators and their relationships</li>
                <li>Track example flows: Sender → Funder → Creator → Token</li>
                <li>Analyze funding amounts and patterns</li>
                <li>Identify suspicious coordination patterns</li>
                <li>Real-time network analysis</li>
            </ul>
        </div>

        <div class="section">
            <h2 style="color: var(--accent-purple); margin-top: 0;">How to Use</h2>
            <ol style="line-height: 2;">
                <li>Click on a Super-Cluster in the main dashboard</li>
                <li>Navigate to the <strong>Networks</strong> tab</li>
                <li>View all funding networks and their members</li>
                <li>Click on root operators to see example flows</li>
                <li>Analyze sender patterns and funding relationships</li>
            </ol>
        </div>
    </div>
</body>
</html>
'''


@app.route('/coordinated-funders')
def coordinated_funders_view():
    """Serve a full webview for coordinated funders analysis"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get funders funding multiple creators
        cursor.execute("""
            SELECT
                funder_address,
                COUNT(DISTINCT creator_address) as creator_count,
                SUM(amount_sol) as total_sol_sent
            FROM creator_funders
            GROUP BY funder_address
            HAVING COUNT(DISTINCT creator_address) > 1
            ORDER BY creator_count DESC, total_sol_sent DESC
            LIMIT 100
        """)

        multi_funders = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Build HTML response
        html_rows = ""
        for funder in multi_funders:
            html_rows += f"""
            <tr>
                <td style="padding: 12px; text-align: center;">
                    <input type="checkbox" class="funder-checkbox" value="{funder['funder_address']}" style="width: 18px; height: 18px; cursor: pointer;">
                </td>
                <td style="padding: 12px; font-family: monospace; font-size: 11px; word-break: break-all;">
                    <a href="/funding-hub/{funder['funder_address']}" style="color: var(--color-cyan); text-decoration: none;">
                        {funder['funder_address']}
                    </a>
                </td>
                <td style="padding: 12px; text-align: right; color: var(--color-yellow); font-weight: 600;">{funder['creator_count']}</td>
                <td style="padding: 12px; text-align: right; color: var(--color-green); font-weight: 600;">{funder['total_sol_sent']:.2f} SOL</td>
            </tr>
            """

        return render_template(
            'coordinated_funders.html',
            active_page='coordinated-funders',
            funders=multi_funders,
        )

    except Exception as e:
        return f"<html><body style='background:#0a0a0e; color: red;'><h1>Error</h1><p>{str(e)}</p></body></html>", 500


@app.route('/clusters')
def clusters_dashboard():
    """Serve a full webview for cross-funding clusters"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all clusters with token stats
        cursor.execute("""
            SELECT
                fn.cluster_id,
                COUNT(DISTINCT fn.primary_funder) as funder_count,
                MAX(fn.network_size) as network_size,
                SUM(fn.total_volume_sol) as total_volume_sol,
                MAX(fn.creators_served) as creators_served_json,
                COUNT(DISTINCT ta.mint) as token_count,
                ROUND(AVG(ta.rug_probability), 3) as avg_rug_probability,
                SUM(CASE WHEN ta.rug_indicator = 'rug' THEN 1 ELSE 0 END) as rug_count
            FROM funder_networks fn
            LEFT JOIN token_analysis ta ON fn.primary_funder = ta.network_funder_address
            WHERE fn.cluster_id IS NOT NULL
            GROUP BY fn.cluster_id
            ORDER BY COUNT(DISTINCT fn.primary_funder) DESC, SUM(fn.total_volume_sol) DESC
        """)

        clusters = []
        risk_multipliers = {
            'FUNDERS_14': {'multiplier': 3.0, 'label': '🚨 CRITICAL - Coordinated Network (25 non-CEX funders)', 'level': 'CRITICAL', 'name': 'NexusCerberus'},
            'FUNDERS_20': {'multiplier': 2.0, 'label': '⚠️ HIGH - Secondary Network (20 non-CEX funders)', 'level': 'HIGH', 'name': 'CrimsonRaven'},
            'FUNDERS_17': {'multiplier': 1.5, 'label': '🟡 MEDIUM - Tertiary Network (9 non-CEX funders)', 'level': 'MEDIUM', 'name': 'StellarDragon'},
            'FUNDERS_6': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'IvoryWarden'},
            'FUNDERS_10': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'OnyxRaven'},
            'FUNDERS_8': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'SilentViper'},
            'FUNDERS_16': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'PhantomWolf'},
            'FUNDERS_9': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'EtherealEagle'},
            'FUNDERS_1': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'CosmicLion'},
            'FUNDERS_11': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'PhoenixAscend'},
            'FUNDERS_13': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'ShadowNova'},
            'FUNDERS_2': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'VortexMind'},
            'FUNDERS_3': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'IceShield'},
            'FUNDERS_4': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'StormBringer'},
            'FUNDERS_5': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'NightHunter'},
            'FUNDERS_7': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'FrostByte'},
            'FUNDERS_12': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'VortexFlow'},
            'FUNDERS_15': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'IceVenom'},
            'FUNDERS_18': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'ShadowBolt'},
            'FUNDERS_19': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'VortexKing'},
        }

        total_funders = 0
        total_volume = 0.0
        total_creators = 0

        for row in cursor.fetchall():
            cluster_id = row['cluster_id']
            funder_count = int(row['funder_count'] or 0)
            volume = float(row['total_volume_sol'] or 0.0)
            total_funders += funder_count
            total_volume += volume

            # Parse creators_served JSON
            import json
            creators_count = 0
            try:
                creators = json.loads(row['creators_served_json'] or '[]')
                creators_count = len(creators) if isinstance(creators, list) else 0
            except:
                creators_count = 0

            total_creators += creators_count
            risk_info = risk_multipliers.get(cluster_id, {'multiplier': 1.0, 'label': f'Network {cluster_id}', 'level': 'CLEAN', 'name': cluster_id})

            token_count = int(row['token_count'] or 0)
            rug_prob = float(row['avg_rug_probability'] or 0.0)
            rug_count = int(row['rug_count'] or 0)

            clusters.append({
                'cluster_id': cluster_id,
                'cluster_name': risk_info.get('name', cluster_id),
                'funder_count': funder_count,
                'network_size': int(row['network_size'] or 0),
                'total_volume_sol': volume,
                'creator_count': creators_count,
                'risk_multiplier': risk_info['multiplier'],
                'risk_label': risk_info['label'],
                'risk_level': risk_info['level'],
                'token_count': token_count,
                'rug_probability': rug_prob,
                'rug_count': rug_count
            })

        conn.close()

        return render_template(
            'clusters.html',
            active_page='clusters',
            clusters=clusters,
            total_funders=total_funders,
            total_creators=total_creators,
            total_volume=total_volume,
        )

    except Exception as e:
        return f"<html><body style='background: var(--bg-dark); color: red;'><h1>Error</h1><p>{str(e)}</p></body></html>", 500

# Original coordinated_funders_view (with syntax issues):
def coordinated_funders_view_old():
    """Serve a full webview for coordinated funders analysis"""
    try:
        from src.utils.infra_mapping import get_account_info, get_cex_info, get_pumpfun_creator_info, get_suspicious_wallet_info

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get funders funding multiple creators
        cursor.execute("""
            SELECT
                funder_address,
                COUNT(DISTINCT creator_address) as creator_count,
                COUNT(*) as funding_record_count,
                SUM(amount_sol) as total_sol_sent,
                MIN(first_detected_at) as first_funding_at,
                MAX(first_detected_at) as last_funding_at,
                MAX(is_cex) as is_cex_flag
            FROM creator_funders
            GROUP BY funder_address
            HAVING COUNT(DISTINCT creator_address) > 1
            ORDER BY creator_count DESC, total_sol_sent DESC
        """)

        all_multi_funders = [dict(row) for row in cursor.fetchall()]

        # Check which funders have been analyzed (have incoming/outgoing transfer data)
        cursor.execute("""
            SELECT DISTINCT funder_address FROM funder_incoming_transfers
            UNION
            SELECT DISTINCT funder_address FROM funder_outgoing_transfers
        """)
        analyzed_funders = set(row[0] for row in cursor.fetchall())

        # Get network information for each funder (can belong to multiple networks)
        funder_networks = {}  # funder_address -> list of networks
        network_id_to_name = {}

        if all_multi_funders:
            cursor.execute("""
                SELECT
                    fnm.funder_address,
                    fn.network_id,
                    COALESCE(fn.network_type, 'shared') as network_type
                FROM funding_network_members fnm
                INNER JOIN funding_networks fn ON fnm.network_id = fn.network_id
                WHERE fnm.funder_address IN ({})
                ORDER BY fn.network_id
            """.format(','.join('?' * len(all_multi_funders))), [f['funder_address'] for f in all_multi_funders])

            # Generate random names for networks using same logic as api_funding_networks_list
            import random
            random.seed(42)  # Consistent seed so names don't change

            adjectives = ['Shadow', 'Ghost', 'Phantom', 'Silent', 'Hidden', 'Dark', 'Swift', 'Rapid',
                         'Sleek', 'Sharp', 'Cunning', 'Sly', 'Stealthy', 'Crafty', 'Clever', 'Subtle',
                         'Veiled', 'Masked', 'Cloaked', 'Whispered', 'Covert', 'Secret', 'Mystic', 'Ancient']
            nouns = ['Circle', 'Ring', 'Syndicate', 'Cabal', 'Order', 'Society', 'Collective', 'Alliance',
                    'Coalition', 'Union', 'Cartel', 'Consortium', 'Federation', 'Network', 'Nexus', 'Web',
                    'Chain', 'Echo', 'Whisper', 'Shadow', 'Phantom', 'Specter', 'Entity', 'Force']

            rows = cursor.fetchall()
            for row in rows:
                network_id = row['network_id']
                network_type = row['network_type']

                # Generate name if not already generated
                if network_id not in network_id_to_name:
                    adj = random.choice(adjectives)
                    noun = random.choice(nouns)
                    network_id_to_name[network_id] = f"{adj} {noun}"

                network_name = network_id_to_name[network_id]
                funder_addr = row['funder_address']

                # Add to list of networks for this funder
                if funder_addr not in funder_networks:
                    funder_networks[funder_addr] = []

                type_badge = ' 🎯' if network_type == 'single_funder' else ''
                funder_networks[funder_addr].append({
                    'network_id': network_id,
                    'network_name': network_name + type_badge,
                    'network_type': network_type
                })

        # Mark analysis status and networks for each funder
        for funder in all_multi_funders:
            funder['is_analyzed'] = funder['funder_address'] in analyzed_funders
            network_list = funder_networks.get(funder['funder_address'], [])
            funder['networks'] = network_list

        # Classify and tag infrastructure/CEX accounts
        suspicious_funders = []
        safe_funders = []

        for funder in all_multi_funders:
            funder_address = funder['funder_address']
            funder_data = dict(funder)
            funder_data['is_infrastructure'] = False
            funder_data['is_cex_account'] = False
            funder_data['account_name'] = None

            # Check if already marked as CEX in database
            if funder['is_cex_flag']:
                funder_data['is_cex_account'] = True

            # Check if it's a known infrastructure account
            infra_info = get_account_info(funder_address)
            if infra_info:
                funder_data['is_infrastructure'] = True
                funder_data['account_name'] = infra_info.get('name')

            # Check if it's a known CEX wallet
            cex_info = get_cex_info(funder_address)
            if cex_info:
                funder_data['is_cex_account'] = True
                funder_data['account_name'] = cex_info.get('name')

            # Classify as suspicious or safe
            if funder_data['is_infrastructure'] or funder_data['is_cex_account']:
                safe_funders.append(funder_data)
            else:
                suspicious_funders.append(funder_data)

        # Get statistics
        cursor.execute("""
            SELECT
                COUNT(DISTINCT funder_address) as total_funders,
                COUNT(DISTINCT CASE WHEN (SELECT COUNT(DISTINCT creator_address) FROM creator_funders cf2 WHERE cf2.funder_address = creator_funders.funder_address) > 1 THEN funder_address END) as multi_creator_funders
            FROM creator_funders
        """)

        stats = dict(cursor.fetchone())
        conn.close()

        # Build suspicious funders table HTML
        suspicious_html = ''
        for funder in suspicious_funders:
            start_date = funder['first_funding_at'][:10] if funder['first_funding_at'] else 'N/A'
            end_date = funder['last_funding_at'][:10] if funder['last_funding_at'] else 'N/A'
            period = start_date if start_date == end_date else f"{start_date} - {end_date}"

            # Get all networks this funder belongs to
            networks = funder.get('networks', [])
            if networks:
                network_display = ', '.join([n['network_name'] for n in networks])
            else:
                network_display = ''

            # Analysis status indicator
            analysis_badge = '✅ Analyzed' if funder['is_analyzed'] else '⏳ Pending'
            analysis_color = '#4ade80' if funder['is_analyzed'] else 'var(--color-medium)'

            # Highlight duplicate creators (high priority) - red background for high creator counts
            row_highlight = 'background: rgba(255, 0, 0, 0.08);' if funder['creator_count'] > 3 else ''

            suspicious_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05); cursor: pointer; {row_highlight}" onclick="window.location.href = '/funder-details/{funder['funder_address']}'">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: var(--color-critical); word-break: break-all;">{funder['funder_address']}</td>
                <td style="padding: 12px; color: var(--accent-cyan); font-weight: 500; font-size: 12px;">{network_display}</td>
                <td style="padding: 12px; color: var(--color-critical); font-weight: bold;">{funder['creator_count']}</td>
                <td style="padding: 12px; color: var(--color-low);">{funder['total_sol_sent']:.2f}</td>
                <td style="padding: 12px; color: var(--text-secondary);">{funder['funding_record_count']}</td>
                <td style="padding: 12px; font-size: 11px; color: {analysis_color};">{analysis_badge}</td>
                <td style="padding: 12px; font-size: 11px; color: var(--text-secondary);">{period}</td>
            </tr>
            """

        # Build safe funders table HTML
        safe_html = ''
        for funder in safe_funders:
            start_date = funder['first_funding_at'][:10] if funder['first_funding_at'] else 'N/A'
            end_date = funder['last_funding_at'][:10] if funder['last_funding_at'] else 'N/A'
            period = start_date if start_date == end_date else f"{start_date} - {end_date}"
            account_type = 'CEX' if funder['is_cex_account'] else 'INFRA'
            account_label = funder['account_name'] if funder['account_name'] else ''

            safe_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: var(--text-secondary);">{funder['funder_address']}</td>
                <td style="padding: 12px; color: var(--color-low); font-weight: 600;">{account_label}</td>
                <td style="padding: 12px; text-align: center;"><span style="background: rgba(34, 197, 94, 0.2); color: var(--color-low); padding: 3px 8px; border-radius: 3px; font-size: 11px;">{account_type}</span></td>
                <td style="padding: 12px; color: var(--text-secondary); font-weight: bold;">{funder['creator_count']}</td>
                <td style="padding: 12px; color: var(--text-secondary);">{funder['total_sol_sent']:.2f}</td>
            </tr>
            """

        html = f"""
        <html>
            <head>
                <title>Coordinated Funders Analysis</title>
                <style>
                    body {{
                        background: #0a0e27;
                        color: var(--text-primary);
                        font-family: 'Segoe UI', sans-serif;
                        margin: 0;
                        padding: 20px;
                    }}
                    .container {{
                        max-width: 1400px;
                        margin: 0 auto;
                    }}
                    h1 {{
                        color: var(--accent-cyan);
                        margin-bottom: 10px;
                    }}
                    .subtitle {{
                        color: var(--text-secondary);
                        margin-bottom: 30px;
                        font-size: 14px;
                    }}
                    .back-link {{
                        margin-bottom: 20px;
                    }}
                    .back-link a {{
                        color: var(--accent-cyan);
                        text-decoration: none;
                    }}
                    .back-link a:hover {{
                        text-decoration: underline;
                    }}
                    .stats-grid {{
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                        gap: 15px;
                        margin-bottom: 30px;
                    }}
                    .stat-box {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 20px;
                        border-radius: 8px;
                        border-left: 3px solid;
                        text-align: center;
                    }}
                    .stat-box.suspicious {{
                        border-left-color: var(--color-critical);
                    }}
                    .stat-box.safe {{
                        border-left-color: var(--color-low);
                    }}
                    .stat-label {{
                        color: var(--text-secondary);
                        font-size: 11px;
                        text-transform: uppercase;
                        margin-bottom: 10px;
                    }}
                    .stat-value {{
                        font-size: 32px;
                        font-weight: bold;
                    }}
                    .stat-box.suspicious .stat-value {{
                        color: var(--color-critical);
                    }}
                    .stat-box.safe .stat-value {{
                        color: var(--color-low);
                    }}
                    .section {{
                        background: var(--bg-secondary);
                        border-radius: 8px;
                        margin-bottom: 30px;
                        overflow: hidden;
                    }}
                    .section-title {{
                        background: var(--bg-secondary);
                        padding: 15px;
                        border-bottom: 1px solid rgba(6, 182, 212, 0.2);
                        font-weight: 600;
                        color: var(--accent-cyan);
                    }}
                    .section-content {{
                        max-height: 800px;
                        overflow-y: auto;
                    }}
                    table {{
                        width: 100%;
                        border-collapse: collapse;
                        font-size: 13px;
                    }}
                    th {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 12px;
                        text-align: left;
                        color: var(--text-secondary);
                        font-size: 11px;
                        border-bottom: 1px solid rgba(6, 182, 212, 0.2);
                    }}
                    tr:hover {{
                        background: rgba(6, 182, 212, 0.05);
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>Coordinated Funders Analysis</h1>
                    <div class="subtitle">Funders supporting multiple token creators (potential coordination risk)</div>

                    <!-- Tab Navigation -->
                    <div style="display: flex; gap: 10px; margin-bottom: 20px; border-bottom: 1px solid rgba(6, 182, 212, 0.2); padding-bottom: 15px; flex-wrap: wrap;">
                        <button onclick="switchTab('funders')" id="tab-funders" style="background: rgba(6, 182, 212, 0.2); color: var(--accent-cyan); border: 1px solid var(--accent-cyan); padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">👥 Multi-Creator Funders</button>
                        <button onclick="switchTab('senders')" id="tab-senders" style="background: transparent; color: var(--text-secondary); border: 1px solid #a0a0a0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">📤 Duplicate Senders</button>
                        <button onclick="switchTab('tokens')" id="tab-tokens" style="background: transparent; color: var(--text-secondary); border: 1px solid #a0a0a0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">🪙 Coordinated Tokens</button>
                        <button onclick="switchTab('funder-networks')" id="tab-funder-networks" style="background: transparent; color: var(--text-secondary); border: 1px solid #a0a0a0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">🔗 Funder Networks</button>
                        <button onclick="switchTab('funding-networks')" id="tab-funding-networks" style="background: transparent; color: var(--text-secondary); border: 1px solid #a0a0a0; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600;">🌐 Funding Networks</button>
                    </div>

                    <script>
                    function switchTab(tabName) {{
                        // Hide all tabs
                        document.getElementById('funders-tab').style.display = 'none';
                        document.getElementById('senders-tab').style.display = 'none';
                        document.getElementById('tokens-tab').style.display = 'none';
                        document.getElementById('funder-networks-tab').style.display = 'none';
                        document.getElementById('funding-networks-tab').style.display = 'none';

                        // Remove active state from all tabs
                        document.getElementById('tab-funders').style.background = 'transparent';
                        document.getElementById('tab-funders').style.color = '#a0a0a0';
                        document.getElementById('tab-funders').style.borderColor = '#a0a0a0';
                        document.getElementById('tab-senders').style.background = 'transparent';
                        document.getElementById('tab-senders').style.color = '#a0a0a0';
                        document.getElementById('tab-senders').style.borderColor = '#a0a0a0';
                        document.getElementById('tab-tokens').style.background = 'transparent';
                        document.getElementById('tab-tokens').style.color = '#a0a0a0';
                        document.getElementById('tab-tokens').style.borderColor = '#a0a0a0';
                        document.getElementById('tab-funder-networks').style.background = 'transparent';
                        document.getElementById('tab-funder-networks').style.color = '#a0a0a0';
                        document.getElementById('tab-funder-networks').style.borderColor = '#a0a0a0';
                        document.getElementById('tab-funding-networks').style.background = 'transparent';
                        document.getElementById('tab-funding-networks').style.color = '#a0a0a0';
                        document.getElementById('tab-funding-networks').style.borderColor = '#a0a0a0';

                        // Show selected tab
                        document.getElementById(tabName + '-tab').style.display = 'block';

                        // Set active state
                        if (tabName === 'funders') {{
                            document.getElementById('tab-funders').style.background = 'rgba(6, 182, 212, 0.2)';
                            document.getElementById('tab-funders').style.color = 'var(--accent-cyan)';
                            document.getElementById('tab-funders').style.borderColor = 'var(--accent-cyan)';
                        }} else if (tabName === 'senders') {{
                            document.getElementById('tab-senders').style.background = 'rgba(251, 191, 36, 0.2)';
                            document.getElementById('tab-senders').style.color = 'var(--color-medium)';
                            document.getElementById('tab-senders').style.borderColor = 'var(--color-medium)';
                            if (!document.getElementById('senders-content').innerHTML) {{
                                loadDuplicateSenders();
                            }}
                        }} else if (tabName === 'tokens') {{
                            document.getElementById('tab-tokens').style.background = 'rgba(34, 197, 94, 0.2)';
                            document.getElementById('tab-tokens').style.color = '#4ade80';
                            document.getElementById('tab-tokens').style.borderColor = '#4ade80';
                            if (!document.getElementById('tokens-content').innerHTML) {{
                                loadDuplicateTokens();
                            }}
                        }} else if (tabName === 'funder-networks') {{
                            document.getElementById('tab-funder-networks').style.background = 'rgba(59, 130, 246, 0.2)';
                            document.getElementById('tab-funder-networks').style.color = 'var(--color-none)';
                            document.getElementById('tab-funder-networks').style.borderColor = 'var(--color-none)';
                            if (!document.getElementById('funder-networks-content').innerHTML) {{
                                loadFunderNetworks();
                            }}
                        }} else if (tabName === 'funding-networks') {{
                            document.getElementById('tab-funding-networks').style.background = 'rgba(124, 58, 237, 0.2)';
                            document.getElementById('tab-funding-networks').style.color = 'var(--primary)';
                            document.getElementById('tab-funding-networks').style.borderColor = 'var(--primary)';
                            if (!document.getElementById('funding-networks-content').innerHTML) {{
                                loadFundingNetworks();
                            }}
                            startNetworkPolling();  // Start polling when networks tab is active
                        }} else {{
                            stopNetworkPolling();  // Stop polling when switching away from networks
                        }}
                    }}

                    async function loadDuplicateSenders() {{
                        const statusEl = document.getElementById('senders-status');
                        statusEl.textContent = '⟲ Loading duplicate senders...';

                        try {{
                            const response = await fetch('/api/duplicate-senders');
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            // Build senders table HTML
                            let html = `
                                <div class="section">
                                    <div class="section-title">📤 Duplicate Senders - Sending to Multiple Funders (${{data.total_duplicate_senders}} total)</div>
                                    <div class="section-content">
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th>Sender Address</th>
                                                    <th>Funders Sent To</th>
                                                    <th>Total Transfers</th>
                                                    <th>Total SOL</th>
                                                    <th>Related Tokens</th>
                                                    <th>Period</th>
                                                </tr>
                                            </thead>
                                            <tbody>`;

                            if (data.senders.length === 0) {{
                                html += '<tr><td colspan="6" style="padding: 20px; text-align: center; color: var(--text-secondary);">No duplicate senders found</td></tr>';
                            }} else {{
                                data.senders.forEach(sender => {{
                                    const firstDate = new Date(sender.first_seen * 1000).toISOString().substring(0, 10);
                                    const lastDate = new Date(sender.last_seen * 1000).toISOString().substring(0, 10);
                                    const period = firstDate === lastDate ? firstDate : firstDate + ' - ' + lastDate;
                                    const rowHighlight = sender.funder_count > 10 ? 'background: rgba(251, 191, 36, 0.1);' : '';

                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05); ${{rowHighlight}}">
                                            <td style="padding: 12px; font-family: monospace; font-size: 11px; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                                <a href="#" onclick="showSenderTokens('${{sender.sender_address}}'); return false;" style="color: var(--color-medium); text-decoration: none; cursor: pointer;" title="${{sender.sender_address}}">${{sender.sender_address}}</a>
                                            </td>
                                            <td style="padding: 12px; color: var(--color-critical); font-weight: bold;">${{sender.funder_count}}</td>
                                            <td style="padding: 12px; color: var(--text-secondary);">${{sender.transfer_count}}</td>
                                            <td style="padding: 12px; color: var(--color-low);">${{sender.total_sol.toFixed(2)}}</td>
                                            <td style="padding: 12px; color: var(--text-secondary); font-weight: bold;">${{sender.related_token_count || 0}}</td>
                                            <td style="padding: 12px; font-size: 11px; color: var(--text-secondary);">${{period}}</td>
                                        </tr>`;
                                }});
                            }}

                            html += `
                                            </tbody>
                                        </table>
                                    </div>
                                </div>`;

                            document.getElementById('senders-content').innerHTML = html;
                            statusEl.textContent = '✅ Loaded ' + data.total_duplicate_senders + ' duplicate senders';
                        }} catch(e) {{
                            statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    async function loadDuplicateTokens() {{
                        const statusEl = document.getElementById('tokens-status');
                        statusEl.textContent = '⟲ Loading coordinated tokens...';

                        try {{
                            const response = await fetch('/api/duplicate-tokens');
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            // Build tokens table HTML
                            let html = `
                                <div class="section">
                                    <div class="section-title">🪙 Coordinated Tokens - Funded by Multiple Senders (${{data.total_duplicate_tokens}} total)</div>
                                    <div class="section-content">
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th>Token Mint</th>
                                                    <th>Creator</th>
                                                    <th>Created</th>
                                                    <th>Num Senders</th>
                                                    <th>Num Funders</th>
                                                    <th>Total SOL</th>
                                                    <th>Risk</th>
                                                    <th>Rug %</th>
                                                </tr>
                                            </thead>
                                            <tbody>`;

                            if (data.tokens.length === 0) {{
                                html += '<tr><td colspan="8" style="padding: 20px; text-align: center; color: var(--text-secondary);">No coordinated tokens found</td></tr>';
                            }} else {{
                                data.tokens.forEach(token => {{
                                    const createdDate = new Date(token.created_at).toISOString().substring(0, 10);
                                    const riskColor = token.risk_level === 'HIGH' ? 'var(--color-critical)' : token.risk_level === 'MEDIUM' ? 'var(--color-high)' : '#4ade80';
                                    const senderHighlight = token.num_senders > 100 ? 'background: rgba(239, 68, 68, 0.15);' : token.num_senders > 50 ? 'background: rgba(245, 158, 11, 0.15);' : '';

                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05); ${{senderHighlight}}">
                                            <td style="padding: 12px; font-family: monospace; font-size: 10px; word-break: break-all; color: var(--color-low);">
                                                <a href="https://solscan.io/token/${{token.mint}}" target="_blank" style="color: var(--color-low); text-decoration: none;">${{token.mint}}</a>
                                            </td>
                                            <td style="padding: 12px; font-family: monospace; font-size: 10px; word-break: break-all; color: var(--text-secondary);">${{token.creator}}</td>
                                            <td style="padding: 12px; font-size: 11px; color: var(--text-secondary);">${{createdDate}}</td>
                                            <td style="padding: 12px; color: var(--color-critical); font-weight: bold; text-align: center;">${{token.num_senders}}</td>
                                            <td style="padding: 12px; color: var(--color-medium); font-weight: bold; text-align: center;">${{token.num_funders}}</td>
                                            <td style="padding: 12px; color: var(--color-low);">${{token.total_sol.toFixed(2)}}</td>
                                            <td style="padding: 12px; color: ${{riskColor}}; font-weight: bold;">${{token.risk_level || 'N/A'}}</td>
                                            <td style="padding: 12px; color: var(--color-high);">${{((token.rug_probability || 0) * 100).toFixed(1)}}%</td>
                                        </tr>`;
                                }});
                            }}

                            html += `
                                            </tbody>
                                        </table>
                                    </div>
                                </div>`;

                            document.getElementById('tokens-content').innerHTML = html;
                            statusEl.textContent = '✅ Loaded ' + data.total_duplicate_tokens + ' coordinated tokens';
                        }} catch(e) {{
                            statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    async function loadFunderNetworks() {{
                        const statusEl = document.getElementById('funder-networks-status');
                        statusEl.textContent = '⟲ Loading funder networks...';

                        try {{
                            const response = await fetch('/api/funder-networks');
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            // Build funders table HTML
                            let html = `
                                <div class="section">
                                    <div class="section-title">🔗 Funder Networks - All Funders with Network Info (${{data.total_funders}} total)</div>
                                    <div class="section-content">
                                        <table style="width: 100%; border-collapse: collapse;">
                                            <thead style="background: rgba(0, 0, 0, 0.3);">
                                                <tr>
                                                    <th style="padding: 12px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">Funder Address</th>
                                                    <th style="padding: 12px; text-align: center; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">Tokens</th>
                                                    <th style="padding: 12px; text-align: center; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">Creators</th>
                                                    <th style="padding: 12px; text-align: center; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">Senders</th>
                                                    <th style="padding: 12px; text-align: right; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">SOL In/Out</th>
                                                    <th style="padding: 12px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">Period</th>
                                                </tr>
                                            </thead>
                                            <tbody>`;

                            if (data.funders.length === 0) {{
                                html += '<tr><td colspan="6" style="padding: 20px; text-align: center; color: var(--text-secondary);">No funder networks found</td></tr>';
                            }} else {{
                                data.funders.forEach(funder => {{
                                    const startDate = new Date(funder.earliest_funding * 1000).toISOString().substring(0, 10);
                                    const endDate = new Date(funder.latest_funding * 1000).toISOString().substring(0, 10);
                                    const period = startDate === endDate ? startDate : startDate + ' - ' + endDate;
                                    const networkHighlight = funder.tokens_funded > 10 ? 'background: rgba(59, 130, 246, 0.15);' : '';

                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05); ${{networkHighlight}}">
                                            <td style="padding: 12px; font-family: monospace; font-size: 10px; word-break: break-all; color: var(--color-none);">
                                                <a href="#" onclick="showFunderTokens('${{funder.funder_address}}'); return false;" style="color: var(--color-none); text-decoration: none; cursor: pointer;">${{funder.funder_address}}</a>
                                            </td>
                                            <td style="padding: 12px; text-align: center; color: var(--color-low); font-weight: bold;">${{funder.tokens_funded}}</td>
                                            <td style="padding: 12px; text-align: center; color: var(--text-secondary);">${{funder.creators_funded}}</td>
                                            <td style="padding: 12px; text-align: center; color: var(--color-medium);">${{funder.num_senders || 0}}</td>
                                            <td style="padding: 12px; text-align: right; color: var(--color-high); font-size: 10px;">${{funder.total_sol_in ? funder.total_sol_in.toFixed(2) : '0'}} / ${{funder.total_sol_out.toFixed(2)}}</td>
                                            <td style="padding: 12px; font-size: 10px; color: var(--text-secondary);">${{period}}</td>
                                        </tr>`;
                                }});
                            }}

                            html += `
                                            </tbody>
                                        </table>
                                    </div>
                                </div>`;

                            document.getElementById('funder-networks-content').innerHTML = html;
                            statusEl.textContent = '✅ Loaded ' + data.total_funders + ' funder networks';
                        }} catch(e) {{
                            statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    async function showFunderTokens(funderAddress) {{
                        const modal = document.getElementById('funderTokensModal');
                        if (!modal) {{
                            alert('Click on a funder address to see its coordinated tokens');
                            return;
                        }}

                        document.getElementById('modalFunderAddress').textContent = funderAddress;
                        const statusEl = document.getElementById('funderTokensStatus');
                        statusEl.textContent = '⟲ Loading tokens...';

                        try {{
                            const response = await fetch(`/api/tokens-by-funder/${{funderAddress}}`);
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            let html = `<table style="width: 100%; border-collapse: collapse;">
                                <thead style="background: rgba(0, 0, 0, 0.3);">
                                    <tr>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">Token Mint</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">Creator</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">Created</th>
                                        <th style="padding: 10px; text-align: right; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">SOL</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(59, 130, 246, 0.2); color: var(--text-secondary); font-size: 11px;">Senders</th>
                                    </tr>
                                </thead>
                                <tbody>`;

                            if (data.tokens.length === 0) {{
                                html += '<tr><td colspan="5" style="padding: 20px; text-align: center; color: var(--text-secondary);">No tokens found</td></tr>';
                            }} else {{
                                data.tokens.forEach(token => {{
                                    const createdDate = new Date(token.created_at).toISOString().substring(0, 10);
                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                            <td style="padding: 10px; font-family: monospace; font-size: 10px; color: var(--color-low);">
                                                <a href="https://solscan.io/token/${{token.mint}}" target="_blank" style="color: var(--color-low); text-decoration: none;">${{token.mint.substring(0, 20)}}...</a>
                                            </td>
                                            <td style="padding: 10px; font-family: monospace; font-size: 10px; color: var(--text-secondary); word-break: break-all;">${{token.creator.substring(0, 20)}}...</td>
                                            <td style="padding: 10px; font-size: 10px; color: var(--text-secondary);">${{createdDate}}</td>
                                            <td style="padding: 10px; color: var(--color-low); font-weight: bold; text-align: right;">${{token.amount_sol ? token.amount_sol.toFixed(2) : '0'}}</td>
                                            <td style="padding: 10px; color: var(--color-medium); font-weight: bold;">${{token.num_senders || 0}}</td>
                                        </tr>`;
                                }});
                            }}

                            html += '</tbody></table>';

                            const tokensContainer = document.getElementById('funderTokensContainer');
                            tokensContainer.innerHTML = html;
                            statusEl.textContent = `✅ Showing ${{data.total_tokens}} tokens funded by this funder`;
                            modal.style.display = 'block';

                        }} catch(error) {{
                            console.error('Error loading funder tokens:', error);
                            statusEl.textContent = '❌ Failed to load tokens';
                        }}
                    }}

                    async function loadFundingNetworks() {{
                        const contentEl = document.getElementById('funding-networks-content');
                        const statusEl = document.getElementById('funding-networks-status');
                        const checkbox = document.getElementById('hideCexInfra');
                        const hideCexInfra = checkbox ? checkbox.checked : false;

                        if (statusEl) {{
                            statusEl.textContent = '⟲ Loading networks...';
                        }}

                        try {{
                            const response = await fetch('/api/funding-networks');
                            const data = await response.json();

                            if (data.error) {{
                                if (statusEl) statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            let filteredNetworks = data.networks;

                            // Filter out networks that contain only CEX/INFRA members if checkbox is checked
                            if (hideCexInfra) {{
                                filteredNetworks = data.networks.map(network => {{
                                    const nonCexMembers = network.members.filter(m => !m.is_cex);
                                    return {{
                                        ...network,
                                        members: nonCexMembers,
                                        member_count: nonCexMembers.length
                                    }};
                                }}).filter(network => network.member_count > 0);
                            }}

                            let html = `<div id="funding-networks-grid" style="display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 15px;">`;

                            filteredNetworks.forEach(network => {{
                                html += `
                                    <div style="background: rgba(0, 0, 0, 0.3); border: 1px solid var(--primary); border-radius: 8px; padding: 15px; cursor: pointer; transition: all 0.3s;"
                                         onclick="showNetworkDetails(${{network.network_id}})"
                                         onmouseover="this.style.background='rgba(124, 58, 237, 0.15)'; this.style.boxShadow='0 0 15px rgba(124, 58, 237, 0.5)';"
                                         onmouseout="this.style.background='rgba(0, 0, 0, 0.3)'; this.style.boxShadow='none';">
                                        <div style="font-weight: bold; color: var(--text-primary); font-size: 14px; margin-bottom: 12px;">${{network.name}}</div>
                                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; font-size: 11px;">
                                            <div style="background: rgba(74, 222, 128, 0.1); padding: 8px; border-radius: 4px; border-left: 2px solid #4ade80;">
                                                <div style="color: var(--text-secondary);">Members</div>
                                                <div style="color: var(--color-low); font-weight: bold;">${{network.member_count}}</div>
                                            </div>
                                            <div style="background: rgba(59, 130, 246, 0.1); padding: 8px; border-radius: 4px; border-left: 2px solid var(--color-none);">
                                                <div style="color: var(--text-secondary);">Tokens</div>
                                                <div style="color: var(--color-none); font-weight: bold;">${{network.total_tokens}}</div>
                                            </div>
                                            <div style="background: rgba(245, 158, 11, 0.1); padding: 8px; border-radius: 4px; border-left: 2px solid var(--color-high);">
                                                <div style="color: var(--text-secondary);">Creators</div>
                                                <div style="color: var(--color-high); font-weight: bold;">${{network.total_creators}}</div>
                                            </div>
                                            <div style="background: rgba(168, 85, 247, 0.1); padding: 8px; border-radius: 4px; border-left: 2px solid var(--accent-purple);">
                                                <div style="color: var(--text-secondary);">SOL</div>
                                                <div style="color: var(--accent-purple); font-weight: bold;">${{network.total_sol.toFixed(0)}}</div>
                                            </div>
                                        </div>
                                    </div>`;
                            }});

                            html += `</div>`;

                            contentEl.innerHTML = html;
                            const displayCount = hideCexInfra ? filteredNetworks.length : data.networks.length;
                            if (statusEl) statusEl.textContent = '✅ Showing ' + displayCount + ' networks' + (hideCexInfra ? ' (CEX/INFRA hidden)' : '');
                        }} catch(e) {{
                            console.error('Error loading networks:', e);
                            if (statusEl) statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    async function showNetworkDetails(networkId) {{
                        const gridEl = document.getElementById('funding-networks-grid');
                        const statusEl = document.getElementById('funding-networks-status');

                        if (!gridEl) {{
                            console.error('funding-networks-grid element not found');
                            return;
                        }}

                        if (statusEl) {{
                            statusEl.textContent = '⟲ Loading details...';
                        }}

                        try {{
                            const response = await fetch(`/api/funding-network-details/${{networkId}}`);
                            const data = await response.json();

                            if (data.error) {{
                                if (statusEl) statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            let html = `
                                <div style="margin-bottom: 20px;">
                                    <button onclick="loadFundingNetworks()" style="background: rgba(124, 58, 237, 0.2); color: var(--primary); border: 1px solid var(--primary); padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 12px;">← Back to Networks</button>
                                </div>
                                <div style="background: rgba(124, 58, 237, 0.1); border-left: 3px solid var(--primary); border-radius: 6px; padding: 20px; margin-bottom: 20px;">
                                    <h2 style="color: var(--primary); margin: 0 0 15px 0;">${{data.network_name}} Details</h2>
                                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px;">
                                        <div>
                                            <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 5px;">FUNDERS</div>
                                            <div style="font-size: 28px; font-weight: bold; color: var(--color-low);">${{data.funders}}</div>
                                        </div>
                                        <div>
                                            <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 5px;">SENDERS</div>
                                            <div style="font-size: 28px; font-weight: bold; color: var(--color-none);">${{data.senders}}</div>
                                        </div>
                                        <div>
                                            <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 5px;">CREATORS</div>
                                            <div style="font-size: 28px; font-weight: bold; color: var(--color-high);">${{data.creators}}</div>
                                        </div>
                                        <div>
                                            <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 5px;">TOKENS</div>
                                            <div style="font-size: 28px; font-weight: bold; color: var(--accent-purple);">${{data.tokens}}</div>
                                        </div>
                                        <div>
                                            <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 5px;">TOTAL SOL</div>
                                            <div style="font-size: 28px; font-weight: bold; color: var(--color-high);">${{data.total_sol.toFixed(0)}}</div>
                                        </div>
                                    </div>
                                </div>

                                <div style="background: var(--bg-secondary); border-radius: 6px; padding: 20px;">
                                    <h3 style="color: var(--text-primary); margin: 0 0 15px 0;">Tokens Coordinated</h3>
                                    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(400px, 1fr)); gap: 10px;">`;

                            data.token_list.forEach(token => {{
                                html += `
                                    <div style="background: rgba(124, 58, 237, 0.05); padding: 10px; border-radius: 4px; border-left: 2px solid var(--primary); font-family: monospace; font-size: 10px; word-break: break-all; color: var(--text-secondary);">
                                        ${{token}}
                                    </div>`;
                            }});

                            html += `</div></div>`;

                            // Add Root Operator Flows section
                            if (data.root_operator_flows && data.root_operator_flows.length > 0) {{
                                html += `<div style="background: var(--bg-secondary); border-radius: 6px; padding: 20px; margin-top: 20px;">
                                    <h3 style="color: var(--text-primary); margin: 0 0 15px 0;">Root Operators & Address Flows</h3>
                                    <div style="display: grid; grid-template-columns: 1fr; gap: 15px;">`;

                                data.root_operator_flows.forEach((flow, idx) => {{
                                    html += `<div style="background: rgba(124, 58, 237, 0.08); border-left: 3px solid var(--primary); border-radius: 6px; padding: 15px;">
                                        <div style="margin-bottom: 12px;">
                                            <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 5px;">ROOT OPERATOR #${{idx + 1}}</div>
                                            <div style="font-family: monospace; font-size: 12px; color: var(--primary); word-break: break-all; padding: 8px; background: rgba(124, 58, 237, 0.1); border-radius: 4px;">${{flow.root_operator}}</div>
                                        </div>
                                        <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 12px;">
                                            <div>
                                                <div style="font-size: 10px; color: var(--text-secondary);">CREATORS FUNDED</div>
                                                <div style="font-size: 18px; font-weight: bold; color: var(--color-high);">${{flow.creators_funded}}</div>
                                            </div>
                                            <div>
                                                <div style="font-size: 10px; color: var(--text-secondary);">TOTAL SOL</div>
                                                <div style="font-size: 18px; font-weight: bold; color: var(--color-low);">${{flow.total_sol_sent.toFixed(2)}}</div>
                                            </div>
                                        </div>
                                        <div style="margin-bottom: 12px;">
                                            <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 5px;">UPSTREAM SOURCES</div>
                                            <div style="background: rgba(0, 0, 0, 0.3); border-radius: 4px; padding: 8px; max-height: 80px; overflow-y: auto;">`;

                                    if (flow.upstream_sources.length > 0) {{
                                        flow.upstream_sources.forEach(source => {{
                                            html += `<div style="font-family: monospace; font-size: 10px; color: var(--color-none); word-break: break-all; padding: 4px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05);">${{source.sender}}</div>`;
                                        }});
                                    }} else {{
                                        html += `<div style="color: var(--text-secondary); font-size: 10px;">No upstream sources found</div>`;
                                    }}

                                    html += `</div></div>
                                        <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 5px;">EXAMPLE ADDRESS FLOWS</div>
                                        <div style="background: rgba(0, 0, 0, 0.3); border-radius: 4px; padding: 8px; max-height: 100px; overflow-y: auto;">`;

                                    if (flow.example_flows && flow.example_flows.length > 0) {{
                                        flow.example_flows.forEach(ex => {{
                                            html += `<div style="font-family: monospace; font-size: 9px; color: var(--text-primary); padding: 4px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05); line-height: 1.4;">
                                                <div style="color: var(--color-none);">${{ex.sender.substring(0, 12)}}...</div>
                                                <div style="color: var(--text-secondary); margin-left: 10px;">↓ (to funder)</div>
                                                <div style="color: var(--primary);">${{ex.funder.substring(0, 12)}}...</div>
                                                <div style="color: var(--text-secondary); margin-left: 10px;">↓ ${{{ex.sol_to_creator.toFixed(2)}}} SOL</div>
                                                <div style="color: var(--color-high);">${{ex.creator.substring(0, 12)}}...</div>
                                            </div>`;
                                        }});
                                    }} else {{
                                        html += `<div style="color: var(--text-secondary); font-size: 10px;">No flows available</div>`;
                                    }}

                                    html += `</div>
                                        <div style="font-size: 11px; color: var(--text-secondary); margin-bottom: 5px; margin-top: 12px;">TOKENS CREATED BY FUNDED CREATORS</div>
                                        <div style="background: rgba(0, 0, 0, 0.3); border-radius: 4px; padding: 8px; max-height: 120px; overflow-y: auto;">`;

                                    if (flow.downstream_creators.length > 0) {{
                                        flow.downstream_creators.forEach(creator => {{
                                            const riskColor = creator.risk_level === 'HIGH' ? 'var(--color-critical)' : creator.risk_level === 'MEDIUM' ? 'var(--color-high)' : '#4ade80';
                                            html += `
                                                <div style="padding: 6px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.05); display: flex; justify-content: space-between; align-items: center;">
                                                    <div style="font-family: monospace; font-size: 9px; color: var(--text-secondary); flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${{creator.mint}}">${{creator.mint.substring(0, 16)}}...</div>
                                                    <div style="color: ${{riskColor}}; font-weight: bold; font-size: 10px; margin-left: 8px;">${{(creator.rug_probability * 100).toFixed(0)}}%</div>
                                                </div>`;
                                        }});
                                    }} else {{
                                        html += `<div style="color: var(--text-secondary); font-size: 10px;">No tokens found</div>`;
                                    }}

                                    html += `</div>
                                    </div>`;
                                }});

                                html += `</div></div>`;
                            }}

                            gridEl.innerHTML = html;
                            if (statusEl) statusEl.textContent = '✅ Network details loaded';
                        }} catch(e) {{
                            console.error('Error loading network details:', e);
                            if (statusEl) statusEl.textContent = '❌ Error: ' + e.message;
                        }}
                    }}

                    function closeNetworkDetails() {{
                        loadFundingNetworks();
                    }}

                    async function showSenderTokens(senderAddress) {{
                        const modal = document.getElementById('senderTokensModal');
                        if (!modal) return;

                        document.getElementById('modalSenderAddress').textContent = senderAddress;
                        const statusEl = document.getElementById('senderTokensStatus');
                        statusEl.textContent = '⟲ Loading tokens...';

                        try {{
                            const response = await fetch(`/api/sender-tokens/${{senderAddress}}`);
                            const data = await response.json();

                            if (data.error) {{
                                statusEl.textContent = '❌ Error: ' + data.error;
                                return;
                            }}

                            let html = `<table style="width: 100%; border-collapse: collapse;">
                                <thead style="background: rgba(0, 0, 0, 0.3);">
                                    <tr>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(6, 182, 212, 0.2); color: var(--text-secondary); font-size: 12px;">Token Mint</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(6, 182, 212, 0.2); color: var(--text-secondary); font-size: 12px;">Creator</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(6, 182, 212, 0.2); color: var(--text-secondary); font-size: 12px;">Funding (SOL)</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(6, 182, 212, 0.2); color: var(--text-secondary); font-size: 12px;">Risk</th>
                                        <th style="padding: 10px; text-align: left; border-bottom: 1px solid rgba(6, 182, 212, 0.2); color: var(--text-secondary); font-size: 12px;">Rug %</th>
                                    </tr>
                                </thead>
                                <tbody>`;

                            if (data.tokens.length === 0) {{
                                html += '<tr><td colspan="5" style="padding: 20px; text-align: center; color: var(--text-secondary);">No tokens found</td></tr>';
                            }} else {{
                                data.tokens.forEach(token => {{
                                    const riskColor = token.risk_level === 'HIGH' ? 'var(--color-critical)' : token.risk_level === 'MEDIUM' ? 'var(--color-high)' : '#4ade80';
                                    html += `
                                        <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                                            <td style="padding: 10px; font-family: monospace; font-size: 11px; color: var(--color-low); max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
                                                <a href="https://solscan.io/token/${{token.mint}}" target="_blank" style="color: var(--color-low); text-decoration: none;" title="${{token.mint}}">${{token.mint}}</a>
                                            </td>
                                            <td style="padding: 10px; font-family: monospace; font-size: 11px; color: var(--text-secondary); max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${{token.creator_address}}">${{token.creator_address}}</td>
                                            <td style="padding: 10px; color: var(--color-low); font-weight: bold;">${{token.total_funding_sol.toFixed(2)}}</td>
                                            <td style="padding: 10px; color: ${{riskColor}}; font-weight: bold;">${{token.risk_level || 'N/A'}}</td>
                                            <td style="padding: 10px; color: var(--color-high);">${{(token.rug_probability * 100).toFixed(1)}}%</td>
                                        </tr>`;
                                }});
                            }}

                            html += '</tbody></table>';

                            const tokensContainer = document.getElementById('senderTokensContainer');
                            tokensContainer.innerHTML = html;
                            statusEl.textContent = `✅ Showing ${{data.total_tokens}} tokens`;
                            modal.style.display = 'block';

                        }} catch(error) {{
                            console.error('Error loading sender tokens:', error);
                            statusEl.textContent = '❌ Failed to load tokens';
                        }}
                    }}

                    function closeSenderTokens() {{
                        const modal = document.getElementById('senderTokensModal');
                        if (modal) modal.style.display = 'none';
                    }}

                    function closeFunderTokens() {{
                        const modal = document.getElementById('funderTokensModal');
                        if (modal) modal.style.display = 'none';
                    }}

                    window.onclick = function(event) {{
                        const senderModal = document.getElementById('senderTokensModal');
                        const funderModal = document.getElementById('funderTokensModal');
                        if (event.target === senderModal) {{
                            senderModal.style.display = 'none';
                        }}
                        if (event.target === funderModal) {{
                            funderModal.style.display = 'none';
                        }}
                    }}
                    </script>

                    <!-- Funders Tab -->
                    <div id="funders-tab" style="display: block;">
                        <div style="margin-bottom: 20px;">
                            <button onclick="analyzeAllFunders()" style="background: rgba(76, 175, 80, 0.2); color: var(--color-low); border: 1px solid #4ade80; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">🔍 Analyze All Funders</button>
                            <span id="analysis-status" style="margin-left: 15px; color: var(--text-secondary);"></span>
                        </div>
                    </div>

                    <!-- Senders Tab -->
                    <div id="senders-tab" style="display: none;">
                        <div style="margin-bottom: 20px;">
                            <button onclick="loadDuplicateSenders()" style="background: rgba(251, 191, 36, 0.2); color: var(--color-medium); border: 1px solid var(--color-medium); padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">⟲ Reload Senders Data</button>
                            <span id="senders-status" style="margin-left: 15px; color: var(--text-secondary);"></span>
                        </div>
                        <div id="senders-content"></div>
                    </div>

                    <!-- Coordinated Tokens Tab -->
                    <div id="tokens-tab" style="display: none;">
                        <div style="margin-bottom: 20px;">
                            <button onclick="loadDuplicateTokens()" style="background: rgba(34, 197, 94, 0.2); color: var(--color-low); border: 1px solid #4ade80; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">⟲ Reload Tokens Data</button>
                            <span id="tokens-status" style="margin-left: 15px; color: var(--text-secondary);"></span>
                        </div>
                        <div id="tokens-content"></div>
                    </div>

                    <!-- Funder Networks Tab -->
                    <div id="funder-networks-tab" style="display: none;">
                        <div style="margin-bottom: 20px;">
                            <button onclick="loadFunderNetworks()" style="background: rgba(59, 130, 246, 0.2); color: var(--color-none); border: 1px solid var(--color-none); padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">⟲ Reload Funder Networks</button>
                            <span id="funder-networks-status" style="margin-left: 15px; color: var(--text-secondary);"></span>
                        </div>
                        <div id="funder-networks-content"></div>
                    </div>

                    <!-- Funding Networks Tab (Token Overlap Clustering) -->
                    <div id="funding-networks-tab" style="display: none;">
                        <div style="margin-bottom: 20px; display: flex; align-items: center; gap: 15px;">
                            <button onclick="loadFundingNetworks()" style="background: rgba(124, 58, 237, 0.2); color: var(--primary); border: 1px solid var(--primary); padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600;">⟲ Reload Funding Networks</button>
                            <label style="display: flex; align-items: center; gap: 8px; color: var(--text-primary); font-size: 14px; cursor: pointer; background: rgba(124, 58, 237, 0.1); padding: 8px 12px; border-radius: 6px; border: 1px solid rgba(124, 58, 237, 0.3);">
                                <input type="checkbox" id="hideCexInfra" onchange="loadFundingNetworks()" style="cursor: pointer; width: 18px; height: 18px; accent-color: var(--primary);">
                                Hide CEX/INFRA
                            </label>
                            <span id="funding-networks-status" style="color: var(--text-secondary);"></span>
                        </div>
                        <div id="funding-networks-content"></div>
                    </div>

                    <script>
                    async function analyzeAllFunders() {{
                        const btn = event.target;
                        btn.disabled = true;
                        const statusEl = document.getElementById('analysis-status');
                        statusEl.textContent = 'Queuing analysis...';

                        try {{
                            const response = await fetch('/api/analyze-all-coordinated-funders', {{ method: 'POST' }});
                            const data = await response.json();
                            statusEl.innerHTML = `✅ ` + data.message + `<br/><small>Queued: ` + data.queued_for_analysis + ` | Already done: ` + data.already_analyzed + `</small>`;
                        }} catch(e) {{
                            statusEl.textContent = `Error: ` + e.message;
                        }} finally {{
                            btn.disabled = false;
                        }}
                    }}
                    </script>

                        <div class="stats-grid">
                            <div class="stat-box suspicious">
                                <div class="stat-label">Suspicious Multi-Creator</div>
                                <div class="stat-value">{len(suspicious_funders)}</div>
                            </div>
                            <div class="stat-box safe">
                                <div class="stat-label">Safe (INFRA/CEX)</div>
                                <div class="stat-value">{len(safe_funders)}</div>
                            </div>
                            <div class="stat-box suspicious">
                                <div class="stat-label">Total Funders</div>
                                <div class="stat-value">{stats['total_funders']}</div>
                            </div>
                        </div>

                        <div class="section">
                            <div class="section-title">⚠️ Suspicious Multi-Creator Funders ({len(suspicious_funders)} total) - Click row for details</div>
                            <div class="section-content">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Funder Address</th>
                                            <th>Network</th>
                                            <th>Creators</th>
                                            <th>Total SOL</th>
                                            <th>Records</th>
                                            <th>Analysis Status</th>
                                            <th>Period</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {suspicious_html if suspicious_html else '<tr><td colspan="7" style="padding: 20px; text-align: center; color: var(--text-secondary);">No suspicious funders found</td></tr>'}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <div class="section">
                            <div class="section-title">✅ Safe Multi-Creator Funders ({len(safe_funders)} total)</div>
                            <div class="section-content">
                                <table>
                                    <thead>
                                        <tr>
                                            <th>Funder Address</th>
                                            <th>Account Name</th>
                                            <th>Type</th>
                                            <th>Creators Funded</th>
                                            <th>Total SOL</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {safe_html if safe_html else '<tr><td colspan="5" style="padding: 20px; text-align: center; color: var(--text-secondary);">No safe funders found</td></tr>'}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>

                    <!-- Sender Tokens Modal -->
                    <div id="senderTokensModal" style="display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0, 0, 0, 0.7);">
                        <div style="background: #0a0e27; margin: 10% auto; padding: 20px; border: 1px solid var(--accent-cyan); width: 90%; max-width: 1200px; max-height: 80vh; overflow-y: auto; border-radius: 8px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                                <h2 style="color: var(--accent-cyan); margin: 0;">Tokens Funded by Sender</h2>
                                <span style="cursor: pointer; font-size: 28px; color: var(--text-secondary);" onclick="closeSenderTokens()">&times;</span>
                            </div>
                            <p style="color: var(--text-secondary); font-size: 12px; word-break: break-all; margin-bottom: 15px;"><strong>Sender:</strong> <span id="modalSenderAddress" style="font-family: monospace;"></span></p>
                            <div style="background: rgba(0, 0, 0, 0.3); padding: 10px; border-radius: 4px; margin-bottom: 15px; color: var(--text-secondary);">
                                <span id="senderTokensStatus">Loading...</span>
                            </div>
                            <div id="senderTokensContainer" style="overflow-x: auto;"></div>
                        </div>
                    </div>

                    <!-- Funder Tokens Modal -->
                    <div id="funderTokensModal" style="display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0, 0, 0, 0.7);">
                        <div style="background: #0a0e27; margin: 10% auto; padding: 20px; border: 1px solid var(--color-none); width: 90%; max-width: 1200px; max-height: 80vh; overflow-y: auto; border-radius: 8px;">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                                <h2 style="color: var(--color-none); margin: 0;">Tokens Funded by Funder</h2>
                                <span style="cursor: pointer; font-size: 28px; color: var(--text-secondary);" onclick="closeFunderTokens()">&times;</span>
                            </div>
                            <p style="color: var(--text-secondary); font-size: 12px; word-break: break-all; margin-bottom: 15px;"><strong>Funder:</strong> <span id="modalFunderAddress" style="font-family: monospace;"></span></p>
                            <div style="background: rgba(0, 0, 0, 0.3); padding: 10px; border-radius: 4px; margin-bottom: 15px; color: var(--text-secondary);">
                                <span id="funderTokensStatus">Loading...</span>
                            </div>
                            <div id="funderTokensContainer" style="overflow-x: auto;"></div>
                        </div>
                    </div>
                </div>
            </body>
        </html>
        """
        return html

    except Exception as e:
        return f"<html><body style='background: #0a0e27; color: var(--text-primary);'><h1>Error</h1><p>{str(e)}</p></body></html>", 500


@app.route('/funder-details/<funder_address>')
def funder_details_view(funder_address: str):
    """Serve a full webview for detailed funder analysis with transfer details"""
    try:
        from src.utils.infra_mapping import get_account_info, get_cex_info
        import requests

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get funder info
        cursor.execute("""
            SELECT
                funder_address,
                COUNT(DISTINCT creator_address) as creator_count,
                COUNT(*) as funding_record_count,
                SUM(amount_sol) as total_sol_sent,
                MIN(first_detected_at) as first_funding_at,
                MAX(first_detected_at) as last_funding_at,
                MAX(is_cex) as is_cex_flag
            FROM creator_funders
            WHERE funder_address = ?
            GROUP BY funder_address
        """, (funder_address,))

        funder = dict(cursor.fetchone() or {})
        if not funder:
            conn.close()
            return f"<html><body style='background: linear-gradient(135deg, #0a0a0e 0%, #0d0d15 100%); background-attachment: fixed; color: var(--text-primary);'><h1>Funder Not Found</h1><p>No funding data for {funder_address}</p><p><a href='/' style='color: var(--color-cyan);'>← Back to Dashboard</a></p></body></html>", 404

        # Get detailed transfers to creators
        cursor.execute("""
            SELECT
                creator_address,
                amount_sol,
                first_detected_at
            FROM creator_funders
            WHERE funder_address = ?
            ORDER BY amount_sol DESC
        """, (funder_address,))

        transfers = [dict(row) for row in cursor.fetchall()]

        # Get funder label/classification
        funder_label = None
        is_cex = False
        is_infra = False

        infra_info = get_account_info(funder_address)
        if infra_info:
            funder_label = infra_info.get('name')
            is_infra = True

        cex_info = get_cex_info(funder_address)
        if cex_info:
            funder_label = cex_info.get('name')
            is_cex = True

        conn.close()

        # Fetch transfer details (incoming/outgoing) from API
        incoming_transfers = []
        outgoing_transfers = []
        total_incoming = 0
        total_outgoing = 0

        try:
            transfer_response = requests.get(f'http://localhost:5002/api/funder-transfer-details/{funder_address}', timeout=5)
            if transfer_response.status_code == 200:
                transfer_data = transfer_response.json()

                # Parse incoming transfers
                incoming_obj = transfer_data.get('incoming_transfers', {})
                if isinstance(incoming_obj, dict):
                    incoming_senders = incoming_obj.get('senders', [])
                    total_incoming = incoming_obj.get('total_sol', 0)
                    incoming_transfers = incoming_senders
                else:
                    incoming_transfers = incoming_obj if isinstance(incoming_obj, list) else []

                # Parse outgoing transfers
                outgoing_obj = transfer_data.get('outgoing_transfers', {})
                if isinstance(outgoing_obj, dict):
                    outgoing_recipients = outgoing_obj.get('recipients', [])
                    total_outgoing = abs(outgoing_obj.get('total_sol', 0))
                    outgoing_transfers = outgoing_recipients
                else:
                    outgoing_transfers = outgoing_obj if isinstance(outgoing_obj, list) else []
        except Exception as e:
            print(f"[ERROR] Failed to fetch transfer details: {str(e)}")

        # Build creators funded table
        transfers_html = ''
        for transfer in transfers:
            transfers_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: var(--color-cyan); cursor: pointer;" onclick="window.location.href = '/creator/{transfer['creator_address']}'"><u>{transfer['creator_address'][:16]}...{transfer['creator_address'][-4:]}</u></td>
                <td style="padding: 12px; color: var(--color-green);">{transfer['amount_sol']:.2f} SOL</td>
                <td style="padding: 12px; color: var(--text-secondary); font-size: 11px;">{transfer['first_detected_at'][:10] if transfer['first_detected_at'] else 'N/A'}</td>
            </tr>
            """

        # Build incoming transfers table
        incoming_html = ''
        for transfer in incoming_transfers[:20]:  # Limit to 20
            label = transfer.get('label', 'Unknown')
            category = transfer.get('category', '')
            badge = ''
            if category == 'CEX':
                badge = '<span style="background: rgba(239, 68, 68, 0.2); color: #ef4444; padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-left: 8px;">🚨 CEX</span>'
            elif category == 'Infrastructure':
                badge = '<span style="background: rgba(22, 163, 74, 0.2); color: var(--color-green); padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-left: 8px;">✅ INFRA</span>'

            incoming_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: var(--text-secondary);">{transfer['address'][:20]}...</td>
                <td style="padding: 12px; color: var(--color-yellow);">{label}{badge}</td>
                <td style="padding: 12px; text-align: right; color: var(--color-green);">{transfer['amount_sol']:.2f} SOL</td>
                <td style="padding: 12px; text-align: center; color: var(--text-secondary);">{transfer['transaction_count']}</td>
            </tr>
            """

        # Build outgoing transfers table
        outgoing_html = ''
        for transfer in outgoing_transfers[:20]:  # Limit to 20
            label = transfer.get('label', 'Unknown')
            category = transfer.get('category', '')
            badge = ''
            if category == 'CEX':
                badge = '<span style="background: rgba(239, 68, 68, 0.2); color: #ef4444; padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-left: 8px;">🚨 CEX</span>'
            elif category == 'Infrastructure':
                badge = '<span style="background: rgba(22, 163, 74, 0.2); color: var(--color-green); padding: 2px 6px; border-radius: 3px; font-size: 10px; margin-left: 8px;">✅ INFRA</span>'

            outgoing_html += f"""
            <tr style="border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                <td style="padding: 12px; font-family: monospace; font-size: 12px; color: var(--text-secondary);">{transfer['address'][:20]}...</td>
                <td style="padding: 12px; color: var(--color-yellow);">{label}{badge}</td>
                <td style="padding: 12px; text-align: right; color: var(--color-green);">{transfer['amount_sol']:.2f} SOL</td>
                <td style="padding: 12px; text-align: center; color: var(--text-secondary);">{transfer['transaction_count']}</td>
            </tr>
            """

        classification = ''
        if is_cex:
            classification = '<span style="background: rgba(239, 68, 68, 0.2); color: #ef4444; padding: 4px 8px; border-radius: 3px; font-size: 11px; font-weight: 600;">🚨 CEX Hot Wallet</span>'
        elif is_infra:
            classification = '<span style="background: rgba(22, 163, 74, 0.2); color: var(--color-green); padding: 4px 8px; border-radius: 3px; font-size: 11px; font-weight: 600;">✅ Infrastructure</span>'
        else:
            classification = '<span style="background: rgba(251, 191, 36, 0.2); color: var(--color-yellow); padding: 4px 8px; border-radius: 3px; font-size: 11px; font-weight: 600;">❓ Unknown</span>'

        html = f"""
        <html>
            <head>
                <title>Funder Details - {funder_address[:16]}...</title>
                <style>
                    :root {{
                        --color-purple: #a78bfa;
                        --color-cyan: #06b6d4;
                        --color-green: #16a34a;
                        --color-yellow: #fbbf24;
                        --text-primary: #e5e7eb;
                        --text-secondary: #9ca3af;
                        --bg-dark: linear-gradient(135deg, #0a0a0e 0%, #0d0d15 100%);
                        --bg-card: rgba(30, 30, 40, 0.8);
                        --bg-hover: rgba(167, 139, 250, 0.05);
                        --border-color: rgba(167, 139, 250, 0.3);
                    }}
                    body {{
                        background: var(--bg-dark);
                        color: var(--text-primary);
                        font-family: 'Segoe UI', sans-serif;
                        margin: 0;
                        padding: 20px;
                    }}
                    .container {{
                        max-width: 1200px;
                        margin: 0 auto;
                    }}
                    h1 {{
                        color: var(--color-cyan);
                        word-break: break-all;
                        margin: 0 0 10px 0;
                        font-size: 18px;
                    }}
                    h2 {{
                        color: var(--color-cyan);
                        font-size: 14px;
                        margin-top: 30px;
                        margin-bottom: 15px;
                    }}
                    .header {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 20px;
                        border-radius: 8px;
                        margin-bottom: 20px;
                    }}
                    .back-link {{
                        margin-bottom: 20px;
                    }}
                    .back-link a {{
                        color: var(--color-cyan);
                        text-decoration: none;
                    }}
                    .back-link a:hover {{
                        text-decoration: underline;
                    }}
                    .stats-grid {{
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                        gap: 12px;
                        margin: 20px 0;
                    }}
                    .stat-box {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 15px;
                        border-radius: 8px;
                        border-left: 3px solid var(--color-cyan);
                    }}
                    .stat-label {{
                        color: var(--text-secondary);
                        font-size: 10px;
                        text-transform: uppercase;
                        margin-bottom: 8px;
                    }}
                    .stat-value {{
                        font-size: 20px;
                        font-weight: bold;
                        color: var(--color-cyan);
                    }}
                    .section {{
                        background: var(--bg-card);
                        border-radius: 8px;
                        margin-bottom: 20px;
                        overflow: hidden;
                    }}
                    .section-title {{
                        background: var(--bg-card);
                        padding: 12px 15px;
                        border-bottom: 1px solid rgba(6, 182, 212, 0.2);
                        font-weight: 600;
                        color: var(--color-cyan);
                        font-size: 13px;
                    }}
                    .section-content {{
                        max-height: 500px;
                        overflow-y: auto;
                    }}
                    table {{
                        width: 100%;
                        border-collapse: collapse;
                        font-size: 12px;
                    }}
                    th {{
                        background: rgba(0, 0, 0, 0.3);
                        padding: 10px;
                        text-align: left;
                        font-size: 10px;
                        color: var(--text-secondary);
                        text-transform: uppercase;
                        border-bottom: 1px solid rgba(6, 182, 212, 0.2);
                        font-weight: 600;
                    }}
                    td {{
                        padding: 10px;
                    }}
                    tr:hover {{
                        background: var(--bg-hover);
                    }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="back-link">
                        <a href="/coordinated-funders">← Back to Coordinated Funders</a>
                    </div>

                    <div class="header">
                        <h1>💰 {funder_label or 'Unknown Funder'}</h1>
                        <p style="margin: 10px 0 0 0; font-family: monospace; font-size: 12px; color: var(--text-secondary); word-break: break-all;">{funder_address}</p>
                        <div style="margin-top: 10px;">
                            {classification}
                        </div>
                    </div>

                    <div class="stats-grid">
                        <div class="stat-box">
                            <div class="stat-label">Creators Funded</div>
                            <div class="stat-value">{funder['creator_count']}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Total SOL Out</div>
                            <div class="stat-value">{funder['total_sol_sent']:.2f}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Total SOL In</div>
                            <div class="stat-value">{total_incoming:.2f}</div>
                        </div>
                        <div class="stat-box">
                            <div class="stat-label">Funding Records</div>
                            <div class="stat-value">{funder['funding_record_count']}</div>
                        </div>
                    </div>

                    <h2>📤 Incoming Transfers (Who Funded This Account)</h2>
                    <div class="section">
                        <div class="section-title">✅ {len(incoming_transfers)} Sources | {total_incoming:.2f} SOL Total</div>
                        <div class="section-content">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Sender Address</th>
                                        <th>Source</th>
                                        <th>Amount</th>
                                        <th>Txs</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {incoming_html if incoming_html else '<tr><td colspan="4" style="padding: 20px; text-align: center; color: var(--text-secondary);">No incoming transfers found (analyze to fetch)</td></tr>'}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <h2>📥 Outgoing Transfers (Where This Account Sent SOL)</h2>
                    <div class="section">
                        <div class="section-title">✅ {len(outgoing_transfers)} Recipients | {total_outgoing:.2f} SOL Total</div>
                        <div class="section-content">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Recipient Address</th>
                                        <th>Destination</th>
                                        <th>Amount</th>
                                        <th>Txs</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {outgoing_html if outgoing_html else '<tr><td colspan="4" style="padding: 20px; text-align: center; color: var(--text-secondary);">No outgoing transfers found (analyze to fetch)</td></tr>'}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <div class="section">
                        <div class="section-title">🎯 {len(transfers)} Creators Funded</div>
                        <div class="section-content">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Creator Address</th>
                                        <th>Amount</th>
                                        <th>Date</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {transfers_html if transfers_html else '<tr><td colspan="3" style="padding: 20px; text-align: center; color: var(--text-secondary);">No creators found</td></tr>'}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </body>
        </html>
        """
        return html

    except Exception as e:
        import traceback
        return f"<html><body style='background: linear-gradient(135deg, #0a0a0e 0%, #0d0d15 100%); background-attachment: fixed; color: var(--text-primary);'><h1>Error</h1><p>{str(e)}</p><pre>{traceback.format_exc()}</pre></body></html>", 500


@app.route('/api/duplicate-senders')
def api_duplicate_senders():
    """Get senders that send to multiple funders (duplicate senders analysis)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get senders sending to multiple funders (excluding CEX/INFRA accounts)
        cursor.execute("""
            SELECT
                sender_address,
                COUNT(DISTINCT funder_address) as funder_count,
                COUNT(*) as transfer_count,
                SUM(amount_sol) as total_sol,
                MIN(block_time) as first_seen,
                MAX(block_time) as last_seen,
                MAX(sender_type) as sender_type
            FROM funder_incoming_transfers
            WHERE sender_address IS NOT NULL
            GROUP BY sender_address
            HAVING COUNT(DISTINCT funder_address) > 1
            ORDER BY funder_count DESC, total_sol DESC
        """)

        all_senders = [dict(row) for row in cursor.fetchall()]

        # Filter out CEX and INFRA accounts
        senders = [s for s in all_senders if s['sender_type'] and s['sender_type'].upper() not in ('CEX', 'INFRA')]

        # For each sender, count related tokens
        for sender in senders:
            cursor.execute("""
                SELECT COUNT(DISTINCT ta.mint) as token_count
                FROM creator_funders cf
                JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
                WHERE cf.funder_address IN (
                    SELECT DISTINCT funder_address
                    FROM funder_incoming_transfers
                    WHERE sender_address = ?
                )
            """, (sender['sender_address'],))

            result = cursor.fetchone()
            sender['related_token_count'] = result['token_count'] if result else 0

        # Sort by related token count (descending), then by funder count
        senders.sort(key=lambda x: (-x['related_token_count'], -x['funder_count']))

        conn.close()

        return jsonify({
            'senders': senders,
            'total_duplicate_senders': len(senders)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sender-tokens/<sender_address>')
def api_sender_tokens(sender_address: str):
    """Get all tokens funded by accounts that received from this sender"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all tokens funded by funders that received from this sender (excluding CEX and INFRA)
        cursor.execute("""
            SELECT DISTINCT
                ta.mint,
                ta.created_at,
                ta.risk_level,
                ROUND(ta.rug_probability, 3) as rug_probability,
                ta.earliest_tx_creator
            FROM creator_funders cf
            JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            WHERE cf.funder_address IN (
                SELECT DISTINCT funder_address
                FROM funder_incoming_transfers
                WHERE sender_address = ?
            ) AND cf.is_cex = 0
            ORDER BY ta.created_at DESC
        """, (sender_address,))

        # Calculate funding for each token from the sender's funder network
        tokens_list = []
        for token_row in cursor.fetchall():
            token_mint = token_row['mint']
            creator_addr = token_row['earliest_tx_creator']

            # Get total funding for this token from this creator's funders that came from the sender (excluding CEX and INFRA)
            cursor.execute("""
                SELECT
                    SUM(cf.amount_sol) as total_funding_sol,
                    COUNT(DISTINCT cf.funder_address) as num_funders
                FROM creator_funders cf
                WHERE cf.creator_address = ? AND cf.funder_address IN (
                    SELECT DISTINCT funder_address
                    FROM funder_incoming_transfers
                    WHERE sender_address = ?
                ) AND cf.is_cex = 0
            """, (creator_addr, sender_address))

            funding_row = cursor.fetchone()

            tokens_list.append({
                'mint': token_row['mint'],
                'created_at': token_row['created_at'],
                'risk_level': token_row['risk_level'],
                'rug_probability': token_row['rug_probability'],
                'creator_address': creator_addr,
                'total_funding_sol': funding_row['total_funding_sol'] if funding_row else 0,
                'num_funders': funding_row['num_funders'] if funding_row else 0
            })

        conn.close()

        return jsonify({
            'sender_address': sender_address,
            'tokens': tokens_list,
            'total_tokens': len(tokens_list)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/duplicate-tokens')
def api_duplicate_tokens():
    """Get tokens funded by multiple senders (coordinated funding)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get tokens funded by multiple senders
        cursor.execute("""
            SELECT
                ta.mint,
                ta.earliest_tx_creator as creator,
                ta.created_at,
                ta.risk_level,
                ta.rug_probability,
                ta.market_cap_current,
                COUNT(DISTINCT fit.sender_address) as num_senders,
                COUNT(DISTINCT fit.funder_address) as num_funders,
                ROUND(SUM(fit.amount_sol), 2) as total_sol
            FROM token_analysis ta
            LEFT JOIN creator_funders cf ON cf.creator_address = ta.earliest_tx_creator
            LEFT JOIN funder_incoming_transfers fit ON fit.funder_address = cf.funder_address
            WHERE ta.earliest_tx_creator IS NOT NULL
              AND fit.sender_address IS NOT NULL
            GROUP BY ta.mint
            HAVING COUNT(DISTINCT fit.sender_address) > 1
            ORDER BY ta.created_at DESC, num_senders DESC
        """)

        tokens = [dict(row) for row in cursor.fetchall()]

        conn.close()

        return jsonify({
            'tokens': tokens,
            'total_duplicate_tokens': len(tokens)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-networks')
def api_funder_networks():
    """Get all funders with their network info (tokens, creators, senders)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all funders and their network stats (excluding CEX and INFRA accounts)
        cursor.execute("""
            SELECT
                cf.funder_address,
                COUNT(DISTINCT cf.creator_address) as creators_funded,
                COUNT(DISTINCT ta.mint) as tokens_funded,
                ROUND(SUM(cf.amount_sol), 2) as total_sol_out,
                COUNT(DISTINCT fit.sender_address) as num_senders,
                ROUND(SUM(fit.amount_sol), 2) as total_sol_in,
                MIN(CAST(strftime('%s', cf.first_detected_at) AS INTEGER)) as earliest_funding,
                MAX(CAST(strftime('%s', cf.first_detected_at) AS INTEGER)) as latest_funding,
                COALESCE(cf.is_cex, 0) as is_cex,
                COALESCE(cf.cex_exchange, '') as cex_exchange
            FROM creator_funders cf
            LEFT JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            LEFT JOIN funder_incoming_transfers fit ON fit.funder_address = cf.funder_address
            WHERE COALESCE(cf.is_cex, 0) = 0
            AND cf.funder_address NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
            GROUP BY cf.funder_address
            HAVING COUNT(DISTINCT ta.mint) > 0
            ORDER BY tokens_funded DESC, total_sol_out DESC
        """)

        funders = [dict(row) for row in cursor.fetchall()]
        conn.close()

        return jsonify({
            'funders': funders,
            'total_funders': len(funders)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funding-networks')
def api_funding_networks():
    """Get funding network clusters (groups of funders that fund overlapping tokens)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all active networks with their stats
        cursor.execute("""
            SELECT
                fn.network_id,
                fn.network_name,
                fn.total_members,
                fn.total_tokens_funded,
                fn.total_creators_funded,
                ROUND(fn.total_sol, 2) as total_sol
            FROM funding_networks fn
            ORDER BY fn.total_sol DESC, fn.total_tokens_funded DESC
        """)

        networks = []
        for row in cursor.fetchall():
            members = []
            # Get detailed member info for each network
            cursor.execute("""
                SELECT
                    fnm.funder_address,
                    fnm.role,
                    fnm.shared_tokens_count,
                    fnm.tokens_unique_to_member,
                    ROUND(fnm.total_sol_out, 2) as total_sol_out,
                    (SELECT COUNT(DISTINCT cw.cex_address) FROM cex_wallets cw WHERE cw.cex_address = fnm.funder_address AND cw.is_active = 1) as is_cex,
                    (SELECT exchange_name FROM cex_wallets WHERE cex_address = fnm.funder_address AND is_active = 1 LIMIT 1) as exchange_name
                FROM funding_network_members fnm
                WHERE fnm.network_id = ?
                ORDER BY fnm.total_sol_out DESC
            """, (row['network_id'],))

            for member_row in cursor.fetchall():
                members.append({
                    'address': member_row['funder_address'],
                    'role': member_row['role'],
                    'shared_tokens': member_row['shared_tokens_count'],
                    'unique_tokens': member_row['tokens_unique_to_member'],
                    'total_sol': member_row['total_sol_out'],
                    'is_cex': member_row['is_cex'],
                    'exchange': member_row['exchange_name']
                })

            networks.append({
                'network_id': row['network_id'],
                'name': row['network_name'],
                'members': members,
                'member_count': row['total_members'],
                'total_tokens': row['total_tokens_funded'],
                'total_creators': row['total_creators_funded'],
                'total_sol': row['total_sol']
            })

        conn.close()
        return jsonify({
            'networks': networks,
            'total_networks': len(networks)
        })

    except Exception as e:
        print(f"[FUNDING_NETWORKS_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/funding-networks-list')

def api_funding_networks_list():
    """Get simplified list of all funding networks with their names and stats"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all networks with basic stats including senders count
        # IMPORTANT: creators_count now shows ACTUAL creators who launched tokens in the network
        cursor.execute("""
            SELECT
                fn.network_id,
                fn.network_name,
                fn.total_members as funders_count,
                COUNT(DISTINCT fnt.mint) as tokens_count,
                COUNT(DISTINCT ta.earliest_tx_creator) as creators_count,
                ROUND(fn.total_sol, 2) as total_sol,
                COUNT(DISTINCT fit.sender_address) as senders_count
            FROM funding_networks fn
            LEFT JOIN funding_network_members fnm ON fn.network_id = fnm.network_id
            LEFT JOIN funding_network_shared_tokens fnt ON fn.network_id = fnt.network_id
            LEFT JOIN token_analysis ta ON fnt.mint = ta.mint
            LEFT JOIN funder_incoming_transfers fit ON fit.funder_address = fnm.funder_address
            GROUP BY fn.network_id, fn.network_name
            ORDER BY fn.total_members DESC
        """)

        networks = []

        # Generate consistent memorable names for networks
        adjectives = ['Shadow', 'Ghost', 'Phantom', 'Silent', 'Hidden', 'Dark', 'Swift', 'Rapid',
                     'Sleek', 'Sharp', 'Cunning', 'Sly', 'Stealthy', 'Crafty', 'Clever', 'Subtle',
                     'Veiled', 'Masked', 'Cloaked', 'Whispered', 'Covert', 'Secret', 'Mystic', 'Ancient',
                     'Stellar', 'Quantum', 'Digital', 'Spectral', 'Ethereal', 'Twilight', 'Nocturnal']
        nouns = ['Circle', 'Ring', 'Syndicate', 'Cabal', 'Order', 'Society', 'Collective', 'Alliance',
                'Coalition', 'Union', 'Cartel', 'Consortium', 'Federation', 'Network', 'Nexus', 'Web',
                'Chain', 'Echo', 'Whisper', 'Shadow', 'Phantom', 'Specter', 'Entity', 'Force',
                'Nexus', 'Confluence', 'Fusion', 'Convergence', 'Resonance', 'Harmony', 'Synergy']

        import random
        random.seed(42)  # Consistent seed for reproducible names

        for idx, row in enumerate(cursor.fetchall()):
            # Generate memorable name using network_id as seed
            random.seed(42 + row['network_id'])  # Use network_id to get consistent names per network
            adj = random.choice(adjectives)
            noun = random.choice(nouns)
            memorable_name = f"{adj}{noun}"

            # Use the memorable name
            network_name = memorable_name
            
            networks.append({
                'network_id': row['network_id'],
                'name': network_name,
                'funders': row['funders_count'],
                'tokens': row['tokens_count'],
                'creators': row['creators_count'],
                'senders': row['senders_count'] or 0,
                'total_sol': row['total_sol']
            })

        conn.close()
        return jsonify({
            'networks': networks,
            'total_networks': len(networks)
        })

    except Exception as e:
        print(f"[FUNDING_NETWORKS_LIST_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/funding-network-details/<int:network_id>')

def api_funding_network_details(network_id):
    """Get detailed stats for a specific network by ID"""

    def new_path():
        """NEW PATH: Use networks_release and convert ID to name"""
        # Map network_id to network_name using deterministic ordering
        network_name = get_network_name_from_id(network_id)
        if not network_name:
            return {'error': 'Network not found'}, 404

        # Get network from networks_release
        network_data = get_network_release_by_name(network_name, include_evidence=False)
        if not network_data:
            return {'error': 'Network not found'}, 404

        # Return network details in same schema as legacy path
        return {
            'network_id': network_id,
            'network_name': network_name,
            'funders': network_data.get('network_size', 0),
            'senders': 0,  # Not available in networks_release
            'creators': network_data.get('network_size', 0),
            'tokens': 0,  # Not available in networks_release
            'total_sol': 0.0,  # Not available in networks_release
            'token_list': [],  # Not available in networks_release
            'root_operator_flows': [],  # Simplified for new path
            'network_risk_level': network_data.get('network_risk_level'),
            'network_type': network_data.get('network_type'),
            'stability_state': network_data.get('stability_state'),
            'build_version': network_data.get('build_version'),
            'last_built_at': network_data.get('last_built_at')
        }, 200

    def legacy_path():
        """OLD PATH: Use legacy funding_networks table"""
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get network basic info
        cursor.execute("""
            SELECT
                fn.network_id,
                fn.network_name,
                fn.total_members as funders_count,
                COUNT(DISTINCT fnt.mint) as tokens_count,
                COUNT(DISTINCT ta.earliest_tx_creator) as creators_count,
                ROUND(fn.total_sol, 2) as total_sol
            FROM funding_networks fn
            LEFT JOIN funding_network_shared_tokens fnt ON fn.network_id = fnt.network_id
            LEFT JOIN token_analysis ta ON fnt.mint = ta.mint
            WHERE fn.network_id = ?
            GROUP BY fn.network_id
        """, (network_id,))

        network_row = cursor.fetchone()
        if not network_row:
            conn.close()
            return {'error': 'Network not found'}, 404

        # Count unique senders
        cursor.execute("""
            SELECT COUNT(DISTINCT COALESCE(fit.sender_address, 0)) as senders_count
            FROM funder_incoming_transfers fit
            WHERE fit.funder_address IN (
                SELECT fnm.funder_address
                FROM funding_network_members fnm
                WHERE fnm.network_id = ?
            )
            AND fit.sender_address IS NOT NULL
        """, (network_id,))

        senders_row = cursor.fetchone()
        senders_count = senders_row['senders_count'] if senders_row and senders_row['senders_count'] else 0

        # Get the tokens this network coordinates
        cursor.execute("""
            SELECT DISTINCT mint
            FROM funding_network_shared_tokens
            WHERE network_id = ?
            ORDER BY mint
        """, (network_id,))

        tokens = [row['mint'] for row in cursor.fetchall()]

        # Get root operators (funders that fund multiple creators in this network)
        cursor.execute("""
            SELECT DISTINCT cf.funder_address
            FROM creator_funders cf
            WHERE cf.creator_address IN (
                SELECT DISTINCT ta.earliest_tx_creator
                FROM funding_network_shared_tokens fnt
                JOIN token_analysis ta ON fnt.mint = ta.mint
                WHERE fnt.network_id = ?
            )
            GROUP BY cf.funder_address
            HAVING COUNT(DISTINCT cf.creator_address) >= 2
            ORDER BY COUNT(DISTINCT cf.creator_address) DESC
        """, (network_id,))

        root_operators = [row['funder_address'] for row in cursor.fetchall()]

        # Build root operator flows
        root_operator_flows = []
        for root_op in root_operators:
            # Get creators funded by this root operator in this network
            cursor.execute("""
                SELECT DISTINCT cf.creator_address, cf.amount_sol
                FROM creator_funders cf
                WHERE cf.funder_address = ?
                AND cf.creator_address IN (
                    SELECT DISTINCT ta.earliest_tx_creator
                    FROM funding_network_shared_tokens fnt
                    JOIN token_analysis ta ON fnt.mint = ta.mint
                    WHERE fnt.network_id = ?
                )
                ORDER BY cf.amount_sol DESC
            """, (root_op, network_id))

            funded_creators = [{'creator': row['creator_address'], 'sol': float(row['amount_sol'])} for row in cursor.fetchall()]

            if not funded_creators:
                continue

            total_sol_to_creators = sum(c['sol'] for c in funded_creators)

            # Get upstream sources (senders to this root operator)
            cursor.execute("""
                SELECT DISTINCT sender_address, COUNT(*) as transfer_count
                FROM funder_incoming_transfers
                WHERE funder_address = ?
                GROUP BY sender_address
                ORDER BY transfer_count DESC
                LIMIT 5
            """, (root_op,))

            upstream_sources = [{'sender': row['sender_address'], 'transfers': row['transfer_count']} for row in cursor.fetchall()]

            # Build example address flows
            example_flows = []
            if upstream_sources and funded_creators:
                for sender_data in upstream_sources[:3]:
                    sender = sender_data['sender']
                    for creator_data in funded_creators[:2]:
                        example_flows.append({
                            'sender': sender,
                            'funder': root_op,
                            'creator': creator_data['creator'],
                            'sol_to_creator': creator_data['sol']
                        })
                    if len(example_flows) >= 3:
                        break

            # Get downstream creators' token details
            creator_list = [c['creator'] for c in funded_creators[:10]]
            if creator_list:
                placeholders = ','.join('?' * len(creator_list))
                cursor.execute(f"""
                    SELECT
                        ta.mint,
                        ta.earliest_tx_creator,
                        ta.risk_level,
                        ta.rug_probability
                    FROM token_analysis ta
                    WHERE ta.earliest_tx_creator IN ({placeholders})
                    ORDER BY ta.rug_probability DESC
                    LIMIT 10
                """, creator_list)

                downstream_creators = [{
                    'mint': row['mint'],
                    'creator': row['earliest_tx_creator'],
                    'risk_level': row['risk_level'],
                    'rug_probability': float(row['rug_probability']) if row['rug_probability'] else 0
                } for row in cursor.fetchall()]
            else:
                downstream_creators = []

            root_operator_flows.append({
                'root_operator': root_op,
                'creators_funded': len(funded_creators),
                'total_sol_sent': total_sol_to_creators,
                'transfer_count': len(funded_creators),
                'upstream_sources': upstream_sources,
                'downstream_creators': downstream_creators,
                'example_flows': example_flows
            })

        conn.close()

        return {
            'network_id': network_row['network_id'],
            'network_name': network_row['network_name'],
            'funders': network_row['funders_count'],
            'senders': senders_count,
            'creators': network_row['creators_count'],
            'tokens': network_row['tokens_count'],
            'total_sol': network_row['total_sol'],
            'token_list': tokens,
            'root_operator_flows': root_operator_flows
        }, 200

    return route_phase2c('/api/funding-network-details', new_path, legacy_path)


@app.route('/api/build-funding-networks', methods=['POST'])
def api_build_funding_networks():
    """Build/rebuild funding network clusters from scratch"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()

        # Clear existing networks
        cursor.execute("DELETE FROM funding_network_shared_tokens")
        cursor.execute("DELETE FROM funding_network_members")
        cursor.execute("DELETE FROM funding_networks")
        conn.commit()

        # Get all funders with their funded tokens (excluding CEX/INFRA)
        cursor.execute("""
            SELECT DISTINCT cf.funder_address
            FROM creator_funders cf
            WHERE cf.funder_address NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
            AND COALESCE(cf.is_cex, 0) = 0
        """)

        all_funders = [row[0] for row in cursor.fetchall()]
        print(f"[NETWORKS] Found {len(all_funders)} non-CEX funders to cluster", flush=True)

        # Build a mapping of funder -> set of tokens they fund
        funder_to_tokens = {}
        cursor.execute("""
            SELECT DISTINCT cf.funder_address, ta.mint
            FROM creator_funders cf
            JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            WHERE cf.funder_address NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
            AND COALESCE(cf.is_cex, 0) = 0
            AND ta.mint IS NOT NULL
        """)

        for funder, mint in cursor.fetchall():
            if funder not in funder_to_tokens:
                funder_to_tokens[funder] = set()
            funder_to_tokens[funder].add(mint)

        print(f"[NETWORKS] Built token map for {len(funder_to_tokens)} funders", flush=True)

        # Union-Find data structure for efficient clustering
        parent = {}
        
        def find(x):
            if x not in parent:
                parent[x] = x
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]
        
        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Pre-compute overlaps: For each pair of funders, if they share 2+ tokens, union them
        funder_list = list(funder_to_tokens.keys())
        print(f"[NETWORKS] Computing overlaps for {len(funder_list)} funders", flush=True)
        
        for i, funder1 in enumerate(funder_list):
            if i % 1000 == 0:
                print(f"[NETWORKS] Processed {i}/{len(funder_list)} funders for overlap...", flush=True)
            
            for funder2 in funder_list[i+1:]:
                overlap = len(funder_to_tokens[funder1] & funder_to_tokens[funder2])
                if overlap >= 2:  # Threshold: 2+ shared tokens
                    union(funder1, funder2)

        # Group funders by their root parent
        networks_dict = {}
        for funder in funder_list:
            root = find(funder)
            if root not in networks_dict:
                networks_dict[root] = []
            networks_dict[root].append(funder)

        # Only keep networks with 2+ members
        networks_dict = {k: v for k, v in networks_dict.items() if len(v) >= 2}
        print(f"[NETWORKS] Found {len(networks_dict)} networks with 2+ members", flush=True)

        # Insert networks into database
        network_id = 1
        for root, network_members in sorted(networks_dict.items(), key=lambda x: -len(x[1])):
            network_name = f"Network_{network_id}"
            cursor.execute("""
                INSERT INTO funding_networks (network_name, total_members)
                VALUES (?, ?)
            """, (network_name, len(network_members)))
            conn.commit()

            current_network_id = cursor.lastrowid

            # Add members to network
            for member_funder in network_members:
                member_tokens = funder_to_tokens.get(member_funder, set())

                # Count how many tokens this member shares with other network members
                shared_tokens = set()
                for other_member in network_members:
                    if other_member != member_funder:
                        shared_tokens.update(member_tokens & funder_to_tokens.get(other_member, set()))

                unique_tokens = len(member_tokens - shared_tokens)

                # Get total SOL from this funder
                cursor.execute("""
                    SELECT ROUND(SUM(amount_sol), 2) as total
                    FROM creator_funders
                    WHERE funder_address = ?
                """, (member_funder,))

                total_sol = cursor.fetchone()[0] or 0

                cursor.execute("""
                    INSERT INTO funding_network_members
                    (network_id, funder_address, shared_tokens_count, tokens_unique_to_member, total_sol_out)
                    VALUES (?, ?, ?, ?, ?)
                """, (current_network_id, member_funder, len(shared_tokens), unique_tokens, total_sol))
                conn.commit()

                # Add shared tokens to network
                for token in shared_tokens:
                    cursor.execute("""
                        INSERT OR IGNORE INTO funding_network_shared_tokens
                        (network_id, mint)
                        VALUES (?, ?)
                    """, (current_network_id, token))
                    conn.commit()

            # Update network totals
            cursor.execute("""
                SELECT
                    COUNT(DISTINCT funder_address) as member_count,
                    COUNT(DISTINCT mint) as token_count,
                    SUM(total_sol_out) as total_sol
                FROM funding_network_members fnm
                LEFT JOIN funding_network_shared_tokens fnst ON fnm.network_id = fnst.network_id
                WHERE fnm.network_id = ?
                GROUP BY fnm.network_id
            """, (current_network_id,))

            stats = cursor.fetchone()
            if stats:
                cursor.execute("""
                    UPDATE funding_networks
                    SET total_members = ?,
                        total_tokens_funded = ?,
                        total_sol = ?
                    WHERE network_id = ?
                """, (len(network_members), stats[1] or 0, stats[2] or 0, current_network_id))
                conn.commit()

            network_id += 1
            if network_id % 10 == 0:
                print(f"[NETWORKS] Inserted {network_id} networks...", flush=True)

        # Now build single-funder networks (one funder funding 2+ creators)
        print(f"[NETWORKS] Building single-funder networks...", flush=True)
        cursor.execute("""
            SELECT funder_address, COUNT(DISTINCT creator_address) as creator_count
            FROM creator_funders
            WHERE funder_address NOT IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1)
            AND COALESCE(is_cex, 0) = 0
            GROUP BY funder_address
            HAVING creator_count >= 2
            ORDER BY creator_count DESC
        """)

        single_funder_networks = cursor.fetchall()
        single_funder_count = 0

        for row in single_funder_networks:
            funder_address = row['funder_address']
            creator_count = row['creator_count']

            # Create a single-funder network
            network_name = f"SingleFunder_{single_funder_count + 1}"
            cursor.execute("""
                INSERT INTO funding_networks (network_name, total_members, network_type)
                VALUES (?, ?, ?)
            """, (network_name, 1, 'single_funder'))
            conn.commit()

            current_network_id = cursor.lastrowid

            # Add the single funder as network member
            # Get all their tokens and total SOL
            cursor.execute("""
                SELECT
                    COUNT(DISTINCT ta.mint) as token_count,
                    ROUND(SUM(cf.amount_sol), 2) as total_sol
                FROM creator_funders cf
                JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
                WHERE cf.funder_address = ?
            """, (funder_address,))

            stats = cursor.fetchone()
            token_count = stats['token_count'] or 0
            total_sol = stats['total_sol'] or 0

            cursor.execute("""
                INSERT INTO funding_network_members
                (network_id, funder_address, shared_tokens_count, tokens_unique_to_member, total_sol_out)
                VALUES (?, ?, ?, ?, ?)
            """, (current_network_id, funder_address, token_count, token_count, total_sol))

            # Add all their funded tokens to the network
            cursor.execute("""
                SELECT DISTINCT ta.mint
                FROM creator_funders cf
                JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
                WHERE cf.funder_address = ?
            """, (funder_address,))

            tokens = [row['mint'] for row in cursor.fetchall()]
            for mint in tokens:
                cursor.execute("""
                    INSERT OR IGNORE INTO funding_network_shared_tokens
                    (network_id, mint)
                    VALUES (?, ?)
                """, (current_network_id, mint))

            # Update network stats
            cursor.execute("""
                UPDATE funding_networks
                SET total_tokens_funded = ?,
                    total_creators_funded = ?,
                    total_sol = ?
                WHERE network_id = ?
            """, (token_count, creator_count, total_sol, current_network_id))
            conn.commit()

            single_funder_count += 1
            if single_funder_count % 10 == 0:
                print(f"[NETWORKS] Inserted {single_funder_count} single-funder networks...", flush=True)

        conn.close()
        print(f"[NETWORKS] ✅ Network building complete: {network_id - 1} shared networks + {single_funder_count} single-funder networks created", flush=True)
        return jsonify({'status': 'Networks built successfully', 'networks_created': network_id - 1}), 201

    except Exception as e:
        print(f"[BUILD_NETWORKS_API] Error: {e}", flush=True)
        return jsonify({'error': str(e)}), 500


def update_networks_for_new_token(mint: str, creator: str):
    """
    Incrementally update existing networks when a new token is launched.
    Checks if the creator's funders/senders are in existing networks.
    If yes, adds the token to those networks without rebuilding.

    Returns: List of affected network IDs for UI refresh
    """
    affected_networks = []

    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.cursor()

        # Get all funders of this creator
        cursor.execute("""
            SELECT DISTINCT funder_address
            FROM creator_funders
            WHERE creator_address = ?
        """, (creator,))

        creator_funders = [row[0] for row in cursor.fetchall()]

        if not creator_funders:
            # No funders yet, nothing to add to networks
            conn.close()
            return affected_networks

        print(f"[NETWORK_UPDATE] Token {mint[:8]}... | Creator {creator[:8]}... has {len(creator_funders)} funders", flush=True)

        # Get all senders to these funders
        cursor.execute("""
            SELECT DISTINCT sender_address
            FROM funder_incoming_transfers
            WHERE funder_address IN ({})
        """.format(','.join('?' * len(creator_funders))), creator_funders)

        creator_senders = [row[0] for row in cursor.fetchall()]

        # Combine funders and senders
        all_addresses = set(creator_funders + creator_senders)
        print(f"[NETWORK_UPDATE] Total addresses (funders + senders): {len(all_addresses)}", flush=True)

        # Find which networks contain any of these addresses
        cursor.execute("""
            SELECT DISTINCT network_id
            FROM funding_network_members
            WHERE funder_address IN ({})
        """.format(','.join('?' * len(list(all_addresses)))), list(all_addresses))

        affected_networks = [row[0] for row in cursor.fetchall()]

        if not affected_networks:
            print(f"[NETWORK_UPDATE] No existing networks contain this creator's funders/senders", flush=True)
            conn.close()
            return affected_networks

        print(f"[NETWORK_UPDATE] Found {len(affected_networks)} affected networks", flush=True)

        # Add token to each affected network
        for network_id in affected_networks:
            cursor.execute("""
                INSERT OR IGNORE INTO funding_network_shared_tokens
                (network_id, mint)
                VALUES (?, ?)
            """, (network_id, mint))
            conn.commit()

        # Update network statistics for affected networks
        for network_id in affected_networks:
            cursor.execute("""
                SELECT
                    COUNT(DISTINCT mint) as token_count,
                    SUM(total_sol_out) as total_sol
                FROM funding_network_members fnm
                LEFT JOIN funding_network_shared_tokens fnst ON fnm.network_id = fnst.network_id
                WHERE fnm.network_id = ?
                GROUP BY fnm.network_id
            """, (network_id,))

            stats = cursor.fetchone()
            if stats:
                token_count, total_sol = stats
                cursor.execute("""
                    UPDATE funding_networks
                    SET total_tokens_funded = ?,
                        total_sol = ?
                    WHERE network_id = ?
                """, (token_count or 0, total_sol or 0, network_id))
                conn.commit()

        print(f"[NETWORK_UPDATE] ✅ Added token {mint[:8]}... to {len(affected_networks)} networks", flush=True)
        conn.close()

    except Exception as e:
        print(f"[NETWORK_UPDATE] Error updating networks: {e}", flush=True)

    return affected_networks


@app.route('/api/tokens-by-funder/<funder_address>')
def api_tokens_by_funder(funder_address: str):
    """Get all tokens funded by a specific funder address"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all tokens funded by this funder
        cursor.execute("""
            SELECT
                ta.mint,
                ta.earliest_tx_creator as creator,
                ta.created_at,
                ta.risk_level,
                ta.rug_probability,
                ta.market_cap_current,
                cf.amount_sol,
                COUNT(DISTINCT fit.sender_address) as num_senders
            FROM creator_funders cf
            JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            LEFT JOIN funder_incoming_transfers fit ON fit.funder_address = cf.funder_address
            WHERE cf.funder_address = ?
            GROUP BY ta.mint
            ORDER BY ta.created_at DESC
        """, (funder_address,))

        tokens = [dict(row) for row in cursor.fetchall()]

        # Get funder info
        cursor.execute("""
            SELECT
                COUNT(DISTINCT creator_address) as creators_funded,
                ROUND(SUM(amount_sol), 2) as total_sol_out,
                MIN(created_at) as earliest_funding,
                MAX(created_at) as latest_funding
            FROM (
                SELECT DISTINCT creator_address, amount_sol, created_at FROM creator_funders WHERE funder_address = ?
            )
        """, (funder_address,))
        funder_info = dict(cursor.fetchone()) if cursor.fetchone() else {}

        # Get funder incoming transfers
        cursor.execute("""
            SELECT
                COUNT(DISTINCT sender_address) as senders,
                ROUND(SUM(amount_sol), 2) as total_sol_in
            FROM funder_incoming_transfers
            WHERE funder_address = ?
        """, (funder_address,))
        incoming_info = dict(cursor.fetchone()) if cursor.fetchone() else {}

        conn.close()

        return jsonify({
            'funder_address': funder_address,
            'tokens': tokens,
            'total_tokens': len(tokens),
            'funder_info': {
                **funder_info,
                'senders': incoming_info.get('senders', 0),
                'total_in': incoming_info.get('total_sol_in', 0)
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/analyze-all-coordinated-funders', methods=['POST'])
def api_analyze_all_coordinated_funders():
    """Trigger transfer analysis for all funders funding multiple creators.

    This runs in background threads and returns immediately with status.
    Each funder's incoming/outgoing transfers are fetched and saved to DB.
    Respects the 'auto_extract_funders' toggle - returns error if toggle is OFF.
    """
    try:
        # Check if auto extraction is enabled
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('auto_extract_funders',))
        row = cursor.fetchone()
        auto_extract_enabled = row[0] == 'true' if row else False

        if not auto_extract_enabled:
            conn.close()
            return jsonify({
                'error': 'Auto Extract Funders toggle is OFF',
                'message': 'Enable the toggle on the main dashboard to allow funder extraction',
                'status': 'blocked'
            }), 403

        from src.extractors.funder_incoming_extractor import extract_for_creator
        import threading

        # Get all funders funding multiple creators
        cursor.execute("""
            SELECT DISTINCT funder_address
            FROM creator_funders
            GROUP BY funder_address
            HAVING COUNT(DISTINCT creator_address) > 1
            ORDER BY SUM(amount_sol) DESC
        """)

        funder_addresses = [row[0] for row in cursor.fetchall()]
        conn.close()

        # Trigger background analysis for each funder
        analyzed_count = 0
        skipped_count = 0

        for funder_address in funder_addresses:
            try:
                # Check if already analyzed
                conn = sqlite3.connect(DB_PATH, timeout=5)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM funder_incoming_transfers WHERE funder_address = ? LIMIT 1", (funder_address,))
                if cursor.fetchone()[0] > 0:
                    skipped_count += 1
                    conn.close()
                    continue
                conn.close()

                # Run analysis in background thread (non-blocking)
                thread = threading.Thread(target=extract_for_creator, args=(funder_address,), daemon=True)
                thread.start()
                analyzed_count += 1

            except Exception as e:
                print(f"[ERROR] Failed to queue analysis for {funder_address}: {str(e)}")

        return jsonify({
            'status': 'queued',
            'total_funders': len(funder_addresses),
            'queued_for_analysis': analyzed_count,
            'already_analyzed': skipped_count,
            'message': f'Queued {analyzed_count} funders for transfer analysis'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/analyze-funder-transfers', methods=['POST'])
def api_analyze_funder_transfers():
    """Trigger funder transfer analysis (incoming/outgoing) for a funder address.

    First checks if data already exists in database, returns immediately if found.
    Only extracts from Helius if no data exists.
    Respects the 'auto_extract_funders' toggle - returns error if toggle is OFF.
    """
    import sys

    try:
        data = request.get_json()
        funder_address = data.get('funder_address')

        if not funder_address:
            return jsonify({'error': 'No funder address provided'}), 400

        # Check if auto extraction is enabled
        conn_check = sqlite3.connect(DB_PATH, timeout=5)
        cursor_check = conn_check.cursor()
        cursor_check.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", ('auto_extract_funders',))
        row = cursor_check.fetchone()
        auto_extract_enabled = row[0] == 'true' if row else False
        conn_check.close()

        if not auto_extract_enabled:
            return jsonify({
                'error': 'Auto Extract Funders toggle is OFF',
                'message': 'Enable the toggle on the main dashboard to allow funder extraction',
                'status': 'blocked'
            }), 403

        # Check if we already have data for this funder in the database
        print(f"[ANALYZE] Checking database for {funder_address[:16]}...", flush=True)
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            cursor = conn.cursor()

            # Count existing transfers
            cursor.execute("SELECT COUNT(*) as count FROM funder_incoming_transfers WHERE funder_address = ?", (funder_address,))
            incoming_count = cursor.fetchone()['count']

            cursor.execute("SELECT COUNT(*) as count FROM funder_outgoing_transfers WHERE funder_address = ?", (funder_address,))
            outgoing_count = cursor.fetchone()['count']

            # Get total SOL
            cursor.execute("SELECT SUM(amount_sol) as total FROM funder_incoming_transfers WHERE funder_address = ?", (funder_address,))
            incoming_total = cursor.fetchone()['total'] or 0.0

            cursor.execute("SELECT SUM(amount_sol) as total FROM funder_outgoing_transfers WHERE funder_address = ?", (funder_address,))
            outgoing_total = cursor.fetchone()['total'] or 0.0

            conn.close()

            # If we have data, return it immediately from cache
            if incoming_count > 0 or outgoing_count > 0:
                print(f"[ANALYZE] ✅ Found in DB: {incoming_count} IN, {outgoing_count} OUT", flush=True)
                result = {
                    'funder': funder_address,
                    'incoming_count': incoming_count,
                    'outgoing_count': outgoing_count,
                    'total_sol': incoming_total + outgoing_total,
                    'source': 'database_cache'
                }

                # Store in memory cache for quick retrieval
                app.funder_analysis_cache = app.funder_analysis_cache or {}
                app.funder_analysis_cache[funder_address] = {
                    'status': 'completed',
                    'result': result,
                    'timestamp': datetime.now().isoformat()
                }

                return jsonify({
                    'status': 'completed',
                    'funder_address': funder_address,
                    'result': result,
                    'message': 'Results from database cache (no re-analysis needed)'
                })
        except Exception as e:
            print(f"[ANALYZE] Database check error (will extract): {e}", flush=True)
            # Continue with extraction if database check fails

        # No data found, need to extract
        print(f"[ANALYZE] No data in DB, will extract from Helius", flush=True)

        # Import here to avoid circular imports
        from src.extractors.funder_helius_extractor import extract_transfers_for_funder
        import threading

        # Run extraction in background thread
        def run_extraction():
            try:
                print(f"[FUNDER_ANALYSIS] Starting extraction for {funder_address[:16]}...", flush=True)

                result = extract_transfers_for_funder(funder_address)

                # Mark all creators with this funder as analyzed
                # NOTE: Only mark fully_analyzed=1 if we found sources (inflows > 0)
                # If zero inflows found, only set last_analyzed to allow re-extraction later
                try:
                    conn = sqlite3.connect(DB_PATH, timeout=5)
                    cursor = conn.cursor()

                    # Get all creators with this funder
                    cursor.execute("SELECT DISTINCT creator_address FROM creator_funders WHERE funder_address = ?", (funder_address,))
                    creators = [row[0] for row in cursor.fetchall()]

                    # Mark each creator-funder pair as analyzed
                    incoming_count = result.get('incoming_count', 0)
                    outgoing_count = result.get('outgoing_count', 0)

                    # Only mark fully_analyzed if we found sources (protects against Helius indexing lag)
                    fully_analyzed_flag = 1 if (incoming_count > 0 or outgoing_count > 0) else 0

                    for creator_addr in creators:
                        cursor.execute(
                            "UPDATE creator_funders SET last_analyzed = CURRENT_TIMESTAMP, fully_analyzed = ? WHERE creator_address = ? AND funder_address = ?",
                            (fully_analyzed_flag, creator_addr, funder_address)
                        )

                    conn.commit()
                    conn.close()

                    status_msg = f"✅ Found sources" if fully_analyzed_flag else "⏳ Zero sources (may re-extract)"
                    print(f"[FUNDER_ANALYSIS] {status_msg} for {len(creators)} creator(s)", flush=True)
                except Exception as mark_err:
                    print(f"[FUNDER_ANALYSIS] ⚠️ Could not mark analyzed: {mark_err}", flush=True)

                # Store result in memory/cache for quick retrieval
                app.funder_analysis_cache = app.funder_analysis_cache or {}
                app.funder_analysis_cache[funder_address] = {
                    'status': 'completed',
                    'result': result,
                    'timestamp': datetime.now().isoformat()
                }
                print(f"[FUNDER_ANALYSIS] ✅ Completed: {result.get('incoming_count', 0)} IN, {result.get('outgoing_count', 0)} OUT", flush=True)
            except Exception as e:
                import traceback
                print(f"[FUNDER_ANALYSIS] ❌ Error: {e}", flush=True)
                print(traceback.format_exc(), flush=True)

        # Start background thread (non-daemon so it completes)
        thread = threading.Thread(target=run_extraction, daemon=False)
        thread.start()

        return jsonify({
            'status': 'queued',
            'funder_address': funder_address,
            'message': 'Analysis queued (extracting from Helius)'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-analysis-status/<funder_address>', methods=['GET'])
def api_funder_analysis_status(funder_address):
    """Check status of funder analysis"""
    try:
        if funder_address in app.funder_analysis_cache:
            cache_entry = app.funder_analysis_cache[funder_address]
            return jsonify(cache_entry)
        else:
            return jsonify({'status': 'pending', 'funder_address': funder_address})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-senders/<funder_address>', methods=['GET'])
def api_funder_senders(funder_address: str):
    """Get all senders (incoming transfers) to a funder with classification (known/unknown)

    Prioritizes known accounts (infrastructure, CEX, blocklisted) first.
    """
    try:
        from src.utils.infra_mapping import get_account_info, get_cex_info

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all incoming transfers for this funder
        cursor.execute("""
            SELECT DISTINCT
                sender_address,
                SUM(amount_sol) as total_sol,
                sender_type,
                is_cex,
                cex_exchange,
                cex_type
            FROM funder_incoming_transfers
            WHERE funder_address = ?
            GROUP BY sender_address
            ORDER BY total_sol DESC
        """, (funder_address,))

        senders = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # Classify senders as known/unknown
        known_senders = []
        unknown_senders = []

        for sender in senders:
            sender_addr = sender['sender_address']
            classification = {
                'address': sender_addr,
                'amount_sol': sender['total_sol'],
                'type': sender['sender_type'],
                'is_known': False,
                'label': None,
                'category': None
            }

            # Check if already marked as CEX in database
            if sender['is_cex']:
                classification['is_known'] = True
                classification['category'] = 'CEX'
                classification['label'] = sender['cex_exchange']

            # Check if infrastructure account
            if not classification['is_known']:
                infra_info = get_account_info(sender_addr)
                if infra_info:
                    classification['is_known'] = True
                    classification['category'] = 'Infrastructure'
                    classification['label'] = infra_info.get('name', 'Unknown INFRA')

            # Check if CEX wallet
            if not classification['is_known']:
                cex_info = get_cex_info(sender_addr)
                if cex_info:
                    classification['is_known'] = True
                    classification['category'] = 'CEX'
                    classification['label'] = cex_info.get('name', 'Unknown CEX')

            # Check in address_tags for any other known classification
            if not classification['is_known']:
                try:
                    conn2 = sqlite3.connect(DB_PATH, timeout=5)
                    cursor2 = conn2.cursor()
                    cursor2.execute("SELECT tag_type FROM address_tags WHERE address = ? LIMIT 1", (sender_addr,))
                    tag_result = cursor2.fetchone()
                    if tag_result:
                        classification['is_known'] = True
                        classification['category'] = tag_result[0]
                    conn2.close()
                except:
                    pass

            if classification['is_known']:
                known_senders.append(classification)
            else:
                unknown_senders.append(classification)

        # Return known senders first, then unknown
        all_senders = known_senders + unknown_senders

        return jsonify({
            'funder_address': funder_address,
            'total_senders': len(all_senders),
            'known_senders': len(known_senders),
            'unknown_senders': len(unknown_senders),
            'senders': all_senders
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-transfer-details/<funder_address>')
def api_funder_transfer_details(funder_address: str):
    """Get complete funder transfer details (IN and OUT) with summaries"""
    try:
        from src.utils.infra_mapping import get_account_info, get_cex_info

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get funder info from creator_funders
        cursor.execute("""
            SELECT DISTINCT funder_address, COUNT(DISTINCT creator_address) as creator_count
            FROM creator_funders
            WHERE funder_address = ?
            GROUP BY funder_address
        """, (funder_address,))
        funder_info = cursor.fetchone()

        # Get incoming transfers (who funded this funder)
        cursor.execute("""
            SELECT
                sender_address,
                SUM(amount_sol) as total_amount,
                COUNT(*) as transaction_count,
                sender_type,
                is_cex,
                cex_exchange,
                cex_type,
                MIN(block_time) as first_transfer,
                MAX(block_time) as last_transfer
            FROM funder_incoming_transfers
            WHERE funder_address = ?
            GROUP BY sender_address
            ORDER BY total_amount DESC
        """, (funder_address,))
        incoming_transfers = [dict(row) for row in cursor.fetchall()]

        # Get outgoing transfers (where this funder sent SOL)
        cursor.execute("""
            SELECT
                recipient_address,
                SUM(amount_sol) as total_amount,
                COUNT(*) as transaction_count,
                recipient_type,
                is_cex,
                cex_exchange,
                cex_type,
                MIN(block_time) as first_transfer,
                MAX(block_time) as last_transfer
            FROM funder_outgoing_transfers
            WHERE funder_address = ?
            GROUP BY recipient_address
            ORDER BY total_amount DESC
        """, (funder_address,))
        outgoing_transfers = [dict(row) for row in cursor.fetchall()]

        conn.close()

        # Enrich incoming transfers with classification
        incoming_enriched = []
        total_incoming = 0
        for t in incoming_transfers:
            total_incoming += t['total_amount']
            addr = t['sender_address']
            classification = {'is_known': False, 'label': None, 'category': None}

            if t['is_cex']:
                classification = {'is_known': True, 'label': t['cex_exchange'], 'category': 'CEX'}
            else:
                infra = get_account_info(addr)
                if infra:
                    classification = {'is_known': True, 'label': infra.get('name'), 'category': 'Infrastructure'}
                else:
                    cex = get_cex_info(addr)
                    if cex:
                        classification = {'is_known': True, 'label': cex.get('name'), 'category': 'CEX'}

            incoming_enriched.append({
                'address': addr,
                'amount_sol': t['total_amount'],
                'transaction_count': t['transaction_count'],
                'type': t['sender_type'],
                **classification
            })

        # Enrich outgoing transfers with classification
        outgoing_enriched = []
        total_outgoing = 0
        for t in outgoing_transfers:
            total_outgoing += t['total_amount']
            addr = t['recipient_address']
            classification = {'is_known': False, 'label': None, 'category': None}

            if t['is_cex']:
                classification = {'is_known': True, 'label': t['cex_exchange'], 'category': 'CEX'}
            else:
                infra = get_account_info(addr)
                if infra:
                    classification = {'is_known': True, 'label': infra.get('name'), 'category': 'Infrastructure'}
                else:
                    cex = get_cex_info(addr)
                    if cex:
                        classification = {'is_known': True, 'label': cex.get('name'), 'category': 'CEX'}

            outgoing_enriched.append({
                'address': addr,
                'amount_sol': t['total_amount'],
                'transaction_count': t['transaction_count'],
                'type': t['recipient_type'],
                **classification
            })

        return jsonify({
            'funder_address': funder_address,
            'creators_funded': funder_info['creator_count'] if funder_info else 0,
            'incoming_transfers': {
                'total_senders': len(incoming_enriched),
                'total_sol': total_incoming,
                'known_senders': sum(1 for t in incoming_enriched if t['is_known']),
                'senders': incoming_enriched[:50]  # Limit to 50
            },
            'outgoing_transfers': {
                'total_recipients': len(outgoing_enriched),
                'total_sol': total_outgoing,
                'known_recipients': sum(1 for t in outgoing_enriched if t['is_known']),
                'recipients': outgoing_enriched[:50]  # Limit to 50
            },
            'net_flow': total_incoming - total_outgoing
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-service-history/<creator_address>')
def api_creator_service_history(creator_address: str):
    """Get full history of service usage (jitotip, meteora, debridge, axiom) for a creator"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all service history records for this creator
        cursor.execute("""
            SELECT
                tag,
                amount_sol,
                tx_signature,
                mint,
                network_fee_sol,
                tip_percentage,
                tx_type,
                created_at
            FROM creator_service_history
            WHERE creator_address = ?
            ORDER BY created_at DESC
        """, (creator_address,))
        
        history = [dict(row) for row in cursor.fetchall()]

        # Calculate statistics per tag
        cursor.execute("""
            SELECT 
                tag,
                COUNT(*) as count,
                SUM(amount_sol) as total,
                AVG(amount_sol) as avg,
                MIN(amount_sol) as min,
                MAX(amount_sol) as max
            FROM creator_service_history
            WHERE creator_address = ?
            GROUP BY tag
        """, (creator_address,))
        
        stats = {}
        for row in cursor.fetchall():
            stats[row['tag']] = {
                'count': row['count'],
                'total_sol': row['total'],
                'avg_sol': row['avg'],
                'min_sol': row['min'],
                'max_sol': row['max']
            }

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'history': history,
            'statistics': stats,
            'total_records': len(history)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/unified-merge-status')
def api_unified_merge_status():
    """Check unified recipient tracking status and run merge if needed"""
    try:
        from unified_recipient_tracker import UnifiedRecipientTracker

        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Check if unified table has data
        cursor.execute("SELECT COUNT(*) FROM creator_recipients_unified")
        unified_count = cursor.fetchone()[0]

        # Check source tables
        cursor.execute("SELECT COUNT(*) FROM creator_outgoing_transfers")
        outgoing_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_tx_ledger")
        ledger_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM network_coordinators")
        coordinator_count = cursor.fetchone()[0]

        conn.close()

        status = {
            'unified_recipients': unified_count,
            'outgoing_transfers': outgoing_count,
            'tx_ledger_entries': ledger_count,
            'network_coordinators': coordinator_count,
            'merge_needed': unified_count == 0 and outgoing_count > 0
        }

        # If merge needed and requested, run it
        if status['merge_needed'] and request.args.get('run_merge') == 'true':
            tracker = UnifiedRecipientTracker()
            merge_results = tracker.run_full_merge_and_analysis()
            status['merge_results'] = merge_results
            status['merge_completed'] = True

        return jsonify(status)

    except ImportError:
        return jsonify({'error': 'Unified recipient tracker not available'}), 503
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/empty-database', methods=['POST'])
def api_empty_database():
    """Empty all tokens, clustering, and creator tracking data"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Get counts before deletion
        cursor.execute("SELECT COUNT(*) FROM token_analysis")
        token_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM wallet_cluster_nodes")
        cluster_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_tx_ledger")
        ledger_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_watch")
        watch_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_state")
        state_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_funders")
        funders_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_outgoing_transfers")
        outgoing_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM creator_recipients_unified")
        unified_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM network_coordinators")
        coordinator_count = cursor.fetchone()[0]

        # Delete all data
        cursor.execute("DELETE FROM token_analysis")
        cursor.execute("DELETE FROM wallet_cluster_nodes")
        cursor.execute("DELETE FROM clustering_alerts")
        cursor.execute("DELETE FROM creator_tx_ledger")
        cursor.execute("DELETE FROM creator_state")
        cursor.execute("DELETE FROM creator_watch")
        cursor.execute("DELETE FROM creator_funders")
        cursor.execute("DELETE FROM creator_outgoing_transfers")
        cursor.execute("DELETE FROM creator_recipients_unified")
        cursor.execute("DELETE FROM network_coordinators")

        conn.commit()
        conn.close()

        return jsonify({
            'status': 'success',
            'deleted': {
                'tokens': token_count,
                'cluster_nodes': cluster_count,
                'clustering_alerts': cursor.rowcount,
                'creator_watch': watch_count,
                'creator_state': state_count,
                'creator_tx_ledger': ledger_count,
                'creator_funders': funders_count,
                'creator_outgoing_transfers': outgoing_count,
                'creator_recipients_unified': unified_count,
                'network_coordinators': coordinator_count,
                'total_items_deleted': token_count + cluster_count + watch_count + state_count + ledger_count + funders_count + outgoing_count + unified_count + coordinator_count
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500



@app.route('/api/funder-extraction-control', methods=['GET', 'POST'])
def api_funder_extraction_control():
    """Get or set funder transfer extraction status"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()

        # Ensure settings table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS polling_settings (
                setting_name TEXT PRIMARY KEY,
                setting_value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        if request.method == 'GET':
            # Get current funder extraction status
            cursor.execute("SELECT setting_value FROM polling_settings WHERE setting_name = 'funder_extraction_enabled'")
            row = cursor.fetchone()
            extraction_enabled = row[0] == '1' if row else False

            conn.close()
            return jsonify({
                'status': 'enabled' if extraction_enabled else 'disabled',
                'extraction_enabled': extraction_enabled
            })

        elif request.method == 'POST':
            data = request.get_json()
            action = data.get('action')  # 'enable', 'disable', 'toggle'

            if action == 'toggle':
                # Get current state
                cursor.execute("SELECT setting_value FROM polling_settings WHERE setting_name = 'funder_extraction_enabled'")
                row = cursor.fetchone()
                current = row[0] == '1' if row else False
                new_value = '0' if current else '1'
            elif action == 'enable':
                new_value = '1'
            elif action == 'disable':
                new_value = '0'
            else:
                conn.close()
                return jsonify({'error': 'Invalid action'}), 400

            # Update setting
            cursor.execute("""
                INSERT OR REPLACE INTO polling_settings (setting_name, setting_value)
                VALUES ('funder_extraction_enabled', ?)
            """, (new_value,))
            conn.commit()
            conn.close()

            extraction_enabled = new_value == '1'
            return jsonify({
                'status': 'success',
                'extraction_enabled': extraction_enabled,
                'action': action
            })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/kill-server', methods=['POST'])
def api_kill_server():
    """Kill the Flask server"""
    try:
        # Return success response first
        response = jsonify({'status': 'server_stopping'})

        # Then shutdown the server
        import os
        import signal
        os.kill(os.getpid(), signal.SIGTERM)

        return response
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/validate-transaction', methods=['POST'])
def api_validate_transaction():
    """Validate a Pump.Fun CREATE transaction"""
    try:
        data = request.json
        sig = data.get('signature', '').strip()

        if not sig:
            return jsonify({'error': 'No signature provided'}), 400

        # Fetch transaction from RPC
        import requests
        from src.metrics.rpc_metrics_recorder import record_request
        rpc_url = "https://api.mainnet-beta.solana.com"

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
        }

        response = requests.post(rpc_url, json=payload, timeout=10)
        result = response.json()

        # Record RPC call
        record_request(
            section="transaction_validation",
            provider="solana",
            method="getTransaction",
            status_code=response.status_code,
            latency_ms=0,  # Already completed
            source_file="main"
        )

        if "result" not in result or not result["result"]:
            return jsonify({'error': 'Transaction not found on-chain'}), 404

        tx = result["result"]

        # Extract details
        message = tx.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])
        instructions = message.get("instructions", [])
        inner_instructions = tx.get("meta", {}).get("innerInstructions", [])

        # Get fee payer (first account)
        fee_payer = None
        if account_keys:
            first_key = account_keys[0]
            fee_payer = first_key.get("pubkey") if isinstance(first_key, dict) else first_key

        # Find mint and other details
        mint = None
        system_create_count = 0
        has_init_mint = False
        has_pump_program = False

        # Check top-level instructions
        for instr in instructions:
            program_id = instr.get("programId")
            parsed = instr.get("parsed", {})
            itype = (parsed.get("type") or "").lower()

            if program_id == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
                has_pump_program = True

        # Check inner instructions
        for group in inner_instructions:
            if isinstance(group, dict) and "instructions" in group:
                for ii in group.get("instructions", []):
                    parsed = ii.get("parsed", {})
                    itype = (parsed.get("type") or "").lower()

                    if itype == "createaccount":
                        system_create_count += 1

                    if itype in ("initializemint", "initializemint2"):
                        has_init_mint = True
                        mint = parsed.get("info", {}).get("mint")

                    if ii.get("programId") == "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P":
                        has_pump_program = True

        # Determine if this is a CREATE
        is_create = system_create_count > 0 and has_init_mint

        if not is_create:
            return jsonify({'error': 'Not a Pump.Fun CREATE transaction (missing System.createAccount or initializeMint)'}), 400

        # Format timestamp
        from datetime import datetime
        block_time = tx.get("blockTime")
        timestamp = datetime.fromtimestamp(block_time).strftime('%Y-%m-%d %H:%M:%S UTC') if block_time else "Unknown"

        return jsonify({
            'signature': sig,
            'mint': mint or 'Unknown',
            'creator': fee_payer or 'Unknown',
            'timestamp': timestamp,
            'confirmed': tx.get("meta", {}).get("err") is None,
            'has_system_create': system_create_count > 0,
            'has_init_mint': has_init_mint,
            'pump_program': has_pump_program,
            'instruction_count': len(instructions),
            'inner_instruction_count': sum(
                len(g.get("instructions", [])) if isinstance(g, dict) else 1
                for g in inner_instructions
            )
        })

    except requests.exceptions.Timeout:
        return jsonify({'error': 'RPC timeout - transaction fetch took too long'}), 503
    except requests.exceptions.RequestException as e:
        return jsonify({'error': f'Network error: {str(e)}'}), 503
    except Exception as e:
        return jsonify({'error': f'Validation error: {str(e)}'}), 500


@app.route('/api/funder-clusters')
def api_funder_clusters():
    """Get all funder clusters from analyzer with cluster_id (FUNDERS_1, FUNDERS_9, etc.)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all clusters with their aggregated stats
        cursor.execute("""
            SELECT
                cluster_id,
                COUNT(*) as funder_count,
                MAX(network_size) as network_size,
                MAX(total_volume_sol) as total_volume_sol,
                MAX(creators_served) as creators_served_json
            FROM funder_networks
            WHERE cluster_id IS NOT NULL
            GROUP BY cluster_id
            ORDER BY funder_count DESC, total_volume_sol DESC
        """)

        # Risk multiplier mapping (v2.2: CEX-exclusive clusters)
        risk_multipliers = {
            'FUNDERS_14': {'multiplier': 3.0, 'label': '🚨 CRITICAL - Coordinated Network (25 non-CEX funders)', 'level': 'CRITICAL', 'name': 'NexusCerberus'},
            'FUNDERS_20': {'multiplier': 2.0, 'label': '⚠️ HIGH - Secondary Network (20 non-CEX funders)', 'level': 'HIGH', 'name': 'CrimsonRaven'},
            'FUNDERS_17': {'multiplier': 1.5, 'label': '🟡 MEDIUM - Tertiary Network (9 non-CEX funders)', 'level': 'MEDIUM', 'name': 'StellarDragon'},
            'FUNDERS_6': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'IvoryWarden'},
            'FUNDERS_10': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'OnyxRaven'},
            'FUNDERS_8': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'SilentViper'},
            'FUNDERS_16': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'PhantomWolf'},
            'FUNDERS_9': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'EtherealEagle'},
            'FUNDERS_1': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'CosmicLion'},
            'FUNDERS_11': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'PhoenixAscend'},
            'FUNDERS_13': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'ShadowNova'},
            'FUNDERS_2': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'VortexMind'},
            'FUNDERS_3': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'IceShield'},
            'FUNDERS_4': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'StormBringer'},
            'FUNDERS_5': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'NightHunter'},
            'FUNDERS_7': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'FrostByte'},
            'FUNDERS_12': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'VortexFlow'},
            'FUNDERS_15': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'IceVenom'},
            'FUNDERS_18': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'ShadowBolt'},
            'FUNDERS_19': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'VortexKing'},
        }

        clusters = []
        total_sol = 0
        for row in cursor.fetchall():
            cluster_id = row['cluster_id']
            volume = float(row['total_volume_sol'] or 0.0)
            total_sol += volume

            # Parse creators_served JSON
            creators_count = 0
            try:
                import json
                creators = json.loads(row['creators_served_json'] or '[]')
                creators_count = len(creators) if isinstance(creators, list) else 0
            except:
                creators_count = 0

            risk_info = risk_multipliers.get(cluster_id, {'multiplier': 1.0, 'label': f'Network {cluster_id}', 'level': 'CLEAN', 'name': cluster_id})

            clusters.append({
                'cluster_id': cluster_id,
                'cluster_name': risk_info.get('name', cluster_id),
                'funder_count': int(row['funder_count'] or 0),
                'network_size': int(row['network_size'] or 0),
                'total_volume_sol': round(volume, 2),
                'creator_count': creators_count,
                'risk_multiplier': risk_info['multiplier'],
                'risk_label': risk_info['label'],
                'risk_level': risk_info['level']
            })

        conn.close()

        return jsonify({
            'clusters': clusters,
            'total_clusters': len(clusters),
            'total_volume_sol': round(total_sol, 2),
            'note': 'Volume is aggregated correctly (MAX per cluster, not SUM per row)'
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/funder-cluster/<cluster_id>')
def api_funder_cluster_details(cluster_id):
    """Get detailed info for a specific funder cluster"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get cluster metadata
        cursor.execute("""
            SELECT
                cluster_id,
                COUNT(*) as funder_count,
                MAX(network_size) as network_size,
                MAX(total_volume_sol) as total_volume_sol,
                MAX(creators_served) as creators_served_json
            FROM funder_networks
            WHERE cluster_id = ?
            GROUP BY cluster_id
        """, (cluster_id,))

        cluster_meta = cursor.fetchone()
        if not cluster_meta:
            return jsonify({'error': f'Cluster {cluster_id} not found'}), 404

        # Get all funders in this cluster
        cursor.execute("""
            SELECT DISTINCT primary_funder as funder_address
            FROM funder_networks
            WHERE cluster_id = ?
            ORDER BY primary_funder
        """, (cluster_id,))

        funders = [dict(row) for row in cursor.fetchall()]

        # Get creators in this cluster
        import json
        creators = []
        try:
            creators_json = cluster_meta['creators_served_json']
            creators = json.loads(creators_json or '[]') if isinstance(creators_json, str) else []
        except:
            creators = []

        # Risk info (v2.2: CEX-exclusive clusters)
        risk_multipliers = {
            'FUNDERS_14': {'multiplier': 3.0, 'label': '🚨 CRITICAL - Coordinated Network (25 non-CEX funders)', 'level': 'CRITICAL', 'name': 'NexusCerberus'},
            'FUNDERS_20': {'multiplier': 2.0, 'label': '⚠️ HIGH - Secondary Network (20 non-CEX funders)', 'level': 'HIGH', 'name': 'CrimsonRaven'},
            'FUNDERS_17': {'multiplier': 1.5, 'label': '🟡 MEDIUM - Tertiary Network (9 non-CEX funders)', 'level': 'MEDIUM', 'name': 'StellarDragon'},
            'FUNDERS_6': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'IvoryWarden'},
            'FUNDERS_10': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'OnyxRaven'},
            'FUNDERS_8': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'SilentViper'},
            'FUNDERS_16': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'PhantomWolf'},
            'FUNDERS_9': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'EtherealEagle'},
            'FUNDERS_1': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'CosmicLion'},
            'FUNDERS_11': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'PhoenixAscend'},
            'FUNDERS_13': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'ShadowNova'},
            'FUNDERS_2': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'VortexMind'},
            'FUNDERS_3': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'IceShield'},
            'FUNDERS_4': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'StormBringer'},
            'FUNDERS_5': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'NightHunter'},
            'FUNDERS_7': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'FrostByte'},
            'FUNDERS_12': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'VortexFlow'},
            'FUNDERS_15': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'IceVenom'},
            'FUNDERS_18': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'ShadowBolt'},
            'FUNDERS_19': {'multiplier': 1.0, 'label': '✅ CLEAN', 'level': 'CLEAN', 'name': 'VortexKing'},
        }
        risk_info = risk_multipliers.get(cluster_id, {'multiplier': 1.0, 'label': f'Network {cluster_id}', 'level': 'CLEAN', 'name': cluster_id})

        conn.close()

        return jsonify({
            'cluster_id': cluster_id,
            'cluster_name': risk_info.get('name', cluster_id),
            'funder_count': int(cluster_meta['funder_count'] or 0),
            'network_size': int(cluster_meta['network_size'] or 0),
            'total_volume_sol': float(cluster_meta['total_volume_sol'] or 0.0),
            'creator_count': len(creators),
            'creators': creators,
            'funders': funders,
            'risk_multiplier': risk_info['multiplier'],
            'risk_label': risk_info['label'],
            'risk_level': risk_info['level']
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator/<creator_address>/cluster-risk')
def api_creator_cluster_risk(creator_address):
    """Get cluster assignment and risk multiplier for a creator"""
    try:
        from cluster_risk_checker import check_creator
        
        result = check_creator(creator_address)
        
        return jsonify({
            'creator_address': creator_address,
            'in_cluster': result['in_cluster'],
            'cluster_id': result.get('cluster_id'),
            'risk_multiplier': result.get('risk_multiplier', 1.0),
            'risk_label': result.get('risk_label', '✅ No cluster detected'),
            'risk_level': 'CRITICAL' if result.get('risk_multiplier', 1.0) >= 3.0 else (
                'HIGH' if result.get('risk_multiplier', 1.0) >= 2.0 else (
                'MEDIUM' if result.get('risk_multiplier', 1.0) >= 1.5 else 'CLEAN'
            )),
            'network_size': result.get('network_size', 0),
            'network_volume_sol': result.get('network_volume_sol', 0.0)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/network-tokens/<network_name>')
def api_network_tokens(network_name):
    """Get all tokens funded by a specific network"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Try to get from networks_release table (new format)
        cursor.execute("""
            SELECT * FROM networks_release
            WHERE network_name = ?
        """, (network_name,))

        network_data = cursor.fetchone()
        
        if network_data:
            # Network found in networks_release
            network_dict = dict(network_data)
            
            # Try to get creator memberships (may be empty for test networks)
            cursor.execute("""
                SELECT DISTINCT creator_address FROM network_membership
                WHERE network_name = ?
            """, (network_name,))
            
            creators = [row['creator_address'] for row in cursor.fetchall()]
            tokens = []
            creators_with_tokens = 0
            
            if creators:
                # Get tokens for these creators
                placeholders = ','.join(['?' for _ in creators])
                cursor.execute(f"""
                    SELECT
                        ta.mint,
                        ta.earliest_tx_creator as creator,
                        ta.risk_level,
                        ta.market_cap_current as market_cap,
                        ta.rug_probability
                    FROM token_analysis ta
                    WHERE ta.earliest_tx_creator IN ({placeholders})
                    ORDER BY ta.market_cap_current DESC
                    LIMIT 100
                """, creators)
                
                tokens = [dict(row) for row in cursor.fetchall()]
                
                # Add CEX/INFRA labels for each token's creator
                for token in tokens:
                    token['creator_label'] = get_cex_infra_label(token['creator'])
                
                creators_with_tokens = len(set(token['creator'] for token in tokens))
            
            creator_count = len(creators)
            
            conn.close()

            return jsonify({
                'network_name': network_name,
                'network_size': network_dict.get('network_size', 0),
                'network_type': network_dict.get('network_type', 'unknown'),
                'tokens': tokens,
                'creators_count': creator_count,
                'creators_with_tokens': creators_with_tokens,
                'token_count': len(tokens)
            })
        
        # Fall back to atomic_network_names (old format)
        cursor.execute("""
            SELECT funder_address FROM atomic_network_names
            WHERE network_name = ?
        """, (network_name,))

        result = cursor.fetchone()
        if not result:
            conn.close()
            return jsonify({'error': f'Network "{network_name}" not found'}), 404

        funder_address = result['funder_address']

        # Get tokens for this funder (through creator_funders relationship)
        cursor.execute("""
            SELECT
                ta.mint,
                ta.earliest_tx_creator as creator,
                ta.risk_level,
                ta.market_cap_current as market_cap,
                ta.rug_probability
            FROM token_analysis ta
            WHERE ta.earliest_tx_creator IN (
                SELECT DISTINCT creator_address FROM creator_funders
                WHERE funder_address = ?
            )
            ORDER BY ta.market_cap_current DESC
            LIMIT 100
        """, (funder_address,))

        tokens = [dict(row) for row in cursor.fetchall()]

        # Add CEX/INFRA labels for each token's creator
        for token in tokens:
            token['creator_label'] = get_cex_infra_label(token['creator'])

        # Get creator counts
        cursor.execute("""
            SELECT COUNT(DISTINCT creator_address) as creator_count
            FROM creator_funders
            WHERE funder_address = ?
        """, (funder_address,))

        creator_count = cursor.fetchone()['creator_count']
        creators_with_tokens = len(set(token['creator'] for token in tokens))

        conn.close()

        return jsonify({
            'network_name': network_name,
            'tokens': tokens,
            'creators_count': creator_count,
            'creators_with_tokens': creators_with_tokens,
            'token_count': len(tokens)
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/networks')
def networks_dashboard():
    """Serve a full webview for atomic funder networks"""

    def new_path():
        """NEW PATH: Use precomputed networks_release"""
        all_networks = get_networks_release_list(include_evidence=False)

        # Batch fetch all network scores (using JOIN for better scalability than IN clause)
        scores_map = {}
        try:
            conn, cursor = get_db_conn()
            # Build a temporary table from network_names for efficient JOIN
            # (Avoids long IN clause with hundreds/thousands of names)
            cursor.execute('DROP TABLE IF EXISTS temp_networks')
            cursor.execute('CREATE TEMP TABLE temp_networks (network_name TEXT PRIMARY KEY)')

            # Insert all network names
            for network in all_networks:
                cursor.execute('INSERT INTO temp_networks (network_name) VALUES (?)',
                             (network['network_name'],))

            # Join with network_scores to fetch scores efficiently
            cursor.execute('''
                SELECT tn.network_name, ns.score
                FROM temp_networks tn
                LEFT JOIN network_scores ns USING (network_name)
            ''')

            for row in cursor.fetchall():
                if row['score'] is not None:  # Only add if score exists
                    scores_map[row['network_name']] = {
                        'score': row['score'],
                        'components': {}
                    }

            cursor.execute('DROP TABLE IF EXISTS temp_networks')
            conn.close()
        except Exception as e:
            print(f"[DEBUG] Error fetching network scores: {e}")

        networks = []
        total_tokens = 0
        total_creators_funded = 0
        total_sol = 0.0

        for network in all_networks:
            network_name = network['network_name']
            network_size = network['network_size']

            # Get token count from API (using the api_network_tokens endpoint logic)
            token_count = 0
            creators_funded = 0
            try:
                conn, cursor = get_db_conn()
                # Get creators from network_membership
                cursor.execute("""
                    SELECT DISTINCT creator_address FROM network_membership
                    WHERE network_name = ?
                """, (network_name,))
                creators = [row['creator_address'] for row in cursor.fetchall()]
                creators_funded = len(creators)

                # Get tokens for these creators
                if creators:
                    placeholders = ','.join(['?' for _ in creators])
                    cursor.execute(f"""
                        SELECT COUNT(DISTINCT mint) as token_count
                        FROM token_analysis
                        WHERE earliest_tx_creator IN ({placeholders})
                    """, creators)
                    result = cursor.fetchone()
                    token_count = result['token_count'] if result else 0

                conn.close()
            except Exception as e:
                print(f"[DEBUG] Error fetching token count for {network_name}: {e}")

            sol_amount = 0.0  # Not available in networks_release
            funder_is_cex = network['has_cex_funder']

            total_tokens += token_count
            total_creators_funded += creators_funded
            total_sol += sol_amount

            # Get CEX/INFRA label if this is a CEX funder
            cex_label = None
            if funder_is_cex:
                # Try to get a representative funder from the network
                conn, cursor = get_db_conn()
                cursor.execute("""
                    SELECT DISTINCT creator_address FROM network_membership
                    WHERE network_name = ? LIMIT 1
                """, (network_name,))
                member_row = cursor.fetchone()
                if member_row:
                    cex_label = get_cex_infra_label(member_row['creator_address'])
                conn.close()

            # Get score information
            score_info = scores_map.get(network_name)

            networks.append({
                'name': network_name,
                'tier': network.get('network_type', 'N/A'),
                'is_cex': funder_is_cex,
                'cex_label': cex_label,
                'network_size': network_size,
                'token_count': token_count,
                'creators_funded': creators_funded,
                'sol_amount': sol_amount,
                'score': score_info['score'] if score_info else None,
                'score_badge': 'high' if (score_info and score_info['score'] >= 70) else ('medium' if (score_info and score_info['score'] >= 30) else 'low') if score_info else None
            })

        return {
            'networks': networks,
            'total_tokens': total_tokens,
            'total_creators_funded': total_creators_funded,
            'total_sol': total_sol,
            'total_networks': len(networks)
        }, 200

    def legacy_path():

        """OLD PATH: Use legacy atomic_network_names"""
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all atomic networks with token stats
        cursor.execute("""
            SELECT
                ann.funder_address,
                ann.network_name,
                ann.network_tier,
                ann.is_cex,
                (CASE WHEN ann.funder_address IN (SELECT cex_address FROM cex_wallets WHERE is_active = 1) THEN 1 ELSE 0 END) as funder_is_cex,
                COUNT(DISTINCT ta.mint) as token_count,
                COUNT(DISTINCT cf.creator_address) as creators_funded,
                ROUND(SUM(cf.amount_sol), 2) as total_sol
            FROM atomic_network_names ann
            LEFT JOIN creator_funders cf ON ann.funder_address = cf.funder_address
            LEFT JOIN token_analysis ta ON cf.creator_address = ta.earliest_tx_creator
            GROUP BY ann.funder_address, ann.network_name, ann.network_tier, ann.is_cex
            HAVING creators_funded >= 2
            ORDER BY
                CASE
                    WHEN ann.network_tier = 'Apex' THEN 1
                    WHEN ann.network_tier = 'Nexus' THEN 2
                    WHEN ann.network_tier = 'Relay' THEN 3
                    WHEN ann.network_tier = 'Node' THEN 4
                    WHEN ann.network_tier = 'Exchange' THEN 5
                END,
                token_count DESC
        """)

        networks = []
        total_tokens = 0
        total_creators = 0
        total_creators_funded = 0
        total_sol = 0.0
        total_networks = 0
        all_rows = list(cursor.fetchall())

        # Batch fetch scores for all networks (using JOIN for better scalability)
        scores_map = {}
        try:
            cursor.execute('DROP TABLE IF EXISTS temp_networks_legacy')
            cursor.execute('CREATE TEMP TABLE temp_networks_legacy (network_name TEXT PRIMARY KEY)')

            # Insert all network names from rows
            for row in all_rows:
                cursor.execute('INSERT INTO temp_networks_legacy (network_name) VALUES (?)',
                             (row['network_name'],))

            # Join with network_scores to fetch scores efficiently
            cursor.execute('''
                SELECT tnl.network_name, ns.score
                FROM temp_networks_legacy tnl
                LEFT JOIN network_scores ns USING (network_name)
            ''')

            for score_row in cursor.fetchall():
                if score_row['score'] is not None:  # Only add if score exists
                    scores_map[score_row['network_name']] = {
                        'score': score_row['score'],
                        'components': {}
                    }

            cursor.execute('DROP TABLE IF EXISTS temp_networks_legacy')
        except Exception as e:
            print(f"[DEBUG] Error fetching network scores in legacy path: {e}")

        for row in all_rows:
            network_name = row['network_name']
            tier = row['network_tier']
            funder_address = row['funder_address']
            token_count = int(row['token_count'] or 0)
            creators_funded = int(row['creators_funded'] or 0)
            sol_amount = float(row['total_sol'] or 0.0)
            funder_is_cex = bool(row['funder_is_cex'])

            total_tokens += token_count
            total_creators_funded += creators_funded
            total_sol += sol_amount
            total_networks += 1

            # Get CEX/INFRA label if this is a CEX/INFRA funder
            cex_label = get_cex_infra_label(funder_address) if funder_is_cex else None

            # Get score information
            score_info = scores_map.get(network_name)

            networks.append({
                'name': network_name,
                'tier': tier,
                'is_cex': funder_is_cex,
                'cex_label': cex_label,
                'token_count': token_count,
                'creators_funded': creators_funded,
                'sol_amount': sol_amount,
                'score': score_info['score'] if score_info else None,
                'score_badge': 'high' if (score_info and score_info['score'] >= 70) else ('medium' if (score_info and score_info['score'] >= 30) else 'low') if score_info else None
            })

        conn.close()

        return {
            'networks': networks,
            'total_tokens': total_tokens,
            'total_creators_funded': total_creators_funded,
            'total_sol': total_sol,
            'total_networks': total_networks
        }, 200

    # Call the router to get the context
    response, status_code = route_phase2c('/networks', new_path, legacy_path)

    # Extract context from response
    if status_code == 200:
        if isinstance(response, str):
            return response, status_code
        # If it came from jsonify, extract the data
        import json as json_module
        context = json.loads(response.get_data(as_text=True))
    else:
        return response, status_code

    networks = context['networks']
    total_tokens = context['total_tokens']
    total_creators_funded = context['total_creators_funded']
    total_sol = context['total_sol']
    total_networks = context['total_networks']

    return render_template(
        "networks_dashboard.html",
        networks=networks,
        total_networks=total_networks,
        total_creators_funded=total_creators_funded,
        total_tokens=total_tokens,
        total_sol=total_sol,
        active_page="networks"
    )


@app.route('/top-funding-hubs')
def top_funding_hubs():
    """Display dashboard of all top funding hubs (duplicate senders)"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Get top 20 senders by number of funders they send to
        c.execute("""
            SELECT
                sender_address,
                COUNT(DISTINCT funder_address) as funder_count,
                SUM(amount_sol) as total_sol_sent
            FROM funder_incoming_transfers
            GROUP BY sender_address
            ORDER BY funder_count DESC
            LIMIT 20
        """)
        hubs = []
        for row in c.fetchall():
            sender_addr, funder_count, total_sol = row

            # Count creators funded by those funders
            c.execute("""
                SELECT COUNT(DISTINCT creator_address) FROM creator_funders
                WHERE funder_address IN (
                    SELECT DISTINCT funder_address FROM funder_incoming_transfers
                    WHERE sender_address = ?
                )
            """, (sender_addr,))
            creator_count = c.fetchone()[0]

            # Count tokens created by those creators
            c.execute("""
                SELECT COUNT(*) FROM token_analysis
                WHERE earliest_tx_creator IN (
                    SELECT DISTINCT creator_address FROM creator_funders
                    WHERE funder_address IN (
                        SELECT DISTINCT funder_address FROM funder_incoming_transfers
                        WHERE sender_address = ?
                    )
                )
            """, (sender_addr,))
            token_count = c.fetchone()[0]

            # Check if this sender is also a creator
            c.execute("""
                SELECT COUNT(*) FROM token_analysis
                WHERE earliest_tx_creator = ?
            """, (sender_addr,))
            created_tokens = c.fetchone()[0]

            # Check for self-funding (funders that only fund the sender back)
            c.execute("""
                SELECT COUNT(DISTINCT funder_address)
                FROM funder_incoming_transfers
                WHERE sender_address = ?
                AND funder_address IN (
                    SELECT funder_address FROM creator_funders
                    WHERE creator_address = ?
                )
            """, (sender_addr, sender_addr))
            self_funding_count = c.fetchone()[0]

            # If all creators are the sender itself, mark as self-funding
            is_likely_self_funded = creator_count == 1 and created_tokens > 0

            hubs.append({
                'address': sender_addr,
                'funder_count': funder_count,
                'creator_count': creator_count,
                'total_sol_sent': total_sol or 0,
                'token_count': token_count,
                'created_tokens': created_tokens,
                'self_funding_count': self_funding_count,
                'is_likely_self_funded': is_likely_self_funded
            })

        conn.close()

        return render_template(
            'top_funding_hubs.html',
            active_page='hubs',
            hubs=list(enumerate(hubs, 1)),
        )

    except Exception as e:
        return f"<html><body style='background:#0a0a0e; color: red;'><h1>Error</h1><p>{str(e)}</p></body></html>", 500


@app.route('/funding-hub/<hub_address>')
def funding_hub(hub_address):
    """Display funding hub network: sender -> funders -> creators -> tokens, OR funder -> creators -> tokens"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Determine if this address is a sender or a funder
        c.execute("SELECT COUNT(*) FROM token_analysis WHERE earliest_tx_creator = ?", (hub_address,))
        sender_token_count = c.fetchone()[0]

        # Check if address is a funder (funds multiple creators)
        c.execute("SELECT COUNT(DISTINCT creator_address) FROM creator_funders WHERE funder_address = ?", (hub_address,))
        funder_creator_count = c.fetchone()[0]

        # Check if address is a sender (has funders in funder_incoming_transfers)
        c.execute("""
            SELECT COUNT(DISTINCT funder_address)
            FROM funder_incoming_transfers
            WHERE sender_address = ?
        """, (hub_address,))
        sender_funder_count = c.fetchone()[0]

        funder_data = []
        self_funding_funders = 0
        third_party_funded_creators = set()
        hub_type = None  # 'sender' or 'funder'

        # CASE 1: Address is a sender (original case)
        if sender_funder_count > 0:
            hub_type = 'sender'
            # Get all funders that received from this sender
            c.execute("""
                SELECT DISTINCT funder_address
                FROM funder_incoming_transfers
                WHERE sender_address = ?
            """, (hub_address,))
            receiving_funders = [row[0] for row in c.fetchall()]

            for funder in receiving_funders:
                c.execute("""
                    SELECT DISTINCT creator_address
                    FROM creator_funders
                    WHERE funder_address = ?
                """, (funder,))
                funded_creators = [row[0] for row in c.fetchall()]

                # Check if this funder only funds the sender (self-funding)
                is_self_funding = len(funded_creators) == 1 and funded_creators[0] == hub_address
                if is_self_funding:
                    self_funding_funders += 1
                    continue

                # Count tokens for those creators
                creator_list = ','.join(['?' for _ in funded_creators])
                if funded_creators:
                    c.execute(f"""
                        SELECT COUNT(*) FROM token_analysis
                        WHERE earliest_tx_creator IN ({creator_list})
                    """, funded_creators)
                    token_count = c.fetchone()[0]
                    for creator in funded_creators:
                        third_party_funded_creators.add(creator)
                else:
                    token_count = 0

                # Check if this funder is a multi-creator funder (coordinated funder)
                c.execute("""
                    SELECT funder_address FROM coordinated_funders
                    WHERE funder_address = ?
                """, (funder,))
                coordinated_result = c.fetchone()
                is_multi_creator_funder = coordinated_result is not None

                funder_data.append({
                    'address': funder,
                    'creator_count': len(funded_creators),
                    'token_count': token_count,
                    'sample_creators': funded_creators[:5],
                    'is_multi_creator_funder': is_multi_creator_funder
                })

        # CASE 2: Address is a funder (funds creators directly)
        elif funder_creator_count > 0:
            hub_type = 'funder'
            # Get all creators funded by this address with their token counts
            c.execute("""
                SELECT DISTINCT creator_address
                FROM creator_funders
                WHERE funder_address = ?
            """, (hub_address,))
            funded_creators = [row[0] for row in c.fetchall()]

            # For each creator, get token count
            for creator in funded_creators:
                c.execute("""
                    SELECT COUNT(*) FROM token_analysis
                    WHERE earliest_tx_creator = ?
                """, (creator,))
                creator_token_count = c.fetchone()[0]

                third_party_funded_creators.add(creator)

                funder_data.append({
                    'address': creator,
                    'creator_count': 1,  # This is a creator, not a funder
                    'token_count': creator_token_count,
                    'sample_creators': [creator],
                    'is_creator': True
                })

        conn.close()

        # Sort by token count descending
        funder_data.sort(key=lambda x: x['token_count'], reverse=True)

        html = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Funding Hub: """ + hub_address + """</title>
            <style>
                :root {
                    --color-purple: #a78bfa;
                    --color-cyan: #06b6d4;
                    --color-green: #16a34a;
                    --color-yellow: #fbbf24;
                    --text-primary: #e5e7eb;
                    --text-secondary: #9ca3af;
                    --bg-dark: linear-gradient(135deg, #0a0a0e 0%, #0d0d15 100%);
                    --bg-card: rgba(30, 30, 40, 0.8);
                    --bg-hover: rgba(167, 139, 250, 0.05);
                    --border-color: rgba(167, 139, 250, 0.3);
                    --blue: #3b82f6;
                }

                * {
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }

                body {
                    background: var(--bg-dark);
                    color: var(--text-primary);
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
                    padding: 30px;
                }

                .container {
                    max-width: 1200px;
                    margin: 0 auto;
                }

                h1 {
                    color: var(--color-purple);
                    margin-bottom: 8px;
                    font-size: 28px;
                }

                h2 {
                    color: var(--color-purple);
                }

                .hub-address {
                    font-family: monospace;
                    font-size: 12px;
                    color: var(--text-secondary);
                    margin-bottom: 24px;
                    word-break: break-all;
                }

                .stats {
                    display: grid;
                    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
                    gap: 16px;
                    margin-bottom: 32px;
                }

                .stat-box {
                    background: var(--bg-card);
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    padding: 16px;
                }

                .stat-box.hub {
                    border-left: 3px solid rgba(167, 139, 250, 0.5);
                }

                .stat-label {
                    font-size: 11px;
                    color: var(--text-secondary);
                    text-transform: uppercase;
                    margin-bottom: 8px;
                }

                .stat-value {
                    font-size: 24px;
                    font-weight: bold;
                    color: var(--color-purple);
                }

                table {
                    width: 100%;
                    border-collapse: collapse;
                    background: var(--bg-card);
                    border: 1px solid var(--border-color);
                    border-radius: 8px;
                    overflow: hidden;
                }

                thead {
                    background: var(--bg-card);
                    border-bottom: 2px solid var(--border-color);
                    border-left: 3px solid rgba(167, 139, 250, 0.5);
                }

                th {
                    padding: 15px;
                    text-align: left;
                    color: var(--color-purple);
                    font-weight: 600;
                    font-size: 13px;
                    text-transform: uppercase;
                }

                td {
                    padding: 15px;
                    border-bottom: 1px solid rgba(167, 139, 250, 0.2);
                    font-size: 13px;
                }

                tr:last-child td {
                    border-bottom: none;
                }

                tbody tr:hover {
                    background: var(--bg-hover);
                }

                .address {
                    font-family: monospace;
                    font-size: 11px;
                    color: var(--blue);
                    word-break: break-all;
                    cursor: pointer;
                }

                .address:hover {
                    color: var(--color-purple);
                    text-decoration: underline;
                }

                .stat-number {
                    font-weight: bold;
                    color: var(--color-green);
                }

                .back-link {
                    display: inline-block;
                    margin-bottom: 20px;
                    color: var(--color-cyan);
                    text-decoration: none;
                    font-size: 13px;
                }

                .back-link:hover {
                    color: var(--color-purple);
                    text-decoration: underline;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <a href="/top-funding-hubs" class="back-link">← Back to Hubs</a>

                <h1>""" + ("🔗 Multi-Creator Funder" if hub_type == 'funder' else "💰 Funding Distribution Sender") + """</h1>
                <div class="hub-address">""" + hub_address + """</div>

                <div class="stats">
        """

        if hub_type == 'sender':
            html += f"""
                    <div class="stat-box hub">
                        <div class="stat-label">Tokens Created by Sender</div>
                        <div class="stat-value">{sender_token_count}</div>
                    </div>
                    <div class="stat-box hub" style="border-left: 3px solid rgba(239, 68, 68, 0.5); background: rgba(239, 68, 68, 0.08);">
                        <div class="stat-label">⚠️ Self-Funding Intermediates</div>
                        <div class="stat-value" style="color: #ef4444;">{self_funding_funders}</div>
                        <div class="stat-label" style="margin-top: 8px; font-size: 11px; color: #ef4444;">Fund sender only</div>
                    </div>
                    <div class="stat-box hub">
                        <div class="stat-label">Third-Party Funded Creators</div>
                        <div class="stat-value">{len(third_party_funded_creators)}</div>
                    </div>
                    <div class="stat-box hub">
                        <div class="stat-label">Tokens from Third-Party Creators</div>
                        <div class="stat-value">{sum(f['token_count'] for f in funder_data)}</div>
                    </div>
                    <div class="stat-box hub" style="border-left: 3px solid rgba(251, 146, 60, 0.5); background: rgba(251, 146, 60, 0.08);">
                        <div class="stat-label">🔗 Multi-Creator Funders</div>
                        <div class="stat-value" style="color: #fb923c;">{sum(1 for f in funder_data if f.get('is_multi_creator_funder'))}</div>
                        <div class="stat-label" style="margin-top: 8px; font-size: 11px; color: #fb923c;">Fund multiple creators</div>
                    </div>
            """
        else:  # funder
            html += f"""
                    <div class="stat-box hub">
                        <div class="stat-label">Creators Funded</div>
                        <div class="stat-value">{funder_creator_count}</div>
                    </div>
                    <div class="stat-box hub">
                        <div class="stat-label">Tokens Launched</div>
                        <div class="stat-value">{sum(f['token_count'] for f in funder_data)}</div>
                    </div>
                    <div class="stat-box hub">
                        <div class="stat-label">Multi-Creator Funder</div>
                        <div class="stat-value" style="color: var(--color-yellow);">⚠️ YES</div>
                    </div>
            """

        html += """
                </div>

                <h2 style="margin-bottom: 16px; font-size: 18px;">""" + ("Creators Funded" if hub_type == 'funder' else "Third-Party Funders (Fund Other Creators)") + """</h2>
                <p style="color: #94a3b8; margin-bottom: 16px; font-size: 13px;">""" + ("Creators funded by this address and their tokens" if hub_type == 'funder' else "Showing funders that fund creators OTHER than this sender. Self-funding intermediaries are excluded.") + """</p>
                <table>
                    <thead>
                        <tr>
                            <th>""" + ("Creator Address" if hub_type == 'funder' else "Funder Address") + """</th>
                            <th style="text-align: center;">""" + ("Tokens Launched" if hub_type == 'funder' else "Creators Funded") + """</th>
                            <th style="text-align: center;">""" + ("Funding Source" if hub_type == 'funder' else "Tokens Launched") + """</th>
                            <th>""" + ("Network Info" if hub_type == 'funder' else "Sample Creators") + """</th>
                        </tr>
                    </thead>
                    <tbody>
        """

        if len(funder_data) == 0:
            # Empty state message
            if hub_type == 'sender':
                html += """
                        <tr>
                            <td colspan="4" style="text-align: center; padding: 40px; color: #94a3b8;">
                                <p style="margin: 0; font-size: 14px;">ℹ️ No third-party funders found</p>
                                <p style="margin: 8px 0 0 0; font-size: 12px; color: #64748b;">All funders are self-funding intermediaries (fund sender only)</p>
                            </td>
                        </tr>
                """
            else:
                html += """
                        <tr>
                            <td colspan="4" style="text-align: center; padding: 40px; color: #94a3b8;">
                                <p style="margin: 0; font-size: 14px;">ℹ️ No creators funded</p>
                            </td>
                        </tr>
                """
        else:
            for funder in funder_data:
                sample_creators = ', '.join([addr[:8] + '...' for addr in funder['sample_creators']])
                if hub_type == 'funder':
                    # For funder type, show the creator entry directly
                    html += f"""
                            <tr>
                                <td><span class="address">{funder['address']}</span></td>
                                <td style="text-align: center;"><span class="stat-number">{funder['token_count']}</span></td>
                                <td style="text-align: center;"><span style="color: var(--color-yellow); font-weight: 600;">Multi-Creator</span></td>
                                <td><small style="color: #94a3b8;">Funded by {hub_address[:8]}...</small></td>
                            </tr>
                    """
                else:
                    # For sender type, show funder entries with multi-funder indicator
                    multi_funder_badge = ""
                    if funder.get('is_multi_creator_funder'):
                        multi_funder_badge = ' <span style="background: rgba(251, 146, 60, 0.3); color: #fb923c; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; margin-left: 4px;">🔗 Multi-Funder</span>'

                    html += f"""
                            <tr>
                                <td><span class="address">{funder['address']}</span>{multi_funder_badge}</td>
                                <td style="text-align: center;"><span class="stat-number">{funder['creator_count']}</span></td>
                                <td style="text-align: center;"><span class="stat-number">{funder['token_count']}</span></td>
                                <td><small style="color: #94a3b8;">{sample_creators if sample_creators else 'N/A'}</small></td>
                            </tr>
                    """

        html += """
                    </tbody>
                </table>
            </div>
        </body>
        </html>
        """

        return html

    except Exception as e:
        return f"<html><body style='background: var(--bg-dark); color: red;'><h1>Error</h1><p>{str(e)}</p></body></html>", 500


@app.route('/api/creator-analysis-queue-status')
def api_creator_analysis_queue_status():
    """Get status of creator analysis queue"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get queue status breakdown (exclude completed)
        cursor.execute("""
            SELECT
                status,
                COUNT(*) as count,
                ROUND(AVG(priority), 1) as avg_priority
            FROM creator_analysis_queue
            WHERE status != 'complete'
            GROUP BY status
        """)

        status_rows = cursor.fetchall()
        status_breakdown = {}
        for row in status_rows:
            status_breakdown[row['status']] = {
                'count': row['count'],
                'avg_priority': row['avg_priority']
            }

        # Get top priority items (exclude completed, show analyzing first, then pending)
        cursor.execute("""
            SELECT creator_address, priority, status,
                   json_extract(findings_cached, '$.risk_level') as risk_level,
                   last_analyzed_at
            FROM creator_analysis_queue
            WHERE status IN ('pending', 'analyzing', 'retry')
            ORDER BY CASE status
                WHEN 'analyzing' THEN 0
                WHEN 'pending' THEN 1
                WHEN 'retry' THEN 2
            END, priority DESC
            LIMIT 5
        """)

        top_items = []
        for row in cursor.fetchall():
            top_items.append({
                'creator_address': row['creator_address'],
                'priority': row['priority'],
                'status': row['status'],
                'risk_level': row['risk_level'],
                'last_analyzed_at': row['last_analyzed_at']
            })

        # Get active queue size (pending + analyzing + retry only)
        cursor.execute("""
            SELECT COUNT(*) as count FROM creator_analysis_queue
            WHERE status IN ('pending', 'analyzing', 'retry')
        """)
        total_queued = cursor.fetchone()['count'] or 0

        conn.close()

        return jsonify({
            'ok': True,
            'total_queued': total_queued,
            'status_breakdown': status_breakdown,
            'top_priority': top_items
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/creator-analysis')
def creator_analysis_page():
    """Display creator scan history, findings, and network impacts"""
    return render_template("creator_analysis.html", active_page="creator-analysis")


@app.route('/api/creator-outgoing-analysis/<creator_address>')
def api_creator_outgoing_analysis(creator_address: str):
    """Get comprehensive creator outgoing transfer analysis from webhook data"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get last webhook timestamp for this creator (most recent activity)
        cursor.execute("""
            SELECT MAX(block_time) as last_activity
            FROM sol_transfers
            WHERE source = ?
        """, (creator_address,))
        last_activity_row = cursor.fetchone()
        last_scanned = None
        if last_activity_row and last_activity_row['last_activity']:
            from datetime import datetime
            last_scanned = datetime.utcfromtimestamp(last_activity_row['last_activity']).isoformat()

        # Get creator outgoing transfers from WEBHOOK DATA (sol_transfers)
        cursor.execute("""
            SELECT 
                COUNT(*) as count, 
                SUM(amount_sol) as total_sol, 
                COUNT(DISTINCT destination) as unique_recipients, 
                MAX(block_time) as last_transaction_time
            FROM sol_transfers
            WHERE source = ?
        """, (creator_address,))
        row = cursor.fetchone()
        transfers = {
            'count': row['count'] if row else 0,
            'total_sol': row['total_sol'] if row else 0,
            'unique_recipients': row['unique_recipients'] if row else 0,
            'last_transaction_time': row['last_transaction_time'] if row else None
        }

        # Get funding chains where this creator is the source
        cursor.execute("""
            SELECT
                source_creator, bridge_funder, target_creator,
                source_to_bridge_amount_sol, bridge_to_target_amount_sol,
                source_block_time, confidence, chain_id
            FROM funding_chains
            WHERE source_creator = ? AND chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'
            ORDER BY created_at DESC
        """, (creator_address,))
        funding_chains = cursor.fetchall()

        # Get coordinated edges where this creator is involved
        cursor.execute("""
            SELECT creator_a, creator_b, bridge_funder, confidence
            FROM coordinated_creator_edges
            WHERE creator_a = ? OR creator_b = ?
            ORDER BY created_at DESC
        """, (creator_address, creator_address))
        coordinated_edges = cursor.fetchall()

        # Get ALL network memberships for this creator (both primary and as connected creator)
        all_networks = []

        # 1. Direct membership (creator_address is the primary creator)
        cursor.execute("""
            SELECT network_name FROM creator_networks
            WHERE creator_address = ?
        """, (creator_address,))
        for row in cursor.fetchall():
            if row['network_name']:
                all_networks.append(row['network_name'])

        # 2. Membership as connected creator (creator_address in connected_creators JSON)
        cursor.execute("""
            SELECT network_name, connected_creators FROM creator_networks
            WHERE connected_creators LIKE ?
        """, (f'%{creator_address}%',))
        for row in cursor.fetchall():
            try:
                connected = json.loads(row['connected_creators'])
                if creator_address in connected and row['network_name'] and row['network_name'] not in all_networks:
                    all_networks.append(row['network_name'])
            except:
                pass

        # 3. Membership via funders (creator's funders are primary creators in a network)
        if not all_networks:
            cursor.execute("""
                SELECT DISTINCT cn.network_name
                FROM creator_networks cn
                WHERE cn.creator_address IN (
                    SELECT DISTINCT funder_address FROM creator_funders
                    WHERE creator_address = ?
                )
            """, (creator_address,))
            for row in cursor.fetchall():
                if row['network_name'] and row['network_name'] not in all_networks:
                    all_networks.append(row['network_name'])

        # 4. Membership in creator-to-creator networks (organic networks for direct transfers)
        cursor.execute("""
            SELECT DISTINCT network_name FROM creator_to_creator_networks
            WHERE creator_address = ?
        """, (creator_address,))
        for row in cursor.fetchall():
            if row['network_name'] and row['network_name'] not in all_networks:
                all_networks.append(row['network_name'])

        # Get CEX/INFRA types for all networks
        networks_with_types = []
        for net_name in all_networks:
            cursor.execute("""
                SELECT network_type
                FROM network_cex_infra_flags
                WHERE network_name = ?
            """, (net_name,))
            net_type_row = cursor.fetchone()
            net_type = net_type_row['network_type'] if net_type_row else None
            networks_with_types.append({
                'name': net_name,
                'type': net_type
            })

        # For backward compatibility, keep single network_name and network_type for primary network
        # Prioritize by risk level: CEX > MIXED > INFRA > ORGANIC
        priority_order = {'cex_connected': 0, 'mixed': 1, 'infra_connected': 2, 'organic': 3}
        sorted_networks = sorted(networks_with_types, key=lambda x: priority_order.get(x['type'], 999))

        network_name = sorted_networks[0]['name'] if sorted_networks else None
        network_type = sorted_networks[0]['type'] if sorted_networks else None

        # Get self-funding data from stored calculations
        cursor.execute("""
            SELECT self_funding_percentage, self_funding_intermediates, total_funders, is_self_funding
            FROM creator_self_funding
            WHERE creator_address = ?
        """, (creator_address,))
        self_funding_row = cursor.fetchone()
        self_funding_percentage = self_funding_row['self_funding_percentage'] if self_funding_row else 0
        self_funding_intermediates = self_funding_row['self_funding_intermediates'] if self_funding_row else 0
        total_funders = self_funding_row['total_funders'] if self_funding_row else 0
        is_self_funding = self_funding_row['is_self_funding'] if self_funding_row else 0

        # Get incoming funders (who funded this creator)
        cursor.execute("""
            SELECT
                funder_address,
                SUM(amount_sol) as total_amount,
                COUNT(*) as transfer_count
            FROM creator_funders
            WHERE creator_address = ?
            GROUP BY funder_address
            ORDER BY total_amount DESC
        """, (creator_address,))
        incoming_funders = cursor.fetchall()

        # Enrich funders with their details
        funders_with_info = []
        for funder in incoming_funders:
            funder_info = {
                'address': funder['funder_address'],
                'amount_sol': funder['total_amount'],
                'transfer_count': funder['transfer_count'],
                'labels': [],
                'network': None,
                'network_type': None,
                'display_name': None
            }

            # Check if funder is a CEX wallet
            cursor.execute("SELECT exchange_name, wallet_type FROM cex_wallets WHERE cex_address = ?", (funder['funder_address'],))
            cex_row = cursor.fetchone()
            if cex_row and cex_row['exchange_name']:
                funder_info['display_name'] = f"{cex_row['exchange_name']} ({cex_row['wallet_type']})"
                funder_info['labels'].append('CEX')

            # Check for infrastructure in infra_mapping (Padre, etc.)
            if not funder_info['display_name']:
                from src.utils.infra_mapping import get_account_info
                acct_info = get_account_info(funder['funder_address'])
                if acct_info:
                    funder_info['display_name'] = acct_info.get('name', '')
                    if acct_info.get('category'):
                        funder_info['labels'].append(f'INFRA({acct_info["category"]})')

            # Check for address labels (infrastructure, services, etc.)
            cursor.execute("SELECT label_name, category FROM address_labels WHERE address = ?", (funder['funder_address'],))
            label_row = cursor.fetchone()
            if label_row and label_row['label_name']:
                if not funder_info['display_name']:
                    funder_info['display_name'] = label_row['label_name']
                if label_row['category'] and label_row['category'].upper() in ['INFRASTRUCTURE', 'SERVICE', 'BRIDGE', 'DEX', 'ROUTER']:
                    if 'INFRA' not in funder_info['labels']:
                        funder_info['labels'].append(f'INFRA({label_row["category"]})')

            # Check if funder is a creator
            cursor.execute("SELECT COUNT(*) as count FROM token_analysis WHERE earliest_tx_creator = ?", (funder['funder_address'],))
            r = cursor.fetchone()
            is_creator = r and r['count'] > 0
            if is_creator:
                funder_info['labels'].append(f'CREATOR({r["count"]})')

            # Check if funder funds other creators
            cursor.execute("SELECT COUNT(DISTINCT creator_address) as count FROM creator_funders WHERE funder_address = ?", (funder['funder_address'],))
            r = cursor.fetchone()
            creator_fund_count = r['count'] if r else 0
            if creator_fund_count > 1:
                funder_info['labels'].append(f'MULTI_CREATOR_FUNDER({creator_fund_count})')
            elif is_creator and creator_fund_count >= 1:
                funder_info['labels'].append('CREATOR_FUNDING_CHAIN')

            # Check for DIRECT circular funding: funder received from creator AND sent back to creator
            cursor.execute("""
                SELECT COUNT(*) as direct_circular FROM (
                    SELECT destination FROM sol_transfers
                    WHERE source = ? AND destination = ?
                    INTERSECT
                    SELECT funder_address FROM creator_funders
                    WHERE creator_address = ? AND funder_address = ?
                )
            """, (creator_address, funder['funder_address'], creator_address, funder['funder_address']))
            direct_circ = cursor.fetchone()
            if direct_circ and direct_circ['direct_circular'] > 0:
                funder_info['labels'].append('⚠️ CIRCULAR_FUNDING(direct)')

            # Check if funder is in a funding chain (bridges SOL between creators)
            cursor.execute("SELECT COUNT(*) as count FROM funding_chains WHERE bridge_funder = ? AND chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'", (funder['funder_address'],))
            chain_row = cursor.fetchone()
            if chain_row and chain_row['count'] > 0 and '⚠️ CIRCULAR_FUNDING' not in funder_info['labels']:
                chain_count = chain_row['count']

                # Check if this is circular funding (same creators appear as both sources and targets)
                cursor.execute("""
                    SELECT COUNT(DISTINCT source_creator) as sources, COUNT(DISTINCT target_creator) as targets
                    FROM funding_chains
                    WHERE bridge_funder = ? AND chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'
                """, (funder['funder_address'],))
                creator_stats = cursor.fetchone()

                # Circular if same set of creators appear as both sources and targets
                is_circular = False
                if creator_stats and creator_stats['sources'] == creator_stats['targets'] and creator_stats['sources'] <= 5:
                    cursor.execute("""
                        SELECT COUNT(*) as overlap FROM (
                            SELECT source_creator FROM funding_chains WHERE bridge_funder = ? AND chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'
                            INTERSECT
                            SELECT target_creator FROM funding_chains WHERE bridge_funder = ? AND chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'
                        )
                    """, (funder['funder_address'], funder['funder_address']))
                    overlap = cursor.fetchone()
                    if overlap and overlap['overlap'] == creator_stats['sources']:
                        is_circular = True

                if is_circular:
                    funder_info['labels'].append(f'⚠️ CIRCULAR_FUNDING({chain_count})')
                else:
                    funder_info['labels'].append(f'CREATOR_FUNDING_CHAIN({chain_count})')

            # Check if funder is in a network
            cursor.execute("SELECT network_name FROM creator_networks WHERE creator_address = ?", (funder['funder_address'],))
            net = cursor.fetchone()
            if net:
                funder_info['network'] = net['network_name']
                cursor.execute("SELECT network_type FROM network_cex_infra_flags WHERE network_name = ?", (net['network_name'],))
                net_type_row = cursor.fetchone()
                if net_type_row:
                    funder_info['network_type'] = net_type_row['network_type']

            funders_with_info.append(funder_info)

        # Get tokens created by this creator
        cursor.execute("""
            SELECT
                ta.mint,
                ta.created_at,
                ta.price_current,
                ta.market_cap_current,
                ta.risk_level
            FROM token_analysis ta
            WHERE ta.earliest_tx_creator = ?
            ORDER BY ta.created_at DESC
            LIMIT 50
        """, (creator_address,))
        tokens = cursor.fetchall()

        # For each token, get the outgoing transfers and recipient addresses from webhooks
        tokens_with_transfers = []
        for token in tokens:
            cursor.execute("""
                SELECT
                    destination,
                    SUM(amount_sol) as amount_sol,
                    MAX(block_time) as last_transaction_time
                FROM sol_transfers
                WHERE source = ?
                GROUP BY destination
                ORDER BY amount_sol DESC
            """, (creator_address,))
            recipients = cursor.fetchall()

            # Flag each recipient if it appears elsewhere in the system
            recipients_with_flags = []
            for recipient in recipients:
                recipient_labels = []
                funded_creators_data = []

                # Check what role the RECIPIENT ADDRESS itself plays
                # 1. Is it a CREATOR (has created tokens)?
                cursor.execute("SELECT COUNT(*) as count FROM token_analysis WHERE earliest_tx_creator = ?", (recipient['destination'],))
                r = cursor.fetchone()
                creator_token_count = r['count'] if r else 0
                if creator_token_count > 0:
                    recipient_labels.append(f'CREATOR({creator_token_count})')

                # 2. Is it a FUNDER (funds creators)?
                cursor.execute("SELECT COUNT(DISTINCT creator_address) as count FROM creator_funders WHERE funder_address = ?", (recipient['destination'],))
                r = cursor.fetchone()
                funder_creator_count = r['count'] if r else 0
                if funder_creator_count > 0:
                    recipient_labels.append(f'FUNDER({funder_creator_count})')

                # 3. Is it a SENDER (sends SOL to other addresses)?
                cursor.execute("SELECT COUNT(DISTINCT destination) as count FROM sol_transfers WHERE source = ?", (recipient['destination'],))
                r = cursor.fetchone()
                sender_count = r['count'] if r else 0
                if sender_count > 0:
                    recipient_labels.append(f'SENDER({sender_count})')

                # 4. Is it MULTI_FUNDED (funded by multiple sources)?
                cursor.execute("SELECT COUNT(DISTINCT funder_address) as count FROM creator_funders WHERE creator_address = ?", (recipient['destination'],))
                r = cursor.fetchone()
                multi_funder_count = r['count'] if r else 0
                if multi_funder_count > 1:
                    recipient_labels.append(f'MULTI_FUNDED({multi_funder_count})')

                # 5. Check for infrastructure FIRST (Padre, etc.)
                recipient_display_name = None
                from src.utils.infra_mapping import get_account_info
                acct_info = get_account_info(recipient['destination'])
                if acct_info:
                    recipient_display_name = acct_info.get('name', '')
                    if acct_info.get('category'):
                        recipient_labels.append(f'INFRA({acct_info["category"]})')

                # 6. If not in infra_mapping, check if it's a CEX wallet
                if not acct_info:
                    cursor.execute("SELECT COUNT(*) as count FROM cex_wallets WHERE cex_address = ?", (recipient['destination'],))
                    r = cursor.fetchone()
                    if r and r['count'] > 0:
                        recipient_labels.append('CEX')
                        cursor.execute("SELECT exchange_name FROM cex_wallets WHERE cex_address = ?", (recipient['destination'],))
                        cex_row = cursor.fetchone()
                        if cex_row:
                            recipient_display_name = cex_row['exchange_name']

                # Check if recipient is in a network
                recipient_network = None
                cursor.execute("SELECT network_name FROM creator_networks WHERE creator_address = ?", (recipient['destination'],))
                network = cursor.fetchone()
                if network:
                    recipient_network = network['network_name']

                # Get details about funded creators
                cursor.execute("SELECT DISTINCT creator_address FROM creator_funders WHERE funder_address = ?", (recipient['destination'],))
                funded = cursor.fetchall()
                if funded:
                    funded_creators = [f['creator_address'] for f in funded]

                    for fc in funded_creators:
                        creator_info = {'address': fc, 'labels': [], 'display_name': None}

                        acct_info = get_account_info(fc)
                        if acct_info:
                            creator_info['display_name'] = acct_info.get('name', '')
                            if acct_info.get('category'):
                                creator_info['labels'].append(f'INFRA({acct_info["category"]})')

                        if not acct_info:
                            cursor.execute("SELECT COUNT(*) as count FROM cex_wallets WHERE cex_address = ?", (fc,))
                            r = cursor.fetchone()
                            if r and r['count'] > 0:
                                creator_info['labels'].append('CEX')
                                cursor.execute("SELECT exchange_name FROM cex_wallets WHERE cex_address = ?", (fc,))
                                cex_row = cursor.fetchone()
                                if cex_row:
                                    creator_info['display_name'] = cex_row['exchange_name']

                        cursor.execute("SELECT network_name FROM creator_networks WHERE creator_address = ?", (fc,))
                        net = cursor.fetchone()
                        if net:
                            creator_info['network'] = net['network_name']

                        cursor.execute("SELECT COUNT(*) as count FROM token_analysis WHERE earliest_tx_creator = ?", (fc,))
                        r = cursor.fetchone()
                        token_count = r['count'] if r else 0
                        if token_count > 0:
                            creator_info['labels'].append(f'CREATOR({token_count})')

                        cursor.execute("SELECT COUNT(DISTINCT destination) as count FROM sol_transfers WHERE source = ?", (fc,))
                        r = cursor.fetchone()
                        outgoing_count = r['count'] if r else 0
                        if outgoing_count > 0:
                            creator_info['labels'].append(f'SENDER({outgoing_count})')

                        cursor.execute("SELECT COUNT(DISTINCT funder_address) as count FROM creator_funders WHERE creator_address = ?", (fc,))
                        r = cursor.fetchone()
                        funder_count = r['count'] if r else 0
                        if funder_count > 1:
                            creator_info['labels'].append(f'MULTI_FUNDED({funder_count})')

                        funded_creators_data.append(creator_info)

                recipients_with_flags.append({
                    'address': recipient['destination'],
                    'amount_sol': recipient['amount_sol'],
                    'last_transaction_time': recipient['last_transaction_time'],
                    'labels': recipient_labels,
                    'network': recipient_network,
                    'display_name': recipient_display_name,
                    'funded_creators': funded_creators_data
                })

            tokens_with_transfers.append({
                'token': token,
                'recipients': recipients_with_flags
            })

        # Check for circular funding from webhooks
        cursor.execute("""
            SELECT COUNT(*) as count FROM sol_transfers st
            WHERE st.source = ?
            AND st.destination IN (
                SELECT funder_address FROM creator_funders
                WHERE creator_address = ?
            )
        """, (creator_address, creator_address))
        circular_result = cursor.fetchone()
        has_circular_funding = circular_result['count'] > 0 if circular_result else False

        conn.close()

        # Build findings
        findings = []

        # Check for cross-funder activity and multi-funder recipients
        cross_funder_count = 0
        multi_funded_count = 0
        funds_count = 0
        sender_count = 0

        for token_data in tokens_with_transfers:
            for recipient in token_data['recipients']:
                labels = recipient.get('labels', [])
                for label in labels:
                    if 'CROSS_FUNDER' in str(label):
                        cross_funder_count += 1
                    elif 'MULTI_FUNDED' in str(label):
                        multi_funded_count += 1
                    elif 'FUNDS_' in str(label) and 'CROSS' not in str(label):
                        funds_count += 1
                    elif 'SENDER_' in str(label):
                        sender_count += 1

        if cross_funder_count > 0:
            network_context = ""
            if network_name:
                if network_type == 'cex_connected':
                    network_context = f' within the 🏦 CEX-connected "{network_name}" network'
                elif network_type == 'infra_connected':
                    network_context = f' within the 🔧 INFRA-connected "{network_name}" network'
                elif network_type == 'mixed':
                    network_context = f' within the ⚠️ MIXED "{network_name}" network'
                elif network_type == 'organic':
                    network_context = f' within the ✓ ORGANIC "{network_name}" network'
                else:
                    network_context = f' within the "{network_name}" network'

            findings.append({
                'type': '🚨 SUSPICIOUS_CROSS_FUNDING',
                'description': f'CRITICAL: Detected {cross_funder_count} recipient address(es) that also fund OTHER different creators. This indicates potential coordinated manipulation or hidden funding networks{network_context}.',
                'networks': [network_name] if network_name else []
            })

        if funds_count > 0 or multi_funded_count > 0:
            network_context = ""
            if network_name:
                if network_type == 'cex_connected':
                    network_context = f' as part of the 🏦 CEX-connected "{network_name}" network'
                elif network_type == 'infra_connected':
                    network_context = f' as part of the 🔧 INFRA-connected "{network_name}" network'
                elif network_type == 'mixed':
                    network_context = f' as part of the ⚠️ MIXED "{network_name}" network'
                elif network_type == 'organic':
                    network_context = f' as part of the ✓ ORGANIC "{network_name}" network'
                else:
                    network_context = f' as part of the "{network_name}" network'

            findings.append({
                'type': '⚠️ FUNDER_ROUTING',
                'description': f'This creator sends SOL to {funds_count} funder address(es) that also fund other creators, and {multi_funded_count} address(es) funded by multiple sources. Possible coordinated network activity{network_context}.',
                'networks': [network_name] if network_name else []
            })

        if funding_chains:
            affected_networks = set()
            if network_name:
                affected_networks.add(network_name)

            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            for chain in funding_chains:
                cursor.execute("""
                    SELECT network_name FROM creator_networks
                    WHERE creator_address = ?
                """, (chain['target_creator'],))
                target_net = cursor.fetchone()
                if target_net:
                    affected_networks.add(target_net['network_name'])
            conn.close()

            findings.append({
                'type': 'CREATOR_FUNDING_CHAIN',
                'description': f'Detected {len(funding_chains)} creator-to-funder-to-creator funding chains. This creator sent SOL to funders who also fund other creators.',
                'networks': list(affected_networks)
            })

        if coordinated_edges:
            network_context = ""
            if network_name:
                if network_type == 'cex_connected':
                    network_context = f' (member of 🏦 CEX-connected "{network_name}")'
                elif network_type == 'infra_connected':
                    network_context = f' (member of 🔧 INFRA-connected "{network_name}")'
                elif network_type == 'mixed':
                    network_context = f' (member of ⚠️ MIXED "{network_name}")'
                elif network_type == 'organic':
                    network_context = f' (member of ✓ ORGANIC "{network_name}")'
                else:
                    network_context = f' (member of "{network_name}")'

            findings.append({
                'type': 'COORDINATED_FUNDING',
                'description': f'This creator is part of {len(coordinated_edges)} coordinated funding relationships with other creators through shared funders{network_context}.',
                'networks': [network_name] if network_name else []
            })

        # Check for distribution pattern
        if (len(incoming_funders) > 0 and transfers['count'] > 0 and
            len(incoming_funders) <= 10 and transfers['unique_recipients'] > len(incoming_funders) * 1.5):
            isolated_funders = sum(1 for f in incoming_funders for _ in [True])
            if isolated_funders >= 5:
                findings.append({
                    'type': '⚠️ DISTRIBUTION_PATTERN',
                    'description': f'Creator receives from {len(incoming_funders)} isolated funders (only support this creator) but distributes to {transfers["unique_recipients"]} separate addresses. Pattern suggests fund distribution/intermediary activity rather than organic token creation support.',
                    'networks': []
                })

        # Check for self-funding WITH circular funding
        if is_self_funding and self_funding_intermediates > 0:
            if self_funding_percentage >= 50 and has_circular_funding:
                findings.insert(0, {
                    'type': '🚩 SELF-FUNDING SCHEME',
                    'description': f'{int(self_funding_percentage)}% of this creator\'s funders ({self_funding_intermediates}/{total_funders}) only fund them, AND the creator sends money back to these funders. Circular funding pattern proves self-funding through intermediaries.',
                    'networks': []
                })

        # Add network membership findings
        if networks_with_types:
            for net_info in networks_with_types:
                net_name = net_info['name']
                net_type = net_info['type']

                network_type_desc = "coordinated funding network"
                finding_type = '⚠️ NETWORK_MEMBER'

                if net_type == 'cex_connected':
                    network_type_desc = "🏦 CEX-connected coordinated network"
                    finding_type = '🚨 NETWORK_MEMBER_CEX'
                elif net_type == 'infra_connected':
                    network_type_desc = "🔧 INFRA-connected coordinated network"
                    finding_type = '⚠️ NETWORK_MEMBER_INFRA'
                elif net_type == 'mixed':
                    network_type_desc = "⚠️ MIXED (CEX+INFRA) coordinated network"
                    finding_type = '🚨 NETWORK_MEMBER_MIXED'
                elif net_type == 'organic':
                    network_type_desc = "✓ ORGANIC creator-to-creator network"
                    finding_type = 'ℹ️ NETWORK_MEMBER'

                findings.append({
                    'type': finding_type,
                    'description': f'Creator is part of the "{net_name}" {network_type_desc}. Part of larger coordinated structure.',
                    'networks': [net_name]
                })
        elif not findings:
            findings.append({
                'type': 'CLEAN',
                'description': 'No suspicious activity detected. Creator operates independently.',
                'networks': []
            })

        # Check if this address received SOL
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(DISTINCT source) as count FROM sol_transfers WHERE destination = ?", (creator_address,))
        result = cursor.fetchone()
        received_from_count = result['count'] if result else 0
        conn.close()

        if received_from_count > 0 and not any('RECIPIENT' in f.get('type', '') for f in findings):
            findings.append({
                'type': 'ℹ️ RECIPIENT',
                'description': f'This address received SOL from {received_from_count} creator(s). It functions as a recipient/intermediate address in the funding network.',
                'networks': []
            })

        # Enrich findings with network type information
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        for finding in findings:
            if finding['networks']:
                enriched_networks = []
                for net_name in finding['networks']:
                    cursor.execute("""
                        SELECT network_type FROM network_cex_infra_flags
                        WHERE network_name = ?
                    """, (net_name,))
                    net_type_row = cursor.fetchone()
                    net_type = net_type_row['network_type'] if net_type_row else None
                    enriched_networks.append({
                        'name': net_name,
                        'type': net_type
                    })
                finding['networks_enriched'] = enriched_networks

        # Enrich funding chains with display names
        enriched_funding_chains = []
        from src.utils.infra_mapping import get_account_info
        for fc in funding_chains:
            chain_data = {
                'source_creator': fc['source_creator'],
                'source_creator_display': None,
                'bridge_funder': fc['bridge_funder'],
                'bridge_funder_display': None,
                'target_creator': fc['target_creator'],
                'target_creator_display': None,
                'amount_sol': fc['source_to_bridge_amount_sol'],
                'confidence': fc['confidence'],
                'timestamp': fc['source_block_time']
            }

            acct_info = get_account_info(fc['source_creator'])
            if acct_info:
                chain_data['source_creator_display'] = acct_info.get('name', '')
            else:
                cursor.execute("SELECT exchange_name FROM cex_wallets WHERE cex_address = ?", (fc['source_creator'],))
                cex_row = cursor.fetchone()
                if cex_row:
                    chain_data['source_creator_display'] = cex_row['exchange_name']

            acct_info = get_account_info(fc['bridge_funder'])
            if acct_info:
                chain_data['bridge_funder_display'] = acct_info.get('name', '')
            else:
                cursor.execute("SELECT exchange_name FROM cex_wallets WHERE cex_address = ?", (fc['bridge_funder'],))
                cex_row = cursor.fetchone()
                if cex_row:
                    chain_data['bridge_funder_display'] = cex_row['exchange_name']

            acct_info = get_account_info(fc['target_creator'])
            if acct_info:
                chain_data['target_creator_display'] = acct_info.get('name', '')
            else:
                cursor.execute("SELECT exchange_name FROM cex_wallets WHERE cex_address = ?", (fc['target_creator'],))
                cex_row = cursor.fetchone()
                if cex_row:
                    chain_data['target_creator_display'] = cex_row['exchange_name']

            enriched_funding_chains.append(chain_data)

        conn.close()

        return jsonify({
            'creator_address': creator_address,
            'last_scanned': last_scanned,
            'scan_status': 'Real-time webhook data' if transfers['count'] > 0 else 'No webhook activity',
            'network_name': network_name,
            'network_type': network_type,
            'networks': networks_with_types,
            'outgoing_transfer_count': transfers['count'] if transfers else 0,
            'total_sol_sent': transfers['total_sol'] if transfers else 0,
            'unique_recipients': transfers['unique_recipients'] if transfers else 0,
            'last_transaction_time': transfers['last_transaction_time'] if transfers else None,
            'incoming_funders': funders_with_info,
            'funding_chain_count': len(funding_chains),
            'coordinated_edge_count': len(coordinated_edges),
            'findings': findings,
            'tokens': [
                {
                    'mint': t['token']['mint'],
                    'created_at': t['token']['created_at'],
                    'price_current': t['token']['price_current'],
                    'market_cap_current': t['token']['market_cap_current'],
                    'risk_level': t['token']['risk_level'],
                    'recipients': t['recipients']
                }
                for t in tokens_with_transfers
            ],
            'funding_chains': enriched_funding_chains[:20]
        })
    except Exception as e:
        import traceback
        print(f"ERROR in api_creator_outgoing_analysis: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/scan-creator/<creator_address>', methods=['POST'])
def api_scan_creator(creator_address: str):
    """Trigger extraction for a specific creator"""
    try:
        import asyncio
        import sys
        import os
        import datetime

        # Import the extraction module
        from src.extractors.creator_outgoing_extractor import (
            rpc_get_signatures, helius_enhanced_parse, extract_outgoing_sol,
            detect_and_update_networks_from_outgoing, calculate_and_store_self_funding
        )

        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get current cursor position for this creator
        cursor.execute("""
            SELECT last_signature, last_slot FROM creator_sig_cursors
            WHERE creator_address = ?
        """, (creator_address,))
        cursor_row = cursor.fetchone()
        last_sig = cursor_row['last_signature'] if cursor_row else None
        last_slot = cursor_row['last_slot'] if cursor_row else None

        # Run async extraction
        async def extract():
            import aiohttp
            async with aiohttp.ClientSession() as session:
                # Get signatures for this creator
                sigs = await rpc_get_signatures(session, creator_address, limit=25)

                # Filter for fresh signatures
                fresh_sigs = []
                newest_sig = None
                newest_slot = None

                for item in sigs:
                    s = item.get("signature")
                    if not s:
                        continue

                    if newest_sig is None:
                        newest_sig = s
                        newest_slot = item.get("slot")

                    if last_sig and s == last_sig:
                        break

                    if item.get("err") is None:
                        fresh_sigs.append(s)

                if not fresh_sigs:
                    return {"status": "no_new_transfers", "fresh_sigs": 0}

                # Parse signatures
                parsed = await helius_enhanced_parse(session, fresh_sigs, source_file="main")

                # Extract outgoing SOL transfers
                transfers = extract_outgoing_sol(parsed, {creator_address})

                if transfers:
                    # Write transfers to database
                    cursor.executemany("""
                        INSERT OR IGNORE INTO creator_outgoing_transfers
                        (creator_address, recipient_address, amount_sol, transaction_signature, slot, block_time)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, transfers)
                    conn.commit()

                # Update cursor
                if newest_sig:
                    cursor.execute("""
                        INSERT OR REPLACE INTO creator_sig_cursors
                        (creator_address, last_signature, last_slot, updated_at)
                        VALUES (?, ?, ?, ?)
                    """, (creator_address, newest_sig, newest_slot, datetime.datetime.utcnow().isoformat()))
                    conn.commit()

                return {"status": "success", "transfers_found": len(transfers), "fresh_sigs": len(fresh_sigs)}

        # Run the async extraction
        result = asyncio.run(extract())

        # Run network detection and self-funding calculation
        detect_and_update_networks_from_outgoing()
        calculate_and_store_self_funding()

        # Log creator-to-creator transfers and detect CEX/INFRA connections
        log_creator_to_creator_transfers(creator_address, conn)
        detect_network_cex_infra_connections()

        conn.close()

        return jsonify({
            'status': 'completed',
            'creator_address': creator_address,
            'extraction_result': result
        })

    except Exception as e:
        import traceback
        print(f"ERROR in api_scan_creator: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


def log_creator_to_creator_transfers(source_creator: str, conn):
    """Log transfers where source creator funds other creators"""
    try:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all outgoing transfers from this creator
        cursor.execute("""
            SELECT cot.recipient_address, cot.amount_sol, cot.transaction_signature, cot.block_time,
                   cn.network_name
            FROM creator_outgoing_transfers cot
            LEFT JOIN creator_networks cn ON cot.recipient_address = cn.creator_address
            WHERE cot.creator_address = ?
        """, (source_creator,))

        transfers = cursor.fetchall()

        for transfer in transfers:
            recipient = transfer['recipient_address']

            # Check if recipient is a creator
            cursor.execute("SELECT COUNT(*) as count FROM token_analysis WHERE earliest_tx_creator = ?", (recipient,))
            is_creator = cursor.fetchone()['count'] > 0

            if is_creator:
                # Get source creator's network
                cursor.execute("SELECT network_name FROM creator_networks WHERE creator_address = ?", (source_creator,))
                source_network_row = cursor.fetchone()
                source_network = source_network_row['network_name'] if source_network_row else None

                # Get target creator's network
                target_network = transfer['network_name']

                # Log the creator-to-creator transfer
                cursor.execute("""
                    INSERT OR IGNORE INTO creator_to_creator_transfers
                    (source_creator, target_creator, amount_sol, transaction_signature, block_time,
                     source_network, target_network)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (source_creator, recipient, transfer['amount_sol'], transfer['transaction_signature'],
                      transfer['block_time'], source_network, target_network))

        conn.commit()
    except Exception as e:
        print(f"Error logging creator-to-creator transfers: {str(e)}")


def detect_network_cex_infra_connections():
    """Detect and flag networks that have CEX or INFRA funders"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all networks from both creator_networks and creator_to_creator_networks
        cursor.execute("""
            SELECT DISTINCT network_name FROM creator_networks WHERE network_name IS NOT NULL
            UNION
            SELECT DISTINCT network_name FROM creator_to_creator_networks
        """)
        networks = cursor.fetchall()

        for network_row in networks:
            network_name = network_row['network_name']

            # Get all creators in this network
            creators_in_network = set()

            # From creator_networks (for traditional networks with primary + connected)
            cursor.execute("""
                SELECT creator_address, connected_creators FROM creator_networks
                WHERE network_name = ?
            """, (network_name,))
            network_data = cursor.fetchone()

            if network_data:
                creators_in_network.add(network_data['creator_address'])
                if network_data['connected_creators']:
                    try:
                        connected = json.loads(network_data['connected_creators'])
                        creators_in_network.update(connected)
                    except:
                        pass

            # From creator_to_creator_networks (for organic creator-to-creator networks)
            cursor.execute("""
                SELECT creator_address FROM creator_to_creator_networks
                WHERE network_name = ?
            """, (network_name,))
            c2c_creators = cursor.fetchall()
            for row in c2c_creators:
                creators_in_network.add(row['creator_address'])

            if not creators_in_network:
                continue

            # Check which funders are CEX or INFRA
            cex_funders = []
            infra_funders = []

            for creator in creators_in_network:
                # Get funders for this creator
                cursor.execute("""
                    SELECT DISTINCT funder_address FROM creator_funders WHERE creator_address = ?
                """, (creator,))
                funders = cursor.fetchall()

                for funder_row in funders:
                    funder = funder_row['funder_address']

                    # Check if CEX
                    cursor.execute("SELECT COUNT(*) as count FROM cex_wallets WHERE cex_address = ?", (funder,))
                    if cursor.fetchone()['count'] > 0:
                        cex_funders.append(funder)

                    # Check if INFRA
                    cursor.execute("SELECT COUNT(*) as count FROM address_labels WHERE address = ? AND category IN ('INFRASTRUCTURE', 'SERVICE', 'BRIDGE')", (funder,))
                    if cursor.fetchone()['count'] > 0:
                        infra_funders.append(funder)

            # Determine network type
            # CreatorTransfer networks are always organic by definition (direct creator-to-creator transfers)
            # They should have NO CEX/INFRA flags regardless of the creators' other funders
            if network_name.startswith('CreatorTransfer_'):
                network_type = 'organic'
                has_cex = 0
                has_infra = 0
            else:
                has_cex = len(cex_funders) > 0
                has_infra = len(infra_funders) > 0

                if has_cex and has_infra:
                    network_type = 'mixed'
                elif has_cex:
                    network_type = 'cex_connected'
                elif has_infra:
                    network_type = 'infra_connected'
                else:
                    network_type = 'organic'

            # Update network flags
            cursor.execute("""
                INSERT OR REPLACE INTO network_cex_infra_flags
                (network_name, has_cex_funder, has_infra_funder, cex_funder_addresses,
                 infra_funder_addresses, network_type)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (network_name, 1 if has_cex else 0, 1 if has_infra else 0,
                  json.dumps(list(set(cex_funders))), json.dumps(list(set(infra_funders))), network_type))

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error detecting network CEX/INFRA connections: {str(e)}")


@app.route('/api/creator-recent-checks')
def api_creator_recent_checks():
    """Get the most recently active creators from webhook data with their findings"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get creators from WEBHOOK DATA (sol_transfers) - most recently active
        # Order by MAX(block_time) to get most recent webhook activity
        cursor.execute("""
            SELECT source, MAX(block_time) as latest_block_time
            FROM sol_transfers
            GROUP BY source
            ORDER BY MAX(block_time) DESC
            LIMIT 15
        """)
        recent_creators = [row['source'] for row in cursor.fetchall()]

        recent_checks = []
        for creator in recent_creators:
            # Get token count for this creator
            cursor.execute("""
                SELECT COUNT(*) as token_count FROM token_analysis
                WHERE earliest_tx_creator = ?
            """, (creator,))
            token_count = cursor.fetchone()['token_count']

            # Get funder count
            cursor.execute("""
                SELECT COUNT(DISTINCT funder_address) as funder_count
                FROM creator_funders
                WHERE creator_address = ?
            """, (creator,))
            funder_count = cursor.fetchone()['funder_count']

            # Get funding chain count (creator as source)
            cursor.execute("""
                SELECT COUNT(*) as chain_count FROM funding_chains
                WHERE source_creator = ?
            """, (creator,))
            chain_count = cursor.fetchone()['chain_count'] or 0

            # Get outgoing transfer count and latest activity time from WEBHOOKS
            cursor.execute("""
                SELECT COUNT(*) as outgoing_count, MAX(block_time) as latest_scan
                FROM sol_transfers
                WHERE source = ?
            """, (creator,))
            result = cursor.fetchone()
            outgoing_count = result['outgoing_count'] or 0
            latest_scan = result['latest_scan']

            # Convert Unix timestamp to datetime string
            last_scanned = None
            if latest_scan is not None:
                try:
                    last_scanned = datetime.utcfromtimestamp(latest_scan).strftime('%Y-%m-%d %H:%M:%S')
                except Exception as e:
                    last_scanned = f"Error: {str(e)}"

            # Determine findings based on creator behavior
            findings = []

            # Check for self-funding pattern
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

            # Check for distribution pattern (many recipients, few funders) from webhooks
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

            # Check for funding chains (creator-to-funder-to-creator)
            if int(chain_count) > 0:
                findings.append('⚠️ CREATOR_FUNDING_CHAIN')

            # Check for coordinated edges with network info
            cursor.execute("""
                SELECT COUNT(*) as coordinated_count FROM coordinated_creator_edges
                WHERE creator_a = ? OR creator_b = ?
            """, (creator, creator))
            coordinated_count = cursor.fetchone()['coordinated_count'] or 0
            if coordinated_count > 0:
                findings.append('⚠️ COORDINATED')

            # Check if creator is in a network
            cursor.execute("""
                SELECT DISTINCT fnm.network_id FROM funding_network_members fnm
                WHERE fnm.funder_address = ?
                LIMIT 1
            """, (creator,))
            network_row = cursor.fetchone()
            if network_row:
                findings.append(f"⚠️ NETWORK_MEMBER")

            # Check creator-to-creator networks
            cursor.execute("""
                SELECT DISTINCT network_name FROM creator_to_creator_networks
                WHERE creator_address = ?
                LIMIT 1
            """, (creator,))
            c2c_network = cursor.fetchone()
            if c2c_network and c2c_network['network_name']:
                findings.append(f"⚠️ C2C_NETWORK")

            # Default to CLEAN if no red flags
            if not findings:
                findings.append('✅ CLEAN')

            recent_checks.append({
                'creator_address': creator,
                'token_count': token_count,
                'funder_count': funder_count,
                'chain_count': chain_count,
                'outgoing_count': outgoing_count,
                'last_scanned': last_scanned,
                'networks': [],
                'findings': findings
            })

        conn.close()
        return jsonify({'recent_checks': recent_checks})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/creator-scan-stats')
def api_creator_scan_stats():
    """Get scanning statistics by tier"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get total creator count
        cursor.execute("SELECT COUNT(DISTINCT earliest_tx_creator) as total FROM token_analysis")
        total_creators = cursor.fetchone()['total'] or 0

        # Get creators with outgoing transfers (scanned by extractor)
        cursor.execute("SELECT COUNT(DISTINCT creator_address) as total FROM creator_outgoing_transfers")
        scanned_creators = cursor.fetchone()['total'] or 0
        scanned_percentage = (scanned_creators / total_creators * 100) if total_creators > 0 else 0

        conn.close()

        return jsonify({
            'total_creators': total_creators,
            'scanned_creators': scanned_creators,
            'scanned_percentage': scanned_percentage,
            'tier_stats': {
                0: {'count': scanned_creators, 'percentage': scanned_percentage},
                1: {'count': total_creators - scanned_creators, 'percentage': 100 - scanned_percentage}
            },
            'tier_labels': {
                0: 'Creators with outgoing transfers scanned',
                1: 'Creators pending outgoing transfer scan'
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _build_score_section(score_info: dict) -> str:
    """Build HTML score display section for creator network page"""
    if not score_info or score_info.get('score') is None:
        return ""

    score = score_info['score']
    components = score_info.get('components', {})

    # Determine badge color
    badge_color_map = {
        'high': '#ef4444',    # red
        'medium': '#eab308',  # yellow
        'low': '#22c55e'      # green
    }
    badge_color = badge_color_map.get(score_info.get('score_badge', 'medium'), '#eab308')

    return f"""
            <div class="members-section" style="background: var(--bg-secondary); border-radius: 8px; padding: 20px; border: 1px solid rgba(124, 58, 237, 0.3); margin-bottom: 30px;">
                <h2 style="color: var(--accent-purple); font-size: 16px; margin-bottom: 15px; display: flex; align-items: center; gap: 8px;">
                    📊 Risk Score
                    <span style="display: inline-block; background-color: {badge_color}; color: #000; padding: 4px 8px; border-radius: 4px; font-size: 14px; font-weight: bold; margin-left: auto;">{score} / 100</span>
                </h2>
                <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px;">
                    <div style="background: var(--bg-primary); padding: 12px; border-radius: 6px; border-left: 3px solid rgba(59, 130, 246, 0.5);">
                        <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Connectivity</div>
                        <div style="font-weight: bold; font-size: 18px; color: #3b82f6;">{components.get('connectivity', 0)} / 40</div>
                    </div>
                    <div style="background: var(--bg-primary); padding: 12px; border-radius: 6px; border-left: 3px solid rgba(251, 191, 36, 0.5);">
                        <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Lifecycle</div>
                        <div style="font-weight: bold; font-size: 18px; color: #fbbf24;">{components.get('lifecycle', 0)} / 25</div>
                    </div>
                    <div style="background: var(--bg-primary); padding: 12px; border-radius: 6px; border-left: 3px solid rgba(168, 85, 247, 0.5);">
                        <div style="color: var(--text-secondary); font-size: 11px; text-transform: uppercase; margin-bottom: 5px;">Evidence</div>
                        <div style="font-weight: bold; font-size: 18px; color: #a855f7;">{components.get('evidence', 0)} / 35</div>
                    </div>
                </div>
            </div>
    """


@app.route('/creator-network/<network_name>')

def creator_network_page(network_name: str):
    """Display creator network details and members separated by role"""
    from urllib.parse import unquote
    import json
    
    def new_path():
        """NEW PATH: Use networks_release and network_membership"""
        network_name_decoded = unquote(network_name)

        # Get network info from networks_release
        network = get_network_release_by_name(network_name_decoded, include_evidence=False)

        if not network:
            return {'error': f"Network '{network_name_decoded}' not found"}, 404

        # Get members from network_membership
        members = get_network_members(network_name_decoded)

        # Get network score
        score_info = get_network_score(network_name_decoded)

        return {
            'network': network,
            'members': members,
            'network_name': network_name_decoded,
            'creator_count': len(members),
            'funder_count': 0,
            'score_info': score_info
        }, 200
    
    def legacy_path():
        """OLD PATH: Use creator_networks and creator_to_creator_networks"""
        network_name_decoded = unquote(network_name)
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Get network info
        # First try creator_networks (traditional networks)
        cursor.execute("""
            SELECT
                creator_address,
                connected_creators,
                network_size,
                network_risk_level,
                updated_at
            FROM creator_networks
            WHERE network_name = ?
            LIMIT 1
        """, (network_name_decoded,))
        network_row = cursor.fetchone()
        
        # If not found, check if it's a CreatorTransfer network
        is_creator_transfer = False
        if not network_row and network_name_decoded.startswith('CreatorTransfer_'):
            is_creator_transfer = True
            # For CreatorTransfer networks, get creators from creator_to_creator_networks
            cursor.execute("""
                SELECT creator_address FROM creator_to_creator_networks
                WHERE network_name = ?
            """, (network_name_decoded,))
            c2c_creators = cursor.fetchall()
            if c2c_creators:
                # Create a virtual network_row for display
                primary_creator = c2c_creators[0]['creator_address']
                connected_creators = [c['creator_address'] for c in c2c_creators[1:]]
                network_row = {
                    'creator_address': primary_creator,
                    'connected_creators': json.dumps(connected_creators),
                    'network_size': len(c2c_creators),
                    'network_risk_level': 'HIGH',
                    'updated_at': ''
                }
        
        # Get CEX/INFRA info for this network
        cursor.execute("""
            SELECT network_type, has_cex_funder, has_infra_funder
            FROM network_cex_infra_flags
            WHERE network_name = ?
        """, (network_name_decoded,))
        cex_infra_row = cursor.fetchone()
        network_type = cex_infra_row['network_type'] if cex_infra_row else 'unknown'
        has_cex = cex_infra_row['has_cex_funder'] if cex_infra_row else 0
        has_infra = cex_infra_row['has_infra_funder'] if cex_infra_row else 0
        
        if not network_row:
            conn.close()
            return {'error': f"Network '{network_name_decoded}' not found"}, 404
        
        creators_html = ""
        funders_html = ""
        creator_count = 0
        funder_count = 0
        
        # Helper function to check if address is CEX
        def is_cex_address(addr):
            cursor.execute("SELECT COUNT(*) as count FROM cex_wallets WHERE cex_address = ?", (addr,))
            return cursor.fetchone()['count'] > 0
        
        # Helper function to get member role tag with network type indicator
        def get_member_role_tag(addr, base_role):
            cex_badge = " 🏦 CEX" if is_cex_address(addr) else ""
            # Add network type badge for CreatorTransfer networks
            network_type_badge = ""
            if is_creator_transfer:
                if network_type == 'organic':
                    network_type_badge = " ✓ ORGANIC"
                elif network_type == 'cex_connected':
                    network_type_badge = " 🏦 CEX"
                elif network_type == 'infra_connected':
                    network_type_badge = " 🔧 INFRA"
                elif network_type == 'mixed':
                    network_type_badge = " ⚠️ MIXED"
            return f"{base_role}{cex_badge}{network_type_badge}"
        
        try:
            connected = json.loads(network_row['connected_creators'])
            
            # Check primary creator
            cursor.execute("SELECT COUNT(*) as count FROM token_analysis WHERE earliest_tx_creator = ?",
                         (network_row['creator_address'],))
            is_primary_creator = cursor.fetchone()['count'] > 0
            
            if is_primary_creator:
                role_tag = get_member_role_tag(network_row['creator_address'], "PRIMARY CREATOR")
                creators_html += f"""
                    <div class="network-member-row">
                        <div class="member-address">{network_row['creator_address']}</div>
                        <div class="member-role">{role_tag}</div>
                        <div class="member-added">{network_row['updated_at']}</div>
                    </div>
                """
                creator_count += 1
            
            # Categorize connected members
            for addr in connected:
                cursor.execute("SELECT COUNT(*) as count FROM token_analysis WHERE earliest_tx_creator = ?", (addr,))
                is_creator = cursor.fetchone()['count'] > 0
                
                if is_creator:
                    role_tag = get_member_role_tag(addr, "CREATOR")
                    creators_html += f"""
                        <div class="network-member-row">
                            <div class="member-address">{addr}</div>
                            <div class="member-role">{role_tag}</div>
                            <div class="member-added">{network_row['updated_at']}</div>
                        </div>
                    """
                    creator_count += 1
                else:
                    role_tag = get_member_role_tag(addr, "FUNDER")
                    funders_html += f"""
                        <div class="network-member-row">
                            <div class="member-address">{addr}</div>
                            <div class="member-role">{role_tag}</div>
                            <div class="member-added">{network_row['updated_at']}</div>
                        </div>
                    """
                    funder_count += 1
            
            # For FundingChain networks, also get funders from funding_chains table
            if network_name_decoded.startswith('FundingChain_'):
                # Extract all creators in this network
                all_creators = [network_row['creator_address']] + connected
                
                # Get all unique bridge funders for these creators
                cursor.execute("""
                    SELECT DISTINCT bridge_funder FROM funding_chains
                    WHERE source_creator IN (""" + ",".join(["?"] * len(all_creators)) + """)
                       OR target_creator IN (""" + ",".join(["?"] * len(all_creators)) + """)
                """, all_creators + all_creators)
                
                bridge_funders = [row['bridge_funder'] for row in cursor.fetchall()]
                
                # Add bridge funders to funders section
                for funder in bridge_funders:
                    if funder and funder not in connected:  # Avoid duplicates
                        role_tag = get_member_role_tag(funder, "FUNDER")
                        funders_html += f"""
                            <div class="network-member-row">
                                <div class="member-address">{funder}</div>
                                <div class="member-role">{role_tag}</div>
                                <div class="member-added">{network_row['updated_at']}</div>
                            </div>
                        """
                        funder_count += 1
        
        except Exception as parse_error:
            creators_html = f'<p style="color: var(--text-secondary);">Error parsing members: {str(parse_error)}</p>'
        
        conn.close()

        # Get network score
        score_info = get_network_score(network_name_decoded)

        return {
            'network': network_row,
            'creators_html': creators_html,
            'funders_html': funders_html,
            'creator_count': creator_count,
            'funder_count': funder_count,
            'network_name': network_name_decoded,
            'network_type': network_type,
            'has_cex': has_cex,
            'has_infra': has_infra,
            'score_info': score_info
        }, 200
    
    # Route to new or legacy path
    result, status_code = route_phase2c('/creator-network', new_path, legacy_path)
    
    if status_code != 200:
        return result, status_code
    
    # Extract result from jsonify response
    import json as json_module
    if isinstance(result, str):
        return result, status_code
    context = json_module.loads(result.get_data(as_text=True))
    
    if context.get('error'):
        return f"<h1>Error</h1><p>{context.get('error')}</p>", 404
    
    # Build HTML response
    network_name_decoded = unquote(network_name)
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Creator Network: {network_name_decoded}</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            :root {{
                --primary: #7c3aed;
                --text-primary: #e5e7eb;
                --text-secondary: #a1a5b4;
                --bg-primary: #1a1a24;
                --bg-secondary: rgba(20, 20, 32, 0.85);
                --accent-cyan: #06b6d4;
                --accent-purple: #a78bfa;
                --color-creator: #fbbf24;
                --color-funder: #3b82f6;
            }}
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #0a0a0e 0%, #0d0d15 100%);
                color: var(--text-primary);
                padding: 20px;
            }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 30px;
            }}
            h1 {{
                color: var(--primary);
                margin: 0;
                font-size: 28px;
            }}
            .back-link {{
                color: var(--accent-cyan);
                text-decoration: none;
                font-size: 13px;
                transition: color 0.2s;
            }}
            .back-link:hover {{
                color: var(--accent-purple);
            }}
            .network-members {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 30px;
                margin-top: 30px;
            }}
            .members-section {{
                background: var(--bg-secondary);
                border-radius: 8px;
                padding: 20px;
                border: 1px solid rgba(124, 58, 237, 0.3);
            }}
            .members-section h2 {{
                color: var(--accent-purple);
                font-size: 16px;
                margin-bottom: 20px;
                display: flex;
                align-items: center;
                gap: 8px;
            }}
            .network-member-row {{
                background: var(--bg-primary);
                padding: 12px;
                border-radius: 6px;
                margin-bottom: 10px;
                border-left: 3px solid rgba(124, 58, 237, 0.5);
            }}
            .member-address {{
                font-family: monospace;
                font-size: 12px;
                color: var(--text-secondary);
                margin-bottom: 5px;
                word-break: break-all;
            }}
            .member-role {{
                font-size: 11px;
                font-weight: bold;
                color: var(--accent-cyan);
                text-transform: uppercase;
            }}
            .member-added {{
                font-size: 10px;
                color: var(--text-secondary);
                margin-top: 5px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>🔗 {network_name_decoded}</h1>
                <a href="/networks" class="back-link">← Back to Networks</a>
            </header>

            {_build_score_section(context.get('score_info', {}))}

            <div class="network-members">
                <div class="members-section">
                    <h2>👥 Creators ({context.get('creator_count', 0)})</h2>
                    {context.get('creators_html', '<p style="color: var(--text-secondary);">No creators found</p>')}
                </div>
                <div class="members-section">
                    <h2>💰 Funders ({context.get('funder_count', 0)})</h2>
                    {context.get('funders_html', '<p style="color: var(--text-secondary);">No funders found</p>')}
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    return html


@app.route('/system-health')
def system_health():
    """System health dashboard with real-time monitoring."""
    return render_template('system_health_dashboard.html', active_page='health')


@app.route('/network-monitoring')
def network_monitoring():
    """
    Phase 8A: Monitoring Dashboard with Risk Band & Trend Surfacing

    Display precomputed monitoring data from Phase 4C + Phase 7A + Phase 7E:
    - Latest alerts (from network_alerts) with severity/alert_type/search filters
    - High risk networks (from network_scores) with smoothed_score, stability_coeff, risk_band, trend_direction
    - Biggest score movers (from network_score_history)

    Sorting: ?sort=score (default), ?sort=risk, ?sort=trend
    Filtering: ?band=CRITICAL|ELEVATED|MODERATE|LOW (optional)
    """
    try:
        conn, cursor = get_db_conn()

        # Check if monitoring tables exist
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name IN ('network_alerts', 'network_score_history')
        """)
        tables = [row[0] for row in cursor.fetchall()]

        if 'network_alerts' not in tables or 'network_score_history' not in tables:
            conn.close()
            return render_template_string('''
            <!DOCTYPE html>
            <html>
            <head>
                <title>Network Monitoring</title>
                <style>
                    body { font-family: system-ui, -apple-system, sans-serif; margin: 20px; }
                    .warning { background: #fef3c7; border: 1px solid #f59e0b; border-radius: 4px; padding: 15px; color: #92400e; }
                </style>
            </head>
            <body>
                <h1>🔍 Network Monitoring</h1>
                <div class="warning">
                    <strong>⚠️ Monitoring not yet available</strong><br>
                    The monitoring system requires the build process to have run. Please run the build to generate monitoring data.
                </div>
            </body>
            </html>
            ''')

        # Section A: Latest Alerts with filters (Phase 4E + Phase 8B)
        severity = request.args.get('severity', '').strip()
        alert_type = request.args.get('alert_type', '').strip()
        q = request.args.get('q', '').strip()
        show = request.args.get('show', 'active').strip().lower()

        # Phase 8B: Build alert lifecycle filter
        # ACTIVE = acknowledged = 0 AND (suppressed_until IS NULL OR suppressed_until <= now)
        where_parts = [
            "(? IS NULL OR severity = ?)",
            "(? IS NULL OR alert_type = ?)",
            "(? IS NULL OR network_name LIKE ?)"
        ]
        params = [
            severity if severity else None, severity,
            alert_type if alert_type else None, alert_type,
            q if q else None, f"%{q}%" if q else None
        ]

        # Add lifecycle filter based on ?show= parameter
        if show == 'active':
            where_parts.append("(acknowledged = 0 AND (suppressed_until IS NULL OR suppressed_until <= CURRENT_TIMESTAMP))")
        elif show == 'unacked':
            where_parts.append("acknowledged = 0")
        elif show == 'escalated':
            where_parts.append("is_escalated = 1")
        # show == 'all' uses no additional filter

        where_clause = ' AND '.join(where_parts)

        # Build parameterized query for alerts (Phase 8B includes lifecycle fields)
        cursor.execute(f'''
            SELECT alert_id, network_name, alert_type, severity, message, created_at,
                   acknowledged, acknowledged_at, acknowledged_by,
                   suppressed_until, suppressed_at, suppression_reason,
                   is_escalated, escalated_at, escalation_rule, escalation_reason
            FROM network_alerts
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT 200
        ''', params)
        latest_alerts = cursor.fetchall()

        # Section B: High Risk Networks with Phase 7A + 7E metrics
        # Phase 8A: Determine sort order (default: score)
        sort_param = request.args.get('sort', 'score').strip().lower()
        band_filter = request.args.get('band', '').strip().upper()

        # Build ORDER BY clause with explicit mapping (no dynamic injection)
        if sort_param == 'risk':
            # Sort by risk_band priority: CRITICAL(1) < ELEVATED(2) < MODERATE(3) < LOW(4)
            # ELSE 5 provides defensive handling for corrupted/unexpected values
            order_clause = '''ORDER BY
                CASE COALESCE(ns.risk_band, 'LOW')
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'ELEVATED' THEN 2
                    WHEN 'MODERATE' THEN 3
                    WHEN 'LOW' THEN 4
                    ELSE 5
                END ASC, ns.smoothed_score DESC'''
        elif sort_param == 'trend':
            # Sort by trend descending (UP > FLAT > DOWN numerically)
            order_clause = 'ORDER BY COALESCE(ns.stability_trend, 0) DESC'
        else:
            # Default: sort by smoothed_score descending
            order_clause = 'ORDER BY COALESCE(ns.smoothed_score, ns.score) DESC'

        # Build WHERE clause for band filtering
        where_clause = ''
        where_params = []
        if band_filter and band_filter in ('CRITICAL', 'ELEVATED', 'MODERATE', 'LOW'):
            where_clause = 'WHERE COALESCE(ns.risk_band, \'LOW\') = ?'
            where_params.append(band_filter)

        query = f'''
            SELECT
              nr.network_name,
              ns.score,
              ns.smoothed_score,
              ns.stability_coeff,
              ns.stability_trend,
              ns.trend_direction,
              ns.risk_band,
              nr.network_type,
              nr.stability_state,
              nr.build_version
            FROM network_scores ns
            JOIN networks_release nr ON nr.network_name = ns.network_name
            {where_clause}
            {order_clause}
            LIMIT 50
        '''
        cursor.execute(query, where_params)
        high_risk_networks = cursor.fetchall()

        # Section C: Biggest Movers
        cursor.execute('''
            SELECT h.network_name, p.score AS prev_score, h.score AS curr_score, (h.score - p.score) AS delta
            FROM network_score_history h
            JOIN network_score_history p ON p.network_name = h.network_name
              AND p.build_version = h.build_version - 1
            WHERE h.build_version = (SELECT MAX(build_version) FROM network_score_history)
            ORDER BY delta DESC
            LIMIT 50
        ''')
        biggest_movers = cursor.fetchall()

        conn.close()

        return render_template('network_monitoring.html',
                             latest_alerts=latest_alerts,
                             high_risk_networks=high_risk_networks,
                             biggest_movers=biggest_movers,
                             severity_filter=severity,
                             alert_type_filter=alert_type,
                             q_filter=q,
                             show_filter=show,
                             sort_param=sort_param,
                             band_filter=band_filter,
                             active_page='networks')

    except Exception as e:
        print(f"[ERROR] network_monitoring: {e}", flush=True)
        conn.close()
        return render_template_string('''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Network Monitoring</title>
            <style>
                body { font-family: system-ui, -apple-system, sans-serif; margin: 20px; }
                .error { background: #fee2e2; border: 1px solid #ef4444; border-radius: 4px; padding: 15px; color: #991b1b; }
            </style>
        </head>
        <body>
            <h1>🔍 Network Monitoring</h1>
            <div class="error">
                <strong>❌ Error loading monitoring data</strong><br>
                Please check the application logs.
            </div>
        </body>
        </html>
        '''), 500


@app.route('/network-monitoring/alerts.csv')
def network_monitoring_csv():
    """
    Phase 4E: Export alerts as CSV

    Uses same filters as /network-monitoring (severity, alert_type, q).
    Returns CSV with headers: created_at, severity, alert_type, network_name, message
    """
    try:
        conn, cursor = get_db_conn()

        # Check if monitoring table exists
        cursor.execute("""
            SELECT name FROM sqlite_master WHERE type='table' AND name = 'network_alerts'
        """)
        if not cursor.fetchone():
            conn.close()
            return "Monitoring data not available yet. Please run the build.", 503

        # Get filters from query params
        severity = request.args.get('severity', '').strip()
        alert_type = request.args.get('alert_type', '').strip()
        q = request.args.get('q', '').strip()

        # Fetch filtered alerts (up to 1000 rows)
        cursor.execute('''
            SELECT created_at, severity, alert_type, network_name, message
            FROM network_alerts
            WHERE (? IS NULL OR severity = ?)
              AND (? IS NULL OR alert_type = ?)
              AND (? IS NULL OR network_name LIKE ?)
            ORDER BY created_at DESC
            LIMIT 1000
        ''', (
            severity if severity else None, severity,
            alert_type if alert_type else None, alert_type,
            q if q else None, f"%{q}%" if q else None
        ))
        alerts = cursor.fetchall()
        conn.close()

        # Generate CSV
        import csv
        from io import StringIO
        output = StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(['created_at', 'severity', 'alert_type', 'network_name', 'message'])

        # Write data rows
        for alert in alerts:
            writer.writerow([
                alert['created_at'],
                alert['severity'],
                alert['alert_type'],
                alert['network_name'],
                alert['message']
            ])

        # Return as CSV attachment
        csv_data = output.getvalue()
        response = Response(csv_data, mimetype='text/csv')
        response.headers['Content-Disposition'] = 'attachment; filename=network_alerts.csv'
        return response

    except Exception as e:
        print(f"[ERROR] network_monitoring_csv: {e}", flush=True)
        return f"Error generating CSV: {str(e)}", 500


# =========================================================================
# Phase 8B: Alert Operator Endpoints
# =========================================================================

@app.route('/api/alerts/<int:alert_id>/ack', methods=['POST'])
def ack_alert(alert_id):
    """
    Acknowledge an alert.

    Request body (optional):
    {
        "acknowledged_by": "operator_name"  (default: "local")
    }

    Returns: {acknowledged: 1, acknowledged_at: timestamp}
    """
    try:
        conn, cursor = get_db_conn()

        data = request.get_json() or {}
        acknowledged_by = data.get('acknowledged_by', 'local').strip()

        cursor.execute('''
            UPDATE network_alerts
            SET acknowledged = 1,
                acknowledged_at = CURRENT_TIMESTAMP,
                acknowledged_by = ?
            WHERE alert_id = ?
        ''', (acknowledged_by, alert_id))

        conn.commit()

        # Return updated alert state
        cursor.execute('SELECT acknowledged, acknowledged_at FROM network_alerts WHERE alert_id = ?', (alert_id,))
        result = cursor.fetchone()
        conn.close()

        if result:
            return {
                'success': True,
                'alert_id': alert_id,
                'acknowledged': result[0],
                'acknowledged_at': result[1]
            }
        else:
            return {'success': False, 'error': 'Alert not found'}, 404

    except Exception as e:
        print(f"[ERROR] ack_alert: {e}", flush=True)
        return {'success': False, 'error': str(e)}, 500


@app.route('/api/alerts/<int:alert_id>/unack', methods=['POST'])
def unack_alert(alert_id):
    """
    Unacknowledge an alert.

    Returns: {acknowledged: 0}
    """
    try:
        conn, cursor = get_db_conn()

        cursor.execute('''
            UPDATE network_alerts
            SET acknowledged = 0,
                acknowledged_at = NULL,
                acknowledged_by = NULL
            WHERE alert_id = ?
        ''', (alert_id,))

        conn.commit()
        conn.close()

        return {
            'success': True,
            'alert_id': alert_id,
            'acknowledged': 0
        }

    except Exception as e:
        print(f"[ERROR] unack_alert: {e}", flush=True)
        return {'success': False, 'error': str(e)}, 500


@app.route('/api/alerts/<int:alert_id>/suppress', methods=['POST'])
def suppress_alert(alert_id):
    """
    Suppress an alert until a specified time.

    Request body:
    {
        "suppressed_until": "2026-02-27T12:00:00",  (ISO 8601 timestamp)
        "suppression_reason": "False positive",
        "suppressed_by": "operator_name"  (optional, default: "local")
    }

    Returns: {suppressed_until: timestamp}
    """
    try:
        conn, cursor = get_db_conn()

        data = request.get_json() or {}
        suppressed_until = data.get('suppressed_until')
        suppression_reason = data.get('suppression_reason', 'User suppression').strip()
        suppressed_by = data.get('suppressed_by', 'local').strip()

        if not suppressed_until:
            return {'success': False, 'error': 'suppressed_until is required'}, 400

        cursor.execute('''
            UPDATE network_alerts
            SET suppressed_until = ?,
                suppressed_at = CURRENT_TIMESTAMP,
                suppressed_by = ?,
                suppression_reason = ?
            WHERE alert_id = ?
        ''', (suppressed_until, suppressed_by, suppression_reason, alert_id))

        conn.commit()

        cursor.execute('SELECT suppressed_until, suppressed_at FROM network_alerts WHERE alert_id = ?', (alert_id,))
        result = cursor.fetchone()
        conn.close()

        if result:
            return {
                'success': True,
                'alert_id': alert_id,
                'suppressed_until': result[0],
                'suppressed_at': result[1]
            }
        else:
            return {'success': False, 'error': 'Alert not found'}, 404

    except Exception as e:
        print(f"[ERROR] suppress_alert: {e}", flush=True)
        return {'success': False, 'error': str(e)}, 500


@app.route('/api/alerts/<int:alert_id>/unsuppress', methods=['POST'])
def unsuppress_alert(alert_id):
    """
    Remove suppression from an alert.

    Returns: {suppressed_until: null}
    """
    try:
        conn, cursor = get_db_conn()

        cursor.execute('''
            UPDATE network_alerts
            SET suppressed_until = NULL,
                suppressed_at = NULL,
                suppressed_by = NULL,
                suppression_reason = NULL
            WHERE alert_id = ?
        ''', (alert_id,))

        conn.commit()
        conn.close()

        return {
            'success': True,
            'alert_id': alert_id,
            'suppressed_until': None
        }

    except Exception as e:
        print(f"[ERROR] unsuppress_alert: {e}", flush=True)
        return {'success': False, 'error': str(e)}, 500



@app.route('/rpc-savings-dashboard')
def rpc_savings_dashboard():
    """
    RPC Savings Dashboard - Visualizes real savings from optimization.
    Displays KPI cards, trends, and efficiency metrics.
    """
    return render_template('rpc_savings_dashboard.html', active_page='rpc')


@app.route('/api/rpc-savings/reset', methods=['POST'])
def api_rpc_savings_reset():
    """
    Reset RPC savings metrics in the database.
    Clears cache_action and credits_saved columns for all records.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Reset cache_action and credits_saved columns
        cursor.execute('''
            UPDATE rpc_metrics
            SET cache_action = 'none', credits_saved = 0
        ''')

        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        return jsonify({
            'status': 'success',
            'message': f'Reset {rows_affected} RPC metrics records',
            'rows_affected': rows_affected
        }), 200
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/metrics/rpc')
def metrics_rpc_proxy():
    """Proxy /metrics/rpc requests to the RPC Metrics API"""
    try:
        import requests
        response = requests.get('http://localhost:8001/metrics/rpc', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/rpc/summary')
def metrics_rpc_summary_proxy():
    """Proxy /metrics/rpc/summary requests to the RPC Metrics API"""
    try:
        import requests
        from flask import make_response
        response = requests.get('http://localhost:8001/metrics/rpc/summary', timeout=5)
        flask_response = make_response(response.json())
        flask_response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        flask_response.headers["Pragma"] = "no-cache"
        flask_response.headers["Expires"] = "0"
        return flask_response, response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/rpc/sections')
def metrics_rpc_sections_proxy():
    """Proxy /metrics/rpc/sections requests to the RPC Metrics API"""
    try:
        import requests
        response = requests.get('http://localhost:8001/metrics/rpc/sections', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/rpc/methods')
def metrics_rpc_methods_proxy():
    """Proxy /metrics/rpc/methods requests to the RPC Metrics API"""
    try:
        import requests
        from flask import request
        limit = request.args.get('limit', '10')
        response = requests.get(f'http://localhost:8001/metrics/rpc/methods?limit={limit}', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/rpc/alerts')
def metrics_rpc_alerts_proxy():
    """Proxy /metrics/rpc/alerts requests to the RPC Metrics API"""
    try:
        import requests
        from flask import request
        burn_rate_threshold = request.args.get('burn_rate_threshold', '100.0')
        response = requests.get(f'http://localhost:8001/metrics/rpc/alerts?burn_rate_threshold={burn_rate_threshold}', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/rpc/optimizations')
def metrics_rpc_optimizations_proxy():
    """Proxy /metrics/rpc/optimizations requests to the RPC Metrics API"""
    try:
        import requests
        from flask import request
        hours = request.args.get('hours', '24')
        response = requests.get(f'http://localhost:8001/metrics/rpc/optimizations?hours={hours}', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/rpc/component-breakdown')
def metrics_rpc_component_breakdown_proxy():
    """Proxy /metrics/rpc/component-breakdown requests to the RPC Metrics API"""
    try:
        import requests
        from flask import request
        hours = request.args.get('hours', '24')
        response = requests.get(f'http://localhost:8001/metrics/rpc/component-breakdown?hours={hours}', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/rpc/source-files')
def metrics_rpc_source_files_proxy():
    """Proxy /metrics/rpc/source-files requests to the RPC Metrics API"""
    try:
        import requests
        response = requests.get('http://localhost:8001/metrics/rpc/source-files', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/rpc/reset', methods=['POST'])
def metrics_rpc_reset_proxy():
    """Reset RPC metrics: proxy to FastAPI metrics API"""
    try:
        import requests
        response = requests.post('http://localhost:8001/metrics/rpc/reset', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {
            'success': False,
            'status': 'error',
            'message': str(e)
        }, 503


@app.route('/metrics/rpc/reset-comparison-baseline', methods=['POST'])
def metrics_rpc_reset_comparison_proxy():
    """Proxy comparison baseline reset to RPC Metrics API"""
    try:
        import requests
        response = requests.post('http://localhost:8001/metrics/rpc/reset-comparison-baseline', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/api/rpc-metrics/reset', methods=['POST'])
def api_rpc_metrics_reset():
    """Reset RPC metrics: clear database and reset Helius baseline"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=60)
        cursor = conn.cursor()

        # Get current Helius usage to set as new baseline
        current_helius = 0
        try:
            from src.monitoring.helius_cli_monitor import get_helius_usage_cli, record_usage_snapshot
            usage = get_helius_usage_cli()
            if usage:
                current_helius = usage.get('credits_used_month', 0) or usage.get('credits_used', 0)
                # Record this snapshot IMMEDIATELY (before any more RPC calls)
                record_usage_snapshot(usage)

                # Clear all RPC metrics BEFORE updating baseline (so no gap)
                cursor.execute("DELETE FROM rpc_metrics")
                print(f"[RESET] Cleared all RPC metrics", flush=True)

                # Now update reset baseline to match the snapshot we just recorded
                cursor.execute(
                    "UPDATE listener_settings SET setting_value = ? WHERE setting_key = 'helius_credits_at_reset'",
                    (str(current_helius),)
                )
                print(f"[RESET] Updated baseline to {current_helius}", flush=True)
        except Exception as e:
            print(f"[RESET] Could not get fresh Helius usage: {e}", flush=True)
            # Still clear metrics even if Helius fetch fails
            cursor.execute("DELETE FROM rpc_metrics")
            print(f"[RESET] Cleared all RPC metrics (Helius unavailable)", flush=True)

        # Update reset timestamp
        cursor.execute(
            "UPDATE listener_settings SET setting_value = ? WHERE setting_key = 'last_metrics_reset_at'",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)
        )

        conn.commit()
        conn.close()

        return {'status': 'success', 'message': 'Metrics reset successfully'}, 200
    except Exception as e:
        print(f"[RESET] Error: {e}", flush=True)
        return {'status': 'error', 'message': str(e)}, 500


@app.route('/metrics/rpc/database')
def metrics_rpc_database_proxy():
    """Proxy /metrics/rpc/database requests to the RPC Metrics API"""
    try:
        import requests
        from flask import request
        since_hours = request.args.get('since_hours', '24')
        response = requests.get(f'http://localhost:8001/metrics/rpc/database?since_hours={since_hours}', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/helius/capture', methods=['POST'])
def metrics_helius_capture_proxy():
    """Proxy /metrics/helius/capture requests to the RPC Metrics API"""
    try:
        import requests
        response = requests.post('http://localhost:8001/metrics/helius/capture', timeout=10)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/helius')
def metrics_helius_proxy():
    """Proxy /metrics/helius requests to the RPC Metrics API"""
    try:
        import requests
        response = requests.get('http://localhost:8001/metrics/helius', timeout=5)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/metrics/rpc/comparison', methods=['POST'])
def metrics_rpc_comparison_proxy():
    """Proxy /metrics/rpc/comparison requests to the RPC Metrics API"""
    try:
        import requests
        from flask import request

        # Support both starting new tests and polling existing tests
        test_id = request.args.get('test_id')
        duration_seconds = request.args.get('duration_seconds', '60')

        # Build URL with optional test_id parameter
        url = f'http://localhost:8001/metrics/rpc/comparison?duration_seconds={duration_seconds}'
        if test_id:
            url += f'&test_id={test_id}'

        response = requests.post(url, timeout=180)
        return response.json(), response.status_code
    except Exception as e:
        return {'error': str(e)}, 503


@app.route('/restart', methods=['POST'])
def restart_services():
    """Kill and restart Flask, listener, and all services"""
    try:
        # Clear launch price log on restart for a fresh start
        import src.core.launch_price_logger as _lpl
        try:
            open(_lpl._LOG_PATH, 'w').close()
            _lpl._first_price_logged.clear()
        except Exception:
            pass

        from src.core.pumpfun_curve_listener import cleanup_and_restart
        # Run in background so response can be sent
        import threading
        thread = threading.Thread(target=cleanup_and_restart, daemon=True)
        thread.start()
        return {
            "status": "restarting",
            "message": "Services are being cleaned up and restarted",
            "details": "Flask (5002) and listener will restart momentarily"
        }, 202
    except Exception as e:
        return {'error': str(e), 'status': 'failed'}, 500


# =========================================================================
# HELIUS WEBHOOK (Real-time transaction updates)
# =========================================================================

# Webhook system uses webhook_integration.py (see setup_webhook_routes(app) call at app init)


@app.route('/api/creator-queue-status')
def api_creator_queue_status():
    """
    Get creator queue monitoring metrics.

    Returns:
    - total_in_queue: Total creators in work_queue
    - critical_count: Creators with priority >= 80
    - currently_processing: Creators currently locked
    - never_checked: Creators with attempts = 0
    - top_creators: Top 10 highest priority creators
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # Get queue overview stats
        now = int(__import__('time').time())

        cur.execute("SELECT COUNT(*) FROM work_queue")
        total_in_queue = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM work_queue WHERE priority >= 80")
        critical_count = cur.fetchone()[0]

        cur.execute(f"SELECT COUNT(*) FROM work_queue WHERE locked_until > {now}")
        currently_processing = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM work_queue WHERE attempts = 0")
        never_checked = cur.fetchone()[0]

        # Get top 20 priority creators
        cur.execute("""
            SELECT address, ROUND(priority, 1),
                   locked_until, next_run_at,
                   attempts, reason
            FROM work_queue
            ORDER BY priority DESC
            LIMIT 20
        """)

        top_creators = []
        for row in cur.fetchall():
            address = row[0]
            priority = row[1]
            locked_until = row[2]
            next_run_at = row[3]
            attempts = row[4]
            reason = row[5]

            # Determine status
            status = 'WAITING'
            if locked_until > now:
                status = 'PROCESSING'
            elif next_run_at <= now:
                status = 'READY'

            # Get activity stats for this creator
            cur.execute("""
                SELECT
                    COUNT(DISTINCT CASE WHEN source = ? THEN signature END) as outbound,
                    COUNT(DISTINCT CASE WHEN destination = ? THEN signature END) as inbound
                FROM sol_transfers
            """, (address, address))

            activity_row = cur.fetchone()
            outbound = activity_row[0] if activity_row[0] else 0
            inbound = activity_row[1] if activity_row[1] else 0
            total_activity = outbound + inbound

            # Look up label from multiple sources (priority order)
            label = None

            # 1. Check custom labels first
            cur.execute("SELECT label_name FROM address_labels WHERE address = ? LIMIT 1", (address,))
            row = cur.fetchone()
            if row:
                label = row[0]

            # 2. Check CEX wallets
            if not label:
                cur.execute("SELECT exchange_name FROM cex_wallets WHERE cex_address = ? LIMIT 1", (address,))
                row = cur.fetchone()
                if row:
                    label = f"{row[0]} (CEX)"

            # 3. Check INFRA funders
            if not label:
                cur.execute("SELECT funder_address FROM infra_funders_observed WHERE funder_address = ? LIMIT 1", (address,))
                if cur.fetchone():
                    label = "INFRA"

            top_creators.append({
                "address": address,
                "label": label,
                "display_name": label or address,
                "priority": priority,
                "status": status,
                "attempts": attempts,
                "reason": reason,
                "locked_until": locked_until,
                "next_run_at": next_run_at,
                "outbound_tx": outbound,
                "inbound_tx": inbound,
                "total_activity": total_activity
            })

        conn.close()

        return {
            "ok": True,
            "total_in_queue": total_in_queue,
            "critical_count": critical_count,
            "currently_processing": currently_processing,
            "never_checked": never_checked,
            "top_creators": top_creators
        }

    except Exception as e:
        try:
            conn.close()
        except:
            pass
        print(f"[QUEUE_STATUS] Error: {e}", flush=True)
        return {"ok": False, "error": str(e)}, 500

@app.route('/webhook-monitor')
def webhook_monitor():
    """
    Webhook monitoring dashboard page.
    Shows real-time metrics about webhook activity.
    """
    return render_template("webhook_monitor.html", active_page="webhook")


@app.route('/webhook-metrics')
def webhook_metrics_proxy():
    """
    Proxy endpoint that fetches webhook metrics from the RPC metrics API.
    This allows the dashboard HTML to fetch from the same port.
    """
    import requests
    try:
        # Forward request to RPC metrics API on port 8001
        response = requests.get('http://localhost:8001/webhook-metrics', timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[WEBHOOK_METRICS] Proxy error: {e}", flush=True)
        # Return empty metrics on error
        return {
            "metrics_5m": {'event_count': 0, 'unique_creators': 0, 'unique_signatures': 0, 'credits_used': 0},
            "metrics_60m": {'event_count': 0, 'unique_creators': 0, 'unique_signatures': 0, 'credits_used': 0},
            "top_creators": [],
            "helius_billing": {'webhook_credits': 0, 'total_credits': 0, 'cost_per_event': 0, 'total_webhook_events': 0},
            "burn_rate": {'events_per_minute': 0, 'credits_per_hour': 0, 'credits_per_day': 0}
        }


@app.route('/api/webhook/status')
def api_webhook_status():
    """
    Get webhook monitor status data.
    Returns metrics about webhooks received, transfers processed, etc.
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get total signatures (webhooks received)
        cursor.execute("SELECT COUNT(DISTINCT signature) as total FROM funder_incoming_transfers")
        total_signatures = cursor.fetchone()['total'] or 0

        # Get total transfers
        cursor.execute("SELECT COUNT(*) as total FROM funder_incoming_transfers")
        total_transfers = cursor.fetchone()['total'] or 0

        # Get transfers from last 24 hours
        cursor.execute("""
            SELECT COUNT(*) as total FROM funder_incoming_transfers
            WHERE timestamp >= datetime('now', '-1 day')
        """)
        transfers_today = cursor.fetchone()['total'] or 0

        # Get last webhook timestamp
        cursor.execute("SELECT MAX(timestamp) as last_ts FROM funder_incoming_transfers")
        last_webhook_row = cursor.fetchone()
        last_webhook = last_webhook_row['last_ts'] if last_webhook_row['last_ts'] else None

        # Get recent transfers (last 20)
        cursor.execute("""
            SELECT
                sender_address,
                funder_address as receiver,
                amount_sol as amount,
                timestamp as time
            FROM funder_incoming_transfers
            ORDER BY timestamp DESC
            LIMIT 20
        """)
        recent_transfers = [
            {
                'sender': row['sender_address'][:16] + '...' if len(row['sender_address']) > 16 else row['sender_address'],
                'sender_full': row['sender_address'],
                'receiver': row['receiver'][:16] + '...' if len(row['receiver']) > 16 else row['receiver'],
                'receiver_full': row['receiver'],
                'amount': f"{row['amount']:.2f}" if row['amount'] else "0",
                'time': row['time'],
                'sender_is_creator': False,
                'sender_has_label': False,
            }
            for row in cursor.fetchall()
        ]

        conn.close()

        return jsonify({
            'ok': True,
            'total_signatures': total_signatures,
            'total_transfers': total_transfers,
            'transfers_today': transfers_today,
            'last_webhook': last_webhook,
            'recent_transfers': recent_transfers
        })

    except Exception as e:
        print(f"[WEBHOOK_STATUS] Error: {e}", flush=True)
        return jsonify({'ok': False, 'error': str(e)}), 500


# =========================================================================
# TASK B: TX CACHE & TASK A: FUNDER WEBHOOKS
# =========================================================================

@app.route('/api/listener/tx-cache-stats')
def listener_tx_cache_stats():
    """
    Expose listener transaction cache statistics to UI.
    Returns cache hit/miss/wait stats and estimated credits saved.
    """
    try:
        global listener
        if 'listener' in globals() and listener:
            stats = listener.get_tx_cache_stats()
            return jsonify(stats)

        return jsonify({
            "tx_cache_hit": 0,
            "tx_cache_miss": 0,
            "tx_cache_wait": 0,
            "tx_cache_size": 0,
            "tx_cache_hit_rate_pct": 0.0,
            "rpc_calls_avoided": 0,
            "credits_saved": 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/webhook/funder', methods=['POST'])
def webhook_funder_event():
    """
    Receive funder webhook events from Helius.
    Dedupes by (signature, funder_address) and stores in database.
    """
    try:
        payload = request.get_json()

        if not payload:
            return jsonify({"error": "empty payload"}), 400

        signature = payload.get("signature")
        slot = payload.get("slot")
        block_time = payload.get("blockTime")
        source = payload.get("source")
        destination = payload.get("destination")
        mint = payload.get("mint")

        if not signature or not source or not destination:
            return jsonify({"error": "missing required fields"}), 400

        direction = None
        counterparty = None
        amount_sol = 0

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Check if source is a watched funder (transfer OUT)
        cursor.execute("""
            SELECT 1 FROM funder_watchlist
            WHERE funder_address = ? AND is_active = 1
        """, (source,))

        if cursor.fetchone():
            direction = "OUT"
            counterparty = destination
            native_transfers = payload.get("nativeTransfers", [])
            if native_transfers:
                amount_sol = sum(t.get("amount", 0) for t in native_transfers) / 1e9

        # Check if destination is a watched funder (transfer IN)
        cursor.execute("""
            SELECT 1 FROM funder_watchlist
            WHERE funder_address = ? AND is_active = 1
        """, (destination,))

        if cursor.fetchone():
            direction = "IN"
            counterparty = source
            native_transfers = payload.get("nativeTransfers", [])
            if native_transfers:
                amount_sol = sum(t.get("amount", 0) for t in native_transfers) / 1e9

        if not direction:
            conn.close()
            return jsonify({"status": "ok"}), 200

        # Insert event (dedupe by UNIQUE constraint)
        funder = source if direction == "OUT" else destination
        try:
            cursor.execute("""
                INSERT INTO funder_webhook_events
                (funder_address, signature, slot, block_time, direction, counterparty, amount_sol, mint, raw_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (funder, signature, slot, block_time, direction, counterparty, amount_sol, mint, json.dumps(payload)))
            conn.commit()
            print(f"[WEBHOOK_FUNDER] ✅ {direction}: {funder[:8]}... <-> {counterparty[:8]}... ({amount_sol:.4f} SOL)", flush=True)
        except sqlite3.IntegrityError:
            pass  # Duplicate event
        finally:
            conn.close()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[WEBHOOK_FUNDER] ⚠ Error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/funder-watchlist/summary')
def funder_watchlist_summary():
    """Get summary of funder watchlist by risk tier."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        summary = {}
        for tier in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            cursor.execute("""
                SELECT COUNT(*) as count, COALESCE(SUM(risk_score), 0) as total_risk
                FROM funder_watchlist
                WHERE webhook_group_id = ? AND is_active = 1
            """, (tier,))
            row = cursor.fetchone()
            summary[tier] = {
                "count": row[0] if row else 0,
                "total_risk_score": row[1] if row else 0,
            }

        conn.close()
        return jsonify(summary)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/funder-watchlist/top-risky')
def funder_watchlist_top_risky():
    """Get top 20 most risky funders."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT funder_address, risk_score, webhook_group_id, risk_reasons
            FROM funder_watchlist
            WHERE is_active = 1
            ORDER BY risk_score DESC
            LIMIT 20
        """)

        rows = cursor.fetchall()
        result = []
        for row in rows:
            risk_reasons = json.loads(row[3]) if row[3] else []
            result.append({
                "funder_address": row[0],
                "risk_score": row[1],
                "risk_tier": row[2],
                "risk_reasons": risk_reasons,
            })

        conn.close()
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/funder-webhook-events')
def funder_webhook_events():
    """Get recent funder webhook events (paginated)."""
    try:
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, funder_address, signature, block_time, direction,
                   counterparty, amount_sol, mint, ingested_at
            FROM funder_webhook_events
            ORDER BY ingested_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))

        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "funder_address": row[1],
                "signature": row[2],
                "block_time": row[3],
                "direction": row[4],
                "counterparty": row[5],
                "amount_sol": row[6],
                "mint": row[7],
                "ingested_at": row[8],
            })

        conn.close()
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =========================================================================
# PHASE 3.2 — STORAGE MONITORING ENDPOINTS
# =========================================================================

@app.route('/api/storage/metrics')
def api_storage_metrics():
    """Get current storage metrics for transfer_index table."""
    try:
        from src.core.storage_monitoring import StorageMonitor

        monitor = StorageMonitor(DB_PATH)
        metrics = monitor.collect_metrics()

        return jsonify({
            'db_size_mb': metrics.db_size_mb,
            'db_size_gb': metrics.db_size_mb / 1024,
            'wal_size_mb': metrics.wal_size_mb,
            'row_count': metrics.row_count,
            'daily_growth_mb': metrics.daily_growth_mb,
            'days_to_capacity': metrics.days_to_capacity if metrics.days_to_capacity != float('inf') else None,
            'last_cleanup_ago_hours': metrics.last_cleanup_ago_hours if metrics.last_cleanup_ago_hours != float('inf') else None,
            'last_cleanup_freed_mb': metrics.last_cleanup_freed_mb,
            'timestamp': int(time.time())
        })
    except Exception as e:
        print(f"[STORAGE_API] Error: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/storage/cleanup-history')
def api_storage_cleanup_history():
    """Get recent cleanup operations."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT cleanup_timestamp, status, rows_actually_deleted, freed_mb,
                   cleanup_duration_ms, db_size_before, db_size_after
            FROM cleanup_log
            ORDER BY cleanup_timestamp DESC
            LIMIT 30
        """)

        rows = cursor.fetchall()
        result = []

        for row in rows:
            result.append({
                'timestamp': int(row[0]),
                'status': row[1],
                'rows_deleted': row[2],
                'freed_mb': row[3],
                'duration_ms': row[4],
                'db_size_before_gb': row[5] / (1024**3) if row[5] else 0,
                'db_size_after_gb': row[6] / (1024**3) if row[6] else 0
            })

        conn.close()
        return jsonify(result)

    except Exception as e:
        print(f"[STORAGE_HISTORY] Error: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/storage/alerts')
def api_storage_alerts():
    """Check storage against alert thresholds."""
    try:
        from src.core.storage_monitoring import StorageMonitor, QueryLatencyMonitor, StorageAlerts

        monitor = StorageMonitor(DB_PATH)
        metrics = monitor.collect_metrics()

        latency_monitor = QueryLatencyMonitor(DB_PATH)
        query_latencies = latency_monitor.measure_key_queries()

        # Get last cleanup result
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT rows_actually_deleted, cleanup_duration_ms, freed_mb
            FROM cleanup_log
            WHERE status = 'success'
            ORDER BY cleanup_timestamp DESC
            LIMIT 1
        """)

        last_cleanup = cursor.fetchone()
        cleanup_result = {
            'deleted': last_cleanup[0] if last_cleanup else 0,
            'duration_ms': last_cleanup[1] if last_cleanup else 0,
            'freed_mb': last_cleanup[2] if last_cleanup else 0
        }
        conn.close()

        # Check alerts
        alerts = StorageAlerts.check_alerts(metrics, cleanup_result, query_latencies)

        return jsonify({
            'has_alerts': len(alerts) > 0,
            'alert_count': len(alerts),
            'alerts': alerts,
            'thresholds': StorageAlerts.THRESHOLDS
        })

    except Exception as e:
        print(f"[STORAGE_ALERTS] Error: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


# =========================================================================
# PHASE 3.3 — CLUSTER DETECTION ENDPOINTS
# =========================================================================

@app.route('/api/clusters/farms')
def api_clusters_farms():
    """List dev farm wallets sorted by confidence score."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT cluster_id, funder_wallet, creator_count, confidence_score,
                   avg_transfer_sol, days_active, has_burst, wallet_age_days,
                   detected_at
            FROM wallet_clusters
            ORDER BY confidence_score DESC
            LIMIT 100
        """)

        rows = cursor.fetchall()
        result = []

        for row in rows:
            cluster_id, funder_wallet, creator_count, confidence_score, avg_transfer_sol, days_active, has_burst, wallet_age_days, detected_at = row
            
            # Get creator list
            cursor.execute(
                "SELECT creator_addresses FROM wallet_clusters WHERE cluster_id = ?",
                (cluster_id,)
            )
            creator_row = cursor.fetchone()
            creators = []
            if creator_row:
                try:
                    creators = json.loads(creator_row[0])
                except:
                    creators = []

            result.append({
                'cluster_id': cluster_id,
                'funder_wallet': funder_wallet,
                'creator_count': creator_count,
                'creators': creators,
                'confidence_score': confidence_score,
                'avg_transfer_sol': avg_transfer_sol,
                'days_active': days_active,
                'has_burst': bool(has_burst),
                'wallet_age_days': wallet_age_days,
                'detected_at': int(detected_at)
            })

        conn.close()
        return jsonify(result)

    except Exception as e:
        print(f"[CLUSTERS_FARMS] Error: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/clusters/reputation/<wallet>')
def api_clusters_reputation(wallet):
    """Get developer reputation for a specific wallet."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT wallet, tokens_launched, tokens_rugged, tokens_above_2x,
                   tokens_above_10x, rug_rate, success_rate, reputation_score,
                   wallet_age_days, cluster_id, last_updated
            FROM dev_reputation
            WHERE wallet = ?
        """, (wallet,))

        row = cursor.fetchone()

        if not row:
            conn.close()
            return jsonify({"error": "Wallet not found"}), 404

        result = {
            'wallet': row[0],
            'tokens_launched': row[1],
            'tokens_rugged': row[2],
            'tokens_above_2x': row[3],
            'tokens_above_10x': row[4],
            'rug_rate': row[5],
            'success_rate': row[6],
            'reputation_score': row[7],
            'wallet_age_days': row[8],
            'cluster_id': row[9],
            'last_updated': int(row[10]),
            'risk_level': _classify_risk(row[7])  # reputation_score
        }

        conn.close()
        return jsonify(result)

    except Exception as e:
        print(f"[REPUTATION] Error: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


@app.route('/api/clusters/high-risk')
def api_clusters_high_risk():
    """Get all creators in high-confidence dev farms with reputation warnings."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # High-confidence farms (>75)
        cursor.execute("""
            SELECT DISTINCT json_each.value as creator, cluster_id, confidence_score
            FROM wallet_clusters,
            json_each(wallet_clusters.creator_addresses)
            WHERE confidence_score > 75
            ORDER BY confidence_score DESC
        """)

        farm_creators = cursor.fetchall()
        result = []

        for creator, cluster_id, farm_confidence in farm_creators:
            # Get reputation for creator
            cursor.execute("""
                SELECT reputation_score, rug_rate, wallet_age_days
                FROM dev_reputation
                WHERE wallet = ?
            """, (creator,))

            rep_row = cursor.fetchone()

            if rep_row:
                reputation_score, rug_rate, wallet_age_days = rep_row
            else:
                reputation_score, rug_rate, wallet_age_days = 50, 0, 0

            result.append({
                'creator': creator,
                'farm_cluster_id': cluster_id,
                'farm_confidence': farm_confidence,
                'reputation_score': reputation_score,
                'rug_rate': rug_rate,
                'wallet_age_days': wallet_age_days,
                'risk_level': _classify_risk(reputation_score),
                'warning': 'High-risk developer in high-confidence farm'
            })

        conn.close()
        return jsonify(result)

    except Exception as e:
        print(f"[HIGH_RISK] Error: {e}", flush=True)
        return jsonify({"error": str(e)}), 500


def _classify_risk(reputation_score: float) -> str:
    """Classify risk level from reputation score."""
    if reputation_score < 30:
        return 'HIGH_RISK'
    elif reputation_score < 60:
        return 'MEDIUM_RISK'
    else:
        return 'LOW_RISK'


# =========================================================================
# PHASE 3.3+ LAUNCH PREDICTION API (Launch watchlist, creator reuse, pumpfun)
# =========================================================================
try:
    from src.core.launch_prediction_api import register_launch_api
    register_launch_api(app, db_path=DB_PATH)
    print("[LAUNCH_PREDICTION] Phase 3.3+ launch prediction API routes registered successfully")
except ImportError as e:
    print(f"[WARNING] Launch prediction API not available: {e}")
except Exception as e:
    print(f"[ERROR] Failed to initialize launch prediction API: {e}")


# =========================================================================
# PHASE 4 ADVANCED FARM INTELLIGENCE API (Ecosystems, launch waves, coordination)
# =========================================================================
try:
    from src.core.advanced_farm_intelligence_api import register_farm_intelligence_api
    register_farm_intelligence_api(app, db_path=DB_PATH)
    print("[ADVANCED_FARM_INTELLIGENCE] Phase 4 advanced farm intelligence API routes registered successfully")
except ImportError as e:
    print(f"[WARNING] Advanced farm intelligence API not available: {e}")
except Exception as e:
    print(f"[ERROR] Failed to initialize advanced farm intelligence API: {e}")

# =========================================================================
# DEV INTELLIGENCE GRAPH API (Multi-layer wallet/creator/token organizations)
# =========================================================================
try:
    from src.core.dev_intelligence_api import register_dev_intelligence_api
    register_dev_intelligence_api(app, db_path=DB_PATH)
    print("[DEV_INTELLIGENCE] Dev intelligence graph API routes registered successfully")
except ImportError as e:
    print(f"[WARNING] Dev intelligence API not available: {e}")
except Exception as e:
    print(f"[ERROR] Failed to initialize dev intelligence API: {e}")

# =========================================================================
# FLEX UI API (Dashboard endpoints for intelligence signals)
# =========================================================================
try:
    from src.core.flex_ui_api import register_flex_ui_api
    register_flex_ui_api(app, db_path=DB_PATH)
    print("[FLEX_UI] FLEX UI API routes registered successfully")
except ImportError as e:
    print(f"[WARNING] FLEX UI API not available: {e}")
except Exception as e:
    print(f"[ERROR] Failed to initialize FLEX UI API: {e}")

# =========================================================================
# FLEX INTELLIGENCE DASHBOARD (Frontend UI with HTML templates)
# =========================================================================
try:
    from src.core.flex_dashboard_routes import register_dashboard_routes
    register_dashboard_routes(app)
    print("[DASHBOARD] FLEX Intelligence Dashboard routes registered successfully")
except ImportError as e:
    print(f"[WARNING] FLEX Dashboard not available: {e}")
except Exception as e:
    print(f"[ERROR] Failed to initialize FLEX Dashboard: {e}")

# Price API
try:
    from src.apis.price_api import register_price_api
    register_price_api(app)
    print("[PRICE_API] Token Price API routes registered successfully")
except ImportError as e:
    print(f"[WARNING] Price API not available: {e}")
except Exception as e:
    print(f"[ERROR] Failed to initialize Price API: {e}")

# =========================================================================
# REAL-TIME PRICE STREAMING (SSE)
# =========================================================================

@app.route('/api/token/<mint>/pools')
def get_token_pools(mint):
    """Get all pools and vault addresses for a token"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.cursor()

        # Get all pools for this mint
        cursor.execute("""
            SELECT 
                pool_address,
                base_account,
                quote_account,
                vault_validation_status,
                discovery_method,
                created_at
            FROM token_pool_accounts
            WHERE mint = ?
            ORDER BY created_at DESC
        """, (mint,))

        pools = [dict(row) for row in cursor.fetchall()]
        conn.close()

        if not pools:
            return jsonify({"pools": [], "message": "No pools found for this token"}), 200

        return jsonify({
            "mint": mint,
            "total_pools": len(pools),
            "pools": pools
        }), 200

    except Exception as e:
        logger.error(f"Error fetching pools for {mint}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/price-stream')
def price_stream():
    """
    Server-Sent Events endpoint for real-time price updates.

    Browser connects via: const es = new EventSource('/api/price-stream')

    Receives events:
    {
        "type": "price_update",
        "mint": "...",
        "price_usd": 0.00123,
        "market_cap": 1000000,
        "source": "pool",
        "updated_at": 1774286512
    }
    """
    try:
        from src.core.price_stream import get_price_stream

        price_stream_instance = get_price_stream()

        def event_generator():
            """Generate SSE events from the price stream"""
            import time
            queue = price_stream_instance.subscribe()
            logger_inst = logging.getLogger(__name__)

            try:
                print(f"[SSE_ENDPOINT] New browser client connected, subscriber count: {price_stream_instance.get_subscriber_count()}", flush=True)
                logger_inst.info(f"[PRICE_STREAM] New browser client connected, subscriber count: {price_stream_instance.get_subscriber_count()}")

                while True:
                    try:
                        # Get next event from queue with timeout (non-blocking with timeout)
                        try:
                            event = queue.get(timeout=30)  # 30 second timeout to detect dead connections
                            print(f"[SSE_SEND] Sending event for {event.get('mint', '?')[:16]}...", flush=True)
                            yield f"data: {json.dumps(event)}\n\n"
                        except:
                            # Queue timeout - send a comment to keep connection alive
                            yield f": keepalive\n\n"
                            continue

                    except GeneratorExit:
                        # Client disconnected
                        break
                    except Exception as e:
                        logger_inst.debug(f"[PRICE_STREAM] Event error: {e}")
                        break

            finally:
                price_stream_instance.unsubscribe(queue)
                print(f"[SSE_ENDPOINT] Browser client disconnected, remaining: {price_stream_instance.get_subscriber_count()}", flush=True)
                logger_inst.info(f"[PRICE_STREAM] Browser client disconnected, remaining: {price_stream_instance.get_subscriber_count()}")

        return Response(
            event_generator(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
                'Connection': 'keep-alive'
            }
        )

    except Exception as e:
        logger.error(f"[PRICE_STREAM] Error: {e}")
        return jsonify({"error": str(e)}), 500


# =========================================================================
# INTERNAL BROADCAST: cross-process SSE injection
# Called by the listener process to push events into Flask's SSE stream.
# =========================================================================

@app.route('/api/internal/broadcast', methods=['POST'])
def internal_broadcast():
    """
    Accepts a JSON event from the listener process and broadcasts it
    to all connected SSE subscribers in this Flask process.
    Only accepts connections from localhost.
    """
    import asyncio as _asyncio
    remote = request.remote_addr
    if remote not in ('127.0.0.1', '::1', 'localhost'):
        return jsonify({'error': 'forbidden'}), 403
    try:
        event = request.get_json(force=True, silent=True)
        if not event or 'type' not in event:
            return jsonify({'error': 'invalid event'}), 400
        from src.core.price_stream import get_price_stream
        ps = get_price_stream()
        # broadcast is async; run synchronously in a new event loop
        loop = _asyncio.new_event_loop()
        loop.run_until_complete(ps.broadcast(event))
        loop.close()
        return jsonify({'ok': True, 'subscribers': ps.get_subscriber_count()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =========================================================================
# WEBSOCKET: mint-filtered real-time price hub (/ws/tokens)
# =========================================================================

@sock.route('/ws/tokens')
def ws_tokens(ws):
    """
    Mint-filtered WebSocket endpoint.

    Protocol:
      c->s  {"type":"subscribe",   "mints":["abc...", ...]}
      c->s  {"type":"unsubscribe", "mints":["abc...", ...]}
      s->c  {"type":"price_update","mint":"...","price_usd":...,"market_cap":...,"source":"...","last_snapshot":...,"age_seconds":0}

    Only subscribed mints are pushed; no global broadcast.
    """
    from src.core.price_stream import get_token_hub
    hub = get_token_hub()
    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue

            msg_type = msg.get("type")
            mints = msg.get("mints", [])
            if not isinstance(mints, list):
                continue

            if msg_type == "subscribe":
                hub.subscribe(ws, mints)
            elif msg_type == "unsubscribe":
                hub.unsubscribe(ws, mints)
    finally:
        hub.remove_client(ws)


# =========================================================================
# EARLY SIGNALS API (PHASE 1 - Predictive Intelligence)
# =========================================================================

@app.route('/api/early-signals')
def api_early_signals():
    """Get all early signal predictions (likely_rug, likely_runner, unknown)"""
    try:
        from src.core.lifecycle_early_signals import EarlySignalEngine, EarlyLabel

        db_path = DB_PATH
        engine = EarlySignalEngine(db_path)

        early_rugs = engine.get_early_signals_by_label(EarlyLabel.RUG, limit=100)
        early_runners = engine.get_early_signals_by_label(EarlyLabel.RUNNER, limit=100)
        unknown_signals = engine.get_early_signals_by_label(EarlyLabel.UNKNOWN, limit=100)

        # Fetch additional data for display (age, scores)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        for group in [early_rugs, early_runners, unknown_signals]:
            for token in group:
                # Get age
                cursor.execute("""
                    SELECT
                        started_at,
                        early_score,
                        early_rug_score,
                        early_success_score
                    FROM token_monitoring_state
                    WHERE mint = ?
                """, (token['mint'],))
                row = cursor.fetchone()
                if row:
                    now = int(time.time())
                    token['age_minutes'] = (now - row['started_at']) // 60
                    token['early_score'] = row['early_score'] or 0
                    token['early_rug_score'] = row['early_rug_score'] or 0
                    token['early_success_score'] = row['early_success_score'] or 0
                    token['confidence'] = row['early_score'] or 0.5

        conn.close()

        return jsonify({
            'early_rugs': early_rugs,
            'early_runners': early_runners,
            'unknown_signals': unknown_signals,
            'total': len(early_rugs) + len(early_runners) + len(unknown_signals),
            'early_rugs_count': len(early_rugs),
            'early_runners_count': len(early_runners),
            'unknown_count': len(unknown_signals),
        })

    except Exception as e:
        logger.error(f"[EARLY_SIGNALS_API] Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/early-signals/<mint>')
def api_early_signal_detail(mint):
    """Get detailed signal information for a specific token"""
    try:
        from src.core.lifecycle_early_signals import EarlySignalEngine

        db_path = DB_PATH
        engine = EarlySignalEngine(db_path)

        # Get from database
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                mint,
                early_label,
                early_score,
                early_rug_score,
                early_success_score,
                early_warning_flags,
                started_at
            FROM token_monitoring_state
            WHERE mint = ?
        """, (mint,))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({"error": "Token not found"}), 404

        now = int(time.time())

        # Parse signals from warning flags (they're stored as CSV)
        warnings = row['early_warning_flags'].split(',') if row['early_warning_flags'] else []

        return jsonify({
            'mint': row['mint'],
            'early_label': row['early_label'],
            'early_score': row['early_score'] or 0,
            'early_rug_score': row['early_rug_score'] or 0,
            'early_success_score': row['early_success_score'] or 0,
            'confidence': max(row['early_rug_score'] or 0, row['early_success_score'] or 0) * 0.8,
            'age_minutes': (now - row['started_at']) // 60,
            'warnings': warnings,
            'recommendation': 'STOP_MONITORING' if row['early_label'] == 'likely_rug'
                            else 'PRIORITIZE' if row['early_label'] == 'likely_runner'
                            else 'CONTINUE_MONITORING',
            # These would come from the actual signal computation
            'rug_signals': [
                'no_velocity', 'negative_velocity', 'early_crash',
                'no_recovery_from_dip', 'poor_liquidity'
            ][:2],  # Show top signals
            'success_signals': [
                'strong_velocity', 'reached_50k_fast', 'stable_price',
                'good_liquidity', 'volume_increasing'
            ][:2],
        })

    except Exception as e:
        logger.error(f"[EARLY_SIGNAL_DETAIL] Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/dashboard')
def api_dashboard():
    """Get dashboard overview data including early signals stats"""
    try:
        logger.info("[DASHBOARD_API] Starting api_dashboard")
        from src.core.lifecycle_early_signals import EarlySignalEngine, EarlyLabel

        db_path = DB_PATH
        logger.info(f"[DASHBOARD_API] DB path: {db_path}")
        engine = EarlySignalEngine(db_path)

        # Get early signal counts
        logger.info("[DASHBOARD_API] Getting early signals")
        early_rugs = engine.get_early_signals_by_label(EarlyLabel.RUG, limit=1000)
        early_runners = engine.get_early_signals_by_label(EarlyLabel.RUNNER, limit=1000)

        logger.info(f"[DASHBOARD_API] Found {len(early_rugs)} rugs and {len(early_runners)} runners")

        result = {
            'critical_alerts': 5,  # Placeholder - would come from real alert system
            'high_alerts': 12,
            'organizations_monitored': 150,
            'latest_wave_detected': 'Wave-2024-03',
            'early_rugs_detected': len(early_rugs),
            'early_runners_detected': len(early_runners),
            'top_launch_candidates': [
                {
                    'operator_wallet': '3j4k9...',
                    'master_launch_score': 0.87,
                    'alert_level': 'HIGH',
                    'token_count': 12,
                    'creator_count': 8,
                    'organization_id': 1
                }
            ]
        }
        logger.info(f"[DASHBOARD_API] Returning result: {result}")
        return jsonify(result)

    except Exception as e:
        logger.error(f"[DASHBOARD_API] Error: {e}", exc_info=True)
        return jsonify({
            'error': str(e),
            'critical_alerts': 0,
            'high_alerts': 0,
            'organizations_monitored': 0,
            'latest_wave_detected': None,
            'early_rugs_detected': 0,
            'early_runners_detected': 0,
            'top_launch_candidates': []
        }), 500


@app.route('/test-prices')
def test_prices():
    """Serve the live price update test dashboard"""
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>FLEX Live Price Update Test</title>
        <style>
            body {
                font-family: 'Courier New', monospace;
                background: #0f172a;
                color: #f1f5f9;
                padding: 20px;
                margin: 0;
            }
            .container { max-width: 1200px; margin: 0 auto; }
            h1 { color: #60a5fa; margin-bottom: 30px; }
            .status-panel {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 20px;
            }
            .status-item {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 10px;
                border-bottom: 1px solid #334155;
            }
            .status-item:last-child { border-bottom: none; }
            .status-label { font-weight: bold; color: #cbd5e1; }
            .status-value {
                font-weight: bold;
                padding: 5px 10px;
                border-radius: 4px;
            }
            .status-value.connected { background: #10b981; color: white; }
            .status-value.disconnected { background: #ef4444; color: white; }
            .status-value.pending { background: #f59e0b; color: white; }
            .log-container {
                background: #0f172a;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 20px;
                height: 500px;
                overflow-y: auto;
                margin-bottom: 20px;
            }
            .log-entry {
                padding: 8px;
                border-left: 3px solid #334155;
                margin-bottom: 5px;
                font-size: 12px;
            }
            .log-entry.info { border-left-color: #60a5fa; color: #93c5fd; }
            .log-entry.success { border-left-color: #10b981; color: #86efac; }
            .log-entry.error { border-left-color: #ef4444; color: #fca5a5; }
            .log-entry.price-update {
                border-left-color: #f59e0b;
                background: rgba(245, 158, 11, 0.1);
                color: #fbbf24;
            }
            .log-timestamp { color: #94a3b8; margin-right: 10px; font-size: 10px; }
            .price-updates {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 20px;
                margin-bottom: 20px;
            }
            .price-update-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
                gap: 15px;
            }
            .price-card {
                background: #0f172a;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 15px;
                font-size: 12px;
            }
            .price-card.price-up { border-color: #10b981; background: rgba(16, 185, 129, 0.1); }
            .price-card.price-down { border-color: #ef4444; background: rgba(239, 68, 68, 0.1); }
            .price-card-mint { font-weight: bold; color: #60a5fa; margin-bottom: 8px; word-break: break-all; }
            .price-card-value { display: flex; justify-content: space-between; margin: 5px 0; }
            .price-card-label { color: #94a3b8; }
            .price-card-data { color: #f1f5f9; font-weight: bold; }
            .controls {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 20px;
                display: flex;
                gap: 10px;
            }
            button {
                background: #3b82f6;
                color: white;
                border: none;
                padding: 10px 20px;
                border-radius: 4px;
                cursor: pointer;
                font-weight: bold;
                transition: all 0.2s;
            }
            button:hover { background: #2563eb; transform: translateY(-1px); }
            .stats {
                background: #1e293b;
                border: 1px solid #334155;
                border-radius: 8px;
                padding: 20px;
                margin-top: 20px;
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 15px;
            }
            .stat-card { text-align: center; }
            .stat-label { color: #94a3b8; font-size: 12px; margin-bottom: 5px; }
            .stat-value { font-size: 24px; font-weight: bold; color: #60a5fa; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🚀 FLEX Live Price Update Test</h1>

            <div class="status-panel">
                <div class="status-item">
                    <span class="status-label">Connection Status:</span>
                    <span class="status-value disconnected" id="connectionStatus">DISCONNECTED</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Events Received:</span>
                    <span class="status-value" id="eventCount" style="background: #3b82f6;">0</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Last Event:</span>
                    <span style="color: #94a3b8;" id="lastEvent">Never</span>
                </div>
                <div class="status-item">
                    <span class="status-label">Unique Tokens:</span>
                    <span class="status-value" id="tokenCount" style="background: #3b82f6;">0</span>
                </div>
            </div>

            <div class="controls">
                <button onclick="startTest()">▶ Start Test</button>
                <button onclick="stopTest()">⏹ Stop Test</button>
                <button onclick="clearLogs()">🗑 Clear Logs</button>
                <button onclick="exportData()">💾 Export Data</button>
            </div>

            <div class="stats">
                <div class="stat-card">
                    <div class="stat-label">Price Updates</div>
                    <div class="stat-value" id="priceUpdateCount">0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Avg Event Time</div>
                    <div class="stat-value" id="avgEventTime">0ms</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Updates/Min</div>
                    <div class="stat-value" id="updateRate">0</div>
                </div>
                <div class="stat-card">
                    <div class="stat-label">Connection Time</div>
                    <div class="stat-value" id="connectionTime">--</div>
                </div>
            </div>

            <div class="price-updates">
                <h2 style="margin-top: 0; color: #60a5fa;">Latest Price Updates</h2>
                <div class="price-update-grid" id="priceGrid"></div>
            </div>

            <div style="background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 20px; margin-bottom: 20px;">
                <h2 style="margin-top: 0; color: #60a5fa;">Live Event Log</h2>
                <div class="log-container" id="logContainer"></div>
            </div>
        </div>

        <script>
            let eventSource = null;
            let eventCount = 0;
            let priceUpdates = new Map();
            let startTime = null;
            let testRunning = false;
            let eventTimes = [];

            function log(message, type = 'info') {
                const logContainer = document.getElementById('logContainer');
                const entry = document.createElement('div');
                entry.className = `log-entry ${type}`;
                const now = new Date();
                const timestamp = now.toLocaleTimeString('en-US', {
                    hour12: false,
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    fractionalSecondDigits: 3
                });
                entry.innerHTML = `<span class="log-timestamp">[${timestamp}]</span> ${message}`;
                logContainer.appendChild(entry);
                logContainer.scrollTop = logContainer.scrollHeight;
            }

            function updateStats() {
                document.getElementById('eventCount').textContent = eventCount;
                document.getElementById('tokenCount').textContent = priceUpdates.size;
                document.getElementById('priceUpdateCount').textContent = eventCount;
                if (eventTimes.length > 0) {
                    const avgTime = eventTimes.reduce((a, b) => a + b, 0) / eventTimes.length;
                    document.getElementById('avgEventTime').textContent = avgTime.toFixed(1) + 'ms';
                }
                if (startTime && eventCount > 0) {
                    const elapsedSecs = (Date.now() - startTime) / 1000;
                    const updateRate = (eventCount / elapsedSecs * 60).toFixed(1);
                    document.getElementById('updateRate').textContent = updateRate;
                    const hours = Math.floor(elapsedSecs / 3600);
                    const mins = Math.floor((elapsedSecs % 3600) / 60);
                    const secs = Math.floor(elapsedSecs % 60);
                    document.getElementById('connectionTime').textContent =
                        `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
                }
            }

            function updatePriceDisplay() {
                const priceGrid = document.getElementById('priceGrid');
                priceGrid.innerHTML = '';
                const sorted = Array.from(priceUpdates.entries())
                    .sort((a, b) => b[1].timestamp - a[1].timestamp)
                    .slice(0, 12);
                sorted.forEach(([mint, data]) => {
                    const card = document.createElement('div');
                    const isUp = data.direction === 'up';
                    card.className = `price-card ${isUp ? 'price-up' : isUp === false ? 'price-down' : ''}`;
                    card.innerHTML = `
                        <div class="price-card-mint">${mint.slice(0, 8)}...</div>
                        <div class="price-card-value">
                            <span class="price-card-label">Price:</span>
                            <span class="price-card-data">$${data.price_usd?.toFixed(8) || 'N/A'}</span>
                        </div>
                        <div class="price-card-value">
                            <span class="price-card-label">Source:</span>
                            <span class="price-card-data">${data.source || 'N/A'}</span>
                        </div>
                        <div class="price-card-value">
                            <span class="price-card-label">MCap:</span>
                            <span class="price-card-data">$${formatNumber(data.market_cap) || 'N/A'}</span>
                        </div>
                    `;
                    priceGrid.appendChild(card);
                });
            }

            function formatNumber(num) {
                if (!num) return '0';
                if (num >= 1e9) return (num / 1e9).toFixed(2) + 'B';
                if (num >= 1e6) return (num / 1e6).toFixed(2) + 'M';
                if (num >= 1e3) return (num / 1e3).toFixed(2) + 'K';
                return num.toFixed(2);
            }

            function startTest() {
                if (testRunning) {
                    log('Test already running', 'info');
                    return;
                }
                testRunning = true;
                eventCount = 0;
                priceUpdates.clear();
                eventTimes = [];
                startTime = Date.now();
                log('🚀 Connecting to /api/price-stream...', 'info');
                document.getElementById('connectionStatus').textContent = 'CONNECTING...';
                document.getElementById('connectionStatus').className = 'status-value pending';

                const serverUrl = 'http://localhost:5002/api/price-stream';
                log(`📡 Server URL: ${serverUrl}`, 'info');

                eventSource = new EventSource(serverUrl);
                eventSource.onopen = () => {
                    log('✅ EventSource connection opened successfully', 'success');
                    document.getElementById('connectionStatus').textContent = 'CONNECTED';
                    document.getElementById('connectionStatus').className = 'status-value connected';
                    startTime = Date.now();
                };
                eventSource.onmessage = (event) => {
                    try {
                        const startProcessing = Date.now();
                        const update = JSON.parse(event.data);
                        const processingTime = Date.now() - startProcessing;
                        eventCount++;
                        eventTimes.push(processingTime);
                        const prevData = priceUpdates.get(update.mint);
                        let direction = null;
                        if (prevData && prevData.price_usd) {
                            direction = update.price_usd > prevData.price_usd ? 'up' : update.price_usd < prevData.price_usd ? 'down' : null;
                        }
                        priceUpdates.set(update.mint, {
                            ...update,
                            timestamp: Date.now(),
                            direction: direction
                        });
                        log(`[PRICE_UPDATE #${eventCount}] ${update.mint.slice(0, 8)}... → $${update.price_usd?.toFixed(8) || 'N/A'} (${update.source})`, 'price-update');
                        document.getElementById('lastEvent').textContent = new Date().toLocaleTimeString('en-US', { hour12: false });
                        updateStats();
                        updatePriceDisplay();
                    } catch (error) {
                        log(`❌ Parse error: ${error.message}`, 'error');
                    }
                };
                eventSource.onerror = (error) => {
                    log('❌ EventSource connection error', 'error');
                    document.getElementById('connectionStatus').textContent = 'DISCONNECTED';
                    document.getElementById('connectionStatus').className = 'status-value disconnected';
                    eventSource?.close();
                };
            }

            function stopTest() {
                if (!testRunning) return;
                testRunning = false;
                if (eventSource) {
                    eventSource.close();
                    log('⏹ Test stopped', 'info');
                }
                document.getElementById('connectionStatus').textContent = 'DISCONNECTED';
                document.getElementById('connectionStatus').className = 'status-value disconnected';
            }

            function clearLogs() {
                document.getElementById('logContainer').innerHTML = '';
                log('🗑 Logs cleared', 'info');
            }

            function exportData() {
                const data = {
                    eventCount: eventCount,
                    totalTokens: priceUpdates.size,
                    priceUpdates: Array.from(priceUpdates.entries()).map(([mint, data]) => ({mint, ...data})),
                    avgEventTime: eventTimes.length > 0 ? (eventTimes.reduce((a, b) => a + b, 0) / eventTimes.length).toFixed(1) : 0
                };
                const json = JSON.stringify(data, null, 2);
                const blob = new Blob([json], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `flex-price-test-${new Date().toISOString().slice(0, 19)}.json`;
                a.click();
                log('💾 Data exported to file', 'success');
            }
        </script>
    </body>
    </html>
    """)

# =========================================================================
# START BACKGROUND WORKERS
# =========================================================================

def _sync_validated_tokens_to_tracker():
    """Register all validated token_pool_accounts into tracked_tokens on startup."""
    try:
        import sqlite3 as _sq
        from src.core.price_worker import PriceWorkerRegistry
        conn = _sq.connect(DB_PATH)
        mints = [r[0] for r in conn.execute(
            "SELECT DISTINCT mint FROM token_pool_accounts WHERE vault_validation_status IN ('validated','pending') AND is_active = 1"
        ).fetchall()]
        conn.close()
        if not mints:
            return
        registry = PriceWorkerRegistry(DB_PATH)
        for mint in mints:
            registry.register_token(mint, priority_level='MEDIUM')
        print(f"[PRICE_WORKER] Synced {len(mints)} validated tokens into tracked_tokens")
    except Exception as e:
        print(f"[WARNING] Token sync failed: {e}")


def start_background_workers():
    """Start price and liquidity workers in background threads"""
    _sync_validated_tokens_to_tracker()

    import os
    if os.environ.get('FLEX_WS_DISABLED', '0') == '1':
        print("[PRICE_WORKER] Skipping worker start — listener process owns pricing (FLEX_WS_DISABLED=1)")
    else:
        try:
            from src.core.price_worker import start_price_worker
            price_worker = start_price_worker(db_path=DB_PATH)
            print(f"[PRICE_WORKER] Background price worker started pid={os.getpid()}")
        except Exception as e:
            print(f"[WARNING] Price worker failed to start: {e}")

    try:
        from src.core.liquidity_worker import start_liquidity_worker
        liquidity_worker = start_liquidity_worker(db_path=DB_PATH)
        print("[LIQUIDITY_WORKER] Background liquidity worker started - updating every 60s")
    except Exception as e:
        print(f"[WARNING] Liquidity worker failed to start: {e}")

    try:
        import subprocess, sys as _sys
        def _run_monitor():
            import time as _time
            while True:
                _time.sleep(600)
                try:
                    subprocess.run(
                        [_sys.executable, '-m', 'src.core.token_behaviour_monitor', DB_PATH],
                        timeout=120,
                        capture_output=True,
                    )
                except Exception:
                    pass
        behaviour_thread = threading.Thread(target=_run_monitor, daemon=True)
        behaviour_thread.start()
        print("[TOKEN_BEHAVIOUR] Background classification monitor started - running every 10 min (subprocess)")
    except Exception as e:
        print(f"[WARNING] Token behaviour monitor failed to start: {e}")

# =========================================================================
# MAIN
# =========================================================================

if __name__ == '__main__':
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    import os as _os
    try:
        from src.core.ws_snapshot_logger import _LOG_PATH as _ws_log_path
        _ws_log_abs = _os.path.abspath(_ws_log_path)
    except Exception:
        _ws_log_abs = '(unavailable)'
    print("[STARTUP] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"[STARTUP] role=flask pid={_os.getpid()}")
    print(f"[STARTUP] db={_os.path.abspath(DB_PATH)}")
    print(f"[STARTUP] ws_snapshot_log={_ws_log_abs}")
    print(f"[STARTUP] cwd={_os.getcwd()}")
    print(f"[STARTUP] FLEX_WS_DISABLED={_os.environ.get('FLEX_WS_DISABLED','0')}")
    print("[STARTUP] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("[FLASK] Starting Migration Tracker UI...")
    print("[FLASK] Dashboard available at http://localhost:5002")
    print(f"[FLASK] Database: {_os.path.abspath(DB_PATH)}")

    # Start background workers before Flask
    start_background_workers()

    app.run(host='0.0.0.0', port=5002, debug=False, threaded=True)
