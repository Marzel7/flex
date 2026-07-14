"""Read-only HTTP routes for X21B provisioning-relationship facts.

These endpoints expose only what has been persisted by
src.ops.provisioning_edges — observed funding edges, provisioning sessions,
and plain-mean timing summaries. No operator identity, no confidence, no
interpretation. Consumers (e.g. a future Treasury Review Lead explanation
panel) are responsible for any analyst-facing framing.
"""
from __future__ import annotations

import os


def register_provisioning_edges_routes(app: object) -> None:
    from flask import jsonify

    from src.ops.provisioning_edges import (
        edges_for_wallet,
        sessions_for_wallet,
        timing_summary_for_wallet,
    )

    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ops_db_path = os.environ.get(
        "WT_OPS_DB_PATH", os.path.join(repo, "database", "wt_ops_v2.db")
    )

    def _connect():
        import sqlite3
        conn = sqlite3.connect(f"file:{ops_db_path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn

    @app.route("/api/ops-v2/provisioning-edges/<path:wallet>")  # type: ignore[attr-defined]
    def provisioning_edges_for_wallet(wallet: str):
        if not os.path.exists(ops_db_path):
            return jsonify({"ok": True, "wallet": wallet, "outgoing": [], "incoming": []})
        conn = _connect()
        try:
            result = edges_for_wallet(conn, wallet)
        finally:
            conn.close()
        return jsonify({"ok": True, "wallet": wallet, **result})

    @app.route("/api/ops-v2/provisioning-sessions/<path:wallet>")  # type: ignore[attr-defined]
    def provisioning_sessions_for_wallet(wallet: str):
        if not os.path.exists(ops_db_path):
            return jsonify({"ok": True, "wallet": wallet, "sessions": []})
        conn = _connect()
        try:
            sessions = sessions_for_wallet(conn, wallet)
        finally:
            conn.close()
        return jsonify({"ok": True, "wallet": wallet, "sessions": sessions})

    @app.route("/api/ops-v2/provisioning-timing/<path:wallet>")  # type: ignore[attr-defined]
    def provisioning_timing_for_wallet(wallet: str):
        if not os.path.exists(ops_db_path):
            return jsonify({"ok": True, "wallet": wallet, "session_count": 0})
        conn = _connect()
        try:
            summary = timing_summary_for_wallet(conn, wallet)
        finally:
            conn.close()
        return jsonify({"ok": True, "wallet": wallet, **summary})
