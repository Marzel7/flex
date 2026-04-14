"""
FLEX Webhook-First Handler
Processes RAW Helius webhooks for native SOL transfers
Minimal RPC, maximum efficiency

Author: Claude Code
Date: 2026-03-03
"""

import sqlite3
from src.utils.db_locking import db_connect
import json
import os
import time
from typing import List, Dict, Tuple, Optional
from datetime import datetime


# ============================================================================
# CONFIGURATION
# ============================================================================

DB_PATH = os.getenv("DB_PATH", "database/flex_complete_database.db")
HELIUS_AUTH_HEADER = os.getenv("HELIUS_WEBHOOK_AUTH", None)

# RPC Guardrails
RPC_MIN_PRIORITY = 80
RPC_COOLDOWN_SECONDS = 30 * 60  # 30 minutes
MAX_RPC_CALLS_PER_HOUR = 100  # Global rate limit


# ============================================================================
# DATABASE UTILITIES
# ============================================================================

from src.metrics.rpc_metrics_recorder import record_request

def get_webhook_db():
    """Create optimized database connection for webhook processing."""
    conn = db_connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_webhook_tables():
    """Create schema tables if they don't exist."""
    conn = get_webhook_db()
    cur = conn.cursor()

    # sol_transfers
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sol_transfers (
            signature TEXT PRIMARY KEY,
            slot INTEGER NOT NULL,
            block_time INTEGER NOT NULL,
            source TEXT NOT NULL,
            destination TEXT NOT NULL,
            lamports INTEGER NOT NULL,
            amount_sol REAL NOT NULL,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed BOOLEAN DEFAULT 0
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_sol_transfers_source ON sol_transfers(source)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sol_transfers_destination ON sol_transfers(destination)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sol_transfers_block_time ON sol_transfers(block_time DESC)")

    # address_activity
    cur.execute("""
        CREATE TABLE IF NOT EXISTS address_activity (
            address TEXT PRIMARY KEY,
            last_seen_at INTEGER NOT NULL,
            tx_5m INTEGER DEFAULT 0,
            tx_1h INTEGER DEFAULT 0,
            tx_24h INTEGER DEFAULT 0,
            sol_in_5m REAL DEFAULT 0.0,
            sol_in_1h REAL DEFAULT 0.0,
            sol_in_24h REAL DEFAULT 0.0,
            sol_out_5m REAL DEFAULT 0.0,
            sol_out_1h REAL DEFAULT 0.0,
            sol_out_24h REAL DEFAULT 0.0,
            last_processed_at INTEGER,
            last_rpc_fetch_at INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_address_activity_last_seen ON address_activity(last_seen_at DESC)")

    # work_queue
    cur.execute("""
        CREATE TABLE IF NOT EXISTS work_queue (
            address TEXT PRIMARY KEY,
            priority REAL DEFAULT 0.0,
            reason TEXT,
            next_run_at INTEGER DEFAULT 0,
            locked_until INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_work_queue_priority ON work_queue(priority DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_work_queue_next_run ON work_queue(next_run_at ASC)")

    # creator_analysis_queue - Background analysis of creator addresses from webhooks
    cur.execute("""
        CREATE TABLE IF NOT EXISTS creator_analysis_queue (
            creator_address TEXT PRIMARY KEY,
            priority REAL DEFAULT 0.0,
            status TEXT DEFAULT 'pending',
            last_analyzed_at INTEGER,
            next_analysis_at INTEGER DEFAULT 0,
            locked_until INTEGER DEFAULT 0,
            attempts INTEGER DEFAULT 0,
            findings_cached TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("CREATE INDEX IF NOT EXISTS idx_creator_analysis_status ON creator_analysis_queue(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_creator_analysis_priority ON creator_analysis_queue(priority DESC)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_creator_analysis_next ON creator_analysis_queue(next_analysis_at ASC)")

    # helius_webhook_assignments - Track which creators are monitored by the webhook
    cur.execute("""
        CREATE TABLE IF NOT EXISTS helius_webhook_assignments (
            creator_address TEXT PRIMARY KEY,
            shard_index INTEGER NOT NULL,
            webhook_id TEXT NOT NULL,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ============================================================================
# WEBHOOK PAYLOAD PARSING
# ============================================================================

def extract_system_transfers(tx: Dict) -> List[Tuple[str, str, int, str, int, int]]:
    """
    Extract native SOL transfers from RAW Helius webhooks.

    Supports:
    1. Parsed format (enhanced webhooks)
    2. RAW format with preBalances/postBalances (raw webhooks)
    """
    transfers = []

    # Get transaction metadata
    sig = tx.get("signature")
    if not sig:
        try:
            sigs = tx.get("transaction", {}).get("signatures", [])
            sig = sigs[0] if sigs else None
        except:
            pass

    if not sig:
        return []

    slot = tx.get("slot", 0)
    block_time = tx.get("blockTime", 0)

    # Get transaction message
    message = tx.get("transaction", {}).get("message", {})
    if not message:
        return []

    instructions = message.get("instructions", [])
    if not instructions:
        return []

    # Get account keys for index resolution
    account_keys = message.get("accountKeys", [])
    if not account_keys:
        return []

    # For RAW webhooks: parse System Program transfer instructions
    meta = tx.get("meta", {})
    pre_balances = meta.get("preBalances", [])
    post_balances = meta.get("postBalances", [])

    # Look for System Program transfer instructions
    for instr in instructions:
        if not isinstance(instr, dict):
            continue

        program_idx = instr.get("programIdIndex")
        if program_idx is None or program_idx >= len(account_keys):
            continue

        # Check if System Program
        program = account_keys[program_idx]
        if program != "11111111111111111111111111111111":
            continue

        # Get accounts involved in this instruction
        instr_accounts = instr.get("accounts", [])
        if len(instr_accounts) < 2:
            continue

        # For System Program transfer: accounts[0] = source, accounts[1] = destination
        source_idx = instr_accounts[0]
        dest_idx = instr_accounts[1]

        if source_idx >= len(account_keys) or dest_idx >= len(account_keys):
            continue

        source = account_keys[source_idx]
        destination = account_keys[dest_idx]

        # Get balance change
        if pre_balances and post_balances and len(pre_balances) > source_idx:
            source_sent = pre_balances[source_idx] - post_balances[source_idx]
            dest_received = post_balances[dest_idx] - pre_balances[dest_idx]

            # Use the amount that matches (receiver's increase is more reliable)
            if dest_received > 0:
                transfers.append((source, destination, dest_received, sig, slot, block_time))
                break  # Got the transfer instruction

    if transfers:
        return transfers

    # Fallback to parsed format (enhanced webhooks)
    for instr in instructions:
        if not isinstance(instr, dict):
            continue

        # Check if this is a System Program instruction
        program_idx = instr.get("programIdIndex")
        if program_idx is None:
            continue

        # System Program is usually at index 0 or 2
        if program_idx < len(account_keys):
            program = account_keys[program_idx]
            # System program address
            if program not in ["11111111111111111111111111111111", "System"]:
                continue
        else:
            continue

        # Look for parsed format (enhanced)
        parsed = instr.get("parsed", {})
        if parsed:
            instr_type = parsed.get("type")
            if instr_type == "transfer":
                info = parsed.get("info", {})
                source = info.get("source")
                destination = info.get("destination")
                lamports = info.get("lamports")

                if source and destination and lamports:
                    lamports = int(lamports)
                    if lamports > 0:
                        amount_sol = lamports / 1e9
                        transfers.append((source, destination, lamports, sig, slot, block_time))

        # Fallback: raw accounts format
        elif not parsed or not parsed.get("type"):
            accounts = instr.get("accounts", [])
            data = instr.get("data", "")

            # System Program transfer is: [source, dest, ...]
            # Instruction discriminator: "11" (0x03 in base58)
            if len(accounts) >= 2 and data:
                try:
                    # Very basic check - would need full borsh parsing for robustness
                    # For now, skip raw format and rely on parsed
                    pass
                except:
                    pass

    return transfers


def validate_auth_header(request_obj) -> bool:
    """
    Validate optional auth header if HELIUS_WEBHOOK_AUTH is set.

    Args:
        request_obj: Flask request object

    Returns:
        True if auth is valid or not required, False otherwise
    """
    if not HELIUS_AUTH_HEADER:
        return True

    auth_header = request_obj.headers.get("Authorization", "")
    return auth_header == HELIUS_AUTH_HEADER


# ============================================================================
# ACTIVITY STATISTICS
# ============================================================================

def update_address_activity(conn: sqlite3.Connection, address: str, is_source: bool,
                            amount_sol: float, block_time: int):
    """
    Update rolling statistics for an address.

    Args:
        conn: Database connection
        address: Wallet address
        is_source: True if this address is the sender, False if receiver
        amount_sol: Amount in SOL
        block_time: Unix timestamp of transaction
    """
    cur = conn.cursor()
    now = int(time.time())

    # Time windows (in seconds)
    t_5m = now - 300
    t_1h = now - 3600
    t_24h = now - 86400

    # Check if address exists
    cur.execute("SELECT * FROM address_activity WHERE address = ?", (address,))
    row = cur.fetchone()

    if not row:
        # New address
        tx_5m = 1
        tx_1h = 1
        tx_24h = 1

        if is_source:
            sol_out_5m = amount_sol
            sol_out_1h = amount_sol
            sol_out_24h = amount_sol
            sol_in_5m = sol_in_1h = sol_in_24h = 0.0
        else:
            sol_in_5m = amount_sol
            sol_in_1h = amount_sol
            sol_in_24h = amount_sol
            sol_out_5m = sol_out_1h = sol_out_24h = 0.0

        cur.execute("""
            INSERT INTO address_activity (
                address, last_seen_at, tx_5m, tx_1h, tx_24h,
                sol_in_5m, sol_in_1h, sol_in_24h,
                sol_out_5m, sol_out_1h, sol_out_24h
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (address, block_time, tx_5m, tx_1h, tx_24h,
              sol_in_5m, sol_in_1h, sol_in_24h, sol_out_5m, sol_out_1h, sol_out_24h))
    else:
        # Update existing address
        # Recalculate rolling counts based on sol_transfers
        cur.execute("""
            SELECT
                COUNT(*) as cnt_5m,
                SUM(CASE WHEN source = ? AND block_time > ? THEN 1 ELSE 0 END) as out_5m,
                SUM(CASE WHEN destination = ? AND block_time > ? THEN 1 ELSE 0 END) as in_5m,
                SUM(CASE WHEN source = ? AND block_time > ? THEN amount_sol ELSE 0 END) as sol_out_5m,
                SUM(CASE WHEN destination = ? AND block_time > ? THEN amount_sol ELSE 0 END) as sol_in_5m
            FROM sol_transfers
            WHERE (source = ? OR destination = ?)
        """, (address, t_5m, address, t_5m, address, t_5m, address, t_5m, address, address))

        row_stats = cur.fetchone()

        cur.execute("""
            UPDATE address_activity SET
                last_seen_at = ?,
                tx_5m = COALESCE(?, 0),
                sol_out_5m = COALESCE(?, 0.0),
                sol_in_5m = COALESCE(?, 0.0),
                updated_at = CURRENT_TIMESTAMP
            WHERE address = ?
        """, (block_time, row_stats[1], row_stats[3], row_stats[4], address))


def save_creator_signatures(conn: sqlite3.Connection, dest: str, sig: str, block_time: int, amount_lamports: int):
    """
    Save transaction signature to creator_tx_ledger if destination is a watched creator.

    Args:
        conn: Database connection
        dest: Destination address (potential creator)
        sig: Transaction signature
        block_time: Block timestamp
        amount_lamports: Amount transferred in lamports
    """
    cur = conn.cursor()

    try:
        # Check if destination is in creator_watch
        cur.execute("SELECT creator_pubkey FROM creator_watch WHERE creator_pubkey = ?", (dest,))
        creator = cur.fetchone()

        if creator:
            # Try to insert into creator_tx_ledger
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO creator_tx_ledger
                    (creator_pubkey, signature, blockTime, delta_sol_lamports, tx_type, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (dest, sig, block_time, amount_lamports, "transfer_in", "webhook"))

                if cur.rowcount > 0:
                    print(f"[WEBHOOK_CREATOR] {sig[:16]}... - Saved tx for creator {dest[:8]}...", flush=True)
            except Exception as e:
                # Signature already exists or other constraint violation - ignore
                pass
    except Exception as e:
        print(f"[WEBHOOK_CREATOR] Error checking creator {dest[:8]}...: {e}", flush=True)


def enqueue_work(conn: sqlite3.Connection, addresses: List[str], reason: str):
    """
    Add addresses to work queue with initial priority.

    Args:
        conn: Database connection
        addresses: List of wallet addresses
        reason: Reason for queuing (e.g., "new_transfer", "active")
    """
    cur = conn.cursor()
    now = int(time.time())

    for addr in addresses:
        # Initial priority based on reason
        initial_priority = {"new_transfer": 20, "active": 30}.get(reason, 10)

        cur.execute("""
            INSERT INTO work_queue (address, priority, reason, next_run_at, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(address) DO UPDATE SET
                priority = MAX(priority, ?),
                reason = ?,
                updated_at = CURRENT_TIMESTAMP
        """, (addr, initial_priority, reason, now, initial_priority, reason))


# ============================================================================
# WEBHOOK HANDLER (Flask Route)
# ============================================================================

def queue_for_creator_analysis(conn: sqlite3.Connection, addresses: List[str], priority: float = 10.0):
    """
    Queue addresses for background creator analysis.
    
    Called whenever new addresses are detected from webhooks.
    Analysis includes malicious activity detection, funding networks, etc.
    
    Args:
        conn: Database connection
        addresses: List of creator addresses to analyze
        priority: Priority score (higher = analyze sooner)
    """
    cur = conn.cursor()
    now = int(time.time())
    
    for addr in addresses:
        try:
            cur.execute("""
                INSERT INTO creator_analysis_queue 
                (creator_address, priority, status, next_analysis_at, updated_at)
                VALUES (?, ?, 'pending', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(creator_address) DO UPDATE SET
                    priority = MAX(priority, ?),
                    status = CASE WHEN status = 'analyzing' THEN status ELSE 'pending' END,
                    updated_at = CURRENT_TIMESTAMP
            """, (addr, priority, now, priority))
        except Exception as e:
            print(f"[WEBHOOK_ANALYSIS_QUEUE] Error queueing {addr[:8]}...: {e}", flush=True)


def handle_helius_webhook(request_obj) -> Tuple[str, int]:
    """
    Flask route handler for POST /helius/webhook

    Processes RAW Helius webhook containing native SOL transfers.
    Returns quickly (200) after queueing work.

    Args:
        request_obj: Flask request object

    Returns:
        Tuple of (response_text, status_code)
    """
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Validate auth if required
    if not validate_auth_header(request_obj):
        print(f"[WEBHOOK] {now} - Auth failed", flush=True)
        return ("unauthorized", 401)

    # Parse payload (list or dict)
    try:
        payload = request_obj.get_json(silent=True)
    except Exception as e:
        print(f"[WEBHOOK] {now} - Parse error: {e}", flush=True)
        return ("ok", 200)

    if not payload:
        print(f"[WEBHOOK] {now} - Empty payload", flush=True)
        return ("ok", 200)

    # Normalize to list
    if isinstance(payload, dict):
        payload = [payload]
    elif not isinstance(payload, list):
        print(f"[WEBHOOK] {now} - Invalid payload type: {type(payload)}", flush=True)
        return ("ok", 200)

    # Process all transactions
    conn = get_webhook_db()
    cur = conn.cursor()

    stored = 0
    skipped = 0
    all_addresses = set()

    for i, tx in enumerate(payload):
        if not isinstance(tx, dict):
            skipped += 1
            continue

        # Extract transfers
        transfers = extract_system_transfers(tx)

        if not transfers:
            skipped += 1
            continue

        sig = tx.get("signature") or tx.get("transaction", {}).get("signatures", [None])[0]

        for source, dest, lamports, sig_out, slot, block_time in transfers:
            amount_sol = lamports / 1e9

            # Filter out dust transfers (< 0.001 SOL)
            MIN_SOL = 0.001
            if amount_sol < MIN_SOL:
                # print(f"[WEBHOOK_DUST] {now} - DUST: {sig_out[:16]}... ({amount_sol:.9f} SOL < {MIN_SOL} SOL)", flush=True)
                skipped += 1
                continue

            # Accept both inbound and outbound transfers involving creators
            # (source = creator OR destination = creator)
            cur.execute("""
                SELECT 1 FROM helius_webhook_assignments
                WHERE creator_address = ? OR creator_address = ?
                LIMIT 1
            """, (source, dest))

            is_creator_involved = cur.fetchone() is not None

            if not is_creator_involved:
                skipped += 1
                continue

            # Insert into sol_transfers (dedupe by signature)
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO sol_transfers
                    (signature, slot, block_time, source, destination, lamports, amount_sol)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (sig_out, slot, block_time, source, dest, lamports, amount_sol))

                if cur.rowcount > 0:
                    stored += 1
                    # print(f"[WEBHOOK_STORED] {now} - STORED: {source[:8]}... → {dest[:8]}... ({amount_sol:.9f} SOL)", flush=True)

                    # Update activity for both addresses
                    update_address_activity(conn, source, True, amount_sol, block_time)
                    update_address_activity(conn, dest, False, amount_sol, block_time)

                    # Save to creator_tx_ledger if destination is a watched creator
                    save_creator_signatures(conn, dest, sig_out, block_time, lamports)

                    # Queue creators involved in the transfer
                    # If source is a creator, queue them (outbound)
                    # If destination is a creator, queue them (inbound)
                    cur.execute("""
                        SELECT 1 FROM helius_webhook_assignments
                        WHERE creator_address = ?
                    """, (source,))
                    if cur.fetchone():
                        all_addresses.add(source)

                    cur.execute("""
                        SELECT 1 FROM helius_webhook_assignments
                        WHERE creator_address = ?
                    """, (dest,))
                    if cur.fetchone():
                        all_addresses.add(dest)

                    # Record webhook event in RPC metrics
                    record_request(
                        section='webhooks',
                        provider='helius_rpc',
                        method='webhook_event',
                        status_code=200,
                        latency_ms=0,
                        source_file='webhook_handler',
                        credits_override=0
                    )
                else:
                    print(f"[WEBHOOK_DUPLICATE] {now} - DUPLICATE: {sig_out[:16]}...", flush=True)

            except Exception as e:
                print(f"[WEBHOOK] {now} - Error: {e}", flush=True)
                continue

    # Enqueue only destination addresses (creators) for background analysis
    if all_addresses:
        enqueue_work(conn, list(all_addresses), "new_transfer")
        queue_for_creator_analysis(conn, list(all_addresses), priority=15.0)

    # Commit and close
    conn.commit()
    conn.close()

    # Only log when we actually stored or queued something
    if stored > 0 or len(all_addresses) > 0:
        print(f"[WEBHOOK_SUMMARY] {now} - stored={stored}, skipped={skipped}, queued={len(all_addresses)}", flush=True)

    return ("ok", 200)


# ============================================================================
# INITIALIZATION
# ============================================================================

def init_webhook():
    """Initialize webhook tables on startup."""
    ensure_webhook_tables()
    print("[WEBHOOK_INIT] Tables created/verified", flush=True)
