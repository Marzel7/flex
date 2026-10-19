"""X74.4 Evidence Integrity & Confidence Presentation — HTTP routes.

Read-only. These endpoints classify existing wt_watchtower_launches rows
via src/ops/watchtower_evidence_status.py; they never write, never alter
attribution/reconciliation/promotion/identity/discovery/walkback/registry
state.

GET /api/watchtower/evidence-integrity            -> summary counts (Phase 4/7)
GET /api/watchtower/launch/<mint>/evidence         -> per-launch drill-down (Phase 3/5)
"""
from __future__ import annotations

from flask import Blueprint, jsonify

watchtower_evidence_bp = Blueprint("watchtower_evidence", __name__)


def _conn():
    import sqlite3
    from src.core.db import OPS_DB_PATH
    conn = sqlite3.connect(str(OPS_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


@watchtower_evidence_bp.route("/api/watchtower/evidence-integrity")
def api_watchtower_evidence_integrity():
    from src.ops.watchtower_evidence_status import evidence_integrity_summary
    conn = _conn()
    try:
        summary = evidence_integrity_summary(conn)
    finally:
        conn.close()
    return jsonify({"ok": True, **summary})


@watchtower_evidence_bp.route("/api/watchtower/launch/<mint>/evidence")
def api_watchtower_launch_evidence(mint: str):
    from src.ops.watchtower_evidence_status import launch_detail
    conn = _conn()
    try:
        detail = launch_detail(conn, mint)
    finally:
        conn.close()
    if not detail:
        return jsonify({"ok": False, "error": "launch not found"}), 404
    return jsonify({"ok": True, "launch": detail})


def register_watchtower_evidence_routes(app: object) -> None:
    app.register_blueprint(watchtower_evidence_bp)
