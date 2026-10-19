"""
Behaviour Change Detection API routes — Sprint X11.

Canonical API consumed by X12 (forecasting). All routes are read-only.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from src.ops.behaviour_change import BehaviourChangeEngine, CURRENT_WINDOW_DAYS

change_bp = Blueprint("behaviour_change", __name__)

_engine: BehaviourChangeEngine | None = None


def _get_engine() -> BehaviourChangeEngine:
    global _engine
    if _engine is None:
        from src.core.db import DB_PATH, OPS_DB_PATH
        _engine = BehaviourChangeEngine(str(OPS_DB_PATH), str(DB_PATH))
    return _engine


@change_bp.route("/api/operators/<operator_id>/behaviour/change")
def operator_change(operator_id: str):
    """
    Full behaviour change report for an operator.

    Query params:
      window_days  — override the current observation window (default 7)
    """
    window = int(request.args.get("window_days", CURRENT_WINDOW_DAYS))
    engine = _get_engine()
    report = engine.compare(operator_id, current_window_days=window)
    return jsonify(report.to_dict())


@change_bp.route("/api/operators/<operator_id>/behaviour/change/<dimension_key>")
def operator_change_dimension(operator_id: str, dimension_key: str):
    """Single dimension change report."""
    window = int(request.args.get("window_days", CURRENT_WINDOW_DAYS))
    engine = _get_engine()
    report = engine.compare(operator_id, current_window_days=window)
    for dc in report.dimension_changes:
        if dc.key == dimension_key:
            return jsonify({"ok": True, "dimension_change": dc.to_dict()})
    return jsonify({"ok": False, "error": f"Unknown dimension: {dimension_key}"}), 404


@change_bp.route("/api/behaviour/change/platform-summary")
def platform_change_summary():
    """
    Cross-operator drift summary.

    Returns which operators have HIGH change, which are stable,
    and which lack sufficient evidence for comparison.
    """
    engine = _get_engine()
    summary = engine.compare_platform()
    return jsonify(summary)


def register_behaviour_change_routes(app) -> None:
    app.register_blueprint(change_bp)
    print("[BEHAVIOUR-CHANGE] Behaviour Change Detection registered "
          "(/api/operators/<id>/behaviour/change)")
