"""
Behaviour Intelligence API routes — Sprint X10.

All routes are canonical. Future systems (X11 anomaly detection, X12
forecasting) should consume /api/operators/<id>/behaviour rather than
independently querying historical data.
"""

from __future__ import annotations

import os
import time

from flask import Blueprint, jsonify, request

from src.ops.behaviour_engine import BehaviourEngine
from src.ops.observation_materializer import ObservationMaterializationPipeline
from src.ops.observation_store import ObservationStore

behaviour_bp = Blueprint("behaviour", __name__)

_engine: BehaviourEngine | None = None


def _get_engine() -> BehaviourEngine:
    global _engine
    if _engine is None:
        from src.core.db import DB_PATH, OPS_DB_PATH
        _engine = BehaviourEngine(str(OPS_DB_PATH), str(DB_PATH))
    return _engine


# ── Per-operator behaviour ────────────────────────────────────────────────────

@behaviour_bp.route("/api/operators/<operator_id>/behaviour")
def operator_behaviour(operator_id: str):
    """
    Canonical behaviour API for an operator.

    Returns a BehaviourProfile with five dimensions:
      campaign / funding / launch / operational / outcome

    Each fact includes confidence and observation count.
    Downstream systems (X11/X12) should consume this endpoint.
    """
    engine = _get_engine()
    profile = engine.compute(operator_id)
    return jsonify(profile.to_dict())


@behaviour_bp.route("/api/operators/<operator_id>/observations")
def operator_observations(operator_id: str):
    """Materialization lifecycle and canonical observations for one operator."""
    from src.core.db import OPS_DB_PATH
    store = ObservationStore(str(OPS_DB_PATH))
    kinds = {value.upper() for value in request.args.getlist("type") if value}
    observations = store.fetch(operator_id, kinds or None)
    return jsonify({
        "ok": True,
        "materialization": store.status(operator_id),
        "observations": [observation.to_dict() for observation in observations],
    })


@behaviour_bp.route("/api/operators/<operator_id>/observations/materialize", methods=["POST"])
def materialize_operator_observations(operator_id: str):
    """Re-run deterministic local materialization; no discovery or RPC work."""
    from src.core.db import DB_PATH, OPS_DB_PATH
    result = ObservationMaterializationPipeline(
        str(OPS_DB_PATH), str(DB_PATH)
    ).run(operator_id)
    return jsonify({"ok": True, "materialization": result})


@behaviour_bp.route("/api/operators/<operator_id>/behaviour/dimension/<dimension_key>")
def operator_behaviour_dimension(operator_id: str, dimension_key: str):
    """Return a single behaviour dimension by key."""
    engine = _get_engine()
    profile = engine.compute(operator_id)
    for dim in profile.dimensions:
        if dim.key == dimension_key:
            return jsonify({"ok": True, "dimension": dim.to_dict()})
    return jsonify({"ok": False, "error": f"Unknown dimension: {dimension_key}"}), 404


@behaviour_bp.route("/api/operators/<operator_id>/behaviour/fact/<fact_key>")
def operator_behaviour_fact(operator_id: str, fact_key: str):
    """Look up a single behaviour fact by key across all dimensions."""
    engine = _get_engine()
    profile = engine.compute(operator_id)
    for dim in profile.dimensions:
        for fact in dim.facts:
            if fact.key == fact_key:
                return jsonify({"ok": True, "fact": fact.to_dict()})
    return jsonify({"ok": False, "error": f"No fact with key: {fact_key}"}), 404


# ── Platform-level behaviour summary ─────────────────────────────────────────

@behaviour_bp.route("/api/behaviour/platform-summary")
def platform_behaviour_summary():
    """
    Cross-operator behaviour summary.

    Intended for the operators index and cross-operation intelligence views.
    Does not compute individual profiles.
    """
    engine = _get_engine()
    summary = engine.compute_platform_summary()
    return jsonify(summary)


# ── Registration ──────────────────────────────────────────────────────────────

def register_behaviour_routes(app) -> None:
    app.register_blueprint(behaviour_bp)
    print("[BEHAVIOUR] Behaviour Intelligence registered (/api/operators/<id>/behaviour)")
