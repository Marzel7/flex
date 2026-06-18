"""
FLEX Webhook Worker
Priority-based address analyzer with DB-only signals and strict RPC gating

Author: Claude Code
Date: 2026-03-03
"""

import sqlite3
from src.utils.db_locking import db_connect
import os
import time
from typing import Optional, Dict, Tuple
from datetime import datetime


# ============================================================================
# CONFIGURATION
# ============================================================================

DB_PATH = os.getenv("DB_PATH", "database/flex_complete_database.db")

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
    conn = db_connect(DB_PATH, timeout=30)
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

    # Recompute priority with latest DB signals
    computed_priority, reasons = compute_priority(conn, address)

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

    # ---- DO NOT CALL ENHANCED TRANSACTIONS ----
    # If RPC needed, would call something like:
    #   - getSignaturesForAddress (behind gate)
    #   - simple getAccountInfo
    # Never call /v0/transactions

    if should_rpc:
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
    except Exception as e:
        pass

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


# ============================================================================
# CREATOR ANALYSIS QUEUE
# ============================================================================

def fetch_next_creator_analysis(conn: sqlite3.Connection, batch_size: int = 5) -> list:
    """
    Fetch next batch of creators to analyze from queue.
    
    Selects unlocked, due items by highest priority.
    Locks them for LOCK_DURATION seconds.
    
    Args:
        conn: Database connection
        batch_size: Number of items to fetch
        
    Returns:
        List of creator_analysis_queue rows
    """
    cur = conn.cursor()
    now = int(time.time())
    
    cur.execute("""
        SELECT creator_address, priority, status, locked_until
        FROM creator_analysis_queue
        WHERE locked_until < ?
            AND next_analysis_at < ?
            AND status IN ('pending', 'retry')
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
        UPDATE creator_analysis_queue
        SET locked_until = ?, status = 'analyzing'
        WHERE creator_address IN ({','.join('?' * len(addresses))})
    """, [lock_until] + addresses)
    
    conn.commit()
    
    return rows


def process_creator_analysis(conn: sqlite3.Connection, creator_address: str) -> bool:
    """
    Analyze a creator address for malicious activity patterns.
    
    Uses only database queries - no RPC calls.
    Caches findings in creator_analysis_queue.findings_cached
    
    Args:
        conn: Database connection
        creator_address: Creator address to analyze
        
    Returns:
        True if analysis succeeded, False otherwise
    """
    cur = conn.cursor()
    now = int(time.time())
    
    try:
        # Query creator analysis endpoint logic from main.py
        # This computes all malicious activity signals
        
        # Get outgoing transfers
        cur.execute("""
            SELECT COUNT(*) as count, 
                   SUM(amount_sol) as total_sol,
                   COUNT(DISTINCT destination) as unique_recipients,
                   MAX(block_time) as last_transaction_time
            FROM sol_transfers
            WHERE source = ?
        """, (creator_address,))
        
        tx_row = cur.fetchone()
        if not tx_row or not tx_row[0]:
            # No transfers from this creator, mark complete
            cur.execute("""
                UPDATE creator_analysis_queue
                SET status = 'complete', last_analyzed_at = ?, 
                    next_analysis_at = ?, locked_until = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE creator_address = ?
            """, (now, now + 86400, creator_address))  # Next check in 24h
            conn.commit()
            return True
        
        outgoing_count = tx_row[0]
        total_sol = tx_row[1] or 0
        recipients = tx_row[2] or 0
        last_tx = tx_row[3]
        
        # Check for self-funding scheme
        # Self-funded if: sends to many addresses that only send back
        cur.execute("""
            SELECT COUNT(DISTINCT destination) as self_funded_count
            FROM sol_transfers st
            WHERE st.source = ?
                AND NOT EXISTS (
                    SELECT 1 FROM sol_transfers st2
                    WHERE st2.source = st.destination
                    AND st2.destination != ?
                )
                AND EXISTS (
                    SELECT 1 FROM sol_transfers st3
                    WHERE st3.source = st.destination
                    AND st3.destination = ?
                )
        """, (creator_address, creator_address, creator_address))
        
        self_funded_row = cur.fetchone()
        self_funded_count = self_funded_row[0] if self_funded_row else 0
        
        # Check circular funding (creator receives from their own funders)
        cur.execute("""
            SELECT COUNT(DISTINCT st2.source) as circular_funders
            FROM sol_transfers st1
            JOIN sol_transfers st2 ON st1.destination = st2.source
            WHERE st1.source = ?
                AND st2.destination = ?
                AND st1.destination != st2.destination
        """, (creator_address, creator_address))
        
        circular_row = cur.fetchone()
        circular_count = circular_row[0] if circular_row else 0
        
        # Check for suspicious cross-funding patterns
        # Creator sends to many addresses, and those addresses fund many other creators
        cur.execute("""
            SELECT COUNT(DISTINCT st2.destination) as cross_funded_creators
            FROM sol_transfers st1
            JOIN sol_transfers st2 ON st1.destination = st2.source
            WHERE st1.source = ?
                AND st2.destination IN (
                    SELECT DISTINCT destination FROM sol_transfers
                    WHERE destination != ?
                )
        """, (creator_address, creator_address))
        
        cross_row = cur.fetchone()
        cross_funded = cross_row[0] if cross_row else 0
        
        # Check funding chain depth (how many hops to reach this creator)
        cur.execute("""
            SELECT COUNT(DISTINCT st2.source) as direct_funders
            FROM sol_transfers st2
            WHERE st2.destination = ?
        """, (creator_address,))
        
        funder_row = cur.fetchone()
        direct_funders = funder_row[0] if funder_row else 0
        
        # Build findings summary
        findings = {
            "outgoing_transfers": outgoing_count,
            "total_sol_sent": round(total_sol, 9),
            "unique_recipients": recipients,
            "self_funded_intermediates": self_funded_count,
            "circular_funding_sources": circular_count,
            "cross_funded_creators": cross_funded,
            "direct_funders": direct_funders,
            "last_transaction_time": last_tx,
            "analyzed_at": now
        }
        
        # Risk score: sum of suspicious signals
        risk_score = 0
        if self_funded_count > 0:
            risk_score += min(self_funded_count * 5, 50)  # Self-funding cap at 50
        if circular_count > 0:
            risk_score += min(circular_count * 10, 40)    # Circular funding cap at 40
        if cross_funded > 10:
            risk_score += 30  # Cross-funding many creators
        if outgoing_count > 100:
            risk_score += 20  # Many transfers
        if recipients > 50:
            risk_score += 15  # Distributing to many addresses
            
        findings["risk_score"] = min(risk_score, 100)
        
        # Determine risk level
        if findings["risk_score"] >= 70:
            findings["risk_level"] = "HIGH"
        elif findings["risk_score"] >= 40:
            findings["risk_level"] = "MEDIUM"
        else:
            findings["risk_level"] = "LOW"
        
        # Cache findings as JSON
        import json
        cached_json = json.dumps(findings)
        
        # Adaptive requeue: Reanalyze frequent actors sooner
        if outgoing_count >= 100:
            next_analysis = now + 3600      # High activity: reanalyze in 1h
        elif outgoing_count >= 20:
            next_analysis = now + 21600     # Moderate: reanalyze in 6h
        else:
            next_analysis = now + 86400     # Low activity: reanalyze in 24h
        
        cur.execute("""
            UPDATE creator_analysis_queue
            SET status = 'complete', 
                last_analyzed_at = ?,
                next_analysis_at = ?,
                findings_cached = ?,
                locked_until = 0,
                attempts = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE creator_address = ?
        """, (now, next_analysis, cached_json, creator_address))
        
        conn.commit()
        return True
        
    except Exception as e:
        print(f"[CREATOR_ANALYSIS] Error analyzing {creator_address[:8]}...: {e}", flush=True)
        
        # Mark as retry for next attempt
        cur.execute("""
            UPDATE creator_analysis_queue
            SET status = 'retry',
                attempts = attempts + 1,
                locked_until = 0,
                next_analysis_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE creator_address = ?
        """, (now + 300, creator_address))  # Retry in 5 minutes
        
        conn.commit()
        return False


def run_worker(max_iterations: Optional[int] = None):
    """
    Main worker loop.

    Continuously processes work queue items and creator analysis queue in priority order.

    Args:
        max_iterations: Stop after N iterations (for testing), None = infinite
    """
    print("[WORKER] Started", flush=True)

    iteration = 0

    try:
        while True:
            if max_iterations and iteration >= max_iterations:
                print(f"[WORKER] Stopped after {max_iterations} iterations", flush=True)
                break

            try:
                conn = get_worker_db()

                # Process creator analysis queue (5 at a time)
                creator_items = fetch_next_creator_analysis(conn, batch_size=5)
                for creator_address, priority, status, locked_until in creator_items:
                    try:
                        process_creator_analysis(conn, creator_address)
                    except Exception as e:
                        print(f"[CREATOR_ANALYSIS] Error: {creator_address[:8]}... - {e}", flush=True)

                # Fetch next batch of regular work queue
                work_items = fetch_next_work(conn, BATCH_SIZE)

                if not work_items and not creator_items:
                    conn.close()
                    time.sleep(WORKER_SLEEP)
                    iteration += 1
                    continue

                # Process each work queue item
                for address, priority, reason, locked_until in work_items:
                    try:
                        process_work_item(conn, address, priority, reason)
                    except Exception as e:
                        print(f"[WORKER] Error: {address[:8]}... - {e}", flush=True)

                conn.close()

            except Exception as e:
                print(f"[WORKER] Error (will retry): {e}", flush=True)
                try:
                    conn.close()
                except Exception:
                    pass
                time.sleep(10)

            iteration += 1
            time.sleep(WORKER_SLEEP)

    except KeyboardInterrupt:
        print("[WORKER] Stopped by user", flush=True)


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    run_worker()
