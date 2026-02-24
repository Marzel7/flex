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

DB_PATH = os.getenv("DB_PATH", "pumpswap_tokens.db")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"
HELIUS_ENHANCED = f"https://api-mainnet.helius-rpc.com/v0/transactions?api-key={HELIUS_API_KEY}"


def _connect():
    """Create connection with optimal PRAGMA settings"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def ensure_tables():
    """Create all tables with proper schema and indexes"""
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
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

        CREATE UNIQUE INDEX IF NOT EXISTS uq_funding_chain
          ON funding_chains(chain_type, source_tx, target_creator);

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

        # Create priority scanning view for intelligent creator selection
        try:
            conn = _connect()
            cur = conn.cursor()
            cur.execute("""
            CREATE VIEW IF NOT EXISTS creator_scan_priority AS
            WITH creator_stats AS (
              SELECT earliest_tx_creator AS creator, MAX(ta.created_at) AS last_launch_at, COUNT(*) AS launch_count_30d
              FROM token_analysis ta WHERE ta.earliest_tx_creator IS NOT NULL AND ta.created_at >= datetime('now','-30 days')
              GROUP BY ta.earliest_tx_creator
            ),
            scan_state AS (SELECT c.creator_address AS creator, c.updated_at AS last_scanned_at FROM creator_sig_cursors c),
            risk AS (SELECT b.creator_address AS creator, b.reputation, COALESCE(b.connected_to_malicious,0) AS connected_to_malicious FROM creator_blocklist b),
            funding AS (SELECT cf.creator_address AS creator, MAX(cf.first_detected_at) AS last_funded_at, SUM(CASE WHEN cf.is_cex=0 THEN 1 ELSE 0 END) AS real_funder_edges FROM creator_funders cf GROUP BY cf.creator_address)
            SELECT s.creator, 
              ((CASE WHEN datetime(cs.last_launch_at) >= datetime('now','-6 hours') THEN 1000 ELSE 0 END) +
               (CASE WHEN r.reputation='MALICIOUS' THEN 900 WHEN r.reputation='SUSPICIOUS' THEN 600 ELSE 0 END) +
               (CASE WHEN r.connected_to_malicious=1 THEN 500 ELSE 0 END) +
               (CASE WHEN datetime(f.last_funded_at) >= datetime('now','-6 hours') THEN 300 ELSE 0 END) +
               (CASE WHEN COALESCE(f.real_funder_edges,0) > 0 THEN 200 ELSE 0 END) +
               (CASE WHEN ss.last_scanned_at IS NULL THEN 150 WHEN datetime(ss.last_scanned_at) < datetime('now','-24 hours') THEN 100 ELSE 0 END) +
               (MIN(COALESCE(cs.launch_count_30d,0), 20) * 10)) AS priority_score
            FROM (SELECT DISTINCT earliest_tx_creator AS creator FROM token_analysis WHERE earliest_tx_creator IS NOT NULL) s
            LEFT JOIN creator_stats cs ON cs.creator = s.creator
            LEFT JOIN scan_state ss ON ss.creator = s.creator
            LEFT JOIN risk r ON r.creator = s.creator
            LEFT JOIN funding f ON f.creator = s.creator
            """)
            conn.commit()
            conn.close()
        except sqlite3.OperationalError as e:
            # View creation may fail if dependencies don't exist yet, that's okay
            pass

        print("[OUTGOING] ✅ Tables ensured", flush=True)


def get_creators(limit: int = 1000) -> List[str]:
    """Get creators prioritized by activity, risk, and scan state.
    
    Priority scoring considers:
    - Recent launches (1000 points if <6 hours)
    - Risk reputation (900 for MALICIOUS, 600 for SUSPICIOUS)
    - Connected to malicious (500 points)
    - Recently funded (300 points if <6 hours)
    - Has real funders (200 points)
    - Never scanned (150 points) or >24 hours stale (100 points)
    - Recent launch count (up to 200 points)
    
    Falls back to chronological ordering if priority view unavailable.
    """
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    try:
        # Try priority-based scanning first
        cur.execute("""
          SELECT creator
          FROM creator_scan_priority
          ORDER BY priority_score DESC
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
        cur.execute(f"""
            SELECT creator_address, last_signature, last_slot
            FROM creator_sig_cursors
            WHERE creator_address IN ({qmarks})
        """, chunk)
        for addr, sig, slot in cur.fetchall():
            cursors[addr] = (sig, slot)

    conn.close()
    return cursors


def batch_update_cursors(rows: List[Tuple[str, str, int]]):
    """
    One transaction upsert for all creators (major lock reduction).
    This replaces 1000 individual update_cursor() calls.
    Uses cross-process lock to serialize with listener.
    """
    if not rows:
        return

    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        cur.executemany("""
          INSERT INTO creator_sig_cursors(creator_address, last_signature, last_slot, updated_at)
          VALUES(?, ?, ?, datetime('now'))
          ON CONFLICT(creator_address) DO UPDATE SET
            last_signature=excluded.last_signature,
            last_slot=excluded.last_slot,
            updated_at=datetime('now')
        """, rows)
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
    """Insert outgoing transfer rows (fast batch write)"""
    if not rows:
        return

    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        cur.executemany("""
          INSERT OR IGNORE INTO creator_outgoing_transfers
            (creator_address, recipient_address, amount_sol, transaction_signature, slot, block_time)
          VALUES (?, ?, ?, ?, ?, ?)
        """, rows)
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
            cur.executemany("""
              INSERT OR IGNORE INTO funding_chains (
                chain_type, source_creator, bridge_funder, target_creator,
                source_tx, source_to_bridge_amount_sol, bridge_to_target_amount_sol,
                source_block_time, bridge_first_detected_at, bridge_is_cex, confidence
              )
              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, candidate_chains)

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
            source_block_time, source_tx, confidence
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
            cur.executemany("""
              INSERT OR IGNORE INTO coordinated_creator_edges (
                creator_a, creator_b, bridge_funder,
                first_seen_block_time, evidence_tx, confidence
              )
              VALUES (?, ?, ?, ?, ?, ?)
            """, candidate_edges)

        # Always advance cursor to latest processed chain_id
        # This prevents re-scanning the same chains forever
        cur.execute("UPDATE coordinated_edge_cursor SET last_chain_id=? WHERE id=1", (new_id,))
        conn.commit()
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
    asyncio.run(run_forever(3600))
