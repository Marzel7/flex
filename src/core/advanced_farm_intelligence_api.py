"""
FLEX Phase 4: Advanced Farm Intelligence API Endpoints

Provides REST endpoints for:
1. Ecosystem coordination queries (2+ funders sharing creators)
2. Launch wave analysis (coordinated 1-hour bursts)
3. Ecosystem member importance and roles
4. Launch wave creator predictions
5. Ecosystem evolution timeline
6. High-risk ecosystems (CRITICAL coordination)
7. Pump.fun wave detection

All endpoints operate on Phase 4 tables:
- dev_farm_ecosystems
- launch_waves
- ecosystem_member_tracking
- launch_wave_creators
- ecosystem_evolution_log
"""

import json
import sqlite3
from typing import Dict, List, Optional, Tuple
from flask import Blueprint, request, jsonify

# Create blueprint
farm_intelligence_api = Blueprint('farm_intelligence_api', __name__, url_prefix='/api')

# Global database path (set by register_farm_intelligence_api)
_DB_PATH = None

def get_db_conn(db_path: str = None) -> sqlite3.Connection:
    """Get database connection."""
    if db_path is None:
        db_path = _DB_PATH
    if db_path is None:
        raise ValueError("Database path not configured")
    conn = sqlite3.connect(db_path, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# ENDPOINT 1: GET /api/ecosystems/high-confidence
# ============================================================================

@farm_intelligence_api.route('/ecosystems/high-confidence', methods=['GET'])
def get_high_confidence_ecosystems():
    """
    Get ecosystems with high coordination scores (>60).

    Query params:
    - min_shared_creators: int (default: 3)
    - min_coordination_score: 0-100 (default: 60)
    - limit: rows (default: 50)

    Response: [{
        "ecosystem_id": int,
        "funder_1": "string",
        "funder_2": "string",
        "shared_creators": int,
        "coordination_score": 0-100,
        "ecosystem_size": int,
        "coordination_span_days": float,
        "risk_level": "CRITICAL|HIGH|MEDIUM",
        "shared_creators_list": [string],
        "last_updated": timestamp
    }]
    """
    try:
        min_shared = request.args.get('min_shared_creators', 3, type=int)
        min_score = request.args.get('min_coordination_score', 60, type=float)
        limit = request.args.get('limit', 50, type=int)

        min_shared = max(2, min(20, min_shared))
        min_score = max(0, min(100, min_score))
        limit = max(1, min(500, limit))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                ecosystem_id,
                funder_1,
                funder_2,
                shared_creators,
                coordination_score,
                ecosystem_size,
                coordination_span_days,
                creators_list,
                updated_at
            FROM dev_farm_ecosystems
            WHERE shared_creators >= ?
              AND coordination_score >= ?
              AND is_active_ecosystem = 1
            ORDER BY coordination_score DESC
            LIMIT ?
        """, (min_shared, min_score, limit))

        rows = cursor.fetchall()

        results = []
        for row in rows:
            # Determine risk level
            score = row[4]
            if score > 80:
                risk_level = "CRITICAL"
            elif score > 65:
                risk_level = "HIGH"
            else:
                risk_level = "MEDIUM"

            # Parse creators list
            creators = []
            if row[7]:
                try:
                    creators = json.loads(row[7])
                except json.JSONDecodeError:
                    creators = []

            results.append({
                'ecosystem_id': row[0],
                'funder_1': row[1],
                'funder_2': row[2],
                'shared_creators': row[3],
                'coordination_score': row[4],
                'ecosystem_size': row[5],
                'coordination_span_days': row[6],
                'risk_level': risk_level,
                'shared_creators_list': creators,
                'last_updated': row[8]
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 2: GET /api/ecosystems/<ecosystem_id>/members
# ============================================================================

@farm_intelligence_api.route('/ecosystems/<int:ecosystem_id>/members', methods=['GET'])
def get_ecosystem_members(ecosystem_id):
    """
    Get all members (funders and creators) in an ecosystem with roles.

    Query params:
    - include_importance: bool (default: true)
    - limit: rows (default: 100)

    Response: {
        "ecosystem_id": int,
        "funder_1": "string",
        "funder_2": "string",
        "shared_creators": int,
        "members": [{
            "address": "string",
            "type": "funder|creator",
            "connections": int,
            "transfer_count": int,
            "is_leader": bool,
            "is_hub": bool,
            "importance_score": 0-100,
            "active_days": float
        }]
    }
    """
    try:
        conn = get_db_conn()
        cursor = conn.cursor()

        # Get ecosystem info
        cursor.execute("""
            SELECT ecosystem_id, funder_1, funder_2, shared_creators, coordination_score
            FROM dev_farm_ecosystems
            WHERE ecosystem_id = ?
        """, (ecosystem_id,))

        ecosystem = cursor.fetchone()
        if not ecosystem:
            conn.close()
            return jsonify({'error': 'Ecosystem not found'}), 404

        # Get members
        cursor.execute("""
            SELECT
                member_address,
                member_type,
                connections_in_ecosystem,
                transfer_count_in_ecosystem,
                avg_amount_in_ecosystem,
                is_ecosystem_leader,
                is_ecosystem_hub,
                ecosystem_importance_score,
                active_days
            FROM ecosystem_member_tracking
            WHERE ecosystem_id = ?
            ORDER BY ecosystem_importance_score DESC
        """, (ecosystem_id,))

        members = []
        for row in cursor.fetchall():
            members.append({
                'address': row[0],
                'type': row[1],
                'connections': row[2],
                'transfer_count': row[3],
                'avg_amount': row[4],
                'is_leader': bool(row[5]),
                'is_hub': bool(row[6]),
                'importance_score': row[7],
                'active_days': row[8]
            })

        result = {
            'ecosystem_id': ecosystem[0],
            'funder_1': ecosystem[1],
            'funder_2': ecosystem[2],
            'shared_creators': ecosystem[3],
            'coordination_score': ecosystem[4],
            'members': members
        }

        conn.close()
        return jsonify(result), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 3: GET /api/waves/pump-fun
# ============================================================================

@farm_intelligence_api.route('/waves/pump-fun', methods=['GET'])
def get_pump_fun_waves():
    """
    Get launch waves matching pump.fun pattern (4+ creators in <1 hour, 2+ funders).

    Query params:
    - min_creators: int (default: 4)
    - min_pump_fun_confidence: 0-100 (default: 70)
    - limit: rows (default: 50)

    Response: [{
        "wave_id": int,
        "wave_hour": int,
        "wave_start_ts": timestamp,
        "creator_count": int,
        "funder_count": int,
        "transfer_count": int,
        "pump_fun_confidence": 0-100,
        "wave_intensity": 0-100,
        "creators": [string],
        "funders": [string],
        "avg_amount": float,
        "coordination_signal": 0-100
    }]
    """
    try:
        min_creators = request.args.get('min_creators', 4, type=int)
        min_confidence = request.args.get('min_pump_fun_confidence', 70, type=float)
        limit = request.args.get('limit', 50, type=int)

        min_creators = max(3, min(50, min_creators))
        min_confidence = max(0, min(100, min_confidence))
        limit = max(1, min(500, limit))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                wave_id,
                wave_hour,
                wave_start_ts,
                creator_count,
                funder_count,
                transfer_count,
                avg_amount,
                pump_fun_confidence,
                wave_intensity,
                coordination_signal,
                creators_list,
                funders_list,
                updated_at
            FROM launch_waves
            WHERE is_pump_fun_wave = 1
              AND creator_count >= ?
              AND pump_fun_confidence >= ?
            ORDER BY pump_fun_confidence DESC
            LIMIT ?
        """, (min_creators, min_confidence, limit))

        rows = cursor.fetchall()

        results = []
        for row in rows:
            creators = []
            funders = []
            try:
                creators = json.loads(row[10]) if row[10] else []
                funders = json.loads(row[11]) if row[11] else []
            except json.JSONDecodeError:
                pass

            results.append({
                'wave_id': row[0],
                'wave_hour': row[1],
                'wave_start_ts': row[2],
                'creator_count': row[3],
                'funder_count': row[4],
                'transfer_count': row[5],
                'avg_amount': row[6],
                'pump_fun_confidence': row[7],
                'wave_intensity': row[8],
                'coordination_signal': row[9],
                'creators': creators,
                'funders': funders,
                'last_updated': row[12]
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 4: GET /api/waves/<wave_id>/creators
# ============================================================================

@farm_intelligence_api.route('/waves/<int:wave_id>/creators', methods=['GET'])
def get_wave_creators(wave_id):
    """
    Get all creators in a launch wave with individual probabilities.

    Query params:
    - min_probability: 0-100 (default: 50)
    - limit: rows (default: 100)

    Response: [{
        "creator": "string",
        "funder_count_in_wave": int,
        "simultaneous_funders": int,
        "launch_probability": 0-100,
        "predicted_launch_ts": timestamp,
        "expected_token_mint": "string|null",
        "token_launched": bool,
        "prediction_accuracy": 0-100|null,
        "last_updated": timestamp
    }]
    """
    try:
        min_prob = request.args.get('min_probability', 50, type=float)
        limit = request.args.get('limit', 100, type=int)

        min_prob = max(0, min(100, min_prob))
        limit = max(1, min(500, limit))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                creator_wallet,
                funder_count_in_wave,
                simultaneous_funders,
                wave_launch_probability,
                predicted_launch_ts,
                expected_token_mint,
                token_launched,
                prediction_accuracy,
                updated_at
            FROM launch_wave_creators
            WHERE wave_id = ?
              AND wave_launch_probability >= ?
            ORDER BY wave_launch_probability DESC
            LIMIT ?
        """, (wave_id, min_prob, limit))

        rows = cursor.fetchall()

        results = []
        for row in rows:
            results.append({
                'creator': row[0],
                'funder_count_in_wave': row[1],
                'simultaneous_funders': row[2],
                'launch_probability': row[3],
                'predicted_launch_ts': row[4],
                'expected_token_mint': row[5],
                'token_launched': bool(row[6]),
                'prediction_accuracy': row[7],
                'last_updated': row[8]
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 5: GET /api/ecosystems/<ecosystem_id>/evolution
# ============================================================================

@farm_intelligence_api.route('/ecosystems/<int:ecosystem_id>/evolution', methods=['GET'])
def get_ecosystem_evolution(ecosystem_id):
    """
    Get timeline of ecosystem changes (member joins, coordination changes, merges).

    Query params:
    - event_type: str (optional filter: 'ecosystem_created', 'member_joined', etc)
    - limit: rows (default: 50)

    Response: [{
        "event_type": "string",
        "funder_count_at_event": int,
        "creator_count_at_event": int,
        "coordination_score_at_event": 0-100,
        "event_ts": timestamp,
        "event_details": object|null
    }]
    """
    try:
        event_type = request.args.get('event_type', type=str)
        limit = request.args.get('limit', 50, type=int)

        limit = max(1, min(500, limit))

        conn = get_db_conn()
        cursor = conn.cursor()

        query = """
            SELECT
                event_type,
                funder_count_at_event,
                creator_count_at_event,
                coordination_score_at_event,
                event_ts,
                event_details
            FROM ecosystem_evolution_log
            WHERE ecosystem_id = ?
        """
        params = [ecosystem_id]

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        query += " ORDER BY event_ts DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()

        results = []
        for row in rows:
            details = None
            if row[5]:
                try:
                    details = json.loads(row[5])
                except json.JSONDecodeError:
                    details = row[5]

            results.append({
                'event_type': row[0],
                'funder_count_at_event': row[1],
                'creator_count_at_event': row[2],
                'coordination_score_at_event': row[3],
                'event_ts': row[4],
                'event_details': details
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 6: GET /api/creators/<creator>/in-waves
# ============================================================================

@farm_intelligence_api.route('/creators/<creator>/in-waves', methods=['GET'])
def get_creator_in_waves(creator):
    """
    Get all launch waves a creator appears in (shows coordination signals).

    Query params:
    - min_probability: 0-100 (default: 20)
    - limit: rows (default: 50)

    Response: [{
        "wave_id": int,
        "wave_start_ts": timestamp,
        "creator_count_in_wave": int,
        "funder_count_in_wave": int,
        "launch_probability": 0-100,
        "wave_intensity": 0-100,
        "pump_fun_confidence": 0-100,
        "coordinated_funders": [string],
        "predicted_launch_ts": timestamp,
        "token_launched": bool
    }]
    """
    try:
        min_prob = request.args.get('min_probability', 20, type=float)
        limit = request.args.get('limit', 50, type=int)

        min_prob = max(0, min(100, min_prob))
        limit = max(1, min(500, limit))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                lwc.wave_id,
                lw.wave_start_ts,
                lw.creator_count,
                lw.funder_count,
                lwc.wave_launch_probability,
                lw.wave_intensity,
                lw.pump_fun_confidence,
                lw.funders_list,
                lwc.predicted_launch_ts,
                lwc.token_launched
            FROM launch_wave_creators lwc
            JOIN launch_waves lw ON lwc.wave_id = lw.wave_id
            WHERE lwc.creator_wallet = ?
              AND lwc.wave_launch_probability >= ?
            ORDER BY lw.wave_start_ts DESC
            LIMIT ?
        """, (creator, min_prob, limit))

        rows = cursor.fetchall()

        results = []
        for row in rows:
            funders = []
            try:
                funders = json.loads(row[7]) if row[7] else []
            except json.JSONDecodeError:
                pass

            results.append({
                'wave_id': row[0],
                'wave_start_ts': row[1],
                'creator_count_in_wave': row[2],
                'funder_count_in_wave': row[3],
                'launch_probability': row[4],
                'wave_intensity': row[5],
                'pump_fun_confidence': row[6],
                'coordinated_funders': funders,
                'predicted_launch_ts': row[8],
                'token_launched': bool(row[9])
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# ENDPOINT 7: GET /api/funder/<funder>/ecosystems
# ============================================================================

@farm_intelligence_api.route('/funder/<funder>/ecosystems', methods=['GET'])
def get_funder_ecosystems(funder):
    """
    Get all ecosystems a funder participates in.

    Query params:
    - min_coordination_score: 0-100 (default: 40)
    - limit: rows (default: 50)

    Response: [{
        "ecosystem_id": int,
        "partner_funder": "string",
        "shared_creators": int,
        "coordination_score": 0-100,
        "coordination_span_days": float,
        "ecosystem_size": int,
        "created_at": timestamp
    }]
    """
    try:
        min_score = request.args.get('min_coordination_score', 40, type=float)
        limit = request.args.get('limit', 50, type=int)

        min_score = max(0, min(100, min_score))
        limit = max(1, min(500, limit))

        conn = get_db_conn()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                ecosystem_id,
                funder_1,
                funder_2,
                shared_creators,
                coordination_score,
                coordination_span_days,
                ecosystem_size,
                detected_at
            FROM dev_farm_ecosystems
            WHERE (funder_1 = ? OR funder_2 = ?)
              AND coordination_score >= ?
            ORDER BY coordination_score DESC
            LIMIT ?
        """, (funder, funder, min_score, limit))

        rows = cursor.fetchall()

        results = []
        for row in rows:
            # Determine which is the partner
            partner = row[2] if row[1] == funder else row[1]

            results.append({
                'ecosystem_id': row[0],
                'partner_funder': partner,
                'shared_creators': row[3],
                'coordination_score': row[4],
                'coordination_span_days': row[5],
                'ecosystem_size': row[6],
                'created_at': row[7]
            })

        conn.close()
        return jsonify(results), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# HELPER FUNCTION: Register blueprint with Flask app
# ============================================================================

def register_farm_intelligence_api(app, db_path: str = 'database/flex_complete_database.db'):
    """
    Register Phase 4 API blueprint with Flask app.

    Usage in main.py:
        from src.core.advanced_farm_intelligence_api import register_farm_intelligence_api
        register_farm_intelligence_api(app, db_path)
    """
    global _DB_PATH
    _DB_PATH = db_path
    app.register_blueprint(farm_intelligence_api)
