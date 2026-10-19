"""X74.1 Treasury Review & Identity Expansion Workspace — HTTP routes.

GET  /intelligence/treasury-review                       → analyst workspace page
GET  /api/ops/treasury-review                             → workspace payload (queue + counts)
GET  /api/ops/treasury-review/<treasury>                  → single item detail
POST /api/ops/treasury-review/<treasury>/action           → perform a review action
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

treasury_review_bp = Blueprint("treasury_review", __name__)


def _conn():
    import sqlite3
    from src.core.db import OPS_DB_PATH
    from src.utils.db_locking import db_connect
    conn = db_connect(str(OPS_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


@treasury_review_bp.route("/intelligence/treasury-review")
def treasury_review_page():
    return render_template("treasury_review.html", active_page="treasury_review")


@treasury_review_bp.route("/api/ops/treasury-review")
def api_treasury_review_list():
    from src.ops.treasury_review_workspace import list_review_workspace
    status = (request.args.get("status") or "PENDING_REVIEW").strip()
    sort = (request.args.get("sort") or "actionable").strip()
    limit = min(max(int(request.args.get("limit", 20)), 1), 100)
    offset = max(int(request.args.get("offset", 0)), 0)
    conn = _conn()
    try:
        payload = list_review_workspace(
            conn, status=status, sort=sort, limit=limit, offset=offset
        )
    finally:
        conn.close()
    return jsonify({"ok": True, **payload})


@treasury_review_bp.route("/api/ops/treasury-review/<treasury>")
def api_treasury_review_detail(treasury: str):
    from src.core import treasury_bank
    from src.ops.treasury_review_workspace import compose_review_item, ensure_schema
    conn = _conn()
    try:
        ensure_schema(conn)
        treasury_bank._ensure_schema_once(conn)
        row = conn.execute("SELECT * FROM wt_treasury_review WHERE treasury=?", (treasury,)).fetchone()
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        item = compose_review_item(conn, dict(row))
    finally:
        conn.close()
    return jsonify({"ok": True, "item": item})


@treasury_review_bp.route("/api/ops/treasury-review/<treasury>/action", methods=["POST"])
def api_treasury_review_action(treasury: str):
    from src.ops.treasury_review_workspace import perform_action, WorkspaceError
    body = request.get_json(silent=True) or {}
    action = body.get("action")
    conn = _conn()
    try:
        result = perform_action(conn, treasury, action, body)
        return jsonify({"ok": True, **result})
    except WorkspaceError as exc:
        return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "code": "TREASURY_REVIEW_ERROR"}), 500
    finally:
        conn.close()


def register_treasury_review_routes(app: object) -> None:
    app.register_blueprint(treasury_review_bp)
