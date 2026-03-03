#!/usr/bin/env python3
"""
Creator Outgoing Transfer Extractor

Scan cycle:
- Up to 1000 creators per cycle
- getSignaturesForAddress per creator (page 1 always; page 2 only if enabled + needed)
- Batch enhanced parse for new sigs (100 sigs/request)
- Write outgoing edges + build chains + build coordinated edges

Concurrency-safe + lock-optimized:
- Preload all cursors in one read
- Preload all before-cursors in one read
- Batch update cursors in one transaction
- Batch save before-cursors in one transaction
- Concurrent signature fetches + safe merge

Metrics:
- Counts *attempts* vs *successful* requests separately
- "Local credits" computed from successful calls (closest to dashboard)
"""

import os
import asyncio
import time
import sqlite3
import random
from typing import List, Dict, Tuple, Optional
import aiohttp

from db_global_lock import db_write_lock_global

# Import RPC metrics recorder for monitoring
try:
    from rpc_metrics_recorder import record_request, initialize_recorder
    initialize_recorder(plan_monthly_credits=50_000_000)
except ImportError:
    def record_request(*args, **kwargs):
        pass  # No-op if metrics recorder not available

DB_PATH = os.getenv("DB_PATH", "flex_complete_database.db")

# Helius API keys (from tests/test_pumpswap_listener.py)
RPC_KEYS = [
    ("a132b19d-9b44-4c71-8e6f-d320d9f351c6", "GITHUB"),     # Primary (best quota)
    ("f084fae8-d111-4337-9960-2d9c5e02a726", "MARZEL"),     # Fallback 1
    ("0ae07551-32df-4d9d-af2a-1925fb7f561f", "JEZZA"),      # Fallback 2
    ("3b2917b8-9bed-4e2e-8c05-a74adbc34bb8", "NEW_KEY"),    # Fallback 3
]

HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "") or RPC_KEYS[0][0]
HELIUS_MONITORING_API_KEY = os.getenv("HELIUS_MONITORING_API_KEY", "")

# Use monitoring key if available, fall back to regular key
_RPC_KEY = HELIUS_MONITORING_API_KEY or HELIUS_API_KEY

RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={_RPC_KEY}"
HELIUS_ENHANCED = f"https://api-mainnet.helius-rpc.com/v0/transactions?api-key={_RPC_KEY}"

# Rate limiting configuration
OUTGOING_RPS = 8.0
OUTGOING_MAX_RETRIES = 3

# Full scan target: 1 page per creator per cycle.
# Set to 2 if you want adaptive page-2 (still "full scan" on page 1).
MAX_PAGES_PER_CYCLE = 1

OUTGOING_CONCURRENCY = 3

# Helius credit model (keep in one place)
CREDITS_GSFA = 10
CREDITS_ENHANCED_BATCH = 100

# Global rate limiter instance
_outgoing_limiter = None

# Global instrumentation counters
GSFA_ATTEMPTS = 0
GSFA_SUCCESS = 0
ENH_ATTEMPTS = 0
ENH_SUCCESS = 0
RPC_SIGNATURE_CALL_COUNT = 0


class RateLimiter:
    """Async rate limiter using token bucket algorithm (no external deps)."""
    def __init__(self, rate_per_sec: float):
        self._interval = 1.0 / max(rate_per_sec, 0.1)
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
            self._next = max(self._next + self._interval, time.monotonic() + self._interval)


async def sleep_backoff(attempt: int, retry_after_s: Optional[float]):
    """Sleep with exponential backoff + jitter, respecting Retry-After header."""
    if retry_after_s is not None:
        await asyncio.sleep(retry_after_s + random.uniform(0, 0.25))
    else:
        await asyncio.sleep(min(2 ** attempt, 30) + random.uniform(0, 0.25))


def get_outgoing_limiter() -> RateLimiter:
    global _outgoing_limiter
    if _outgoing_limiter is None:
        _outgoing_limiter = RateLimiter(OUTGOING_RPS)
    return _outgoing_limiter


def _connect():
    """Create connection with optimal PRAGMA settings."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def ensure_tables():
    """Create all tables with proper schema and indexes."""
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

            CREATE TABLE IF NOT EXISTS creator_outgoing_cursor (
              creator_address TEXT PRIMARY KEY,
              before_signature TEXT,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            conn.commit()
        except sqlite3.OperationalError as e:
            if 'source_tx' not in str(e):
                raise
            print("[OUTGOING] ℹ️ Schema evolved - ignoring index on non-existent column", flush=True)
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
            pass

        print("[OUTGOING] ✅ Tables ensured", flush=True)


def get_creators(limit: int = 1000) -> List[str]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
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
            cur.execute(f"""
                SELECT creator_address, last_signature, last_slot
                FROM creator_sig_cursors
                WHERE creator_address IN ({qmarks})
            """, chunk)
            for addr, sig, slot in cur.fetchall():
                cursors[addr] = (sig, slot)
        except sqlite3.OperationalError:
            cur.execute(f"""
                SELECT creator_address, cursor_position
                FROM creator_sig_cursors
                WHERE creator_address IN ({qmarks})
            """, chunk)
            for addr, pos in cur.fetchall():
                cursors[addr] = (None, pos if pos else None)

    conn.close()
    return cursors


def batch_update_cursors(rows: List[Tuple[str, str, int]]):
    if not rows:
        return

    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA table_info(creator_sig_cursors)")
            cols = [col[1] for col in cur.fetchall()]

            if 'cursor_position' in cols:
                for creator_addr, sig, slot in rows:
                    cur.execute("""
                      INSERT OR REPLACE INTO creator_sig_cursors(creator_address, cursor_position, updated_at)
                      VALUES(?, ?, datetime('now'))
                    """, (creator_addr, slot))
            else:
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


def load_all_before_cursors(creators: List[str]) -> Dict[str, Optional[str]]:
    if not creators:
        return {}

    conn = _connect()
    cur = conn.cursor()
    result: Dict[str, Optional[str]] = {}

    chunk_size = 800
    for i in range(0, len(creators), chunk_size):
        chunk = creators[i:i+chunk_size]
        qmarks = ",".join(["?"] * len(chunk))
        cur.execute(f"""
            SELECT creator_address, before_signature
            FROM creator_outgoing_cursor
            WHERE creator_address IN ({qmarks})
        """, chunk)
        for addr, before_sig in cur.fetchall():
            result[addr] = before_sig

    conn.close()
    return result


def batch_save_before_cursors(rows: List[Tuple[str, Optional[str]]]):
    if not rows:
        return

    # Filter out None before_signatures (don’t overwrite with NULL)
    rows = [(a, b) for (a, b) in rows if b]
    if not rows:
        return

    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            cur.executemany("""
                INSERT INTO creator_outgoing_cursor(creator_address, before_signature, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(creator_address) DO UPDATE SET
                  before_signature=excluded.before_signature,
                  updated_at=datetime('now')
            """, rows)
            conn.commit()
        finally:
            conn.close()


async def rpc_get_signatures(
    session: aiohttp.ClientSession,
    address: str,
    limit: int = 25,
    before: Optional[str] = None,
    source_file: str = "creator_outgoing_extractor"
) -> List[dict]:
    """
    Fetch recent signatures for an address.

    Instrumentation:
    - GSFA_ATTEMPTS increments per HTTP attempt
    - GSFA_SUCCESS increments only for HTTP 200 (closest to billable)
    """
    global GSFA_ATTEMPTS, GSFA_SUCCESS, RPC_SIGNATURE_CALL_COUNT

    limiter = get_outgoing_limiter()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": limit, **({"before": before} if before else {})}],
    }

    for attempt in range(OUTGOING_MAX_RETRIES + 1):
        await limiter.acquire()
        start_time = time.time()
        GSFA_ATTEMPTS += 1
        RPC_SIGNATURE_CALL_COUNT += 1

        try:
            async with session.post(
                RPC_HTTP,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                latency_ms = (time.time() - start_time) * 1000

                retry_after_hdr = resp.headers.get("Retry-After")
                retry_after_s = float(retry_after_hdr) if retry_after_hdr else None

                record_request(
                    section="creator_outgoing_scan",
                    provider="helius_rpc",
                    method="getSignaturesForAddress",
                    status_code=resp.status,
                    latency_ms=latency_ms,
                    mode="background",
                    source_file=source_file,
                    retries=attempt,
                    retry_after_ms=(retry_after_s * 1000) if retry_after_s else None,
                )

                if resp.status == 200:
                    GSFA_SUCCESS += 1
                    data = await resp.json()
                    return data.get("result") or []

                if resp.status == 429 and attempt < OUTGOING_MAX_RETRIES:
                    await sleep_backoff(attempt, retry_after_s)
                    continue

                return []

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            record_request(
                section="creator_outgoing_scan",
                provider="helius_rpc",
                method="getSignaturesForAddress",
                status_code=0,
                latency_ms=latency_ms,
                mode="background",
                source_file=source_file,
                retries=attempt,
                error=str(e),
            )
            if attempt < OUTGOING_MAX_RETRIES:
                await sleep_backoff(attempt, None)
                continue
            return []

    return []


async def helius_enhanced_parse(session: aiohttp.ClientSession, sigs: List[str]) -> List[dict]:
    """
    Batch parse transaction signatures via Helius Enhanced API (max 100/request).

    Instrumentation:
    - ENH_ATTEMPTS increments per HTTP attempt
    - ENH_SUCCESS increments only for HTTP 200 (closest to billable)
    """
    global ENH_ATTEMPTS, ENH_SUCCESS

    if not sigs:
        return []

    body = {"transactions": sigs}
    max_retries = 3
    backoff_times = [0.5, 1.0, 2.0]

    for attempt in range(max_retries):
        ENH_ATTEMPTS += 1
        try:
            start_time = time.time()
            async with session.post(HELIUS_ENHANCED, json=body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                latency_ms = (time.time() - start_time) * 1000

                if resp.status == 429:
                    if attempt < max_retries - 1:
                        await asyncio.sleep(backoff_times[attempt])
                        continue
                    return []

                if resp.status != 200:
                    body_snippet = await resp.text()
                    if len(body_snippet) > 200:
                        body_snippet = body_snippet[:200] + "..."
                    print(f"[OUTGOING] ⚠️ Helius enhanced status {resp.status}: {body_snippet}", flush=True)
                    return []

                ENH_SUCCESS += 1
                data = await resp.json()
                # Record request with batch multiplier (Helius charges per-transaction)
                batch_size = len(sigs)
                for i in range(batch_size):
                    record_request(
                        section="creator_outgoing_scan",
                        provider="helius_enhanced",
                        method="helius_enhanced_transactions_batch",
                        status_code=resp.status,
                        latency_ms=latency_ms,
                        mode="background",
                    )

                if isinstance(data, dict) and "result" in data:
                    data = data["result"]
                return data if isinstance(data, list) else []

        except asyncio.TimeoutError:
            if attempt < max_retries - 1:
                await asyncio.sleep(backoff_times[attempt])
                continue
            return []
        except Exception as e:
            print(f"[OUTGOING] ⚠️ helius_enhanced_parse error: {e}", flush=True)
            return []

    return []


def extract_outgoing_sol(transactions: List[dict], creator_set: set) -> List[Tuple]:
    rows = []
    for tx in (transactions or []):
        if not isinstance(tx, dict):
            continue

        sig = tx.get("signature")
        if not sig:
            continue

        slot = int(tx.get("slot") or 0)
        ts = int(tx.get("timestamp") or 0)

        for nt in tx.get("nativeTransfers", []) or []:
            frm = nt.get("fromUserAccount")
            to = nt.get("toUserAccount")
            amt = nt.get("amount")  # lamports

            if not frm or not to or not amt:
                continue

            if frm in creator_set and int(amt) > 0:
                amount_sol = float(amt) / 1e9
                rows.append((frm, to, amount_sol, sig, slot, ts))

    return rows


def insert_outgoing_rows(rows: List[Tuple]):
    if not rows:
        return

    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        cur.execute("PRAGMA table_info(creator_outgoing_transfers)")
        existing_cols = {col[1] for col in cur.fetchall()}

        if 'slot' in existing_cols:
            cur.executemany("""
              INSERT OR IGNORE INTO creator_outgoing_transfers
                (creator_address, recipient_address, amount_sol, transaction_signature, slot, block_time)
              VALUES (?, ?, ?, ?, ?, ?)
            """, rows)
        else:
            rows_without_slot = [(r[0], r[1], r[2], r[3], r[5] if len(r) > 5 else None) for r in rows]
            cur.executemany("""
              INSERT OR IGNORE INTO creator_outgoing_transfers
                (creator_address, recipient_address, amount_sol, transaction_signature, block_time)
              VALUES (?, ?, ?, ?, ?)
            """, rows_without_slot)

        conn.commit()
        conn.close()


def build_funding_chains_incremental():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("BEGIN DEFERRED")

    try:
        cur.execute("SELECT last_block_time FROM outgoing_chain_cursor WHERE id=1")
        row = cur.fetchone()
        last_bt = int(row[0] or 0) if row else 0

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

        cur.execute("SELECT COALESCE(MAX(cot.block_time), ?) FROM creator_outgoing_transfers cot WHERE cot.block_time > ?", (last_bt, last_bt))
        new_bt_row = cur.fetchone()
        new_bt = int(new_bt_row[0] or last_bt) if new_bt_row else last_bt

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        if candidate_chains:
            rows_for_insert = [
                (r[0], r[1], r[2], r[3], r[5], r[6], r[7], r[10])
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

        cur.execute("UPDATE outgoing_chain_cursor SET last_block_time=? WHERE id=1", (new_bt,))
        conn.commit()
        conn.close()


def build_coordinated_edges_incremental():
    conn = _connect()
    cur = conn.cursor()
    cur.execute("BEGIN DEFERRED")

    try:
        cur.execute("SELECT last_chain_id FROM coordinated_edge_cursor WHERE id=1")
        row = cur.fetchone()
        last_id = int(row[0] or 0) if row else 0

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

        cur.execute("SELECT COALESCE(MAX(chain_id), ?) FROM funding_chains WHERE chain_id > ?", (last_id, last_id))
        new_id_row = cur.fetchone()
        new_id = int(new_id_row[0] or last_id) if new_id_row else last_id

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        if candidate_edges:
            edges_for_insert = [(e[0], e[1], e[2], e[3], None, e[4]) for e in candidate_edges]
            cur.executemany("""
              INSERT OR IGNORE INTO coordinated_creator_edges (
                creator_a, creator_b, bridge_funder,
                first_seen_block_time, evidence_tx, confidence
              )
              VALUES (?, ?, ?, ?, ?, ?)
            """, edges_for_insert)

        cur.execute("UPDATE coordinated_edge_cursor SET last_chain_id=? WHERE id=1", (new_id,))
        conn.commit()
        conn.close()


def detect_and_update_networks_from_outgoing():
    # unchanged (kept as-is in your version)
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()
        try:
            import json
            from datetime import datetime
            # PART 1: Expand existing networks
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

                if new_members:
                    updated_connected = list(set(connected) | new_members)
                    cur.execute("""
                        UPDATE creator_networks
                        SET connected_creators = ?
                        WHERE network_name = ?
                    """, (json.dumps(updated_connected), network_name))

            # PART 1.5 direct creator-to-creator transfers
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

            for transfer in direct_transfers:
                source = transfer['source_creator']
                target = transfer['target_creator']

                import hashlib
                cluster = sorted([source, target])
                cluster_hash = hashlib.md5('|'.join(cluster).encode()).hexdigest()[:8]
                network_name = f"CreatorTransfer_{cluster_hash}"

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

            # PART 2 networks from funding chains
            cur.execute("""
                SELECT source_creator, target_creator, COUNT(*) as chain_count
                FROM funding_chains
                WHERE chain_type = 'CREATOR_TO_FUNDER_TO_CREATOR'
                GROUP BY source_creator, target_creator
                HAVING chain_count > 0
            """)
            funding_relationships = cur.fetchall()

            creator_connections = {}
            for rel in funding_relationships:
                source = rel['source_creator']
                target = rel['target_creator']
                creator_connections.setdefault(source, set()).add(target)
                creator_connections.setdefault(target, set()).add(source)

            processed = set()
            networks_to_create = []

            for creator, connections in creator_connections.items():
                if creator in processed:
                    continue
                cluster = {creator}
                queue = [creator]
                while queue:
                    current = queue.pop(0)
                    for neighbor in creator_connections.get(current, set()):
                        if neighbor not in cluster:
                            cluster.add(neighbor)
                            queue.append(neighbor)
                processed.update(cluster)
                if len(cluster) > 1:
                    networks_to_create.append(cluster)

            for cluster in networks_to_create:
                if len(cluster) < 2:
                    continue

                cluster_list = sorted(list(cluster))
                primary_creator = cluster_list[0]
                connected_creators = cluster_list[1:]

                import hashlib
                cluster_hash = hashlib.md5('|'.join(cluster_list).encode()).hexdigest()[:8]
                network_name = f"CoordinatedFunding_{cluster_hash}"

                cur.execute("""
                    SELECT COUNT(*) as count FROM creator_networks
                    WHERE network_name = ?
                """, (network_name,))
                if cur.fetchone()['count'] == 0:
                    if len(cluster_list) == 2:
                        creator1, creator2 = cluster_list
                        cur.execute("""
                            SELECT DISTINCT network_name FROM creator_to_creator_networks
                            WHERE (creator_address = ? OR creator_address = ?)
                            AND network_name LIKE 'CreatorTransfer_%'
                        """, (creator1, creator2))
                        if cur.fetchone():
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
                            json.dumps([]),
                            len(cluster),
                            'HIGH',
                            datetime.now().isoformat()
                        ))
                    except Exception:
                        pass

            conn.commit()
            print("[OUTGOING] ✅ Network detection complete", flush=True)

        except Exception as e:
            conn.rollback()
            print(f"[OUTGOING] ⚠️  Network detection error: {e}", flush=True)
        finally:
            conn.close()


def calculate_and_store_self_funding():
    with db_write_lock_global():
        conn = _connect()
        cur = conn.cursor()

        try:
            cur.execute("SELECT DISTINCT creator_address FROM creator_funders")
            creators = [row['creator_address'] for row in cur.fetchall()]
            updates = []

            for creator in creators:
                cur.execute("""
                    SELECT DISTINCT funder_address FROM creator_funders
                    WHERE creator_address = ?
                """, (creator,))
                funders = [row['funder_address'] for row in cur.fetchall()]
                if not funders:
                    continue

                self_funding_intermediates = 0
                for funder in funders:
                    cur.execute("""
                        SELECT COUNT(DISTINCT creator_address) as count
                        FROM creator_funders
                        WHERE funder_address = ?
                    """, (funder,))
                    if cur.fetchone()['count'] == 1:
                        self_funding_intermediates += 1

                total_funders = len(funders)
                self_funding_percentage = (self_funding_intermediates / total_funders) * 100 if total_funders else 0

                has_isolated_funders = self_funding_percentage >= 50

                has_circular_funding = False
                if has_isolated_funders:
                    cur.execute("""
                        SELECT COUNT(*) as count FROM creator_outgoing_transfers cot
                        WHERE cot.creator_address = ?
                        AND cot.recipient_address IN (
                            SELECT funder_address FROM creator_funders
                            WHERE creator_address = ?
                        )
                    """, (creator, creator))
                    circular_result = cur.fetchone()
                    has_circular_funding = (circular_result['count'] > 0) if circular_result else False

                is_self_funding = 1 if (has_isolated_funders and has_circular_funding) else 0

                updates.append((
                    self_funding_intermediates,
                    total_funders,
                    self_funding_percentage,
                    is_self_funding,
                    creator
                ))

            cur.executemany("""
                INSERT OR REPLACE INTO creator_self_funding
                (self_funding_intermediates, total_funders, self_funding_percentage, is_self_funding, creator_address, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
            """, updates)

            conn.commit()
            print(f"[OUTGOING] ✅ Recalculated self-funding for {len(updates)} creators", flush=True)

        except Exception as e:
            conn.rollback()
            print(f"[OUTGOING] ⚠️  Self-funding calculation error: {e}", flush=True)
        finally:
            conn.close()


def reset_local_counters():
    global GSFA_ATTEMPTS, GSFA_SUCCESS, ENH_ATTEMPTS, ENH_SUCCESS, RPC_SIGNATURE_CALL_COUNT
    GSFA_ATTEMPTS = GSFA_SUCCESS = 0
    ENH_ATTEMPTS = ENH_SUCCESS = 0
    print("Actual getSignaturesForAddress calls:", RPC_SIGNATURE_CALL_COUNT)
    RPC_SIGNATURE_CALL_COUNT = 0


def local_credits_summary() -> Dict[str, int]:
    # Closest-to-billable: use SUCCESS counts
    rpc_credits = GSFA_SUCCESS * CREDITS_GSFA
    enh_credits = ENH_SUCCESS * CREDITS_ENHANCED_BATCH
    total = rpc_credits + enh_credits
    return {
        "gsfa_attempts": GSFA_ATTEMPTS,
        "gsfa_success": GSFA_SUCCESS,
        "enh_attempts": ENH_ATTEMPTS,
        "enh_success": ENH_SUCCESS,
        "credits_rpc": rpc_credits,
        "credits_enhanced": enh_credits,
        "credits_total_local": total,
    }


async def scan_once(concurrency: int = OUTGOING_CONCURRENCY):
    reset_local_counters()

    creators = get_creators(limit=1000)
    if not creators:
        print("[OUTGOING] ℹ️ No creators found", flush=True)
        return

    creator_set = set(creators)
    print(f"[OUTGOING] 🔍 Scanning {len(creators)} creators...", flush=True)

    cursors = load_all_cursors(creators)
    before_cursors = load_all_before_cursors(creators)

    # Reuse ONE session for both RPC + Enhanced (less overhead)
    connector = aiohttp.TCPConnector(limit=OUTGOING_CONCURRENCY * 4)
    async with aiohttp.ClientSession(connector=connector) as session:
        sem = asyncio.Semaphore(concurrency)

        async def handle_creator(c: str) -> Tuple[List[str], Optional[Tuple[str, int]], Optional[str]]:
            async with sem:
                last_sig, _ = cursors.get(c, (None, None))
                before = before_cursors.get(c)

                # ===== Page 1 (always fetch) =====
                sigs = await rpc_get_signatures(session, c, limit=25, before=before)
                if not sigs:
                    return ([], None, before)

                fresh = []
                page_newest_sig = None
                page_newest_slot = None
                hit_last_sig = False

                for item in sigs:
                    s = item.get("signature")
                    if not s:
                        continue

                    if page_newest_sig is None:
                        page_newest_sig = s
                        page_newest_slot = item.get("slot")

                    if last_sig and s == last_sig:
                        hit_last_sig = True
                        break

                    if item.get("err") is None:
                        fresh.append(s)

                all_fresh = list(fresh)

                newest_sig = page_newest_sig
                newest_slot = page_newest_slot
                final_before = sigs[-1].get("signature")

                # ===== Optional adaptive Page 2 =====
                should_fetch_page2 = (
                    MAX_PAGES_PER_CYCLE >= 2 and
                    len(sigs) == 25 and
                    not hit_last_sig and
                    len(fresh) > 0 and
                    final_before is not None
                )

                if should_fetch_page2:
                    sigs2 = await rpc_get_signatures(session, c, limit=25, before=final_before)
                    if sigs2:
                        for item in sigs2:
                            s = item.get("signature")
                            if not s:
                                continue
                            if last_sig and s == last_sig:
                                break
                            if item.get("err") is None:
                                all_fresh.append(s)
                        final_before = sigs2[-1].get("signature") or final_before

                creator_update = (newest_sig, int(newest_slot or 0)) if newest_sig else None
                return (all_fresh, creator_update, final_before)

        results = await asyncio.gather(*[handle_creator(c) for c in creators])

        new_sigs: List[str] = []
        cursor_updates: List[Tuple[str, str, int]] = []
        before_cursor_updates: List[Tuple[str, Optional[str]]] = []

        for c, result in zip(creators, results):
            fresh, creator_update, final_before = result
            new_sigs.extend(fresh)
            if creator_update:
                newest_sig, newest_slot = creator_update
                cursor_updates.append((c, newest_sig, newest_slot))
            if final_before:
                before_cursor_updates.append((c, final_before))

        print(f"[OUTGOING] 📋 Collected {len(new_sigs)} new signatures", flush=True)

        new_sigs = list(dict.fromkeys(new_sigs))
        print(f"[OUTGOING] 🔄 After dedup: {len(new_sigs)} signatures", flush=True)

        # Enhanced parse (100 sigs/request)
        rows_all: List[Tuple] = []
        for i in range(0, len(new_sigs), 100):
            chunk = new_sigs[i:i+100]
            txs = await helius_enhanced_parse(session, chunk)
            rows_all.extend(extract_outgoing_sol(txs, creator_set))

    print(f"[OUTGOING] ✍️ Extracted {len(rows_all)} outgoing transfers", flush=True)

    insert_outgoing_rows(rows_all)
    batch_update_cursors(cursor_updates)
    batch_save_before_cursors(before_cursor_updates)

    build_funding_chains_incremental()
    build_coordinated_edges_incremental()
    detect_and_update_networks_from_outgoing()
    calculate_and_store_self_funding()

    # Recorder credits (if present)
    try:
        from rpc_metrics_recorder import get_recorder
        recorder = get_recorder()
        summary = recorder.get_summary()
        recorder_credits = int(summary.get("credits_total", 0) or 0)
    except Exception:
        recorder_credits = 0

    local = local_credits_summary()

    print(
        "[OUTGOING] ✅ Scan complete: "
        f"creators={len(creators)} new_sigs={len(new_sigs)} new_rows={len(rows_all)}",
        flush=True
    )
    print(
        "[OUTGOING] 🧾 Local counters: "
        f"GSFA attempts={local['gsfa_attempts']} success={local['gsfa_success']} | "
        f"ENH attempts={local['enh_attempts']} success={local['enh_success']}",
        flush=True
    )
    print(
        "[OUTGOING] 💰 Credits: "
        f"local_success_based={local['credits_total_local']} "
        f"(rpc={local['credits_rpc']} enh={local['credits_enhanced']}) "
        f"| recorder={recorder_credits}",
        flush=True
    )


async def run_forever(interval_seconds: int = 3600):
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
    # Run every 2 minutes
    asyncio.run(run_forever(120))