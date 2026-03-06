"""
PATCH: Funder Webhook Monitoring System

This patch adds:
1. SQLite schema for funder_watchlist, funder_webhook_groups, funder_webhook_events
2. Funder watchlist builder job (scores funders by risk)
3. Webhook receiver endpoint (/api/webhook/funder)
4. Database schema migration
5. UI endpoints for watchlist management

Expected scale:
- Monitor 50-500 "curated" funders (not all funders, to avoid noise)
- Group them by risk tier (CRITICAL/HIGH/MEDIUM/LOW)
- Webhooks are ~0 cost (1 credit per connection)
- Dramatically more signal than polling all funders

Usage:
1. Apply SQL schema patch to _ensure_db()
2. Create funder_watchlist_builder.py script
3. Add webhook receiver endpoint to main.py
4. Run initial watchlist builder
5. Configure Helius webhook(s) to post to /api/webhook/funder
"""

import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple
import asyncio

# ============================================================================
# PATCH 1: Database Schema (add to _ensure_db() in pumpfun_curve_listener.py)
# ============================================================================

def ensure_funder_webhook_schema(db_path: str):
    """Ensure funder webhook tables exist."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Funder watchlist: curated list of funders to monitor with webhooks
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS funder_watchlist (
            funder_address TEXT PRIMARY KEY,
            risk_score INTEGER DEFAULT 0,  -- 0-1000, higher = more risky
            risk_reasons TEXT,             -- JSON array of strings (why they're monitored)
            first_added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1,   -- 0/1, whether to monitor
            webhook_group_id TEXT          -- which webhook group they're assigned to
        )
    """)

    # Webhook groups: organize funders into buckets for webhook management
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS funder_webhook_groups (
            webhook_group_id TEXT PRIMARY KEY,
            description TEXT,              -- e.g., "CRITICAL", "HIGH", "MEDIUM", "LOW"
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active INTEGER DEFAULT 1,   -- toggle entire group on/off
            helius_webhook_id TEXT         -- Helius webhook ID (when created)
        )
    """)

    # Funder webhook events: ingest funder transactions from Helius webhooks
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS funder_webhook_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            funder_address TEXT NOT NULL,
            signature TEXT NOT NULL,
            slot INTEGER,
            block_time INTEGER,
            direction TEXT,                -- "IN" (received) or "OUT" (sent)
            counterparty TEXT,             -- address they transacted with
            amount_sol REAL,               -- SOL amount
            mint TEXT,                     -- token mint (if token transfer, else NULL)
            raw_payload TEXT,              -- full Helius webhook payload (JSON)
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(signature, funder_address)  -- prevent duplicate events
        )
    """)

    # Indexes for performance
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_watchlist_active ON funder_watchlist(is_active)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_watchlist_group ON funder_watchlist(webhook_group_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_webhook_events_funder ON funder_webhook_events(funder_address)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_webhook_events_block_time ON funder_webhook_events(block_time DESC)")

    conn.commit()
    conn.close()
    print("[SCHEMA] ✅ Funder webhook tables ensured", flush=True)


# ============================================================================
# PATCH 2: Funder Watchlist Builder (new file: funder_watchlist_builder.py)
# ============================================================================

"""
File: funder_watchlist_builder.py

Identifies and scores funders for webhook monitoring based on:
1. Rugged creator funding (funded creators that later rugged)
2. Multi-creator funding (funds many creators)
3. Fingerprint cluster membership (in cluster with malicious wallets)

Run periodically (e.g., every 6 hours) or on-demand.
"""

DB_PATH = "flex_complete_database.db"

# Risk tier assignments (webhook grouping)
RISK_TIERS = {
    "CRITICAL": (800, 1000),
    "HIGH": (500, 799),
    "MEDIUM": (200, 499),
    "LOW": (0, 199),
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def compute_funder_risk_score(conn: sqlite3.Connection, funder_address: str) -> Tuple[int, List[str]]:
    """
    Compute risk score for a funder (0-1000).
    Returns: (score, list of reasons)
    """
    reasons = []
    score = 0

    # Rule 1: Check if funder funded rugged creators
    cursor = conn.execute("""
        SELECT COUNT(DISTINCT cf.creator_address) as rugged_creator_count
        FROM creator_funders cf
        JOIN creator_blocklist cb ON cf.creator_address = cb.creator_address
        WHERE cf.funder_address = ?
    """, (funder_address,))

    row = cursor.fetchone()
    rugged_count = row[0] if row else 0

    if rugged_count >= 6:
        score += 400
        reasons.append(f"Funded {rugged_count} rugged creators")
    elif rugged_count >= 3:
        score += 200
        reasons.append(f"Funded {rugged_count} rugged creators")
    elif rugged_count >= 1:
        score += 80
        reasons.append(f"Funded {rugged_count} rugged creator(s)")

    # Rule 2: Check multi-creator funding (many creators in short time window)
    cursor = conn.execute("""
        SELECT COUNT(DISTINCT creator_address) as creator_count
        FROM creator_funders
        WHERE funder_address = ?
    """, (funder_address,))

    row = cursor.fetchone()
    creator_count = row[0] if row else 0

    if creator_count >= 50:
        score += 300
        reasons.append(f"Funds {creator_count} distinct creators (hub behavior)")
    elif creator_count >= 20:
        score += 150
        reasons.append(f"Funds {creator_count} distinct creators")
    elif creator_count >= 10:
        score += 60
        reasons.append(f"Funds {creator_count} creators")

    # Rule 3: Check fingerprint cluster membership
    # (Assumes super_clusters table exists with clustering data)
    try:
        cursor = conn.execute("""
            SELECT COUNT(*) as malicious_in_cluster
            FROM super_clusters sc1
            JOIN creator_blocklist cb ON sc1.creator_address = cb.creator_address
            WHERE sc1.cluster_id = (
                SELECT cluster_id FROM super_clusters WHERE creator_address = ? LIMIT 1
            ) AND sc1.creator_address != ?
        """, (funder_address, funder_address))

        row = cursor.fetchone()
        malicious_count = row[0] if row else 0

        if malicious_count >= 3:
            score += 250
            reasons.append(f"In cluster with {malicious_count} blocklisted creators")
    except:
        pass  # super_clusters may not exist yet

    # Rule 4: Check if funder is marked as CEX/infra (reduce score)
    try:
        cursor = conn.execute("""
            SELECT is_cex FROM creator_funders WHERE funder_address = ? LIMIT 1
        """, (funder_address,))
        row = cursor.fetchone()
        if row and row[0]:
            score = max(0, score - 200)  # Penalize CEX wallets
            reasons.append("(CEX/infra wallet - score reduced)")
    except:
        pass

    # Cap score at 1000
    score = min(score, 1000)

    return score, reasons


def assign_to_webhook_group(score: int) -> str:
    """Assign funder to webhook group based on risk score."""
    for tier, (min_score, max_score) in RISK_TIERS.items():
        if min_score <= score <= max_score:
            return tier
    return "LOW"


def ensure_webhook_groups(conn: sqlite3.Connection):
    """Ensure webhook groups exist."""
    cursor = conn.cursor()
    for tier in RISK_TIERS.keys():
        cursor.execute("""
            INSERT OR IGNORE INTO funder_webhook_groups
            (webhook_group_id, description, is_active)
            VALUES (?, ?, 1)
        """, (tier, f"{tier} Risk Funder Tier"))
    conn.commit()


def rebuild_funder_watchlist():
    """
    Rebuild funder watchlist from scratch.
    Called periodically (e.g., every 6 hours) to update scores and assignments.
    """
    conn = get_db()
    cursor = conn.cursor()

    print("[WATCHLIST_BUILDER] 🚀 Starting watchlist rebuild...", flush=True)

    # Ensure webhook groups exist
    ensure_webhook_groups(conn)

    # Get all known funders
    cursor.execute("SELECT DISTINCT funder_address FROM creator_funders")
    funders = [row[0] for row in cursor.fetchall()]

    print(f"[WATCHLIST_BUILDER] Scoring {len(funders)} funders...", flush=True)

    updated_count = 0
    added_count = 0

    for funder_address in funders:
        score, reasons = compute_funder_risk_score(conn, funder_address)

        # Only add if score is above threshold (> 50)
        if score > 50:
            webhook_group = assign_to_webhook_group(score)

            # Check if already exists
            cursor.execute("""
                SELECT funder_address FROM funder_watchlist WHERE funder_address = ?
            """, (funder_address,))
            exists = cursor.fetchone()

            if exists:
                cursor.execute("""
                    UPDATE funder_watchlist
                    SET risk_score = ?, risk_reasons = ?, webhook_group_id = ?,
                        last_updated_at = CURRENT_TIMESTAMP
                    WHERE funder_address = ?
                """, (score, json.dumps(reasons), webhook_group, funder_address))
                updated_count += 1
            else:
                cursor.execute("""
                    INSERT INTO funder_watchlist
                    (funder_address, risk_score, risk_reasons, webhook_group_id, is_active)
                    VALUES (?, ?, ?, ?, 1)
                """, (funder_address, score, json.dumps(reasons), webhook_group))
                added_count += 1

    conn.commit()
    print(f"[WATCHLIST_BUILDER] ✅ Watchlist rebuilt: {added_count} added, {updated_count} updated", flush=True)

    # Summarize by tier
    for tier in RISK_TIERS.keys():
        cursor.execute("""
            SELECT COUNT(*) FROM funder_watchlist
            WHERE webhook_group_id = ? AND is_active = 1
        """, (tier,))
        count = cursor.fetchone()[0]
        print(f"[WATCHLIST_BUILDER]   {tier}: {count} funders", flush=True)

    conn.close()


if __name__ == "__main__":
    rebuild_funder_watchlist()


# ============================================================================
# PATCH 3: Webhook Receiver Endpoint (add to main.py)
# ============================================================================

from flask import request, jsonify
import sqlite3

@app.route('/api/webhook/funder', methods=['POST'])
def webhook_funder_event():
    """
    Receive funder webhook events from Helius.

    Helius event format:
    {
        "signature": "...",
        "slot": 12345,
        "blockTime": 1234567890,
        "source": "...",
        "destination": "...",
        "nativeTransfers": [
            {
                "fromUserAccount": "...",
                "toUserAccount": "...",
                "amount": 1000000  # lamports
            }
        ],
        "tokenTransfers": [...],
        "mint": "..." (if token transfer)
    }
    """
    try:
        payload = request.get_json()

        if not payload:
            return jsonify({"error": "empty payload"}), 400

        signature = payload.get("signature")
        slot = payload.get("slot")
        block_time = payload.get("blockTime")
        source = payload.get("source")
        destination = payload.get("destination")
        mint = payload.get("mint")

        if not signature or not source or not destination:
            return jsonify({"error": "missing required fields"}), 400

        # Determine direction and counterparty
        direction = None
        counterparty = None
        amount_sol = 0

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Check if source is a watched funder (transfer OUT)
        cursor.execute("""
            SELECT 1 FROM funder_watchlist
            WHERE funder_address = ? AND is_active = 1
        """, (source,))

        if cursor.fetchone():
            direction = "OUT"
            counterparty = destination
            # Extract SOL amount from nativeTransfers
            native_transfers = payload.get("nativeTransfers", [])
            if native_transfers:
                amount_sol = sum(t.get("amount", 0) for t in native_transfers) / 1e9  # lamports to SOL

        # Check if destination is a watched funder (transfer IN)
        cursor.execute("""
            SELECT 1 FROM funder_watchlist
            WHERE funder_address = ? AND is_active = 1
        """, (destination,))

        if cursor.fetchone():
            direction = "IN"
            counterparty = source
            native_transfers = payload.get("nativeTransfers", [])
            if native_transfers:
                amount_sol = sum(t.get("amount", 0) for t in native_transfers) / 1e9

        if not direction:
            conn.close()
            return jsonify({"status": "ok"}), 200  # Funder not in watchlist, skip

        # Insert event (dedupe by UNIQUE constraint on signature + funder_address)
        funder = source if direction == "OUT" else destination
        try:
            cursor.execute("""
                INSERT INTO funder_webhook_events
                (funder_address, signature, slot, block_time, direction, counterparty, amount_sol, mint, raw_payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (funder, signature, slot, block_time, direction, counterparty, amount_sol, mint, json.dumps(payload)))
            conn.commit()
            print(f"[WEBHOOK_FUNDER] ✅ {direction}: {funder[:8]}... <-> {counterparty[:8]}... ({amount_sol:.4f} SOL)", flush=True)
        except sqlite3.IntegrityError:
            # Duplicate event, silently skip
            pass
        finally:
            conn.close()

        return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"[WEBHOOK_FUNDER] ⚠ Error: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ============================================================================
# PATCH 4: UI Endpoints for Watchlist Management (add to main.py)
# ============================================================================

@app.route('/api/funder-watchlist/summary')
def funder_watchlist_summary():
    """Get summary of funder watchlist by risk tier."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        summary = {}
        for tier in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            cursor.execute("""
                SELECT COUNT(*) as count, SUM(risk_score) as total_risk
                FROM funder_watchlist
                WHERE webhook_group_id = ? AND is_active = 1
            """, (tier,))
            row = cursor.fetchone()
            summary[tier] = {
                "count": row[0] if row else 0,
                "total_risk_score": row[1] if row else 0,
            }

        conn.close()
        return jsonify(summary)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/funder-watchlist/top-risky')
def funder_watchlist_top_risky():
    """Get top 20 most risky funders."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT funder_address, risk_score, webhook_group_id, risk_reasons
            FROM funder_watchlist
            WHERE is_active = 1
            ORDER BY risk_score DESC
            LIMIT 20
        """)

        rows = cursor.fetchall()
        result = []
        for row in rows:
            risk_reasons = json.loads(row[3]) if row[3] else []
            result.append({
                "funder_address": row[0],
                "risk_score": row[1],
                "risk_tier": row[2],
                "risk_reasons": risk_reasons,
            })

        conn.close()
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/funder-webhook-events')
def funder_webhook_events():
    """Get recent funder webhook events (paginated)."""
    try:
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, funder_address, signature, block_time, direction,
                   counterparty, amount_sol, mint, ingested_at
            FROM funder_webhook_events
            ORDER BY ingested_at DESC
            LIMIT ? OFFSET ?
        """, (limit, offset))

        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "id": row[0],
                "funder_address": row[1],
                "signature": row[2],
                "block_time": row[3],
                "direction": row[4],
                "counterparty": row[5],
                "amount_sol": row[6],
                "mint": row[7],
                "ingested_at": row[8],
            })

        conn.close()
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# PATCH 5: Integration Checklist
# ============================================================================

"""
IMPLEMENTATION CHECKLIST:

1. Database Schema
   [ ] Add schema via ensure_funder_webhook_schema() to _ensure_db()
   [ ] Run on next listener startup
   [ ] Verify tables created: funder_watchlist, funder_webhook_groups, funder_webhook_events

2. Watchlist Builder
   [ ] Create funder_watchlist_builder.py file
   [ ] Test on dev database: python funder_watchlist_builder.py
   [ ] Verify risk scores assigned correctly
   [ ] Set up scheduled task (e.g., cron) to run every 6 hours

3. Webhook Receiver
   [ ] Add /api/webhook/funder endpoint to main.py
   [ ] Test with curl:
       curl -X POST http://localhost:5002/api/webhook/funder \
         -H "Content-Type: application/json" \
         -d '{"signature":"...", "source":"...", "destination":"...", "blockTime":123}'
   [ ] Verify events stored in funder_webhook_events table

4. Helius Configuration
   [ ] Create webhook(s) in Helius dashboard for watched funders
   [ ] Configure webhook URL: http://your-server/api/webhook/funder
   [ ] Select desired event types (SOL_TRANSFER, TOKEN_TRANSFER, etc.)
   [ ] Test webhook delivery

5. UI Integration
   [ ] Add endpoints: /api/funder-watchlist/summary, /api/funder-watchlist/top-risky, /api/funder-webhook-events
   [ ] Create dashboard panel showing:
      - Watchlist size by tier
      - Top risky funders
      - Recent events stream
      - Risk reason explanations

EXPECTED SCALE:

Before (polling model):
- Query all creators' funders: ~1,000-10,000 addresses
- Polling interval: every 1-6 hours
- RPC cost: 100-1000 getSignaturesForAddress calls per cycle @ 10 credits each = 1,000-10,000 credits

After (webhook model):
- Monitor ~50-500 curated funders (high-signal only)
- Webhook delivery: ~0 cost (1 credit per connection, then events are free)
- RPC cost: ~10 credits per webhook setup, then essentially 0

SAVINGS: 90-99% reduction in RPC credits for funder monitoring
"""
