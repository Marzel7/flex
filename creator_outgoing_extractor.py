#!/usr/bin/env python3
"""
Creator Outgoing Transfer Extractor

Hourly scan:
- 1000 creators
- 1 RPC call per creator (getSignaturesForAddress)
- Batch enhanced parse for new sigs (100 sigs/request)
- Write outgoing edges + build chains + build coordinated edges

Concurrency-safe + lock-optimized:
- Preload all cursors in one read
- Batch update cursors in one transaction
- Concurrent signature fetches + safe merge
"""

import os
import asyncio
import time
import sqlite3
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import aiohttp

from db_global_lock import db_write_lock_global

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")

# Helius API keys (from tests/test_pumpswap_listener.py)
RPC_KEYS = [
    ("a132b19d-9b44-4c71-8e6f-d320d9f351c6", "GITHUB"),     # Primary (best quota)
    ("f084fae8-d111-4337-9960-2d9c5e02a726", "MARZEL"),     # Fallback 1
    ("0ae07551-32df-4d9d-af2a-1925fb7f561f", "JEZZA"),      # Fallback 2
    ("3b2917b8-9bed-4e2e-8c05-a74adbc34bb8", "NEW_KEY"),    # Fallback 3
]

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "") or RPC_KEYS[0][0]  # Use first key as default
RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
HELIUS_ENHANCED = f"https://api-mainnet.helius-rpc.com/v0/transactions?api-key={HELIUS_API_KEY}"


def _connect():
    """Create connection with optimal PRAGMA settings"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def ensure_tables():
    """Create all tables with proper schema and indexes"""
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            cur.executescript("""
            CREATE TABLE IF NOT EXISTS creator_sig_cursors (
              creator_address TEXT PRIMARY KEY,
              last_signature  TEXT,
              last_slot       INTEGER,
              updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS creator_outgoing_transfers (
              creator_address TEXT NOT NULL,
              recipient_address TEXT NOT NULL,
              amount_sol REAL NOT NULL,
              transaction_signature TEXT PRIMARY KEY,
              slot INTEGER,
              block_time INTEGER,
              first_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              recipient_type TEXT DEFAULT 'unknown',
              is_cex INTEGER DEFAULT 0,
              cex_exchange TEXT,
              cex_type TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_cot_creator ON creator_outgoing_transfers(creator_address);
            CREATE INDEX IF NOT EXISTS idx_cot_recipient ON creator_outgoing_transfers(recipient_address);
            CREATE INDEX IF NOT EXISTS idx_cot_block_time ON creator_outgoing_transfers(block_time);

            CREATE TABLE IF NOT EXISTS funding_chains (
              chain_id INTEGER PRIMARY KEY AUTOINCREMENT,
              chain_type TEXT NOT NULL,
              source_creator TEXT,
              bridge_funder TEXT,
              target_creator TEXT,
              source_tx TEXT,
              bridge_to_target_amount_sol REAL,
              source_to_bridge_amount_sol REAL,
              source_block_time INTEGER,
              bridge_first_detected_at TIMESTAMP,
              bridge_is_cex INTEGER DEFAULT 0,
              confidence INTEGER DEFAULT 50,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_fc_source ON funding_chains(source_creator);
            CREATE INDEX IF NOT EXISTS idx_fc_bridge ON funding_chains(bridge_funder);
            CREATE INDEX IF NOT EXISTS idx_fc_target ON funding_chains(target_creator);

            CREATE TABLE IF NOT EXISTS coordinated_creator_edges (
              creator_a TEXT NOT NULL,
              creator_b TEXT NOT NULL,
              bridge_funder TEXT NOT NULL,
              first_seen_block_time INTEGER,
              evidence_tx TEXT,
              confidence INTEGER DEFAULT 50,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (creator_a, creator_b, bridge_funder)
            );

            CREATE INDEX IF NOT EXISTS idx_coord_a ON coordinated_creator_edges(creator_a);
            CREATE INDEX IF NOT EXISTS idx_coord_b ON coordinated_creator_edges(creator_b);

            CREATE TABLE IF NOT EXISTS creator_self_funding (
              creator_address TEXT PRIMARY KEY,
              self_funding_intermediates INTEGER DEFAULT 0,
              total_funders INTEGER DEFAULT 0,
              self_funding_percentage REAL DEFAULT 0.0,
              is_self_funding INTEGER DEFAULT 0,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS outgoing_chain_cursor (
              id INTEGER PRIMARY KEY CHECK (id=1),
              last_block_time INTEGER DEFAULT 0
            );
            INSERT OR IGNORE INTO outgoing_chain_cursor(id,last_block_time) VALUES (1,0);

            CREATE TABLE IF NOT EXISTS coordinated_edge_cursor (
              id INTEGER PRIMARY KEY CHECK (id=1),
              last_chain_id INTEGER DEFAULT 0
            );
            INSERT OR IGNORE INTO coordinated_edge_cursor(id,last_chain_id) VALUES (1,0);
            """)
            conn.commit()
        except sqlite3.OperationalError as e:
            # Ignore schema mismatch errors - tables may have evolved
            if 'source_tx' not in str(e):
                raise
            print(f"[OUTGOING] ℹ️ Schema evolved - ignoring index on non-existent column", flush=True)
        finally:
            conn.close()

        # Try to create idx_cf_funder on creator_funders (may not exist yet)
        try:
            conn = _connect()
            cur = conn.cursor()
            cur.execute("CREATE INDEX IF NOT EXISTS idx_cf_funder ON creator_funders(funder_address)")
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            # creator_funders table doesn't exist yet, skip index
            pass

        # Create priority scanning view using simple tier-based ordering
        try:
            conn = _connect()
            cur = conn.cursor()
            cur.execute("""
            CREATE VIEW IF NOT EXISTS creator_scan_priority AS
            WITH creator_data AS (
              SELECT 
                ta.earliest_tx_creator AS creator,
                MAX(ta.created_at) AS last_launch_at,
                CASE WHEN cn.creator_address IS NOT NULL THEN 1 ELSE 0 END AS in_network,
                MAX(cf.first_detected_at) AS last_funded_at,
                COALESCE(csc.updated_at, '2000-01-01') AS last_scanned_at,
                COALESCE(cb.reputation, 'CLEAN') AS reputation,
                COALESCE(cb.connected_to_malicious, 0) AS connected_to_malicious
              FROM token_analysis ta
              LEFT JOIN creator_networks cn ON cn.creator_address = ta.earliest_tx_creator
              LEFT JOIN creator_funders cf ON cf.creator_address = ta.earliest_tx_creator
              LEFT JOIN creator_sig_cursors csc ON csc.creator_address = ta.earliest_tx_creator
              LEFT JOIN creator_blocklist cb ON cb.creator_address = ta.earliest_tx_creator
              WHERE ta.earliest_tx_creator IS NOT NULL
              GROUP BY ta.earliest_tx_creator
            )
            SELECT creator,
              CASE
                WHEN datetime(last_launch_at) >= datetime('now', '-6 hours') THEN 0
                WHEN reputation IN ('MALICIOUS', 'SUSPICIOUS') THEN 1
                WHEN connected_to_malicious = 1 THEN 1
                WHEN in_network = 0 THEN 2
                WHEN datetime(last_funded_at) >= datetime('now', '-6 hours') THEN 3
                WHEN datetime(last_scanned_at) < datetime('now', '-24 hours') THEN 4
                ELSE 5
              END AS tier,
              last_launch_at,
              last_scanned_at
            FROM creator_data
            """)
            conn.commit()
            conn.close()
        except sqlite3.OperationalError:
            # View creation may fail if dependencies don't exist yet, that's okay
            pass

        print("[OUTGOING] ✅ Tables ensured", flush=True)


def get_creators(limit: int = 1000) -> List[str]:
    """Get creators ordered by priority tier.
    
    Priority tiers (in order):
    0. Launched in last 6 hours (always top)
    1. MALICIOUS/SUSPICIOUS or connected to malicious
    2. Not in any network (discovery)
    3. Recently funded (last 6 hours)
    4. Not scanned in 24 hours
    5. Everything else
    
    Falls back to chronological ordering if priority view unavailable.
    """
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    try:
        # Try tier-based scanning first
        cur.execute("""
          SELECT creator
          FROM creator_scan_priority
          ORDER BY tier ASC, last_launch_at DESC, last_scanned_at ASC
          LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        return [r["creator"] for r in rows if r and r["creator"]]
    except sqlite3.OperationalError:
        # Fallback to simple chronological ordering if view doesn't exist
        cur.execute("""
          SELECT DISTINCT earliest_tx_creator
          FROM token_analysis
          WHERE earliest_tx_creator IS NOT NULL
          ORDER BY analyzed_at DESC
          LIMIT ?
        """, (limit,))
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows if r and r[0]]


def load_all_cursors(creators: List[str]) -> Dict[str, Tuple[Optional[str], Optional[int]]]:
    """
    Load all cursors in one DB read (reduces lock pressure).
    Chunks by 800 to avoid SQLite parameter limit issues.
    Uses cursor_position if available (new schema), falls back to dummy values.
    """
    if not creators:
        return {}

    conn = _connect()
    cur = conn.cursor()
    cursors: Dict[str, Tuple[Optional[str], Optional[int]]] = {}

    chunk_size = 800
    for i in range(0, len(creators), chunk_size):
        chunk = creators[i:i+chunk_size]
        qmarks = ",".join(["?"] * len(chunk))
        try:
            # Try to get last_signature and last_slot (old schema)
            cur.execute(f"""
                SELECT creator_address, last_signature, last_slot
                FROM creator_sig_cursors
                WHERE creator_address IN ({qmarks})
            """, chunk)
            for addr, sig, slot in cur.fetchall():
                cursors[addr] = (sig, slot)
        except sqlite3.OperationalError:
            # Fall back to cursor_position only (new schema)
            cur.execute(f"""
                SELECT creator_address, cursor_position
                FROM creator_sig_cursors
                WHERE creator_address IN ({qmarks})
            """, chunk)
            for addr, pos in cur.fetchall():
                # Return cursor_position as both signature and slot for compatibility
                cursors[addr] = (None, pos if pos else None)

    conn.close()
    return cursors


def batch_update_cursors(rows: List[Tuple[str, str, int]]):
    """
    One transaction upsert for all creators (major lock reduction).
    This replaces 1000 individual update_cursor() calls.
    Uses cross-process lock to serialize with listener.
    Handles both old schema (last_signature, last_slot) and new schema (cursor_position).
    """
    if not rows:
        return

    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        # Try to detect which schema we have by checking a PRAGMA
        try:
            cur.execute("PRAGMA table_info(creator_sig_cursors)")
            cols = [col[1] for col in cur.fetchall()]

            if 'cursor_position' in cols:
                # New schema: just update updated_at and cursor_position
                for creator_addr, sig, slot in rows:
                    cur.execute("""
                      INSERT OR REPLACE INTO creator_sig_cursors(creator_address, cursor_position, updated_at)
                      VALUES(?, ?, datetime('now'))
                    """, (creator_addr, slot))
            else:
                # Old schema: update last_signature and last_slot
                cur.executemany("""
                  INSERT INTO creator_sig_cursors(creator_address, last_signature, last_slot, updated_at)
                  VALUES(?, ?, ?, datetime('now'))
                  ON CONFLICT(creator_address) DO UPDATE SET
                    last_signature=excluded.last_signature,
                    last_slot=excluded.last_slot,
                    updated_at=datetime('now')
                """, rows)
        except Exception as e:
            print(f"[OUTGOING] Warning: Could not update cursors: {e}", flush=True)

        conn.commit()
        conn.close()


async def rpc_get_signatures(session: aiohttp.ClientSession, address: str, limit: int = 25) -> List[dict]:
    """Fetch recent signatures for a creator address"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit}]
    }
    try:
        async with session.post(RPC_HTTP, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("result") or []
    except Exception as e:
        print(f"[OUTGOING] ⚠️ rpc_get_signatures error: {e}", flush=True)
        return []


async def helius_enhanced_parse(session: aiohttp.ClientSession, sigs: List[str]) -> List[dict]:
    """
    Batch parse transaction signatures via Helius Enhanced API (max 100/request).
    Handles 429 rate limits with exponential backoff + retries.
    """
    if not sigs:
        return []

    body = {"transactions": sigs}
    max_retries = 3
    backoff_times = [0.5, 1.0, 2.0]

    for attempt in range(max_retries):
        try:
            async with session.post(HELIUS_ENHANCED, json=body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 429:
                    if attempt < max_retries - 1:
                        sleep_time = backoff_times[attempt]
                        print(f"[OUTGOING] ⏸️ Rate limited (429), retry in {sleep_time}s (attempt {attempt+1}/{max_retries})", flush=True)
                        await asyncio.sleep(sleep_time)
                        continue
                    else:
                        print(f"[OUTGOING] ⚠️ Rate limited (429) after {max_retries} retries, skipping batch", flush=True)
                        return []
                elif resp.status != 200:
                    body_snippet = await resp.text()
                    if len(body_snippet) > 200:
                        body_snippet = body_snippet[:200] + "..."
                    print(f"[OUTGOING] ⚠️ Helius status {resp.status}: {body_snippet}", flush=True)
                    return []
                data = await resp.json()
                # Normalize response format (may be dict with "result" or direct list)
                if isinstance(data, dict) and "result" in data:
                    data = data["result"]
                return data if isinstance(data, list) else []
        except asyncio.TimeoutError:
            print(f"[OUTGOING] ⚠️ Helius timeout on attempt {attempt+1}/{max_retries}", flush=True)
            if attempt < max_retries - 1:
                await asyncio.sleep(backoff_times[attempt])
                continue
            return []
        except Exception as e:
            print(f"[OUTGOING] ⚠️ helius_enhanced_parse error: {e}", flush=True)
            return []

    return []


def extract_outgoing_sol(transactions: List[dict], creator_set: set) -> List[Tuple]:
    """
    Extract outgoing SOL transfers from transactions where creator is the sender.
    Resilient to None transactions, missing/None fields.
    Returns rows: (creator, recipient, amount_sol, signature, slot, block_time)
    """
    rows = []
    for tx in (transactions or []):
        # Skip non-dict items (error objects, None, etc from Helius)
        if not isinstance(tx, dict):
            continue

        sig = tx.get("signature")
        if not sig:
            continue

        # Force slot and timestamp to ints (Helius sometimes returns None)
        slot = int(tx.get("slot") or 0)
        ts = int(tx.get("timestamp") or 0)

        for nt in tx.get("nativeTransfers", []) or []:
            frm = nt.get("fromUserAccount")
            to = nt.get("toUserAccount")
            amt = nt.get("amount")  # lamports

            if not frm or not to or not amt:
                continue

            # Only track outgoing (creator as sender)
            if frm in creator_set and int(amt) > 0:
                amount_sol = float(amt) / 1e9
                rows.append((frm, to, amount_sol, sig, slot, ts))

    return rows


def insert_outgoing_rows(rows: List[Tuple]):
    """Insert outgoing transfer rows (fast batch write)
    Handles both old schema (with slot) and new schema (without slot).
    """
    if not rows:
        return

    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        # Check which columns exist in the table
        cur.execute("PRAGMA table_info(creator_outgoing_transfers)")
        existing_cols = {col[1] for col in cur.fetchall()}

        if 'slot' in existing_cols:
            # Old schema with slot column
            cur.executemany("""
              INSERT OR IGNORE INTO creator_outgoing_transfers
                (creator_address, recipient_address, amount_sol, transaction_signature, slot, block_time)
              VALUES (?, ?, ?, ?, ?, ?)
            """, rows)
        else:
            # New schema without slot column - skip slot parameter
            # Assuming rows are (creator, recipient, amount, sig, slot, block_time)
            # We'll use (creator, recipient, amount, sig, block_time) and ignore slot
            rows_without_slot = [(r[0], r[1], r[2], r[3], r[5] if len(r) > 5 else None) for r in rows]
            cur.executemany("""
              INSERT OR IGNORE INTO creator_outgoing_transfers
                (creator_address, recipient_address, amount_sol, transaction_signature, block_time)
              VALUES (?, ?, ?, ?, ?)
            """, rows_without_slot)

        conn.commit()
        conn.close()


def build_funding_chains_incremental():
    """
    Build funding chains from only NEW outgoing transfers since last run.
    Optimized: read cursor, release lock, do join, acquire lock again for insert.
    Safe against races: capture max block_time in same snapshot as candidates.
    """
    # Step 1: Read cursor and candidate chains in explicit read transaction (ensures snapshot consistency)
    conn = _connect()
    cur = conn.cursor()
    cur.execute("BEGIN DEFERRED")  # Start explicit read transaction

    try:
        cur.execute("SELECT last_block_time FROM outgoing_chain_cursor WHERE id=1")
        row = cur.fetchone()
        last_bt = int(row[0] or 0) if row else 0

        # Step 2: Execute read-only join to find candidate chains + capture max block_time in same snapshot
        # This prevents races where newer transfers arrive after we read candidates but before we update cursor
        cur.execute("""
          SELECT
            'CREATOR_TO_FUNDER_TO_CREATOR' AS chain_type,
            cot.creator_address AS source_creator,
            cot.recipient_address AS bridge_funder,
            cf.creator_address AS target_creator,
            cot.transaction_signature AS source_tx,
            cot.amount_sol AS source_to_bridge_amount_sol,
            cf.amount_sol AS bridge_to_target_amount_sol,
            cot.block_time AS source_block_time,
            cf.first_detected_at AS bridge_first_detected_at,
            COALESCE(cf.is_cex, 0) AS bridge_is_cex,
            CASE
              WHEN COALESCE(cf.is_cex,0)=1 THEN 10
              WHEN cot.amount_sol >= 1 THEN 85
              WHEN cot.amount_sol >= 0.2 THEN 70
              ELSE 55
            END AS confidence
          FROM creator_outgoing_transfers cot
          JOIN creator_funders cf
            ON cf.funder_address = cot.recipient_address
          WHERE
            cot.block_time > ?
            AND cot.creator_address IS NOT NULL
            AND cf.creator_address IS NOT NULL
            AND cot.creator_address != cf.creator_address
            AND COALESCE(cf.is_cex,0) = 0
        """, (last_bt,))
        candidate_chains = cur.fetchall()

        # Also capture the max block_time from outgoing transfers in the same transaction snapshot
        cur.execute("SELECT COALESCE(MAX(cot.block_time), ?) FROM creator_outgoing_transfers cot WHERE cot.block_time > ?", (last_bt, last_bt))
        new_bt_row = cur.fetchone()
        new_bt = int(new_bt_row[0] or last_bt) if new_bt_row else last_bt

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Step 3: Acquire lock only for insert + cursor update (short hold)
    # Even if no candidates, we still advance the cursor based on max block_time
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        if candidate_chains:
            # Insert all candidate chains
            # Adapt to actual schema: only insert columns that exist
            # Rows from query are (chain_type, source_creator, bridge_funder, target_creator,
            #                      source_tx, source_to_bridge_amount_sol, bridge_to_target_amount_sol,
            #                      source_block_time, bridge_first_detected_at, bridge_is_cex, confidence)
            # Table only has: chain_type, source_creator, bridge_funder, target_creator,
            #               source_to_bridge_amount_sol, bridge_to_target_amount_sol,
            #               source_block_time, confidence
            rows_for_insert = [
                (r[0], r[1], r[2], r[3], r[5], r[6], r[7], r[10])  # Skip indices 4,8,9
                for r in candidate_chains
            ]
            cur.executemany("""
              INSERT OR IGNORE INTO funding_chains (
                chain_type, source_creator, bridge_funder, target_creator,
                source_to_bridge_amount_sol, bridge_to_target_amount_sol,
                source_block_time, confidence
              )
              VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, rows_for_insert)

        # Always advance cursor to latest processed block_time
        # This prevents re-scanning the same outgoing transfers forever
        cur.execute("UPDATE outgoing_chain_cursor SET last_block_time=? WHERE id=1", (new_bt,))
        conn.commit()
        conn.close()


def build_coordinated_edges_incremental():
    """
    Build coordinated edges only from NEW high-confidence chains.
    Uses coordinated_edge_cursor to track max chain_id, avoiding full table scans.
    Safe against races: capture max chain_id in same snapshot as candidates.
    """
    # Step 1: Read cursor and candidate edges in explicit read transaction (ensures snapshot consistency)
    conn = _connect()
    cur = conn.cursor()
    cur.execute("BEGIN DEFERRED")  # Start explicit read transaction

    try:
        cur.execute("SELECT last_chain_id FROM coordinated_edge_cursor WHERE id=1")
        row = cur.fetchone()
        last_id = int(row[0] or 0) if row else 0

        # Step 2: Execute read-only query to find new high-confidence chains + capture max chain_id in same snapshot
        # This prevents races where newer chains arrive after we read candidates but before we update cursor
        cur.execute("""
          SELECT
            source_creator, target_creator, bridge_funder,
            source_block_time, confidence
          FROM funding_chains
          WHERE chain_id > ?
            AND chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'
            AND confidence >= 70
        """, (last_id,))
        candidate_edges = cur.fetchall()

        # Also capture the max chain_id from funding_chains in the same transaction snapshot
        cur.execute("SELECT COALESCE(MAX(chain_id), ?) FROM funding_chains WHERE chain_id > ?", (last_id, last_id))
        new_id_row = cur.fetchone()
        new_id = int(new_id_row[0] or last_id) if new_id_row else last_id

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # Step 3: Acquire lock only for insert + cursor update (short hold)
    # Even if no candidates, we still advance the cursor based on max chain_id
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        if candidate_edges:
            # Insert all candidate edges
            # candidate_edges is (source_creator, target_creator, bridge_funder, source_block_time, confidence)
            # We need to map to (creator_a, creator_b, bridge_funder, first_seen_block_time, evidence_tx, confidence)
            # evidence_tx is now NULL since source_tx doesn't exist in funding_chains
            edges_for_insert = [
                (e[0], e[1], e[2], e[3], None, e[4])  # Add NULL for evidence_tx
                for e in candidate_edges
            ]
            cur.executemany("""
              INSERT OR IGNORE INTO coordinated_creator_edges (
                creator_a, creator_b, bridge_funder,
                first_seen_block_time, evidence_tx, confidence
              )
              VALUES (?, ?, ?, ?, ?, ?)
            """, edges_for_insert)

        # Always advance cursor to latest processed chain_id
        # This prevents re-scanning the same chains forever
        cur.execute("UPDATE coordinated_edge_cursor SET last_chain_id=? WHERE id=1", (new_id,))
        conn.commit()
        conn.close()


def detect_and_update_networks_from_outgoing():
    """
    Detect creators funding funders/senders of network members and add them to networks.
    Also create new networks for creators connected via funding chains.

    Logic:
    1. Add creators that fund network members to those networks
    2. Create new networks from CREATOR_FUNDING_CHAIN patterns
    3. Expand networks based on coordinated funding activity
    """
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        try:
            import json
            from datetime import datetime
            
            # ========== PART 1: Expand existing networks ==========
            # Get all networks and their members
            cur.execute("""
                SELECT creator_address, network_name, connected_creators
                FROM creator_networks
            """)
            networks = cur.fetchall()
            
            for network in networks:
                network_creator = network['creator_address']
                network_name = network['network_name']
                connected = json.loads(network['connected_creators'] or '[]')
                all_members = {network_creator} | set(connected)
                
                # Find creators that fund funders of this network's members
                cur.execute("""
                    SELECT DISTINCT cot.creator_address
                    FROM creator_outgoing_transfers cot
                    JOIN creator_funders cf ON cot.recipient_address = cf.funder_address
                    WHERE cf.creator_address IN ({})
                    AND cot.creator_address NOT IN ({})
                """.format(
                    ','.join('?' * len(all_members)),
                    ','.join('?' * len(all_members))
                ), list(all_members) + list(all_members))
                
                new_members = set(row['creator_address'] for row in cur.fetchall())
                
                # Find creators that directly fund network members
                cur.execute("""
                    SELECT DISTINCT cot.creator_address
                    FROM creator_outgoing_transfers cot
                    WHERE cot.recipient_address IN ({})
                    AND cot.creator_address NOT IN ({})
                """.format(
                    ','.join('?' * len(all_members)),
                    ','.join('?' * len(all_members))
                ), list(all_members) + list(all_members))
                
                new_members.update(row['creator_address'] for row in cur.fetchall())
                
                # Update network if there are new members
                if new_members:
                    updated_connected = list(set(connected) | new_members)
                    cur.execute("""
                        UPDATE creator_networks
                        SET connected_creators = ?
                        WHERE network_name = ?
                    """, (json.dumps(updated_connected), network_name))
            
            # ========== PART 1.5: Detect direct creator-to-creator transfers ==========
            # Find cases where one creator directly transfers SOL to another creator
            # Check both creator_outgoing_transfers (from extractor) and creator_funders (from listener)
            cur.execute("""
                SELECT DISTINCT
                    cot.creator_address as source_creator,
                    cot.recipient_address as target_creator
                FROM creator_outgoing_transfers cot
                WHERE cot.recipient_address IN (
                    SELECT DISTINCT creator_address FROM creator_networks
                    UNION
                    SELECT DISTINCT earliest_tx_creator FROM token_analysis
                )
                AND cot.creator_address IN (
                    SELECT DISTINCT creator_address FROM creator_networks
                    UNION
                    SELECT DISTINCT earliest_tx_creator FROM token_analysis
                )
                AND cot.creator_address != cot.recipient_address

                UNION

                -- Also check creator_funders for direct creator-to-creator transfers
                SELECT DISTINCT
                    cf.funder_address as source_creator,
                    cf.creator_address as target_creator
                FROM creator_funders cf
                WHERE cf.creator_address IN (
                    SELECT DISTINCT earliest_tx_creator FROM token_analysis
                )
                AND cf.funder_address IN (
                    SELECT DISTINCT earliest_tx_creator FROM token_analysis
                )
                AND cf.funder_address != cf.creator_address
            """)

            direct_transfers = cur.fetchall()

            # Create SEPARATE organic networks for direct creator-to-creator transfers
            # These are independent of existing CEX/INFRA networks they may belong to
            for transfer in direct_transfers:
                source = transfer['source_creator']
                target = transfer['target_creator']

                import hashlib
                cluster = sorted([source, target])
                cluster_hash = hashlib.md5('|'.join(cluster).encode()).hexdigest()[:8]
                network_name = f"CreatorTransfer_{cluster_hash}"

                # Add both creators to the creator_to_creator_networks table
                # This allows tracking multiple network memberships without UNIQUE constraint issues
                cur.execute("""
                    INSERT OR IGNORE INTO creator_to_creator_networks
                    (creator_address, network_name)
                    VALUES (?, ?)
                """, (source, network_name))

                cur.execute("""
                    INSERT OR IGNORE INTO creator_to_creator_networks
                    (creator_address, network_name)
                    VALUES (?, ?)
                """, (target, network_name))

            # ========== PART 2: Create networks from funding chains ==========
            # Find creators with multiple CREATOR_FUNDING_CHAIN or COORDINATED_FUNDING patterns
            cur.execute("""
                SELECT source_creator, target_creator, COUNT(*) as chain_count
                FROM funding_chains
                WHERE chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'
                GROUP BY source_creator, target_creator
                HAVING chain_count > 0
            """)

            funding_relationships = cur.fetchall()

            # Group creators that are connected via funding chains
            creator_connections = {}
            for rel in funding_relationships:
                source = rel['source_creator']
                target = rel['target_creator']

                # Create bidirectional graph for connected creators
                if source not in creator_connections:
                    creator_connections[source] = set()
                if target not in creator_connections:
                    creator_connections[target] = set()

                creator_connections[source].add(target)
                creator_connections[target].add(source)

            # Find connected components (clusters of related creators)
            processed = set()
            networks_to_create = []

            for creator, connections in creator_connections.items():
                if creator in processed:
                    continue

                # BFS to find all connected creators
                cluster = {creator}
                queue = [creator]

                while queue:
                    current = queue.pop(0)
                    for neighbor in creator_connections.get(current, set()):
                        if neighbor not in cluster:
                            cluster.add(neighbor)
                            queue.append(neighbor)

                # Mark all as processed
                processed.update(cluster)

                # Create new network for this creator cluster regardless of existing network membership
                # This allows direct creator-to-creator relationships to be tracked separately
                # even if the creators are already members of other networks (e.g., CEX networks)
                if len(cluster) > 1:
                    networks_to_create.append(cluster)

            # Create new networks
            for cluster in networks_to_create:
                if len(cluster) < 2:
                    continue

                cluster_list = sorted(list(cluster))
                primary_creator = cluster_list[0]
                connected_creators = cluster_list[1:]

                # Create network name that's unique per cluster
                # Use hash of all members to ensure uniqueness even with same primary creator
                import hashlib
                cluster_hash = hashlib.md5('|'.join(cluster_list).encode()).hexdigest()[:8]
                network_name = f"CoordinatedFunding_{cluster_hash}"

                # Check if this network already exists in creator_networks OR creator_to_creator_networks
                # (might have been created in PART 1.5 as CreatorTransfer for direct transfers)
                cur.execute("""
                    SELECT COUNT(*) as count FROM creator_networks
                    WHERE network_name = ?
                """, (network_name,))

                if cur.fetchone()['count'] == 0:
                    # Also check if this pair already exists as a CreatorTransfer network
                    if len(cluster_list) == 2:
                        creator1 = cluster_list[0]
                        creator2 = cluster_list[1]
                        cur.execute("""
                            SELECT DISTINCT network_name FROM creator_to_creator_networks
                            WHERE (creator_address = ? OR creator_address = ?)
                            AND network_name LIKE 'CreatorTransfer_%'
                        """, (creator1, creator2))
                        existing_c2c = cur.fetchone()

                        # If a CreatorTransfer network already exists for this pair, skip creation
                        if existing_c2c:
                            continue

                    try:
                        cur.execute("""
                            INSERT INTO creator_networks
                            (creator_address, network_name, connected_creators, shared_destinations, network_size, network_risk_level, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            primary_creator,
                            network_name,
                            json.dumps(connected_creators),
                            json.dumps([]),  # Empty shared destinations for now
                            len(cluster),
                            'HIGH',  # Creators funding other creators = high risk
                            datetime.now().isoformat()
                        ))
                    except:
                        # If primary creator already exists, skip (they're already in a network)
                        pass
            
            conn.commit()
            print("[OUTGOING] ✅ Network detection complete", flush=True)

        except Exception as e:
            conn.rollback()
            print(f"[OUTGOING] ⚠️  Network detection error: {e}", flush=True)
        finally:
            conn.close()


def calculate_and_store_self_funding():
    """
    Calculate self-funding percentages for all creators and store in database.
    A creator is TRULY self-funded only if:
    1. 50%+ of their funders only fund that specific creator (isolated funder group)
    AND
    2. The creator sends money BACK to these funders (circular funding)
    """
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        try:
            # Get all creators with funders
            cur.execute("""
                SELECT DISTINCT creator_address FROM creator_funders
            """)
            creators = [row['creator_address'] for row in cur.fetchall()]

            updates = []

            for creator in creators:
                # Get all funders of this creator
                cur.execute("""
                    SELECT DISTINCT funder_address FROM creator_funders
                    WHERE creator_address = ?
                """, (creator,))
                funders = [row['funder_address'] for row in cur.fetchall()]

                if not funders:
                    continue

                # Count how many funders only fund this creator
                self_funding_intermediates = 0
                for funder in funders:
                    cur.execute("""
                        SELECT COUNT(DISTINCT creator_address) as count
                        FROM creator_funders
                        WHERE funder_address = ?
                    """, (funder,))
                    funder_count = cur.fetchone()['count']
                    if funder_count == 1:
                        self_funding_intermediates += 1

                total_funders = len(funders)
                self_funding_percentage = (self_funding_intermediates / total_funders) * 100 if total_funders > 0 else 0

                # Only set is_self_funding = 1 if BOTH conditions are met:
                # 1. 50%+ of funders only fund this creator
                # 2. Creator sends money back to at least one of these funders (circular)
                has_isolated_funders = self_funding_percentage >= 50

                has_circular_funding = False
                if has_isolated_funders:
                    # Check if creator sends SOL back to any of their funders
                    cur.execute("""
                        SELECT COUNT(*) as count FROM creator_outgoing_transfers cot
                        WHERE cot.creator_address = ?
                        AND cot.recipient_address IN (
                            SELECT funder_address FROM creator_funders
                            WHERE creator_address = ?
                        )
                    """, (creator, creator))
                    circular_result = cur.fetchone()
                    has_circular_funding = circular_result['count'] > 0 if circular_result else False

                is_self_funding = 1 if (has_isolated_funders and has_circular_funding) else 0

                updates.append((
                    self_funding_intermediates,
                    total_funders,
                    self_funding_percentage,
                    is_self_funding,
                    creator
                ))

            # Batch update
            cur.executemany("""
                INSERT OR REPLACE INTO creator_self_funding
                (self_funding_intermediates, total_funders, self_funding_percentage, is_self_funding, creator_address, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, updates)

            conn.commit()
            print(f"[OUTGOING] ✅ Recalculated self-funding for {len(updates)} creators (only true self-funding with circular flows)", flush=True)

        except Exception as e:
            conn.rollback()
            print(f"[OUTGOING] ⚠️  Self-funding calculation error: {e}", flush=True)
        finally:
            conn.close()


async def scan_once(concurrency: int = 25):
    """
    Scan all creators for new outgoing transfers.

    Flow:
    1. Get 1000 creators
    2. Load all cursors in one batch read
    3. Concurrently fetch new signatures (1 RPC call per creator)
    4. Batch parse new signatures via Helius Enhanced (100 sigs/request)
    5. Extract outgoing SOL transfers
    6. Write rows fast
    7. Update all cursors in one batch
    8. Build chains
    """
    creators = get_creators(limit=1000)
    if not creators:
        print("[OUTGOING] ℹ️ No creators found", flush=True)
        return

    creator_set = set(creators)

    print(f"[OUTGOING] 🔍 Scanning {len(creators)} creators...", flush=True)

    # Preload all cursors at once (reduces lock pressure)
    cursors = load_all_cursors(creators)

    # Concurrent signature fetches + safe result collection
    async with aiohttp.ClientSession() as session:
        sem = asyncio.Semaphore(concurrency)

        async def handle_creator(c: str) -> Tuple[List[str], Optional[Tuple[str, int]]]:
            """Returns (fresh_sigs, (newest_sig, newest_slot))"""
            async with sem:
                last_sig, _ = cursors.get(c, (None, None))
                sigs = await rpc_get_signatures(session, c, limit=25)

                fresh = []
                newest_sig = None
                newest_slot = None

                for item in sigs:
                    s = item.get("signature")
                    if not s:
                        continue

                    if newest_sig is None:
                        newest_sig = s
                        newest_slot = item.get("slot")

                    # Stop at last known signature
                    if last_sig and s == last_sig:
                        break

                    # Only include successful txs
                    if item.get("err") is None:
                        fresh.append(s)

                creator_update = None
                if newest_sig:
                    creator_update = (newest_sig, int(newest_slot or 0))

                return (fresh, creator_update)

        # Concurrent signature fetches (1000 RPC calls)
        tasks = [handle_creator(c) for c in creators]
        results = await asyncio.gather(*tasks)

    # Safely merge results from concurrent tasks
    new_sigs: List[str] = []
    cursor_updates: List[Tuple[str, str, int]] = []

    for c, (fresh, creator_update) in zip(creators, results):
        new_sigs.extend(fresh)
        if creator_update:
            newest_sig, newest_slot = creator_update
            cursor_updates.append((c, newest_sig, newest_slot))

    print(f"[OUTGOING] 📋 Collected {len(new_sigs)} new signatures", flush=True)

    # Batch enhanced parse (100 sigs/request)
    rows_all = []
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(new_sigs), 100):
            chunk = new_sigs[i:i+100]
            txs = await helius_enhanced_parse(session, chunk)
            rows_all.extend(extract_outgoing_sol(txs, creator_set))

    print(f"[OUTGOING] ✍️ Extracted {len(rows_all)} outgoing transfers", flush=True)

    # Write rows (fast)
    insert_outgoing_rows(rows_all)

    # Update cursors (one batch, not 1000 individual commits)
    batch_update_cursors(cursor_updates)

    # Build chains incrementally from only new outgoing transfers (keeps lock short)
    build_funding_chains_incremental()
    build_coordinated_edges_incremental()

    # Detect and update network membership based on outgoing transfers
    detect_and_update_networks_from_outgoing()

    # Calculate and store self-funding metrics for all creators
    calculate_and_store_self_funding()

    print(f"[OUTGOING] ✅ Scan complete: creators={len(creators)} new_sigs={len(new_sigs)} new_rows={len(rows_all)}", flush=True)


async def run_forever(interval_seconds: int = 3600):
    """Run scanner loop forever"""
    ensure_tables()

    while True:
        t0 = time.time()
        try:
            await scan_once()
        except Exception as e:
            print(f"[OUTGOING] ❌ Error: {e}", flush=True)
            import traceback
            traceback.print_exc()

        dt = time.time() - t0
        sleep_for = max(5, interval_seconds - dt)
        print(f"[OUTGOING] ⏰ Next scan in {sleep_for:.0f}s", flush=True)
        await asyncio.sleep(sleep_for)


if __name__ == "__main__":
    # Run every 12 hours to ensure all creators scanned once per day
    # With 1453 creators and 1000 per scan, 2 scans cover all creators daily
    asyncio.run(run_forever(43200))  # 43200 seconds = 12 hours
