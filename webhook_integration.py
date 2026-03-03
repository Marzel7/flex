"""
FLEX Webhook Integration
Wires webhook handler and worker into existing Flask application

Author: Claude Code
Date: 2026-03-03
"""

from flask import request, jsonify
from webhook_handler import (
    handle_helius_webhook,
    init_webhook,
)
import threading


def setup_webhook_routes(app):
    """
    Register webhook routes with Flask app.

    Call this during app initialization:
        setup_webhook_routes(app)

    Args:
        app: Flask application instance
    """

    @app.route("/helius/webhook", methods=["POST"])
    def helius_webhook_route():
        """POST /helius/webhook - Helius RAW webhook endpoint"""
        response_text, status_code = handle_helius_webhook(request)
        return response_text, status_code

    @app.route("/api/webhook/status", methods=["GET"])
    def webhook_status_route():
        """GET /api/webhook/status - Webhook health and stats"""
        import sqlite3
        import os

        db_path = os.getenv("FLEX_DB_PATH", "flex_complete_database.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        cur = conn.cursor()

        # Stats from sol_transfers
        cur.execute("SELECT COUNT(*) as cnt FROM sol_transfers")
        total_transfers = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) as cnt FROM sol_transfers
            WHERE block_time > (strftime('%s', 'now') - 3600)
        """)
        transfers_1h = cur.fetchone()[0]

        # Stats from work_queue
        cur.execute("SELECT COUNT(*) as cnt FROM work_queue")
        queue_size = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) as cnt FROM work_queue
            WHERE priority >= 50
        """)
        high_priority = cur.fetchone()[0]

        conn.close()

        return jsonify({
            "ok": True,
            "total_transfers": total_transfers,
            "transfers_1h": transfers_1h,
            "queue_size": queue_size,
            "high_priority_count": high_priority,
        }), 200

    print("[WEBHOOK_INTEGRATION] Routes registered: /helius/webhook, /api/webhook/status", flush=True)


def start_webhook_worker(daemon=True):
    """
    Start webhook worker in background thread.

    Call this after Flask app is initialized:
        start_webhook_worker()

    Args:
        daemon: If True, worker thread won't block app shutdown
    """
    from webhook_worker import run_worker

    worker_thread = threading.Thread(target=run_worker, daemon=daemon)
    worker_thread.start()

    print("[WEBHOOK_INTEGRATION] Worker thread started", flush=True)

    return worker_thread


def init_webhook_system(app):
    """
    Complete webhook system initialization.

    Call this during Flask app startup:
        init_webhook_system(app)

    Args:
        app: Flask application instance
    """
    init_webhook()
    setup_webhook_routes(app)
    start_webhook_worker(daemon=True)

    print("[WEBHOOK_INTEGRATION] Webhook system initialized", flush=True)
