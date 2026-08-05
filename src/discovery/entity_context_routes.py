"""X75.3A PART 3/5 -- Entity Context HTTP routes.

GET /api/discovery/entity-context/<wallet> -> {canonical_identity,
                                                review_decision,
                                                structural_populations,
                                                related_operators}

Read-only. Never claims a direct relationship where only structural
co-membership exists, and never omits a wallet's review history when
presenting its canonical identity (or vice versa) -- see
src/discovery/relationship_classification.py.
"""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, jsonify

entity_context_bp = Blueprint("entity_context", __name__)


def _conn():
    import sqlite3
    from src.core.db import OPS_DB_PATH
    conn = sqlite3.connect(str(OPS_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _populations():
    from src.discovery.relationship_classification import dedupe_populations_by_family_id
    from src.ops.operator_routes import _get_emerging_service
    d = _get_emerging_service().list(limit=200, debug=False)
    return dedupe_populations_by_family_id(
        d.get("confirmed_operations_reconciled", []),
        d.get("active_investigations_reconciled", []),
        d.get("operator_candidates_reconciled", []),
        d.get("review_cases_reconciled", []),
        d.get("infrastructure_alerts_reconciled", []),
    )


@entity_context_bp.route("/api/discovery/entity-context/<wallet>")
def api_entity_context(wallet: str):
    from src.discovery.relationship_classification import build_entity_context

    conn = _conn()
    try:
        pops = _populations()
        ctx = build_entity_context(conn, wallet, pops)
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "wallet": ctx.wallet,
        "canonical_identity": asdict(ctx.canonical_identity) if ctx.canonical_identity else None,
        "review_decision": asdict(ctx.review_decision) if ctx.review_decision else None,
        "structural_populations": list(ctx.structural_populations),
    })


@entity_context_bp.route("/api/discovery/relationship/<wallet_a>/<wallet_b>")
def api_relationship_between(wallet_a: str, wallet_b: str):
    from src.discovery.relationship_classification import relationship_between

    conn = _conn()
    try:
        pops = _populations()
        rel = relationship_between(conn, wallet_a, wallet_b, pops)
    finally:
        conn.close()
    return jsonify({"ok": True, **rel})


def register_entity_context_routes(app: object) -> None:
    app.register_blueprint(entity_context_bp)
