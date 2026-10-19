"""
Operations OS — Inbox API Routes.

GET  /api/ops/inbox                     → active items (all ops, sorted by priority)
GET  /api/ops/inbox/summary             → count summary for Mission Control
GET  /api/ops/inbox/<op_id>             → active items for one operation
POST /api/ops/inbox/<item_id>/status    → update item status
POST /api/ops/inbox/refresh             → trigger adapter refresh (idempotent)
GET  /intelligence/inbox                → inbox page
"""

from __future__ import annotations

import time
from flask import Blueprint, jsonify, request, render_template

from src.ops.inbox import InboxStore, STATUSES_VALID
from src.ops.inbox_adapters import refresh_inbox

inbox_bp = Blueprint("inbox", __name__)

_store: InboxStore | None = None


def _get_store() -> InboxStore:
    global _store
    if _store is None:
        from src.core.db import OPS_DB_PATH
        _store = InboxStore(str(OPS_DB_PATH))
    return _store


# ── API routes ────────────────────────────────────────────────────────────────

@inbox_bp.route("/api/ops/inbox")
def inbox_all():
    store = _get_store()
    limit = min(int(request.args.get("limit", 50)), 200)
    items = store.fetch_active(limit=limit)
    return jsonify({
        "ok":        True,
        "items":     items,
        "count":     len(items),
        "generated_at": int(time.time()),
    })


@inbox_bp.route("/api/ops/inbox/summary")
def inbox_summary():
    store = _get_store()
    summary = store.fetch_summary()
    summary["ok"] = True
    summary["generated_at"] = int(time.time())
    return jsonify(summary)


@inbox_bp.route("/api/ops/inbox/<op_id>")
def inbox_for_op(op_id: str):
    store = _get_store()
    limit = min(int(request.args.get("limit", 50)), 200)
    items = store.fetch_active(operation_id=op_id, limit=limit)
    return jsonify({
        "ok":        True,
        "operation": op_id,
        "items":     items,
        "count":     len(items),
        "generated_at": int(time.time()),
    })


@inbox_bp.route("/api/ops/inbox/<item_id>/status", methods=["POST"])
def set_item_status(item_id: str):
    data   = request.get_json(silent=True) or {}
    status = data.get("status", "")
    if status not in STATUSES_VALID:
        return jsonify({"ok": False, "error": f"Invalid status: {status!r}"}), 400
    store = _get_store()
    ok = store.set_status(item_id, status)
    return jsonify({"ok": ok})


@inbox_bp.route("/api/ops/inbox/refresh", methods=["POST"])
def inbox_refresh():
    store   = _get_store()
    written = refresh_inbox(store)
    return jsonify({
        "ok":          True,
        "items_written": written,
        "generated_at":  int(time.time()),
    })


# ── Page route ────────────────────────────────────────────────────────────────

@inbox_bp.route("/intelligence/inbox")
def inbox_page():
    return render_template("inbox.html", active_page="inbox")


# ── Registration ──────────────────────────────────────────────────────────────

def register_inbox_routes(app) -> None:
    app.register_blueprint(inbox_bp)
