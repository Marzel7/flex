"""
FLEX Webhook Worker
Priority-based address analyzer with DB-only signals and strict RPC gating

Author: Claude Code
Date: 2026-03-03
"""

import sqlite3
import os
import time
from typing import Optional, Dict, Tuple
from datetime import datetime


# ============================================================================
# CONFIGURATION
# ============================================================================

DB_PATH = os.getenv("FLEX_DB_PATH", "flex_complete_database.db")

# RPC Guardrails
RPC_MIN_PRIORITY = 80
RPC_COOLDOWN_SECONDS = 30 * 60  # 30 minutes
MAX_RPC_CALLS_PER_HOUR = 100
RPC_CALLS_THIS_HOUR = 0
HOUR_START_TIME = int(time.time())

# Worker tuning
LOCK_DURATION = 120  # seconds
BATCH_SIZE = 10
WORKER_SLEEP = 1  # seconds between batches


# ============================================================================
# DATABASE UTILITIES
# ============================================================================

def get_worker_db():
    """Create optimized database connection for worker."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# PRIORITY SCORING
# ============================================================================

def compute_priority(conn: sqlite3.Connection, address: str) -> Tuple[float, str]:
    """
    Compute priority score using DB-only signals.

    Priority = activity + tag + network + multi_token - cooldown

    Args:
        conn: Database connection
        address: Wallet address

    Returns:
        Tuple of (score, reasons_str)
    """
    score = 0.0
    reasons = []

    cur = conn.cursor()
    now = int(time.time())

    # ---- ACTIVITY SIGNALS ----
    cur.execute("""
        SELECT
            last_seen_at,
            tx_5m,
            tx_1h,
            tx_24h,
            sol_in_1h,
            sol_out_1h,
            last_processed_at
        FROM address_activity
        WHERE address = ?
    """, (address,))

    row = cur.fetchone()

    if row:
        last_seen = row[0]  # block_time
        tx_5m = row[1]
        tx_1h = row[2]
        sol_in_1h = row[4]
        sol_out_1h = row[5]
        last_processed = row[6]

        # Recency bonus
        if last_seen and (now - last_seen) < 300:  # < 5 minutes
            score += 50
            reasons.append("active_5m")
        elif last_seen and (now - last_seen) < 3600:  # < 1 hour
            score += 30
            reasons.append("active_1h")

        # Volume bonus
        if tx_1h and tx_1h >= 5:
            score += 20
            reasons.append(f"high_volume_{tx_1h}tx")

        if (sol_in_1h + sol_out_1h) > 10.0:
            score += 15
            reasons.append("high_value")

        # Cooldown penalty (don't process same address too often)
        if last_processed:
            time_since_processed = now - last_processed
            if time_since_processed < 120:  # < 2 minutes
                score -= 50
                reasons.append("cooldown_2m")
            elif time_since_processed < 600:  # < 10 minutes
                score -= 20
                reasons.append("cooldown_10m")

    # ---- TAG SIGNALS ----
    # Try to find tags in existing tables
    try:
        cur.execute("""
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name='creator_tags'
        """)
        if cur.fetchone()[0] > 0:
            cur.execute("""
                SELECT tag FROM creator_tags
                WHERE address = ?
                LIMIT 1
            """, (address,))
            tag_row = cur.fetchone()
            if tag_row:
                tag = tag_row[0]
                if tag in ["watchlist", "known_malicious"]:
                    score += 60
                    reasons.append(f"tag_{tag}")
                elif tag in ["suspicious"]:
                    score += 40
                    reasons.append(f"tag_{tag}")
    except:
        pass

    # ---- NETWORK SIGNALS ----
    # Check if address is in a cluster/network
    try:
        cur.execute("""
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name='super_clusters'
        """)
        if cur.fetchone()[0] > 0:
            cur.execute("""
                SELECT cluster_id FROM super_clusters
                WHERE member_address = ?
                LIMIT 1
            """, (address,))
            cluster_row = cur.fetchone()
            if cluster_row:
                score += 30
                reasons.append("in_cluster")
    except:
        pass

    # Check for connected_to_malicious signal
    try:
        cur.execute("""
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name='coordinated_funders'
        """)
        if cur.fetchone()[0] > 0:
            cur.execute("""
                SELECT COUNT(*) FROM coordinated_funders
                WHERE funder_address = ?
            """, (address,))
            count = cur.fetchone()[0]
            if count > 0:
                score += 20
                reasons.append("coordinated_funder")
    except:
        pass

    # ---- MULTI-TOKEN CREATOR SIGNAL ----
    try:
        cur.execute("""
            SELECT COUNT(*) FROM sqlite_master
            WHERE type='table' AND name='token_analysis'
        """)
        if cur.fetchone()[0] > 0:
            cur.execute("""
                SELECT COUNT(DISTINCT mint) FROM token_analysis
                WHERE creator = ?
            """, (address,))
            mint_count = cur.fetchone()[0]
            if mint_count and mint_count >= 2:
                score += 20
                reasons.append(f"multi_token_{mint_count}")
    except:
        pass

    return (score, " + ".join(reasons) if reasons else "baseline")


# ============================================================================
# WORKER LOOP
# ============================================================================

def fetch_next_work(conn: sqlite3.Connection, batch_size: int = BATCH_SIZE) -> list:
    """
    Fetch next batch of work from queue.

    Selects unlocked, due items by highest priority.
    Locks them for LOCK_DURATION seconds.

    Args:
        conn: Database connection
        batch_size: Number of items to fetch

    Returns:
        List of work_queue rows
    """
    cur = conn.cursor()
    now = int(time.time())

    cur.execute("""
        SELECT address, priority, reason, locked_until
        FROM work_queue
        WHERE locked_until < ?
            AND next_run_at < ?
        ORDER BY priority DESC
        LIMIT ?
    """, (now, now, batch_size))

    rows = cur.fetchall()

    if not rows:
        return []

    # Lock these rows
    addresses = [row[0] for row in rows]
    lock_until = now + LOCK_DURATION

    cur.execute(f"""
        UPDATE work_queue
        SET locked_until = ?
        WHERE address IN ({','.join('?' * len(addresses))})
    """, [lock_until] + addresses)

    conn.commit()

    return rows


def process_work_item(conn: sqlite3.Connection, address: str, priority: float, reason: str) -> bool:
    """
    Process a single work queue item.

    Computes current priority, applies logic, updates activity stats.
    Decides whether to call RPC based on priority.

    Args:
        conn: Database connection
        address: Wallet address
        priority: Current priority score
        reason: Reason for queueing

    Returns:
        True if processing was successful, False otherwise
    """
    cur = conn.cursor()
    now = int(time.time())

    print(f"[WORKER] Processing {address[:8]}... (priority={priority:.1f}, reason={reason})", flush=True)

    # Recompute priority with latest DB signals
    computed_priority, reasons = compute_priority(conn, address)

    print(f"[WORKER] {address[:8]}... computed_priority={computed_priority:.1f} ({reasons})", flush=True)

    # Apply RPC guardrails
    should_rpc = False
    if computed_priority >= RPC_MIN_PRIORITY:
        cur.execute("""
            SELECT last_rpc_fetch_at FROM address_activity
            WHERE address = ?
        """, (address,))
        row = cur.fetchone()
        last_rpc = row[0] if row and row[0] else 0

        if (now - last_rpc) > RPC_COOLDOWN_SECONDS:
            global RPC_CALLS_THIS_HOUR, HOUR_START_TIME

            # Check hourly rate limit
            if (now - HOUR_START_TIME) > 3600:
                RPC_CALLS_THIS_HOUR = 0
                HOUR_START_TIME = now

            if RPC_CALLS_THIS_HOUR < MAX_RPC_CALLS_PER_HOUR:
                should_rpc = True
                RPC_CALLS_THIS_HOUR += 1
                print(f"[WORKER] {address[:8]}... RPC ALLOWED (calls_hour={RPC_CALLS_THIS_HOUR})", flush=True)
            else:
                print(f"[WORKER] {address[:8]}... RPC rate limit hit", flush=True)
        else:
            print(f"[WORKER] {address[:8]}... RPC cooldown (last was {now - last_rpc}s ago)", flush=True)
    else:
        print(f"[WORKER] {address[:8]}... priority too low for RPC ({computed_priority:.1f} < {RPC_MIN_PRIORITY})", flush=True)

    # ---- DO NOT CALL ENHANCED TRANSACTIONS ----
    # If RPC needed, would call something like:
    #   - getSignaturesForAddress (behind gate)
    #   - simple getAccountInfo
    # Never call /v0/transactions

    # For now, just log what we would do
    if should_rpc:
        print(f"[WORKER] {address[:8]}... [RPC] Would call getSignaturesForAddress", flush=True)
        # In real implementation:
        # results = rpc_client.get_signatures_for_address(address, limit=100)
        # ... process results ...

        cur.execute("""
            UPDATE address_activity
            SET last_rpc_fetch_at = ?
            WHERE address = ?
        """, (now, address))

    # ---- UPDATE CREATOR RISK SCORE ----
    # Call ranker to compute and log risk score
    try:
        from webhook_creator_ranker import compute_creator_risk_score
        risk_score = compute_creator_risk_score(conn, address)
        print(f"[WORKER] {address[:8]}... risk_score={risk_score['score']} level={risk_score['risk_level']}", flush=True)
    except Exception as e:
        print(f"[WORKER] {address[:8]}... error computing risk score: {e}", flush=True)

    # Adaptive requeue: Higher priority = sooner recheck
    # Reduces DB churn on low-value addresses
    if computed_priority >= 80:
        next_run_delay = 60      # Critical: recheck in 1 minute
    elif computed_priority >= 60:
        next_run_delay = 300     # Elevated: recheck in 5 minutes
    elif computed_priority >= 40:
        next_run_delay = 900     # Moderate: recheck in 15 minutes
    else:
        next_run_delay = 3600    # Low: recheck in 1 hour

    # Update attempt count and next_run_at with adaptive delay
    cur.execute("""
        UPDATE work_queue
        SET
            priority = ?,
            attempts = attempts + 1,
            locked_until = 0,
            next_run_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE address = ?
    """, (computed_priority, now + next_run_delay, address))

    conn.commit()

    return True


def run_worker(max_iterations: Optional[int] = None):
    """
    Main worker loop.

    Continuously processes work queue items in priority order.

    Args:
        max_iterations: Stop after N iterations (for testing), None = infinite
    """
    print("[WORKER] Starting webhook worker...", flush=True)

    iteration = 0

    try:
        while True:
            if max_iterations and iteration >= max_iterations:
                print(f"[WORKER] Reached max_iterations={max_iterations}, stopping", flush=True)
                break

            conn = get_worker_db()

            # Fetch next batch
            work_items = fetch_next_work(conn, BATCH_SIZE)

            if not work_items:
                print(f"[WORKER] No work items, sleeping {WORKER_SLEEP}s", flush=True)
                conn.close()
                time.sleep(WORKER_SLEEP)
                iteration += 1
                continue

            print(f"[WORKER] Fetched {len(work_items)} work items", flush=True)

            # Process each item
            for address, priority, reason, locked_until in work_items:
                try:
                    process_work_item(conn, address, priority, reason)
                except Exception as e:
                    print(f"[WORKER] Error processing {address[:8]}...: {e}", flush=True)

            conn.close()
            iteration += 1
            time.sleep(WORKER_SLEEP)

    except KeyboardInterrupt:
        print("[WORKER] Interrupted by user", flush=True)
    except Exception as e:
        print(f"[WORKER] Fatal error: {e}", flush=True)
        raise


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    run_worker()
