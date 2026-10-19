"""
Sprint X13 — Intelligence Assessment API routes.

All routes read from pre-computed engine outputs.
No route triggers unbounded computation on page load.

GET /api/operators/<id>/assessment              — full bundle
GET /api/operators/<id>/assessment/<type>       — single assessment type
GET /api/assessments/platform-summary           — cached platform view
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from src.ops.assessment_engine import AssessmentEngine, ASSESSMENT_TYPES

assessment_bp = Blueprint("assessment", __name__)

_engine: AssessmentEngine | None = None


def _get_engine() -> AssessmentEngine:
    global _engine
    if _engine is None:
        from src.core.db import OPS_DB_PATH, LIVE_DB_PATH
        from src.ops.behaviour_engine import BehaviourEngine
        from src.ops.behaviour_change import BehaviourChangeEngine
        from src.ops.operator_similarity import OperatorSimilarityEngine

        _engine = AssessmentEngine(
            behaviour_engine   = BehaviourEngine(str(OPS_DB_PATH), str(LIVE_DB_PATH)),
            change_engine      = BehaviourChangeEngine(str(OPS_DB_PATH), str(LIVE_DB_PATH)),
            similarity_engine  = OperatorSimilarityEngine(str(OPS_DB_PATH)),
        )
    return _engine


def _unavailable(operator_id: str = "") -> dict:
    return {
        "ok":          False,
        "unavailable": True,
        "operator_id": operator_id,
        "message":     "Assessment generation is not available.",
    }


@assessment_bp.route("/api/operators/<operator_id>/assessment")
def operator_assessment(operator_id: str):
    """Full assessment bundle for one operator."""
    try:
        engine = _get_engine()
        bundle = engine.assess(operator_id)
        if not bundle.available:
            return jsonify({**_unavailable(operator_id), "error": bundle.error}), 200
        return jsonify({"ok": True, **bundle.to_dict()})
    except Exception as exc:
        return jsonify({**_unavailable(operator_id), "error": str(type(exc).__name__)}), 200


@assessment_bp.route("/api/operators/<operator_id>/assessment/<assessment_type>")
def operator_assessment_by_type(operator_id: str, assessment_type: str):
    """Single assessment of a given type for one operator."""
    atype = assessment_type.upper()
    if atype not in ASSESSMENT_TYPES:
        return jsonify({"ok": False, "message": f"Unknown assessment type: {atype}"}), 400
    try:
        engine = _get_engine()
        bundle = engine.assess(operator_id)
        if not bundle.available:
            return jsonify({**_unavailable(operator_id)}), 200
        match  = bundle.by_type(atype)
        if match is None:
            return jsonify({
                "ok":          False,
                "operator_id": operator_id,
                "type":        atype,
                "message":     f"No {atype} assessment available for this operator.",
            })
        return jsonify({"ok": True, "assessment": match.to_dict()})
    except Exception as exc:
        return jsonify({**_unavailable(operator_id), "error": str(type(exc).__name__)}), 200


@assessment_bp.route("/api/assessments/platform-summary")
def platform_assessment_summary():
    """
    Platform-level assessment summary.

    Query params:
      operator_ids  — comma-separated list of operator IDs (max 50)
    """
    try:
        id_param = request.args.get("operator_ids", "")
        if id_param:
            operator_ids = [x.strip() for x in id_param.split(",") if x.strip()][:50]
        else:
            # Read from ops DB
            from src.core.db import OPS_DB_PATH
            import sqlite3
            with sqlite3.connect(f"file:{OPS_DB_PATH}?mode=ro", uri=True) as conn:
                rows = conn.execute(
                    "SELECT operator_id FROM operators ORDER BY updated_at DESC LIMIT 50"
                ).fetchall()
            operator_ids = [r[0] for r in rows]

        if not operator_ids:
            return jsonify({
                "ok":      True,
                "summary": {
                    "computed_at": 0, "operators_assessed": 0,
                    "operators_errored": 0, "coverage_pct": 0.0,
                    "counts_by_type": {}, "highest_confidence": [],
                },
            })

        engine  = _get_engine()
        summary = engine.platform_summary(operator_ids)
        return jsonify({"ok": True, "summary": summary})
    except Exception as exc:
        return jsonify({
            "ok":      False,
            "message": "Platform summary unavailable.",
            "error":   str(type(exc).__name__),
        }), 200


def register_assessment_routes(app) -> None:
    app.register_blueprint(assessment_bp)
    print("[ASSESSMENT] Intelligence Assessment registered (/api/operators/<id>/assessment)")
