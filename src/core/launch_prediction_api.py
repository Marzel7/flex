"""
FLEX Phase 3.3+ Launch Prediction API Endpoints

Provides REST endpoints for:
1. Launch watchlist queries (probability, risk levels)
2. Creator reuse analysis
3. Pump.fun farm detection
4. Prediction accuracy history

All endpoints operate on Phase 3.3+ tables:
- creator_reuse
- launch_watchlist
- launch_detection_history
"""

import json
import sqlite3
from typing import Dict, List, Optional, Tuple
from flask import Blueprint, request, jsonify

# Create blueprint
launch_api = Blueprint('launch_api', __name__, url_prefix='/api')


def get_db_conn(db_path: str) -> sqlite3.Connection:
    """Get database connection."""
    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# ENDPOINT 1: GET /api/launch/watchlist
# ============================================================================

@launch_api.route('/launch/watchlist', methods=['GET'])
def get_launch_watchlist():
    """
    Get launch watchlist sorted by probability (highest first).

    Query params:
    - risk_level: CRITICAL|HIGH|MEDIUM|LOW|MINIMAL (filter)
    - min_probability: 0-100 (default: 20)
    - limit: rows (default: 100)

    Response: [{
        "creator": "string",
        "launch_probability": 0-100,
        "risk_level": "string",
        "expected_launch_day": 1-7,
        "funder_count": int,
        "active_days": float,
        "signals": [string],
        "factor_breakdown": { ... }
    }]
    """
    try:
        # Parse query params
        risk_level = request.args.get('risk_level', type=str)
        min_probability = request.args.get('min_probability', 20, type=float)
        limit = request.args.get('limit', 100, type=int)

        # Validate
        min_probability = max(0, min(100, min_probability))
        limit = max(1, min(1000, limit))

        db_path = request.app.config.get('DB_PATH', 'database/flex_complete_database.db')
        conn = get_db_conn(db_path)
        cursor = conn.cursor()

        # Build query
        query = """
            SELECT
                creator_wallet,
                launch_probability,
                risk_level,
                expected_launch_day,
                funder_count,
                funding_days_active,
                cluster_id,
                reuse_score,
                farm_confidence_score,
                recency_score,
                reputation_score,
                signal_count,
                updated_at
            FROM launch_watchlist
            WHERE launch_probability >= ?
        """
        params = [min_probability]

        if risk_level:
            query += " AND risk_level = ?"
            params.append(risk_level)

        query += " ORDER BY launch_probability DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        # Build response
        results = []
        for row in rows:
            # Compute factor breakdown
            factor_breakdown = {
                'cluster_confidence': row[8],
                'creator_reuse': row[7],
                'recency': row[9],
                'reputation': row[10],
                'wallet_age': 0  # Not stored, compute from dev_reputation if needed
            }

            results.append({
                'creator': row[0],
                'launch_probability': row[1],
                'risk_level': row[2],
                'expected_launch_day': row[3],
                'funder_count': row[4],
                'active_days': row[5],
                'cluster_id': row[6],
                'signal_count': row[11],
                'factor_breakdown': factor_breakdown,
                'last_updated': row[12]
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 2: GET /api/launch/watchlist/<creator>
# ============================================================================

@launch_api.route('/launch/watchlist/<creator>', methods=['GET'])
def get_launch_prediction(creator):
    """
    Get detailed launch prediction for specific creator.

    Response: {
        "creator": "string",
        "launch_probability": 0-100,
        "risk_level": "string",
        "expected_launch_day": 1-7,
        "expected_launch_window": "0-1 days|1-3 days|3-7 days",
        "funder_count": int,
        "cluster_id": int|null,
        "funding_history": [{...}],
        "factor_breakdown": {...},
        "signals": [string]
    }
    """
    try:
        db_path = request.app.config.get('DB_PATH', 'database/flex_complete_database.db')
        conn = get_db_conn(db_path)
        cursor = conn.cursor()

        # Get launch watchlist entry
        cursor.execute("""
            SELECT
                creator_wallet,
                launch_probability,
                risk_level,
                expected_launch_day,
                funder_count,
                funding_days_active,
                cluster_id,
                last_funding_ts,
                reuse_score,
                farm_confidence_score,
                recency_score,
                reputation_score,
                signal_count
            FROM launch_watchlist
            WHERE creator_wallet = ?
        """, (creator,))

        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({'error': 'Creator not found in watchlist'}), 404

        # Get creator reuse info for funding history
        cursor.execute("""
            SELECT funder_list, first_funded_ts, last_funded_ts
            FROM creator_reuse
            WHERE creator_wallet = ?
        """, (creator,))

        reuse_row = cursor.fetchone()
        funder_list = []
        if reuse_row:
            # Parse funder list (comma-separated)
            funder_list = [f.strip() for f in reuse_row[0].split(',') if f.strip()]

        # Determine launch window
        active_days = row[5]
        if active_days <= 1:
            launch_window = '0-1 days'
        elif active_days <= 3:
            launch_window = '1-3 days'
        elif active_days <= 7:
            launch_window = '3-7 days'
        else:
            launch_window = '7+ days'

        factor_breakdown = {
            'cluster_confidence': row[9],
            'creator_reuse': row[8],
            'recency': row[10],
            'reputation': row[11]
        }

        result = {
            'creator': row[0],
            'launch_probability': row[1],
            'risk_level': row[2],
            'expected_launch_day': row[3],
            'expected_launch_window': launch_window,
            'funder_count': row[4],
            'funding_days_active': row[5],
            'cluster_id': row[6],
            'last_funding_ts': row[7],
            'funder_list': funder_list,
            'signal_count': row[12],
            'factor_breakdown': factor_breakdown
        }

        conn.close()
        return jsonify(result), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 3: GET /api/launch/critical-risk
# ============================================================================

@launch_api.route('/launch/critical-risk', methods=['GET'])
def get_critical_risk_launches():
    """
    Get all creators with CRITICAL or HIGH risk levels.

    Response: [{
        "creator": "string",
        "launch_probability": 0-100,
        "risk_level": "CRITICAL|HIGH",
        "hours_since_last_funding": float,
        "expected_launch_ts": int,
        "cluster_id": int,
        "funder_count": int,
        "warning": "string"
    }]
    """
    try:
        db_path = request.app.config.get('DB_PATH', 'database/flex_complete_database.db')
        conn = get_db_conn(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                creator_wallet,
                launch_probability,
                risk_level,
                expected_launch_day,
                last_funding_ts,
                cluster_id,
                funder_count
            FROM launch_watchlist
            WHERE risk_level IN ('CRITICAL', 'HIGH')
            ORDER BY launch_probability DESC
        """)

        rows = cursor.fetchall()

        results = []
        current_ts = int(__import__('time').time())

        for row in rows:
            hours_since = (current_ts - row[4]) / 3600.0 if row[4] else None
            expected_launch_ts = row[4] + (row[3] * 86400) if row[4] else None

            warning = f"{row[2]} risk - {row[1]:.0f}% probability"
            if hours_since and hours_since < 24:
                warning += f", funded {hours_since:.1f}h ago"

            results.append({
                'creator': row[0],
                'launch_probability': row[1],
                'risk_level': row[2],
                'expected_launch_day': row[3],
                'hours_since_last_funding': hours_since,
                'expected_launch_ts': expected_launch_ts,
                'cluster_id': row[5],
                'funder_count': row[6],
                'warning': warning
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 4: GET /api/launch/history
# ============================================================================

@launch_api.route('/launch/history', methods=['GET'])
def get_launch_history():
    """
    Get historical predictions and actual launches for accuracy tracking.

    Query params:
    - limit: rows (default: 50)
    - launched_only: bool (default: false)

    Response: [{
        "creator": "string",
        "predicted_probability": 0-100,
        "predicted_risk_level": "string",
        "actual_launch_ts": int|null,
        "days_to_launch": int|null,
        "prediction_accuracy": 0-100|null,
        "token_mint": "string|null"
    }]
    """
    try:
        # Parse params
        limit = request.args.get('limit', 50, type=int)
        launched_only = request.args.get('launched_only', False, type=bool)

        limit = max(1, min(500, limit))

        db_path = request.app.config.get('DB_PATH', 'database/flex_complete_database.db')
        conn = get_db_conn(db_path)
        cursor = conn.cursor()

        query = """
            SELECT
                creator_wallet,
                predicted_probability,
                predicted_risk_level,
                actual_launch_ts,
                days_to_actual_launch,
                prediction_accuracy,
                token_mint,
                detected_at
            FROM launch_detection_history
        """

        if launched_only:
            query += " WHERE launch_detected = 1"

        query += " ORDER BY detected_at DESC LIMIT ?"

        cursor.execute(query, (limit,))
        rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append({
                'creator': row[0],
                'predicted_probability': row[1],
                'predicted_risk_level': row[2],
                'actual_launch_ts': row[3],
                'days_to_launch': row[4],
                'prediction_accuracy': row[5],
                'token_mint': row[6],
                'detected_at': row[7]
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 5: GET /api/clusters/pumpfun
# ============================================================================

@launch_api.route('/clusters/pumpfun', methods=['GET'])
def get_pumpfun_farms():
    """
    Get pump.fun-style coordinated operations (4+ creators, <48h).

    Query params:
    - min_confidence: 0-100 (default: 50)
    - limit: rows (default: 50)

    Response: [{
        "funder_wallet": "string",
        "creator_count": int,
        "transfer_count": int,
        "confidence": 0-100,
        "avg_amount": float,
        "min_amount": float,
        "max_amount": float,
        "span_hours": float,
        "creators": [string],
        "created_at": int
    }]
    """
    try:
        min_confidence = request.args.get('min_confidence', 50, type=float)
        limit = request.args.get('limit', 50, type=int)

        min_confidence = max(0, min(100, min_confidence))
        limit = max(1, min(500, limit))

        db_path = request.app.config.get('DB_PATH', 'database/flex_complete_database.db')
        conn = get_db_conn(db_path)
        cursor = conn.cursor()

        # Query creator_reuse for marked pump.fun targets
        # This is a simplified query - in production you'd track pump.fun farms separately
        cursor.execute("""
            SELECT DISTINCT creator_wallet FROM creator_reuse
            WHERE is_pump_fun_target = 1
            LIMIT ?
        """, (limit,))

        pumpfun_creators = [row[0] for row in cursor.fetchall()]

        # For full pump.fun farm info, you'd query transfer_index
        # This is a simplified response
        results = []
        for creator in pumpfun_creators[:limit]:
            results.append({
                'funder_wallet': None,  # Would need tracking table for this
                'creator_count': None,
                'creator_example': creator,
                'is_pump_fun_target': True
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 6: GET /api/creators/reuse
# ============================================================================

@launch_api.route('/launch/creators/reuse', methods=['GET'])
def get_creator_reuse():
    """
    Get creators with multiple funding sources (coordination signal).

    Query params:
    - min_funders: int (default: 3)
    - min_reuse_score: 0-40 (default: 0)
    - limit: rows (default: 100)

    Response: [{
        "creator": "string",
        "funder_count": int,
        "transfer_count": int,
        "reuse_score": 0-40,
        "active_days": float,
        "expected_launch_window": "0-1 days|1-3 days|3-7 days",
        "funder_list": [string],
        "in_cluster": bool,
        "cluster_id": int|null
    }]
    """
    try:
        min_funders = request.args.get('min_funders', 3, type=int)
        min_reuse_score = request.args.get('min_reuse_score', 0, type=float)
        limit = request.args.get('limit', 100, type=int)

        min_funders = max(2, min(20, min_funders))
        min_reuse_score = max(0, min(40, min_reuse_score))
        limit = max(1, min(1000, limit))

        db_path = request.app.config.get('DB_PATH', 'database/flex_complete_database.db')
        conn = get_db_conn(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                creator_wallet,
                funder_count,
                transfer_count,
                reuse_score,
                active_days,
                funder_list,
                cluster_id,
                detected_at
            FROM creator_reuse
            WHERE funder_count >= ?
              AND reuse_score >= ?
            ORDER BY reuse_score DESC
            LIMIT ?
        """, (min_funders, min_reuse_score, limit))

        rows = cursor.fetchall()

        results = []
        for row in rows:
            active_days = row[4]
            if active_days <= 1:
                launch_window = '0-1 days'
            elif active_days <= 3:
                launch_window = '1-3 days'
            elif active_days <= 7:
                launch_window = '3-7 days'
            else:
                launch_window = '7+ days'

            funder_list = [f.strip() for f in row[5].split(',') if f.strip()] if row[5] else []

            results.append({
                'creator': row[0],
                'funder_count': row[1],
                'transfer_count': row[2],
                'reuse_score': row[3],
                'active_days': row[4],
                'expected_launch_window': launch_window,
                'funder_list': funder_list,
                'in_cluster': row[6] is not None,
                'cluster_id': row[6]
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# HELPER FUNCTION: Register blueprint with Flask app
# ============================================================================

def register_launch_api(app, db_path: str = 'database/flex_complete_database.db'):
    """
    Register Phase 3.3+ API blueprint with Flask app.

    Usage in main.py:
        from src.core.launch_prediction_api import register_launch_api
        register_launch_api(app, db_path)
    """
    app.config['DB_PATH'] = db_path
    app.register_blueprint(launch_api)
