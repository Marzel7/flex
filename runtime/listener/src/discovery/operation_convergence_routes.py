"""X75.0/X75.2 Discovery <-> Operations Convergence -- HTTP routes.

GET /api/discovery/convergence -> {known_operations, potential_expansions,
                                    new_investigations, new_operational_behaviour,
                                    review, shared_infrastructure}

Read-only re-projection of EmergingOperatorService.list() and walkback's
already-materialised terminal outcomes -- see
src/discovery/operation_convergence.py and
src/discovery/infrastructure_reconstruction.py. No writes, no attribution
changes, no reconciliation changes, no resolver changes.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

discovery_convergence_bp = Blueprint("discovery_convergence", __name__)


@discovery_convergence_bp.route("/api/discovery/convergence")
def api_discovery_convergence():
    import sqlite3
    from dataclasses import asdict
    from src.core.db import OPS_DB_PATH
    from src.discovery.operation_convergence import build_convergence_view
    from src.discovery.infrastructure_reconstruction import recent_infrastructure_facts

    from src.ops.operator_routes import _get_emerging_service
    list_payload = _get_emerging_service().list(limit=200, debug=False)

    conn = sqlite3.connect(str(OPS_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        min_score = float(request.args.get("min_score", 0.34))
        view = build_convergence_view(conn, list_payload, min_score=min_score)
        behaviour_limit = int(request.args.get("behaviour_limit", 30))
        facts = recent_infrastructure_facts(conn, limit=behaviour_limit)
    finally:
        conn.close()
    view["new_operational_behaviour"] = [asdict(f) for f in facts]
    return jsonify({"ok": True, **view})


def register_discovery_convergence_routes(app: object) -> None:
    app.register_blueprint(discovery_convergence_bp)
