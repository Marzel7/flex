#!/usr/bin/env python3
"""
Creator Outgoing Transfer Extractor

Detects when creators send SOL to addresses (especially funders).
Runs hourly to catch creator→funder→creator relationships.

Pattern: 1000 creators × 1 sig lookup each + batched enhanced parse of new sigs
"""

import os
import asyncio
import time
import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import aiohttp
import threading

DB_PATH = os.getenv("DB_PATH", "pumpswap_tokens.db")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}" if HELIUS_API_KEY else "https://api.mainnet-beta.solana.com"
HELIUS_ENHANCED = f"https://api-mainnet.helius-rpc.com/v0/transactions?api-key={HELIUS_API_KEY}"

# Global write lock (shared across modules)
DB_WRITE_LOCK = threading.RLock()

def ensure_tables():
    """Create creator_outgoing_transfers and cursor tracking tables"""
    with DB_WRITE_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
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
          block_time INTEGER,
          first_detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          recipient_type TEXT DEFAULT 'unknown',
          is_cex INTEGER DEFAULT 0,
          cex_exchange TEXT,
          cex_type TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_cot_creator ON creator_outgoing_transfers(creator_address);
        CREATE INDEX IF NOT EXISTS idx_cot_recipient ON creator_outgoing_transfers(recipient_address);

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
        """)
        conn.commit()
        conn.close()
        print("[OUTGOING] ✅ Tables ensured", flush=True)


def get_creators(limit: int = 1000) -> List[str]:
    """Get all unique creators from token_analysis"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
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


def get_cursor(creator: str) -> Tuple[Optional[str], Optional[int]]:
    """Get last signature and slot for a creator"""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    cur = conn.cursor()
    cur.execute("SELECT last_signature, last_slot FROM creator_sig_cursors WHERE creator_address = ?", (creator,))
    row = cur.fetchone()
    conn.close()
    return (row[0], row[1]) if row else (None, None)


def update_cursor(creator: str, last_sig: str, last_slot: int):
    """Update cursor after processing new signatures"""
    with DB_WRITE_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        cur = conn.cursor()
        cur.execute("""
          INSERT INTO creator_sig_cursors(creator_address, last_signature, last_slot, updated_at)
          VALUES(?, ?, ?, datetime('now'))
          ON CONFLICT(creator_address) DO UPDATE SET
            last_signature=excluded.last_signature,
            last_slot=excluded.last_slot,
            updated_at=datetime('now')
        """, (creator, last_sig, last_slot))
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
    """Batch parse transaction signatures via Helius Enhanced API (max 100/request)"""
    if not sigs:
        return []

    body = {"transactions": sigs}
    try:
        async with session.post(HELIUS_ENHANCED, json=body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                return []
            return await resp.json()
    except Exception as e:
        print(f"[OUTGOING] ⚠️ helius_enhanced_parse error: {e}", flush=True)
        return []


def extract_outgoing_sol(transactions: List[dict], creator_set: set) -> List[Tuple]:
    """
    Extract outgoing SOL transfers from transactions where creator is the sender.
    Returns rows: (creator, recipient, amount_sol, signature, block_time)
    """
    rows = []
    for tx in transactions:
        sig = tx.get("signature")
        slot = tx.get("slot")
        ts = tx.get("timestamp")  # seconds

        if not sig:
            continue

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

    with DB_WRITE_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        cur = conn.cursor()
        cur.executemany("""
          INSERT OR IGNORE INTO creator_outgoing_transfers
            (creator_address, recipient_address, amount_sol, transaction_signature, slot, block_time)
          VALUES (?, ?, ?, ?, ?, ?)
        """, rows)
        conn.commit()
        conn.close()


def build_funding_chains():
    """Build funding_chains from creator_outgoing_transfers + creator_funders join"""
    with DB_WRITE_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        cur = conn.cursor()

        # Insert new chains where creator sends to a funder who funds another creator
        cur.execute("""
          INSERT OR IGNORE INTO funding_chains (
            chain_type,
            source_creator,
            bridge_funder,
            target_creator,
            source_tx,
            source_to_bridge_amount_sol,
            bridge_to_target_amount_sol,
            source_block_time,
            bridge_first_detected_at,
            bridge_is_cex,
            confidence
          )
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
            cot.creator_address IS NOT NULL
            AND cf.creator_address IS NOT NULL
            AND cot.creator_address != cf.creator_address
            AND COALESCE(cf.is_cex,0) = 0
        """)

        conn.commit()
        conn.close()


def build_coordinated_edges():
    """Build coordinated_creator_edges from high-confidence funding chains"""
    with DB_WRITE_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        cur = conn.cursor()

        cur.execute("""
          INSERT OR IGNORE INTO coordinated_creator_edges (
            creator_a,
            creator_b,
            bridge_funder,
            first_seen_block_time,
            evidence_tx,
            confidence
          )
          SELECT
            source_creator AS creator_a,
            target_creator AS creator_b,
            bridge_funder,
            source_block_time,
            source_tx,
            confidence
          FROM funding_chains
          WHERE chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'
            AND confidence >= 70
        """)

        conn.commit()
        conn.close()


async def scan_once(concurrency: int = 25):
    """
    Scan all creators for new outgoing transfers.

    Flow:
    1. Get 1000 creators
    2. Concurrently fetch new signatures (1 RPC call per creator)
    3. Batch parse new signatures via Helius Enhanced (100 sigs/request)
    4. Extract outgoing SOL transfers
    5. Write rows fast
    6. Update cursors
    7. Build chains
    """
    creators = get_creators(limit=1000)
    if not creators:
        print("[OUTGOING] ℹ️ No creators found", flush=True)
        return

    creator_set = set(creators)
    new_sigs: List[str] = []
    creator_latest: Dict[str, Tuple[str, int]] = {}

    print(f"[OUTGOING] 🔍 Scanning {len(creators)} creators...", flush=True)

    async with aiohttp.ClientSession() as session:
        sem = asyncio.Semaphore(concurrency)

        async def handle_creator(c: str):
            async with sem:
                last_sig, _ = get_cursor(c)
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

                if newest_sig:
                    creator_latest[c] = (newest_sig, int(newest_slot or 0))

                if fresh:
                    new_sigs.extend(fresh)

        # Concurrent signature fetches (1000 RPC calls)
        await asyncio.gather(*(handle_creator(c) for c in creators))

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

    # Update cursors (fast)
    for c, (sig, slot) in creator_latest.items():
        update_cursor(c, sig, slot)

    # Build chains from new outgoing transfers
    build_funding_chains()
    build_coordinated_edges()

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
