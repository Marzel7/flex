"""
FLEX Webhook Integration
Wires webhook handler and worker into existing Flask application

Author: Claude Code
Date: 2026-03-03
"""

from flask import request, jsonify
from src.core.webhook_handler import (
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
        import time
        from datetime import datetime

        db_path = os.getenv("DB_PATH", "database/flex_complete_database.db")
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row

        cur = conn.cursor()

        # Count unique signatures (webhooks received)
        cur.execute("SELECT COUNT(DISTINCT signature) as cnt FROM sol_transfers")
        total_signatures = cur.fetchone()[0]

        # Stats from sol_transfers
        cur.execute("SELECT COUNT(*) as cnt FROM sol_transfers")
        total_transfers = cur.fetchone()[0]

        # Transfers from last 24 hours
        now_timestamp = int(time.time())
        one_day_ago = now_timestamp - 86400
        cur.execute("""
            SELECT COUNT(*) as cnt FROM sol_transfers
            WHERE block_time > ?
        """, (one_day_ago,))
        transfers_today = cur.fetchone()[0]

        # Last webhook timestamp
        cur.execute("""
            SELECT MAX(block_time) as last_time FROM sol_transfers
        """)
        last_block_time = cur.fetchone()[0]

        last_webhook = None
        if last_block_time:
            last_webhook = datetime.utcfromtimestamp(last_block_time).strftime('%Y-%m-%dT%H:%M:%S')

        # Recent transfers (last 10) with creator status and labels
        cur.execute("""
            SELECT st.source, st.destination, st.amount_sol, st.signature, st.block_time,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM helius_webhook_assignments hwa
                       WHERE hwa.creator_address = st.source
                   ) THEN 1 ELSE 0 END as sender_is_creator,
                   CASE WHEN EXISTS (
                       SELECT 1 FROM helius_webhook_assignments hwa
                       WHERE hwa.creator_address = st.destination
                   ) THEN 1 ELSE 0 END as receiver_is_creator
            FROM sol_transfers st
            ORDER BY st.block_time DESC
            LIMIT 10
        """)
        recent_rows = cur.fetchall()

        recent_transfers = []
        for row in recent_rows:
            source = row[0]
            dest = row[1]

            # Look up labels for sender (priority: custom > CEX > INFRA)
            sender_label = None
            cur.execute("SELECT label_name FROM address_labels WHERE address = ? LIMIT 1", (source,))
            r = cur.fetchone()
            if r:
                sender_label = r[0]
            else:
                cur.execute("SELECT exchange_name FROM cex_wallets WHERE cex_address = ? LIMIT 1", (source,))
                r = cur.fetchone()
                if r:
                    sender_label = f"{r[0]} (CEX)"
                else:
                    cur.execute("SELECT 1 FROM infra_funders_observed WHERE funder_address = ? LIMIT 1", (source,))
                    if cur.fetchone():
                        sender_label = "INFRA"

            # Look up labels for receiver (same priority)
            receiver_label = None
            cur.execute("SELECT label_name FROM address_labels WHERE address = ? LIMIT 1", (dest,))
            r = cur.fetchone()
            if r:
                receiver_label = r[0]
            else:
                cur.execute("SELECT exchange_name FROM cex_wallets WHERE cex_address = ? LIMIT 1", (dest,))
                r = cur.fetchone()
                if r:
                    receiver_label = f"{r[0]} (CEX)"
                else:
                    cur.execute("SELECT 1 FROM infra_funders_observed WHERE funder_address = ? LIMIT 1", (dest,))
                    if cur.fetchone():
                        receiver_label = "INFRA"

            recent_transfers.append({
                "sender": sender_label or source,
                "receiver": receiver_label or dest,
                "sender_address": source,
                "receiver_address": dest,
                "amount_sol": round(row[2], 9),
                "signature": row[3],
                "timestamp": row[4],
                "sender_is_creator": bool(row[5]),
                "receiver_is_creator": bool(row[6]),
                "sender_has_label": bool(sender_label),
                "receiver_has_label": bool(receiver_label)
            })

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
            "total_signatures": total_signatures,
            "total_transfers": total_transfers,
            "transfers_today": transfers_today,
            "last_webhook": last_webhook,
            "recent_transfers": recent_transfers,
            "transfers_1h": 0,  # Kept for compatibility
            "queue_size": queue_size,
            "high_priority_count": high_priority,
        }), 200

    @app.route("/api/webhook/usage-snapshot", methods=["GET"])
    def webhook_usage_snapshot_route():
        """GET /api/webhook/usage-snapshot - Helius webhook usage metrics"""
        import sqlite3
        import os
        from datetime import datetime

        db_path = os.getenv("DB_PATH", "database/flex_complete_database.db")
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row

        cur = conn.cursor()

        # Get latest usage snapshot from helius_usage_snapshots table
        cur.execute("""
            SELECT timestamp, credits_remaining, credits_used, credits_used_month, project_id
            FROM helius_usage_snapshots
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        usage_row = cur.fetchone()

        # Get webhook stats
        cur.execute("SELECT COUNT(DISTINCT signature) as cnt FROM sol_transfers")
        total_webhooks = cur.fetchone()[0]

        conn.close()

        response = {
            "ok": True,
            "timestamp": datetime.utcnow().isoformat(),
            "project_id": os.getenv("HELIUS_PROJECT_ID", "unknown"),
            "webhook_usage": {
                "total_webhooks_received": total_webhooks,
            }
        }

        if usage_row:
            response["credits"] = {
                "remaining": usage_row["credits_remaining"],
                "used": usage_row["credits_used"],
                "used_this_month": usage_row["credits_used_month"],
                "snapshot_timestamp": usage_row["timestamp"]
            }
        else:
            response["credits"] = {
                "remaining": None,
                "used": None,
                "used_this_month": None,
                "snapshot_timestamp": None,
                "note": "No usage snapshots recorded yet. Run: python helius_usage_cli.py update <remaining> <used> <used_month>"
            }

        return jsonify(response), 200

    print("[WEBHOOK_INTEGRATION] Routes registered: /helius/webhook, /api/webhook/status, /api/webhook/usage-snapshot", flush=True)


def start_webhook_worker(daemon=True):
    """
    Start webhook worker in background thread.

    Call this after Flask app is initialized:
        start_webhook_worker()

    Args:
        daemon: If True, worker thread won't block app shutdown
    """
    from src.core.webhook_worker import run_worker

    worker_thread = threading.Thread(target=run_worker, daemon=daemon)
    worker_thread.start()

    print("[WEBHOOK_INTEGRATION] Worker thread started", flush=True)

    return worker_thread


def get_webhook_event_metrics(minutes: int = 5):
    """Get webhook event metrics for the last N minutes"""
    import os
    import sqlite3
    from datetime import datetime, timedelta

    db_path = os.getenv("DB_PATH", "database/flex_complete_database.db")
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Events in last N minutes
    cutoff = int((datetime.now() - timedelta(minutes=minutes)).timestamp())
    cur.execute("""
        SELECT COUNT(*) as event_count,
               COUNT(DISTINCT source) as unique_creators,
               COUNT(DISTINCT signature) as unique_signatures
        FROM sol_transfers
        WHERE block_time > ?
    """, (cutoff,))

    row = cur.fetchone()
    conn.close()

    return {
        'event_count': row[0] or 0,
        'unique_creators': row[1] or 0,
        'unique_signatures': row[2] or 0
    }

def get_top_webhook_creators(limit: int = 10, hours: int = 24):
    """Get top creators by webhook event count"""
    import os
    import sqlite3
    from datetime import datetime, timedelta

    db_path = os.getenv("DB_PATH", "database/flex_complete_database.db")
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cutoff = int((datetime.now() - timedelta(hours=hours)).timestamp())
    cur.execute("""
        SELECT source as creator, COUNT(*) as event_count, SUM(amount_sol) as total_sol
        FROM sol_transfers
        WHERE block_time > ?
        GROUP BY source
        ORDER BY event_count DESC
        LIMIT ?
    """, (cutoff, limit))

    creators = [{
        'address': row[0],
        'event_count': row[1],
        'total_sol': row[2] or 0
    } for row in cur.fetchall()]

    conn.close()
    return creators

_WT_PERMANENT_INFRA = [
    "44orWS68MqXG198M3YXyZoNrYtsNhgnNhtUT5SavqJFM",  # TREASURY
    "44orA1BxQfFaX2iMjRbWstoqqWBE7ag8BD93ikxR4JFM",  # SIGNALLER
    "44o1Hecb4QUhqcRNYJBC6XZoeHWzkWAvenR5YYHRGbFM",  # SIGNALLER_2
    "6jeT3WyrfwLxox3yAmchDg7ZQvS8XK8XXbkviPUudUW1",  # TREASURY_UP
    "N3TKf3wMBNu8XmZsTSnk2xWQ2LjiGvUJh1ae9Lc3dW7",   # SUB_PROV (reclassified from PROFIT_RELAY)
    "8g2qFR27iDPZimUpmaySqg8TgmNKTDAwimEdBB6w96mn",  # TRADING_MGR
    "2ujRcf1fwQjW8cjUPK6krBJBMdbiMiSKvNscYjdbFW6R", # SUB_PROV (800 SOL 2026-05-30)
    "CcdyBAT7L2cbfBYNGAdJffUrZibCKsXEmq3q6cVxTNUV",  # SUB_PROV
    "Dw7xNxxwuBnTfpwSrZMhcAQqFp4XNe9XNaVhhsvyx6Da",  # SUB_PROV
    "96b4e8qvhjEPsXDtenEgs1VLBTkFqAcgYV8tE16Mgt7h",  # SUB_PROV
    "kFycb9QoQaRgLy3zZpF4Zw5DM5gKoT5HkZogSerq1Hd",   # SUB_PROV
    "C745erBxwn4sJZGDRZpi71FPV3MA3kBQUXWbeJxRsGS4",  # SUB_PROV
    "Gs7zXNYwdd2X1PoyBbsJBCuNTz6EyTT5KSd38tLMEmif",  # SUB_PROV
    "2vBd5o7ppBLVXo2TyTfSXhWgK9LMKxEszuNFLU7ZKnUz",  # SUB_PROV: capital redistribution hub, most-signalled (27x)
]


def _enroll_permanent_infra():
    """Ensure all permanent infra addresses are in the INFRA webhook at startup."""
    import asyncio
    import os
    from src.analysis.webhook_manager import enroll_infra

    db_path = os.getenv("DB_PATH", "flex_complete_database.db")

    async def _run():
        enrolled = 0
        for addr in _WT_PERMANENT_INFRA:
            try:
                result = await enroll_infra(addr, db_path, notes="permanent_infra_startup")
                if result:
                    enrolled += 1
                    print(f"[WEBHOOK_INTEGRATION] Enrolled permanent infra: {addr[:20]}…", flush=True)
            except Exception as e:
                print(f"[WEBHOOK_INTEGRATION] Failed to enroll {addr[:20]}…: {e}", flush=True)
        print(f"[WEBHOOK_INTEGRATION] Permanent infra enrollment: {enrolled}/{len(_WT_PERMANENT_INFRA)} newly enrolled", flush=True)

    try:
        asyncio.run(_run())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_run())
        loop.close()


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
    _enroll_permanent_infra()
    start_webhook_worker(daemon=True)

    print("[WEBHOOK_INTEGRATION] Webhook system initialized", flush=True)
