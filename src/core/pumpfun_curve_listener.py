#!/usr/bin/env python3
"""
Pump.Fun → PumpSwap Migration Listener

Detects token migrations from Pump.Fun bonding curve to PumpSwap AMM via WebSocket.
When a migration is detected, runs post-migration analyzer to assess risk.
"""

import asyncio
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sqlite3
from src.utils.db_locking import db_connect
import sys
import time
import threading
import websockets
import aiohttp
import requests
from datetime import datetime
from enum import Enum
from typing import Any, Set, Optional, List, Dict, Tuple
from src.analysis.pump_fun_post_migration_analyzer import PostMigrationAnalyzer
from src.extractors.realtime_creator_funding_extractor import extract_funding_for_new_token
from src.extractors.funder_incoming_extractor import extract_for_creator as extract_funder_transfers
from src.analysis.clustering_task_queue import enqueue_clustering
from src.core.fast_candidate_retry import PendingCandidateShortlist, score_candidate
from src.core.fast_lane_discovery import FastLaneDiscovery
from src.core.ws_price_tracer import trace as _wstrace
from dotenv import load_dotenv

class RegisterResult(str, Enum):
    SUCCESS = "success"
    RETRY   = "retry"   # transient: not yet visible on-chain — safe to re-attempt
    FAIL    = "fail"    # permanent: wrong owner / extraction failure — never retry


# === ANSI Color Codes ===
class Colors:
    DETECT = "\033[94m"      # Blue for POOL_DETECT
    DISCOVER = "\033[92m"    # Green for POOL_DISCOVER_FALLBACK
    RESET = "\033[0m"

# === Logging Helper ===

# Migration log: pool discovery/detection events only
_MIGRATION_LOG_PATH = os.path.join(
    os.path.dirname(__file__), "../../migration.log"
)

# Pre-migration signal debug log: ALL PF signal inputs, parsing, and outputs
PREMIG_LOG_PATH = os.path.join(
    os.path.dirname(__file__), "../../logs/premigration.log"
)
CURVE_WATCH_STATE_PATH = os.path.join(
    os.path.dirname(__file__), "../../logs/curve_watch_state.json"
)


def premig_log(message: str) -> None:
    """Append a line to premigration.log (fire-and-forget, never raises)."""
    try:
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        with open(PREMIG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts}Z  {message}\n")
    except Exception:
        pass


# Truncate premigration.log on every startup so each run starts fresh
try:
    with open(PREMIG_LOG_PATH, "w", encoding="utf-8") as _pf:
        _pf.write(f"{datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z  [STARTUP] premigration.log initialised\n")
except Exception:
    pass
_MIGRATION_LOG_PREFIXES = (
    "[EVENT]",
    "[MIGRATION]",
    "[STATE]",
    "[POOL_DETECT]",
    "[FAST_LANE]",
    "[FAST_PATH_REGISTER]",
    "[TIMING]",
    "[DISCOVERY_CHECKPOINT]",
    "[DECISION]",
    "[INLINE RETRY",
    "[VISIBILITY_PROBE]",
    "[BATCH_VALIDATE_REASONS]",
    "[CANDIDATE_REJECTED]",
    "[FAST_LANE_PRIMARY]",
)

_FLASK_BROADCAST_URL = "http://127.0.0.1:5002/api/internal/broadcast"

def _broadcast_to_flask(event: dict) -> None:
    """
    Fire-and-forget HTTP POST to Flask's internal broadcast endpoint.
    Flask runs in a separate process so we cannot use the in-process PriceStream.
    Never raises — failures are logged and ignored.
    """
    try:
        import urllib.request as _urllib_req, json as _json
        _body = _json.dumps(event).encode()
        _req = _urllib_req.Request(
            _FLASK_BROADCAST_URL,
            data=_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _urllib_req.urlopen(_req, timeout=1) as _resp:
            log_print(f"[SSE_BROADCAST] → {event.get('type')} subscribers={_json.loads(_resp.read()).get('subscribers', '?')}", flush=True)
    except Exception as _e:
        log_print(f"[SSE_BROADCAST] ⚠️  {event.get('type')} failed: {_e}", flush=True)


def _fetch_and_store_symbol(mint: str, db_path: str) -> None:
    """
    Background thread: fetch token symbol from Dexscreener and write it to
    metadata_cache and tracked_tokens.symbol. No-op if already cached.
    """
    try:
        import sqlite3 as _sqlite3, requests as _requests, time as _time
        # Check metadata_cache first
        try:
            _c = _sqlite3.connect(db_path, timeout=15)
            _c.execute("PRAGMA busy_timeout=15000")
            _row = _c.execute(
                "SELECT symbol FROM metadata_cache WHERE mint = ?", (mint,)
            ).fetchone()
            _c.close()
            if _row and _row[0] and _row[0] not in ('UNKNOWN', ''):
                # Already cached — just backfill tracked_tokens if needed
                _c2 = _sqlite3.connect(db_path, timeout=15)
                _c2.execute("PRAGMA busy_timeout=15000")
                _c2.execute(
                    "UPDATE tracked_tokens SET symbol = ? WHERE mint = ? AND (symbol IS NULL OR symbol = '')",
                    (_row[0], mint)
                )
                _c2.commit()
                _c2.close()
                log_print(f"[SYMBOL_FETCH] ✅ Symbol from cache: {_row[0]} ({mint[:16]}...)", flush=True)
                return
        except Exception:
            pass

        # Fetch from pump.fun first (available immediately at migration)
        symbol = None
        name = None
        try:
            resp = _requests.get(
                f"https://frontend-api.pump.fun/coins/{mint}",
                timeout=5,
                headers={"Accept": "application/json"}
            )
            if resp.status_code == 200:
                coin = resp.json()
                symbol = coin.get("symbol") or None
                name   = coin.get("name") or symbol
                log_print(f"[SYMBOL_FETCH] pump.fun: {symbol} for {mint[:16]}...", flush=True)
        except Exception:
            pass

        # Fallback: Dexscreener
        if not symbol:
            try:
                resp2 = _requests.get(
                    f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                    timeout=8
                )
                if resp2.status_code == 200:
                    pairs = resp2.json().get("pairs") or []
                    if pairs:
                        base = pairs[0].get("baseToken", {})
                        symbol = base.get("symbol") or None
                        name   = base.get("name") or symbol
                        log_print(f"[SYMBOL_FETCH] dexscreener: {symbol} for {mint[:16]}...", flush=True)
            except Exception:
                pass

        if not symbol:
            log_print(f"[SYMBOL_FETCH] ⚠️  No symbol found for {mint[:16]}...", flush=True)
            return

        now = int(_time.time())
        _c3 = _sqlite3.connect(db_path, timeout=15)
        _c3.execute("PRAGMA busy_timeout=15000")
        _c3.execute(
            "INSERT OR REPLACE INTO metadata_cache (mint, symbol, name, cached_at, cached_source) VALUES (?, ?, ?, ?, ?)",
            (mint, symbol, name, now, "dexscreener_listener")
        )
        _c3.execute(
            "UPDATE tracked_tokens SET symbol = ? WHERE mint = ? AND (symbol IS NULL OR symbol = '')",
            (symbol, mint)
        )
        _c3.commit()
        _c3.close()
        log_print(f"[SYMBOL_FETCH] ✅ {symbol} ({mint[:16]}...) written to DB", flush=True)

        # Broadcast so the UI can update the symbol without a page reload
        _broadcast_to_flask({
            "type": "symbol_resolved",
            "mint": mint,
            "symbol": symbol,
            "name": name,
        })
    except Exception as _e:
        log_print(f"[SYMBOL_FETCH] ⚠️  {mint[:16]}...: {_e}", flush=True)
    finally:
        # Dedupe should only cover the active fetch.
        # If a lookup fails or returns no symbol, allow future retries.
        with _symbol_fetch_seen_lock:
            _symbol_fetch_seen.discard(mint)


_symbol_fetch_seen: set = set()
_symbol_fetch_seen_lock = __import__('threading').Lock()

def _spawn_symbol_fetch(mint: str, db_path: str) -> None:
    """Fire-and-forget symbol fetch submitted to the shared pool. Deduplicates per mint."""
    with _symbol_fetch_seen_lock:
        if mint in _symbol_fetch_seen:
            return
        _symbol_fetch_seen.add(mint)
    _TOKEN_WORK_POOL.submit(_fetch_and_store_symbol, mint, db_path)


def _write_migration_log(line: str) -> None:
    """Append a line to migration.log (fire-and-forget, never raises)."""
    try:
        with open(_MIGRATION_LOG_PATH, "a", encoding="utf-8") as _f:
            ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            _f.write(f"{ts}Z  {line}\n")
    except Exception:
        pass

def log_print(*args, **kwargs):
    """Print with flush support across Python versions"""
    kwargs.pop('flush', None)  # Remove flush if present
    print(*args, **kwargs)
    sys.stdout.flush()
    # Mirror migration/detection lines to migration.log
    if args:
        line = " ".join(str(a) for a in args)
        # Strip ANSI colour codes before prefix match
        import re as _re
        clean = _re.sub(r"\033\[[0-9;]*m", "", line)
        if any(clean.lstrip().startswith(p) for p in _MIGRATION_LOG_PREFIXES):
            _write_migration_log(clean)

# === Global Database Write Lock ===
try:
    from src.metrics.rpc_metrics_recorder import initialize_recorder, record_request
    initialize_recorder(plan_monthly_credits=50_000_000)
except ImportError:
    def record_request(*args, **kwargs):
        pass  # No-op if metrics recorder not available
except Exception as e:
    log_print(f"[WARNING] Could not initialize RPC metrics: {e}", flush=True)
    def record_request(*args, **kwargs):
        pass  # No-op fallback

try:
    from src.metrics.usage_tracker import record_wss, record_webhook
except Exception:
    def record_wss(*args, **kwargs): pass
    def record_webhook(*args, **kwargs): pass

# Serializes ALL database writes across threads/processes to prevent lock contention
# Used by asyncio tasks (wrapped with self.db_lock THEN this), executor threads, and workers
DB_WRITE_LOCK = threading.RLock()

# Bounded thread pool for per-token background work (symbol fetch, scoring, price fast-lane).
# Caps OS thread count; tasks queue internally if all workers are busy.
_TOKEN_WORK_POOL = ThreadPoolExecutor(max_workers=40, thread_name_prefix="tok_work")

# Import settings checker (will be imported dynamically when needed)
def get_migration_setting(key: str, default=True) -> bool:
    """Get a migration setting from file or database"""
    try:
        # Try database first (listener_settings table)
        import sqlite3
        import json
        if key in ['listen_to_launches', 'listen_to_price_updates', 'auto_extract_funding', 'auto_extract_funders']:
            # Use DB_PATH if available, otherwise fall back to default location
            db_path = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), '../../database/flex_complete_database.db'))
            conn = db_connect(db_path, timeout=15)
            cursor = conn.cursor()
            try:
                cursor.execute("SELECT setting_value FROM listener_settings WHERE setting_key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    result = row[0] == 'true'
                    conn.close()
                    return result
            except Exception as e:
                pass
            conn.close()

        # Fall back to migration_settings.json for migration-specific settings
        settings_file = "migration_settings.json"
        if os.path.exists(settings_file):
            with open(settings_file) as f:
                settings = json.load(f)
                return settings.get(key, default)
    except Exception as e:
        pass
    return default


async def extract_funder_transfers_async(creator_address: str):
    """Async wrapper for funder transfer extraction"""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, extract_funder_transfers, creator_address)
        if result.get('status') == 'complete':
            log_print(f"[FUNDER_EXTRACTION] ✅ Funding complete for {creator_address[:8]}...: IN={result.get('incoming_found', 0)}, OUT={result.get('outgoing_found', 0)}, SOL={result.get('total_sol', 0):.4f}", flush=True)
        else:
            log_print(f"[FUNDER_EXTRACTION] Completed for {creator_address[:8]}...: {result}", flush=True)
    except Exception as e:
        log_print(f"[FUNDER_EXTRACTION] Error extracting transfers for {creator_address[:8]}...: {e}", flush=True)
        import traceback
        traceback.print_exc()


async def update_network_clustering_async():
    """Update network clustering database from extracted funding data (no RPC needed)"""
    try:
        loop = asyncio.get_event_loop()
        # Rebuild super_clusters table based on extracted funding relationships
        result = await loop.run_in_executor(None, rebuild_super_clusters_from_funding)
        log_print(f"[CLUSTERING] ✅ Super-clusters updated from funding data", flush=True)
    except Exception as e:
        log_print(f"[CLUSTERING] Error updating network clustering: {e}", flush=True)
        import traceback
        traceback.print_exc()


def rebuild_super_clusters_from_funding():
    """Rebuild super_clusters table and assign creators to networks based on funding"""
    try:
        # Serialize writes: prevent other threads from interfering with clustering writes
        with DB_WRITE_LOCK:
            conn = db_connect('flex_complete_database.db', timeout=60)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=60000")
            cursor = conn.cursor()

            # Note: BEGIN IMMEDIATE removed - WAL mode + busy_timeout handles serialization
            # Holding write lock for entire clustering operation starved other writers
            try:
                log_print(f"[CLUSTERING] 🔄 Rebuilding super_clusters from funding data...", flush=True)

                # Get all creators with funding relationships
                cursor.execute("""
                    SELECT DISTINCT creator_address FROM creator_funders
                    WHERE fully_analyzed = 1 AND amount_sol > 0
                """)

                creators_to_process = [row[0] for row in cursor.fetchall()]
                log_print(f"[CLUSTERING]    Found {len(creators_to_process)} creators with complete funding extraction", flush=True)

                # Update cluster counts and metadata for existing clusters
                cursor.execute("""
                    SELECT super_cluster_id FROM super_clusters
                """)

                for (cluster_id,) in cursor.fetchall():
                    # Count creators in this cluster
                    cursor.execute("""
                        SELECT COUNT(DISTINCT creator_address) as creator_count
                        FROM creator_super_cluster_membership
                        WHERE super_cluster_id = ?
                    """, (cluster_id,))

                    row = cursor.fetchone()
                    creator_count = row[0] if row else 0

                    # Update cluster metadata
                    cursor.execute("""
                        UPDATE super_clusters
                        SET creator_count = ?
                        WHERE super_cluster_id = ?
                    """, (creator_count, cluster_id))

                # Assign creators and funders to super_clusters
                # Logic:
                # 1. Add creator to clusters where their funders are
                # 2. Add funders to clusters if they fund real creators (not just CEX/INFRA)
                creators_assigned = 0
                funders_assigned = 0

                for creator in creators_to_process:
                    # Find all super_clusters where this creator's funders appear as cluster members
                    cursor.execute("""
                        SELECT DISTINCT cscm.super_cluster_id, cf.funder_address
                        FROM creator_funders cf
                        JOIN creator_super_cluster_membership cscm ON cf.funder_address = cscm.creator_address
                        WHERE cf.creator_address = ? AND cf.fully_analyzed = 1 AND cf.amount_sol > 0
                        AND cf.is_cex = 0
                    """, (creator,))

                    results = cursor.fetchall()
                    clusters = [row[0] for row in results]
                    funders = [row[1] for row in results]

                    # Add creator to those clusters
                    for cluster_id in clusters:
                        cursor.execute("""
                            INSERT OR IGNORE INTO creator_super_cluster_membership
                            (creator_address, super_cluster_id)
                            VALUES (?, ?)
                        """, (creator, cluster_id))
                        creators_assigned += 1

                    # Add funders to clusters if they fund real addresses (not just CEX/INFRA)
                    for funder in funders:
                        # Check if funder has outgoing transfers to non-CEX/INFRA addresses
                        cursor.execute("""
                            SELECT COUNT(*) FROM funder_outgoing_transfers
                            WHERE funder_address = ? AND is_cex = 0
                        """, (funder,))

                        has_real_funding = cursor.fetchone()[0] > 0

                        if has_real_funding:
                            # Add funder to all clusters where creator appears
                            for cluster_id in clusters:
                                cursor.execute("""
                                    INSERT OR IGNORE INTO creator_super_cluster_membership
                                    (creator_address, super_cluster_id)
                                    VALUES (?, ?)
                                """, (funder, cluster_id))
                                funders_assigned += 1

                conn.commit()
                log_print(f"[CLUSTERING] ✅ Assigned {creators_assigned} creators to networks", flush=True)
                log_print(f"[CLUSTERING] ✅ Assigned {funders_assigned} funders to networks (funding real addresses)", flush=True)
                log_print(f"[CLUSTERING] ✅ Updated super_cluster metadata", flush=True)

            except Exception as e:
                conn.rollback()
                raise

            finally:
                conn.close()

        return {'status': 'success', 'creators_updated': len(creators_to_process), 'creators_assigned': creators_assigned, 'funders_assigned': funders_assigned}

    except Exception as e:
        log_print(f"[CLUSTERING] ⚠ Error rebuilding super_clusters: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return {'status': 'error', 'error': str(e)}


def check_if_cex_funding(cex_address: str) -> dict:
    """Check if a wallet address is a known CEX wallet
    
    Returns dict with:
    - is_cex: bool
    - exchange_name: str or None
    - wallet_type: str or None
    - confidence_level: int (0-100)
    - flag: str (e.g. "🏛️ Kraken Hot Wallet") or None
    """
    try:
        import sqlite3
        conn = db_connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT exchange_name, wallet_type, confidence_level
            FROM cex_wallets
            WHERE cex_address = ? AND is_active = 1
        """, (cex_address,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                'is_cex': True,
                'exchange_name': row['exchange_name'],
                'wallet_type': row['wallet_type'],
                'confidence_level': row['confidence_level'],
                'flag': f"🏛️ {row['exchange_name']} {row['wallet_type']}"
            }
        else:
            return {
                'is_cex': False,
                'exchange_name': None,
                'wallet_type': None,
                'confidence_level': 0,
                'flag': None
            }
    except Exception as e:
        log_print(f"[ERROR] Failed to check CEX wallet: {e}")
        return {
            'is_cex': False,
            'exchange_name': None,
            'wallet_type': None,
            'confidence_level': 0,
            'flag': None
        }

load_dotenv(os.path.join(os.path.dirname(__file__), '../../config/.env'))

# === Config ===
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
HELIUS_MONITORING_API_KEY = os.getenv("HELIUS_MONITORING_API_KEY", "")

# Use monitoring key if available, fall back to regular key
_RPC_KEY = HELIUS_MONITORING_API_KEY or HELIUS_API_KEY

# RPC Configuration: Use Helius + Public Solana only (QuickNode removed)
# WebSocket: Try Helius first, fall back to public Solana
HELIUS_RPC_WS = f"wss://mainnet.helius-rpc.com/?api-key={_RPC_KEY}" if _RPC_KEY else "wss://api.mainnet-beta.solana.com/"

# HTTP: Use Helius if available, otherwise public Solana
RPC_HTTP = f"https://mainnet.helius-rpc.com/?api-key={_RPC_KEY}" if _RPC_KEY else "https://api.mainnet-beta.solana.com"

# RPC failover chain: Helius -> Public Solana
RPC_URLS = []
if _RPC_KEY:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={_RPC_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")  # Public fallback

PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMPFUN_MIGRATION_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
SOL_MINT = "So11111111111111111111111111111111111111112"
KNOWN_NON_MINT_ADDRESSES = {
    PUMPFUN_PROGRAM,
    PUMPSWAP_PROGRAM,
    SOL_MINT,
    "11111111111111111111111111111111",
    "ComputeBudget111111111111111111111111111111",
    "BPFLoader1111111111111111111111111111111111",
    "BPFLoader2111111111111111111111111111111111",
    "BPFLoaderUpgradeab1e11111111111111111111111",
    "SysvarRent111111111111111111111111111111111",
    "SysvarC1ock11111111111111111111111111111111",
    "SysvarRecentB1ockHashes11111111111111111111",
    "Sysvar1nstructions1111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
    "AddressLookupTab1e1111111111111111111111111",
    "Stake11111111111111111111111111111111111111",
    "Vote111111111111111111111111111111111111111",
    "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ",
    "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9",
    "Gz9VPiSLQYbvKyb3jZPjNfyA6n4T4qVFUuAukgL964nL",
    "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e",
    "term9YPb9mzAsABaqN71A4xdbxHmpBNZavpBiQKZzN3",
    "GMgnVFR8Jb39LoXsEVzb3DvBy3ywCmdmJquHUy1Lrkqb",
    "FAdo9NCw1ssek6Z6yeWzWjhLVsr8uiCwcWNUnKgzTnHe",
    "troyXT7Ty3s2rjJe4bqWaroUrS4Fjd8rbHHNHxcACF4",
    "b1oomGGqPKGD6errbyfbVMBuzSC8WtAAYo8MwNafWW1",
    "proVF4pMXVaYqmy4NjniPh4pqKNfMmsihgd4wdkCX3u",
    "haqqqMGN35ehCftXca3KWxJFXTBcWTWeNHNtUHLGQdh",
}
SOLANA_PUBKEY_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# Use DB_PATH from environment or construct it relative to project root
DB_PATH = os.getenv("DB_PATH")
if not DB_PATH:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    DB_PATH = os.path.join(PROJECT_ROOT, 'database', 'flex_complete_database.db')

# Creator pipeline DB (separate from main app DB)
CREATOR_DB_PATH = os.getenv("CREATOR_DB_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "pumpswap_tokens.db",
)


def _ensure_webhook_birth_queue_schema(db_path: str) -> None:
    import sqlite3 as _sq
    conn = _sq.connect(db_path, timeout=10)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS webhook_birth_queue (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                signature TEXT    NOT NULL UNIQUE,
                consumed  INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            )
        """)
    conn.close()


class PumpFunCurveListener(FastLaneDiscovery):
    """Detects Pump.Fun → PumpSwap migrations via WebSocket and analyzes them"""

    def __init__(self):
        super().__init__()  # Initialize FastLaneDiscovery
        self.seen_mints: Set[str] = set()
        self.processing_migrations: Set[str] = set()
        self.completed_migrations: Set[str] = set()
        self.processing_launches: Set[str] = set()
        self.completed_launches: Set[str] = set()
        self.analyzed_tokens = {}
        self.db_lock = asyncio.Lock()
        # PumpPortal live vSol state: {mint: {"v_sol": float, "ts": int, "symbol": str, "name": str, "creator": str}}
        self._portal_vsol: dict = {}
        self.websocket_connected = False
        self.websocket_msg_count = 0  # Track message receipt
        self.websocket_migration_count = 0  # Track migrations detected

        # === NEW: Transaction caching ===
        self.tx_cache = {}  # {signature: (tx_data, timestamp)}
        self.tx_cache_ttl_seconds = 1800  # 30 minutes TTL
        self.tx_cache_max_size = 10000  # Prevent unbounded growth
        self.tx_inflight_locks = {}  # {signature: asyncio.Lock()} for singleflight
        self.tx_cache_pending_retries = {}  # {signature: retry_task} for delayed re-checks
        self.tx_cache_stats = {
            'hit': 0,
            'miss': 0,
            'wait': 0,
        }

        # === Price extraction metrics ===
        self.price_stats = {
            'onchain_success': 0,
            'dexscreener_fallback': 0,
        }

        # === NEW: Deferred pool detection retries ===
        self.pool_detection_retries = {}  # {mint: (tx_data, signature, retry_count)}
        self.pool_detection_max_retries = 3
        self.pool_detection_retry_delay = 5  # seconds

        # === NEW: Token state tracking (pending → resolved) ===
        self.token_states = {}  # {mint: "pending" | "resolving" | "resolved"}
        self.token_discovery_times = {}  # {mint: {"detected": time, "resolved": time}}
        self._flow_windows_by_mint: Dict[str, deque] = {}
        self._last_market_cap_by_mint: Dict[str, float] = {}
        self._bonding_curve_to_mint: Dict[str, str] = {}
        self._known_bonding_curve_mints: Set[str] = set()
        self._curve_watch_queue: asyncio.Queue = asyncio.Queue()
        self._curve_watch_subscribed: set = set()
        self._recent_birth_mints: Dict[str, float] = {}
        self._recent_birth_cache_ttl_seconds = 20 * 60
        self._bonding_curve_index_last_rowid = 0
        self._bonding_curve_refresh_interval_seconds = 15
        self._last_bonding_curve_refresh_monotonic = 0.0
        self._last_bonding_curve_refresh_failure_monotonic = 0.0
        self._bonding_curve_refresh_failure_cooldown_seconds = 2.0
        self._bonding_curve_refresh_retry_attempts = 3
        self._bonding_curve_refresh_retry_backoff_seconds = 0.15
        self._premigration_signal_floor_warm = 50000.0
        self._premigration_signal_floor_hot = 58000.0
        self._pumpfun_trade_debug_budget = 25
        self._resolver_resolved_count = 0
        self._resolver_unresolved_count = 0

        self._ensure_db()
        self._normalize_existing_pumpfun_rows()
        self._hydrate_bonding_curve_index()
        log_print(f"[INIT] Pump.Fun → PumpSwap Migration Listener ready", flush=True)
        log_print(f"[INIT] ✅ TX Cache initialized (TTL: {self.tx_cache_ttl_seconds}s)", flush=True)
        log_print(f"[INIT] 🔄 Pool detection retries enabled (max {self.pool_detection_max_retries} retries, {self.pool_detection_retry_delay}s delay)", flush=True)
        log_print(f"[INIT] Monitoring PumpSwap program: {PUMPSWAP_PROGRAM}", flush=True)
        log_print(f"[INIT] WebSocket: {HELIUS_RPC_WS[:60]}...", flush=True)
        log_print(f"[INIT] HTTP RPC: {RPC_HTTP[:60]}...", flush=True)

        # === PHASE 2: Critical-path protection ===
        # Protect pool discovery from background RPC contention
        self.DISCOVERY_CRITICAL_WINDOW_SECONDS = 45
        self.DISCOVERY_HARD_TIMEOUT_SECONDS = 60
        self.CREATOR_FUNDING_JOB_TIMEOUT_SECONDS = 90
        self.critical_window_tasks = {}  # {mint: critical_window_expiry_time}

        # RPC isolation: separate quotas for discovery vs background
        self.discovery_rpc_semaphore = asyncio.Semaphore(8)  # 8 concurrent discovery calls
        self.background_rpc_semaphore = asyncio.Semaphore(2)  # 2 concurrent background calls

        # Background job queue (deferred execution during critical window)
        self.background_job_queue = asyncio.Queue()
        self.background_jobs_processing = False
        self._creator_funding_queue_wakeup = asyncio.Event()
        asyncio.create_task(self._process_background_queue())

        # Periodic TX cache cleanup (prevent memory leak on long-running listener)
        asyncio.create_task(self._cleanup_tx_cache_periodic())
        asyncio.create_task(self._process_creator_resolution_queue_periodic())
        asyncio.create_task(self._process_creator_funding_queue_periodic())
        asyncio.create_task(self._periodic_cluster_rebuild())
        asyncio.create_task(self._flush_portal_vsol_periodic())
        asyncio.create_task(self._db_maintenance_periodic())

        # Telemetry for discovery attempts
        self.discovery_attempts = {}  # {mint: [attempt_1, attempt_2, ...]}

        # Post-extraction intelligence refresh debounce
        self._intel_refresh_last_run: float = 0.0
        self._intel_refresh_debounce_secs: int = 180  # 3 min window

        # === Initialize price worker with WebSocket for pool price streaming ===
        try:
            from src.core.price_worker import get_price_worker
            import os
            import sys
            from src.core.ws_snapshot_logger import _LOG_PATH as _ws_log_path
            _db_abs = os.path.abspath(self.db_path if hasattr(self, 'db_path') else 'database/flex_complete_database.db')
            _ws_log_abs = os.path.abspath(_ws_log_path)
            log_print(f"[STARTUP] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
            log_print(f"[STARTUP] role=listener pid={os.getpid()}", flush=True)
            log_print(f"[STARTUP] db={_db_abs}", flush=True)
            log_print(f"[STARTUP] ws_snapshot_log={_ws_log_abs}", flush=True)
            log_print(f"[STARTUP] cwd={os.getcwd()}", flush=True)
            log_print(f"[STARTUP] ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", flush=True)
            self.price_worker = get_price_worker()
            self.price_worker.start()  # Start background thread + WebSocket
            log_print(f"[INIT] ✅ Price worker started pid={os.getpid()} worker=0x{id(self.price_worker):x}", flush=True)
        except Exception as e:
            log_print(f"[INIT] ⚠️  Price worker initialization failed: {e}", flush=True)
            self.price_worker = None

        log_print(f"[INIT] ✅ Phase 2 critical-path protection initialized", flush=True)
        log_print(f"[INIT]   • Discovery RPC: 8 concurrent slots", flush=True)
        log_print(f"[INIT]   • Background RPC: 2 concurrent slots", flush=True)
        log_print(f"[INIT]   • Critical window: {self.DISCOVERY_CRITICAL_WINDOW_SECONDS}s", flush=True)

        # === NEW: Duplicate discovery prevention guards ===
        self._active_pool_discoveries_by_mint = set()  # {mint}
        self._active_pool_discoveries_by_sig = set()   # {signature}
        self._retry_tasks_by_mint = {}                  # {mint: task}
        self._primary_attempted_by_mint = {}            # {mint: timestamp}
        # Candidates that failed registration (timeout or validation) — never retry these
        self._failed_registration: dict = {}            # {mint: set(addr)}
        # Account info fetched during batch validation — reused in registration to skip re-fetch
        self._validated_account_cache: dict = {}        # {addr: account_info}

    def _hydrate_bonding_curve_index(self) -> None:
        """Warm a mint/bonding-curve lookup table from the local DB without RPC."""
        try:
            rows = self._refresh_bonding_curve_index(force=True, full=True)
            log_print(f"[INIT] ✅ Bonding-curve index hydrated ({rows} rows)", flush=True)
            log_print(f"[PREMIG_INIT] indexed_mints={len(self._known_bonding_curve_mints)}", flush=True)
        except Exception as e:
            log_print(f"[INIT] ⚠ Could not hydrate bonding-curve index: {e}", flush=True)

    def _refresh_bonding_curve_index(self, *, force: bool = False, full: bool = False) -> int:
        """Incrementally hydrate Pump.fun rows into the in-memory resolver index."""
        now_mono = time.monotonic()
        if not force and not full:
            elapsed = now_mono - float(self._last_bonding_curve_refresh_monotonic or 0.0)
            if elapsed < self._bonding_curve_refresh_interval_seconds:
                return 0
        recent_failure_elapsed = now_mono - float(self._last_bonding_curve_refresh_failure_monotonic or 0.0)
        if force and not full and recent_failure_elapsed < float(self._bonding_curve_refresh_failure_cooldown_seconds or 0.0):
            return 0

        query = """
            SELECT rowid, mint, bonding_curve_pda
            FROM token_analysis
            WHERE mint IS NOT NULL
              AND (
                    NULLIF(TRIM(COALESCE(bonding_curve_pda, '')), '') IS NOT NULL
                    OR COALESCE(source_platform, '') = 'pumpfun'
                    OR COALESCE(lifecycle_stage, '') = 'bonding_curve'
                  )
        """
        params: List[Any] = []
        if not full and self._bonding_curve_index_last_rowid:
            query += " AND rowid > ?"
            params.append(int(self._bonding_curve_index_last_rowid))
        query += " ORDER BY rowid ASC"
        log_print(
            f"[INDEX_REFRESH_START] force={'yes' if force else 'no'} full={'yes' if full else 'no'} "
            f"last_rowid={int(self._bonding_curve_index_last_rowid or 0)} cached_index={len(self._bonding_curve_to_mint)}",
            flush=True,
        )

        rows: List[Tuple[Any, Any, Any]] = []
        refresh_error: Optional[Exception] = None
        max_attempts = max(1, int(self._bonding_curve_refresh_retry_attempts or 1))
        for attempt in range(1, max_attempts + 1):
            conn = None
            try:
                conn = db_connect(DB_PATH, timeout=15)
                conn.execute("PRAGMA query_only = ON")
                conn.execute("PRAGMA busy_timeout = 5000")
                cursor = conn.cursor()
                cursor.execute(query, tuple(params))
                rows = cursor.fetchall()
                refresh_error = None
                break
            except sqlite3.OperationalError as e:
                refresh_error = e
                if attempt < max_attempts:
                    log_print(
                        f"[INDEX_REFRESH_RETRY] attempt={attempt} error={str(e)[:160]}",
                        flush=True,
                    )
                    time.sleep(float(self._bonding_curve_refresh_retry_backoff_seconds or 0.15) * attempt)
            except Exception as e:
                refresh_error = e
                break
            finally:
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass

        if refresh_error is not None:
            self._last_bonding_curve_refresh_failure_monotonic = now_mono
            log_print(
                f"[INDEX_REFRESH_FAIL] preserved_cached_index=yes error={str(refresh_error)[:160]} "
                f"cached_index={len(self._bonding_curve_to_mint)}",
                flush=True,
            )
            return 0

        max_rowid = int(self._bonding_curve_index_last_rowid or 0)
        for rowid, mint, bonding_curve in rows:
            if rowid:
                max_rowid = max(max_rowid, int(rowid))
            self._remember_bonding_curve_token(
                str(mint) if mint else "",
                str(bonding_curve) if bonding_curve else None,
            )

        self._bonding_curve_index_last_rowid = max_rowid
        self._last_bonding_curve_refresh_monotonic = now_mono
        self._last_bonding_curve_refresh_failure_monotonic = 0.0
        log_print(
            f"[INDEX_REFRESH_OK] rows={len(rows)} last_rowid={self._bonding_curve_index_last_rowid} "
            f"cached_index={len(self._bonding_curve_to_mint)} known_mints={len(self._known_bonding_curve_mints)}",
            flush=True,
        )
        return len(rows)

    def _prune_recent_birth_cache(self, *, now_ts: Optional[float] = None) -> None:
        """Drop stale birth cache entries so the resolver stays lightweight."""
        now = float(now_ts or time.time())
        cutoff = now - float(self._recent_birth_cache_ttl_seconds)
        stale_mints = [mint for mint, ts in self._recent_birth_mints.items() if float(ts) < cutoff]
        for mint in stale_mints:
            self._recent_birth_mints.pop(mint, None)

    def _remember_recent_birth_token(self, mint: str, bonding_curve_pda: Optional[str] = None) -> None:
        """Track newly created Pump.fun tokens immediately for live buy resolution."""
        if not mint:
            return
        self._prune_recent_birth_cache()
        self._recent_birth_mints[str(mint)] = time.time()
        self._remember_bonding_curve_token(mint, bonding_curve_pda)

    def _normalize_existing_pumpfun_rows(self) -> None:
        """Promote legacy bonding-curve rows into the Pump.fun tracking namespace."""
        try:
            conn = db_connect(DB_PATH, timeout=15)
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE token_analysis
                SET
                    source_platform = COALESCE(source_platform, 'pumpfun'),
                    lifecycle_stage = CASE
                        WHEN COALESCE(lifecycle_stage, 'migration_pending') = 'migrated' THEN lifecycle_stage
                        WHEN NULLIF(TRIM(COALESCE(pool_address, pumpswap_pool_address, dex, '')), '') IS NOT NULL THEN lifecycle_stage
                        ELSE 'bonding_curve'
                    END
                WHERE mint IS NOT NULL
                  AND NULLIF(TRIM(COALESCE(bonding_curve_pda, '')), '') IS NOT NULL
                  AND NULLIF(TRIM(COALESCE(pool_address, pumpswap_pool_address, dex, '')), '') IS NULL
                  AND (source_platform IS NULL OR source_platform = '' OR lifecycle_stage IS NULL OR lifecycle_stage = 'migration_pending')
                """
            )
            normalized = cursor.rowcount or 0
            conn.commit()
            conn.close()
            log_print(f"[PREMIG_NORMALIZE] rows={normalized}", flush=True)
        except Exception as e:
            log_print(f"[PREMIG_NORMALIZE] ⚠ Failed to normalize Pump.fun rows: {e}", flush=True)

    def _remember_bonding_curve_token(self, mint: str, bonding_curve_pda: Optional[str] = None) -> None:
        """Keep an in-memory lookup from bonding curve and mint to the tracked token."""
        if mint:
            self._known_bonding_curve_mints.add(str(mint))
        if mint and bonding_curve_pda:
            self._bonding_curve_to_mint[str(bonding_curve_pda)] = str(mint)

    def _is_definitely_not_mint_candidate(self, value: str) -> bool:
        """Reject obvious non-mint account keys before treating them as mint candidates."""
        if not isinstance(value, str):
            return True
        candidate = value.strip()
        if not candidate:
            return True
        if candidate in KNOWN_NON_MINT_ADDRESSES:
            return True
        if candidate in self._bonding_curve_to_mint:
            return True
        if len(candidate) < 32 or len(candidate) > 44:
            return True
        if not SOLANA_PUBKEY_RE.fullmatch(candidate):
            return True
        if any(ch in candidate for ch in ("0", "O", "I", "l", "+", "/", "=")):
            return True
        if candidate.startswith("AAAAAAAA") or candidate.endswith("AAAAAAAA"):
            return True
        lowered = candidate.lower()
        if lowered.startswith("computebudget") or lowered.startswith("bpfloader"):
            return True
        if len(set(candidate)) <= 3:
            return True
        return False

    def _looks_like_pumpfun_mint(self, value: str) -> bool:
        """Strict filter for Pump.fun mint-like log candidates."""
        if self._is_definitely_not_mint_candidate(value):
            return False
        candidate = value.strip()
        if not candidate.lower().endswith("pump"):
            return False
        return True

    def _is_pumpfun_buy_candidate(self, logs: List[str]) -> bool:
        """Identify Pump.fun buy traffic from websocket logs without RPC."""
        lowered = " ".join(logs or []).lower()
        if "instruction: create" in lowered or "instruction: migrate" in lowered:
            return False
        if "instruction: sell" in lowered:
            return False
        if "instruction: buy" in lowered:
            return True
        if re.search(r"\bbuy\b", lowered):
            return True
        if "program data:" in lowered:
            candidates = self._extract_base58_candidates(logs)
            if any(candidate in self._bonding_curve_to_mint or candidate in self._known_bonding_curve_mints for candidate in candidates):
                return True
        return False

    def _is_pumpfun_sell_candidate(self, logs: List[str]) -> bool:
        lowered = " ".join(logs or []).lower()
        if "instruction: create" in lowered or "instruction: migrate" in lowered:
            return False
        return "instruction: sell" in lowered or bool(re.search(r"\bsell\b", lowered))

    def _extract_base58_candidates(self, logs: List[str]) -> List[str]:
        text = " ".join(logs or [])
        return re.findall(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b", text)

    def _extract_instruction_names_from_logs(self, logs: List[str]) -> List[str]:
        instruction_names: List[str] = []
        seen: Set[str] = set()
        for line in logs or []:
            for match in re.finditer(r"Instruction:\s*([A-Za-z0-9_]+)", str(line), flags=re.IGNORECASE):
                name = str(match.group(1) or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                instruction_names.append(name)
        return instruction_names

    def _extract_candidate_tokens_from_text(self, value: Any) -> List[str]:
        if not isinstance(value, str):
            return []
        text = value.strip()
        if not text:
            return []
        tokens: List[str] = []
        raw_tokens = re.findall(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b", text)
        for match in re.finditer(
            r"(?:mint|token[_\s-]?mint|base[_\s-]?mint|bonding[_\s-]?curve|account|accounts?)[:=\s\[]+([1-9A-HJ-NP-Za-km-z]{32,44})",
            text,
            flags=re.IGNORECASE,
        ):
            raw_tokens.append(match.group(1))
        for token in raw_tokens:
            if token.startswith("AAAAAAAA") or token.endswith("AAAAAAAA"):
                continue
            tokens.append(token)
        return tokens

    def _extract_raw_mint_resolution_candidates(
        self,
        logs: List[str],
        explicit_mint: Optional[str] = None,
    ) -> List[str]:
        raw_candidates: List[str] = []
        if explicit_mint:
            raw_candidates.append(explicit_mint)
        for line in logs or []:
            raw_candidates.extend(self._extract_candidate_tokens_from_text(line))
        return raw_candidates

    def _normalize_mint_resolution_candidates(self, raw_candidates: List[str]) -> List[str]:
        normalized: List[str] = []
        seen: Set[str] = set()
        for value in raw_candidates:
            if not isinstance(value, str):
                continue
            candidate = value.strip().strip("[]{}(),;\"'")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        return normalized

    def _extract_account_keys_from_tx(self, tx_data: Optional[Dict]) -> List[str]:
        if not isinstance(tx_data, dict):
            return []
        keys: List[str] = []
        seen: Set[str] = set()
        message = (tx_data.get("transaction") or {}).get("message") or {}
        raw_keys = message.get("accountKeys") or []
        for entry in raw_keys:
            value = None
            if isinstance(entry, str):
                value = entry
            elif isinstance(entry, dict):
                value = entry.get("pubkey") or entry.get("address")
            if not isinstance(value, str):
                continue
            candidate = value.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            keys.append(candidate)

        loaded_addresses = (tx_data.get("meta") or {}).get("loadedAddresses") or {}
        for section in ("writable", "readonly"):
            for entry in loaded_addresses.get(section, []) or []:
                if not isinstance(entry, str):
                    continue
                candidate = entry.strip()
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                keys.append(candidate)
        return keys

    def _extract_instruction_contexts_from_tx(self, tx_data: Optional[Dict]) -> List[Dict[str, Any]]:
        if not isinstance(tx_data, dict):
            return []
        contexts: List[Dict[str, Any]] = []
        message = (tx_data.get("transaction") or {}).get("message") or {}
        account_keys = self._extract_account_keys_from_tx(tx_data)

        def _normalise_account_ref(value: Any) -> Optional[str]:
            if isinstance(value, str):
                return value.strip() or None
            if isinstance(value, int) and 0 <= value < len(account_keys):
                resolved = account_keys[value].strip()
                return resolved or None
            if isinstance(value, dict):
                pubkey = value.get("pubkey") or value.get("address")
                if isinstance(pubkey, str):
                    pubkey = pubkey.strip()
                    return pubkey or None
            return None

        def _append_instruction(ix: Any) -> None:
            if not isinstance(ix, dict):
                return
            parsed = ix.get("parsed") if isinstance(ix.get("parsed"), dict) else {}
            info = parsed.get("info") if isinstance(parsed.get("info"), dict) else {}
            program_id = ix.get("programId")
            if not isinstance(program_id, str):
                program_id = ix.get("program")
            program_id = str(program_id or "").strip() or None
            instruction_name = str(parsed.get("type") or ix.get("program") or ix.get("name") or "").strip() or None
            accounts: List[str] = []
            for account_ref in ix.get("accounts", []) or []:
                resolved = _normalise_account_ref(account_ref)
                if resolved:
                    accounts.append(resolved)
            for value in info.values():
                if isinstance(value, list):
                    for child in value:
                        resolved = _normalise_account_ref(child)
                        if resolved:
                            accounts.append(resolved)
            contexts.append(
                {
                    "instruction": instruction_name,
                    "program_id": program_id,
                    "info": info,
                    "accounts": list(dict.fromkeys(accounts)),
                }
            )

        for instruction in message.get("instructions", []) or []:
            _append_instruction(instruction)
        for inner_group in (tx_data.get("meta") or {}).get("innerInstructions", []) or []:
            for instruction in inner_group.get("instructions", []) or []:
                _append_instruction(instruction)
        return contexts

    def _extract_token_account_mint_map(self, tx_data: Optional[Dict]) -> Dict[str, str]:
        token_account_to_mint: Dict[str, str] = {}
        account_keys = self._extract_account_keys_from_tx(tx_data)
        meta = (tx_data or {}).get("meta") or {}
        for balance in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
            if not isinstance(balance, dict):
                continue
            mint = str(balance.get("mint") or "").strip()
            if not mint:
                continue
            account_index = balance.get("accountIndex")
            if isinstance(account_index, int) and 0 <= account_index < len(account_keys):
                token_account = account_keys[account_index]
                if token_account:
                    token_account_to_mint[token_account] = mint
        return token_account_to_mint

    def _extract_token_account_owner_map(self, tx_data: Optional[Dict]) -> Dict[str, str]:
        token_account_to_owner: Dict[str, str] = {}
        account_keys = self._extract_account_keys_from_tx(tx_data)
        meta = (tx_data or {}).get("meta") or {}
        for balance in (meta.get("preTokenBalances") or []) + (meta.get("postTokenBalances") or []):
            if not isinstance(balance, dict):
                continue
            owner = str(balance.get("owner") or "").strip()
            if not owner:
                continue
            account_index = balance.get("accountIndex")
            if isinstance(account_index, int) and 0 <= account_index < len(account_keys):
                token_account = account_keys[account_index]
                if token_account:
                    token_account_to_owner[token_account] = owner
        return token_account_to_owner

    def _extract_signer_keys_from_tx(self, tx_data: Optional[Dict]) -> List[str]:
        if not isinstance(tx_data, dict):
            return []
        signers: List[str] = []
        seen: Set[str] = set()
        message = (tx_data.get("transaction") or {}).get("message") or {}
        account_keys = self._extract_account_keys_from_tx(tx_data)
        raw_keys = message.get("accountKeys") or []

        for idx, entry in enumerate(raw_keys):
            pubkey = None
            signer = None
            if isinstance(entry, str):
                pubkey = entry
            elif isinstance(entry, dict):
                pubkey = entry.get("pubkey") or entry.get("address")
                signer = entry.get("signer")
            if not isinstance(pubkey, str):
                continue
            candidate = pubkey.strip()
            if not candidate or candidate in seen:
                continue
            if signer is True:
                seen.add(candidate)
                signers.append(candidate)
                continue
            if signer is False:
                continue
            header = message.get("header") if isinstance(message.get("header"), dict) else {}
            required = header.get("numRequiredSignatures")
            if isinstance(required, int) and idx < required:
                seen.add(candidate)
                signers.append(candidate)

        if signers:
            return signers

        header = message.get("header") if isinstance(message.get("header"), dict) else {}
        required = header.get("numRequiredSignatures")
        if isinstance(required, int) and required > 0:
            for candidate in account_keys[:required]:
                clean = str(candidate or "").strip()
                if clean and clean not in seen:
                    seen.add(clean)
                    signers.append(clean)
        return signers

    def _is_viable_buyer_candidate(
        self,
        value: Any,
        *,
        mint: Optional[str] = None,
        token_account_to_mint: Optional[Dict[str, str]] = None,
    ) -> bool:
        if not isinstance(value, str):
            return False
        candidate = value.strip()
        if not candidate or candidate == mint:
            return False
        if candidate in KNOWN_NON_MINT_ADDRESSES:
            return False
        if candidate in self._bonding_curve_to_mint:
            return False
        if candidate in (token_account_to_mint or {}):
            return False
        if self._looks_like_pumpfun_mint(candidate):
            return False
        if not SOLANA_PUBKEY_RE.fullmatch(candidate):
            return False
        return True

    def _coerce_sol_amount_candidate(self, value: Any, *, key: str = "") -> Optional[float]:
        numeric_value: Optional[float] = None
        if isinstance(value, (int, float)):
            numeric_value = float(value)
        elif isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                numeric_value = float(stripped)
            except Exception:
                return None
        if numeric_value is None or numeric_value <= 0:
            return None

        lowered_key = str(key or "").lower()
        if "lamport" in lowered_key or lowered_key in {"maxsolcost", "solcost"}:
            return numeric_value / 1e9
        if "sol" in lowered_key:
            if numeric_value > 1_000_000:
                return numeric_value / 1e9
            return numeric_value
        return None

    def _recover_partial_trade_details_from_tx(
        self,
        tx_data: Optional[Dict],
        *,
        mint: str,
        buyer: Optional[str] = None,
        sol_amount: Optional[float] = None,
    ) -> Tuple[Optional[str], Optional[float], str, Optional[str]]:
        if not isinstance(tx_data, dict):
            return buyer, sol_amount, "count_only", "no_tx_context"

        token_account_to_mint = self._extract_token_account_mint_map(tx_data)
        token_account_to_owner = self._extract_token_account_owner_map(tx_data)
        instruction_contexts = self._extract_instruction_contexts_from_tx(tx_data)
        signer_keys = self._extract_signer_keys_from_tx(tx_data)

        mint_contexts: List[Dict[str, Any]] = []
        for context in instruction_contexts:
            info = context.get("info") if isinstance(context.get("info"), dict) else {}
            related_accounts = list(context.get("accounts") or [])
            for key in ("account", "source", "destination", "wallet", "owner", "sourceAccount", "destinationAccount"):
                value = info.get(key)
                if isinstance(value, str):
                    related_accounts.append(value)
            info_mentions_mint = any(
                str(info.get(key) or "").strip() == mint
                for key in ("mint", "tokenMint", "baseMint", "quoteMint")
            )
            account_mentions_mint = mint in related_accounts or any(
                token_account_to_mint.get(account) == mint for account in related_accounts
            )
            if info_mentions_mint or account_mentions_mint:
                mint_contexts.append(context)

        buyer_candidates: List[str] = []
        for context in mint_contexts:
            info = context.get("info") if isinstance(context.get("info"), dict) else {}
            for key in ("buyer", "user", "owner", "authority", "wallet", "payer", "signer", "sourceOwner", "destinationOwner"):
                value = info.get(key)
                if self._is_viable_buyer_candidate(value, mint=mint, token_account_to_mint=token_account_to_mint):
                    buyer_candidates.append(str(value).strip())

            for account in context.get("accounts") or []:
                owner = token_account_to_owner.get(account)
                if self._is_viable_buyer_candidate(owner, mint=mint, token_account_to_mint=token_account_to_mint):
                    buyer_candidates.append(str(owner).strip())
                if account in signer_keys and self._is_viable_buyer_candidate(account, mint=mint, token_account_to_mint=token_account_to_mint):
                    buyer_candidates.append(account)

        if buyer is None and buyer_candidates:
            ranked_candidates = sorted(
                set(buyer_candidates),
                key=lambda candidate: (
                    -buyer_candidates.count(candidate),
                    0 if candidate in signer_keys else 1,
                    candidate,
                ),
            )
            top_candidate = ranked_candidates[0]
            top_count = buyer_candidates.count(top_candidate)
            second_count = buyer_candidates.count(ranked_candidates[1]) if len(ranked_candidates) > 1 else 0
            if len(ranked_candidates) == 1 or top_count > second_count:
                buyer = top_candidate

        if buyer is None:
            viable_signers = [
                candidate
                for candidate in signer_keys
                if self._is_viable_buyer_candidate(candidate, mint=mint, token_account_to_mint=token_account_to_mint)
            ]
            if len(viable_signers) == 1:
                buyer = viable_signers[0]

        if sol_amount is None:
            for context in mint_contexts:
                info = context.get("info") if isinstance(context.get("info"), dict) else {}
                for key, value in info.items():
                    recovered_sol = self._coerce_sol_amount_candidate(value, key=key)
                    if recovered_sol is not None:
                        sol_amount = recovered_sol
                        break
                if sol_amount is not None:
                    break

        if sol_amount is None:
            account_keys = self._extract_account_keys_from_tx(tx_data)
            meta = (tx_data.get("meta") or {})
            pre_balances = meta.get("preBalances") or []
            post_balances = meta.get("postBalances") or []
            fee_lamports = float(meta.get("fee") or 0.0)
            target_wallet = buyer
            if target_wallet is None and len(signer_keys) == 1:
                target_wallet = signer_keys[0]
            if target_wallet and target_wallet in account_keys:
                account_index = account_keys.index(target_wallet)
                if account_index < len(pre_balances) and account_index < len(post_balances):
                    try:
                        lamport_delta = float(pre_balances[account_index] or 0.0) - float(post_balances[account_index] or 0.0)
                    except Exception:
                        lamport_delta = 0.0
                    attributable_delta = max(lamport_delta - fee_lamports, 0.0)
                    if attributable_delta > 0:
                        sol_amount = attributable_delta / 1e9

        if buyer is not None and sol_amount is not None:
            return buyer, sol_amount, "full", None
        if buyer is not None:
            return buyer, sol_amount, "buyer_only", None
        if sol_amount is not None:
            return buyer, sol_amount, "sol_only", None
        return buyer, sol_amount, "count_only", "no_confident_buyer_or_sol"

    def _pick_confident_pumpfun_mint(self, candidates: List[str]) -> Optional[str]:
        for candidate in candidates:
            if candidate in self._recent_birth_mints:
                return candidate
        for candidate in candidates:
            if candidate in self._known_bonding_curve_mints:
                return candidate
        for candidate in candidates:
            if self._looks_like_pumpfun_mint(candidate):
                return candidate
        return None

    def _build_unresolved_shape_summary(
        self,
        logs: List[str],
        *,
        tx_data: Optional[Dict] = None,
        normalized_candidates: Optional[List[str]] = None,
        birth_neighbor_candidates: Optional[List[str]] = None,
    ) -> str:
        instruction_names = self._extract_instruction_names_from_logs(logs)
        tx_instruction_names = []
        if tx_data:
            tx_instruction_names = [
                str(ctx.get("instruction") or "").strip()
                for ctx in self._extract_instruction_contexts_from_tx(tx_data)
                if str(ctx.get("instruction") or "").strip()
            ]
        names: List[str] = []
        seen: Set[str] = set()
        for name in instruction_names + tx_instruction_names:
            clean = str(name or "").strip()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            names.append(clean)
        ata_present = any(
            "createidempotent" in name.lower() or "create" == name.lower()
            for name in names
        ) or any("ATokenGP" in str(line) for line in (logs or []))
        token_program_present = any(
            token in " ".join(logs or [])
            for token in ("Tokenkeg", "TokenzQd", "ATokenGP")
        )
        mint_like_present = any(self._looks_like_pumpfun_mint(candidate) for candidate in (normalized_candidates or []))
        birth_neighbors = len(birth_neighbor_candidates or [])
        instr_preview = ",".join(names[:4]) if names else "none"
        return (
            f"[UNRESOLVED_SHAPE] sig=? instrs={instr_preview} "
            f"ata={'yes' if ata_present else 'no'} "
            f"token_prog={'yes' if token_program_present else 'no'} "
            f"mint_like={'yes' if mint_like_present else 'no'} "
            f"birth_neighbors={birth_neighbors}"
        )

    def _extract_buyer_from_logs(self, logs: List[str]) -> Optional[str]:
        text = " ".join(logs or [])
        patterns = [
            r"(?:buyer|user|owner|trader)[:=\s]+([1-9A-HJ-NP-Za-km-z]{32,44})",
            r"([1-9A-HJ-NP-Za-km-z]{32,44})\s+(?:bought|buying)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _extract_sol_amount_from_logs(self, logs: List[str]) -> Optional[float]:
        text = " ".join(logs or [])
        sol_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*SOL\b", text, flags=re.IGNORECASE)
        if sol_match:
            try:
                return float(sol_match.group(1))
            except Exception:
                return None

        lamports_match = re.search(r"([0-9]{5,})\s*lamports\b", text, flags=re.IGNORECASE)
        if lamports_match:
            try:
                return float(lamports_match.group(1)) / 1e9
            except Exception:
                return None
        return None

    def _extract_explicit_mint_from_logs(self, logs: List[str]) -> Optional[str]:
        """Best-effort extraction for logs that explicitly label the mint."""
        patterns = [
            r"(?:^|[\s,;])mint[:=\s]+([1-9A-HJ-NP-Za-km-z]{32,44})",
            r"(?:token[_\s-]?mint|base[_\s-]?mint)[:=\s]+([1-9A-HJ-NP-Za-km-z]{32,44})",
        ]
        for line in logs or []:
            for pattern in patterns:
                match = re.search(pattern, line, flags=re.IGNORECASE)
                if not match:
                    continue
                candidate = match.group(1)
                if candidate in self._known_bonding_curve_mints or self._looks_like_pumpfun_mint(candidate):
                    return candidate
        return None

    def _classify_mint_resolution_candidate(
        self,
        candidate: str,
        *,
        explicit_mint: Optional[str] = None,
    ) -> Tuple[str, Optional[str]]:
        if not isinstance(candidate, str):
            return "unknown", "non_string"
        value = candidate.strip()
        if not value:
            return "unknown", "empty"
        if value == explicit_mint:
            return "explicit_mint", None
        if value in self._bonding_curve_to_mint:
            return "known_bonding_curve_pda", None
        if value in KNOWN_NON_MINT_ADDRESSES:
            if value in {PUMPFUN_PROGRAM, PUMPSWAP_PROGRAM} or value.startswith("ComputeBudget") or value.startswith("BPFLoader"):
                return "known_program_id", "program_id"
            return "known_non_mint_account", "known_non_mint"
        if len(value) < 32 or len(value) > 44:
            return "unknown", "malformed_length"
        if not SOLANA_PUBKEY_RE.fullmatch(value):
            return "unknown", "not_base58_like"
        if value.startswith("AAAAAAAA") or value.endswith("AAAAAAAA"):
            return "unknown", "junk_pattern"
        if value in self._recent_birth_mints or value in self._known_bonding_curve_mints:
            return "mint_like", None
        if self._looks_like_pumpfun_mint(value):
            return "mint_like", None
        return "unknown", "not_pump_suffix"

    def _log_resolver_summary(self) -> None:
        resolved = int(self._resolver_resolved_count or 0)
        unresolved = int(self._resolver_unresolved_count or 0)
        total = resolved + unresolved
        resolved_pct = (resolved / total * 100.0) if total > 0 else 0.0
        premig_log(
            f"[RESOLVER_SUMMARY] index_rows={len(self._bonding_curve_to_mint)} "
            f"birth_cache={len(self._recent_birth_mints)} "
            f"resolved={resolved} unresolved={unresolved} resolved_pct={resolved_pct:.1f}"
        )

    def _evaluate_migration_prediction_snapshot(
        self,
        snapshot: Dict[str, Any],
        *,
        migrated_at: int,
    ) -> Dict[str, Any]:
        pre_migration_cutoff_seconds = 3
        pre_migration_cutoff = max(0, int(migrated_at) - pre_migration_cutoff_seconds)
        signal_updated_at = snapshot.get("migration_signal_updated_at")
        signal_age_seconds: Optional[int] = None
        if signal_updated_at is not None:
            try:
                signal_age_seconds = max(0, int(migrated_at) - int(signal_updated_at))
            except Exception:
                signal_age_seconds = None

        signal_observed_before_cutoff = (
            signal_updated_at is not None and int(signal_updated_at) <= pre_migration_cutoff
        )
        signal_observed_after_cutoff = (
            signal_updated_at is not None and int(signal_updated_at) > pre_migration_cutoff
        )
        signal_was_fresh = bool(
            signal_observed_before_cutoff
            and signal_age_seconds is not None
            and signal_age_seconds <= 15 * 60
        )
        market_cap_current = float(snapshot.get("market_cap_current") or 0.0)
        signal_source = str(snapshot.get("migration_signal_source") or "").strip().lower()
        migration_band = str(snapshot.get("migration_band") or "").strip().lower()
        explicit_signal = bool(snapshot.get("is_about_to_migrate")) or migration_band in {"hot", "warm"}
        weak_market_cap_signal = market_cap_current >= float(self._premigration_signal_floor_warm or 0.0)
        birth_only_signal = bool(
            signal_source == "birth"
            and not explicit_signal
            and not weak_market_cap_signal
        )
        has_observed_signal = bool(
            (signal_updated_at is not None and not birth_only_signal)
            or (signal_source and signal_source != "birth")
            or explicit_signal
            or weak_market_cap_signal
        )

        predicted_by_flow = bool(
            signal_was_fresh
            and signal_source == "flow"
        )
        predicted_by_market_cap = bool(
            signal_was_fresh and weak_market_cap_signal
        )
        predicted_by_explicit_signal = bool(signal_was_fresh and explicit_signal)
        was_about_to_migrate_at_migration = bool(signal_was_fresh and snapshot.get("is_about_to_migrate"))
        was_hot_or_warm_before_migration = bool(signal_was_fresh and migration_band in {"hot", "warm"})

        if predicted_by_flow or predicted_by_explicit_signal:
            final_verdict = "predicted"
        elif predicted_by_market_cap:
            final_verdict = "market_cap_only"
        elif birth_only_signal:
            final_verdict = "no_signal"
        elif signal_updated_at is None:
            final_verdict = "no_signal"
        elif not has_observed_signal:
            final_verdict = "no_signal"
        elif signal_observed_after_cutoff:
            final_verdict = "late_capture"
        elif not signal_was_fresh:
            final_verdict = "stale_signal"
        else:
            final_verdict = "missed"

        return {
            "predicted_by_flow": int(predicted_by_flow),
            "predicted_by_market_cap": int(predicted_by_market_cap),
            "predicted_by_explicit_signal": int(predicted_by_explicit_signal),
            "was_about_to_migrate_at_migration": int(was_about_to_migrate_at_migration),
            "was_hot_or_warm_before_migration": int(was_hot_or_warm_before_migration),
            "signal_age_seconds": signal_age_seconds,
            "signal_was_fresh": int(signal_was_fresh),
            "signal_before_cutoff": int(signal_observed_before_cutoff),
            "signal_after_cutoff": int(signal_observed_after_cutoff),
            "final_verdict": final_verdict,
        }

    async def _mark_token_migrated_in_db(
        self,
        mint: str,
        *,
        migrated_at: Optional[int] = None,
        migration_tx: Optional[str] = None,
        pool_address: Optional[str] = None,
        dex: Optional[str] = None,
        migration_slot: Optional[int] = None,
    ) -> None:
        migrated_ts = int(migrated_at or time.time())
        try:
            conn = db_connect(DB_PATH, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO token_analysis (
                    mint, analyzed_at, created_at, source_platform,
                    lifecycle_stage, migrated_at, migration_tx,
                    dex, pumpswap_pool_address, pool_address, migration_slot, is_new
                ) VALUES (?, ?, ?, 'pumpfun', 'migrated', ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(mint) DO UPDATE SET
                    analyzed_at = excluded.analyzed_at,
                    source_platform = COALESCE(token_analysis.source_platform, excluded.source_platform),
                    lifecycle_stage = 'migrated',
                    migrated_at = COALESCE(token_analysis.migrated_at, excluded.migrated_at),
                    migration_tx = COALESCE(excluded.migration_tx, token_analysis.migration_tx),
                    dex = COALESCE(excluded.dex, token_analysis.dex),
                    pumpswap_pool_address = COALESCE(excluded.pumpswap_pool_address, token_analysis.pumpswap_pool_address),
                    pool_address = COALESCE(excluded.pool_address, token_analysis.pool_address),
                    migration_slot = COALESCE(excluded.migration_slot, token_analysis.migration_slot),
                    is_new = 1
                """,
                (
                    mint,
                    float(time.time()),
                    migrated_ts,
                    migrated_ts,
                    migration_tx,
                    dex,
                    pool_address,
                    pool_address,
                    migration_slot,
                ),
            )
            conn.commit()
            conn.close()
            log_print(f"[DB] ✅ Marked token migrated: {mint[:16]}... dex={dex} tx={migration_tx[:20] if migration_tx else 'N/A'}...", flush=True)
        except Exception as exc:
            log_print(f"[MIGRATION_VERIFY] ⚠ Failed to mark migrated state for {mint[:16]}...: {exc}", flush=True)
            return

        def _score():
            try:
                import sqlite3 as _sq
                from src.core.token_prediction_builder import TokenPredictionBuilder
                conn2 = _sq.connect(DB_PATH, timeout=30)
                conn2.execute("PRAGMA journal_mode=WAL")
                TokenPredictionBuilder(DB_PATH).score_single(conn2, mint, 'MIGRATED')
                conn2.close()
            except Exception as _e:
                log_print(f"[PREDICTION] ⚠ score_single MIGRATED {mint[:16]}: {_e}", flush=True)
        _TOKEN_WORK_POOL.submit(_score)

    async def _record_migration_verification_snapshot(
        self,
        mint: str,
        *,
        migrated_at: Optional[int] = None,
        migration_tx: Optional[str] = None,
        dex: Optional[str] = None,
        pumpswap_pool_address: Optional[str] = None,
    ) -> None:
        migrated_ts = int(migrated_at or time.time())
        try:
            conn = db_connect(DB_PATH, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            row = cursor.execute(
                """
                SELECT mint, source_platform, lifecycle_stage, migration_tx, dex, pumpswap_pool_address,
                       is_about_to_migrate, migration_band, migration_progress_pct,
                       migration_signal_updated_at, first_pre_migration_signal_at, migration_signal_source,
                       market_cap_current, price_updated_at, bonding_curve_pda
                FROM token_analysis
                WHERE mint = ?
                """,
                (mint,),
            ).fetchone()
            row_dict = dict(row) if row else {}
            market_cap_current = float((row_dict or {}).get("market_cap_current") or self._last_market_cap_by_mint.get(mint, 0.0) or 0.0)
            signal_snapshot = self._compute_pre_migration_signal(mint, market_cap_current, migrated_ts)
            snapshot = {
                "mint": mint,
                "migration_signal_source": (row_dict or {}).get("migration_signal_source"),
                "is_about_to_migrate": int((row_dict or {}).get("is_about_to_migrate") or 0),
                "migration_band": (row_dict or {}).get("migration_band"),
                "migration_progress_pct": (row_dict or {}).get("migration_progress_pct"),
                "migration_signal_updated_at": (
                    (row_dict or {}).get("first_pre_migration_signal_at")
                    or (row_dict or {}).get("migration_signal_updated_at")
                ),
                "market_cap_current": market_cap_current,
                "market_cap_updated_at": (row_dict or {}).get("price_updated_at"),
                "buys_10s": int(signal_snapshot.get("buys_10s") or 0),
                "unique_30s": int(signal_snapshot.get("unique_buyers_30s") or 0),
                "sol_15s": float(signal_snapshot.get("sol_15s") or 0.0),
                "inflow_accel": float(signal_snapshot.get("inflow_accel") or 0.0),
                "signal_score": int(signal_snapshot.get("score") or 0),
            }
            evaluation = self._evaluate_migration_prediction_snapshot(snapshot, migrated_at=migrated_ts)
            cursor.execute(
                """
                INSERT INTO pumpfun_migration_verification (
                    mint, migrated_at, migration_tx, dex, pumpswap_pool_address,
                    pre_is_about_to_migrate, pre_migration_band, pre_migration_progress_pct,
                    pre_migration_signal_updated_at, pre_market_cap_current, pre_market_cap_updated_at,
                    pre_buys_10s, pre_unique_30s, pre_sol_15s, pre_inflow_accel, pre_signal_score,
                    pre_migration_signal_source, predicted_by_flow, predicted_by_market_cap,
                    predicted_by_explicit_signal, was_about_to_migrate_at_migration,
                    was_hot_or_warm_before_migration, signal_age_seconds, signal_was_fresh,
                    final_verdict, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mint) DO UPDATE SET
                    migrated_at = excluded.migrated_at,
                    migration_tx = COALESCE(excluded.migration_tx, pumpfun_migration_verification.migration_tx),
                    dex = COALESCE(excluded.dex, pumpfun_migration_verification.dex),
                    pumpswap_pool_address = COALESCE(excluded.pumpswap_pool_address, pumpfun_migration_verification.pumpswap_pool_address),
                    pre_is_about_to_migrate = excluded.pre_is_about_to_migrate,
                    pre_migration_band = excluded.pre_migration_band,
                    pre_migration_progress_pct = excluded.pre_migration_progress_pct,
                    pre_migration_signal_updated_at = excluded.pre_migration_signal_updated_at,
                    pre_market_cap_current = excluded.pre_market_cap_current,
                    pre_market_cap_updated_at = excluded.pre_market_cap_updated_at,
                    pre_buys_10s = excluded.pre_buys_10s,
                    pre_unique_30s = excluded.pre_unique_30s,
                    pre_sol_15s = excluded.pre_sol_15s,
                    pre_inflow_accel = excluded.pre_inflow_accel,
                    pre_signal_score = excluded.pre_signal_score,
                    pre_migration_signal_source = excluded.pre_migration_signal_source,
                    predicted_by_flow = excluded.predicted_by_flow,
                    predicted_by_market_cap = excluded.predicted_by_market_cap,
                    predicted_by_explicit_signal = excluded.predicted_by_explicit_signal,
                    was_about_to_migrate_at_migration = excluded.was_about_to_migrate_at_migration,
                    was_hot_or_warm_before_migration = excluded.was_hot_or_warm_before_migration,
                    signal_age_seconds = excluded.signal_age_seconds,
                    signal_was_fresh = excluded.signal_was_fresh,
                    final_verdict = excluded.final_verdict,
                    created_at = excluded.created_at
                """,
                (
                    mint,
                    migrated_ts,
                    migration_tx or (row_dict or {}).get("migration_tx"),
                    dex or (row_dict or {}).get("dex"),
                    pumpswap_pool_address or (row_dict or {}).get("pumpswap_pool_address"),
                    snapshot["is_about_to_migrate"],
                    snapshot["migration_band"],
                    snapshot["migration_progress_pct"],
                    snapshot["migration_signal_updated_at"],
                    snapshot["market_cap_current"],
                    snapshot["market_cap_updated_at"],
                    snapshot["buys_10s"],
                    snapshot["unique_30s"],
                    snapshot["sol_15s"],
                    snapshot["inflow_accel"],
                    snapshot["signal_score"],
                    snapshot["migration_signal_source"],
                    evaluation["predicted_by_flow"],
                    evaluation["predicted_by_market_cap"],
                    evaluation["predicted_by_explicit_signal"],
                    evaluation["was_about_to_migrate_at_migration"],
                    evaluation["was_hot_or_warm_before_migration"],
                    evaluation["signal_age_seconds"],
                    evaluation["signal_was_fresh"],
                    evaluation["final_verdict"],
                    migrated_ts,
                ),
            )
            conn.commit()
            conn.close()
            log_print(
                f"[MIGRATION_VERIFY] mint={mint[:16]} verdict={evaluation['final_verdict']} "
                f"flow={evaluation['predicted_by_flow']} market_cap={evaluation['predicted_by_market_cap']} "
                f"explicit={evaluation['predicted_by_explicit_signal']} age={evaluation['signal_age_seconds']}",
                flush=True,
            )
        except Exception as exc:
            log_print(f"[MIGRATION_VERIFY] ⚠ Failed to store verification snapshot for {mint[:16]}...: {exc}", flush=True)

    def _lookup_recent_unresolved_mint_in_db(self, candidates: List[str]) -> Tuple[Optional[str], Optional[str]]:
        """Optional DB fallback for very recent unresolved Pump.fun candidates."""
        if not candidates:
            return None, None
        try:
            unique_candidates = list(dict.fromkeys(candidates[:8]))
            placeholders = ",".join("?" for _ in unique_candidates)
            recent_cutoff = time.time() - (2 * 60 * 60)
            query = f"""
                SELECT mint, bonding_curve_pda
                FROM token_analysis
                WHERE (
                        mint IN ({placeholders})
                        OR bonding_curve_pda IN ({placeholders})
                      )
                  AND (
                        COALESCE(source_platform, '') = 'pumpfun'
                        OR COALESCE(lifecycle_stage, '') = 'bonding_curve'
                        OR NULLIF(TRIM(COALESCE(bonding_curve_pda, '')), '') IS NOT NULL
                      )
                  AND COALESCE(analyzed_at, 0) >= ?
                ORDER BY COALESCE(analyzed_at, 0) DESC
                LIMIT 1
            """
            conn = db_connect(DB_PATH, timeout=15)
            cursor = conn.cursor()
            cursor.execute(query, tuple(unique_candidates + unique_candidates + [recent_cutoff]))
            row = cursor.fetchone()
            conn.close()
            if not row:
                return None, None
            mint = str(row[0]) if row[0] else None
            bonding_curve = str(row[1]) if row[1] else None
            if mint:
                self._remember_bonding_curve_token(mint, bonding_curve)
            return mint, bonding_curve
        except Exception:
            return None, None

    async def _get_trade_transaction_context(self, signature: Optional[str]) -> Optional[Dict]:
        if not signature:
            return None
        try:
            cached = (self.tx_cache or {}).get(signature)
            if cached:
                cached_data, cached_time = cached
                if (time.time() - float(cached_time or 0.0)) < float(self.tx_cache_ttl_seconds or 0):
                    return cached_data
        except Exception:
            pass

        try:
            result = await self.call_background_rpc(
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "commitment": "confirmed",
                        "maxSupportedTransactionVersion": 0,
                    },
                ],
                timeout=4.0,
            )
            tx_data = result.get("result") if isinstance(result, dict) else None
            if tx_data:
                self.tx_cache[signature] = (tx_data, time.time())
            return tx_data
        except Exception:
            return None

    async def _infer_indirect_pumpfun_mint(
        self,
        signature: Optional[str],
        logs: List[str],
        *,
        normalized_candidates: Optional[List[str]] = None,
    ) -> Tuple[Optional[str], Optional[str], List[str], Optional[str], Optional[Dict], List[str]]:
        sig_label = (signature or "")[:16]
        log_print(f"[INDIRECT_INFER_ATTEMPT] sig={sig_label}", flush=True)
        tx_data = await self._get_trade_transaction_context(signature)
        if not tx_data:
            log_print(f"[INDIRECT_INFER_FAIL] sig={sig_label} reason=no_tx_context", flush=True)
            return None, None, [], "no_tx_context", None, []

        account_keys = self._extract_account_keys_from_tx(tx_data)
        instruction_contexts = self._extract_instruction_contexts_from_tx(tx_data)
        token_account_to_mint = self._extract_token_account_mint_map(tx_data)

        direct_info_mints: List[str] = []
        ata_context_mints: List[str] = []
        token_account_context_mints: List[str] = []
        birth_neighbor_mints: List[str] = []
        tx_candidates: List[str] = []

        for key in account_keys:
            if key in self._bonding_curve_to_mint:
                mapped_mint = self._bonding_curve_to_mint.get(key)
                if mapped_mint:
                    tx_candidates.append(mapped_mint)
                    birth_neighbor_mints.append(mapped_mint)
            if key in self._recent_birth_mints:
                birth_neighbor_mints.append(key)
                tx_candidates.append(key)

        for mint in token_account_to_mint.values():
            tx_candidates.append(mint)

        for context in instruction_contexts:
            info = context.get("info") if isinstance(context.get("info"), dict) else {}
            program_id = str(context.get("program_id") or "")
            instruction_name = str(context.get("instruction") or "")
            is_ata_context = (
                program_id == "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
                or instruction_name.lower() in {"createidempotent", "create"}
            )
            for mint_key in ("mint", "tokenMint", "baseMint", "quoteMint"):
                mint_value = info.get(mint_key)
                if isinstance(mint_value, str):
                    tx_candidates.append(mint_value)
                    if is_ata_context:
                        ata_context_mints.append(mint_value)
                    else:
                        direct_info_mints.append(mint_value)

            related_accounts = list(context.get("accounts") or [])
            for related_key in ("account", "source", "destination", "wallet", "owner", "sourceAccount", "destinationAccount"):
                related_value = info.get(related_key)
                if isinstance(related_value, str):
                    related_accounts.append(related_value)
            for account in related_accounts:
                mint_value = token_account_to_mint.get(account)
                if not mint_value:
                    continue
                tx_candidates.append(mint_value)
                if is_ata_context:
                    ata_context_mints.append(mint_value)
                else:
                    token_account_context_mints.append(mint_value)

        birth_neighbor_mints = list(dict.fromkeys(birth_neighbor_mints))
        direct_info_mints = list(dict.fromkeys(direct_info_mints))
        ata_context_mints = list(dict.fromkeys(ata_context_mints))
        token_account_context_mints = list(dict.fromkeys(token_account_context_mints))
        tx_candidates = self._normalize_mint_resolution_candidates(tx_candidates)

        for candidates, source in (
            (direct_info_mints, "token_account_context"),
            (birth_neighbor_mints, "birth_neighbor"),
            (ata_context_mints, "ata_context"),
            (token_account_context_mints, "token_account_context"),
        ):
            resolved = self._pick_confident_pumpfun_mint(candidates)
            if resolved:
                shortlist = self._normalize_mint_resolution_candidates((normalized_candidates or []) + [resolved])[:5]
                log_print(
                    f"[INDIRECT_INFER_SUCCESS] sig={sig_label} mint={resolved} source={source}",
                    flush=True,
                )
                return resolved, source, shortlist, f"indirect_{source}={resolved}", tx_data, birth_neighbor_mints

        mint, bonding_curve = self._lookup_recent_unresolved_mint_in_db(tx_candidates)
        if mint:
            shortlist = self._normalize_mint_resolution_candidates((normalized_candidates or []) + tx_candidates + [mint])[:5]
            reason = f"indirect_db_lookup_mint={mint}"
            if bonding_curve:
                reason += f" bonding_curve={bonding_curve}"
            log_print(
                f"[INDIRECT_INFER_SUCCESS] sig={sig_label} mint={mint} source=token_account_context",
                flush=True,
            )
            return mint, "token_account_context", shortlist, reason, tx_data, birth_neighbor_mints

        log_print(f"[INDIRECT_INFER_FAIL] sig={sig_label} reason=no_strong_link", flush=True)
        return None, None, tx_candidates[:5], "no_strong_link", tx_data, birth_neighbor_mints

    async def _resolve_bonding_curve_mint_for_trade(
        self,
        signature: str,
        logs: List[str],
    ) -> Tuple[Optional[str], Optional[str], List[str], Optional[str], Optional[Dict]]:
        mint, source, candidates, reason = self._resolve_bonding_curve_mint_from_logs_detailed(logs, signature=signature)
        if mint:
            return mint, source, candidates, reason, None

        normalized_candidates = self._normalize_mint_resolution_candidates(self._extract_raw_mint_resolution_candidates(logs))
        inferred_mint, inferred_source, inferred_candidates, inferred_reason, tx_data, birth_neighbors = await self._infer_indirect_pumpfun_mint(
            signature,
            logs,
            normalized_candidates=normalized_candidates,
        )
        if inferred_mint:
            merged_candidates = self._normalize_mint_resolution_candidates((candidates or []) + (inferred_candidates or []))[:5]
            return inferred_mint, inferred_source, merged_candidates, inferred_reason, tx_data

        shape_line = self._build_unresolved_shape_summary(
            logs,
            tx_data=tx_data,
            normalized_candidates=normalized_candidates,
            birth_neighbor_candidates=birth_neighbors,
        ).replace("sig=?", f"sig={(signature or '')[:16]}")
        premig_log(shape_line)
        return None, source, candidates, reason or inferred_reason, tx_data

    def _resolve_bonding_curve_mint_from_logs_detailed(
        self,
        logs: List[str],
        *,
        signature: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str], List[str], Optional[str]]:
        """Resolve buy mints with source attribution while preserving the legacy return path."""
        self._prune_recent_birth_cache()
        explicit_mint = self._extract_explicit_mint_from_logs(logs)

        raw_candidates = self._extract_raw_mint_resolution_candidates(logs, explicit_mint)
        normalized_candidates = self._normalize_mint_resolution_candidates(raw_candidates)
        viable_candidates: List[str] = []
        rejected_candidates: List[Tuple[str, str]] = []
        explicit_candidates: List[str] = []
        birth_cache_candidates: List[str] = []
        mint_like_candidates: List[str] = []
        pda_candidates: List[str] = []

        for candidate in normalized_candidates:
            classification, rejection_reason = self._classify_mint_resolution_candidate(
                candidate,
                explicit_mint=explicit_mint,
            )
            if classification == "explicit_mint":
                viable_candidates.append(candidate)
                explicit_candidates.append(candidate)
                log_print(f"[MINT_CANDIDATE_ACCEPT] sig={(signature or '')[:16]} value={candidate} class=explicit_mint", flush=True)
                continue
            if classification == "known_bonding_curve_pda":
                viable_candidates.append(candidate)
                pda_candidates.append(candidate)
                continue
            if classification == "mint_like":
                viable_candidates.append(candidate)
                if candidate in self._recent_birth_mints:
                    birth_cache_candidates.append(candidate)
                    log_print(f"[MINT_CANDIDATE_ACCEPT] sig={(signature or '')[:16]} value={candidate} class=birth_cache", flush=True)
                else:
                    mint_like_candidates.append(candidate)
                    log_print(f"[MINT_CANDIDATE_ACCEPT] sig={(signature or '')[:16]} value={candidate} class=mint_like", flush=True)
                continue
            rejected_candidates.append((candidate, rejection_reason or classification))
            log_print(
                f"[MINT_CANDIDATE_REJECT] sig={(signature or '')[:16]} value={candidate} reason={rejection_reason or classification}",
                flush=True,
            )

        top_candidates = (explicit_candidates + birth_cache_candidates + mint_like_candidates + pda_candidates)[:5]
        log_print(
            f"[CANDIDATE_SUMMARY] sig={(signature or '')[:16]} raw={len(raw_candidates)} "
            f"normalized={len(normalized_candidates)} rejected={len(rejected_candidates)} viable={len(viable_candidates)}",
            flush=True,
        )
        for rejected_value, rejected_reason in rejected_candidates[:3]:
            log_print(
                f"[CANDIDATE_REJECTION] sig={(signature or '')[:16]} value={rejected_value} reason={rejected_reason}",
                flush=True,
            )

        if explicit_candidates:
            mint = explicit_candidates[0]
            return mint, "explicit_mint", top_candidates, f"explicit_mint={mint}"

        if birth_cache_candidates:
            mint = birth_cache_candidates[0]
            return mint, "birth_cache", top_candidates, f"recent_birth={mint}"

        if mint_like_candidates:
            mint = mint_like_candidates[0]
            return mint, "mint_candidate", top_candidates, f"mint_like={mint}"

        mint, bonding_curve = self._lookup_recent_unresolved_mint_in_db(viable_candidates or normalized_candidates)
        if mint:
            reason = f"db_lookup_mint={mint}"
            if bonding_curve:
                reason += f" bonding_curve={bonding_curve}"
            shortlist = top_candidates or [mint]
            return mint, "db_refresh", shortlist[:5], reason

        for candidate in pda_candidates:
            mint = self._bonding_curve_to_mint.get(candidate)
            if mint:
                return mint, "pda_map", top_candidates[:5], f"bonding_curve={candidate}"

        refreshed = self._refresh_bonding_curve_index(force=True)
        if refreshed:
            mint, bonding_curve = self._lookup_recent_unresolved_mint_in_db(viable_candidates or normalized_candidates)
            if mint:
                reason = f"db_lookup_mint={mint}"
                if bonding_curve:
                    reason += f" bonding_curve={bonding_curve}"
                shortlist = top_candidates or [mint]
                return mint, "db_refresh", shortlist[:5], reason
            for candidate in pda_candidates:
                mint = self._bonding_curve_to_mint.get(candidate)
                if mint:
                    return mint, "pda_map", top_candidates[:5], f"bonding_curve={candidate}"

        return None, None, top_candidates[:5], "no_viable_candidate"

    def _resolve_bonding_curve_mint_from_logs(self, logs: List[str]) -> Optional[str]:
        mint, _, _, _ = self._resolve_bonding_curve_mint_from_logs_detailed(logs)
        return mint

    def _debug_pumpfun_trade_skip(self, reason: str, logs: List[str]) -> None:
        if self._pumpfun_trade_debug_budget <= 0:
            return
        self._pumpfun_trade_debug_budget -= 1
        joined = " | ".join((logs or [])[:6])
        log_print(f"[PREMIG_DEBUG] reason={reason} logs={joined[:400]}", flush=True)

    def _record_flow_event(
        self,
        mint: str,
        *,
        observed_at: Optional[float] = None,
        buyer: Optional[str] = None,
        sol_amount: Optional[float] = None,
        kind: str = "buy",
    ) -> None:
        if not mint:
            return
        now = float(observed_at or time.time())
        flow = self._flow_windows_by_mint.setdefault(mint, deque())
        flow.append({
            "ts": now,
            "buyer": buyer,
            "sol_amount": float(sol_amount) if sol_amount is not None else None,
            "kind": kind,
        })
        cutoff = now - 45
        while flow and float(flow[0]["ts"]) < cutoff:
            flow.popleft()

    # Maximum plausible market cap for a Pump.fun bonding-curve token at migration.
    # Pump.fun migration occurs at ~$69k SOL raised → MC ceiling ≈ $150k.
    # Anything beyond $5M is almost certainly a corrupted value (wrong pair, unit bug, etc.)
    # and must not be used for fallback classification.
    _PREMIG_MC_SANITY_CAP = 5_000_000  # $5M USD

    def _compute_pre_migration_signal(
        self,
        mint: str,
        current_market_cap: float,
        now_ts: int,
    ) -> Dict[str, Optional[float]]:
        # Clamp corrupted/stale MC values before any scoring or fallback logic.
        # DexScreener sometimes returns migrated-pair data for bonding-curve rows,
        # producing trillion-scale values that cause every token to be classified hot.
        sanitised_mc = current_market_cap
        if current_market_cap and current_market_cap > self._PREMIG_MC_SANITY_CAP:
            premig_log(f"[MC_CLAMPED] mint={mint} raw_mc={current_market_cap} clamped_to=0")
            sanitised_mc = 0.0
        current_market_cap = sanitised_mc

        events = self._flow_windows_by_mint.get(mint, deque())
        buys_10s = 0
        buy_events_45s = 0
        buyers_30s = set()
        sol_15s = 0.0
        sol_15s_prev = 0.0
        have_trade_details = False

        for event in events:
            age = now_ts - float(event["ts"])
            if event.get("kind") != "buy":
                continue
            buy_events_45s += 1
            if age <= 10:
                buys_10s += 1
            if age <= 30 and event.get("buyer"):
                buyers_30s.add(str(event["buyer"]))
            if event.get("sol_amount") is not None:
                have_trade_details = True
                if age <= 15:
                    sol_15s += float(event["sol_amount"] or 0.0)
                elif 15 < age <= 30:
                    sol_15s_prev += float(event["sol_amount"] or 0.0)

        score = 0
        fallback_used = False
        if buys_10s >= 10:
            score += 1
        if buys_10s >= 20:
            score += 1

        unique_buyers_30s = len(buyers_30s)
        if unique_buyers_30s >= 6:
            score += 1
        if unique_buyers_30s >= 12:
            score += 1

        if sol_15s >= 20:
            score += 1
        if sol_15s >= 40:
            score += 1

        if sol_15s_prev > 0 and (sol_15s / sol_15s_prev) >= 1.5:
            score += 1
        if sol_15s_prev > 0 and (sol_15s / sol_15s_prev) >= 2.0:
            score += 1

        inflow_accel = (sol_15s / sol_15s_prev) if sol_15s_prev > 0 else 0.0

        flow_min_buys = buys_10s >= 6
        flow_min_unique = unique_buyers_30s >= 4
        flow_min_sol = sol_15s >= 2.0
        flow_min_hits = int(flow_min_buys) + int(flow_min_unique) + int(flow_min_sol)
        flow_candidate = score >= 3 or flow_min_hits >= 1
        strong_flow = score >= 5 or flow_min_hits >= 2
        very_strong_flow = score >= 7 or flow_min_hits >= 3 or (score >= 5 and inflow_accel >= 1.5)

        band = None
        is_about_to_migrate = 0
        if very_strong_flow:
            band = "hot"
            is_about_to_migrate = 1
        elif strong_flow:
            band = "warm"
        elif flow_candidate:
            band = "likely_close"
        elif score >= 1:
            band = "early"

        if not band:
            if current_market_cap >= self._premigration_signal_floor_hot:
                band = "hot"
                is_about_to_migrate = 1
                fallback_used = True
            elif current_market_cap >= self._premigration_signal_floor_warm:
                band = "warm"
                fallback_used = True

        if band == "hot":
            progress = max(min(score * 12.5, 100.0), 87.5)
        elif band == "warm":
            progress = max(min(score * 12.5, 87.5), 67.5)
        elif band == "likely_close":
            progress = max(min(score * 12.5, 72.5), 50.0)
        elif band == "early":
            progress = max(min(score * 12.5, 45.0), 25.0)
        else:
            progress = 0.0
        if progress <= 0 and current_market_cap >= self._premigration_signal_floor_hot:
            progress = 87.5
            fallback_used = True
        elif progress <= 0 and current_market_cap >= self._premigration_signal_floor_warm:
            progress = 62.5
            fallback_used = True

        premig_log(
            f"[FLOW_METRICS] mint={mint} "
            f"buys_10s={buys_10s} "
            f"unique_30s={unique_buyers_30s} "
            f"sol_15s={sol_15s:.4f} "
            f"sol_prev_15s={sol_15s_prev:.4f}"
        )
        premig_log(
            f"[SCORE] mint={mint} score={score} band={band} "
            f"flow_min_hits={flow_min_hits} flow_candidate={int(flow_candidate)} "
            f"strong_flow={int(strong_flow)}"
        )

        if buys_10s == 0 and sol_15s == 0:
            premig_log(f"[ZERO_FLOW] mint={mint} no activity detected")

        if current_market_cap and current_market_cap > 100_000_000:
            premig_log(f"[MC_ANOMALY] mint={mint} mc={current_market_cap}")

        log_print(
            f"[PREMIG_SIGNAL] mint={mint[:6]} "
            f"score={score} band={band} "
            f"buys_10s={buys_10s} "
            f"unique_30s={unique_buyers_30s} "
            f"sol_15s={sol_15s:.2f} "
            f"accel={inflow_accel:.2f}",
            flush=True,
        )
        if fallback_used:
            premig_log(f"[FALLBACK_USED] mint={mint} mc={current_market_cap} band={band}")
            log_print(
                f"[PREMIG_FALLBACK] mint={mint[:6]} "
                f"mc={current_market_cap:.2f} -> band={band}",
                flush=True,
            )

        return {
            "score": score,
            "buys_10s": buys_10s,
            "unique_buyers_30s": unique_buyers_30s,
            "sol_15s": sol_15s,
            "sol_15s_prev": sol_15s_prev,
            "inflow_accel": inflow_accel,
            "band": band,
            "is_about_to_migrate": is_about_to_migrate,
            "migration_progress_pct": progress if band else None,
            "has_trade_details": have_trade_details,
            "observed_flow_event": buy_events_45s > 0,
            "fallback_used": fallback_used,
            "signal_source": "flow" if flow_candidate else ("fallback" if band else None),
        }

    async def _persist_pre_migration_signal(
        self,
        mint: str,
        current_market_cap: float,
        now_ts: Optional[int] = None,
        *,
        source_hint: Optional[str] = None,
    ) -> None:
        now = int(now_ts or time.time())
        signal = self._compute_pre_migration_signal(mint, current_market_cap or 0.0, now)

        should_check_pf_ws_creator = False
        pf_ws_creator_band: Optional[str] = None

        async with self.db_lock:
            try:
                conn = db_connect(DB_PATH, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT
                        mint,
                        source_platform,
                        lifecycle_stage,
                        dex,
                        pool_address,
                        pumpswap_pool_address,
                        create_tx_signature,
                        bonding_curve_pda,
                        is_about_to_migrate,
                        migration_band,
                        migration_progress_pct,
                        migration_signal_source,
                        migration_signal_updated_at,
                        first_pre_migration_signal_at,
                        pf_ws_creator
                    FROM token_analysis
                    WHERE mint = ?
                    LIMIT 1
                    """,
                    (mint,),
                )
                row = cursor.fetchone()
                row_exists = row is not None
                source_platform = str(row[1]) if row and row[1] is not None else None
                lifecycle_stage = str(row[2]) if row and row[2] is not None else None
                dex_value = str(row[3]) if row and row[3] is not None else None
                pool_address = str(row[4]) if row and row[4] is not None else None
                pf_ws_creator_from_row = str(row[15]) if row and row[15] is not None else None
                pumpswap_pool_address = str(row[5]) if row and row[5] is not None else None
                create_tx_signature = str(row[6]) if row and row[6] is not None else None
                bonding_curve_pda = str(row[7]) if row and row[7] is not None else None
                before_row = row[8:14] if row else None
                premig_log(
                    f"[DB_MATCH_CHECK] mint={mint} "
                    f"row_exists={'yes' if row_exists else 'no'} "
                    f"source_platform={source_platform or ''} "
                    f"lifecycle_stage={lifecycle_stage or ''} "
                    f"dex={dex_value or ''} "
                    f"pool_address={pool_address or ''} "
                    f"pumpswap_pool_address={pumpswap_pool_address or ''} "
                    f"create_tx_signature={create_tx_signature or ''} "
                    f"bonding_curve_pda={bonding_curve_pda or ''}"
                )

                row_materialized = False
                if not row_exists and source_hint == "flow":
                    cursor.execute(
                        """
                        INSERT INTO token_analysis (
                            mint,
                            analyzed_at,
                            created_at,
                            source_platform,
                            lifecycle_stage,
                            migration_signal_updated_at,
                            first_pre_migration_signal_at
                        ) VALUES (?, ?, ?, 'pumpfun', 'bonding_curve', ?, ?)
                        ON CONFLICT(mint) DO UPDATE SET
                            analyzed_at = COALESCE(token_analysis.analyzed_at, excluded.analyzed_at),
                            created_at = COALESCE(token_analysis.created_at, excluded.created_at),
                            source_platform = COALESCE(NULLIF(TRIM(token_analysis.source_platform), ''), excluded.source_platform),
                            lifecycle_stage = CASE
                                WHEN COALESCE(token_analysis.lifecycle_stage, 'migration_pending') = 'migrated' THEN token_analysis.lifecycle_stage
                                ELSE 'bonding_curve'
                            END,
                            migration_signal_updated_at = COALESCE(token_analysis.migration_signal_updated_at, excluded.migration_signal_updated_at),
                            first_pre_migration_signal_at = COALESCE(token_analysis.first_pre_migration_signal_at, excluded.first_pre_migration_signal_at)
                        """,
                        (mint, float(now), now, now, now),
                    )
                    row_materialized = True
                    premig_log(f"[DB_CREATE] mint={mint} source=flow")
                    cursor.execute(
                        """
                        SELECT
                            is_about_to_migrate,
                            migration_band,
                            migration_progress_pct,
                            migration_signal_source,
                            migration_signal_updated_at,
                            first_pre_migration_signal_at
                        FROM token_analysis
                        WHERE mint = ?
                        LIMIT 1
                        """,
                        (mint,),
                    )
                    before_row = cursor.fetchone()
                    source_platform = "pumpfun"
                    lifecycle_stage = "bonding_curve"

                signal_source = signal["signal_source"]
                if before_row and (before_row[3] or None) == "flow" and signal_source != "flow":
                    signal["is_about_to_migrate"] = int(before_row[0] or 0)
                    signal["band"] = before_row[1] or None
                    signal["migration_progress_pct"] = before_row[2]
                    signal_source = "flow"

                signal_has_presence = bool(
                    signal.get("observed_flow_event")
                    or signal_source
                    or signal.get("band")
                    or signal.get("is_about_to_migrate")
                    or int(signal.get("score") or 0) > 0
                    or float(current_market_cap or 0.0) >= float(self._premigration_signal_floor_warm or 0.0)
                )
                existing_signal_updated_at = before_row[4] if before_row else None
                existing_first_pre_signal_at = before_row[5] if before_row else None
                changed_values = (
                    before_row is None
                    or int(before_row[0] or 0) != int(signal["is_about_to_migrate"] or 0)
                    or (before_row[1] or None) != (signal["band"] or None)
                    or before_row[2] != signal["migration_progress_pct"]
                    or (before_row[3] or None) != (signal_source or None)
                )
                # Preserve the first observed pre-migration signal boundary. Later
                # upgrades from none -> likely_close -> warm -> hot should not erase
                # when we first saw real pre-migration activity.
                should_seed_signal_timestamp = bool(signal_has_presence and existing_signal_updated_at is None)
                signal_updated_at_to_write = now if should_seed_signal_timestamp else existing_signal_updated_at
                should_seed_first_pre_signal_at = bool(signal_has_presence and existing_first_pre_signal_at is None)
                first_pre_signal_at_to_write = now if should_seed_first_pre_signal_at else existing_first_pre_signal_at

                # Avoid churn from periodic "empty" refreshes that do not carry any
                # observed pre-migration signal and do not change stored state.
                if source_hint != "flow" and (not signal_has_presence) and (not changed_values):
                    conn.close()
                    premig_log(f"[DB_SKIP] mint={mint} reason=empty_periodic_refresh")
                    return

                cursor.execute(
                    """
                    UPDATE token_analysis
                    SET
                        source_platform = CASE
                            WHEN COALESCE(source_platform, '') = '' THEN 'pumpfun'
                            ELSE source_platform
                        END,
                        lifecycle_stage = CASE
                            WHEN COALESCE(lifecycle_stage, 'migration_pending') = 'migrated' THEN lifecycle_stage
                            ELSE 'bonding_curve'
                        END,
                        is_about_to_migrate = ?,
                        migration_band = ?,
                        migration_progress_pct = ?,
                        migration_signal_source = CASE
                            WHEN ? = 'flow' THEN 'flow'
                            WHEN COALESCE(migration_signal_source, '') = 'flow' THEN migration_signal_source
                            ELSE ?
                        END,
                        migration_signal_updated_at = ?,
                        first_pre_migration_signal_at = COALESCE(first_pre_migration_signal_at, ?)
                    WHERE mint = ?
                      AND COALESCE(lifecycle_stage, 'migration_pending') != 'migrated'
                    """,
                    (
                        signal["is_about_to_migrate"],
                        signal["band"],
                        signal["migration_progress_pct"],
                        signal_source,
                        signal_source,
                        signal_updated_at_to_write,
                        first_pre_signal_at_to_write,
                        mint,
                    ),
                )
                changed = cursor.rowcount
                conn.commit()
                conn.close()

                if not changed:
                    premig_log(
                        f"[DB_MATCH_CHECK] mint={mint} row_exists={'yes' if row_exists else 'no'} "
                        f"source_platform={source_platform or ''} lifecycle_stage={lifecycle_stage or ''}"
                    )
                    premig_log(f"[DB_SKIP] mint={mint} reason=no_matching_row")
                if changed:
                    if not changed_values:
                        premig_log(f"[DB_SKIP] mint={mint} reason=no_value_change band={signal['band']} source={signal_source}")
                    if changed_values:
                        write_label = "DB_CREATE" if row_materialized else "DB_UPDATE"
                        premig_log(
                            f"[{write_label}] mint={mint} "
                            f"source={signal_source} "
                            f"band={signal['band']} "
                            f"progress={float(signal['migration_progress_pct'] or 0.0):.1f}"
                        )
                        premig_log(
                            f"[DB_WRITE] mint={mint} "
                            f"source={signal_source} "
                            f"band={signal['band']} "
                            f"progress={float(signal['migration_progress_pct'] or 0.0):.1f}"
                        )
                        log_print(
                            f"[PREMIG_DB_WRITE] mint={mint[:6]} "
                            f"band={signal['band']} "
                            f"about_to_migrate={signal['is_about_to_migrate']} "
                            f"progress={float(signal['migration_progress_pct'] or 0.0):.1f} "
                            f"source={signal_source}",
                            flush=True,
                        )
                    if should_seed_first_pre_signal_at:
                        log_print(
                            f"[PREMIG_FIRST_SEEN] mint={mint[:6]} ts={first_pre_signal_at_to_write} "
                            f"band={signal['band']} source={signal_source or 'none'}",
                            flush=True,
                        )
                    log_print(f"[PREMIG_REFRESH] mint={mint[:6]} ts_updated", flush=True)

                    should_check_pf_ws_creator = bool(
                        changed_values
                        and signal.get("is_about_to_migrate")
                    )
                    pf_ws_creator_band = signal.get("band") or "unknown"

                    # Early funding extraction — trigger when token first hits HOT
                    # so creator profile is ready before/at migration time
                    if (
                        changed_values
                        and signal.get("band") == "hot"
                        and (before_row is None or (before_row[1] or "") != "hot")
                    ):
                        _hot_creator = pf_ws_creator_from_row
                        _hot_sig = str(row[6]) if row and row[6] else None

                        async def _enqueue_with_creator_resolve(_mint=mint, _creator=_hot_creator, _sig=_hot_sig):
                            if not _creator:
                                _creator = await self._ensure_pf_ws_creator(_mint, reason="hot_band_early")
                            if not _creator:
                                log_print(f"[EARLY_FUNDING] ⚠ mint={_mint[:8]} band=hot — creator unresolved, skipping", flush=True)
                                return
                            log_print(
                                f"[EARLY_FUNDING] 🔥 mint={_mint[:8]} band=hot → enqueuing early extraction for creator={_creator[:8]}",
                                flush=True,
                            )
                            await self._enqueue_creator_funding_job(
                                _creator,
                                mint=_mint,
                                migration_timestamp=None,
                                create_tx_signature=_sig,
                                delay_seconds=0,
                                source="early_hot_band",
                            )

                        asyncio.create_task(_enqueue_with_creator_resolve())
                        # Risk scoring runs after extraction completes in the queue processor
                        # (see score_creator_now call in _process_funding_queue) — not here,
                        # since funders aren't written yet at HOT band detection time.
            except Exception as e:
                log_print(f"[PREMIG_SIGNAL] ⚠ Failed to persist signal for {mint[:16]}...: {e}", flush=True)

        # Add to curve watcher when token crosses the hot threshold
        if bonding_curve_pda and signal.get("is_about_to_migrate"):
            await self.watch_bonding_curve(bonding_curve_pda)

    async def handle_pumpfun_trade(self, signature: str, logs: List[str]) -> None:
        """Best-effort no-RPC tracking of Pump.fun buy momentum from websocket logs."""
        mint, source, candidates, reason, tx_data = await self._resolve_bonding_curve_mint_for_trade(signature, logs)
        premig_log(f"[PARSE_ATTEMPT] sig={signature[:16]} mint={mint} logs={json.dumps(logs)[:400]}")
        if not mint:
            self._resolver_unresolved_count += 1
            log_print(
                f"[BUY_UNRESOLVED] sig={signature[:16]} reason={reason or 'no_viable_candidate'} top_candidates={candidates}",
                flush=True,
            )
            premig_log(f"[NO_BUY_MATCH] sig={signature[:16]} reason=mint_unresolved")
            self._debug_pumpfun_trade_skip("mint_unresolved", logs)
            return
        self._resolver_resolved_count += 1
        log_print(
            f"[BUY_RESOLVED] sig={signature[:16]} mint={mint} source={source or 'unknown'} candidates={candidates}",
            flush=True,
        )
        buyer = self._extract_buyer_from_logs(logs)
        sol_amount = self._extract_sol_amount_from_logs(logs)
        if buyer is None or sol_amount is None:
            log_print(f"[PARTIAL_ENRICH_ATTEMPT] sig={signature[:16]} mint={mint}", flush=True)
            if tx_data is None:
                tx_data = await self._get_trade_transaction_context(signature)
            buyer, sol_amount, partial_mode, enrich_reason = self._recover_partial_trade_details_from_tx(
                tx_data,
                mint=mint,
                buyer=buyer,
                sol_amount=sol_amount,
            )
            if partial_mode != "count_only":
                log_print(
                    f"[PARTIAL_ENRICH_SUCCESS] sig={signature[:16]} mint={mint} mode={partial_mode}",
                    flush=True,
                )
                log_print(
                    f"[BUY_ENRICHED] sig={signature[:16]} mint={mint} "
                    f"buyer={'yes' if buyer else 'no'} sol={'yes' if sol_amount is not None else 'no'} mode={partial_mode}",
                    flush=True,
                )
            else:
                log_print(
                    f"[PARTIAL_ENRICH_FAIL] sig={signature[:16]} mint={mint} reason={enrich_reason or 'no_confident_buyer_or_sol'}",
                    flush=True,
                )
            log_print(
                f"[BUY_PARTIAL] sig={signature[:16]} mint={mint} "
                f"buyer={'yes' if buyer else 'no'} sol={'yes' if sol_amount is not None else 'no'} mode={partial_mode}",
                flush=True,
            )
            self._debug_pumpfun_trade_skip(
                f"partial_trade_details buyer={'yes' if buyer else 'no'} sol={'yes' if sol_amount is not None else 'no'} mode={partial_mode}",
                logs,
            )
        if buyer is not None and sol_amount is not None:
            premig_log(f"[BUY_DETECTED] mint={mint} sol={sol_amount} buyer={buyer}")
        elif buyer is not None or sol_amount is not None:
            premig_log(
                f"[BUY_PARTIAL] mint={mint} buyer={'yes' if buyer else 'no'} sol={'yes' if sol_amount is not None else 'no'}"
            )
        else:
            premig_log(f"[BUY_PARTIAL] mint={mint} buyer=no sol=no")
        now = time.time()
        self._record_flow_event(mint, observed_at=now, buyer=buyer, sol_amount=sol_amount, kind="buy")
        market_cap_hint = self._last_market_cap_by_mint.get(mint, 0.0)
        await self._persist_pre_migration_signal(mint, market_cap_hint, int(now), source_hint="flow")

    async def _refresh_pre_migration_signals_periodic(self) -> None:
        """Keep Pump.fun watchlist rows fresh from DB activity without adding RPC calls."""
        await asyncio.sleep(5)
        while True:
            try:
                candidates: List[Tuple[str, float]] = []
                async with self.db_lock:
                    conn = db_connect(DB_PATH, timeout=15)
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT mint,
                               CASE
                                 WHEN COALESCE(market_cap_current, 0) > 5000000 THEN 0
                                 ELSE COALESCE(market_cap_current, 0)
                               END AS market_cap_current
                        FROM token_analysis
                        WHERE mint IS NOT NULL
                          AND source_platform = 'pumpfun'
                          AND COALESCE(lifecycle_stage, 'bonding_curve') = 'bonding_curve'
                          AND COALESCE(lifecycle_stage, '') != 'migrated'
                          AND NULLIF(TRIM(COALESCE(pool_address, pumpswap_pool_address, dex, '')), '') IS NULL
                        ORDER BY
                          COALESCE(is_about_to_migrate, 0) DESC,
                          COALESCE(migration_progress_pct, 0) DESC,
                          COALESCE(market_cap_current, 0) DESC,
                          mint ASC
                        LIMIT 75
                        """
                    )
                    rows = cursor.fetchall()
                    conn.close()
                    candidates = [(str(row["mint"]), float(row["market_cap_current"] or 0.0)) for row in rows]

                if candidates:
                    index_rows = self._refresh_bonding_curve_index()
                    refreshed = 0
                    now_ts = int(time.time())
                    flow_count = 0
                    fallback_count = 0
                    for mint, current_market_cap in candidates:
                        await self._persist_pre_migration_signal(mint, current_market_cap, now_ts)
                        refreshed += 1
                        await asyncio.sleep(0)  # yield event loop between each persist
                        # Count flow vs fallback based on cached flow windows
                        if self._flow_windows_by_mint.get(mint):
                            flow_count += 1
                        else:
                            fallback_count += 1
                    premig_log(
                        f"[SUMMARY] active_mints={len(self._flow_windows_by_mint)} "
                        f"index_rows={index_rows} "
                        f"sweep_candidates={refreshed} "
                        f"flow_active={flow_count} fallback_active={fallback_count}"
                    )
                    self._log_resolver_summary()
                    log_print(f"[PREMIG_SWEEP] refreshed={refreshed} index_rows={index_rows}", flush=True)
            except Exception as e:
                log_print(f"[PREMIG_SWEEP] ⚠ refresh failed: {e}", flush=True)
            await asyncio.sleep(15)

    def _log_fl(self, msg: str):
        """Override fast-lane logging to use log_print for consistent output."""
        log_print(msg, flush=True)

    def _filter_failed(self, mint: str, candidates: List[str]) -> List[str]:
        """Remove candidates that previously failed registration for this mint."""
        failed = self._failed_registration.get(mint, set())
        if not failed:
            return candidates
        filtered = [c for c in candidates if c not in failed]
        if len(filtered) < len(candidates):
            removed = len(candidates) - len(filtered)
            log_print(
                f"[FAST_LANE] 🚫 Filtered {removed} previously-failed candidate(s) for {mint[:16]}...",
                flush=True,
            )
        return filtered

    # ===== PHASE 2: Background Job Queue Processing =====

    async def _process_background_queue(self):
        """Process background jobs after critical window expires."""
        while True:
            try:
                # Check if any critical windows have expired
                now = time.time()
                expired_mints = [
                    mint for mint, expiry in self.critical_window_tasks.items()
                    if now >= expiry
                ]

                # Remove expired windows and allow their jobs to process
                for mint in expired_mints:
                    del self.critical_window_tasks[mint]

                # Process jobs whose own mint's critical window has expired.
                # A job is eligible if its mint has no active window, or it has no mint.
                jobs_processed = 0
                requeue = []
                try:
                    while not self.background_job_queue.empty():
                        job_item = self.background_job_queue.get_nowait()
                        mint = job_item.get('mint', '?')
                        mint_still_active = (
                            mint and mint != '?'
                            and mint in self.critical_window_tasks
                            and time.time() < self.critical_window_tasks[mint]
                        )
                        if mint_still_active:
                            requeue.append(job_item)
                            self.background_job_queue.task_done()
                            continue
                        try:
                            log_print(f"[BACKGROUND] 🚀 Executing queued job (mint={mint[:8]}...)", flush=True)
                            await job_item['coro']
                            jobs_processed += 1
                        except Exception as e:
                            logger.error(f"[BACKGROUND] ❌ Job failed (mint={mint[:8]}...): {e}")
                        self.background_job_queue.task_done()
                except asyncio.QueueEmpty:
                    pass
                # Re-enqueue jobs that were still in their critical window
                for job_item in requeue:
                    await self.background_job_queue.put(job_item)

                if jobs_processed > 0:
                    log_print(f"[BACKGROUND] ✅ Processed {jobs_processed} background jobs after critical windows expired", flush=True)

                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Background queue processor error: {e}")
                await asyncio.sleep(1)

    async def queue_background_job(self, coro, mint: str = None, priority: str = "normal"):
        """Queue a background job for deferred execution."""
        await self.background_job_queue.put({
            'coro': coro,
            'mint': mint,
            'priority': priority,
            'queued_at': time.time()
        })

    def start_critical_window(self, mint: str):
        """Mark when critical discovery window starts for a token."""
        self.critical_window_tasks[mint] = time.time() + self.DISCOVERY_CRITICAL_WINDOW_SECONDS

    def any_token_in_critical_window(self) -> bool:
        """Check if ANY token is currently in critical discovery window."""
        now = time.time()
        return any(
            now < expiry
            for expiry in self.critical_window_tasks.values()
        )

    def _correlation_id(self, mint: str, attempt: int = None, tier: str = None, elapsed: float = None) -> str:
        """Generate correlation ID for log tracing: mint|attempt|tier|elapsed."""
        parts = [mint[:8] if mint else "?"]
        if attempt is not None:
            parts.append(f"A{attempt}")
        if tier:
            parts.append(f"T{tier[:1]}")  # T=TX_ONLY, L=Light, F=Full
        if elapsed is not None:
            parts.append(f"{elapsed:.1f}s")
        return "|".join(parts)

    def assert_not_in_critical_window(self, job_type: str, mint: str = None) -> bool:
        """
        Assert that a non-discovery job is NOT running during critical window.

        Returns True if OK to run (no active critical window).
        Returns False if BLOCKED (active critical window detected).

        Logs loudly if violation detected.
        """
        if not self.critical_window_tasks:
            # No active critical windows - OK to proceed
            return True

        # Critical window is ACTIVE - this job must wait
        active_mints = list(self.critical_window_tasks.keys())
        log_print(
            f"[CRITICAL_PATH_LEAK] ❌ BLOCKED: {job_type} tried to run during critical_window=ACTIVE for mints={active_mints[:3]}",
            flush=True
        )
        log_print(
            f"[CRITICAL_PATH_ASSERTION] Job={job_type} mint={mint} MUST be queued, not spawned, during critical window",
            flush=True
        )
        return False

    def is_in_critical_window(self, mint: str) -> bool:
        """Check if mint is still in critical discovery window."""
        if mint not in self.critical_window_tasks:
            return False
        return time.time() < self.critical_window_tasks[mint]

    # ===== PHASE 2: RPC Isolation =====

    async def call_discovery_rpc(self, method: str, params: list, timeout: float = 5.0):
        """RPC call with discovery quota priority."""
        async with self.discovery_rpc_semaphore:
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params
                }
                result = await self.__internal_rpc(payload, timeout=timeout, priority="critical")
                return result
            except Exception as e:
                logger.debug(f"Discovery RPC error ({method}): {e}")
                return None

    async def call_background_rpc(self, method: str, params: list, timeout: float = 10.0):
        """RPC call with background quota (throttled during critical window)."""
        async with self.background_rpc_semaphore:
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params
                }
                result = await self.__internal_rpc(payload, timeout=timeout, priority="background")
                return result
            except Exception as e:
                logger.debug(f"Background RPC error ({method}): {e}")
                return None

    async def _write_resolution_telemetry(self, mint: str, resolve_source: str, pool_address: str = None, retry_count: int = 0):
        """Write token resolution telemetry to database."""
        try:
            import time
            from src.core.vault_discovery_persistence import record_vault_discovery_result

            conn = db_connect(DB_PATH, timeout=15)
            cursor = conn.cursor()
            now = int(time.time())
            times = self.token_discovery_times.get(mint, {})
            detected_at = times.get("detected") or now
            resolved_at = times.get("resolved") or now

            # CRITICAL: Use pool_registered_at (not resolved_at) for discovery time
            # resolved_at includes post-registration delays (background jobs, state transitions)
            # pool_registered_at is when discovery actually succeeded
            pool_registered_at = times.get("pool_registered_at") or resolved_at
            resolve_seconds = pool_registered_at - detected_at if detected_at else 0.0

            cursor.execute("""
                INSERT OR REPLACE INTO token_resolution_telemetry
                (mint, detected_at, resolved_at, resolve_seconds, resolve_source, retry_count, pool_address, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (mint, int(detected_at), int(resolved_at), resolve_seconds, resolve_source, retry_count, pool_address, now, now))
            conn.commit()
            conn.close()

            # IMPORTANT: Also persist vault discovery metadata to token_pool_accounts
            # This ensures the Vaults page shows real discovery data, not defaults
            if pool_address:
                # Attempt is retry_count + 1 (attempts are 1-indexed)
                attempts = retry_count + 1
                
                success = record_vault_discovery_result(
                    db_path=DB_PATH,
                    mint=mint,
                    base_account=pool_address,
                    strategy=resolve_source,
                    attempts=attempts,
                    elapsed_secs=float(resolve_seconds),
                    pool_address=pool_address,
                )
                
                if success:
                    log_print(
                        f"[VAULT_PERSISTENCE] ✅ Persisted discovery: "
                        f"strategy={resolve_source} attempts={attempts} elapsed={resolve_seconds}s",
                        flush=True
                    )
                else:
                    log_print(
                        f"[VAULT_PERSISTENCE] ⚠️  Failed to persist discovery data "
                        f"for {mint[:16]}... / {pool_address[:16]}...",
                        flush=True
                    )

        except Exception as e:
            log_print(f"[TELEMETRY] ⚠️  Failed to write telemetry for {mint}: {e}", flush=True)

        # Fire-and-forget symbol fetch for every resolved token (covers all resolution paths)
        _spawn_symbol_fetch(mint, DB_PATH)

    async def __internal_rpc(self, payload: dict, timeout: int = 10, priority: str = "critical") -> Optional[dict]:
        """
        Post to RPC with automatic failover chain.

        Tries: Primary QuickNode -> Secondary QuickNode -> Helius -> Public Solana
        Returns: JSON response data or None if all fail

        Args:
            priority: "critical" (discovery RPC) or "background" (deferred work RPC)
        """
        try:
            rpc_method = payload.get("method", "unknown")
            start_time = time.time()
            last_status = None
            last_error = None
            retry_count = 0
            optimization_layer = "critical_discovery" if priority == "critical" else "background_deferred"

            async with aiohttp.ClientSession() as session:
                for i, rpc_url in enumerate(RPC_URLS):
                    try:
                        retry_count = i  # Track which RPC endpoint we tried
                        async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                            latency_ms = (time.time() - start_time) * 1000

                            if resp.status == 200:
                                # Success - record metrics with priority tag
                                record_request(
                                    section="listener",
                                    provider="helius_rpc" if "helius" in rpc_url else "quicknode_rpc" if "quiknode" in rpc_url else "solana_rpc",
                                    method=rpc_method,
                                    status_code=200,
                                    latency_ms=latency_ms,
                                    mode="realtime",
                                    retries=retry_count,
                                    source_file="pumpfun_curve_listener",
                                    optimization_layer=optimization_layer,
                                    error=None,
                                )
                                return await resp.json()
                            elif resp.status == 429:
                                # Rate limited, record and try next in chain
                                last_status = 429
                                record_request(
                                    section="listener",
                                    provider="helius_rpc" if "helius" in rpc_url else "quicknode_rpc" if "quiknode" in rpc_url else "solana_rpc",
                                    method=rpc_method,
                                    status_code=429,
                                    latency_ms=latency_ms,
                                    mode="realtime",
                                    retries=retry_count,
                                    source_file="pumpfun_curve_listener",
                                    optimization_layer=optimization_layer,
                                    error="Rate limited",
                                )
                                if i < len(RPC_URLS) - 1:
                                    continue
                            else:
                                # Other error, record and try next
                                last_status = resp.status
                                record_request(
                                    section="listener",
                                    provider="helius_rpc" if "helius" in rpc_url else "quicknode_rpc" if "quiknode" in rpc_url else "solana_rpc",
                                    method=rpc_method,
                                    status_code=resp.status,
                                    latency_ms=latency_ms,
                                    mode="realtime",
                                    retries=retry_count,
                                    source_file="pumpfun_curve_listener",
                                    optimization_layer=optimization_layer,
                                    error=f"HTTP {resp.status}",
                                )
                                if i < len(RPC_URLS) - 1:
                                    continue
                    except asyncio.TimeoutError:
                        latency_ms = (time.time() - start_time) * 1000
                        last_error = "Timeout"
                        record_request(
                            section="listener",
                            provider="helius_rpc" if "helius" in rpc_url else "quicknode_rpc" if "quiknode" in rpc_url else "solana_rpc",
                            method=rpc_method,
                            status_code=0,
                            latency_ms=latency_ms,
                            mode="realtime",
                            retries=retry_count,
                            source_file="pumpfun_curve_listener",
                            optimization_layer=optimization_layer,
                            error="Timeout",
                        )
                        if i < len(RPC_URLS) - 1:
                            continue
                    except Exception as e:
                        latency_ms = (time.time() - start_time) * 1000
                        last_error = str(e)
                        record_request(
                            section="listener",
                            provider="helius_rpc" if "helius" in rpc_url else "quicknode_rpc" if "quiknode" in rpc_url else "solana_rpc",
                            method=rpc_method,
                            status_code=0,
                            latency_ms=latency_ms,
                            mode="realtime",
                            retries=retry_count,
                            source_file="pumpfun_curve_listener",
                            optimization_layer=optimization_layer,
                            error=last_error,
                        )
                        if i < len(RPC_URLS) - 1:
                            continue

                # All RPC endpoints failed - record final failure
                latency_ms = (time.time() - start_time) * 1000
                record_request(
                    section="listener",
                    provider="solana_rpc",
                    method=rpc_method,
                    status_code=last_status or 0,
                    latency_ms=latency_ms,
                    mode="realtime",
                    retries=retry_count,
                    source_file="pumpfun_curve_listener",
                    optimization_layer=optimization_layer,
                    error=last_error or "All endpoints failed",
                )
                return None
        except Exception as e:
            log_print(f"[RPC_ERROR] {e}", flush=True)
            # Record the outer exception too
            latency_ms = (time.time() - start_time) * 1000 if 'start_time' in locals() else 0
            record_request(
                section="listener",
                provider="solana_rpc",
                method=payload.get("method", "unknown"),
                status_code=0,
                latency_ms=latency_ms,
                mode="realtime",
                retries=0,
                source_file="pumpfun_curve_listener",
                optimization_layer=optimization_layer if 'optimization_layer' in locals() else "unknown",
                error=str(e),
            )
            return None

    async def _get_transaction_cached(self, signature: str, timeout: int = 15) -> Optional[Dict]:
        """
        Fetch transaction with TTL cache + singleflight deduplication.
        Includes retry/backoff for indexing delays.

        Returns tx_data dict from "result" field, or None if not found.
        """
        import time
        current_time = time.time()

        # === Check cache hit ===
        if signature in self.tx_cache:
            cached_data, cached_time = self.tx_cache[signature]
            age = current_time - cached_time
            if age < self.tx_cache_ttl_seconds:
                self.tx_cache_stats['hit'] += 1
                log_print(f"[TX_CACHE] 💾 HIT: {signature[:16]}... (age: {age:.1f}s)", flush=True)
                return cached_data
            else:
                # Expired, remove from cache
                del self.tx_cache[signature]
                # Also remove pending retry task if exists
                if signature in self.tx_cache_pending_retries:
                    del self.tx_cache_pending_retries[signature]

        # === Check if already in-flight (singleflight pattern) ===
        if signature in self.tx_inflight_locks:
            # Another coroutine is already fetching this
            self.tx_cache_stats['wait'] += 1
            lock = self.tx_inflight_locks[signature]
            await lock.acquire()
            lock.release()

            # After lock released, tx should be in cache
            if signature in self.tx_cache:
                cached_data, _ = self.tx_cache[signature]
                log_print(f"[TX_CACHE] ⏳ WAIT: {signature[:16]}... (shared fetch completed)", flush=True)
                return cached_data
            return None

        # === Cache miss: fetch with retry/backoff ===
        self.tx_cache_stats['miss'] += 1

        # Create lock for this signature (singleflight)
        lock = asyncio.Lock()
        self.tx_inflight_locks[signature] = lock
        await lock.acquire()

        try:
            log_print(f"[TX_CACHE] 🌐 MISS: fetching {signature[:16]}...", flush=True)

            # Retry with backoff for indexing delays
            retry_delays = [1, 2, 4, 6, 10, 15, 20, 30]
            total_attempts = len(retry_delays) + 1

            for attempt in range(total_attempts):
                if attempt > 0:
                    delay = retry_delays[attempt - 1]
                    log_print(f"[TX_CACHE] ⏳ Retry {attempt + 1}/{total_attempts} after {delay}s for {signature[:16]}...", flush=True)
                    await asyncio.sleep(delay)

                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [
                        signature,
                        {
                            "encoding": "jsonParsed",
                            "commitment": "confirmed",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                }

                # Use discovery RPC tier with semaphore protection
                result = await self.call_discovery_rpc(
                    "getTransaction",
                    [signature, {
                        "encoding": "jsonParsed",
                        "commitment": "confirmed",
                        "maxSupportedTransactionVersion": 0,
                    }],
                    timeout=timeout
                )
                tx_data = result

                # Check if we got a real result
                if tx_data and "result" in tx_data and tx_data["result"]:
                    result = tx_data["result"]
                    # Cache it
                    self.tx_cache[signature] = (result, time.time())
                    log_print(f"[TX_CACHE] 💾 CACHED: {signature[:16]}... ({len(str(result))} bytes)", flush=True)
                    return result

                # Log what we got (for debugging indexing delays)
                if tx_data is None:
                    log_print(f"[TX_CACHE] ⚠ Attempt {attempt + 1}/{total_attempts}: _post_rpc_with_fallback returned None", flush=True)
                elif "error" in tx_data:
                    log_print(f"[TX_CACHE] ⚠ Attempt {attempt + 1}/{total_attempts}: RPC error: {tx_data['error']}", flush=True)
                elif "result" not in tx_data:
                    log_print(f"[TX_CACHE] ⚠ Attempt {attempt + 1}/{total_attempts}: No 'result' field in response", flush=True)
                elif tx_data["result"] is None:
                    log_print(f"[TX_CACHE] ⚠ Attempt {attempt + 1}/{total_attempts}: result is None (indexing delay)", flush=True)
                else:
                    log_print(f"[TX_CACHE] ⚠ Attempt {attempt + 1}/{total_attempts}: result is empty/falsy", flush=True)

            log_print(f"[TX_CACHE] ❌ All {total_attempts} attempts exhausted for {signature[:16]}...", flush=True)
            return None

        except asyncio.TimeoutError:
            log_print(f"[TX_CACHE] ⏱️  Timeout fetching {signature[:16]}...", flush=True)
            return None

        except Exception as e:
            log_print(f"[TX_CACHE] ⚠ Error fetching {signature[:16]}...: {e}", flush=True)
            return None

        finally:
            # Release lock for other waiters
            lock.release()
            self.tx_inflight_locks.pop(signature, None)

    async def _cleanup_tx_cache_periodic(self):
        """
        Periodically clean up expired entries in TX cache to prevent memory leak.
        Also enforces max size cap by evicting oldest entries.
        Runs every 60 seconds.
        """
        import time
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()

                # Remove expired entries
                expired = [
                    sig for sig, (_, cached_time) in self.tx_cache.items()
                    if now - cached_time > self.tx_cache_ttl_seconds
                ]
                if expired:
                    for sig in expired:
                        del self.tx_cache[sig]
                        self.tx_cache_pending_retries.pop(sig, None)
                        self.tx_inflight_locks.pop(sig, None)
                    log_print(f"[TX_CACHE_CLEANUP] Removed {len(expired)} expired entries (cache size: {len(self.tx_cache)})", flush=True)

                # Enforce max size by evicting oldest entries
                if len(self.tx_cache) > self.tx_cache_max_size:
                    to_evict = len(self.tx_cache) - self.tx_cache_max_size
                    # Sort by timestamp and evict oldest
                    oldest = sorted(self.tx_cache.items(), key=lambda x: x[1][1])[:to_evict]
                    for sig, _ in oldest:
                        del self.tx_cache[sig]
                        self.tx_cache_pending_retries.pop(sig, None)
                        self.tx_inflight_locks.pop(sig, None)
                    log_print(f"[TX_CACHE_CLEANUP] Evicted {to_evict} oldest entries (cache size: {len(self.tx_cache)})", flush=True)

            except Exception as e:
                log_print(f"[TX_CACHE_CLEANUP] Error during cleanup: {e}", flush=True)

    async def _db_maintenance_periodic(self):
        """
        Nightly DB maintenance: purge expired RPC cache rows, prune rpc_metrics and
        helius_usage_snapshots, then checkpoint the WAL.  Runs at startup if >12h since
        last run, then again every 24h.  Results are stored in db_maintenance_log so the
        performance dashboard can surface them.
        """
        import time as _time

        INTERVAL = 6 * 3600
        MIN_GAP  = 4 * 3600

        def _run():
            conn = db_connect(DB_PATH, timeout=60)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=60000")
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS db_maintenance_log (
                        id           INTEGER PRIMARY KEY AUTOINCREMENT,
                        ran_at       INTEGER NOT NULL,
                        rpc_cache_deleted   INTEGER,
                        rpc_metrics_deleted INTEGER,
                        helius_snapshots_deleted INTEGER,
                        wal_checkpoint_pages INTEGER,
                        duration_ms  INTEGER
                    )
                """)
                conn.commit()

                # Check last run time
                row = conn.execute(
                    "SELECT ran_at FROM db_maintenance_log ORDER BY ran_at DESC LIMIT 1"
                ).fetchone()
                if row and (_time.time() - row[0]) < MIN_GAP:
                    return None  # Too soon

                t0 = _time.time()

                # 1. Purge expired RPC cache rows
                cur = conn.execute(
                    "DELETE FROM rpc_response_cache WHERE cached_at + ttl_seconds <= ?",
                    (int(_time.time()),)
                )
                rpc_cache_deleted = cur.rowcount
                conn.commit()

                # 2. Prune rpc_metrics — keep 7 days
                cur = conn.execute(
                    "DELETE FROM rpc_metrics WHERE timestamp < ?",
                    (_time.time() - 7 * 86400,)
                )
                rpc_metrics_deleted = cur.rowcount
                conn.commit()

                # 3. Prune helius_usage_snapshots — keep 48h
                cur = conn.execute(
                    "DELETE FROM helius_usage_snapshots WHERE captured_at < datetime('now', '-2 days')"
                )
                helius_deleted = cur.rowcount
                conn.commit()

                # 4. WAL checkpoint — RESTART resets the write position without
                # requiring exclusive access (TRUNCATE would block on open readers)
                ckpt = conn.execute("PRAGMA wal_checkpoint(RESTART)").fetchone()
                wal_pages = ckpt[2] if ckpt else 0

                # 5. VACUUM rpc_response_cache (reclaim freed pages)
                conn.execute("VACUUM")

                duration_ms = int((_time.time() - t0) * 1000)

                conn.execute(
                    """INSERT INTO db_maintenance_log
                       (ran_at, rpc_cache_deleted, rpc_metrics_deleted,
                        helius_snapshots_deleted, wal_checkpoint_pages, duration_ms)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (int(_time.time()), rpc_cache_deleted, rpc_metrics_deleted,
                     helius_deleted, wal_pages, duration_ms)
                )
                conn.commit()
                return {
                    'rpc_cache_deleted': rpc_cache_deleted,
                    'rpc_metrics_deleted': rpc_metrics_deleted,
                    'helius_snapshots_deleted': helius_deleted,
                    'wal_pages': wal_pages,
                    'duration_ms': duration_ms,
                }
            finally:
                conn.close()

        # Run at startup (respects MIN_GAP), then every 24h
        while True:
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, _run)
                if result:
                    log_print(
                        f"[DB_MAINTENANCE] ✅ rpc_cache={result['rpc_cache_deleted']} deleted, "
                        f"rpc_metrics={result['rpc_metrics_deleted']} deleted, "
                        f"helius_snapshots={result['helius_snapshots_deleted']} deleted, "
                        f"wal_pages={result['wal_pages']}, took {result['duration_ms']}ms",
                        flush=True
                    )
                else:
                    log_print("[DB_MAINTENANCE] Skipped — ran recently", flush=True)
            except Exception as e:
                log_print(f"[DB_MAINTENANCE] Error: {e}", flush=True)
            await asyncio.sleep(INTERVAL)

    async def _flush_portal_vsol_periodic(self):
        """Write _portal_vsol to portal_vsol.json every 5s for Flask to read."""
        import json as _json
        out_path = os.path.join(os.path.dirname(DB_PATH), '..', 'portal_vsol.json')
        while True:
            try:
                await asyncio.sleep(5)
                # Prune entries older than 10 minutes
                cutoff = int(time.time()) - 600
                pruned = {m: s for m, s in self._portal_vsol.items() if s.get("ts", 0) >= cutoff}
                self._portal_vsol = pruned
                with open(out_path, 'w') as _f:
                    _json.dump(pruned, _f)
            except Exception:
                pass

    # --- Database ---
    def _ensure_db(self):
        conn = db_connect(DB_PATH, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        cursor = conn.cursor()

        # Post-migration token analysis with live on-chain price and market cap tracking
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_analysis (
                mint TEXT PRIMARY KEY,
                analyzed_at REAL,
                total_txs INTEGER,
                total_events INTEGER,
                events_parsed INTEGER,
                mint_concentration REAL,
                unique_minters_ratio REAL,
                sell_suppression_ratio REAL,
                mint_velocity_sec REAL,
                buy_size_variance REAL,
                sell_volume_concentration REAL,
                creator_activity_ratio REAL,
                post_migration_mint_concentration REAL,
                post_migration_unique_minters_ratio REAL,
                post_migration_sell_suppression_ratio REAL,
                post_migration_mint_velocity_sec REAL,
                post_migration_buy_size_variance REAL,
                post_migration_sell_volume_concentration REAL,
                post_migration_creator_activity_ratio REAL,
                post_migration_coverage REAL,
                rug_probability REAL,
                risk_level TEXT,
                migration_tx TEXT,
                price_current REAL,
                price_highest REAL,
                market_cap_current REAL,
                market_cap_highest REAL,
                market_cap_highest_at TIMESTAMP,
                price_updated_at TIMESTAMP,
                price_source TEXT,
                pool_address TEXT,
                creator_address TEXT,
                creator_reputation TEXT,
                earliest_tx_creator TEXT,
                creator_is_blocked INTEGER DEFAULT 0,
                network_risk INTEGER DEFAULT 0,
                connected_malicious_count INTEGER,
                is_about_to_migrate BOOLEAN DEFAULT 0,
                migration_progress_pct REAL,
                migration_band TEXT,
                migration_signal_updated_at INTEGER,
                first_pre_migration_signal_at INTEGER,
                lifecycle_stage TEXT DEFAULT 'migration_pending',
                migrated_at INTEGER,
                dex TEXT,
                pumpswap_pool_address TEXT,
                source_platform TEXT,
                is_new INTEGER DEFAULT 0,
                rug_indicator TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Creator network tracking - stores SOL transfer destinations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_sol_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                destination_address TEXT NOT NULL,
                total_amount REAL DEFAULT 0,
                transfer_count INTEGER DEFAULT 0,
                first_detected_at TIMESTAMP,
                last_detected_at TIMESTAMP,
                is_pool_address INTEGER DEFAULT 0,
                UNIQUE(creator_address, destination_address)
            )
        """)

        # Creator networks - identifies groups of creators sharing destinations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_networks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                connected_creators TEXT NOT NULL,  -- JSON array of connected creator addresses
                shared_destinations TEXT NOT NULL,  -- JSON array of shared destination addresses
                network_size INTEGER,  -- Number of creators in network
                network_risk_level TEXT,  -- CRITICAL, HIGH, MEDIUM, LOW based on connected ruggers
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(creator_address)
            )
        """)

        # Creator funders - tracks funding sources for each creator
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_funders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                funder_address TEXT NOT NULL,
                amount_sol REAL DEFAULT 0,
                transfer_count INTEGER DEFAULT 0,
                first_detected_at TIMESTAMP,
                last_detected_at TIMESTAMP,
                fully_analyzed INTEGER DEFAULT 0,
                is_cex INTEGER DEFAULT 0,
                cex_name TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(creator_address, funder_address)
            )
        """)

        # Creator funding graph - relationship graph for network analysis
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_funding_graph (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_address TEXT NOT NULL,
                funding_source_address TEXT NOT NULL,
                total_amount_sol REAL DEFAULT 0,
                relationship_type TEXT,  -- direct, indirect, etc
                first_detected_at TIMESTAMP,
                last_detected_at TIMESTAMP,
                is_suspicious INTEGER DEFAULT 0,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(creator_address, funding_source_address)
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pumpfun_migration_verification (
                mint TEXT PRIMARY KEY,
                migrated_at INTEGER,
                migration_tx TEXT,
                dex TEXT,
                pumpswap_pool_address TEXT,
                pre_is_about_to_migrate INTEGER DEFAULT 0,
                pre_migration_band TEXT,
                pre_migration_progress_pct REAL,
                pre_migration_signal_updated_at INTEGER,
                pre_market_cap_current REAL,
                pre_market_cap_updated_at INTEGER,
                pre_buys_10s INTEGER DEFAULT 0,
                pre_unique_30s INTEGER DEFAULT 0,
                pre_sol_15s REAL DEFAULT 0,
                pre_inflow_accel REAL DEFAULT 0,
                pre_signal_score INTEGER DEFAULT 0,
                pre_migration_signal_source TEXT,
                predicted_by_flow INTEGER DEFAULT 0,
                predicted_by_market_cap INTEGER DEFAULT 0,
                predicted_by_explicit_signal INTEGER DEFAULT 0,
                was_about_to_migrate_at_migration INTEGER DEFAULT 0,
                was_hot_or_warm_before_migration INTEGER DEFAULT 0,
                signal_age_seconds INTEGER,
                signal_was_fresh INTEGER DEFAULT 0,
                final_verdict TEXT,
                created_at INTEGER
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS creator_funding_queue (
                creator_address TEXT NOT NULL,
                mint TEXT NOT NULL,
                migration_timestamp TEXT,
                create_tx_signature TEXT,
                status TEXT DEFAULT 'pending',
                source TEXT,
                next_attempt_at INTEGER DEFAULT 0,
                locked_until INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                last_error TEXT,
                created_at INTEGER DEFAULT (strftime('%s','now')),
                updated_at INTEGER DEFAULT (strftime('%s','now')),
                PRIMARY KEY (creator_address, mint)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_funding_queue_status ON creator_funding_queue(status, next_attempt_at)")
        try:
            cursor.execute("PRAGMA table_info(creator_funding_queue)")
            cfq_cols = [col[1] for col in cursor.fetchall()]
            if "source" not in cfq_cols:
                cursor.execute("ALTER TABLE creator_funding_queue ADD COLUMN source TEXT")
                log_print("[DB] ✅ Added source column to creator_funding_queue", flush=True)
            if "funding_enqueued_at" not in cfq_cols:
                cursor.execute("ALTER TABLE creator_funding_queue ADD COLUMN funding_enqueued_at INTEGER")
                log_print("[DB] ✅ Added funding_enqueued_at column to creator_funding_queue", flush=True)
            if "funding_extracted_at" not in cfq_cols:
                cursor.execute("ALTER TABLE creator_funding_queue ADD COLUMN funding_extracted_at INTEGER")
                log_print("[DB] ✅ Added funding_extracted_at column to creator_funding_queue", flush=True)
            if "curve_completed_slot" not in cfq_cols:
                cursor.execute("ALTER TABLE creator_funding_queue ADD COLUMN curve_completed_slot INTEGER")
                log_print("[DB] ✅ Added curve_completed_slot column to creator_funding_queue", flush=True)
            if "enqueued_slot" not in cfq_cols:
                cursor.execute("ALTER TABLE creator_funding_queue ADD COLUMN enqueued_slot INTEGER")
                log_print("[DB] ✅ Added enqueued_slot column to creator_funding_queue", flush=True)
            if "job_priority" not in cfq_cols:
                cursor.execute("ALTER TABLE creator_funding_queue ADD COLUMN job_priority INTEGER DEFAULT 0")
                log_print("[DB] ✅ Added job_priority column to creator_funding_queue", flush=True)
            if "priority_reason" not in cfq_cols:
                cursor.execute("ALTER TABLE creator_funding_queue ADD COLUMN priority_reason TEXT")
                log_print("[DB] ✅ Added priority_reason column to creator_funding_queue", flush=True)
        except Exception:
            pass

        # Add columns if they don't exist (for backward compatibility)
        try:
            cursor.execute("PRAGMA table_info(token_analysis)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if "creator_is_blocked" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN creator_is_blocked INTEGER DEFAULT 0")
                log_print("[DB] ✅ Added creator_is_blocked column to token_analysis", flush=True)
            
            if "network_risk" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN network_risk INTEGER DEFAULT 0")
                log_print("[DB] ✅ Added network_risk column to token_analysis", flush=True)
            
            if "connected_malicious_count" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN connected_malicious_count INTEGER")
                log_print("[DB] ✅ Added connected_malicious_count column to token_analysis", flush=True)

            if "is_about_to_migrate" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN is_about_to_migrate BOOLEAN DEFAULT 0")
                log_print("[DB] ✅ Added is_about_to_migrate column to token_analysis", flush=True)

            if "migration_progress_pct" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN migration_progress_pct REAL")
                log_print("[DB] ✅ Added migration_progress_pct column to token_analysis", flush=True)

            if "migration_band" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN migration_band TEXT")
                log_print("[DB] ✅ Added migration_band column to token_analysis", flush=True)

            if "migration_signal_updated_at" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN migration_signal_updated_at INTEGER")
                log_print("[DB] ✅ Added migration_signal_updated_at column to token_analysis", flush=True)

            if "first_pre_migration_signal_at" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN first_pre_migration_signal_at INTEGER")
                log_print("[DB] ✅ Added first_pre_migration_signal_at column to token_analysis", flush=True)

            if "migration_signal_source" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN migration_signal_source TEXT")
                log_print("[DB] ✅ Added migration_signal_source column to token_analysis", flush=True)

            if "lifecycle_stage" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN lifecycle_stage TEXT DEFAULT 'migration_pending'")
                log_print("[DB] ✅ Added lifecycle_stage column to token_analysis", flush=True)

            if "migrated_at" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN migrated_at INTEGER")
                log_print("[DB] ✅ Added migrated_at column to token_analysis", flush=True)

            if "dex" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN dex TEXT")
                log_print("[DB] ✅ Added dex column to token_analysis", flush=True)

            if "pumpswap_pool_address" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN pumpswap_pool_address TEXT")
                log_print("[DB] ✅ Added pumpswap_pool_address column to token_analysis", flush=True)

            if "source_platform" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN source_platform TEXT")
                log_print("[DB] ✅ Added source_platform column to token_analysis", flush=True)

            if "is_new" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN is_new INTEGER DEFAULT 0")
                log_print("[DB] ✅ Added is_new column to token_analysis", flush=True)

            if "curve_complete" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN curve_complete INTEGER DEFAULT 0")
                log_print("[DB] ✅ Added curve_complete column to token_analysis", flush=True)

            if "curve_completed_at" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN curve_completed_at INTEGER")
                log_print("[DB] ✅ Added curve_completed_at column to token_analysis", flush=True)

            if "curve_completed_slot" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN curve_completed_slot INTEGER")
                log_print("[DB] ✅ Added curve_completed_slot column to token_analysis", flush=True)

            if "curve_complete_source" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN curve_complete_source TEXT")
                log_print("[DB] ✅ Added curve_complete_source column to token_analysis", flush=True)

            if "migration_slot" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN migration_slot INTEGER")
                log_print("[DB] ✅ Added migration_slot column to token_analysis", flush=True)

            if "creator_resolved_slot" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN creator_resolved_slot INTEGER")
                log_print("[DB] ✅ Added creator_resolved_slot column to token_analysis", flush=True)

            if "funding_job_enqueued_slot" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN funding_job_enqueued_slot INTEGER")
                log_print("[DB] ✅ Added funding_job_enqueued_slot column to token_analysis", flush=True)

            if "funding_extracted_slot" not in columns:
                cursor.execute("ALTER TABLE token_analysis ADD COLUMN funding_extracted_slot INTEGER")
                log_print("[DB] ✅ Added funding_extracted_slot column to token_analysis", flush=True)
        except Exception as e:
            pass  # Columns likely already exist

        # Add network columns to creator_blocklist if they don't exist
        try:
            cursor.execute("PRAGMA table_info(creator_blocklist)")
            columns = [col[1] for col in cursor.fetchall()]
            if "connected_to_malicious" not in columns:
                cursor.execute("ALTER TABLE creator_blocklist ADD COLUMN connected_to_malicious INTEGER DEFAULT 0")
                log_print("[DB] ✅ Added connected_to_malicious column to creator_blocklist", flush=True)
            if "network_members" not in columns:
                cursor.execute("ALTER TABLE creator_blocklist ADD COLUMN network_members TEXT")
                log_print("[DB] ✅ Added network_members column to creator_blocklist", flush=True)
        except Exception as e:
            pass  # Columns likely already exist

        # === NEW: Funder webhook tables (Task A) ===
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funder_watchlist (
                funder_address TEXT PRIMARY KEY,
                risk_score INTEGER DEFAULT 0,
                risk_reasons TEXT,
                first_added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                webhook_group_id TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funder_webhook_groups (
                webhook_group_id TEXT PRIMARY KEY,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                helius_webhook_id TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS funder_webhook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                funder_address TEXT NOT NULL,
                signature TEXT NOT NULL,
                slot INTEGER,
                block_time INTEGER,
                direction TEXT,
                counterparty TEXT,
                amount_sol REAL,
                mint TEXT,
                raw_payload TEXT,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(signature, funder_address)
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_watchlist_active ON funder_watchlist(is_active)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_watchlist_group ON funder_watchlist(webhook_group_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_webhook_events_funder ON funder_webhook_events(funder_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_funder_webhook_events_block_time ON funder_webhook_events(block_time DESC)")

        # Create indexes for creator_funders and creator_funding_graph
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_funders_creator ON creator_funders(creator_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_funders_funder ON creator_funders(funder_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_funders_analyzed ON creator_funders(fully_analyzed)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_funding_graph_creator ON creator_funding_graph(creator_address)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_creator_funding_graph_funder ON creator_funding_graph(funder_address)")

        log_print("[DB] ✅ Funder webhook tables ensured", flush=True)
        log_print("[DB] ✅ Creator funders and funding graph tables ensured", flush=True)

        conn.commit()
        conn.close()

    async def _enqueue_creator_funding_job(
        self,
        creator: Optional[str],
        *,
        mint: Optional[str],
        migration_timestamp: Optional[str],
        create_tx_signature: Optional[str] = None,
        delay_seconds: Optional[int] = None,
        source: Optional[str] = None,
        curve_completed_slot: Optional[int] = None,
    ) -> bool:
        """Persist creator funding extraction so it survives restarts."""
        if not creator or not mint:
            return False
        now = int(time.time())
        next_attempt_at = now + int(delay_seconds if delay_seconds is not None else self.DISCOVERY_CRITICAL_WINDOW_SECONDS)

        # Brand-new creators (no existing funder data) get priority=1 so they jump
        # ahead of any backlog in the queue worker. Known creators stay at priority=0.
        try:
            _check = db_connect(DB_PATH, timeout=3)
            _funder_count = _check.execute(
                "SELECT COUNT(*) FROM creator_funders WHERE creator_address=?", (creator,)
            ).fetchone()[0]
            _check.close()
            job_priority = 1 if _funder_count == 0 else 0
            priority_reason = "brand_new_creator" if job_priority else "known_creator"
        except Exception:
            job_priority = 0
            priority_reason = "unknown"
        async with self.db_lock:
            try:
                conn = db_connect(DB_PATH, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                cursor = conn.cursor()
                existing = cursor.execute(
                    """
                    SELECT creator_address, status
                    FROM creator_funding_queue
                    WHERE mint = ?
                    LIMIT 1
                    """,
                    (mint,),
                ).fetchone()
                if existing:
                    existing_creator = str(existing[0]) if existing[0] else "unknown"
                    existing_status = str(existing[1]) if existing[1] else "unknown"
                    conn.close()
                    log_print(
                        f"[FUNDING_QUEUE] ⏭️ Skip duplicate enqueue for mint={mint[:8]}... existing_creator={existing_creator[:8]}... status={existing_status}",
                        flush=True,
                    )
                    return False
                cursor.execute(
                    """
                    INSERT INTO creator_funding_queue (
                        creator_address, mint, migration_timestamp, create_tx_signature,
                        status, source, next_attempt_at, locked_until, attempts, last_error,
                        funding_enqueued_at, curve_completed_slot, enqueued_slot,
                        job_priority, priority_reason, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, ?, 0, 0, NULL, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(creator_address, mint) DO NOTHING
                    """,
                    (creator, mint, migration_timestamp, create_tx_signature, source, next_attempt_at,
                     now, curve_completed_slot, curve_completed_slot, job_priority, priority_reason, now, now),
                )
                conn.commit()
                conn.close()
                log_print(
                    f"[FUNDING_QUEUE] 📥 Enqueued creator funding for {creator[:8]}... mint={mint[:8]} next={next_attempt_at} priority={'HIGH' if job_priority else 'normal'} reason={priority_reason}",
                    flush=True,
                )
                premig_log(f"[TIMING] mint={mint} enqueued source={source} t={now}")
                try:
                    conn2 = db_connect(DB_PATH, timeout=10)
                    conn2.execute(
                        "UPDATE token_analysis SET funding_job_enqueued_slot = ? WHERE mint = ?",
                        (now, mint),
                    )
                    conn2.commit()
                    conn2.close()
                except Exception:
                    pass
                self._creator_funding_queue_wakeup.set()
                return True
            except Exception as e:
                log_print(f"[FUNDING_QUEUE] ⚠ Failed to enqueue funding for {creator[:16]}...: {e}", flush=True)
                return False

    async def _post_extraction_intelligence_refresh(self, creator: str) -> None:
        """
        Targeted intelligence refresh fired after creator funding extraction completes.
        Debounced: skipped if a refresh ran within the last debounce window.
        Runs in a background thread so it never blocks the listener event loop.

        Signals updated immediately:
          1. IRC watchlist row for this creator
          2. NetworksReleaseBuilder (dashboard summary)
          3. Relationship events diff (log new discoveries)

        Signals intentionally deferred to cron:
          - WalletClusteringEngine, FarmClusterDetection, CoordinatedEdgesBuilder
          - SecondHopExpansionBuilder / UpstreamExpansionBuilder
        """
        import time as _time
        now = _time.time()

        # Debounce: if a refresh just ran, skip — cron will catch it
        if now - self._intel_refresh_last_run < self._intel_refresh_debounce_secs:
            remaining = int(self._intel_refresh_debounce_secs - (now - self._intel_refresh_last_run))
            log_print(f"[INTEL_REFRESH] Debounced for {creator[:8]}… (next in {remaining}s)", flush=True)
            return

        self._intel_refresh_last_run = now

        def _run_refresh():
            import time as _t
            t0 = _t.time()
            try:
                # 1. Snapshot before (for relationship event diff)
                from src.core.relationship_events import take_snapshot
                before = take_snapshot(DB_PATH)

                # 2. IRC — targeted upsert for this creator only (avoid full rebuild)
                _ts = _t.time()
                try:
                    from src.core.intelligence_refresh import apply_migration as irc_migrate, _db as irc_db, _score_creator, _now as irc_now
                    import json as _json
                    irc_migrate(DB_PATH)
                    _irc_conn = irc_db(DB_PATH)
                    _now_ts = irc_now()
                    _row = _irc_conn.execute("""
                        SELECT
                            COUNT(DISTINCT cf.funder_address) AS funder_count,
                            COUNT(DISTINCT ta.mint)           AS token_count,
                            SUM(CASE WHEN ta.migrated_at IS NOT NULL THEN 1 ELSE 0 END) AS migrated_count,
                            csf.is_self_funding,
                            (SELECT COUNT(*) FROM network_membership nm WHERE nm.creator_address = ta.earliest_tx_creator) AS network_count
                        FROM token_analysis ta
                        LEFT JOIN creator_funders cf ON cf.creator_address = ta.earliest_tx_creator AND cf.is_cex = 0
                        LEFT JOIN creator_self_funding csf ON csf.creator_address = ta.earliest_tx_creator
                        WHERE ta.earliest_tx_creator = ?
                        GROUP BY ta.earliest_tx_creator
                    """, (creator,)).fetchone()
                    _non_cex_funders = (_row["funder_count"] or 0) if _row else 0
                    _existing = _irc_conn.execute(
                        "SELECT status FROM intelligence_refresh_candidates WHERE target_type='creator' AND target_address=?",
                        (creator,)
                    ).fetchone()
                    if not _existing:
                        if _non_cex_funders >= 1:
                            # Baseline watchlist — every migrated creator with ≥1 non-CEX funder.
                            # rpc_allowed=0: no RPC scan triggered. Analyzers upgrade priority later
                            # if signals emerge (shared funders, self-funding, cluster overlap etc.)
                            _baseline_reasons = _json.dumps(["migrated_creator", "has_non_cex_funder", "baseline_watchlist"])
                            _irc_conn.execute("""
                                INSERT INTO intelligence_refresh_candidates
                                    (target_type, target_address, priority, reason_codes, status, rpc_allowed, created_at, updated_at)
                                VALUES ('creator', ?, 15, ?, 'watchlist', 0, ?, ?)
                            """, (creator, _baseline_reasons, _now_ts, _now_ts))
                            _irc_conn.commit()
                            log_print(f"[INTEL_REFRESH] Baseline watchlist added for {creator[:8]}… ({_non_cex_funders} non-CEX funders)", flush=True)
                    elif _row and _non_cex_funders > 0:
                        # Creator already in IRC — check if signal score warrants an upgrade
                        _priority, _reasons = _score_creator(
                            self_funding=bool(_row["is_self_funding"]),
                            funder_count=_non_cex_funders,
                            single_creator_ratio=0.0,
                            last_scan_age_days=999,
                            migrated_count=_row["migrated_count"] or 0,
                            token_count=_row["token_count"] or 0,
                            no_network=(_row["network_count"] or 0) == 0,
                        )
                        if _priority > 15 and _existing["status"] == "watchlist":
                            _irc_conn.execute("""
                                UPDATE intelligence_refresh_candidates
                                SET priority=?, reason_codes=?, updated_at=?
                                WHERE target_type='creator' AND target_address=? AND priority < ?
                            """, (_priority, _json.dumps(_reasons), _now_ts, creator, _priority))
                            _irc_conn.commit()
                    _irc_conn.close()
                    log_print(f"[INTEL_REFRESH] IRC upsert for {creator[:8]}… ({_t.time()-_ts:.1f}s)", flush=True)
                except Exception as e:
                    log_print(f"[INTEL_REFRESH] IRC error ({_t.time()-_ts:.1f}s): {e}", flush=True)

                # Full 2H expansion is intentionally kept out of the listener
                # hot path. It can scan a large static exclusion set and hold
                # SQLite write locks long enough to block migration ingestion.
                # The local graph refresh/analyzer cycle rebuilds 2H instead.
                log_print("[INTEL_REFRESH] 2H expansion deferred to local graph refresh", flush=True)

                # 3. NetworksReleaseBuilder
                _ts = _t.time()
                try:
                    from src.utils.build_networks_release import build_networks_release
                    build_networks_release(DB_PATH)
                    log_print(f"[INTEL_REFRESH] NetworksRelease rebuilt ({_t.time()-_ts:.1f}s)", flush=True)
                except Exception as e:
                    log_print(f"[INTEL_REFRESH] NetworksRelease error ({_t.time()-_ts:.1f}s): {e}", flush=True)

                # 5. Relationship events diff
                _ts = _t.time()
                try:
                    from src.core.relationship_events import rebuild_after_scan
                    rebuild_after_scan(DB_PATH, before=before)
                    log_print(f"[INTEL_REFRESH] Events diff done ({_t.time()-_ts:.1f}s)", flush=True)
                except Exception as e:
                    log_print(f"[INTEL_REFRESH] Relationship events error ({_t.time()-_ts:.1f}s): {e}", flush=True)

                log_print(f"[INTEL_REFRESH] Done in {_t.time()-t0:.1f}s", flush=True)

            except Exception as e:
                log_print(f"[INTEL_REFRESH] Unexpected error: {e}", flush=True)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _run_refresh)

    async def _process_creator_resolution_queue_periodic(self) -> None:
        """Drain P0 missing-creator jobs before creator funding extraction."""
        await asyncio.sleep(2)
        while True:
            sleep_seconds = 15
            try:
                from src.core.creator_resolution_queue import (
                    enqueue_missing_migrated_tokens,
                    process_queue as process_creator_resolution_queue,
                )

                enqueued = await asyncio.to_thread(
                    enqueue_missing_migrated_tokens,
                    DB_PATH,
                    limit=25,
                    source="listener_p0_sweep",
                    max_age_seconds=3600,
                )
                result = await asyncio.to_thread(
                    process_creator_resolution_queue,
                    DB_PATH,
                    limit=2,
                )
                processed = int(result.get("processed") or 0)
                if enqueued or processed:
                    sleep_seconds = 3
                    log_print(
                        "[CREATOR_RESOLUTION_QUEUE] "
                        f"P0 enqueued={enqueued} processed={processed} "
                        f"resolved={result.get('resolved', 0)} "
                        f"funding={result.get('funding_enqueued', 0)} "
                        f"failed={result.get('failed', 0)}",
                        flush=True,
                    )
            except Exception as e:
                log_print(f"[CREATOR_RESOLUTION_QUEUE] ⚠ P0 worker error: {e}", flush=True)
                sleep_seconds = 20
            await asyncio.sleep(sleep_seconds)

    async def _periodic_cluster_rebuild(self) -> None:
        """Rebuild super_clusters every 10 minutes — decoupled from per-extraction triggers."""
        await asyncio.sleep(60)  # initial delay to let startup settle
        while True:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, rebuild_super_clusters_from_funding)
                log_print("[CLUSTERING] ✅ Periodic cluster rebuild complete", flush=True)
            except Exception as e:
                log_print(f"[CLUSTERING] ⚠ Periodic rebuild error: {e}", flush=True)
            await asyncio.sleep(600)  # 10 minutes

    async def _process_creator_funding_queue_periodic(self) -> None:
        """Process durable creator funding work after the critical window."""
        await asyncio.sleep(2)
        last_idle_log_at = 0
        while True:
            try:
                now = int(time.time())
                stale_running_recovered = 0
                overdue_ready_count = 0
                oldest_overdue_seconds = 0
                async with self.db_lock:
                    conn = db_connect(DB_PATH, timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("PRAGMA busy_timeout=30000")
                    conn.row_factory = sqlite3.Row
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        UPDATE creator_funding_queue
                        SET status = 'complete',
                            locked_until = 0,
                            attempts = attempts + 1,
                            last_error = NULL,
                            funding_extracted_at = COALESCE(funding_extracted_at, ?),
                            updated_at = ?
                        WHERE (
                              (status = 'running' AND locked_until > 0 AND locked_until < ?)
                           OR status = 'retry'
                          )
                          AND EXISTS (
                              SELECT 1
                              FROM creator_funders cf
                              WHERE cf.creator_address = creator_funding_queue.creator_address
                              LIMIT 1
                          )
                        """,
                        (now, now, now),
                    )
                    recovered_completed = int(cursor.rowcount or 0)
                    if recovered_completed:
                        cursor.execute(
                            """
                            INSERT OR IGNORE INTO token_rescore_queue (mint, reason, created_at)
                            SELECT mint, 'funding_extracted_recovered', ?
                            FROM creator_funding_queue
                            JOIN token_analysis USING (mint)
                            WHERE status = 'complete'
                              AND creator_funding_queue.updated_at = ?
                              AND COALESCE(token_analysis.lifecycle_stage, '') = 'migrated'
                              AND token_analysis.migrated_at IS NOT NULL
                            """,
                            (now, now),
                        )
                        log_print(
                            f"[FUNDING_QUEUE] ✅ Recovered {recovered_completed} stale running job(s) with extracted funders",
                            flush=True,
                        )
                    cursor.execute(
                        """
                        UPDATE creator_funding_queue
                        SET status = 'retry',
                            locked_until = 0,
                            last_error = COALESCE(last_error, 'stale running job recovered'),
                            updated_at = ?
                        WHERE status = 'running'
                          AND locked_until > 0
                          AND locked_until < ?
                        """,
                        (now, now),
                    )
                    stale_running_recovered = int(cursor.rowcount or 0)
                    if stale_running_recovered:
                        log_print(
                            f"[FUNDING_QUEUE] ♻ Recovered {stale_running_recovered} stale running job(s)",
                            flush=True,
                        )
                    queue_stats = cursor.execute(
                        """
                        SELECT
                            SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count,
                            SUM(CASE WHEN status IN ('pending', 'retry') AND locked_until < ? AND next_attempt_at <= ? THEN 1 ELSE 0 END) AS ready_count,
                            MIN(CASE WHEN status IN ('pending', 'retry') AND locked_until < ? AND next_attempt_at <= ? THEN next_attempt_at END) AS oldest_ready_at
                        FROM creator_funding_queue
                        """,
                        (now, now, now, now),
                    ).fetchone()
                    running_count = int((queue_stats["running_count"] or 0) if queue_stats else 0)
                    overdue_ready_count = int((queue_stats["ready_count"] or 0) if queue_stats else 0)
                    oldest_ready_at = int(queue_stats["oldest_ready_at"] or 0) if queue_stats and queue_stats["oldest_ready_at"] else 0
                    oldest_overdue_seconds = max(0, now - oldest_ready_at) if oldest_ready_at else 0
                    rows = cursor.execute(
                        """
                        SELECT creator_address, mint, migration_timestamp, create_tx_signature, attempts,
                               COALESCE(job_priority, 0) as job_priority,
                               COALESCE(priority_reason, 'unknown') as priority_reason
                        FROM creator_funding_queue
                        WHERE status IN ('pending', 'retry')
                          AND locked_until < ?
                          AND next_attempt_at <= ?
                        ORDER BY COALESCE(job_priority, 0) DESC, next_attempt_at ASC, created_at ASC
                        LIMIT 3
                        """,
                        (now, now),
                    ).fetchall()
                    if rows:
                        lock_until = now + 180
                        cursor.executemany(
                            """
                            UPDATE creator_funding_queue
                            SET status = 'running',
                                locked_until = ?,
                                updated_at = ?
                            WHERE creator_address = ?
                              AND mint = ?
                            """,
                            [(lock_until, now, str(row["creator_address"]), str(row["mint"])) for row in rows],
                        )
                        conn.commit()
                        log_print(
                            f"[FUNDING_QUEUE] 📦 Claimed {len(rows)} job(s) ready={overdue_ready_count} running={running_count} oldest_overdue={oldest_overdue_seconds}s",
                            flush=True,
                        )
                    conn.close()

                if not rows and overdue_ready_count > 0 and now - last_idle_log_at >= 30:
                    log_print(
                        f"[FUNDING_QUEUE] ⏳ Ready work waiting ready={overdue_ready_count} running={running_count} oldest_overdue={oldest_overdue_seconds}s",
                        flush=True,
                    )
                    last_idle_log_at = now

                for row in rows:
                    creator = str(row["creator_address"])
                    mint = str(row["mint"])
                    migration_timestamp = row["migration_timestamp"]
                    if not migration_timestamp:
                        # Fall back to migrated_at from token_analysis, then now
                        try:
                            _mt_row = db_connect(DB_PATH, timeout=5).execute(
                                "SELECT migrated_at FROM token_analysis WHERE mint = ? LIMIT 1", (mint,)
                            ).fetchone()
                            if _mt_row and _mt_row[0]:
                                from datetime import timezone
                                migration_timestamp = datetime.utcfromtimestamp(int(_mt_row[0])).replace(tzinfo=timezone.utc).isoformat()
                            else:
                                migration_timestamp = datetime.utcnow().isoformat() + "Z"
                        except Exception:
                            migration_timestamp = datetime.utcnow().isoformat() + "Z"
                    create_tx_signature = row["create_tx_signature"]
                    attempts = int(row["attempts"] or 0)
                    try:
                        job_started_at = time.time()
                        _pr = str(row["priority_reason"]) if row["priority_reason"] else "unknown"
                        _jp = int(row["job_priority"]) if row["job_priority"] else 0
                        log_print(
                            f"[FUNDING_QUEUE] 🚀 Processing creator funding for {creator[:8]}... mint={mint[:8]} priority={'HIGH' if _jp else 'normal'} reason={_pr}",
                            flush=True,
                        )
                        try:
                            _extraction_result = await asyncio.wait_for(
                                extract_funding_for_new_token(
                                    creator,
                                    migration_timestamp,
                                    create_tx_signature,
                                    mint,
                                ),
                                timeout=self.CREATOR_FUNDING_JOB_TIMEOUT_SECONDS,
                            )
                        except asyncio.TimeoutError as timeout_exc:
                            raise TimeoutError(
                                f"creator funding timed out after {self.CREATOR_FUNDING_JOB_TIMEOUT_SECONDS}s"
                            ) from timeout_exc
                        _extraction_errored = bool(
                            isinstance(_extraction_result, dict) and _extraction_result.get("error")
                        )
                        # If creator_funders is still empty, scan immediately in background thread
                        # so prediction can move from PENDING_FUNDING to a real score
                        try:
                            with db_connect(DB_PATH, timeout=10) as _cf_conn:
                                _cf_count = _cf_conn.execute(
                                    "SELECT COUNT(*) FROM creator_funders WHERE creator_address=?", (creator,)
                                ).fetchone()[0]
                            if _cf_count == 0:
                                log_print(f"[FRESH_CREATOR] ⏳ No funders found — scanning immediately: {creator[:8]}", flush=True)
                                import threading as _threading
                                def _scan_and_rescore(_creator, _mint):
                                    try:
                                        # Use extract_funder_transfers (sync) which already knows DB_PATH
                                        from src.extractors.funder_incoming_extractor import extract_for_creator as _extract
                                        import os as _os
                                        _os.environ.setdefault('DB_PATH', DB_PATH)
                                        _extract(_creator)
                                        log_print(f"[FRESH_CREATOR] ✅ Funder extraction complete: {_creator[:8]}", flush=True)
                                        with db_connect(DB_PATH, timeout=30) as _pc:
                                            from src.core.token_prediction_builder import TokenPredictionBuilder as _TPB
                                            _TPB(DB_PATH).score_single(_pc, _mint, 'FUNDING_COMPLETE')
                                        log_print(f"[FRESH_CREATOR] ✅ Rescored after scan: {_mint[:16]}", flush=True)
                                    except Exception as _e:
                                        log_print(f"[FRESH_CREATOR] ⚠ Scan/rescore failed: {_e}", flush=True)
                                _threading.Thread(target=_scan_and_rescore, args=(creator, mint), daemon=True).start()
                        except Exception as _fc_e:
                            log_print(f"[FRESH_CREATOR] ⚠ Failed to start scan thread: {_fc_e}", flush=True)
                        if get_migration_setting('auto_extract_funders', False):
                            try:
                                log_print(f"[FUNDER_EXTRACTION] ⏳ Starting funder transfer extraction for {creator[:8]}...", flush=True)
                                await extract_funder_transfers_async(creator)
                                log_print(f"[FUNDER_EXTRACTION] ✅ Funder transfer extraction complete", flush=True)
                            except Exception as funder_exc:
                                log_print(f"[FUNDER_EXTRACTION] ⚠️ Error in funder extraction: {funder_exc}", flush=True)
                        else:
                            log_print(f"[FUNDER_EXTRACTION] ⏭️ Skipped (auto_extract_funders toggle is OFF)", flush=True)
                        try:
                            from src.core.risk_scoring_builder import RiskScoringBuilder as _RSB
                            _RSB(DB_PATH).score_creator_now(creator)
                            log_print(f"[RISK_SCORE] ✅ Creator scored mint={mint[:16]} creator={creator[:8]}", flush=True)
                        except Exception as _rs_e:
                            log_print(f"[RISK_SCORE] ⚠ Creator score failed: {_rs_e}", flush=True)
                        try:
                            from src.core.token_prediction_builder import TokenPredictionBuilder as _TPB
                            with db_connect(DB_PATH, timeout=60) as _pred_conn:
                                _TPB(DB_PATH).score_single(_pred_conn, mint, 'FUNDING_COMPLETE')
                            log_print(f"[PREDICTION] ✅ Re-scored after funding complete mint={mint[:16]}", flush=True)
                        except Exception as _pred_e:
                            log_print(f"[PREDICTION] ⚠ Re-score after funding failed mint={mint[:16]}: {_pred_e}", flush=True)

                        # Cluster rebuild is now scheduled periodically — skip per-extraction
                        # trigger to avoid long write locks on every funding completion.

                        # Verify funders were actually written before marking complete.
                        # A DB lock during _flush_page_batch could silently lose rows.
                        _funder_count = 0
                        try:
                            with db_connect(DB_PATH, timeout=10) as _vconn:
                                _funder_count = _vconn.execute(
                                    "SELECT COUNT(*) FROM creator_funders WHERE creator_address=?", (creator,)
                                ).fetchone()[0]
                        except Exception as _ve:
                            log_print(f"[FUNDING_QUEUE] ⚠ Funder count check failed: {_ve}", flush=True)

                        if _extraction_errored and _funder_count == 0 and attempts < 3:
                            # Extraction ran but wrote nothing — retry in 60s
                            async with self.db_lock:
                                conn = db_connect(DB_PATH, timeout=30)
                                cursor = conn.cursor()
                                cursor.execute(
                                    """
                                    UPDATE creator_funding_queue
                                    SET status = 'retry',
                                        locked_until = 0,
                                        attempts = ?,
                                        next_attempt_at = ?,
                                        last_error = 'no_funders_written',
                                        updated_at = ?
                                    WHERE creator_address = ? AND mint = ?
                                    """,
                                    (attempts + 1, int(time.time()) + 60, int(time.time()), creator, mint),
                                )
                                conn.commit()
                                conn.close()
                            log_print(f"[FUNDING_QUEUE] ⚠ No funders written — queued for retry (attempt {attempts+1}): {creator[:8]}", flush=True)
                        else:
                            async with self.db_lock:
                                conn = db_connect(DB_PATH, timeout=30)
                                conn.execute("PRAGMA busy_timeout=30000")
                                cursor = conn.cursor()
                                cursor.execute(
                                    """
                                    UPDATE creator_funding_queue
                                    SET status = 'complete',
                                        locked_until = 0,
                                        attempts = ?,
                                        last_error = NULL,
                                        funding_extracted_at = ?,
                                        updated_at = ?
                                    WHERE creator_address = ?
                                      AND mint = ?
                                    """,
                                    (attempts + 1, int(time.time()), int(time.time()), creator, mint),
                                )
                                cursor.execute(
                                    """
                                    INSERT OR REPLACE INTO token_rescore_queue (mint, reason, created_at)
                                    SELECT ?, 'funding_complete', ?
                                    WHERE EXISTS (
                                        SELECT 1
                                        FROM token_analysis
                                        WHERE mint = ?
                                          AND COALESCE(lifecycle_stage, '') = 'migrated'
                                          AND migrated_at IS NOT NULL
                                    )
                                    """,
                                    (mint, int(time.time()), mint),
                                )
                                cursor.execute(
                                    "UPDATE token_analysis SET funding_extracted_slot = ? WHERE mint = ?",
                                    (int(time.time()), mint),
                                )
                                conn.commit()
                                conn.close()
                        elapsed = time.time() - job_started_at
                        log_print(f"[FUNDING_QUEUE] ✅ Completed creator funding for {creator[:8]}... mint={mint[:8]}... funders={_funder_count} elapsed={elapsed:.1f}s", flush=True)
                        # Auto-enqueue unclassified funders for second-hop lite scan
                        # so fresh creators don't stay with unknown fund source
                        try:
                            with db_connect(DB_PATH, timeout=10) as _shl_conn:
                                _shl_rows = _shl_conn.execute("""
                                    SELECT funder_address FROM creator_funders
                                    WHERE creator_address = ?
                                      AND is_cex = 0
                                      AND is_classified = 0
                                      AND funder_address NOT IN (
                                          SELECT funder_address FROM second_hop_lite_queue
                                      )
                                """, (creator,)).fetchall()
                                if _shl_rows:
                                    _shl_conn.executemany("""
                                        INSERT OR IGNORE INTO second_hop_lite_queue (
                                            funder_address, priority, reason_codes,
                                            status, attempts, last_error, rpc_calls_used,
                                            created_at, scanned_at, next_attempt_at
                                        ) VALUES (?, 170, '["fresh_creator_auto"]',
                                            'pending', 0, NULL, 0, ?, NULL, ?)
                                    """, [(r[0], int(time.time()), int(time.time())) for r in _shl_rows])
                                    log_print(f"[SHL_AUTO] ✅ Enqueued {len(_shl_rows)} unclassified funder(s) for second-hop scan: {creator[:8]}", flush=True)
                        except Exception as _shl_e:
                            log_print(f"[SHL_AUTO] ⚠ Failed to enqueue SHL: {_shl_e}", flush=True)
                        # Immediate provisional network assignment — best-effort, non-blocking
                        try:
                            from src.core.network_membership_builder import assign_live_network_for_creator
                            net_result = assign_live_network_for_creator(DB_PATH, creator)
                            if net_result.get('assigned'):
                                log_print(f"[LIVE_NETWORK] ✅ {creator[:8]} → {net_result['network_name']} (provisional={net_result['provisional']})", flush=True)
                            else:
                                log_print(f"[LIVE_NETWORK] No shared funders for {creator[:8]}", flush=True)
                        except Exception as _lne:
                            log_print(f"[LIVE_NETWORK] Error: {_lne}", flush=True)
                        # Targeted intelligence refresh (debounced, background)
                        asyncio.create_task(
                            self._post_extraction_intelligence_refresh(creator)
                        )

                        # Phase 1 dual-write: mark creator as baselined in creator_profile
                        # so Phase 2 cache check fires immediately for this creator on next token.
                        try:
                            from src.creators.migration_bridge import dual_write_creator_resolved
                            from src.creators.repository import CreatorRepository
                            from src.creators.helius_watch import register_creator_address
                            _repo = CreatorRepository(CREATOR_DB_PATH, self.db_lock)
                            await dual_write_creator_resolved(
                                creator, mint,
                                create_tx_signature=create_tx_signature,
                                reason="extraction_complete",
                                repo=_repo,
                                register_webhook_fn=register_creator_address,
                            )
                        except Exception as _dw_e:
                            log_print(f"[FUNDING_QUEUE] ⚠ dual_write failed creator={creator[:8]}: {_dw_e}", flush=True)
                    except Exception as e:
                        retry_at = int(time.time()) + min(900, 120 * (attempts + 1))
                        async with self.db_lock:
                            conn = db_connect(DB_PATH, timeout=30)
                            conn.execute("PRAGMA busy_timeout=30000")
                            cursor = conn.cursor()
                            cursor.execute(
                                """
                                UPDATE creator_funding_queue
                                SET status = 'retry',
                                    locked_until = 0,
                                    attempts = ?,
                                    next_attempt_at = ?,
                                    last_error = ?,
                                    updated_at = ?
                                WHERE creator_address = ?
                                  AND mint = ?
                                """,
                                (attempts + 1, retry_at, str(e), int(time.time()), creator, mint),
                            )
                            conn.commit()
                            conn.close()
                        elapsed = time.time() - job_started_at
                        log_print(
                            f"[FUNDING_QUEUE] ⚠ Funding extraction failed for {creator[:8]}... mint={mint[:8]} elapsed={elapsed:.1f}s retry_at={retry_at}: {e}",
                            flush=True,
                        )
            except Exception as e:
                log_print(f"[FUNDING_QUEUE] ⚠ Queue processor error: {e}", flush=True)
            try:
                await asyncio.wait_for(self._creator_funding_queue_wakeup.wait(), timeout=2.0)
                self._creator_funding_queue_wakeup.clear()
            except asyncio.TimeoutError:
                pass

    async def _store_analysis(self, mint: str, analysis: dict, signature: str = None, pool_address: str = None):
        """Store post-migration analysis results"""
        async with self.db_lock:
            try:
                conn = db_connect(DB_PATH, timeout=15)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA busy_timeout=60000")
                cursor = conn.cursor()

                # Check if creator belongs to any cluster
                creator_address = analysis.get("earliest_tx_creator")
                cluster_id = None
                cluster_name = None
                cluster_risk_multiplier = 1.0
                network_funder_address = None
                network_name = None

                if creator_address:
                    try:
                        from cluster_risk_checker import check_creator
                        cluster_info = check_creator(creator_address)
                        if cluster_info.get('in_cluster'):
                            cluster_id = cluster_info.get('cluster_id')
                            cluster_name = cluster_info.get('cluster_name', cluster_id)
                            cluster_risk_multiplier = cluster_info.get('risk_multiplier', 1.0)
                    except Exception as e:
                        log_print(f"[CLUSTER] Error checking creator {creator_address}: {e}", flush=True)

                    # Look up creator's network immediately
                    try:
                        cursor.execute("""
                            SELECT DISTINCT funder_address
                            FROM creator_funders
                            WHERE creator_address = ?
                            LIMIT 1
                        """, (creator_address,))
                        funder_row = cursor.fetchone()
                        if funder_row:
                            network_funder_address = funder_row[0]
                            # Look up network name from atomic_network_names
                            cursor.execute("""
                                SELECT network_name
                                FROM atomic_network_names
                                WHERE funder_address = ?
                                LIMIT 1
                            """, (network_funder_address,))
                            network_row = cursor.fetchone()
                            if network_row:
                                network_name = network_row[0]
                    except Exception as e:
                        log_print(f"[NETWORK] Error looking up creator network {creator_address}: {e}", flush=True)

                # Store post-migration analysis with live price tracking
                cursor.execute("""
                    INSERT INTO token_analysis (
                        mint, created_at, analyzed_at, events_parsed,
                        post_migration_mint_concentration, post_migration_unique_minters_ratio,
                        post_migration_sell_suppression_ratio, post_migration_mint_velocity_sec,
                        post_migration_buy_size_variance, post_migration_sell_volume_concentration,
                        post_migration_creator_activity_ratio,
                        rug_probability, risk_level, post_migration_coverage,
                        migration_tx, price_current, price_highest, pool_address, earliest_tx_creator, creator_is_blocked, network_risk, connected_malicious_count,
                        lifecycle_stage,
                        cluster_id, cluster_name, cluster_risk_multiplier, network_funder_address, network_name
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(mint) DO UPDATE SET
                        created_at = COALESCE(token_analysis.created_at, excluded.created_at),
                        analyzed_at = excluded.analyzed_at,
                        events_parsed = excluded.events_parsed,
                        post_migration_mint_concentration = excluded.post_migration_mint_concentration,
                        post_migration_unique_minters_ratio = excluded.post_migration_unique_minters_ratio,
                        post_migration_sell_suppression_ratio = excluded.post_migration_sell_suppression_ratio,
                        post_migration_mint_velocity_sec = excluded.post_migration_mint_velocity_sec,
                        post_migration_buy_size_variance = excluded.post_migration_buy_size_variance,
                        post_migration_sell_volume_concentration = excluded.post_migration_sell_volume_concentration,
                        post_migration_creator_activity_ratio = excluded.post_migration_creator_activity_ratio,
                        rug_probability = excluded.rug_probability,
                        risk_level = excluded.risk_level,
                        post_migration_coverage = excluded.post_migration_coverage,
                        migration_tx = COALESCE(excluded.migration_tx, token_analysis.migration_tx),
                        pool_address = COALESCE(excluded.pool_address, token_analysis.pool_address),
                        earliest_tx_creator = COALESCE(excluded.earliest_tx_creator, token_analysis.earliest_tx_creator),
                        creator_mismatch = CASE
                            WHEN excluded.earliest_tx_creator IS NOT NULL
                             AND token_analysis.pf_ws_creator IS NOT NULL
                             AND excluded.earliest_tx_creator != token_analysis.pf_ws_creator
                            THEN 1 ELSE 0
                        END,
                        creator_is_blocked = COALESCE(excluded.creator_is_blocked, token_analysis.creator_is_blocked),
                        network_risk = COALESCE(excluded.network_risk, token_analysis.network_risk),
                        connected_malicious_count = COALESCE(excluded.connected_malicious_count, token_analysis.connected_malicious_count),
                        lifecycle_stage = COALESCE(token_analysis.lifecycle_stage, excluded.lifecycle_stage),
                        cluster_id = COALESCE(excluded.cluster_id, token_analysis.cluster_id),
                        cluster_name = COALESCE(excluded.cluster_name, token_analysis.cluster_name),
                        cluster_risk_multiplier = COALESCE(excluded.cluster_risk_multiplier, token_analysis.cluster_risk_multiplier),
                        network_funder_address = COALESCE(excluded.network_funder_address, token_analysis.network_funder_address),
                        network_name = COALESCE(excluded.network_name, token_analysis.network_name)
                """, (
                    mint,
                    time.time(),  # created_at
                    time.time(),  # analyzed_at
                    analysis.get("total_events", 0),
                    analysis.get("mint_concentration", 0),
                    analysis.get("unique_minters_ratio", 0),
                    analysis.get("sell_suppression_ratio", 0),
                    analysis.get("mint_velocity_sec", 0),
                    analysis.get("buy_size_variance", 0),
                    analysis.get("sell_volume_concentration", 0),
                    analysis.get("creator_activity_ratio", 0),
                    analysis.get("rug_probability", 0),
                    analysis.get("risk_level", ""),
                    analysis.get("coverage", 0),
                    signature,
                    None,  # price_current will be updated by background task
                    None,  # price_highest will be updated by background task
                    pool_address,  # Extracted pool address from migration transaction
                    analysis.get("earliest_tx_creator"),  # Creator from earliest transaction
                    analysis.get("creator_is_blocked", 0),  # Is creator in blocklist?
                    analysis.get("network_risk", 0),  # Is creator connected to malicious creators?
                    analysis.get("connected_malicious_count"),  # Count of connected malicious creators
                    'migration_pending',
                    cluster_id,  # Cluster ID if creator is in a cluster
                    cluster_name,  # Cluster name (NexusCerberus, etc.)
                    cluster_risk_multiplier,  # Risk multiplier for cluster
                    network_funder_address,  # Funder address from creator_funders
                    network_name  # Network name from atomic_network_names
                ))

                conn.commit()

                # Check and log creator mismatch
                rpc_creator = analysis.get("earliest_tx_creator")
                if rpc_creator:
                    conn2 = db_connect(DB_PATH, timeout=15)
                    row = conn2.execute(
                        "SELECT pf_ws_creator, creator_mismatch FROM token_analysis WHERE mint = ?", (mint,)
                    ).fetchone()
                    conn2.close()
                    if row and row[0]:
                        if row[1]:
                            log_print(
                                f"[CREATOR_CHECK] ⚠ MISMATCH {mint} | WS={row[0][:8]}... RPC={rpc_creator[:8]}...",
                                flush=True,
                            )
                        else:
                            log_print(
                                f"[CREATOR_CHECK] ✅ MATCH {mint} | WS={row[0][:8]}... RPC={rpc_creator[:8]}...",
                                flush=True,
                            )

                conn.close()
                if rpc_creator:
                    create_tx_sig = getattr(analyzer, "_create_tx_signature", None) if analyzer else None
                    await self._enqueue_creator_funding_job(
                        rpc_creator,
                        mint=mint,
                        migration_timestamp=created_at,
                        create_tx_signature=create_tx_sig,
                        delay_seconds=0,
                        source="store_analysis",
                    )
                else:
                    try:
                        from src.core.creator_resolution_queue import connect as _crq_connect, enqueue_missing_creator
                        with _crq_connect(DB_PATH, timeout=10) as _crq_conn:
                            enqueue_missing_creator(
                                _crq_conn,
                                mint,
                                reason="store_analysis_missing_creator",
                                source="store_analysis",
                            )
                            _crq_conn.commit()
                    except Exception as _crq_e:
                        log_print(
                            f"[CREATOR_RESOLUTION_QUEUE] ⚠ enqueue failed mint={mint[:8]}...: {_crq_e}",
                            flush=True,
                        )
                pool_info = f"Pool: {pool_address[:16]}" if pool_address else "Pool: will discover at price-time"
                log_print(f"[DB] ✅ Stored analysis {mint} | {pool_info}", flush=True)
            except Exception as e:
                log_print(f"[DB] ❌ Failed to store analysis for {mint}: {e}", flush=True)

    def _token_exists_in_db(self, mint: str) -> bool:
        """
        Check whether a token is already in a post-birth lifecycle stage.

        Pre-migration launch rows are inserted early with `lifecycle_stage='bonding_curve'`.
        Those rows must still be allowed to flow through the later migration pipeline.
        """
        try:
            conn = db_connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute("SELECT lifecycle_stage FROM token_analysis WHERE mint = ?", (mint,))
            result = cursor.fetchone()
            conn.close()
            if not result:
                return False
            lifecycle_stage = result[0]
            return lifecycle_stage != 'bonding_curve'
        except Exception as e:
            log_print(f"[DB] ⚠ Could not check if token exists: {e}", flush=True)
            return False

    # --- Migration Detection ---
    def _is_migration_transaction(self, logs: list) -> bool:
        """
        Check if transaction logs indicate a Pump.Fun → PumpSwap migration.

        Looks for:
        - "Instruction: Migrate" (Pump.Fun migration marker)
        - Pool initialization patterns
        - Excludes swaps (Buy/Sell instructions)
        - Excludes MigrateBondingCurveCreator (NOT a pool creation)
        """
        logs_text = ' '.join(logs or [])
        lowered = logs_text.lower()

        # Exclude swaps (Buy/Sell instructions)
        if 'Instruction: Buy' in logs_text or 'Instruction: Sell' in logs_text:
            return False

        # Filter out MigrateBondingCurveCreator - that's NOT a pool creation
        if 'MigrateBondingCurveCreator' in logs_text:
            return False

        # Must have Migrate instruction (Pump.Fun migration marker)
        if 'Instruction: Migrate' not in logs_text:
            return False

        # Match real migration log shapes, including the observed "Instruction: CreatePool".
        if not any(pattern in lowered for pattern in (
            'initialize',
            'create_pool',
            'createpool',
            'initializepool',
            PUMPSWAP_PROGRAM.lower(),
        )):
            return False

        return True

    def _debug_pumpswap_migration_skip(self, signature: str, logs: list) -> None:
        """Log why a PumpSwap-side websocket event was not classified as a migration."""
        try:
            logs_text = ' '.join(logs or [])
            lowered = logs_text.lower()
            reason = "unknown"

            if 'MigrateBondingCurveCreator' in logs_text:
                reason = 'migrate_bonding_curve_creator'
            elif 'Instruction: Migrate' not in logs_text:
                reason = 'no_migrate_instruction'
            elif 'Instruction: Buy' in logs_text or 'Instruction: Sell' in logs_text:
                reason = 'swap_not_migration'
            elif not any(pattern in lowered for pattern in (
                'initialize',
                'create_pool',
                'createpool',
                'initializepool',
                PUMPSWAP_PROGRAM.lower(),
            )):
                reason = 'no_pool_init_pattern'

            interesting = (
                'Instruction: Migrate' in logs_text
                or 'CreatePool' in logs_text
                or 'InitializePool' in logs_text
                or 'MigrateBondingCurveCreator' in logs_text
            )
            if interesting:
                premig_log(
                    f"[PUMPSWAP_SKIP] sig={signature[:16]} reason={reason} logs={json.dumps((logs or [])[:20])[:1200]}"
                )
        except Exception:
            pass

    def _is_pumpfun_create_candidate(self, logs: list) -> bool:
        """
        Cheap pre-filter for Pump.fun birth events.

        We only fetch the transaction when logs look like a CREATE and clearly are
        not buy/sell/migrate noise.
        """
        logs_text = " ".join(logs or [])
        lowered = logs_text.lower()

        if "instruction: migrate" in lowered or "migratebondingcurvecreator" in lowered:
            return False
        if "instruction: buy" in lowered or "instruction: sell" in lowered:
            return False
        if "instruction: create" in lowered:
            return True
        if "initializemint" in lowered or "initializemint2" in lowered:
            return True
        return False

    def _extract_birth_timestamp(self, tx_data: Optional[Dict]) -> str:
        """Return ISO timestamp for token birth using tx blockTime when available."""
        try:
            block_time = (tx_data or {}).get("blockTime")
            if block_time:
                return datetime.utcfromtimestamp(int(block_time)).isoformat() + "Z"
        except Exception:
            pass
        return datetime.utcnow().isoformat() + "Z"

    def _extract_birth_metadata(self, tx_data: Optional[Dict]) -> Tuple[Optional[str], Optional[str]]:
        """
        Best-effort extraction of token symbol/name from the CREATE transaction.

        Pump.fun CREATE transactions sometimes surface UTF-8 metadata strings directly in
        parsed instruction payloads or nested dict values. Keep this deliberately cheap and
        defensive: if we cannot extract clean values, downstream symbol fetch will backfill.
        """
        if not tx_data:
            return None, None

        def _clean_text(value: str, *, upper: bool = False) -> Optional[str]:
            if not isinstance(value, str):
                return None
            text = value.strip().replace("\x00", "")
            if not text or len(text) > 64:
                return None
            if not re.fullmatch(r"[A-Za-z0-9 _.\-/$]{1,64}", text):
                return None
            if upper and not re.fullmatch(r"[A-Z0-9._\-/$]{1,16}", text):
                return None
            return text

        symbol = None
        name = None

        def _walk(value):
            nonlocal symbol, name
            if symbol and name:
                return
            if isinstance(value, dict):
                for key, child in value.items():
                    lowered = str(key).lower()
                    if symbol is None and lowered == "symbol":
                        symbol = _clean_text(child, upper=True)
                    elif name is None and lowered == "name":
                        name = _clean_text(child)
                    else:
                        _walk(child)
            elif isinstance(value, list):
                for item in value:
                    _walk(item)

        try:
            _walk(tx_data)
        except Exception:
            return None, None

        return symbol, name

    async def _upsert_birth_metadata_cache(
        self,
        mint: str,
        symbol: Optional[str],
        name: Optional[str],
    ) -> None:
        """Persist launch metadata for immediate dashboard use when it is available."""
        if not symbol and not name:
            return
        # metadata_cache.symbol is NOT NULL — use name as fallback, skip if both absent
        if not symbol:
            symbol = name

        async with self.db_lock:
            try:
                conn = db_connect(DB_PATH, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                cursor = conn.cursor()
                now = int(time.time())
                cursor.execute(
                    """
                    INSERT INTO metadata_cache (mint, symbol, name, cached_at, cached_source)
                    VALUES (?, ?, ?, ?, 'pumpfun_birth')
                    ON CONFLICT(mint) DO UPDATE SET
                        symbol = COALESCE(metadata_cache.symbol, excluded.symbol),
                        name = COALESCE(metadata_cache.name, excluded.name),
                        cached_at = excluded.cached_at,
                        cached_source = CASE
                            WHEN COALESCE(metadata_cache.symbol, metadata_cache.name) IS NULL
                                THEN excluded.cached_source
                            ELSE metadata_cache.cached_source
                        END
                    """,
                    (mint, symbol, name, now),
                )
                conn.commit()
                conn.close()
            except Exception as e:
                log_print(f"[BIRTH] ⚠ Failed to persist metadata for {mint[:16]}...: {e}", flush=True)

    async def _insert_bonding_curve_token(
        self,
        mint: str,
        creator: Optional[str],
        created_at: str,
        *,
        bonding_curve_pda: Optional[str] = None,
        create_tx_signature: Optional[str] = None,
        symbol: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        """Insert a token at birth into token_analysis without creating duplicates."""
        async with self.db_lock:
            try:
                conn = db_connect(DB_PATH, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                cursor = conn.cursor()

                analyzed_at = time.time()
                birth_seen_at = int(analyzed_at)
                cursor.execute(
                    """
                    INSERT INTO token_analysis (
                        mint, created_at, analyzed_at, earliest_tx_creator,
                        pf_ws_creator,
                        bonding_curve_pda, create_tx_signature, source_platform,
                        lifecycle_stage, is_new, migration_signal_source,
                        migration_signal_updated_at, first_pre_migration_signal_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pumpfun', 'bonding_curve', 1, 'birth', ?, ?)
                    ON CONFLICT(mint) DO UPDATE SET
                        created_at = COALESCE(token_analysis.created_at, excluded.created_at),
                        analyzed_at = excluded.analyzed_at,
                        earliest_tx_creator = COALESCE(token_analysis.earliest_tx_creator, excluded.earliest_tx_creator),
                        pf_ws_creator = COALESCE(token_analysis.pf_ws_creator, excluded.pf_ws_creator),
                        bonding_curve_pda = COALESCE(token_analysis.bonding_curve_pda, excluded.bonding_curve_pda),
                        create_tx_signature = COALESCE(token_analysis.create_tx_signature, excluded.create_tx_signature),
                        source_platform = COALESCE(token_analysis.source_platform, excluded.source_platform),
                        lifecycle_stage = CASE
                            WHEN token_analysis.lifecycle_stage = 'migrated' THEN token_analysis.lifecycle_stage
                            ELSE 'bonding_curve'
                        END,
                        migration_signal_source = CASE
                            WHEN COALESCE(token_analysis.migration_signal_source, '') = '' THEN excluded.migration_signal_source
                            WHEN token_analysis.migration_signal_source = 'flow' THEN token_analysis.migration_signal_source
                            ELSE token_analysis.migration_signal_source
                        END,
                        migration_signal_updated_at = COALESCE(token_analysis.migration_signal_updated_at, excluded.migration_signal_updated_at),
                        first_pre_migration_signal_at = COALESCE(token_analysis.first_pre_migration_signal_at, excluded.first_pre_migration_signal_at),
                        is_new = 1
                    """,
                    (
                        mint,
                        created_at,
                        analyzed_at,
                        creator,
                        creator,
                        bonding_curve_pda,
                        create_tx_signature,
                        birth_seen_at,
                        birth_seen_at,
                    ),
                )
                conn.commit()
                conn.close()
                self._remember_recent_birth_token(mint, bonding_curve_pda)
                log_print(
                    f"[PREMIG_BIRTH_SEED] mint={mint[:6]} ts={birth_seen_at} source=birth",
                    flush=True,
                )
            except Exception as e:
                log_print(f"[BIRTH] ⚠ Failed to insert bonding-curve token {mint[:16]}...: {e}", flush=True)
                return

        await self._upsert_birth_metadata_cache(mint, symbol, name)

        import threading as _threading
        def _score_birth():
            try:
                import sqlite3 as _sq
                from src.core.token_prediction_builder import TokenPredictionBuilder
                conn2 = _sq.connect(DB_PATH, timeout=30)
                conn2.execute("PRAGMA journal_mode=WAL")
                TokenPredictionBuilder(DB_PATH).score_single(conn2, mint, 'BIRTH')
                conn2.close()
            except Exception as _e:
                log_print(f"[PREDICTION] ⚠ score_single BIRTH {mint[:16]}: {_e}", flush=True)
        _TOKEN_WORK_POOL.submit(_score_birth)

        try:
            from src.core.price_worker import PriceWorkerRegistry
            PriceWorkerRegistry(DB_PATH).register_token(mint, priority_level='HIGH')
        except Exception as e:
            log_print(f"[BIRTH] ⚠ Price tracking registration failed for {mint[:16]}...: {e}", flush=True)

        _spawn_symbol_fetch(mint, DB_PATH)

        event = {
            "type": "token_detected",
            "mint": mint,
            "creator": creator,
            "created_at": created_at,
            "detected_at": int(time.time()),
            "status": "bonding_curve",
            "source": "pumpfun_create",
            "lifecycle_stage": "bonding_curve",
        }
        if symbol:
            event["symbol"] = symbol
        if name:
            event["name"] = name
        _broadcast_to_flask(event)

    async def _ensure_pf_ws_creator(self, mint: str, reason: str = "premig") -> Optional[str]:
        """
        Populate pf_ws_creator from the Pump.fun CREATE tx using a single getTransaction RPC.

        Flow:
        create_tx_signature -> getTransaction -> strict CREATE validate -> infer creator.
        """
        try:
            async with self.db_lock:
                conn = db_connect(DB_PATH, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT create_tx_signature, pf_ws_creator, earliest_tx_creator
                    FROM token_analysis
                    WHERE mint = ?
                    LIMIT 1
                    """,
                    (mint,),
                )
                row = cursor.fetchone()
                conn.close()
        except Exception as e:
            log_print(f"[PF_WS_CREATOR] ⚠ DB read failed for {mint[:16]}...: {e}", flush=True)
            return None

        if not row:
            return None

        create_tx_signature = str(row[0]) if row[0] else None
        existing_pf_ws_creator = str(row[1]) if row[1] else None
        earliest_tx_creator = str(row[2]) if row[2] else None

        if existing_pf_ws_creator:
            _at_migration = reason.startswith("migration")
            _should_enqueue = _at_migration
            if not _should_enqueue:
                try:
                    _gate_row = db_connect(DB_PATH, timeout=5).execute(
                        "SELECT curve_complete FROM token_analysis WHERE mint = ? LIMIT 1", (mint,)
                    ).fetchone()
                    _should_enqueue = bool(_gate_row[0]) if _gate_row else False
                except Exception:
                    _should_enqueue = False
            if _should_enqueue:
                enqueue_source = "pf_ws_creator_existing_migration" if _at_migration else "pf_ws_creator_existing_curve_complete"
                await self._enqueue_creator_funding_job(
                    existing_pf_ws_creator,
                    mint=mint,
                    migration_timestamp=datetime.utcnow().isoformat() + "Z",
                    create_tx_signature=create_tx_signature,
                    delay_seconds=0,
                    source=enqueue_source,
                )
            return existing_pf_ws_creator

        # Fast path: PumpPortal already gave us the creator in-memory at birth — no RPC needed
        portal_creator = (self._portal_vsol.get(mint) or {}).get('creator')
        if portal_creator:
            log_print(f"[PF_WS_CREATOR] ⚡ Portal fast-path: creator={portal_creator[:8]}... for {mint[:8]}... trigger={reason}", flush=True)
            try:
                async with self.db_lock:
                    conn = db_connect(DB_PATH, timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("UPDATE token_analysis SET pf_ws_creator=? WHERE mint=?", (portal_creator, mint))
                    conn.commit()
                    conn.close()
            except Exception as e:
                log_print(f"[PF_WS_CREATOR] ⚠ Portal fast-path DB write failed: {e}", flush=True)
            await self._enqueue_creator_funding_job(
                portal_creator,
                mint=mint,
                migration_timestamp=datetime.utcnow().isoformat() + "Z",
                create_tx_signature=create_tx_signature,
                delay_seconds=0,
                source="pf_ws_creator_migration",
            )
            return portal_creator

        rpc_url = RPC_HTTP or os.environ.get('HELIUS_RPC_URL')
        analyzer = PostMigrationAnalyzer(mint, rpc_url=rpc_url)

        if not create_tx_signature:
            # Fallback: find creator via getSignaturesForAddress on the mint
            log_print(
                f"[PF_WS_CREATOR] ℹ No create_tx_signature for {mint[:8]}..., trying RPC fallback trigger={reason}",
                flush=True,
            )
            try:
                provenance = await analyzer.get_creator_from_earliest_tx()
                pf_ws_creator = provenance.get('creator') if provenance else None
                if not pf_ws_creator:
                    log_print(f"[PF_WS_CREATOR] ⚠ RPC fallback found no creator for {mint[:8]}...", flush=True)
                    return None
            except Exception as e:
                log_print(f"[PF_WS_CREATOR] ⚠ RPC fallback failed for {mint[:8]}...: {e}", flush=True)
                return None
        else:
            tx_data = await self._get_transaction_cached(create_tx_signature)
            if not tx_data:
                log_print(
                    f"[PF_WS_CREATOR] ⚠ Skip {mint[:8]}... reason=create_tx_unavailable trigger={reason}",
                    flush=True,
                )
                return None

            validation = analyzer._validate_pumpfun_create_tx(tx_data)
            if not validation.get("is_pumpfun_create"):
                log_print(
                    f"[PF_WS_CREATOR] ⚠ Skip {mint[:8]}... reason=create_tx_not_strict trigger={reason}",
                    flush=True,
                )
                return None

            pf_ws_creator = analyzer._infer_creator_from_tx(tx_data)
            if not pf_ws_creator:
                log_print(
                    f"[PF_WS_CREATOR] ⚠ Skip {mint[:8]}... reason=infer_failed trigger={reason}",
                    flush=True,
                )
                return None

        mismatch = bool(earliest_tx_creator and earliest_tx_creator != pf_ws_creator)

        try:
            async with self.db_lock:
                conn = db_connect(DB_PATH, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE token_analysis
                    SET pf_ws_creator = ?,
                        earliest_tx_creator = COALESCE(NULLIF(TRIM(earliest_tx_creator), ''), ?),
                        creator_mismatch = CASE
                            WHEN earliest_tx_creator IS NOT NULL
                             AND earliest_tx_creator != ''
                             AND earliest_tx_creator != ?
                            THEN 1 ELSE 0
                        END
                    WHERE mint = ?
                    """,
                    (pf_ws_creator, pf_ws_creator, pf_ws_creator, mint),
                )
                conn.commit()
                conn.close()
        except Exception as e:
            log_print(f"[PF_WS_CREATOR] ⚠ DB write failed for {mint[:16]}...: {e}", flush=True)
            return None

        status = "MISMATCH" if mismatch else "MATCH"
        rpc_display = earliest_tx_creator[:8] + "..." if earliest_tx_creator else "none"
        log_print(
            f"[PF_WS_CREATOR] ✅ {status} mint={mint[:8]}... trigger={reason} "
            f"pf_ws={pf_ws_creator[:8]}... rpc={rpc_display}",
            flush=True,
        )
        _at_migration = reason.startswith("migration")
        if not _at_migration:
            try:
                _gate_row = db_connect(DB_PATH, timeout=5).execute(
                    "SELECT curve_complete FROM token_analysis WHERE mint = ? LIMIT 1", (mint,)
                ).fetchone()
                _curve_complete = bool(_gate_row[0]) if _gate_row else False
            except Exception:
                _curve_complete = False
        else:
            _curve_complete = True  # at migration, curve is complete by definition

        if _curve_complete:
            enqueue_source = "pf_ws_creator_migration" if _at_migration else "pf_ws_creator_curve_complete"
            await self._enqueue_creator_funding_job(
                pf_ws_creator,
                mint=mint,
                migration_timestamp=datetime.utcnow().isoformat() + "Z",
                create_tx_signature=create_tx_signature,
                delay_seconds=0,
                source=enqueue_source,
            )
        else:
            log_print(
                f"[PF_WS_CREATOR] ⏭️ Skip funding enqueue mint={mint[:8]}... reason=curve_not_complete",
                flush=True,
            )

        # Phase 1 dual-write: upsert creator_profile and conditionally enqueue baseline job.
        # Fire-and-forget — never raises, never blocks the hot path.
        try:
            from src.creators.migration_bridge import dual_write_creator_resolved
            from src.creators.repository import CreatorRepository
            from src.creators.helius_watch import register_creator_address
            _repo = CreatorRepository(CREATOR_DB_PATH, self.db_lock)
            asyncio.create_task(dual_write_creator_resolved(
                pf_ws_creator, mint,
                create_tx_signature=create_tx_signature,
                reason=reason,
                repo=_repo,
                register_webhook_fn=register_creator_address,
            ))
        except Exception as _dw_e:
            log_print(f"[PF_WS_CREATOR] ⚠ dual_write enqueue failed: {_dw_e}", flush=True)

        return pf_ws_creator

    def _token_needs_creator_backfill(self, mint: str) -> bool:
        """Return True only when both creator fields are still missing."""
        if not mint:
            return False
        try:
            conn = db_connect(DB_PATH, timeout=15)
            cursor = conn.cursor()
            row = cursor.execute(
                """
                SELECT pf_ws_creator, earliest_tx_creator
                FROM token_analysis
                WHERE mint = ?
                LIMIT 1
                """,
                (mint,),
            ).fetchone()
            conn.close()
        except Exception as e:
            log_print(f"[PF_WS_CREATOR] ⚠ Creator backfill check failed for {mint[:16]}...: {e}", flush=True)
            return True

        if not row:
            return True

        pf_ws_creator = str(row[0]).strip() if row[0] else ""
        earliest_tx_creator = str(row[1]).strip() if row[1] else ""
        return not (pf_ws_creator or earliest_tx_creator)

    def _get_resolved_creator_for_mint(self, mint: str) -> Tuple[Optional[str], Optional[str]]:
        """Return the best resolved creator plus create-tx signature for a mint."""
        if not mint:
            return None, None
        try:
            conn = db_connect(DB_PATH, timeout=15)
            cursor = conn.cursor()
            row = cursor.execute(
                """
                SELECT pf_ws_creator, earliest_tx_creator, create_tx_signature
                FROM token_analysis
                WHERE mint = ?
                LIMIT 1
                """,
                (mint,),
            ).fetchone()
            conn.close()
        except Exception as e:
            log_print(f"[FUNDING_QUEUE] ⚠ Creator lookup failed for {mint[:16]}...: {e}", flush=True)
            return None, None

        if not row:
            return None, None

        pf_ws_creator = str(row[0]).strip() if row[0] else ""
        earliest_tx_creator = str(row[1]).strip() if row[1] else ""
        create_tx_signature = str(row[2]).strip() if row[2] else None
        return (pf_ws_creator or earliest_tx_creator or None), create_tx_signature

    async def handle_birth(self, signature: str, logs: list):
        """Process a Pump.fun token birth event."""
        if signature in self.processing_launches or signature in self.completed_launches:
            return

        self.processing_launches.add(signature)
        try:
            tx_data = await self._get_transaction_cached(signature)
            if not tx_data:
                return

            mint = await self._extract_mint_from_tx(tx_data)
            if not mint:
                return

            analyzer = PostMigrationAnalyzer(mint, rpc_url=RPC_HTTP)
            validation = analyzer._validate_pumpfun_create_tx(tx_data)
            if not validation.get("is_pumpfun_create"):
                return

            creator = analyzer._infer_creator_from_tx(tx_data)
            created_at = self._extract_birth_timestamp(tx_data)
            bonding_curve_pda = validation.get("bonding_curve")
            symbol, name = self._extract_birth_metadata(tx_data)

            await self._insert_bonding_curve_token(
                mint,
                creator,
                created_at,
                bonding_curve_pda=bonding_curve_pda,
                create_tx_signature=signature,
                symbol=symbol,
                name=name,
            )
            self.completed_launches.add(signature)
            self.seen_mints.add(mint)
            meta_suffix = f" symbol={symbol}" if symbol else ""
            log_print(f"[BIRTH] ✅ Pump.fun launch detected: {mint} creator={creator[:8] + '...' if creator else 'unknown'}{meta_suffix}", flush=True)

            # Immediately watch the bonding curve — no need to wait for momentum threshold
            if bonding_curve_pda:
                await self.watch_bonding_curve(bonding_curve_pda)
        except Exception as e:
            log_print(f"[BIRTH] ⚠ Error handling launch {signature[:16]}...: {e}", flush=True)
        finally:
            self.processing_launches.discard(signature)

    async def _fetch_mint_from_transaction(self, signature: str) -> Optional[str]:
        """
        Fetch full transaction and extract token mint.

        Strategy:
        1. Try postTokenBalances first (most reliable)
        2. Fall back to accountKeys if postTokenBalances missing
        3. Filter out system programs
        4. Accept 43 or 44 char addresses (Pump.Fun token length variance)

        Includes retry logic for newly-confirmed transactions that may have indexing delays.
        Uses RPC failover chain: Primary QuickNode -> Secondary QuickNode -> Helius -> Public.
        """
        max_retries = 12
        retry_delays = [0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0]  # Extended backoff for slow indexing

        for attempt in range(max_retries):
            try:
                # Use discovery RPC tier for mint extraction (critical path)
                data = await self.call_discovery_rpc(
                    "getTransaction",
                    [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
                    timeout=15
                )

                if not data or "result" not in data or not data["result"]:
                    # Transaction not indexed yet, retry with backoff
                    if attempt < max_retries - 1:
                        log_print(f"[MINT] 📝 Transaction indexing delay, retry {attempt + 1}/{max_retries}...", flush=True)
                        await asyncio.sleep(retry_delays[attempt])
                        continue
                    log_print(f"[MINT] ⚠ Transaction not found after retries: {signature}", flush=True)
                    return None

                tx_data = data["result"]
                meta = tx_data.get("meta", {})

                # Strategy 1: Try postTokenBalances first
                post_balances = meta.get("postTokenBalances", [])
                for balance in post_balances:
                    mint = balance.get("mint", "")
                    # Accept valid token mints (43-44 chars), exclude SOL
                    if mint and len(mint) in (43, 44) and mint != "So11111111111111111111111111111111111111112":
                        return mint

                # Strategy 2: Fall back to accountKeys
                message = tx_data.get("transaction", {}).get("message", {})
                accounts = message.get("accountKeys", [])

                system_programs = {
                    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # Pump.Fun
                    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # PumpSwap
                    "11111111111111111111111111111111",               # System program
                    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",  # ATA program
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Token program
                    "So11111111111111111111111111111111111111112",   # Wrapped SOL
                }

                for account in accounts[:10]:
                    if len(account) in (43, 44) and account not in system_programs:
                        return account

                log_print(f"[MINT] ⚠ No valid mint found in {signature}", flush=True)
                return None

            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    log_print(f"[MINT] ⏱️  Timeout, retrying {attempt + 1}/{max_retries}...", flush=True)
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                log_print(f"[MINT] ⚠ Timeout after retries: {signature}", flush=True)
                return None
            except Exception as e:
                if attempt < max_retries - 1:
                    log_print(f"[MINT] ⚠ Error on attempt {attempt + 1}, retrying: {e}", flush=True)
                    await asyncio.sleep(retry_delays[attempt])
                    continue
                log_print(f"[MINT] ⚠ Error fetching {signature}: {e}", flush=True)
                return None
        
        return None

    async def _extract_mint_from_tx(self, tx_data: Dict) -> Optional[str]:
        """
        Extract token mint from transaction data (no RPC call needed).

        Strategies:
        1. Try postTokenBalances first (most reliable)
        2. Fall back to accountKeys if postTokenBalances missing
        3. Filter out system programs
        """
        if not tx_data:
            return None

        meta = tx_data.get("meta", {})

        # Strategy 1: Try postTokenBalances first
        post_balances = meta.get("postTokenBalances", [])
        for balance in post_balances:
            mint = balance.get("mint", "")
            if mint and len(mint) in (43, 44) and mint != "So11111111111111111111111111111111111111112":
                return mint

        # Strategy 2: Fall back to accountKeys
        message = tx_data.get("transaction", {}).get("message", {})
        accounts = message.get("accountKeys", [])

        system_programs = {
            "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
            "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
            "11111111111111111111111111111111",
            "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "So11111111111111111111111111111111111111112",
        }

        for account in accounts[:10]:
            if len(account) in (43, 44) and account not in system_programs:
                return account

        # Debug: log what we found
        log_print(f"[MINT_EXTRACT] postTokenBalances count: {len(post_balances)}, accountKeys count: {len(accounts)}", flush=True)
        if post_balances:
            log_print(f"[MINT_EXTRACT] First postTokenBalance: {post_balances[0] if post_balances else 'None'}", flush=True)
        if accounts:
            log_print(f"[MINT_EXTRACT] First 5 accountKeys: {accounts[:5]}", flush=True)

        return None

    async def _extract_pool_from_migration_tx(self, signature: str) -> List[str]:
        """
        Extract ALL PumpSwap pool candidates from migration transaction.
        
        Handles both int indices and string pubkey formats from different RPC providers.
        Returns list of ALL candidate accounts (not filtered, not validated).
        Caller is responsible for validation and filtering.
        """
        max_retries = 3
        retry_delays = [1.0, 3.0, 5.0]

        for attempt in range(max_retries):
            try:
                # Use discovery RPC tier (still part of pool detection pipeline)
                data = await self.call_discovery_rpc(
                    "getTransaction",
                    [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
                    timeout=10
                )

                if not data or "result" not in data or not data["result"]:
                    return []

                tx_data = data["result"]
                message = tx_data.get("transaction", {}).get("message", {})
                account_keys = message.get("accountKeys", [])
                
                if not account_keys:
                    return []
                
                meta = tx_data.get("meta", {})
                inner = meta.get("innerInstructions", [])
                
                candidates = set()
                
                # Find program index
                program_idx = None
                for i, acc in enumerate(account_keys):
                    if acc == PUMPSWAP_PROGRAM:
                        program_idx = i
                        break
                
                # Extract ALL accounts from ALL PumpSwap instructions
                if program_idx is not None:
                    for group in inner:
                        for ix in group.get("instructions", []):
                            if ix.get("programIdIndex") != program_idx:
                                continue

                            raw_accounts = ix.get("accounts", [])
                            if raw_accounts:
                                log_print(
                                    f"[IX_ACCOUNTS_SHAPE] types={[type(x).__name__ for x in raw_accounts[:5]]} sample={raw_accounts[:5]}",
                                    flush=True
                                )

                            for acc_ref in raw_accounts:
                                # Case 1: classic indexed format (int)
                                if isinstance(acc_ref, int):
                                    if 0 <= acc_ref < len(account_keys):
                                        candidates.add(account_keys[acc_ref])
                                # Case 2: already-normalized pubkey string
                                elif isinstance(acc_ref, str):
                                    if len(acc_ref) >= 32:
                                        candidates.add(acc_ref)
                else:
                    # Fallback: scan ALL instructions (not just by index)
                    # Some migrations don't include PumpSwap in accountKeys
                    for group in inner:
                        for ix in group.get("instructions", []):
                            raw_accounts = ix.get("accounts", [])
                            if raw_accounts:
                                log_print(
                                    f"[IX_ACCOUNTS_SHAPE] types={[type(x).__name__ for x in raw_accounts[:5]]} sample={raw_accounts[:5]}",
                                    flush=True
                                )

                            for acc_ref in raw_accounts:
                                # Case 1: classic indexed format (int)
                                if isinstance(acc_ref, int):
                                    if 0 <= acc_ref < len(account_keys):
                                        candidates.add(account_keys[acc_ref])
                                # Case 2: already-normalized pubkey string
                                elif isinstance(acc_ref, str):
                                    if len(acc_ref) >= 32:
                                        candidates.add(acc_ref)

                # Exclude system programs
                SKIP = {
                    PUMPSWAP_PROGRAM,
                    PUMPFUN_PROGRAM,
                    "11111111111111111111111111111111",
                    "So11111111111111111111111111111111111111112",
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
                }
                candidates = {c for c in candidates if c not in SKIP}

                # Prevent pathological TX explosions (some migrations have 100+ accounts)
                candidate_list = list(candidates)
                if len(candidate_list) > 50:
                    log_print(f"[EXTRACT_POOL] Limiting candidates from {len(candidate_list)} to 50", flush=True)
                    candidate_list = candidate_list[:50]

                return candidate_list

            except Exception as e:
                if attempt < max_retries - 1:
                    log_print(f"[POOL] ⚠ Error extracting pool (attempt {attempt + 1}/{max_retries}): {e}", flush=True)
                    await asyncio.sleep(retry_delays[attempt])
                else:
                    log_print(f"[POOL_ERROR] Failed to extract pool address after {max_retries} attempts: {e}", flush=True)
                    return []

        return []

    async def batch_validate_candidates_with_reasons(self, candidates: list, strict_mode: bool = True) -> Tuple[list, Dict[str, str]]:
        """
        Batch validate all candidates and return both valid addresses and rejection reasons.

        Returns:
            Tuple of (valid_addresses, rejection_map)
            - valid_addresses: List of valid pool addresses
            - rejection_map: Dict mapping rejected addr -> reason
                            (reason = "account_not_found", "wrong_owner", "shared_account", etc.)
        """
        if not candidates:
            return [], {}

        try:
            result = await self.call_discovery_rpc(
                "getMultipleAccounts",
                [candidates, {"encoding": "base64", "commitment": "processed"}],
                timeout=10
            )

            if not result or "result" not in result:
                log_print(f"[BATCH_VALIDATE_REASONS] ❌ RPC call failed or empty result", flush=True)
                return [], {}

            PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
            # Vault token accounts are owned by SPL Token or Token-2022 — accept both
            ALLOWED_POOL_OWNERS = {
                PUMPSWAP_PROGRAM,
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
                "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
            }
            values = result.get("result", {}).get("value", [])

            log_print(f"[BATCH_VALIDATE_REASONS] Validating {len(candidates)} candidates (strict_mode={strict_mode})", flush=True)

            valid = []
            rejections = {}

            for addr, acc in zip(candidates, values):
                addr_short = addr[:16] if isinstance(addr, str) else str(addr)[:16]

                # Check 1: Account must exist
                if not acc:
                    reason = "account_not_found"
                    log_print(f"[CANDIDATE_REJECTED] addr={addr_short}... reason={reason}", flush=True)
                    rejections[addr] = reason
                    continue

                # Check 2: Owner must be PumpSwap pool program or a token vault program (SPL/Token-2022)
                owner = acc.get("owner")
                if owner not in ALLOWED_POOL_OWNERS:
                    reason = "wrong_owner"
                    log_print(f"[CANDIDATE_REJECTED] addr={addr_short}... reason={reason} owner={owner[:16] if owner else 'null'}...", flush=True)
                    rejections[addr] = reason
                    continue

                # Check 3: Shared account check (always enforce, never accept ADyA-like accounts)
                try:
                    from src.core.pool_discovery import PoolDiscovery
                    db_path = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), '../../database/flex_complete_database.db'))
                    pd = PoolDiscovery(db_path, "")

                    # Use stricter threshold in strict mode
                    threshold = 2 if strict_mode else 3
                    is_shared = await pd._is_shared_account(addr, threshold=threshold)
                    if is_shared:
                        reason = "shared_account"
                        log_print(f"[CANDIDATE_REJECTED] addr={addr_short}... reason={reason} threshold={threshold}", flush=True)
                        rejections[addr] = reason
                        continue

                except Exception as check_error:
                    reason = "shared_check_failed"
                    log_print(f"[CANDIDATE_REJECTED] addr={addr_short}... reason={reason} error={str(check_error)[:40]}", flush=True)
                    # Only skip in strict mode; in retry mode, accept if check fails
                    if strict_mode:
                        rejections[addr] = reason
                        continue
                    log_print(f"[CANDIDATE_ACCEPTED] addr={addr_short}... (shared check failed but accepting in retry mode)", flush=True)
                    valid.append(addr)
                    continue

                # All checks passed — return rich object so registration skips re-fetch
                log_print(f"[CANDIDATE_ACCEPTED] addr={addr_short}... passed all validation checks", flush=True)
                valid.append({"address": addr, "account_info": acc, "owner": owner})
                self._validated_account_cache[addr] = acc

            log_print(f"[BATCH_VALIDATE_REASONS] Result: {len(valid)} valid, {len(rejections)} rejected from {len(candidates)} input", flush=True)
            return valid, rejections

        except Exception as e:
            log_print(f"[BATCH_VALIDATE_REASONS] ❌ Fatal error during validation: {e}", flush=True)
            return [], {}

    async def batch_validate_candidates(self, candidates: list, strict_mode: bool = True) -> list:
        """
        Batch validate all candidates with single RPC call.

        Returns list of valid pool addresses owned by PUMPSWAP program.
        Filters out shared accounts (known PDAs used across many tokens).

        Args:
            candidates: List of account addresses to validate
            strict_mode: If True, apply all filters (first pass)
                        If False, looser validation for retries (never allow shared accounts)
        """
        if not candidates:
            return []

        try:
            result = await self.call_discovery_rpc(
                "getMultipleAccounts",
                [candidates, {"encoding": "base64", "commitment": "processed"}],
                timeout=10
            )

            if not result or "result" not in result:
                log_print(f"[BATCH_VALIDATE] ❌ RPC call failed or empty result", flush=True)
                return []

            PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
            # Vault token accounts are owned by SPL Token or Token-2022 — accept both
            ALLOWED_POOL_OWNERS = {
                PUMPSWAP_PROGRAM,
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # SPL Token
                "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",  # Token-2022
            }
            values = result.get("result", {}).get("value", [])

            log_print(f"[BATCH_VALIDATE] Validating {len(candidates)} candidates (strict_mode={strict_mode})", flush=True)

            valid = []
            for addr, acc in zip(candidates, values):
                addr_short = addr[:16] if isinstance(addr, str) else str(addr)[:16]

                # Check 1: Account must exist
                if not acc:
                    log_print(f"[CANDIDATE_REJECTED] addr={addr_short}... reason=account_not_found", flush=True)
                    continue

                # Check 2: Owner must be PumpSwap pool program or a token vault program (SPL/Token-2022)
                owner = acc.get("owner")
                if owner not in ALLOWED_POOL_OWNERS:
                    log_print(f"[CANDIDATE_REJECTED] addr={addr_short}... reason=wrong_owner owner={owner[:16] if owner else 'null'}...", flush=True)
                    continue

                # Check 3: Shared account check (always enforce, never accept ADyA-like accounts)
                try:
                    from src.core.pool_discovery import PoolDiscovery
                    db_path = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), '../../database/flex_complete_database.db'))
                    pd = PoolDiscovery(db_path, "")

                    # Use stricter threshold in strict mode
                    threshold = 2 if strict_mode else 3
                    is_shared = await pd._is_shared_account(addr, threshold=threshold)
                    if is_shared:
                        log_print(f"[CANDIDATE_REJECTED] addr={addr_short}... reason=shared_account threshold={threshold}", flush=True)
                        continue

                except Exception as check_error:
                    log_print(f"[CANDIDATE_REJECTED] addr={addr_short}... reason=shared_check_failed error={str(check_error)[:40]}", flush=True)
                    # Only skip in strict mode; in retry mode, accept if check fails
                    if strict_mode:
                        continue
                    log_print(f"[CANDIDATE_ACCEPTED] addr={addr_short}... (shared check failed but accepting in retry mode)", flush=True)
                    valid.append(addr)
                    continue

                # All checks passed
                log_print(f"[CANDIDATE_ACCEPTED] addr={addr_short}... passed all validation checks", flush=True)
                valid.append(addr)

            log_print(f"[BATCH_VALIDATE] Result: {len(valid)} valid candidates from {len(candidates)} input", flush=True)
            return valid

        except Exception as e:
            log_print(f"[BATCH_VALIDATE] ❌ Fatal error during validation: {e}", flush=True)
            return []

    async def resolve_pool_from_tx(self, tx_data: Dict) -> Optional[str]:
        """
        Full pipeline: extract → filter → validate → return best pool
        """
        if not tx_data:
            return None

        candidates = await self._extract_pool_from_tx(tx_data)

        if not candidates:
            log_print("[RESOLVE_POOL] No candidates extracted", flush=True)
            return None

        log_print(f"[RESOLVE_POOL] Extracted {len(candidates)} candidates, filtering...", flush=True)

        # Ensure all candidates are strings (RPC may return dicts or other types)
        candidates = [str(c) if not isinstance(c, str) else c for c in candidates]

        # Pre-filter obvious junk (saves RPC bandwidth)
        candidates = [
            c for c in candidates
            if isinstance(c, str) and len(c) >= 32 and not c.startswith("111")
        ]

        if not candidates:
            log_print("[RESOLVE_POOL] All candidates filtered out", flush=True)
            return None

        log_print(f"[RESOLVE_POOL] After pre-filter: {len(candidates)} candidates, batch validating (strict mode)...", flush=True)

        # First pass: strict validation
        valid = await self.batch_validate_candidates(candidates, strict_mode=True)

        # Fallback: if strict mode found nothing, try again with looser validation
        if not valid:
            log_print("[RESOLVE_POOL] ⚠️  No valid pools in strict mode, trying looser validation for retry recovery...", flush=True)
            valid = await self.batch_validate_candidates(candidates, strict_mode=False)

            if not valid:
                log_print("[RESOLVE_POOL] ❌ No valid pools found even with loose validation", flush=True)
                return None

            log_print(f"[RESOLVE_POOL] ✅ Found {len(valid)} candidates in loose mode", flush=True)

        log_print(f"[RESOLVE_POOL] Proceeding with {len(valid)} valid candidates to selection phase", flush=True)

        # Deterministic selection from multiple valid pools
        pool = self.select_best_pool(valid, tx_data)
        if pool:
            log_print(f"[RESOLVE_POOL] ✅ Selected pool: {pool[:16]}...", flush=True)
            return pool

        log_print("[RESOLVE_POOL] ❌ No pool selected from candidates", flush=True)
        return None

    async def resolve_pool_from_signature(self, signature: str) -> Optional[str]:
        """
        Full pipeline: extract from TX signature → filter → validate → return best pool

        Uses TX cache for deduplication, retry/backoff, and singleflight.
        """
        # Fetch tx_data using cache (retry/backoff, dedup, singleflight)
        tx_data = await self._get_transaction_cached(signature)

        if not tx_data:
            log_print("[RESOLVE_POOL_SIG] Could not fetch transaction", flush=True)
            return None

        # Extract candidates from CACHED tx_data (NOT a new RPC call)
        candidates = await self._extract_pool_from_tx(tx_data)

        if not candidates:
            log_print("[RESOLVE_POOL_SIG] No candidates extracted", flush=True)
            return None

        log_print(f"[RESOLVE_POOL_SIG] Extracted {len(candidates)} candidates, filtering...", flush=True)

        # Ensure all candidates are strings (RPC may return dicts or other types)
        candidates = [str(c) if not isinstance(c, str) else c for c in candidates]

        # Pre-filter obvious junk
        candidates = [
            c for c in candidates
            if isinstance(c, str) and len(c) >= 32 and not c.startswith("111")
        ]

        if not candidates:
            log_print("[RESOLVE_POOL_SIG] All candidates filtered out", flush=True)
            return None

        log_print(f"[RESOLVE_POOL_SIG] After pre-filter: {len(candidates)} candidates, batch validating...", flush=True)

        valid = await self.batch_validate_candidates(candidates)

        if not valid:
            log_print("[RESOLVE_POOL_SIG] No valid pools found after validation", flush=True)
            return None

        # Deterministic selection requires TX structure
        if not tx_data:
            log_print("[RESOLVE_POOL_SIG] ⚠️  No TX data — cannot deterministically select pool", flush=True)
            return None

        pool = self.select_best_pool(valid, tx_data)
        if pool:
            log_print(f"[RESOLVE_POOL_SIG] ✅ Selected pool: {pool[:16]}...", flush=True)
            return pool

        return None

    def select_best_pool(self, candidates: list, tx_data: dict) -> str:
        """
        Choose the correct pool PDA from validated candidates using scoring.

        Multiple accounts can be owned by PumpSwap:
        - pool PDA ✅ (correct)
        - LP mint ❌
        - vault ❌

        Scoring prioritizes:
        1. Proximity to token mint + SOL mint (strongest signal)
        2. Earliest appearance in accountKeys
        3. Frequency in inner instructions
        """
        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0]

        try:
            message = tx_data.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])
            meta = tx_data.get("meta", {})
            inner = meta.get("innerInstructions", [])

            SOL_MINT = "So11111111111111111111111111111111111111112"

            # Build scores for each candidate (ensure all are strings)
            scores = {}
            for candidate in candidates:
                # Ensure candidate is string
                candidate_str = str(candidate) if not isinstance(candidate, str) else candidate
                score = 0

                # Score 0: Proximity to SOL mint (highest priority - strong signal of real pool)
                # Real pools appear near their token mint and SOL in the account list
                if SOL_MINT in account_keys:
                    sol_index = account_keys.index(SOL_MINT)
                    if candidate_str in account_keys:
                        candidate_index = account_keys.index(candidate_str)
                        # Distance: pools are usually within 5 slots of SOL
                        distance = abs(candidate_index - sol_index)
                        if distance <= 5:
                            score += 20  # Strong bonus for proximity to SOL
                            log_print(f"[SELECT_POOL] {candidate_str[:16]}... has SOL proximity bonus (distance={distance})", flush=True)

                # Score 1: earliest appearance in accountKeys (priority 5)
                if candidate_str in account_keys:
                    score += 5 + (100 - account_keys.index(candidate_str))

                # Score 2: frequency in inner instructions (priority 3)
                frequency = 0
                for group in inner:
                    for ix in group.get("instructions", []):
                        if candidate_str in ix.get("accounts", []):
                            frequency += 1
                score += frequency * 3

                scores[candidate_str] = score
                log_print(f"[SELECT_POOL] Score {candidate_str[:16]}...: {score}", flush=True)

            # Return highest scoring candidate
            if not scores:
                log_print("[SELECT_POOL] ❌ No candidates scored", flush=True)
                return None

            best = max(scores.items(), key=lambda x: x[1])[0]
            log_print(f"[SELECT_POOL] Selected by scoring: {best[:16]}... (score: {scores[best]})", flush=True)
            return best

        except Exception as e:
            log_print(f"[SELECT_POOL] Error analyzing TX structure: {e}", flush=True)

        # No deterministic match found — strict mode
        log_print("[SELECT_POOL] ❌ No deterministic match found in TX accountKeys", flush=True)
        return None

    async def _extract_pool_from_tx(self, tx_data: Dict) -> List[str]:
        """
        Extract pool candidates from transaction data using INSTRUCTION-FOCUSED extraction.
        
        This matches the retry path's parse_candidates_from_cached_tx() logic:
        1. Extract accounts referenced by top-level instructions
        2. Extract accounts referenced by inner instructions
        3. Deduplicate and filter system programs
        
        This is more targeted than extracting all accounts, giving us a focused candidate set
        that matches what the retry path sees.
        """
        if not tx_data:
            return []

        try:
            message = tx_data.get("transaction", {}).get("message", {})
            meta = tx_data.get("meta", {})
            
            raw_account_keys = message.get("accountKeys", []) or []
            loaded_addrs = meta.get("loadedAddresses", {}) or {}
            loaded_writable = loaded_addrs.get("writable", []) or []
            loaded_readonly = loaded_addrs.get("readonly", []) or []

            # Normalize all accounts into a single ordered list
            all_accounts = [
                str(a.get("pubkey") if isinstance(a, dict) else a)
                for a in (raw_account_keys + loaded_writable + loaded_readonly)
            ]

            if not all_accounts:
                log_print(f"[_EXTRACT_POOL_FROM_TX] No accounts found in TX", flush=True)
                return []

            SYSTEM_PROGRAMS = {
                "11111111111111111111111111111111",
                "ComputeBudget111111111111111111111111111111",
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                "So11111111111111111111111111111111111111112",
                "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
            }

            SKIP_ACCOUNTS = {
                "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf",
                "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw",
            }

            def resolve_instruction_accounts(ix_obj):
                """Return concrete pubkeys referenced by one instruction."""
                resolved = []

                # jsonParsed often has integer account indices
                if isinstance(ix_obj, dict) and "accounts" in ix_obj:
                    for ref in ix_obj.get("accounts", []):
                        if isinstance(ref, int):
                            if 0 <= ref < len(all_accounts):
                                resolved.append(all_accounts[ref])
                        elif isinstance(ref, str):
                            resolved.append(ref)

                # Sometimes parsed instructions expose programId directly
                program_id = ix_obj.get("programId") if isinstance(ix_obj, dict) else None
                if program_id:
                    resolved.append(str(program_id))

                return resolved

            # 1) Top-level instruction accounts
            instruction_accounts = []
            top_level_ix = message.get("instructions", []) or []
            for ix in top_level_ix:
                instruction_accounts.extend(resolve_instruction_accounts(ix))

            # 2) Inner instruction accounts
            inner_ix_groups = meta.get("innerInstructions", []) or []
            for group in inner_ix_groups:
                for ix in group.get("instructions", []) or []:
                    instruction_accounts.extend(resolve_instruction_accounts(ix))

            # Deduplicate while preserving order
            seen = set()
            candidates = []
            for acc in instruction_accounts:
                if not acc or acc in seen:
                    continue
                if acc in SYSTEM_PROGRAMS or acc in SKIP_ACCOUNTS:
                    continue
                seen.add(acc)
                candidates.append(acc)

            # Fallback only if instruction parsing found nothing
            if not candidates:
                # Fall back to full account list (all_accounts filtered)
                log_print(
                    f"[_EXTRACT_POOL_FROM_TX] No instruction-derived accounts, falling back to full list",
                    flush=True
                )
                seen = set()
                for acc in all_accounts:
                    if not acc or acc in seen:
                        continue
                    if acc in SYSTEM_PROGRAMS or acc in SKIP_ACCOUNTS:
                        continue
                    seen.add(acc)
                    candidates.append(acc)
            else:
                log_print(
                    f"[_EXTRACT_POOL_FROM_TX] Extracted {len(candidates)} instruction-derived candidates",
                    flush=True
                )

            # Prevent pathological TX explosions
            if len(candidates) > 50:
                log_print(f"[_EXTRACT_POOL_FROM_TX] Limiting candidates from {len(candidates)} to 50", flush=True)
                candidates = candidates[:50]

            return candidates

        except Exception as e:
            log_print(f"[_EXTRACT_POOL_FROM_TX] Error: {e}", flush=True)
            return []

    async def _get_pool_address(self, token_mint: str, signature: str) -> Optional[str]:
        """Get pool address from database only.
        
        Pool discovery happens in _process_migration_with_mint via:
        1. Migration TX scan (stage 1)
        2. Scheduled retry with program-account discovery (stage 2)
        
        This method only reads from DB - does not attempt discovery.
        """
        try:
            conn = db_connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute("SELECT pool_address FROM token_analysis WHERE mint = ?", (token_mint,))
            row = cursor.fetchone()
            conn.close()
            
            if row and row[0]:
                return row[0]
            
            # No pool in DB - wait for retries or return None
            # Pool discovery is handled in _process_migration_with_mint
            return None
            
        except Exception as e:
            return None

    async def _extract_price_from_transaction(self, signature: str, token_mint: str) -> Optional[tuple]:
        """
        Extract on-chain price from pool vault balances.

        Strategy:
        1. Get pool address (from DB or extract from transaction)
        2. Retry on-chain price extraction with small delays (hydration guard)
        3. Fall back to DexScreener only after retries exhausted

        Returns: (price_usd, market_cap_usd, source, liquidity_usd) or None
        """
        try:
            # Get or extract pool address
            pool_address = await self._get_pool_address(token_mint, signature)

            if pool_address:
                # HYDRATION GUARD: Retry on-chain extraction (pool data may not be ready immediately)
                # Pools need ~200-400ms to populate balances after registration
                for attempt in range(3):
                    result = await self._get_price_from_pool_account(pool_address, token_mint)
                    if result is not None:
                        price, market_cap, liquidity_usd = result
                        self.price_stats['onchain_success'] += 1
                        return (price, market_cap, "onchain", liquidity_usd)

                    # Retry delay: 200ms initially, then 300ms, then 400ms
                    if attempt < 2:
                        delay = 0.2 + (attempt * 0.1)
                        await asyncio.sleep(delay)

            # Fall back to DexScreener only after all retries exhausted
            self.price_stats['dexscreener_fallback'] += 1
            fallback_rate = self.price_stats['dexscreener_fallback'] / (self.price_stats['onchain_success'] + self.price_stats['dexscreener_fallback'])
            log_print(f"[PRICE_FALLBACK] mint={token_mint[:16]}... reason=onchain_failed (fallback_rate={fallback_rate:.2%})", flush=True)
            _wstrace('ONCHAIN_FAILED', token_mint, f"fallback_rate={fallback_rate:.2%}")
            result = await self._fetch_dexscreener_price(token_mint)
            if result is not None:
                price, market_cap, liquidity_usd = result
                return (price, market_cap, "dexscreener", liquidity_usd)

            return None

        except Exception as e:
            log_print(f"[PRICE_ERROR] Failed to extract price for {token_mint}: {e}", flush=True)
            return None

    async def _find_pool_account(self, token_mint: str) -> Optional[str]:
        """
        DEPRECATED: Use PoolDetector.detect_pool_from_tx() instead.

        This method was a legacy fallback before hardened pool detection.
        It has fundamental issues:
        - Returns token account owner, not pool PDA
        - Doesn't validate with parser
        - Causes "Unknown pool program owner" errors

        Do not use.
        """
        raise NotImplementedError(
            "Legacy _find_pool_account() is deprecated. "
            "Use PoolDetector.detect_pool_from_tx() for all pool discovery."
        )

    async def _get_price_from_pool_account(self, pool_address: str, token_mint: str) -> Optional[tuple]:
        """
        Get price by querying pool account's token and SOL balances.

        PumpSwap pools store liquidity in WSOL (wrapped SOL) token accounts, not native lamports.
        We query for both WSOL and the token mint, then calculate price from the balance ratio.

        Returns: (price_usd, market_cap_usd, liquidity_usd) or None
        """
        try:
            # Query WSOL (wrapped SOL) token accounts owned by this pool
            # WSOL mint: So11111111111111111111111111111111111111112
            wsol_mint = "So11111111111111111111111111111111111111112"

            # Use background RPC tier (price extraction is not critical path)
            data = await self.call_background_rpc(
                "getTokenAccountsByOwner",
                [pool_address, {"mint": wsol_mint}, {"encoding": "jsonParsed"}]
            )

            sol_balance = 0
            if data and "result" in data:
                result_data = data["result"]
                if isinstance(result_data, dict) and "value" in result_data:
                    accounts = result_data["value"]
                    if accounts and isinstance(accounts, list) and len(accounts) > 0:
                        # Get WSOL balance from first (should only be one)
                        first_account = accounts[0]
                        if isinstance(first_account, dict):
                            account_obj = first_account.get("account", {})
                            if isinstance(account_obj, dict):
                                data_obj = account_obj.get("data", {})
                                if isinstance(data_obj, dict):
                                    parsed = data_obj.get("parsed", {})
                                    if isinstance(parsed, dict):
                                        wsol_info = parsed.get("info", {})
                                        if isinstance(wsol_info, dict):
                                            token_amount_info = wsol_info.get("tokenAmount", {})
                                            if isinstance(token_amount_info, dict):
                                                sol_balance = float(token_amount_info.get("uiAmount", 0))

            # If no WSOL, fall back to pool account lamports
            if sol_balance == 0:
                # Use background RPC tier for fallback query
                data = await self.call_background_rpc(
                    "getAccountInfo",
                    [pool_address, {"encoding": "jsonParsed"}]
                )
                if not data or "result" not in data or not data["result"]:
                    log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=getAccountInfo_no_result", flush=True)
                    return None

                result_data = data["result"]
                if not isinstance(result_data, dict):
                    log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=getAccountInfo_bad_result_type", flush=True)
                    return None

                account_value = result_data.get("value", {})
                if not account_value or not isinstance(account_value, dict):
                    log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=getAccountInfo_no_value", flush=True)
                    return None

                lamports = account_value.get("lamports", 0)
                sol_balance = lamports / 1e9

            if sol_balance == 0:
                log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=sol_balance_zero", flush=True)
                return None

            # Query token accounts owned by this pool (use background RPC tier)
            data2 = await self.call_background_rpc(
                "getTokenAccountsByOwner",
                [pool_address, {"mint": token_mint}, {"encoding": "jsonParsed"}]
            )
            if not data2 or "result" not in data2:
                log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=token_accounts_no_result sol={sol_balance:.4f}", flush=True)
                return None

            result_data2 = data2["result"]
            if not isinstance(result_data2, dict) or "value" not in result_data2:
                log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=token_accounts_bad_result sol={sol_balance:.4f}", flush=True)
                return None

            accounts = result_data2["value"]
            if not accounts or not isinstance(accounts, list):
                # No token accounts for this mint in this pool - wrong pool address
                log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=no_token_accounts sol={sol_balance:.4f}", flush=True)
                return None

            try:
                # Find the account with the LARGEST token balance
                # (PumpSwap pools may have multiple token accounts)
                max_balance_account = None
                max_balance = 0

                for token_account in accounts:
                    if not isinstance(token_account, dict):
                        continue

                    account_data = token_account.get("account", {})
                    if not isinstance(account_data, dict):
                        continue

                    parsed = account_data.get("data", {})
                    if isinstance(parsed, dict):
                        parsed = parsed.get("parsed", {})
                    elif not isinstance(parsed, dict):
                        continue

                    if not isinstance(parsed, dict):
                        continue
                    token_info = parsed.get("info", {})
                    if not isinstance(token_info, dict):
                        continue
                    token_amount_info = token_info.get("tokenAmount", {})
                    if not isinstance(token_amount_info, dict):
                        continue
                    balance = float(token_amount_info.get("uiAmount", 0))

                    if balance > max_balance:
                        max_balance = balance
                        max_balance_account = token_account

                if not max_balance_account:
                    log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=no_parseable_token_account n_accounts={len(accounts)} sol={sol_balance:.4f}", flush=True)
                    return None

                account_data = max_balance_account.get("account", {})
                if not isinstance(account_data, dict):
                    return None
                data_obj = account_data.get("data", {})
                if not isinstance(data_obj, dict):
                    return None
                parsed = data_obj.get("parsed", {})
                if not isinstance(parsed, dict):
                    return None
                token_info = parsed.get("info", {})
                if not isinstance(token_info, dict):
                    return None
                token_amount_info = token_info.get("tokenAmount", {})
                if not isinstance(token_amount_info, dict):
                    return None
                token_balance = float(token_amount_info.get("uiAmount", 0))

            except (KeyError, ValueError, TypeError) as e:
                log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=parse_exception err={e}", flush=True)
                return None

            # HYDRATION GUARD: Require both base AND quote to be ready
            # Zero balances indicate pool data not yet synced from chain
            if token_balance <= 0 or sol_balance <= 0:
                log_print(f"[ONCHAIN_FAIL] mint={token_mint[:16]} pool={pool_address[:16]} reason=zero_balances sol={sol_balance:.4f} token={token_balance:.0f}", flush=True)
                return None

            # Calculate price
            price_sol = sol_balance / token_balance
            sol_usd = await self._get_sol_price_usd()
            price_usd = price_sol * sol_usd
            total_supply = 1_000_000_000  # Pump.Fun tokens have 1B supply
            market_cap_usd = price_usd * total_supply
            quote_liquidity_usd = sol_balance * sol_usd if sol_usd else 0

            if quote_liquidity_usd < 750:
                try:
                    from src.core.price_worker import record_low_liquidity
                    record_low_liquidity(
                        token_mint,
                        sol_balance,
                        db_path=DB_PATH,
                        quote_usd=quote_liquidity_usd,
                        source="listener_onchain_threshold",
                    )
                except Exception as liq_err:
                    log_print(f"[LIQUIDITY_FLAG_FAIL] mint={token_mint[:16]} err={liq_err}", flush=True)
            elif sol_balance >= 5.0:
                try:
                    from src.core.price_worker import clear_low_liquidity
                    clear_low_liquidity(token_mint, sol_balance, db_path=DB_PATH)
                except Exception:
                    pass

            log_print(f"[ONCHAIN_OK] mint={token_mint[:16]} pool={pool_address[:16]} sol={sol_balance:.4f} token={token_balance:.0f} mc=${market_cap_usd:,.0f}", flush=True)
            return (price_usd, market_cap_usd, quote_liquidity_usd)

        except Exception as e:
            log_print(f"[PRICE_ERROR] Exception in on-chain extraction: {e}", flush=True)
            return None

    async def _extract_onchain_pool_price(self, token_mint: str) -> Optional[tuple]:
        """
        Extract price from on-chain pool account balances.
        
        NOTE: The proper implementation would require:
        1. Finding the actual pool account address for this token
        2. Querying that specific account's vault balances
        3. Calculating price from token/SOL ratio
        
        Since we don't have the pool address readily available during price updates,
        and extracting it reliably from transactions is complex, we rely on DexScreener
        which has verified, real-time pricing. The fallback mechanism ensures pricing
        always works.
        
        Returns: None (signals to use DexScreener fallback)
        """
        return None

    async def _get_pool_price_from_vault(self, token_mint: str) -> Optional[tuple]:
        """
        Extract on-chain price by querying token pool account balances.
        Uses Jupiter API to find pool and fetch live vault balances.
        
        Returns: (price_usd, market_cap_usd) or None
        """
        try:
            # Query Jupiter API for token info which includes pool address
            url = f"https://api.jup.ag/tokens/v1?searchQuery={token_mint}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
                    tokens = data.get("tokens", [])
                    
                    if not tokens:
                        return None
                    
                    # Get SOL price for USD conversion
                    sol_price_usd = await self._get_sol_price_usd()
                    
                    # For now, fallback to DexScreener if we can't determine pool
                    # TODO: Implement proper pool vault balance fetching
                    return None
                    
        except Exception as e:
            return None

    async def _get_sol_price_usd(self) -> float:
        """Get current SOL price in USD using the shared worker-grade cache/fetcher."""
        try:
            from src.core.sol_price_cache import get_sol_price_cache
            from src.core.pool_price_engine import PoolPriceCalculator

            cache = get_sol_price_cache()
            price = await cache.get_price(PoolPriceCalculator.fetch_sol_price_usd)
            # Sanity guard: if some upstream source returns nonsense, keep on-chain MC usable.
            if price and 20.0 <= float(price) <= 1000.0:
                return float(price)
        except Exception:
            pass
        return 94.0

    async def _fetch_dexscreener_price(self, token_mint: str) -> Optional[tuple]:
        """
        Fetch price and market cap from DexScreener API.
        
        Returns: (price_usd, market_cap_usd, liquidity_usd) or None
        All values are in USD for consistency with database storage.
        """
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None
                    
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    
                    if not pairs:
                        return None
                    
                    pair = pairs[0]
                    
                    # Get USD price and market cap from DexScreener
                    price_usd = pair.get("priceUsd")
                    market_cap_usd = pair.get("marketCap")
                    
                    if not price_usd or not market_cap_usd:
                        return None
                    
                    try:
                        price_usd = float(price_usd)
                        market_cap_usd = float(market_cap_usd)
                    except (ValueError, TypeError):
                        return None
                    
                    liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                    return (price_usd, market_cap_usd, liquidity_usd)
                    
        except Exception as e:
            log_print(f"[PRICE_ERROR] DexScreener fetch failed {token_mint}: {e}", flush=True)
            return None

    def _extract_mint_from_logs(self, logs: list) -> Optional[str]:
        """
        Fallback: Extract token mint address from transaction logs.
        Looks for patterns like "mint: EPjFWdd5Au..." in the logs.
        """
        try:
            logs_text = ' '.join(logs)
            # Look for "mint:" patterns followed by base58 addresses
            matches = re.findall(r'mint[:\s]+([1-9A-HJ-NP-Z]{32,})', logs_text, re.IGNORECASE)
            if matches:
                # Return the first valid match (don't filter by "pump" - not all pump.fun tokens contain "pump")
                return matches[0] if matches else None
            return None
        except Exception as e:
            log_print(f"[MINT] ⚠ Error extracting mint from logs: {e}", flush=True)
            return None

    # --- Analyzer ---
    async def analyze_post_migration(self, mint: str, signature: str = None, pool_address: str = None):
        """Analyze token's post-migration activity on PumpSwap"""
        if mint in self.analyzed_tokens:
            return
        try:
            log_print(f"[ANALYZER] 🔍 Analyzing post-migration {mint}", flush=True)
            analyzer = PostMigrationAnalyzer(mint, rpc_url=RPC_HTTP)
            await analyzer.fetch_curve_activity_async()

            summary = await analyzer.get_summary_async()

            # Extract creator from earliest transaction with provenance validation
            provenance = await analyzer.get_creator_from_earliest_tx()
            earliest_creator = None
            creator_is_blocked = 0
            network_risk = None

            if provenance:
                earliest_creator = provenance.get('creator')
                # Store provenance data in summary for auditing
                summary["creator_provenance"] = {
                    'status': provenance.get('status'),
                    'earliest_sig': provenance.get('earliest_sig'),
                    'reached_end': provenance.get('reached_end'),
                    'pages_traversed': provenance.get('pages_traversed'),
                    'is_pumpfun_create': provenance.get('is_pumpfun_create'),
                    'slot': provenance.get('slot'),
                    'blockTime': provenance.get('blockTime'),
                    'validation_notes': provenance.get('validation_notes', [])
                }

            if earliest_creator:
                summary["earliest_tx_creator"] = earliest_creator
                provenance_status = provenance.get('status', 'unknown') if provenance else 'unknown'
                log_print(f"[CREATOR] ✅ Extracted from earliest tx: {earliest_creator} ({provenance_status})", flush=True)

                # Check if creator is in blocklist
                try:
                    conn = db_connect(DB_PATH, timeout=60)
                    cursor = conn.cursor()
                    try:
                        cursor.execute("SELECT rug_count, reputation, connected_to_malicious, network_members FROM creator_blocklist WHERE creator_address = ?", (earliest_creator,))
                        blocklist_row = cursor.fetchone()
                    except sqlite3.OperationalError:
                        # creator_blocklist table doesn't exist yet
                        blocklist_row = None
                    conn.close()

                    if blocklist_row:
                        rug_count, reputation, connected_to_malicious, network_members_json = blocklist_row
                        creator_is_blocked = 1
                        summary["creator_is_blocked"] = 1
                        summary["creator_reputation"] = reputation

                        if rug_count >= 2:
                            log_print(f"[BLOCKLIST] 🚨 MALICIOUS CREATOR DETECTED: {earliest_creator} | {rug_count} rugs", flush=True)
                        else:
                            log_print(f"[BLOCKLIST] 📝 SUSPICIOUS CREATOR: {earliest_creator} | on watch list", flush=True)

                        # Check if connected to other malicious creators
                        if connected_to_malicious:
                            try:
                                network_members = json.loads(network_members_json) if network_members_json else []
                                network_risk = len(network_members)
                                summary["network_risk"] = 1
                                summary["connected_malicious_count"] = len(network_members)
                                log_print(f"[NETWORK] 🔗 NETWORK RISK: Creator is connected to {len(network_members)} malicious creator(s)", flush=True)
                            except:
                                pass

                except Exception as e:
                    log_print(f"[BLOCKLIST_CHECK] Error checking creator: {e}", flush=True)

            self.analyzed_tokens[mint] = summary
            risk_level = summary.get("risk_level", "🟢 LOW RISK")
            score = summary.get("rug_probability", 0.0)

            # Add creator risk indicator if blocked
            if creator_is_blocked:
                if network_risk:
                    risk_indicator = f"🔗 NETWORK RISK ({network_risk} connected)"
                elif summary.get("creator_reputation") == "MALICIOUS":
                    risk_indicator = "🚨 MALICIOUS CREATOR"
                else:
                    risk_indicator = "📝 SUSPICIOUS CREATOR"
                log_print(f"[ANALYZER] {risk_indicator} | {risk_level} | Score: {score:.2%} | {mint}", flush=True)
            else:
                log_print(f"[ANALYZER] {risk_level} | Score: {score:.2%} | {mint}", flush=True)

            # Store analysis results (will be updated with live price in background)
            # Pass pool_address if available
            await self._store_analysis(mint, summary, signature, pool_address)

            # Incrementally update networks if creator was found
            if earliest_creator:
                try:
                    from main import update_networks_for_new_token
                    update_networks_for_new_token(mint, earliest_creator)
                except Exception as e:
                    log_print(f"[NETWORK_UPDATE] Error updating networks for {mint}: {e}", flush=True)

        except Exception as e:
            log_print(f"[ANALYZER] ⚠ Analysis failed for {mint}: {e}", flush=True)

    async def update_live_prices_background(self):
        """Background task: Update live prices and market caps continuously"""
        await asyncio.sleep(2)  # Wait 2s before starting
        
        while True:
            try:
                tokens = self._get_tokens_needing_price_update()
                
                if not tokens:
                    await asyncio.sleep(5)
                    continue
                
                updated_count = 0
                failed_count = 0

                for token_mint in tokens:
                    try:
                        # Get the migration transaction for this token to extract price
                        tx_signature = await self._get_migration_tx_for_token(token_mint)

                        if not tx_signature:
                            failed_count += 1
                            continue

                        # Extract price from DexScreener or on-chain
                        result = await self._extract_price_from_transaction(tx_signature, token_mint)

                        if result is not None:
                            price, market_cap, source, liquidity_usd = result
                            await self._update_price_in_db(
                                token_mint, price, market_cap, source, liquidity_usd
                            )
                            updated_count += 1
                        else:
                            failed_count += 1

                        # Rate limit
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        failed_count += 1

                # Loop back after 10 seconds for live updates
                await asyncio.sleep(10)
                        
            except Exception as e:
                log_print(f"[PRICE_BG] Error in background task: {e}", flush=True)
                await asyncio.sleep(5)

    def _prune_tx_cache(self):
        """Remove expired entries from TX cache to prevent unbounded growth"""
        import time
        now = time.time()
        expired = [sig for sig, (_, ts) in self.tx_cache.items() if (now - ts) >= self.tx_cache_ttl_seconds]
        for sig in expired:
            self.tx_cache.pop(sig, None)
            # Also clean up pending retry tasks for expired entries
            if sig in self.tx_cache_pending_retries:
                self.tx_cache_pending_retries.pop(sig, None)
        if expired:
            log_print(f"[TX_CACHE] 🧹 Pruned {len(expired)} expired entries (cache size: {len(self.tx_cache)})", flush=True)

    def _get_tokens_needing_price_update(self) -> List[str]:
        """Get tokens that need live price updates (prioritize newer)"""
        try:
            conn = db_connect(DB_PATH, timeout=60)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Get top 50 newest tokens (all active tokens)
            cursor.execute("""
                SELECT mint FROM token_analysis
                ORDER BY analyzed_at DESC
                LIMIT 50
            """)
            
            tokens = [row[0] for row in cursor.fetchall()]
            conn.close()
            return tokens
        except Exception as e:
            log_print(f"[DB_ERROR] Failed to fetch tokens: {e}", flush=True)
            return []

    async def _get_migration_tx_for_token(self, token_mint: str) -> Optional[str]:
        """Get the migration transaction signature for a token"""
        try:
            conn = db_connect(DB_PATH, timeout=60)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT migration_tx FROM token_analysis WHERE mint = ?",
                (token_mint,)
            )
            row = cursor.fetchone()
            conn.close()
            
            return row[0] if row and row[0] else None
        except Exception as e:
            log_print(f"[DB_ERROR] Failed to get tx for {token_mint}: {e}", flush=True)
            return None

    async def _add_rug_creator_to_blocklist(self, token_mint: str, earliest_tx_creator: str = None):
        """
        When a rug is detected, add the creator to the block list in the database.
        This allows future tokens from the same creator to be skipped.
        """
        if not earliest_tx_creator:
            return

        async with self.db_lock:
            try:
                conn = db_connect(DB_PATH, timeout=15)
                cursor = conn.cursor()

                # Check if creator already in blocklist
                cursor.execute("SELECT rug_count, rugged_tokens FROM creator_blocklist WHERE creator_address = ?", (earliest_tx_creator,))
                row = cursor.fetchone()

                if row:
                    # Update existing entry
                    rug_count, rugged_tokens_json = row
                    rug_count += 1

                    # Parse existing tokens and add new one
                    try:
                        rugged_tokens = json.loads(rugged_tokens_json) if rugged_tokens_json else []
                    except:
                        rugged_tokens = []

                    if token_mint not in rugged_tokens:
                        rugged_tokens.append(token_mint)

                    # Determine reputation
                    reputation = "MALICIOUS" if rug_count >= 2 else "SUSPICIOUS"

                    cursor.execute(
                        """UPDATE creator_blocklist
                           SET rug_count = ?, rugged_tokens = ?, reputation = ?, last_rug_detected_at = datetime('now'), updated_at = datetime('now')
                           WHERE creator_address = ?""",
                        (rug_count, json.dumps(rugged_tokens), reputation, earliest_tx_creator)
                    )
                else:
                    # Insert new entry
                    cursor.execute(
                        """INSERT INTO creator_blocklist (creator_address, rug_count, rugged_tokens, reputation, first_rug_detected_at, last_rug_detected_at)
                           VALUES (?, 1, ?, 'SUSPICIOUS', datetime('now'), datetime('now'))""",
                        (earliest_tx_creator, json.dumps([token_mint]))
                    )
                    rug_count = 1

                conn.commit()
                conn.close()

                # Log
                if rug_count >= 2:
                    log_print(f"[BLOCKLIST] 🚨 SERIAL RUGGER: {earliest_tx_creator} | {rug_count} rugs detected", flush=True)
                else:
                    log_print(f"[BLOCKLIST] 📝 Added to watch list: {earliest_tx_creator} | {rug_count} rug", flush=True)

            except Exception as e:
                log_print(f"[BLOCKLIST_ERROR] Failed to update rug creator block list: {e}", flush=True)

    async def _persist_price_update(self, token_mint: str, current_price: float, current_market_cap: float,
                                    source: str = "onchain", liquidity_usd: float = 0.0) -> None:
        """
        Route listener prices through the canonical worker persistence sink so
        current MC, snapshots, peaks, and SSE stay in one pipeline.
        """
        try:
            from src.core.price_service import TokenPrice
            from src.core.price_worker import get_price_worker

            sol_usd = await self._get_sol_price_usd()
            price_sol = (current_price / sol_usd) if sol_usd and current_price > 0 else 0.0
            token_price = TokenPrice(
                mint=token_mint,
                price_usd=current_price or 0.0,
                price_sol=price_sol or 0.0,
                liquidity_usd=liquidity_usd or 0.0,
                volume_24h=0.0,
                market_cap=current_market_cap or 0.0,
                source=source or "onchain",
                timestamp=int(time.time()),
                is_stale=False,
            )

            worker = self.price_worker or get_price_worker()
            worker._on_price_fetched(token_mint, token_price)
        except Exception as e:
            log_print(f"[PRICE_CANONICAL] ⚠ Failed to persist {token_mint[:16]}... via worker sink: {e}", flush=True)
            raise

    async def _update_price_in_db(self, token_mint: str, current_price: float, current_market_cap: float,
                                  source: str = "onchain", liquidity_usd: float = 0.0):
        """
        Persist live price via the canonical worker sink, then update listener-owned
        metadata such as rug detection and price_highest.
        
        Also automatically detects and flags rug pulls:
        - If time to peak < 30 minutes AND peak market cap < $100k → flag as 'quick_peak_low_mc'
        
        Note: Prices and market caps are stored in USD for consistency with DexScreener.
        """
        should_refresh_signal = False
        signal_now = int(time.time())
        async with self.db_lock:
            try:
                conn = db_connect(DB_PATH, timeout=15)
                cursor = conn.cursor()

                # Read only what this component owns: price_highest, rug_indicator, created_at
                # Peak MC is owned by price_service.py via token_market_cap_peaks
                cursor.execute(
                    """
                    SELECT price_highest, rug_indicator, created_at, lifecycle_stage,
                           pool_address, pumpswap_pool_address, dex, source_platform, bonding_curve_pda
                    FROM token_analysis WHERE mint = ?
                    """,
                    (token_mint,)
                )
                row = cursor.fetchone()
                price_highest = row[0] if row and row[0] else current_price
                current_rug_indicator = row[1] if row else None
                created_at_raw = row[2] if row else None
                lifecycle_stage = row[3] if row else None
                pool_address = row[4] if row else None
                pumpswap_pool_address = row[5] if row else None
                dex_name = row[6] if row else None
                source_platform = row[7] if row else None
                bonding_curve_pda = row[8] if row else None

                if bonding_curve_pda:
                    self._remember_bonding_curve_token(token_mint, bonding_curve_pda)
                if source_platform == 'pumpfun' or bonding_curve_pda:
                    self._known_bonding_curve_mints.add(token_mint)

                if current_price > price_highest:
                    price_highest = current_price

                # Read authoritative peak from token_market_cap_peaks for rug detection
                cursor.execute(
                    "SELECT peak_market_cap, peak_market_cap_at FROM token_market_cap_peaks WHERE mint = ?",
                    (token_mint,)
                )
                peak_row = cursor.fetchone()
                prev_peak_mc = peak_row[0] if peak_row and peak_row[0] else 0
                is_new_peak = current_market_cap > prev_peak_mc

                # Auto-detect rug pulls based on timing
                rug_indicator = current_rug_indicator
                if is_new_peak and created_at_raw is not None and current_market_cap > 0:
                    try:
                        now_ts = int(datetime.now().timestamp())
                        # Parse created_at to unix
                        if isinstance(created_at_raw, (int, float)):
                            created_ts = int(created_at_raw)
                        else:
                            created_ts = int(datetime.fromisoformat(
                                str(created_at_raw).replace('Z', '+00:00')
                            ).timestamp())

                        time_to_peak_minutes = (now_ts - created_ts) / 60

                        if time_to_peak_minutes < 30 and current_market_cap < 100000:
                            rug_indicator = 'quick_peak_low_mc'
                            log_print(f"[RUG] 🚨 DETECTED: {token_mint} | Time to peak: {time_to_peak_minutes:.1f} min | Peak MC: ${current_market_cap:,.0f}", flush=True)
                            cursor.execute("SELECT earliest_tx_creator FROM token_analysis WHERE mint = ?", (token_mint,))
                            creator_row = cursor.fetchone()
                            if creator_row and creator_row[0]:
                                asyncio.create_task(self._add_rug_creator_to_blocklist(token_mint, creator_row[0]))
                        elif time_to_peak_minutes < 30:
                            rug_indicator = None
                            log_print(f"[PEAK] ⚡ Fast peak but legit size: {token_mint} | Time: {time_to_peak_minutes:.1f} min | MC: ${current_market_cap:,.0f}", flush=True)
                        else:
                            rug_indicator = None
                    except Exception as e:
                        log_print(f"[RUG_CHECK] ⚠ Could not analyze rug pattern for {token_mint}: {e}", flush=True)

                try:
                    await self._persist_price_update(
                        token_mint,
                        current_price,
                        current_market_cap,
                        source=source,
                        liquidity_usd=liquidity_usd,
                    )
                except Exception:
                    # Fallback: keep current price visible even if the canonical sink is unavailable.
                    cursor.execute("""
                        UPDATE token_analysis
                        SET price_current = ?,
                            market_cap_current = ?,
                            price_source = ?, price_updated_at = datetime('now')
                        WHERE mint = ?
                    """, (current_price, current_market_cap, source, token_mint))

                # Only write what this component owns — peaks/snapshots are written by price_service.py
                cursor.execute("""
                    UPDATE token_analysis
                    SET price_highest = ?,
                        rug_indicator = ?
                    WHERE mint = ?
                """, (price_highest, rug_indicator, token_mint))
                
                conn.commit()
                conn.close()

                self._last_market_cap_by_mint[token_mint] = float(current_market_cap or 0.0)
                has_pool = any(value and str(value).strip() for value in (pool_address, pumpswap_pool_address, dex_name))
                if not has_pool and (source_platform == 'pumpfun' or bonding_curve_pda or lifecycle_stage in ('bonding_curve', 'migration_pending')):
                    self._record_flow_event(
                        token_mint,
                        observed_at=signal_now,
                        kind="buy" if float(current_market_cap or 0.0) > 0 else "observation",
                    )
                    should_refresh_signal = True
                
            except Exception as e:
                log_print(f"[DB_ERROR] Failed to update price for {token_mint}: {e}", flush=True)
                return

        if should_refresh_signal:
            await self._persist_pre_migration_signal(
                token_mint,
                float(current_market_cap or 0.0),
                signal_now,
            )

    async def _create_minimal_token_entry(self, mint: str):
        """Create a minimal token entry in database immediately when migration is detected"""
        max_retries = 6
        base_delay = 0.25

        for attempt in range(max_retries):
            conn = None
            try:
                # Hot-path migration persistence should not queue behind listener-local
                # async DB work; the global write lock + SQLite busy_timeout is enough here.
                conn = db_connect(DB_PATH, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                cursor = conn.cursor()

                now = time.time()
                cursor.execute("""
                    INSERT INTO token_analysis (
                        mint, created_at, analyzed_at,
                        lifecycle_stage,
                        rug_probability, risk_level, post_migration_coverage,
                        rug_indicator, events_parsed
                    ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)
                    ON CONFLICT(mint) DO UPDATE SET
                        created_at = COALESCE(token_analysis.created_at, excluded.created_at),
                        analyzed_at = excluded.analyzed_at,
                        lifecycle_stage = COALESCE(token_analysis.lifecycle_stage, excluded.lifecycle_stage)
                """, (mint, now, now, 'migration_pending'))

                conn.commit()
                conn.close()
                conn = None

                log_print(f"[DB] ✅ Created minimal token entry for {mint}", flush=True)
                return

            except sqlite3.OperationalError as e:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                    wait = base_delay * (2 ** attempt)
                    log_print(f"[DB_RETRY] ⏳ Database locked (attempt {attempt+1}/{max_retries}), retrying in {wait:.2f}s...", flush=True)
                    await asyncio.sleep(wait)
                    continue
                log_print(f"[DB_ERROR] Failed to create minimal token entry: {e}", flush=True)
                return
            except Exception as e:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                log_print(f"[DB_ERROR] Failed to create minimal token entry: {e}", flush=True)
                return

    async def _update_token_entry_with_creator(self, mint: str, creator: str, created_at: str, bonding_curve_pda: str = None, create_tx_signature: str = None):
        """Update minimal token entry with creator, creation date, bonding curve, and CREATE tx signature"""
        # Check if creator belongs to any cluster (sync operation, can run outside lock)
        cluster_id = None
        cluster_name = None
        cluster_risk_multiplier = 1.0

        if creator:
            try:
                from cluster_risk_checker import check_creator
                cluster_info = check_creator(creator)
                if cluster_info.get('in_cluster'):
                    cluster_id = cluster_info.get('cluster_id')
                    cluster_name = cluster_info.get('cluster_name', cluster_id)
                    cluster_risk_multiplier = cluster_info.get('risk_multiplier', 1.0)
                    log_print(f"[CLUSTER] ✅ Creator {creator[:8]}... belongs to {cluster_name} ({cluster_id}) - Risk multiplier: {cluster_risk_multiplier}x", flush=True)
                else:
                    log_print(f"[CLUSTER] ℹ Creator {creator[:8]}... not in any cluster", flush=True)
            except Exception as e:
                log_print(f"[CLUSTER] Error checking creator {creator}: {e}", flush=True)

        max_retries = 6
        base_delay = 0.25

        for attempt in range(max_retries):
            try:
                async with self.db_lock:
                    conn = db_connect(DB_PATH, timeout=30)
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA synchronous=NORMAL")
                    conn.execute("PRAGMA busy_timeout=30000")
                    cursor = conn.cursor()

                    cursor.execute("""
                        UPDATE token_analysis
                        SET earliest_tx_creator = ?, created_at = ?, bonding_curve_pda = ?, create_tx_signature = ?,
                            cluster_id = ?, cluster_name = ?, cluster_risk_multiplier = ?
                        WHERE mint = ?
                    """, (creator, created_at, bonding_curve_pda, create_tx_signature, cluster_id, cluster_name, cluster_risk_multiplier, mint))

                    conn.commit()
                    conn.close()

                cluster_info_str = f" | Cluster: {cluster_name} ({cluster_risk_multiplier}x)" if cluster_id else ""
                self._remember_bonding_curve_token(mint, bonding_curve_pda)
                log_print(f"[DB] ✅ Updated token entry with creator: {creator[:8]}... | Created: {created_at} | CREATE tx: {create_tx_signature[:20] if create_tx_signature else 'N/A'}...{cluster_info_str}", flush=True)
                await self._enqueue_creator_funding_job(
                    creator,
                    mint=mint,
                    migration_timestamp=created_at,
                    create_tx_signature=create_tx_signature,
                    delay_seconds=0,
                    source="creator_discovery",
                )
                return

            except sqlite3.OperationalError as e:
                if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                    wait = base_delay * (2 ** attempt)
                    log_print(f"[DB_RETRY] ⏳ Database locked (attempt {attempt+1}/{max_retries}), retrying in {wait:.2f}s...", flush=True)
                    await asyncio.sleep(wait)
                else:
                    log_print(f"[DB_ERROR] Failed to update token entry with creator: {e}", flush=True)
                    return

    async def _register_pool_and_mark_resolved(
        self,
        mint: str,
        pool_address: str,
        discovery_source: str = "tx_parsing",
        timeout: float = 8.0,
        pool_account_info=None,
    ) -> RegisterResult:
        """
        Register a pool and mark token as resolved.

        Returns RegisterResult:
            SUCCESS — registered and resolved
            RETRY   — not yet visible on-chain; caller should wait briefly and re-attempt
            FAIL    — permanent failure (wrong owner, extraction error); add to blacklist
        """
        # Priority: explicit pool_account_info arg > cache from batch validation > RPC fetch
        cached_account_info = (
            pool_account_info
            or self._validated_account_cache.pop(pool_address, None)
        )
        try:
            result = await asyncio.wait_for(
                self._register_pool_inner(mint, pool_address, discovery_source, cached_account_info),
                timeout=timeout,
            )
            if result == RegisterResult.FAIL:
                self._mark_registration_failed(mint, pool_address)
            # RETRY: do NOT blacklist — candidate is still good, just not indexed yet
            return result
        except asyncio.TimeoutError:
            log_print(
                f"[FAST_PATH_REGISTER] ⏳ Timeout ({timeout:.1f}s) — keeping {pool_address[:16]}... alive for retry",
                flush=True,
            )
            # Slow pipeline ≠ bad candidate. Do NOT blacklist — return RETRY so the
            # caller's micro-retry loop re-attempts rather than falling to secondary discovery.
            return RegisterResult.RETRY
        except Exception as e:
            log_print(f"[FAST_PATH_REGISTER] ❌ Registration error: {e}", flush=True)
            self._mark_registration_failed(mint, pool_address)
            return RegisterResult.FAIL

    def _mark_registration_failed(self, mint: str, addr: str) -> None:
        """
        Permanently blacklist a candidate that failed or timed out during registration.
        Writes to both _failed_registration (for inline-retry filter) and the fast-lane
        pending shortlist (so fast-lane retry loops never re-queue this candidate).
        """
        self._failed_registration.setdefault(mint, set()).add(addr)
        # Feed back into the shortlist as a permanent reject so fast-lane naturally
        # excludes it from get_ready_for_retry, forced fallback, and soft accept.
        self.pending_candidates.record_rejection(mint, addr, "registration_failed")

    async def _register_pool_inner(
        self,
        mint: str,
        pool_address: str,
        discovery_source: str,
        cached_account_info=None,
    ) -> RegisterResult:
        """Inner registration logic — called via _register_pool_and_mark_resolved with a timeout."""
        try:
            from src.core.pool_discovery import PoolDiscovery
            from src.core.pool_detector import AMMPrograms

            # Reuse account info from batch validation when available (saves one RPC round-trip)
            if cached_account_info is not None:
                value = cached_account_info
                log_print(
                    f"[FAST_PATH_REGISTER] ♻️  Reusing cached account info for {pool_address[:16]}...",
                    flush=True
                )
            else:
                acct = await self.call_discovery_rpc(
                    "getAccountInfo",
                    [pool_address, {"encoding": "base64", "commitment": "processed"}],
                    timeout=5
                )
                result = (acct or {}).get("result") or {}
                value = result.get("value")

            if not value:
                # Pool not yet indexed at processed commitment — transient, safe to retry
                log_print(
                    f"[FAST_PATH_REGISTER] ⏳ Not yet visible (processed): {pool_address[:16]}...",
                    flush=True
                )
                return RegisterResult.RETRY

            owner = value.get("owner")
            if owner not in AMMPrograms.ALL:
                log_print(
                    f"[FAST_PATH_REGISTER] ❌ Invalid owner {owner[:16] if owner else '???'}... for {pool_address[:16]}...",
                    flush=True
                )
                return RegisterResult.FAIL

            # Register the pool — pass cached account info to skip redundant RPC fetch
            discovery = PoolDiscovery(DB_PATH, RPC_HTTP)
            registered = await discovery.discover_and_register_pool(
                pool_address, mint, pool_account_info=value
            )

            if not registered:
                log_print(f"[FAST_PATH_REGISTER] ❌ Pool registration failed: {pool_address[:16]}...", flush=True)
                return RegisterResult.FAIL

            # Mark token as resolved
            self.token_states[mint] = "resolved"
            self.token_discovery_times[mint]["resolved"] = time.time()
            elapsed = self.token_discovery_times[mint]["resolved"] - self.token_discovery_times[mint]["detected"]

            log_print(
                f"{Colors.DETECT}[FAST_PATH_REGISTER] ✅ Pool {pool_address[:16]}... registered (resolved in {elapsed:.1f}s){Colors.RESET}",
                flush=True
            )
            _wstrace('POOL_REGISTERED', mint, f"pool={pool_address[:20]} elapsed={elapsed:.1f}s")

            # Write pool_address to token_analysis so _get_pool_address can find it for price extraction
            try:
                _conn = db_connect(DB_PATH, timeout=15)
                _conn.execute(
                    "UPDATE token_analysis SET pool_address = ?, pumpswap_pool_address = COALESCE(pumpswap_pool_address, ?), dex = COALESCE(dex, 'pumpswap'), lifecycle_stage = 'migrated' WHERE mint = ?",
                    (pool_address, pool_address, mint),
                )
                _conn.commit()
                _conn.close()
            except Exception as _e:
                log_print(f"[FAST_PATH_REGISTER] ⚠️  Failed to write pool_address to token_analysis: {_e}", flush=True)

            import threading as _threading
            def _score_migrated_fast(m=mint):
                try:
                    import sqlite3 as _sq
                    from src.core.token_prediction_builder import TokenPredictionBuilder
                    c = _sq.connect(DB_PATH, timeout=30)
                    c.execute("PRAGMA journal_mode=WAL")
                    TokenPredictionBuilder(DB_PATH).score_single(c, m, 'MIGRATED')
                    c.close()
                except Exception as _e:
                    log_print(f"[PREDICTION] ⚠ score_single MIGRATED {m[:16]}: {_e}", flush=True)
            _TOKEN_WORK_POOL.submit(_score_migrated_fast)

            await self._record_migration_verification_snapshot(
                mint,
                migrated_at=int(time.time()),
                dex="pumpswap",
                pumpswap_pool_address=pool_address,
            )

            # Persist telemetry (retry_count=0 for primary fast-lane path)
            await self._write_resolution_telemetry(mint, discovery_source, pool_address, 0)

            # Non-blocking reserve check — warmup is async, no reason to block the critical path
            if self.price_worker and self.price_worker.has_pool_data(pool_address):
                log_print(f"[FAST_PATH_REGISTER] ✅ Pool {pool_address[:16]}... reserves ready", flush=True)
            else:
                log_print(f"[FAST_PATH_REGISTER] ℹ️  Pool {pool_address[:16]}... reserves not ready yet (async warmup)", flush=True)

            # Trigger WebSocket refresh (pool data should now be ready for price extraction)
            if self.price_worker:
                try:
                    self.price_worker.trigger_pool_refresh()
                except Exception as e:
                    log_print(f"[FAST_PATH_REGISTER] ⚠️  WebSocket refresh failed: {e}", flush=True)

                # Bootstrap reserves for this pool immediately so _recompute_prices_from_ws_state()
                # can price it right away — without this, the new mint is invisible to the WS
                # price cycle until the first on-chain vault event arrives (potentially 30-60s gap).
                try:
                    _conn2 = db_connect(DB_PATH, timeout=15)
                    _conn2.row_factory = sqlite3.Row
                    _pool_row = _conn2.execute(
                        "SELECT * FROM token_pool_accounts WHERE mint = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
                        (mint,)
                    ).fetchone()
                    _conn2.close()
                    if _pool_row:
                        import threading as _thr
                        _pool_meta = dict(_pool_row)
                        def _bootstrap_with_retry(mint=mint, pool_meta=_pool_meta):
                            import time as _time
                            for _attempt, _delay in enumerate([0, 3, 8, 20], 1):
                                if _delay:
                                    _time.sleep(_delay)
                                ok = self.price_worker.bootstrap_single_pool(mint, pool_meta)
                                if ok:
                                    log_print(f"[FAST_PATH_REGISTER] ✅ Bootstrap succeeded on attempt {_attempt} for {mint[:16]}...", flush=True)
                                    return
                                log_print(f"[FAST_PATH_REGISTER] ⏳ Bootstrap attempt {_attempt} returned no reserves for {mint[:16]}... (retrying)", flush=True)
                            log_print(f"[FAST_PATH_REGISTER] ⚠️  Bootstrap gave up after 4 attempts for {mint[:16]}...", flush=True)
                        _TOKEN_WORK_POOL.submit(_bootstrap_with_retry)
                        log_print(f"[FAST_PATH_REGISTER] 🔄 Bootstrapping reserves for {mint[:16]}... (with retry)", flush=True)
                except Exception as _be:
                    log_print(f"[FAST_PATH_REGISTER] ⚠️  Reserve bootstrap failed: {_be}", flush=True)

            # Register mint for price tracking immediately (don't wait for dashboard load)
            try:
                from src.core.price_worker import PriceWorkerRegistry
                PriceWorkerRegistry(DB_PATH).register_token(mint, priority_level='HIGH')
                log_print(f"[FAST_PATH_REGISTER] 📈 Registered {mint[:16]}... for price tracking (HIGH priority)", flush=True)
                _spawn_symbol_fetch(mint, DB_PATH)
            except Exception as _e:
                log_print(f"[FAST_PATH_REGISTER] ⚠️  Price tracking registration failed: {_e}", flush=True)

            # Broadcast pool_registered event so the UI refreshes immediately
            _reg_creator = None
            try:
                _rc = db_connect(DB_PATH, timeout=15)
                _rr = _rc.execute(
                    "SELECT earliest_tx_creator FROM token_analysis WHERE mint = ?", (mint,)
                ).fetchone()
                _rc.close()
                if _rr and _rr[0]:
                    _reg_creator = _rr[0]
            except Exception:
                pass
            _broadcast_to_flask({
                "type": "pool_registered",
                "mint": mint,
                "pool_address": pool_address,
                "elapsed_secs": round(elapsed, 3),
                **({"creator": _reg_creator} if _reg_creator else {}),
            })

            return RegisterResult.SUCCESS

        except Exception as e:
            log_print(f"[FAST_PATH_REGISTER] ❌ Registration error: {e}", flush=True)
            return RegisterResult.FAIL

    async def _process_migration_with_mint(self, signature: str, logs: list, mint: str, tx_data: Optional[Dict] = None):
        """Continue migration pipeline once mint is known."""
        _mig_t = int(time.time())
        try:
            _cc_row = db_connect(DB_PATH, timeout=5).execute(
                "SELECT curve_complete, curve_completed_at FROM token_analysis WHERE mint=? LIMIT 1", (mint,)
            ).fetchone()
            if _cc_row and _cc_row[1]:
                _delta = _mig_t - int(_cc_row[1])
                premig_log(f"[TIMING] mint={mint} migration_arrived t={_mig_t} curve_complete_was={'yes' if _cc_row[0] else 'no'} delta_since_complete={_delta}s")
            else:
                premig_log(f"[TIMING] mint={mint} migration_arrived t={_mig_t} curve_complete_was=no")
        except Exception:
            pass
        if self._token_exists_in_db(mint):
            log_print(f"[MIGRATION] ⏭️  Token {mint} already analyzed - SKIPPED", flush=True)
            resolved_creator, create_tx_sig = self._get_resolved_creator_for_mint(mint)
            if resolved_creator:
                migration_time_str = datetime.utcfromtimestamp(int(time.time())).isoformat() + "Z"
                await self._enqueue_creator_funding_job(
                    resolved_creator,
                    mint=mint,
                    migration_timestamp=migration_time_str,
                    create_tx_signature=create_tx_sig,
                    delay_seconds=0,
                    source="migration_already_known",
                )
            # Always ensure pf_ws_creator is set — returns early if already resolved.
            asyncio.create_task(self._ensure_pf_ws_creator(mint, reason="migration:pre_tracked"))
            import threading as _threading
            def _score_already_known(m=mint):
                try:
                    import sqlite3 as _sq
                    from src.core.token_prediction_builder import TokenPredictionBuilder
                    c = _sq.connect(DB_PATH, timeout=30)
                    c.execute("PRAGMA journal_mode=WAL")
                    TokenPredictionBuilder(DB_PATH).score_single(c, m, 'MIGRATED')
                    c.commit()
                    c.close()
                except Exception as _e:
                    log_print(f"[PREDICTION] ⚠ score_single MIGRATED {m[:16]}: {_e}", flush=True)
            _TOKEN_WORK_POOL.submit(_score_already_known)
            return

        # === GUARD 1: Prevent duplicate primary discovery by mint ===
        if mint in self._active_pool_discoveries_by_mint:
            log_print(f"[DISCOVERY_GUARD] ⏭️  Skip duplicate primary discovery for mint={mint[:16]}...", flush=True)
            return

        # === GUARD 2: Prevent duplicate primary discovery by signature ===
        if signature in self._active_pool_discoveries_by_sig:
            log_print(f"[DISCOVERY_GUARD] ⏭️  Skip duplicate primary discovery for sig={signature[:16]}...", flush=True)
            return

        # === GUARD 3: Prevent re-entry if retry is already active ===
        existing_task = self._retry_tasks_by_mint.get(mint)
        if existing_task and not existing_task.done():
            log_print(f"[DISCOVERY_GUARD] ⏭️  Retry already active for {mint[:16]}..., skipping duplicate primary discovery", flush=True)
            return

        # === GUARD 4: Prevent primary re-entry within short window ===
        last_attempt = self._primary_attempted_by_mint.get(mint)
        if last_attempt and time.time() - last_attempt < 120:
            log_print(f"[PRIMARY_GUARD] ⏭️  Primary discovery already attempted recently for {mint[:16]}..., skipping", flush=True)
            return

        # Mark as active immediately
        self._active_pool_discoveries_by_mint.add(mint)
        self._active_pool_discoveries_by_sig.add(signature)
        self._primary_attempted_by_mint[mint] = time.time()

        try:
            self.seen_mints.add(mint)
            log_print(f"[EVENT] 🚀 MIGRATION DETECTED: {mint}", flush=True)
            log_print(f"[EVENT] Migration signature: {signature}", flush=True)
            _wstrace('MIGRATION_DETECTED', mint, f"sig={signature[:20]}")
            migrated_at_ts = int(time.time())

            # === PHASE 2: Start critical window for RPC isolation ===
            # Discovery RPC calls use 8 concurrent slots, background jobs use only 2
            self.start_critical_window(mint)

            # Create minimal token entry immediately (so token appears in UI right away)
            log_print(f"[MIGRATION_TRACE] step=create_minimal_entry:start mint={mint[:16]}... sig={signature[:16]}...", flush=True)
            await self._create_minimal_token_entry(mint)
            log_print(f"[MIGRATION_TRACE] step=create_minimal_entry:done mint={mint[:16]}...", flush=True)
            log_print(f"[MIGRATION_TRACE] step=mark_migrated:start mint={mint[:16]}...", flush=True)
            _migration_slot = tx_data.get("slot") if tx_data else None
            await self._mark_token_migrated_in_db(
                mint,
                migrated_at=migrated_at_ts,
                migration_tx=signature,
                dex="pumpswap",
                migration_slot=_migration_slot,
            )
            log_print(f"[MIGRATION_TRACE] step=mark_migrated:done mint={mint[:16]}...", flush=True)
            log_print(f"[MIGRATION_TRACE] step=record_verification:start mint={mint[:16]}...", flush=True)
            await self._record_migration_verification_snapshot(
                mint,
                migrated_at=migrated_at_ts,
                migration_tx=signature,
                dex="pumpswap",
            )
            log_print(f"[MIGRATION_TRACE] step=record_verification:done mint={mint[:16]}...", flush=True)

            # Always resolve creator at migration — one getTransaction RPC, enqueues funding job.
            # _ensure_pf_ws_creator returns early if pf_ws_creator already set.
            asyncio.create_task(self._ensure_pf_ws_creator(mint, reason="migration"))

            # Register for price tracking immediately — HIGH priority, fail-safe
            try:
                from src.core.price_worker import PriceWorkerRegistry
                PriceWorkerRegistry(DB_PATH).register_token(mint, priority_level='HIGH')
                log_print(f"[MIGRATION] 📈 Registered {mint[:16]}... for price tracking (HIGH priority)", flush=True)
                _spawn_symbol_fetch(mint, DB_PATH)
            except Exception as _reg_err:
                log_print(f"[MIGRATION] ⚠️  Price tracking registration skipped: {_reg_err}", flush=True)

            # Fast-lane first-price: bypass the queue, retry every 3s for up to 60s
            def _first_price_fast_lane(mint: str) -> None:
                try:
                    from src.core.price_worker import get_price_worker
                    worker = get_price_worker()
                    deadline = time.time() + 60
                    interval = 3.0
                    while time.time() < deadline:
                        try:
                            price = worker._fetch_single_price(mint)
                            if price and price.source != 'unavailable' and price.price_usd > 0:
                                worker._on_price_fetched(mint, price)
                                log_print(f"[FAST_PRICE] ✅ {mint[:16]}... first price ${price.price_usd:.8f} (source={price.source})", flush=True)
                                _wstrace('FAST_LANE_PRICE', mint, f"price=${price.price_usd:.8f} source={price.source}")
                                return
                        except Exception:
                            pass
                        time.sleep(interval)
                    log_print(f"[FAST_PRICE] ⏱ {mint[:16]}... gave up after 60s", flush=True)
                    _wstrace('FAST_LANE_TIMEOUT', mint, "no price after 60s")
                except Exception as _e:
                    log_print(f"[FAST_PRICE] ⚠️  fast-lane failed: {_e}", flush=True)

            _TOKEN_WORK_POOL.submit(_first_price_fast_lane, mint)

            # === INSTANT UI: broadcast token_detected before any discovery ===
            _det_event = {
                "type": "token_detected",
                "mint": mint,
                "detected_at": int(time.time()),
                "status": "detecting",
                "source": "migration",
            }
            # Enrich with any data already in DB from minimal entry
            try:
                _conn = db_connect(DB_PATH, timeout=15)
                _row = _conn.execute(
                    "SELECT symbol, earliest_tx_creator, pool_address FROM token_analysis WHERE mint = ?",
                    (mint,)
                ).fetchone()
                _conn.close()
                if _row:
                    if _row[0]: _det_event["symbol"] = _row[0]
                    if _row[1]: _det_event["creator"] = _row[1]
                    if _row[2]: _det_event["pool_address"] = _row[2]
            except Exception:
                pass
            _broadcast_to_flask(_det_event)

            # Store migration TX signature (needed for retry discovery and analytics)
            try:
                conn = db_connect(DB_PATH, timeout=15)
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE token_analysis SET migration_tx = ?, lifecycle_stage = 'migrated', migrated_at = COALESCE(migrated_at, ?), dex = COALESCE(dex, 'pumpswap'), source_platform = COALESCE(source_platform, 'pumpfun') WHERE mint = ?",
                    (signature, migrated_at_ts, mint)
                )
                conn.commit()
                conn.close()
                log_print(f"[DB] ✅ Stored migration TX: {signature[:20]}...", flush=True)
                def _score_migrated_tx(m=mint):
                    try:
                        import sqlite3 as _sq
                        from src.core.token_prediction_builder import TokenPredictionBuilder
                        c = _sq.connect(DB_PATH, timeout=30)
                        c.execute("PRAGMA journal_mode=WAL")
                        TokenPredictionBuilder(DB_PATH).score_single(c, m, 'MIGRATED')
                        c.close()
                    except Exception as _e:
                        log_print(f"[PREDICTION] ⚠ score_single MIGRATED {m[:16]}: {_e}", flush=True)
                _TOKEN_WORK_POOL.submit(_score_migrated_tx)
            except Exception as e:
                log_print(f"[DB] ⚠️  Failed to store migration TX: {e}", flush=True)

            # === NEW: Track token state (pending initially) ===
            current_state = self.token_states.get(mint)
            if current_state not in {"pending", "resolving", "resolved"}:
                self.token_states[mint] = "pending"
                detected_at = time.time()
                self.token_discovery_times[mint] = {
                    "detected": detected_at,
                    "resolved": None,
                    "first_valid_pool_at": None,
                    "pool_registered_at": None,
                    "resolved_at": None,
                }
                log_print(f"[STATE] Token {mint[:16]}... → pending", flush=True)
            else:
                log_print(
                    f"[STATE_GUARD] ⏭️  Skip duplicate pending transition for {mint[:16]}... "
                    f"(current={current_state})",
                    flush=True
                )

            # === Write initial telemetry entry ===
            try:
                now = int(time.time())
                conn = db_connect(DB_PATH, timeout=15)
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR IGNORE INTO token_resolution_telemetry
                    (mint, detected_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                """, (mint, now, now, now))
                conn.commit()
                conn.close()
            except Exception as e:
                log_print(f"[TELEMETRY] ⚠️  Failed to write initial telemetry for {mint}: {e}", flush=True)

            # === Extract pool via FAST-LANE PRIMARY PATH ===
            # NEW: Fast-lane is now the primary path when tx_data is available
            # This replaces the old discover_pool_candidates_from_migration_tx approach
            pool_address = None
            pool_discovery_source = "none"

            if tx_data:
                # CRITICAL: Enrich tx_data before fast-lane (reconstruct meta.accounts from accountKeys + loadedAddresses)
                tx_data = await self._enrich_tx_data(tx_data)

                log_print(
                    f"{Colors.DISCOVER}[FAST_LANE_PRIMARY] 🚀 Starting fast-lane discovery (PRIMARY PATH) for {mint[:16]}...{Colors.RESET}",
                    flush=True
                )
                try:
                    # Fast-lane with TX data: extract candidates, score, validate, and retry on transient failures.
                    # 8.0s window: RPC visibility lag can exceed 4-7s for slower-indexing migrations;
                    # retries are distributed across the full window via fast_candidate_retry delay schedule.
                    pool = await self.fast_lane_resolve_with_retries(
                        mint=mint,
                        tx_data=tx_data,
                        max_wait_secs=8.0
                    )

                    if pool:
                        # ✅ FAST-LANE SUCCESS: Record first valid pool timestamp
                        first_valid_pool_at = time.time()
                        self.token_discovery_times[mint]["first_valid_pool_at"] = first_valid_pool_at

                        # Unwrap rich object returned by fast-lane (dict with address + cached account info)
                        if isinstance(pool, dict):
                            pool_addr = pool["address"]
                            winner_account_info = pool.get("account_info")
                        else:
                            pool_addr = pool
                            winner_account_info = None
                        pool = pool_addr  # normalise to string for the rest of this block

                        # pop_valid_candidates returns all valid candidates from the fast-lane run
                        # (best first), then falls back to [pool] if somehow empty.
                        # Try each in order until one registers successfully — never fall back to
                        # full rediscovery while valid candidates still exist.
                        raw_ranked = self.pop_valid_candidates(mint) or [pool]
                        if pool not in raw_ranked:
                            raw_ranked = [pool] + raw_ranked
                        # Strip candidates that already failed registration this session
                        _already_failed = self._failed_registration.get(mint, set())
                        ranked = [c for c in raw_ranked if c not in _already_failed]
                        if not ranked:
                            ranked = raw_ranked  # all failed before — keep them as last resort

                        registered = False

                        # Single-candidate fast path: no parallel overhead needed
                        if len(ranked) == 1:
                            res = await self._register_pool_and_mark_resolved(
                                mint, ranked[0], "tx_parsing", timeout=8.0,
                                pool_account_info=winner_account_info if ranked[0] == pool else None,
                            )
                            if res == RegisterResult.SUCCESS:
                                registered = True
                            elif res == RegisterResult.RETRY:
                                retry_pending = [ranked[0]]
                                for _a in range(3):
                                    await asyncio.sleep(0.2)
                                    res = await self._register_pool_and_mark_resolved(
                                        mint, ranked[0], "tx_parsing", timeout=8.0
                                    )
                                    if res == RegisterResult.SUCCESS:
                                        registered = True
                                        break

                        # PARALLEL REGISTRATION: race top 2 candidates simultaneously.
                        # First SUCCESS wins and cancels the other.
                        # RETRY results are collected for a brief local re-attempt window.
                        # FAIL results are permanently blacklisted.
                        elif len(ranked) >= 2:
                            top2 = ranked[:2]
                            self._log_fl(
                                f"[FAST_LANE_PRIMARY] ⚡ Parallel registration: {top2[0][:16]}... vs {top2[1][:16]}..."
                            )
                            tasks = {
                                asyncio.ensure_future(
                                    self._register_pool_and_mark_resolved(
                                        mint, c, "tx_parsing", timeout=8.0,
                                        pool_account_info=winner_account_info if c == pool else None,
                                    )
                                ): c
                                for c in top2
                            }
                            winner = None
                            retry_pending = []
                            pending_tasks = set(tasks)
                            while pending_tasks and not winner:
                                done, pending_tasks = await asyncio.wait(
                                    pending_tasks, return_when=asyncio.FIRST_COMPLETED
                                )
                                for t in done:
                                    if t.result() == RegisterResult.SUCCESS:
                                        winner = tasks[t]
                                    elif t.result() == RegisterResult.RETRY:
                                        retry_pending.append(tasks[t])
                            for t in pending_tasks:
                                t.cancel()
                            if winner:
                                registered = True
                                pool = winner
                            else:
                                # Try remaining serial candidates first
                                for candidate in ranked[2:]:
                                    res = await self._register_pool_and_mark_resolved(
                                        mint, candidate, "tx_parsing", timeout=8.0
                                    )
                                    if res == RegisterResult.SUCCESS:
                                        registered = True
                                        pool = candidate
                                        break
                                    elif res == RegisterResult.RETRY:
                                        retry_pending.append(candidate)
                        # VISIBILITY MICRO-RETRY: candidates that returned RETRY were not yet
                        # indexed at processed commitment. They are almost certainly valid —
                        # wait briefly and re-attempt rather than falling to inline retry / secondary.
                        if not registered and retry_pending:
                            self._log_fl(
                                f"[FAST_LANE_PRIMARY] ⏳ {len(retry_pending)} candidate(s) not yet visible — "
                                f"micro-retry up to 3x (200ms each)"
                            )
                            for _attempt in range(3):
                                await asyncio.sleep(0.2)
                                for candidate in retry_pending:
                                    res = await self._register_pool_and_mark_resolved(
                                        mint, candidate, "tx_parsing", timeout=8.0
                                    )
                                    if res == RegisterResult.SUCCESS:
                                        registered = True
                                        pool = candidate
                                        break
                                if registered:
                                    break

                        if registered:
                            # Record pool registration timestamp
                            pool_registered_at = time.time()
                            self.token_discovery_times[mint]["pool_registered_at"] = pool_registered_at

                            log_print(
                                f"{Colors.DETECT}[FAST_LANE_PRIMARY] ✅ Fast-lane short-circuiting: pool {pool[:16]}... "
                                f"registered, skipping secondary discovery and retries{Colors.RESET}",
                                flush=True
                            )

                            # Log timing checkpoints for debugging
                            time_to_valid = first_valid_pool_at - self.token_discovery_times[mint]["detected"]
                            time_to_registered = pool_registered_at - self.token_discovery_times[mint]["detected"]
                            log_print(
                                f"[TIMING] first_valid_pool={time_to_valid:.2f}s, pool_registered={time_to_registered:.2f}s",
                                flush=True
                            )

                            # Extract creator and schedule background work, then return
                            # (do not continue to secondary discovery or retry scheduling)
                            pool_address = pool
                            pool_discovery_source = "tx_parsing"
                            # Continue to creator extraction below, but skip secondary/retries
                    else:
                        log_print(
                            f"{Colors.DISCOVER}[FAST_LANE_PRIMARY] ⏭️  Fast-lane timed out or found no valid pool{Colors.RESET}",
                            flush=True
                        )
                except Exception as e:
                    log_print(
                        f"{Colors.DISCOVER}[FAST_LANE_PRIMARY] ⏭️  Fast-lane error: {e}{Colors.RESET}",
                        flush=True
                    )

            # INLINE RETRY: Quick retry before heavy fallback (high impact optimization)
            # Only runs if the primary fast-lane returned no pool.
            # _filter_failed inside fast-lane already strips FAIL-blacklisted candidates.
            if not pool_address and tx_data:
                log_print(
                    f"{Colors.DISCOVER}[POOL_DETECT] ⚡ INLINE RETRY: Quick re-attempt for {mint[:16]}...{Colors.RESET}",
                    flush=True
                )
                await asyncio.sleep(0.4)
                candidate = await self.fast_lane_resolve_with_retries(
                    mint=mint,
                    tx_data=tx_data,
                    max_wait_secs=5.0  # Inline retry: shorter than primary but still covers mid-range visibility lag
                )
                # Unwrap rich object: fast_lane_resolve_with_retries may return {"address": ..., ...}
                if isinstance(candidate, dict):
                    candidate = candidate.get("address") or None
                _inline_failed = self._failed_registration.get(mint, set())
                if candidate and candidate not in _inline_failed:
                    pool_address = candidate
                    pool_discovery_source = "tx_parsing_retry"
                    log_print(
                        f"{Colors.DISCOVER}[POOL_DETECT] ⚡ INLINE RETRY RETURNED CANDIDATE: {pool_address[:16]}...{Colors.RESET}",
                        flush=True
                    )
                elif candidate:
                    log_print(
                        f"{Colors.DISCOVER}[POOL_DETECT] ⚡ INLINE RETRY: candidate {candidate[:16]}... already hard-failed, skipping{Colors.RESET}",
                        flush=True
                    )

            # STAGE 2: RPC-based vault discovery (FALLBACK when fast-lane fails)
            if not pool_address:
                try:
                    from src.core.vault_discovery import discover_vaults_rpc

                    log_print(
                        f"{Colors.DISCOVER}[POOL_DETECT] 🔍 SECONDARY: RPC vault discovery (Strategy 2/3) for {mint[:16]}...{Colors.RESET}",
                        flush=True
                    )

                    # Create simple RPC client adapter for vault discovery
                    class RPCClientAdapter:
                        def __init__(self, rpc_url: str):
                            self.rpc_url = rpc_url

                        async def _post_rpc_with_fallback(self, payload):
                            import aiohttp
                            try:
                                async with aiohttp.ClientSession() as session:
                                    async with session.post(self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10.0)) as resp:
                                        return await resp.json()
                            except Exception as e:
                                return None

                        async def call_async(self, method: str, params):
                            """Generic RPC call for vault discovery"""
                            payload = {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": method,
                                "params": params
                            }
                            result = await self._post_rpc_with_fallback(payload)
                            if not result:
                                return None
                            if "error" in result:
                                return None
                            if "result" in result:
                                return result["result"]
                            return None

                        async def get_account_info(self, address: str, encoding: str = "base64"):
                            """Fetch account info for vault discovery"""
                            result = await self.call_async(
                                "getAccountInfo",
                                [address, {"encoding": encoding, "commitment": "confirmed"}]
                            )
                            if result:
                                return type('AccountInfo', (), {
                                    'data': result.get('data', ''),
                                    'owner': result.get('owner', ''),
                                    'lamports': result.get('lamports', 0)
                                })()
                            return None

                        async def get_multiple_accounts(self, addresses: list, encoding: str = "base64", commitment: str = "confirmed"):
                            """Batch fetch account info for vault discovery"""
                            result = await self.call_async(
                                "getMultipleAccounts",
                                [addresses, {"encoding": encoding, "commitment": commitment}]
                            )
                            if result and "value" in result:
                                accounts = []
                                for acct_data in result["value"]:
                                    if acct_data:
                                        accounts.append(type('AccountInfo', (), {
                                            'data': acct_data.get('data', ''),
                                            'owner': acct_data.get('owner', ''),
                                            'lamports': acct_data.get('lamports', 0),
                                            'executable': acct_data.get('executable', False)
                                        })())
                                    else:
                                        accounts.append(None)
                                return accounts
                            return None

                        async def get_token_accounts_by_owner(self, owner: str, mint: str, encoding: str = "base64"):
                            """Query for token accounts owned by a specific owner (for quote vault fallback)"""
                            result = await self.call_async(
                                "getTokenAccountsByOwner",
                                [owner, {"mint": mint}, {"encoding": encoding}]
                            )
                            if result and "value" in result:
                                return result
                            return None

                    rpc_adapter = RPCClientAdapter(RPC_HTTP)
                    vault_pair = await discover_vaults_rpc(
                        token_mint=mint,
                        rpc_client=rpc_adapter,
                        ws_monitor=None,
                        max_retries=1  # Single attempt in listener context
                    )

                    if vault_pair:
                        pool_address = vault_pair.base_vault.address
                        pool_discovery_source = "rpc_vaults"
                        log_print(
                            f"{Colors.DETECT}[POOL_DETECT] ✅ Pool discovered via RPC vaults: {pool_address[:16]}...{Colors.RESET}",
                            flush=True
                        )
                        # Store quote vault for later use if needed
                        if not hasattr(self, '_last_quote_vault'):
                            self._last_quote_vault = {}
                        self._last_quote_vault[mint] = vault_pair.quote_vault.get('address') if hasattr(vault_pair.quote_vault, 'get') else vault_pair.quote_vault

                except Exception as e:
                    pool_address = None

            # === AUTO-REGISTER POOL FOR WEBSOCKET PRICING ===
            # ✅ Validate pool owner before registration (belt-and-suspenders check).
            # pool_discovery_source is treated as provisional until this passes —
            # if owner check fails, reset to "none" so retry scheduling fires correctly.
            #
            # SKIP for pools already registered by fast-lane (_register_pool_and_mark_resolved).
            # Those pools were validated + written to DB inside the fast-lane path.
            # Re-running getAccountInfo here re-introduces the RPC indexing-lag problem and
            # causes "source=none pool=None" to appear right after a successful registration.
            _fast_lane_registered = pool_discovery_source == "tx_parsing"
            if pool_address and not _fast_lane_registered:
                try:
                    from src.core.pool_detector import AMMPrograms
                    # Check pool owner is actually an AMM program
                    # Use discovery RPC (pool validation is critical)
                    acct = await self.call_discovery_rpc(
                        "getAccountInfo",
                        [pool_address, {"encoding": "base64"}],
                        timeout=5
                    )
                    pool_is_valid = False
                    result = (acct or {}).get("result") or {}
                    value = result.get("value")
                    if value:
                        owner = value.get("owner")
                        if owner in AMMPrograms.ALL:
                            pool_is_valid = True
                        else:
                            log_print(
                                f"{Colors.DETECT}[POOL_DETECT] ⚠️  Rejecting pool {pool_address}: owner {owner[:16] if owner else '???'}... is not AMM program{Colors.RESET}",
                                flush=True
                            )
                            pool_address = None
                            pool_discovery_source = "none"  # Treat as not found — trigger retry scheduling
                    else:
                        log_print(
                            f"{Colors.DETECT}[POOL_DETECT] ⚠️  Pool {pool_address[:16]}... not yet visible on-chain{Colors.RESET}",
                            flush=True
                        )
                        pool_address = None
                        pool_discovery_source = "none"  # Treat as not found — trigger retry scheduling

                    if pool_is_valid:
                        # Pool passed validation - proceed with registration
                        try:
                            from src.core.pool_discovery import PoolDiscovery
                            discovery = PoolDiscovery(DB_PATH, RPC_HTTP)
                            registered = await discovery.discover_and_register_pool(pool_address, mint)
                            if registered:
                                log_print(f"[POOL] 🚀 Auto-registered pool for WebSocket pricing", flush=True)
                                # Transition to "resolved" state
                                self.token_states[mint] = "resolved"
                                self.token_discovery_times[mint]["resolved"] = time.time()
                                elapsed = self.token_discovery_times[mint]["resolved"] - self.token_discovery_times[mint]["detected"]
                                log_print(f"{Colors.DETECT}[STATE] Token {mint[:16]}... → resolved (in {elapsed:.1f}s){Colors.RESET}", flush=True)

                                # Persist discovery metadata
                                await self._write_resolution_telemetry(mint, "rpc_discovery", pool_address, 0)

                                # Trigger WebSocket refresh to subscribe to new pool
                                if self.price_worker:
                                    log_print(f"[POOL] Triggering price worker WebSocket refresh for {mint[:16]}...", flush=True)
                                    try:
                                        self.price_worker.trigger_pool_refresh()
                                    except Exception as e:
                                        log_print(f"[POOL] ⚠️  Price worker refresh failed: {e}", flush=True)
                            else:
                                log_print(f"[POOL] ⚠️  Could not auto-register pool reserves", flush=True)
                        except Exception as pool_err:
                            log_print(f"[POOL] ⚠️  Pool auto-registration error: {pool_err}", flush=True)
                except Exception as pool_err:
                    log_print(f"{Colors.DETECT}[POOL_DETECT] ⚠️  Pool validation error: {pool_err}{Colors.RESET}", flush=True)
                    pool_address = None
                    pool_discovery_source = "none"

            # ✅ Emit source-of-truth result AFTER owner validation has run.
            # pool_discovery_source is only non-"none" here if the owner check passed.
            log_print(
                f"{Colors.DETECT}[POOL_DETECT] Final discovery result: source={pool_discovery_source} pool={pool_address if pool_address else 'None'}{Colors.RESET}",
                flush=True
            )

            # === SCHEDULE RETRY DISCOVERY IF NO VALIDATED POOL FOUND ===
            log_print(
                f"🔴 [DISCOVERY_CHECKPOINT] pool_discovery_source='{pool_discovery_source}' (none=retry, other=success)",
                flush=True
            )

            if pool_discovery_source == "none":
                self.token_states[mint] = "resolving"
                log_print(
                    f"{Colors.DETECT}[STATE] Token {mint[:16]}... → resolving (scheduling retries){Colors.RESET}",
                    flush=True
                )
                log_print(f"🔴 [DECISION] pool_discovery_source=none → WILL SCHEDULE RETRIES", flush=True)
                log_print(
                    f"{Colors.DETECT}[POOL_DETECT] Initial discovery failed, scheduling optimized retries...{Colors.RESET}",
                    flush=True
                )
                schedule_retry_after_creator_extraction = True
            else:
                log_print(
                    f"🔴 [DECISION] pool_discovery_source='{pool_discovery_source}' → NO retries needed",
                    flush=True
                )
                schedule_retry_after_creator_extraction = False

            # Trigger immediate price fetch (don't wait for background task)
            # This ensures market cap appears quickly in UI regardless of analysis settings
            try:
                result = await self._extract_price_from_transaction(signature, mint)
                if result is not None:
                    price, market_cap, source, liquidity_usd = result
                    await self._update_price_in_db(mint, price, market_cap, source, liquidity_usd)
                    log_print(f"[PRICE] ✅ Initial price fetched: ${price:.2e} | Market Cap: ${market_cap:.2e} | Source: {source}", flush=True)
            except Exception as price_err:
                log_print(f"[PRICE] ⚠ Initial price fetch failed: {price_err}", flush=True)

            # Extract earliest creator and creation date (always, regardless of analysis toggles)
            # This ensures creator and date are always visible in the UI
            earliest_creator = None
            bonding_curve_pda = None
            created_at = None
            analyzer = None

            # Fast path: check PumpPortal in-memory state and DB before hitting RPC
            _fast_creator = (self._portal_vsol.get(mint) or {}).get('creator') or None
            if not _fast_creator:
                try:
                    _db_fast = db_connect(DB_PATH, timeout=5).execute(
                        "SELECT pf_ws_creator, earliest_tx_creator FROM token_analysis WHERE mint=? LIMIT 1", (mint,)
                    ).fetchone()
                    if _db_fast:
                        _fast_creator = (str(_db_fast[0]).strip() if _db_fast[0] else "") or (str(_db_fast[1]).strip() if _db_fast[1] else "") or None
                except Exception:
                    pass

            if _fast_creator:
                log_print(f"[CREATOR_EXTRACTION] ⚡ Fast-path creator={_fast_creator[:16]}... for {mint[:16]}...", flush=True)
                earliest_creator = _fast_creator

            try:
                from src.analysis.pump_fun_post_migration_analyzer import PostMigrationAnalyzer
                analyzer = PostMigrationAnalyzer(mint, rpc_url=RPC_HTTP)
                if not earliest_creator:
                    provenance = await analyzer.get_creator_from_earliest_tx()
                    earliest_creator = provenance.get('creator') if provenance else None
                else:
                    provenance = None

                log_print(
                    f"🔴 [CREATOR_EXTRACTION] creator={earliest_creator[:16] if earliest_creator else 'NONE'}... "
                    f"provenance={provenance.get('status') if provenance else 'fast-path'}",
                    flush=True
                )

                # Prefer on-chain blockTime from provenance over migration tx blockTime
                if provenance and provenance.get('blockTime'):
                    block_time = provenance.get('blockTime')
                    created_at = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                    log_print(f"[CREATOR] 🕐 Using on-chain time from earliest tx: {created_at}", flush=True)

                # Fallback: Get migration block time if provenance doesn't have blockTime
                if not created_at and signature and tx_data:
                    try:
                        block_time = tx_data.get("blockTime")
                        if block_time:
                            created_at = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                    except Exception:
                        pass

                created_at = created_at or (datetime.utcnow().isoformat() + "Z")

                if earliest_creator:
                    provenance_status = provenance.get('status', 'unknown') if provenance else 'unknown'
                    bonding_curve_pda = provenance.get('bonding_curve_pda') if provenance else None

                    # CRITICAL: Only accept create_tx_signature if it's a validated Pump.Fun CREATE transaction
                    is_pumpfun_create = provenance.get('is_pumpfun_create', False) if provenance else False
                    create_tx_signature = analyzer._create_tx_signature if (analyzer and hasattr(analyzer, '_create_tx_signature') and is_pumpfun_create) else None

                    if create_tx_signature:
                        log_print(f"[CREATOR] ✅ Extracted from earliest tx: {earliest_creator} ({provenance_status}) | CREATE tx validated: {create_tx_signature[:20]}...", flush=True)
                    else:
                        analyzer_sig = analyzer._create_tx_signature if (analyzer and hasattr(analyzer, '_create_tx_signature')) else None
                        log_print(f"[CREATOR] ✅ Extracted from earliest tx: {earliest_creator} ({provenance_status}) | CREATE tx validation: {'FAILED' if analyzer_sig else 'NOT_SET'}", flush=True)

                    # Update minimal entry with creator, date, bonding curve, and CREATE tx signature (only if validated)
                    await self._update_token_entry_with_creator(mint, earliest_creator, created_at, bonding_curve_pda, create_tx_signature)

                    # Creator tracking now handled by creator_outgoing_extractor (background job)
            except Exception as creator_err:
                log_print(f"[CREATOR] ⚠ Could not extract creator: {creator_err}", flush=True)

            # === RETRY SCHEDULING (after creator extraction) ===
            # If initial discovery failed, schedule optimized retries with full context
            if schedule_retry_after_creator_extraction:
                bonding_curve_for_retry = bonding_curve_pda if bonding_curve_pda else None
                creator_for_retry = earliest_creator if earliest_creator else None
                migration_timestamp_for_retry = None
                if tx_data and 'blockTime' in tx_data:
                    try:
                        migration_timestamp_for_retry = int(tx_data['blockTime'])
                    except Exception:
                        pass

                # CRITICAL: Check if anchors are missing
                if not bonding_curve_for_retry or not creator_for_retry:
                    log_print(
                        f"⚠️ [RETRY_SCHEDULE] ⚠️ MISSING ANCHORS: "
                        f"bonding_curve={'MISSING' if not bonding_curve_for_retry else bonding_curve_for_retry[:16]+'...'} "
                        f"creator={'MISSING' if not creator_for_retry else creator_for_retry[:16]+'...'} "
                        f"(follow-on discovery will be weaker or impossible)",
                        flush=True
                    )

                log_print(
                    f"{Colors.DISCOVER}[RETRY_SCHEDULE] Scheduling retries with context: bonding_curve={bonding_curve_for_retry[:16] if bonding_curve_for_retry else 'None'}... creator={creator_for_retry[:16] if creator_for_retry else 'None'}...{Colors.RESET}",
                    flush=True
                )

                # === GUARD: Prevent duplicate retry tasks ===
                existing_task = self._retry_tasks_by_mint.get(mint)
                if existing_task and not existing_task.done():
                    log_print(
                        f"[RETRY_GUARD] ⏭️  Retry task already exists for {mint[:16]}..., skipping",
                        flush=True
                    )
                else:
                    # Schedule retries at optimized delays (don't await - fire and forget)
                    # Optimized schedule: denser early retries + extended late retries (0.5s intervals for first 8, then 3-5s intervals)
                    # Pass tx_data, tx_source, and discovery context to maximize pool discovery success
                    log_print(
                        f"🔴 [RETRY_CREATE_TASK] Creating asyncio task for {mint[:16]}... (THIS MUST LOG OR RETRY NEVER RUNS)",
                        flush=True
                    )
                    task = asyncio.create_task(self._retry_pool_discovery(
                        mint,
                        signature,
                        delays=[0.5, 1, 1.5, 2, 3, 5, 8, 12, 18, 25, 35, 50],
                        tx_source="cached" if tx_data else "miss",
                        tx_data=tx_data,
                        bonding_curve=bonding_curve_for_retry,
                        creator=creator_for_retry,
                        migration_timestamp=migration_timestamp_for_retry
                    ))
                    self._retry_tasks_by_mint[mint] = task
                    log_print(
                        f"🔴 [RETRY_CREATED] Task created: {task}, done={task.done()}",
                        flush=True
                    )

            # Analyze token history (includes creator behavior from all token transactions) - MUST be deferred during critical window
            # This is a background task that consumes RPC quota and must wait until critical window expires
            if get_migration_setting('token_history_check', True):
                log_print(f"[SETTINGS] Token history ✅ ON - queueing post-migration analysis (deferred)", flush=True)
                # Queue for deferred execution after critical window expires (45s)
                await self.queue_background_job(self.analyze_post_migration(mint, signature, pool_address), mint=mint, priority=5)
            else:
                log_print(f"[SETTINGS] Token history ❌ OFF - skipping post-migration analysis", flush=True)

            log_print(f"[MIGRATION] ✅ CRITICAL PATH COMPLETE - Token {mint[:8]}... with creator {earliest_creator[:8] if earliest_creator else 'unknown'}... is now visible in UI", flush=True)

            enqueue_creator = earliest_creator
            enqueue_source = "migration"
            # Always check DB — earliest_tx_creator may have been set via a path
            # that bypasses pool discovery (e.g. pre-migration RPC fallback)
            try:
                _db_row = db_connect(DB_PATH, timeout=5).execute(
                    "SELECT pf_ws_creator, earliest_tx_creator, create_tx_signature FROM token_analysis WHERE mint = ? LIMIT 1",
                    (mint,),
                ).fetchone()
                if _db_row:
                    db_creator = (str(_db_row[0]).strip() if _db_row[0] else "") or (str(_db_row[1]).strip() if _db_row[1] else "") or None
                    if not enqueue_creator and db_creator:
                        enqueue_creator = db_creator
                        enqueue_source = "migration_db_fallback"
            except Exception:
                pass
            if enqueue_creator:
                create_tx_sig = analyzer._create_tx_signature if analyzer and hasattr(analyzer, '_create_tx_signature') else None
                await self._enqueue_creator_funding_job(
                    enqueue_creator,
                    mint=mint,
                    migration_timestamp=created_at,
                    create_tx_signature=create_tx_sig,
                    delay_seconds=0,
                    source=enqueue_source,
                )
            else:
                log_print(f"[BACKGROUND] ⏭️ Skipping background tasks (no creator found)", flush=True)

        finally:
            # Clean up active discovery tracking
            self._active_pool_discoveries_by_mint.discard(mint)
            self._active_pool_discoveries_by_sig.discard(signature)

    async def _enrich_tx_data(self, tx_data: Optional[Dict]) -> Optional[Dict]:
        """
        Enrich tx_data by reconstructing meta.accounts from accountKeys + loadedAddresses.
        
        This is critical for pool candidate extraction, which relies on meta.accounts
        to find accounts referenced in inner instructions.
        
        Args:
            tx_data: Raw transaction data from RPC
            
        Returns:
            Enriched tx_data (modified in-place), or None if invalid
        """
        if not tx_data:
            return None

        has_meta = tx_data.get('meta') is not None
        has_block_time = tx_data.get('blockTime') is not None
        has_transaction = tx_data.get('transaction') is not None
        has_meta_accounts = (tx_data.get('meta') or {}).get('accounts') is not None
        meta_accounts_count = len((tx_data.get('meta') or {}).get('accounts') or [])
        tx_keys = list(tx_data.keys()) if tx_data else []

        log_print(
            f"🔴 [TX_DATA_VALIDATION] has_meta={has_meta} has_blockTime={has_block_time} "
            f"has_transaction={has_transaction} has_meta_accounts={has_meta_accounts} "
            f"meta_accounts_count={meta_accounts_count} "
            f"keys={tx_keys[:5]}{'...' if len(tx_keys) > 5 else ''}",
            flush=True
        )

        # If critical fields missing, log warning
        if not (has_meta and has_block_time and has_transaction):
            log_print(
                f"⚠️ [TX_DATA_INCOMPLETE] MISSING: "
                f"{'meta ' if not has_meta else ''}"
                f"{'blockTime ' if not has_block_time else ''}"
                f"{'transaction ' if not has_transaction else ''}",
                flush=True
            )

        # ENRICHMENT: If meta.accounts is missing, reconstruct from accountKeys + loadedAddresses
        if has_meta and not has_meta_accounts:
            try:
                message = tx_data.get('transaction', {}).get('message', {})
                account_keys = message.get('accountKeys', [])
                loaded_addresses = tx_data.get('meta', {}).get('loadedAddresses', {})

                # Build full account list (accountKeys + loaded addresses)
                all_accounts = []
                all_accounts.extend(account_keys)  # Original accounts
                all_accounts.extend(loaded_addresses.get('writable', []))  # Writable loaded
                all_accounts.extend(loaded_addresses.get('readonly', []))   # Readonly loaded

                # Create synthetic meta.accounts with owner info where available
                # This is a best-effort reconstruction
                synthetic_accounts = [{'pubkey': addr} for addr in all_accounts]

                if synthetic_accounts:
                    tx_data['meta']['accounts'] = synthetic_accounts
                    log_print(
                        f"🔧 [TX_DATA_ENRICHMENT] Reconstructed meta.accounts from accountKeys + loadedAddresses: "
                        f"{len(synthetic_accounts)} accounts",
                        flush=True
                    )
                    # Re-check
                    has_meta_accounts = (tx_data.get('meta') or {}).get('accounts') is not None
                    meta_accounts_count = len((tx_data.get('meta') or {}).get('accounts') or [])
                    log_print(
                        f"✅ [TX_DATA_ENRICHMENT] has_meta_accounts now={has_meta_accounts}, count={meta_accounts_count}",
                        flush=True
                    )
            except Exception as e:
                log_print(
                    f"⚠️ [TX_DATA_ENRICHMENT] Failed to reconstruct accounts: {e}",
                    flush=True
                )

        return tx_data

    async def _retry_pool_discovery(self, mint: str, original_migration_sig: str, delays: List[int], tx_source: str = "miss", tx_data: Optional[Dict] = None, bonding_curve: Optional[str] = None, creator: Optional[str] = None, migration_timestamp: Optional[int] = None):
        """
        PHASE 2: Critical-path protected retry discovery with tier-based strategies.

        Retry Tiers:
        1. Retries 1-5 (T=0.5-8s): TX-only (protect RPC quota, poll exact migration TX)
        2. Retries 6-7 (T=13-21s): TX + light RPC fallback (single RPC call)
        3. Retries 8-12 (T=33-161s): TX + full RPC fallback (complete discovery)

        Why tiers?
        - Early window: TX not indexed yet, vaults not ready. Don't waste RPC.
        - Middle window: TX indexed, vaults becoming ready. Light probing.
        - Late window: Full RPC enabled, background jobs may start processing.

        Args:
            mint: Token mint address
            original_migration_sig: Original migration transaction signature
            delays: List of delays in seconds for retries
            tx_source: Where the TX came from: "cached", "rpc", or "miss"
            tx_data: Optional pre-fetched transaction data (from cached handle_migration fetch)
            bonding_curve: Optional bonding curve PDA (primary anchor for follow-on discovery)
            creator: Optional earliest creator address (secondary anchor for follow-on discovery)
            migration_timestamp: Optional migration block time for context
        """
        log_print(
            f"🔴 [RETRY_START] CRITICAL: Retry loop started for {mint[:16]}... "
            f"sig={original_migration_sig[:16]}... "
            f"delays={len(delays)} "
            f"curve={bonding_curve[:16] if bonding_curve else 'None'}... "
            f"creator={creator[:16] if creator else 'None'}... "
            f"tx_data={'YES' if tx_data else 'NO'}",
            flush=True
        )

        # CRITICAL: Validate tx_data structure (not just presence)
        if tx_data:
            has_meta = tx_data.get('meta') is not None
            has_block_time = tx_data.get('blockTime') is not None
            has_transaction = tx_data.get('transaction') is not None
            has_meta_accounts = (tx_data.get('meta') or {}).get('accounts') is not None
            meta_accounts_count = len((tx_data.get('meta') or {}).get('accounts') or [])
            tx_keys = list(tx_data.keys()) if tx_data else []

            log_print(
                f"🔴 [TX_DATA_VALIDATION] has_meta={has_meta} has_blockTime={has_block_time} "
                f"has_transaction={has_transaction} has_meta_accounts={has_meta_accounts} "
                f"meta_accounts_count={meta_accounts_count} "
                f"keys={tx_keys[:5]}{'...' if len(tx_keys) > 5 else ''}",
                flush=True
            )

            # If critical fields missing, log warning
            if not (has_meta and has_block_time and has_transaction):
                log_print(
                    f"⚠️ [TX_DATA_INCOMPLETE] MISSING: "
                    f"{'meta ' if not has_meta else ''}"
                    f"{'blockTime ' if not has_block_time else ''}"
                    f"{'transaction ' if not has_transaction else ''}",
                    flush=True
                )

            # ENRICHMENT: If meta.accounts is missing, reconstruct from accountKeys + loadedAddresses
            if has_meta and not has_meta_accounts:
                try:
                    message = tx_data.get('transaction', {}).get('message', {})
                    account_keys = message.get('accountKeys', [])
                    loaded_addresses = tx_data.get('meta', {}).get('loadedAddresses', {})

                    # Build full account list (accountKeys + loaded addresses)
                    all_accounts = []
                    all_accounts.extend(account_keys)  # Original accounts
                    all_accounts.extend(loaded_addresses.get('writable', []))  # Writable loaded
                    all_accounts.extend(loaded_addresses.get('readonly', []))   # Readonly loaded

                    # Create synthetic meta.accounts with owner info where available
                    # This is a best-effort reconstruction
                    synthetic_accounts = [{'pubkey': addr} for addr in all_accounts]

                    if synthetic_accounts:
                        tx_data['meta']['accounts'] = synthetic_accounts
                        log_print(
                            f"🔧 [TX_DATA_ENRICHMENT] Reconstructed meta.accounts from accountKeys + loadedAddresses: "
                            f"{len(synthetic_accounts)} accounts",
                            flush=True
                        )
                        # Re-check
                        has_meta_accounts = (tx_data.get('meta') or {}).get('accounts') is not None
                        meta_accounts_count = len((tx_data.get('meta') or {}).get('accounts') or [])
                        log_print(
                            f"✅ [TX_DATA_ENRICHMENT] has_meta_accounts now={has_meta_accounts}, count={meta_accounts_count}",
                            flush=True
                        )
                except Exception as e:
                    log_print(
                        f"⚠️ [TX_DATA_ENRICHMENT] Failed to reconstruct accounts: {e}",
                        flush=True
                    )

        from src.core.post_migration_pool_discovery import PostMigrationPoolDiscovery
        from src.core.pool_detector import AMMPrograms

        # Track metrics for this token's discovery
        discovery_metrics = {
            'mint': mint,
            'tx_parsing_attempts': 0,
            'rpc_attempts': 0,
            'total_candidates_tested': 0,
            'rejections': {}
        }

        # Start critical window for this mint
        self.start_critical_window(mint)

        for attempt, delay in enumerate(delays, 1):
            try:
                await asyncio.sleep(delay)

                elapsed = time.time() - self.token_discovery_times[mint]["detected"]
                in_critical_window = self.is_in_critical_window(mint)

                # Determine retry tier based on attempt number
                if attempt <= 5:
                    tier = "TX_ONLY"
                    run_tx = True
                    run_rpc = False
                elif attempt <= 7:
                    tier = "TX_PLUS_LIGHT_RPC"
                    run_tx = True
                    run_rpc = True
                    rpc_mode = "light"
                else:
                    tier = "TX_PLUS_FULL_RPC"
                    run_tx = True
                    run_rpc = True
                    rpc_mode = "full"

                corr_id = self._correlation_id(mint, attempt=attempt, tier=tier, elapsed=elapsed)
                log_print(
                    f"{Colors.DISCOVER}[DISCOVERY] corr={corr_id} tx_source={tx_source} window={'ACTIVE' if in_critical_window else 'EXPIRED'}{Colors.RESET}",
                    flush=True
                )

                # ===== TIER: TX PARSING =====
                if run_tx:
                    try:
                        discovery = PostMigrationPoolDiscovery(RPC_HTTP)

                        # CRITICAL: Parse cached TX directly (no RPC, no refetch)
                        # This is the fastest path - pure extraction from cached payload
                        candidates_from_cached = []
                        cached_tx_parsed = False
                        cached_candidate_count = 0

                        cached_diagnostics = {}
                        if tx_data is not None:
                            # Use cached-only parsing: no RPC, no fallback
                            candidates_from_cached, cached_tx_parsed, cached_candidate_count, cached_diagnostics = await discovery.parse_candidates_from_cached_tx(tx_data)

                            # 🔥 PATCH 4: Filter out garbage candidates before validation
                            if candidates_from_cached:
                                SKIP = {
                                    mint,
                                    bonding_curve,
                                    "11111111111111111111111111111111",
                                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                                    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
                                }
                                candidates_from_cached = [c for c in candidates_from_cached if c not in SKIP]
                                cached_candidate_count = len(candidates_from_cached)

                                # 🔥 PATCH 5: Batch validate ALL candidates at once
                                if candidates_from_cached:
                                    valid_from_cached = await self.batch_validate_candidates(candidates_from_cached)
                                    if valid_from_cached:
                                        candidates_from_cached = valid_from_cached
                                        log_print(
                                            f"{Colors.DISCOVER}[BATCH_VALIDATED] Found {len(valid_from_cached)} valid pools from {len(candidates_from_cached)} candidates{Colors.RESET}",
                                            flush=True
                                        )

                            log_print(
                                f"{Colors.DISCOVER}[CACHED_TX_PARSE] cached_tx_present=yes cached_tx_parsed={cached_tx_parsed} cached_candidate_count={cached_candidate_count}{Colors.RESET}",
                                flush=True
                            )

                            # If zero candidates, log diagnostic reason
                            if cached_candidate_count == 0 and cached_diagnostics:
                                diag = cached_diagnostics
                                log_print(
                                    f"{Colors.DISCOVER}[CACHED_TX_DIAGNOSTICS] {diag.get('diagnostic_detail', 'unknown reason')}{Colors.RESET}",
                                    flush=True
                                )

                        # === POST-PARSE ROUTING DECISION ===
                        first_candidates = [c[:12] for c in candidates_from_cached[:5]] if candidates_from_cached else []
                        log_print(
                            f"[POST_PARSE_ROUTE] mint={mint[:16]}... "
                            f"cached_candidate_count={cached_candidate_count} "
                            f"first_candidates={first_candidates} "
                            f"will_run_follow_on={attempt >= 2 if 'attempt' in locals() else 'N/A'} "
                            f"curve_present={bonding_curve is not None} "
                            f"creator_present={creator is not None} "
                            f"tx_data_present={tx_data is not None}",
                            flush=True
                        )

                        # If cached parsing yielded candidates, validate them
                        # Otherwise try follow-on discovery (Phase 3)
                        if candidates_from_cached:
                            # 🔥 ORCHESTRATION: extract → validate → select (FAST-LANE OPTIMIZED)
                            log_print(f"🔴 [PRE_RESOLVE] Calling fast_lane_resolve_with_retries with tx_data={tx_data is not None}", flush=True)
                            pool = await self.fast_lane_resolve_with_retries(mint=mint, tx_data=tx_data, max_wait_secs=10.0)
                            if isinstance(pool, dict):
                                pool = pool.get("address") or None
                            if pool:
                                log_print(f"[POOL_RESOLVED] ✅ Found valid pool via cached TX: {pool[:16]}...", flush=True)
                                pool_candidates = [pool]
                                using_cached_payload = True
                                log_print(f"🔴 [POST_RESOLVE] pool_candidates set to [{pool[:16]}...], will validate", flush=True)
                            else:
                                log_print(f"[POOL_RESOLVED] ⚠️  Cached candidates didn't validate, trying follow-on", flush=True)
                                pool_candidates = []
                                using_cached_payload = False
                        else:
                            pool_candidates = []
                            using_cached_payload = False

                        # If resolved, skip follow-on
                        if not pool_candidates:
                            # Phase 3: Try follow-on transaction discovery
                            # KEY INSIGHT: Use cached diagnostics to determine strategy
                            pool_candidates = []
                            follow_on_pool = None
                            follow_on_anchor = None
                            follow_on_txs_scanned = 0

                            # Extract reason code from cached diagnostics
                            reason_code = None
                            if cached_diagnostics and isinstance(cached_diagnostics, dict):
                                reason_code = cached_diagnostics.get("reason_code")

                            # Runtime conditions for follow-on
                            tx_data_present = tx_data is not None
                            curve_present = bonding_curve is not None
                            creator_present = creator is not None

                            # 🔴 CRITICAL FIX: Routing logic
                            # If no candidates in cached TX, check if we have anchors
                            if cached_candidate_count == 0:
                                if bonding_curve or creator:
                                    follow_on_max_txs = 12  # immediately
                                else:
                                    follow_on_max_txs = 0
                            else:
                                follow_on_max_txs = 0

                            log_print(
                                f"[POST_PARSE_ROUTE] mint={mint[:16]}... "
                                f"cached_candidate_count={cached_candidate_count} "
                                f"candidates_from_cached={len(candidates_from_cached) if candidates_from_cached else 0} "
                                f"will_run_follow_on={follow_on_max_txs > 0} "
                                f"curve_present={curve_present} "
                                f"creator_present={creator_present} "
                                f"tx_data_present={tx_data_present} "
                                f"reason_code={reason_code}",
                                flush=True
                            )

                            log_print(
                                f"[FOLLOW_ON_CHECK] mint={mint[:16]}... "
                                f"follow_on_max_txs={follow_on_max_txs} "
                                f"tx_data={tx_data_present} "
                                f"cached_count={cached_candidate_count}",
                                flush=True
                            )

                            # CRITICAL: Verify tx_data integrity before follow-on
                            if tx_data_present:
                                tx_has_meta = tx_data.get('meta') is not None
                                tx_has_block_time = tx_data.get('blockTime') is not None
                                tx_has_transaction = tx_data.get('transaction') is not None

                                if not (tx_has_meta and tx_has_transaction):
                                    log_print(
                                        f"⚠️ [FOLLOW_ON_SKIP] tx_data incomplete: "
                                        f"meta={tx_has_meta} transaction={tx_has_transaction}",
                                        flush=True
                                    )

                            # Check follow-on condition
                            follow_on_condition = (
                                follow_on_max_txs > 0
                                and tx_data_present
                                and cached_candidate_count == 0
                                and (curve_present or creator_present)
                            )

                            if not follow_on_condition:
                                reasons = []
                                if follow_on_max_txs == 0:
                                    reasons.append(f"follow_on_max_txs={follow_on_max_txs}")
                                if not tx_data_present:
                                    reasons.append("tx_data=None")
                                if cached_candidate_count > 0:
                                    reasons.append(f"cached_candidate_count={cached_candidate_count}")
                                if not (curve_present or creator_present):
                                    reasons.append("no_anchor")
                                log_print(
                                    f"⏭️ [FOLLOW_ON_SKIP] Not running follow-on discovery: {', '.join(reasons)}",
                                    flush=True
                                )

                            # Run follow-on if condition met
                            if follow_on_condition:
                                bonding_curve_for_follow_on = bonding_curve
                                creator_for_follow_on = creator

                                try:
                                    follow_on_pool, follow_on_anchor, follow_on_offset, follow_on_txs_scanned = await discovery.discover_follow_on_pools(
                                        mint=mint,
                                        migration_sig=original_migration_sig,
                                        bonding_curve=bonding_curve_for_follow_on,
                                        creator=creator_for_follow_on,
                                        token_mint=mint,
                                        max_txs_per_anchor=follow_on_max_txs,
                                    )

                                    if follow_on_pool:
                                        log_print(
                                            f"{Colors.DISCOVER}[FOLLOW_ON_SUCCESS] Found pool {follow_on_pool[:16]}... "
                                            f"via anchor={follow_on_anchor} at offset={follow_on_offset}{Colors.RESET}",
                                            flush=True
                                        )
                                        pool_candidates = [follow_on_pool]
                                        using_cached_payload = True
                                    else:
                                        log_print(
                                            f"{Colors.DISCOVER}[FOLLOW_ON_EXHAUSTED] "
                                            f"reason_code={reason_code} "
                                            f"anchor={follow_on_anchor} "
                                            f"scanned={follow_on_txs_scanned} TXs, no valid pool found{Colors.RESET}",
                                            flush=True
                                        )
                                        pool_candidates = []
                                        using_cached_payload = tx_data_present

                                except Exception as e:
                                    logger.error(f"[FOLLOW_ON_DISCOVERY] Error: {e}")
                                    pool_candidates = []
                                    using_cached_payload = tx_data_present

                            # IMPROVED: Only fall back to migration-TX parsing if reason was NOT "no_amm_program_in_tx"
                            # If it was, we already know the pool isn't there - don't waste RPC calls re-parsing it
                            if not pool_candidates:
                                using_cached_payload = tx_data_present

                                if reason_code == "no_amm_program_in_tx":
                                    log_print(
                                        f"[MIGRATION_TX_PARSE_SKIP] mint={mint[:16]}... "
                                        f"reason_code=no_amm_program_in_tx so skipping re-parse of migration TX",
                                        flush=True
                                    )
                                    pool_candidates = []
                                else:
                                    pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(
                                        mint=mint,
                                        migration_sig=original_migration_sig,
                                        tx_data=tx_data
                                    )

                        candidates_tested = 0
                        rejection_reasons = []

                        log_print(
                            f"🔴 [VALIDATION_LOOP] mint={mint[:16]}... pool_candidates={len(pool_candidates) if pool_candidates else 0} "
                            f"candidates_from_cached={len(candidates_from_cached) if candidates_from_cached else 0}",
                            flush=True
                        )

                        if pool_candidates:
                            # 🔥 BATCH validate all candidates at once (not serial)
                            log_print(
                                f"[BATCH_VALIDATION] Validating {len(pool_candidates)} candidates in parallel",
                                flush=True
                            )

                            try:
                                batch_result = await self.call_discovery_rpc(
                                    "getMultipleAccounts",
                                    [pool_candidates, {"encoding": "base64"}],
                                    timeout=10
                                )

                                if batch_result and "result" in batch_result:
                                    accounts_info = batch_result.get("result", {}).get("value", [])

                                    for candidate, acct in zip(pool_candidates, accounts_info):
                                        candidates_tested += 1
                                        discovery_metrics['total_candidates_tested'] += 1

                                        if not acct:
                                            log_print(
                                                f"[CANDIDATE_REJECT] {candidate[:16]}... reason=tx_not_indexed",
                                                flush=True
                                            )
                                            rejection_reasons.append("tx_not_indexed")
                                            discovery_metrics['rejections']['tx_not_indexed'] = discovery_metrics['rejections'].get('tx_not_indexed', 0) + 1
                                            continue

                                        owner = acct.get("owner")
                                        log_print(
                                            f"[CANDIDATE_OWNER] {candidate[:16]}... owner={owner[:16] if owner else 'None'}... valid={owner in AMMPrograms.ALL if owner else False}",
                                            flush=True
                                        )

                                        if owner not in AMMPrograms.ALL:
                                            rejection_reasons.append("owner_mismatch")
                                            discovery_metrics['rejections']['owner_mismatch'] = discovery_metrics['rejections'].get('owner_mismatch', 0) + 1
                                            continue

                                        # Owner valid - try registration
                                        log_print(
                                            f"[CANDIDATE_VALID] {candidate[:16]}... owner valid, attempting registration",
                                            flush=True
                                        )
                                        try:
                                            from src.core.pool_discovery import PoolDiscovery
                                            discovery_pipeline = PoolDiscovery(DB_PATH, RPC_HTTP)
                                            registered = await discovery_pipeline.discover_and_register_pool(
                                                candidate, mint
                                            )
                                            if registered:
                                                # SUCCESS!
                                                # Record timing for retry path
                                                resolved_at = time.time()
                                                self.token_discovery_times[mint]["resolved_at"] = resolved_at

                                                log_print(
                                                    f"[POOL_REGISTERED] {candidate[:16]}... registered successfully",
                                                    flush=True
                                                )
                                                self.token_states[mint] = "resolved"
                                                self.token_discovery_times[mint]["resolved"] = resolved_at
                                                elapsed = resolved_at - self.token_discovery_times[mint]["detected"]

                                                # Log timing details
                                                log_print(
                                                    f"[TIMING] resolved={resolved_at}, detected={self.token_discovery_times[mint]['detected']}, elapsed={elapsed:.2f}s",
                                                    flush=True
                                                )

                                                corr = self._correlation_id(mint, attempt=attempt, tier="TX", elapsed=elapsed)
                                                log_print(
                                                    f"{Colors.DISCOVER}[DISCOVERY_SUCCESS] corr={corr} strategy=tx_parsing pool={candidate[:16]}...{Colors.RESET}",
                                                    flush=True
                                                )
                                                log_print(
                                                    f"{Colors.DETECT}[STATE] Token {mint[:16]}... → resolved (TX parsing attempt {attempt} in {elapsed:.1f}s){Colors.RESET}",
                                                    flush=True
                                                )
                                                await self._write_resolution_telemetry(mint, "tx_parsing", candidate, attempt - 1)

                                                # Log fast-lane metrics
                                                self.log_discovery_metrics(mint)

                                                # Trigger WebSocket refresh to subscribe to new pool
                                                if self.price_worker:
                                                    log_print(f"[POOL_REGISTERED] Triggering price worker WebSocket refresh for {mint[:16]}...", flush=True)
                                                    try:
                                                        self.price_worker.trigger_pool_refresh()
                                                    except Exception as e:
                                                        log_print(f"[POOL_REGISTERED] ⚠️  Price worker refresh failed: {e}", flush=True)

                                                return

                                            else:
                                                rejection_reasons.append("registration_failed")
                                                discovery_metrics['rejections']['registration_failed'] = discovery_metrics['rejections'].get('registration_failed', 0) + 1

                                        except Exception as reg_err:
                                            rejection_reasons.append("registration_error")
                                            discovery_metrics['rejections']['registration_error'] = discovery_metrics['rejections'].get('registration_error', 0) + 1

                            except Exception as batch_err:
                                log_print(
                                    f"[BATCH_VALIDATION] ⚠️  Batch validation failed: {batch_err}",
                                    flush=True
                                )
                                rejection_reasons.append("batch_rpc_error")
                                discovery_metrics['rejections']['batch_rpc_error'] = discovery_metrics['rejections'].get('batch_rpc_error', 0) + 1

                            # TX round summary
                            discovery_metrics['tx_parsing_attempts'] += 1
                            corr = self._correlation_id(mint, attempt=attempt)
                            log_print(
                                f"{Colors.DISCOVER}[DISCOVERY_TX] corr={corr} using_cached_payload={using_cached_payload} parsed_candidates={len(pool_candidates)} tested={candidates_tested} rejections={','.join(set(rejection_reasons))}{Colors.RESET}",
                                flush=True
                            )
                        else:
                            # No candidates (TX not indexed yet)
                            discovery_metrics['tx_parsing_attempts'] += 1
                            log_print(
                                f"{Colors.DISCOVER}[DISCOVERY_TX] attempt={attempt} candidates=0 (tx_not_indexed){Colors.RESET}",
                                flush=True
                            )

                    except Exception as tx_err:
                        discovery_metrics['tx_parsing_attempts'] += 1
                        log_print(
                            f"{Colors.DISCOVER}[DISCOVERY_TX_ERROR] attempt={attempt} error={str(tx_err)[:50]}{Colors.RESET}",
                            flush=True
                        )

                # ===== TIER: RPC FALLBACK =====
                if run_rpc:
                    try:
                        from src.core.vault_discovery import discover_and_register_all_pools

                        class SimpleRPCClient:
                            def __init__(self, listener_instance):
                                self.listener = listener_instance

                            async def call_async(self, method, params):
                                result = await self.listener.call_discovery_rpc(method, params, timeout=10)
                                return result.get("result") if result else None

                            async def get_account_info(self, address, encoding="base64", commitment="confirmed"):
                                result = await self.call_async(
                                    "getAccountInfo",
                                    [address, {"encoding": encoding, "commitment": commitment}]
                                )
                                if result:
                                    acct_data = result.get("value", {})
                                    return type('Account', (), {
                                        'owner': acct_data.get('owner'),
                                        'lamports': acct_data.get('lamports'),
                                        'data': acct_data.get('data', ['', ''])[0] if isinstance(acct_data.get('data'), list) else acct_data.get('data', ''),
                                    })()
                                return None

                            async def get_multiple_accounts(self, addresses, encoding="base64", commitment="confirmed"):
                                result = await self.call_async(
                                    "getMultipleAccounts",
                                    [addresses, {"encoding": encoding, "commitment": commitment}]
                                )
                                if result and "value" in result:
                                    accounts = []
                                    for acct_data in result["value"]:
                                        if acct_data:
                                            accounts.append(type('Account', (), {
                                                'owner': acct_data.get('owner'),
                                                'lamports': acct_data.get('lamports'),
                                                'data': acct_data.get('data', ['', ''])[0],
                                            })())
                                        else:
                                            accounts.append(None)
                                    return accounts
                                return []

                        rpc_client = SimpleRPCClient(self)
                        price_worker = self.price_worker

                        rpc_success = await discover_and_register_all_pools(
                            token_mint=mint,
                            rpc_client=rpc_client,
                            db=DB_PATH,
                            price_worker=price_worker,
                            max_retries=1
                        )

                        if rpc_success:
                            # Record timing for RPC success path
                            resolved_at = time.time()
                            self.token_discovery_times[mint]["resolved_at"] = resolved_at

                            self.token_states[mint] = "resolved"
                            self.token_discovery_times[mint]["resolved"] = resolved_at
                            elapsed = resolved_at - self.token_discovery_times[mint]["detected"]

                            # Log timing details
                            log_print(
                                f"[TIMING] resolved={resolved_at}, detected={self.token_discovery_times[mint]['detected']}, elapsed={elapsed:.2f}s",
                                flush=True
                            )

                            discovery_metrics['rpc_attempts'] += 1
                            corr = self._correlation_id(mint, attempt=attempt, tier="RPC", elapsed=elapsed)
                            log_print(
                                f"{Colors.DISCOVER}[DISCOVERY_RPC_SUCCESS] corr={corr} strategy={rpc_mode}_rpc{Colors.RESET}",
                                flush=True
                            )
                            log_print(
                                f"{Colors.DETECT}[STATE] Token {mint[:16]}... → resolved (RPC fallback attempt {attempt} in {elapsed:.1f}s){Colors.RESET}",
                                flush=True
                            )
                            await self._write_resolution_telemetry(mint, "rpc_discovery", None, attempt - 1)
                            return
                        else:
                            discovery_metrics['rpc_attempts'] += 1
                            log_print(
                                f"{Colors.DISCOVER}[DISCOVERY_RPC] attempt={attempt} strategy={rpc_mode}_rpc rejected=vaults_not_ready{Colors.RESET}",
                                flush=True
                            )

                    except Exception as rpc_err:
                        discovery_metrics['rpc_attempts'] += 1
                        log_print(
                            f"{Colors.DISCOVER}[DISCOVERY_RPC_ERROR] attempt={attempt} error={str(rpc_err)[:50]}{Colors.RESET}",
                            flush=True
                        )

                # Allow background jobs to process after critical window
                if elapsed > self.DISCOVERY_CRITICAL_WINDOW_SECONDS and not in_critical_window:
                    try:
                        while not self.background_job_queue.empty():
                            job_item = self.background_job_queue.get_nowait()
                            try:
                                await job_item['coro']
                            except Exception as e:
                                logger.error(f"Background job failed: {e}")
                            self.background_job_queue.task_done()
                    except asyncio.QueueEmpty:
                        pass

            except asyncio.CancelledError:
                log_print(f"{Colors.DISCOVER}[DISCOVERY_CANCELLED] Cancelled at attempt {attempt}{Colors.RESET}", flush=True)
                return
            except Exception as e:
                log_print(
                    f"{Colors.DISCOVER}[DISCOVERY_ERROR] attempt={attempt} error={str(e)[:50]}{Colors.RESET}",
                    flush=True
                )

        # All retries exhausted - classify failure
        # Determine failure class based on what was tried
        failure_class = "unknown_exhaustion"

        if discovery_metrics['tx_parsing_attempts'] > 0 and discovery_metrics['rpc_attempts'] == 0:
            failure_class = "no_cached_tx_candidates_never_tried_rpc"
        elif discovery_metrics['tx_parsing_attempts'] > 0 and discovery_metrics['rpc_attempts'] > 0:
            vaults_not_ready_count = discovery_metrics['rejections'].get('vaults_not_ready', 0)
            if vaults_not_ready_count > 0:
                failure_class = "rpc_vaults_never_ready"
            else:
                failure_class = "all_candidates_rejected_or_failed"
        else:
            failure_class = "no_discovery_attempted"

        log_print(
            f"{Colors.DISCOVER}[DISCOVERY_FAILED] ❌ All {len(delays)} attempts exhausted for {mint[:16]}... (failure_class={failure_class}){Colors.RESET}",
            flush=True
        )
        log_print(
            f"{Colors.DISCOVER}[DISCOVERY_METRICS] {mint[:16]}... → tx_attempts={discovery_metrics['tx_parsing_attempts']} rpc_attempts={discovery_metrics['rpc_attempts']} candidates_tested={discovery_metrics['total_candidates_tested']} rejections={discovery_metrics['rejections']} failure_class={failure_class}{Colors.RESET}",
            flush=True
        )

        # Clean up retry task tracking when done
        existing = self._retry_tasks_by_mint.get(mint)
        if existing is asyncio.current_task():
            self._retry_tasks_by_mint.pop(mint, None)

    async def handle_migration(self, signature: str, logs: list):
        """Process detected migration."""
        if signature in self.processing_migrations or signature in self.completed_migrations:
            return

        self.processing_migrations.add(signature)

        try:

            # === CRITICAL OPTIMIZATION: Cache TX fetch ===
            # Fetch TX once and reuse for mint, pool, blockTime extraction
            tx_data = await self._get_transaction_cached(signature)

            if tx_data:
                mint = await self._extract_mint_from_tx(tx_data)
            else:
                mint = None

            if not mint:
                log_print(f"[MIGRATION] ⚠ Failed to extract mint from cached tx, trying logs fallback", flush=True)
                mint = self._extract_mint_from_logs(logs)

            if not mint:
                log_print(f"[MIGRATION] ⚠ Could not extract mint from {signature}, scheduling delayed re-check...", flush=True)

                # Schedule delayed re-check (fire-and-forget)
                async def delayed_mint_recheck():
                    """Re-attempt mint extraction after 45 seconds for delayed indexing"""
                    await asyncio.sleep(45)
                    try:
                        # Use discovery RPC for delayed re-check (still critical path)
                        raw = await self.call_discovery_rpc(
                            "getTransaction",
                            [
                                signature,
                                {
                                    "encoding": "jsonParsed",
                                    "commitment": "finalized",
                                    "maxSupportedTransactionVersion": 0,
                                },
                            ],
                            timeout=20
                        )
                        tx_data_retry = raw.get("result") if raw and "result" in raw else None

                        if not tx_data_retry:
                            log_print(f"[MIGRATION] ⚠ Delayed re-check still has no tx: {signature}", flush=True)
                            return

                        mint_retry = await self._extract_mint_from_tx(tx_data_retry)
                        if not mint_retry:
                            mint_retry = self._extract_mint_from_logs(logs)

                        if not mint_retry:
                            log_print(f"[MIGRATION] ⚠ Could not extract mint from {signature} after delayed re-check - SKIPPED", flush=True)
                            return

                        log_print(f"[MIGRATION] ✅ Delayed re-check succeeded for {signature}: {mint_retry}", flush=True)
                        await self._process_migration_with_mint(signature, logs, mint_retry, tx_data_retry)
                        self.completed_migrations.add(signature)

                    except Exception as e:
                        log_print(f"[MIGRATION] ⚠ Delayed re-check failed: {e}", flush=True)
                    finally:
                        self.processing_migrations.discard(signature)
                        self.tx_cache_pending_retries.pop(signature, None)

                # Fire-and-forget task, track to avoid duplicates
                if signature not in self.tx_cache_pending_retries:
                    task = asyncio.create_task(delayed_mint_recheck())
                    self.tx_cache_pending_retries[signature] = task
                return

            # Mint found immediately - continue with normal pipeline
            await self._process_migration_with_mint(signature, logs, mint, tx_data)
            self.completed_migrations.add(signature)

            # Cancel any pending delayed retry since migration succeeded
            pending = self.tx_cache_pending_retries.pop(signature, None)
            if pending and not pending.done():
                pending.cancel()

        except Exception as e:
            log_print(f"[MIGRATION] ⚠ Error handling migration: {e}", flush=True)
            import traceback
            traceback.print_exc()
        finally:
            if signature in self.completed_migrations:
                self.processing_migrations.discard(signature)

    # --- WebSocket Listener ---
    def get_tx_cache_stats(self) -> Dict:
        """Return current transaction cache statistics."""
        total = sum(self.tx_cache_stats.values())
        hit_rate = (self.tx_cache_stats['hit'] / total * 100) if total > 0 else 0

        return {
            'tx_cache_hit': self.tx_cache_stats['hit'],
            'tx_cache_miss': self.tx_cache_stats['miss'],
            'tx_cache_wait': self.tx_cache_stats['wait'],
            'tx_cache_size': len(self.tx_cache),
            'tx_cache_hit_rate_pct': round(hit_rate, 2),
            'rpc_calls_avoided': self.tx_cache_stats['hit'],
            'credits_saved': self.tx_cache_stats['hit'] * 10,
        }

    def _websocket_endpoints(self) -> list:
        return [
            (HELIUS_RPC_WS, "Helius"),
            ("wss://api.mainnet-beta.solana.com/", "Public Solana"),
        ]

    async def _wait_for_launch_toggle(self, label: str) -> None:
        # Listener always runs 24/7 — not gated by the dashboard toggle.
        pass

    async def listen_pumpswap_websocket(self):
        """Dedicated PumpSwap websocket for migration intake."""
        await self._wait_for_launch_toggle("PUMPSWAP")
        log_print(f"\n[WEBSOCKET][PUMPSWAP] Connecting to PumpSwap program...", flush=True)

        endpoints = self._websocket_endpoints()
        current_endpoint_idx = 0
        reconnect_delay = 5

        while True:
            try:
                endpoint, name = endpoints[current_endpoint_idx]
                async with websockets.connect(
                    endpoint,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    self.websocket_connected = True
                    reconnect_delay = 5
                    log_print(f"[WEBSOCKET][PUMPSWAP] ✓ Connected via {name}", flush=True)

                    record_request(
                        section='listener',
                        provider='helius_rpc',
                        method='logsSubscribe',
                        status_code=200,
                        latency_ms=0,
                        source_file='pumpfun_curve_listener'
                    )

                    subscribe_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [PUMPFUN_MIGRATION_ACCOUNT]},
                            {"commitment": "confirmed"}
                        ]
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    log_print(f"[WEBSOCKET][PUMPSWAP] Subscribed to PumpFun migration account", flush=True)
                    premig_log("[WS_SUBSCRIBED] program=pumpswap waiting for confirmation")

                    subscription_id = None
                    while subscription_id is None:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=10)
                            data = json.loads(msg)
                            if "result" in data and data.get("id") == 1:
                                subscription_id = data.get("result")
                                log_print(f"[WEBSOCKET][PUMPSWAP] ✓ Subscription confirmed (id={subscription_id})", flush=True)
                                premig_log(f"[WS_SUBSCRIBED] program=pumpswap subscription_id={subscription_id}")
                                break
                        except asyncio.TimeoutError:
                            log_print(f"[WEBSOCKET][PUMPSWAP] ⚠ No subscription confirmation after 10s", flush=True)
                            continue

                    if subscription_id is None:
                        continue

                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60)
                            record_wss('pumpswap_logs', 'pumpfun_curve_listener', msg_count=1, est_bytes=len(msg))
                            data = json.loads(msg)

                            if 'params' not in data or 'result' not in data['params']:
                                continue

                            self.websocket_msg_count += 1
                            result = data['params']['result']
                            value = result.get('value', {})
                            logs = value.get('logs', [])
                            signature = value.get('signature', '')
                            err = value.get('err')
                            raw_sub_id = data['params'].get('subscription')

                            if raw_sub_id != subscription_id:
                                premig_log(f"[WS_MESSAGE_DROPPED] reason=unknown_subscription sub_id={raw_sub_id} expected={subscription_id} kind=pumpswap")
                                continue

                            if err or not signature:
                                if err:
                                    premig_log(f"[PUMPSWAP_ERR] sig={signature[:16] if signature else 'none'} err={json.dumps(err)[:400]}")
                                premig_log(f"[WS_MESSAGE_DROPPED] reason={'err' if err else 'no_sig'} kind=pumpswap")
                                continue

                            logs_text = ' '.join(logs or [])
                            if signature and signature[-1] in {'0', '1', '2'}:
                                premig_log(
                                    f"[PUMPSWAP_OK_SAMPLE] sig={signature[:16]} logs={json.dumps((logs or [])[:12])[:900]}"
                                )
                            if (
                                'Instruction: Migrate' in logs_text
                                or 'CreatePool' in logs_text
                                or 'InitializePool' in logs_text
                                or 'MigrateBondingCurveCreator' in logs_text
                            ):
                                premig_log(
                                    f"[PUMPSWAP_EVENT] sig={signature[:16]} logs={json.dumps((logs or [])[:20])[:1200]}"
                                )

                            # Migration account subscription only receives graduation txs —
                            # Instruction: Migrate is sufficient, no need for CreatePool check
                            logs_text_check = ' '.join(logs or [])
                            is_migration = (
                                'Instruction: Migrate' in logs_text_check
                                and 'Instruction: Buy' not in logs_text_check
                                and 'Instruction: Sell' not in logs_text_check
                                and 'MigrateBondingCurveCreator' not in logs_text_check
                            )

                            if is_migration:
                                listen_enabled = get_migration_setting('listen_to_launches', True)
                                log_print(f"[WEBSOCKET] 🔍 Migration found (migration account sub). listen_to_launches={listen_enabled}", flush=True)

                                if not listen_enabled:
                                    log_print(f"[WEBSOCKET] ⏸ Migration detected but launch listening disabled: {signature}", flush=True)
                                    continue

                                self.websocket_migration_count += 1
                                premig_log(f"[WS_MESSAGE_ROUTED] sig={signature[:16]} route=handle_migration source=pumpswap_migration_account")
                                log_print(f"[WEBSOCKET] 🚨 Migration #{self.websocket_migration_count} detected via migration account: {signature}", flush=True)
                                asyncio.create_task(self.handle_migration(signature, logs))
                                continue

                            self._debug_pumpswap_migration_skip(signature, logs)

                        except asyncio.TimeoutError:
                            continue
                        except json.JSONDecodeError:
                            continue
                        except websockets.exceptions.ConnectionClosed as e:
                            log_print(f"[WEBSOCKET][PUMPSWAP] 🔌 Connection closed ({e.code if hasattr(e, 'code') else e}), reconnecting...", flush=True)
                            break
                        except Exception as e:
                            error_msg = str(e).lower()
                            if "keepalive" not in error_msg and "close frame" not in error_msg:
                                log_print(f"[WEBSOCKET][PUMPSWAP] ⚠ Error processing message: {e}", flush=True)
                            if "close frame" in error_msg or "connection closed" in error_msg or "going away" in error_msg:
                                break
                            continue

            except Exception as e:
                self.websocket_connected = False
                error_str = str(e).lower()
                if "401" in str(e) or "unauthorized" in error_str:
                    log_print(f"[WEBSOCKET][PUMPSWAP] ⚠ Auth error (401) - falling back to public RPC", flush=True)
                    current_endpoint_idx = 1
                    reconnect_delay = 5
                elif "connection" in error_str or "refused" in error_str:
                    log_print(f"[WEBSOCKET][PUMPSWAP] ⚠ Connection refused, retrying in {reconnect_delay}s...", flush=True)
                elif "close frame" not in error_str:
                    log_print(f"[WEBSOCKET][PUMPSWAP] ⚠ {name} connection error: {e}", flush=True)
                    log_print(f"[WEBSOCKET][PUMPSWAP] Retrying in {reconnect_delay}s...", flush=True)

                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 30)

    async def listen_pumpportal_websocket(self):
        """
        Single PumpPortal WSS connection replacing all Helius pump.fun subscriptions:
          - subscribeNewToken  → handle_birth (no RPC getTransaction needed)
          - subscribeMigration → handle_migration
          - subscribeTokenTrade on near-complete tokens → handle_migration when vSol >= threshold

        Zero Helius WSS credits consumed for pump.fun events.
        """
        PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
        # pump.fun bonding curve fills at ~85 SOL virtual reserves
        MIGRATION_SOL_THRESHOLD = 60.0
        # Mints we are tracking trades for (near migration)
        tracked_trade_mints: set = set()
        reconnect_delay = 5

        while True:
            try:
                async with websockets.connect(
                    PUMPPORTAL_WS,
                    ping_interval=20,
                    ping_timeout=30,
                    close_timeout=10,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    reconnect_delay = 5
                    log_print("[PUMPPORTAL] ✓ Connected", flush=True)

                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    log_print("[PUMPPORTAL] Subscribed to newToken + migration", flush=True)

                    # Seed trade subscriptions for recently active bonding curve tokens
                    # so we get vSol updates immediately without waiting for new births
                    try:
                        seed_conn = db_connect(DB_PATH, timeout=5)
                        seed_cur = seed_conn.cursor()
                        # Prioritise tokens missing creator first (most likely to need fast-path),
                        # then recent active tokens. Limit 200 to cover post-downtime gaps.
                        seed_cur.execute("""
                            SELECT mint FROM token_analysis
                            WHERE source_platform = 'pumpfun'
                              AND lifecycle_stage = 'bonding_curve'
                            ORDER BY
                                CASE WHEN (pf_ws_creator IS NULL OR pf_ws_creator = '') THEN 0 ELSE 1 END ASC,
                                analyzed_at DESC
                            LIMIT 200
                        """)
                        seed_mints = [r[0] for r in seed_cur.fetchall()]
                        seed_conn.close()
                        if seed_mints:
                            # PumpPortal accepts up to 100 keys per message — batch if needed
                            for i in range(0, len(seed_mints), 100):
                                batch = seed_mints[i:i+100]
                                await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": batch}))
                            tracked_trade_mints.update(seed_mints)
                            log_print(f"[PUMPPORTAL] Seeded trade subscriptions for {len(seed_mints)} active tokens ({sum(1 for m in seed_mints if m not in tracked_trade_mints)} missing creator)", flush=True)
                    except Exception as _e:
                        log_print(f"[PUMPPORTAL] ⚠ Seed subscription failed: {_e}", flush=True)

                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            log_print("[PUMPPORTAL] ⚠ No message in 60s — reconnecting", flush=True)
                            break

                        try:
                            data = json.loads(msg)
                        except Exception:
                            continue

                        # Ignore subscription confirmation messages
                        if "message" in data and "txType" not in data:
                            continue

                        tx_type = data.get("txType")
                        record_wss(
                            f"pumpportal_{tx_type or 'other'}",
                            "pumpfun_curve_listener",
                            msg_count=1,
                            est_bytes=len(msg),
                        )
                        sig = data.get("signature", "")
                        mint = data.get("mint", "")

                        if tx_type == "create":
                            creator = data.get("traderPublicKey")
                            bonding_curve_pda = data.get("bondingCurveKey")
                            symbol = data.get("symbol")
                            name = data.get("name")
                            v_sol = float(data.get("vSolInBondingCurve") or 0)
                            mc_sol = float(data.get("marketCapSol") or 0)

                            if mint:
                                self._portal_vsol[mint] = {
                                    "v_sol": v_sol,
                                    "mc_sol": mc_sol,
                                    "symbol": symbol or "",
                                    "name": name or "",
                                    "creator": creator or "",
                                    "ts": int(time.time()),
                                }

                            if mint and sig and mint not in self.completed_launches:
                                self.completed_launches.add(sig)
                                self.seen_mints.add(mint)
                                if bonding_curve_pda:
                                    self._remember_bonding_curve_token(mint, bonding_curve_pda)
                                try:
                                    await self._insert_bonding_curve_token(
                                        mint, creator, str(int(time.time())),
                                        bonding_curve_pda=bonding_curve_pda,
                                        create_tx_signature=sig,
                                        symbol=symbol,
                                        name=name,
                                    )
                                    log_print(
                                        f"[PUMPPORTAL] 🟢 Birth: {mint[:16]}... symbol={symbol} creator={creator[:8] if creator else '?'}",
                                        flush=True,
                                    )
                                    # Trigger creator pipeline
                                    asyncio.create_task(
                                        self._ensure_pf_ws_creator(mint, reason="birth")
                                    )
                                except Exception as e:
                                    log_print(f"[PUMPPORTAL] ⚠ Birth insert error {mint[:16]}: {e}", flush=True)

                                # Subscribe to trades if already near migration threshold
                                if v_sol >= MIGRATION_SOL_THRESHOLD and mint not in tracked_trade_mints:
                                    tracked_trade_mints.add(mint)
                                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))

                        elif tx_type in ("buy", "sell") and mint:
                            v_sol = float(data.get("vSolInBondingCurve") or 0)
                            mc_sol = float(data.get("marketCapSol") or 0)
                            # Update live vSol state
                            existing = self._portal_vsol.get(mint, {})
                            self._portal_vsol[mint] = {
                                "v_sol": v_sol,
                                "mc_sol": mc_sol,
                                "symbol": existing.get("symbol", ""),
                                "name": existing.get("name", ""),
                                "creator": existing.get("creator", "") or data.get("traderPublicKey", ""),
                                "ts": int(time.time()),
                            }
                            # If not yet tracking this token but it's near migration, subscribe
                            if v_sol >= MIGRATION_SOL_THRESHOLD and mint not in tracked_trade_mints:
                                tracked_trade_mints.add(mint)
                                await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))

                        elif tx_type == "migration" and sig:
                            if sig not in self.completed_migrations:
                                log_print(f"[PUMPPORTAL] 🚀 Migration: {mint[:16]}... sig={sig[:16]}", flush=True)
                                asyncio.create_task(self.handle_migration(sig, []))
                                # Unsubscribe from trade tracking for this mint
                                if mint in tracked_trade_mints:
                                    tracked_trade_mints.discard(mint)
                                    await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": [mint]}))

            except Exception as e:
                error_str = str(e).lower()
                if "close frame" not in error_str:
                    log_print(f"[PUMPPORTAL] ⚠ Connection error: {e} — retrying in {reconnect_delay}s", flush=True)
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, 60)

    async def drain_webhook_birth_queue(self):
        """
        Drains the webhook_birth_queue table (written by the Flask process when Helius
        POSTs a pumpfun-birth event).  Runs forever on a 5-second poll cadence.
        """
        import sqlite3 as _sq
        _ensure_webhook_birth_queue_schema(DB_PATH)
        log_print("[LISTENER] ✅ Webhook birth queue drainer started", flush=True)
        while True:
            try:
                conn = _sq.connect(DB_PATH, timeout=10)
                conn.row_factory = _sq.Row
                with conn:
                    rows = conn.execute(
                        "SELECT id, signature FROM webhook_birth_queue WHERE consumed = 0 ORDER BY id LIMIT 50"
                    ).fetchall()
                    if rows:
                        ids = [r["id"] for r in rows]
                        conn.execute(
                            f"UPDATE webhook_birth_queue SET consumed = 1 WHERE id IN ({','.join('?' * len(ids))})",
                            ids,
                        )
                conn.close()
                for row in rows:
                    sig = row["signature"]
                    if sig not in self.processing_launches and sig not in self.completed_launches:
                        premig_log(f"[WEBHOOK_BIRTH] sig={sig[:16]} source=helius_webhook")
                        asyncio.create_task(self.handle_birth(sig, []))
            except Exception as exc:
                log_print(f"[LISTENER] ⚠ webhook birth drain error: {exc}", flush=True)
            await asyncio.sleep(5)

    async def listen_pumpfun_websocket(self):
        """Dedicated Pump.fun websocket for births and pre-migration trade flow."""
        await self._wait_for_launch_toggle("PUMPFUN")
        log_print(f"\n[WEBSOCKET][PUMPFUN] Connecting to Pump.fun program...", flush=True)

        endpoints = self._websocket_endpoints()
        current_endpoint_idx = 0
        reconnect_delay = 5

        while True:
            try:
                endpoint, name = endpoints[current_endpoint_idx]
                async with websockets.connect(
                    endpoint,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    self.websocket_connected = True
                    reconnect_delay = 5
                    log_print(f"[WEBSOCKET][PUMPFUN] ✓ Connected via {name}", flush=True)

                    record_request(
                        section='listener',
                        provider='helius_rpc',
                        method='logsSubscribe',
                        status_code=200,
                        latency_ms=0,
                        source_file='pumpfun_curve_listener'
                    )

                    subscribe_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [PUMPFUN_PROGRAM]},
                            {"commitment": "confirmed"}
                        ]
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    log_print(f"[WEBSOCKET][PUMPFUN] Subscribed to Pump.fun births + trades", flush=True)
                    premig_log("[WS_SUBSCRIBED] program=pumpfun waiting for confirmation")

                    subscription_id = None
                    while subscription_id is None:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=10)
                            data = json.loads(msg)
                            if "result" in data and data.get("id") == 1:
                                subscription_id = data.get("result")
                                log_print(f"[WEBSOCKET][PUMPFUN] ✓ Subscription confirmed (id={subscription_id})", flush=True)
                                premig_log(f"[WS_SUBSCRIBED] program=pumpfun subscription_id={subscription_id}")
                                break
                        except asyncio.TimeoutError:
                            log_print(f"[WEBSOCKET][PUMPFUN] ⚠ No subscription confirmation after 10s", flush=True)
                            continue

                    if subscription_id is None:
                        continue

                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60)
                            data = json.loads(msg)

                            if 'params' not in data or 'result' not in data['params']:
                                continue

                            self.websocket_msg_count += 1
                            result = data['params']['result']
                            value = result.get('value', {})
                            logs = value.get('logs', [])
                            signature = value.get('signature', '')
                            err = value.get('err')
                            raw_sub_id = data['params'].get('subscription')

                            if raw_sub_id != subscription_id:
                                premig_log(f"[WS_MESSAGE_DROPPED] reason=unknown_subscription sub_id={raw_sub_id} expected={subscription_id} kind=pumpfun")
                                continue

                            if err or not signature:
                                premig_log(f"[WS_MESSAGE_DROPPED] reason={'err' if err else 'no_sig'} kind=pumpfun")
                                continue

                            premig_log(f"[RAW_EVENT] sig={signature[:16]} {json.dumps(logs)[:500]}")

                            if self._is_migration_transaction(logs):
                                listen_enabled = get_migration_setting('listen_to_launches', True)
                                log_print(f"[WEBSOCKET] 🔍 Migration found via pumpfun subscription. listen_to_launches={listen_enabled}", flush=True)

                                if not listen_enabled:
                                    log_print(f"[WEBSOCKET] ⏸ Migration detected via pumpfun subscription but launch listening disabled: {signature}", flush=True)
                                    continue

                                self.websocket_migration_count += 1
                                premig_log(f"[WS_MESSAGE_ROUTED] sig={signature[:16]} route=handle_migration source=pumpfun")
                                log_print(f"[WEBSOCKET] 🚨 Migration #{self.websocket_migration_count} detected via pumpfun subscription: {signature}", flush=True)
                                asyncio.create_task(self.handle_migration(signature, logs))
                                continue

                            if self._is_pumpfun_create_candidate(logs):
                                premig_log(f"[WS_MESSAGE_ROUTED] sig={signature[:16]} route=handle_birth")
                                asyncio.create_task(self.handle_birth(signature, logs))
                                continue

                            if self._is_pumpfun_buy_candidate(logs):
                                premig_log(f"[WS_MESSAGE_ROUTED] sig={signature[:16]} route=handle_pumpfun_trade")
                                asyncio.create_task(self.handle_pumpfun_trade(signature, logs))
                                continue

                            if self._is_pumpfun_sell_candidate(logs):
                                premig_log(f"[WS_MESSAGE_DROPPED] reason=sell sig={signature[:16]}")
                                continue

                            self._debug_pumpfun_trade_skip("unclassified_pumpfun_event", logs)

                        except asyncio.TimeoutError:
                            continue
                        except json.JSONDecodeError:
                            continue
                        except websockets.exceptions.ConnectionClosed as e:
                            log_print(f"[WEBSOCKET][PUMPFUN] 🔌 Connection closed ({e.code if hasattr(e, 'code') else e}), reconnecting...", flush=True)
                            break
                        except Exception as e:
                            error_msg = str(e).lower()
                            if "keepalive" not in error_msg and "close frame" not in error_msg:
                                log_print(f"[WEBSOCKET][PUMPFUN] ⚠ Error processing message: {e}", flush=True)
                            if "close frame" in error_msg or "connection closed" in error_msg or "going away" in error_msg:
                                break
                            continue

            except Exception as e:
                self.websocket_connected = False
                error_str = str(e).lower()
                if "401" in str(e) or "unauthorized" in error_str:
                    log_print(f"[WEBSOCKET][PUMPFUN] ⚠ Auth error (401) - falling back to public RPC", flush=True)
                    current_endpoint_idx = 1
                    reconnect_delay = 5
                elif "connection" in error_str or "refused" in error_str:
                    log_print(f"[WEBSOCKET][PUMPFUN] ⚠ Connection refused, retrying in {reconnect_delay}s...", flush=True)
                elif "close frame" not in error_str:
                    log_print(f"[WEBSOCKET][PUMPFUN] ⚠ {name} connection error: {e}", flush=True)
                    log_print(f"[WEBSOCKET][PUMPFUN] Retrying in {reconnect_delay}s...", flush=True)

                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 30)

    async def _handle_curve_complete_transition(self, bonding_curve_pda: str, slot: int) -> None:
        """
        Called when we observe complete=false→true on a bonding curve account.
        Persists the transition, resolves the creator, and enqueues the funding job.
        """
        mint = self._bonding_curve_to_mint.get(bonding_curve_pda)
        if not mint:
            return

        t0 = time.time()
        now = int(t0)
        log_print(
            f"[CURVE_COMPLETE] ✅ mint={mint[:8]}... bonding_curve={bonding_curve_pda[:8]}... slot={slot}",
            flush=True,
        )
        premig_log(f"[TIMING] mint={mint} curve_complete_event t=+0.000s slot={slot}")

        # Persist curve_complete state
        try:
            async with self.db_lock:
                conn = db_connect(DB_PATH, timeout=30)
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute(
                    """
                    UPDATE token_analysis
                    SET curve_complete = 1,
                        curve_completed_at = ?,
                        curve_completed_slot = ?,
                        curve_complete_source = 'account_state'
                    WHERE mint = ? AND (curve_complete IS NULL OR curve_complete = 0)
                    """,
                    (now, slot, mint),
                )
                conn.commit()
                conn.close()
        except Exception as e:
            log_print(f"[CURVE_COMPLETE] ⚠ DB write failed for {mint[:16]}...: {e}", flush=True)
            return

        # Resolve creator and enqueue funding job
        premig_log(f"[TIMING] mint={mint} creator_resolve_start t=+{time.time()-t0:.3f}s")
        creator = await self._ensure_pf_ws_creator(mint, reason="curve_complete")
        premig_log(f"[TIMING] mint={mint} creator_resolve_done t=+{time.time()-t0:.3f}s creator={'yes' if creator else 'no'}")
        if not creator:
            # Creator resolution failed — still enqueue if we have a creator already
            try:
                row = db_connect(DB_PATH, timeout=5).execute(
                    "SELECT pf_ws_creator, earliest_tx_creator, create_tx_signature FROM token_analysis WHERE mint = ? LIMIT 1",
                    (mint,),
                ).fetchone()
                if row:
                    creator = (str(row[0]).strip() if row[0] else "") or (str(row[1]).strip() if row[1] else "")
                    create_tx_sig = str(row[2]) if row[2] else None
                    if creator:
                        await self._enqueue_creator_funding_job(
                            creator,
                            mint=mint,
                            migration_timestamp=datetime.utcnow().isoformat() + "Z",
                            create_tx_signature=create_tx_sig,
                            delay_seconds=0,
                            source="curve_complete_fallback",
                            curve_completed_slot=slot,
                        )
                        premig_log(f"[TIMING] mint={mint} enqueued_fallback t=+{time.time()-t0:.3f}s")
            except Exception as e:
                log_print(f"[CURVE_COMPLETE] ⚠ Fallback enqueue failed for {mint[:16]}...: {e}", flush=True)
        else:
            premig_log(f"[TIMING] mint={mint} enqueue_start t=+{time.time()-t0:.3f}s")

    def _get_hot_bonding_curves(self) -> List[str]:
        """Return bonding curve PDAs for tokens that are close to migrating."""
        try:
            rows = db_connect(DB_PATH, timeout=5).execute(
                """
                SELECT bonding_curve_pda FROM token_analysis
                WHERE bonding_curve_pda IS NOT NULL
                  AND lifecycle_stage = 'bonding_curve'
                  AND curve_complete = 0
                  AND is_about_to_migrate = 1
                """,
            ).fetchall()
            return [r[0] for r in rows if r[0]]
        except Exception:
            return []

    async def watch_bonding_curve(self, pda: str) -> None:
        """Dynamically add a bonding curve PDA to the active watcher. Thread-safe."""
        if pda and pda not in self._curve_watch_subscribed:
            self._curve_watch_queue.put_nowait(pda)

    async def listen_bonding_curve_accounts(self):
        """
        accountSubscribe loop: watches hot bonding curve PDAs for complete=false→true.
        Only subscribes to tokens near migration (progress >= 50% or is_about_to_migrate).
        Unsubscribes immediately after complete fires to keep subscription count low.
        New curves are added dynamically via watch_bonding_curve().
        """
        await self._wait_for_launch_toggle("PUMPFUN")
        log_print("[CURVE_WATCH] Starting bonding curve account watcher...", flush=True)
        premig_log("[CURVE_WATCH] Starting bonding curve account watcher...")

        endpoints = self._websocket_endpoints()
        current_endpoint_idx = 0
        reconnect_delay = 5

        subscribed_curves: Dict[str, int] = {}   # pda -> subscription_id
        sub_id_to_curve: Dict[int, str] = {}      # subscription_id -> pda
        curve_complete_state: Dict[str, bool] = {}
        pending_confirmations: Dict[int, str] = {}
        pending_unsubs: Dict[int, int] = {}        # req_id -> sub_id being unsubscribed
        next_req_id = 100

        while True:
            try:
                endpoint, name = endpoints[current_endpoint_idx]
                async with websockets.connect(
                    endpoint,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    log_print(f"[CURVE_WATCH] ✓ Connected via {name}", flush=True)
                    premig_log(f"[CURVE_WATCH] Connected via {name}")
                    reconnect_delay = 5
                    subscribed_curves.clear()
                    sub_id_to_curve.clear()
                    pending_confirmations.clear()
                    pending_unsubs.clear()
                    self._curve_watch_subscribed.clear()
                    next_req_id = 100

                    def _write_state() -> None:
                        try:
                            mints = []
                            for pda in list(subscribed_curves.keys()) + list(pending_confirmations.values()):
                                mint = self._bonding_curve_to_mint.get(pda)
                                mints.append({"pda": pda, "mint": mint or ""})
                            with open(CURVE_WATCH_STATE_PATH, "w") as _f:
                                json.dump({"subscriptions": mints, "updated_at": int(time.time())}, _f)
                        except Exception:
                            pass

                    async def _subscribe(pda: str) -> None:
                        nonlocal next_req_id
                        if pda in subscribed_curves or pda in {v for v in pending_confirmations.values()}:
                            return
                        req_id = next_req_id
                        next_req_id += 1
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": req_id,
                            "method": "accountSubscribe",
                            "params": [pda, {"encoding": "base64", "commitment": "confirmed"}]
                        }))
                        pending_confirmations[req_id] = pda
                        self._curve_watch_subscribed.add(pda)
                        _write_state()

                    async def _unsubscribe(pda: str) -> None:
                        nonlocal next_req_id
                        sub_id = subscribed_curves.pop(pda, None)
                        if sub_id is None:
                            return
                        sub_id_to_curve.pop(sub_id, None)
                        self._curve_watch_subscribed.discard(pda)
                        req_id = next_req_id
                        next_req_id += 1
                        await ws.send(json.dumps({
                            "jsonrpc": "2.0", "id": req_id,
                            "method": "accountUnsubscribe",
                            "params": [sub_id]
                        }))
                        pending_unsubs[req_id] = sub_id
                        log_print(f"[CURVE_WATCH] 🔕 Unsubscribed pda={pda[:8]}... sub_id={sub_id}", flush=True)
                        _write_state()

                    # Subscribe to hot curves on startup
                    hot = self._get_hot_bonding_curves()
                    for pda in hot:
                        await _subscribe(pda)
                    log_print(f"[CURVE_WATCH] Subscribed to {len(hot)} hot bonding curves", flush=True)
                    premig_log(f"[CURVE_WATCH] Subscribed to {len(hot)} hot bonding curves on startup")

                    while True:
                        # Drain any dynamically queued PDAs first
                        while not self._curve_watch_queue.empty():
                            try:
                                pda = self._curve_watch_queue.get_nowait()
                                await _subscribe(pda)
                                log_print(f"[CURVE_WATCH] ➕ Added pda={pda[:8]}...", flush=True)
                                premig_log(f"[CURVE_WATCH] Dynamic subscribe pda={pda[:8]}... total={len(subscribed_curves)+len(pending_confirmations)}")
                            except asyncio.QueueEmpty:
                                break

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        except asyncio.TimeoutError:
                            continue

                        try:
                            data = json.loads(raw)
                        except Exception:
                            continue

                        # Subscription confirmation
                        if "result" in data and isinstance(data.get("result"), int):
                            req_id = data.get("id")
                            sub_id = data["result"]
                            if req_id in pending_confirmations:
                                pda = pending_confirmations.pop(req_id)
                                subscribed_curves[pda] = sub_id
                                sub_id_to_curve[sub_id] = pda
                                _write_state()
                            elif req_id in pending_unsubs:
                                pending_unsubs.pop(req_id, None)

                        # Account notification
                        elif data.get("method") == "accountNotification":
                            params = data.get("params", {})
                            result = params.get("result", {})
                            sub_id = params.get("subscription")
                            pda = sub_id_to_curve.get(sub_id)
                            if not pda:
                                continue

                            slot = result.get("context", {}).get("slot", 0)
                            account_data = result.get("value", {}).get("data")
                            if not account_data or not isinstance(account_data, list):
                                continue

                            try:
                                import base64
                                raw_bytes = base64.b64decode(account_data[0])
                                if len(raw_bytes) < 49:
                                    continue
                                complete = bool(raw_bytes[48])
                            except Exception:
                                continue

                            prev = curve_complete_state.get(pda)
                            curve_complete_state[pda] = complete

                            if complete and not prev:
                                # false→true (or first-seen-complete) transition
                                mint = self._bonding_curve_to_mint.get(pda)
                                should_fire = False
                                if prev is False:
                                    should_fire = True
                                elif prev is None and mint:
                                    try:
                                        existing = db_connect(DB_PATH, timeout=5).execute(
                                            "SELECT curve_complete FROM token_analysis WHERE mint = ? LIMIT 1",
                                            (mint,),
                                        ).fetchone()
                                        should_fire = bool(existing and not existing[0])
                                    except Exception:
                                        pass

                                if should_fire:
                                    premig_log(f"[CURVE_COMPLETE] pda={pda[:8]}... mint={mint or '?'} slot={slot} firing transition")
                                    asyncio.create_task(
                                        self._handle_curve_complete_transition(pda, slot)
                                    )
                                else:
                                    premig_log(f"[CURVE_COMPLETE] pda={pda[:8]}... mint={mint or '?'} slot={slot} skipped (already complete in DB or no mint)")
                                # Unsubscribe — we're done watching this curve
                                await _unsubscribe(pda)
                                curve_complete_state.pop(pda, None)

            except Exception as e:
                error_str = str(e).lower()
                if "close frame" not in error_str and "connection closed" not in error_str:
                    log_print(f"[CURVE_WATCH] ⚠ Connection error: {e}, retrying in {reconnect_delay}s", flush=True)
                    premig_log(f"[CURVE_WATCH] Connection error: {e}, retrying in {reconnect_delay}s")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 30)

    # --- Main listener ---


    async def listen(self):
        """Main entry point - start WebSocket listener with live price updater"""
        # ENABLED: Price updater is now ON
        PRICE_UPDATER_ENABLED = True

        if PRICE_UPDATER_ENABLED:
            # Start live price updater in background
            asyncio.create_task(self.update_live_prices_background())
            log_print("[LISTENER] ✅ Price updater started", flush=True)
        else:
            log_print("[LISTENER] ⏸ Price updater disabled (HARDCODED OFF)", flush=True)

        # Creator outgoing transfer extraction is now handled by Helius webhook (real-time monitoring)
        log_print("[LISTENER] ✅ Creator outgoing transfers monitored via Helius webhook (real-time)", flush=True)

        # Start creator activity worker
        try:
            from src.creators.repository import CreatorRepository
            from src.creators.worker import CreatorActivityWorker
            from src.creators.baseline import CreatorBaselineScanner
            from src.creators.helius_watch import register_creator_address
            from src.creators.service import restore_creator_watches, enqueue_stale_creator_reconciles

            _creator_lock = asyncio.Lock()
            _creator_repo = CreatorRepository(db_path=CREATOR_DB_PATH, db_lock=_creator_lock)
            await _creator_repo.ensure_schema()

            _scanner = CreatorBaselineScanner(
                background_rpc_fn=self.call_background_rpc,
                repo=_creator_repo,
            )
            _creator_worker = CreatorActivityWorker(
                _creator_repo,
                run_baseline_scan_fn=_scanner.run_baseline_scan,
                get_signatures_fn=_scanner.get_signatures,
                process_signature_fn=_scanner.process_signature,
            )
            asyncio.create_task(_creator_worker.run())
            log_print("[LISTENER] ✅ Creator activity worker started", flush=True)

            # Re-register all creator watches after restart (marks stale, then re-registers).
            asyncio.create_task(
                restore_creator_watches(repo=_creator_repo, register_webhook_fn=register_creator_address)
            )

            # Periodic stale-reconcile scheduler: every 10 minutes.
            async def _stale_reconcile_loop():
                while True:
                    await asyncio.sleep(600)
                    try:
                        n = await enqueue_stale_creator_reconciles(repo=_creator_repo)
                        if n:
                            log_print(f"[LISTENER] Enqueued {n} stale creator reconciles", flush=True)
                    except Exception as exc:
                        log_print(f"[LISTENER] ⚠ stale reconcile loop error: {exc}", flush=True)

            asyncio.create_task(_stale_reconcile_loop())

        except Exception as e:
            log_print(f"[LISTENER] ⚠ Creator activity worker failed to start: {e}", flush=True)

        # PumpPortal WSS handles births, migrations, and near-complete curve tracking.
        # listen_pumpswap_websocket detects PumpSwap pool creation (migrations) via Helius logsSubscribe.
        # listen_pumpportal_websocket handles pump.fun births via PumpPortal WSS (free, no Helius cost).
        # drain_webhook_birth_queue is a fallback for Helius birth webhook delivery.
        await asyncio.gather(
            self.listen_pumpswap_websocket(),
            self.listen_pumpportal_websocket(),
            self.drain_webhook_birth_queue(),
        )


_listener_singleton: Optional["PumpFunCurveListener"] = None

def _get_listener_instance() -> Optional["PumpFunCurveListener"]:
    return _listener_singleton


async def main():
    # Ensure only one instance runs at a time
    import fcntl
    import os
    from pathlib import Path
    
    lock_file = Path("/tmp/pumpfun_curve_listener.lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = open(lock_file, "w")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_handle.seek(0)
        lock_handle.truncate()
        lock_handle.write(str(os.getpid()))
        lock_handle.flush()
    except BlockingIOError:
        log_print("[STARTUP] Another pumpfun_curve_listener instance is already running; exiting", flush=True)
        return
    
    global _listener_singleton
    listener = PumpFunCurveListener()
    _listener_singleton = listener
    try:
        await listener.listen()
    finally:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
            lock_handle.close()
        except Exception:
            pass


def cleanup_and_restart():
    """Kill Flask (5002) and listener, then restart both"""
    import subprocess
    import os
    import time

    log_print("[CLEANUP] 🔄 Cleaning up and restarting services...", flush=True)

    try:
        # Kill Flask on port 5002
        os.system("lsof -i :5002 | tail -1 | awk '{print $2}' | xargs kill -9 2>/dev/null || true")
        time.sleep(1)
        log_print("[CLEANUP] ✓ Flask (port 5002) killed", flush=True)
    except:
        pass

    try:
        # Kill ALL listener instances (both module and direct script forms)
        os.system("pkill -9 -f 'pumpfun_curve_listener' 2>/dev/null || true")
        time.sleep(1)
        log_print("[CLEANUP] ✓ All listener instances killed", flush=True)
    except:
        pass

    try:
        # Restart listener using module form (matches actual process)
        log_print("[CLEANUP] 🚀 Starting listener...", flush=True)
        listener_process = subprocess.Popen(
            ["python", "-m", "src.core.pumpfun_curve_listener"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        time.sleep(4)
        log_print("[CLEANUP] ✓ Listener restarted", flush=True)
    except Exception as e:
        log_print(f"[CLEANUP] ⚠️ Could not restart listener: {e}", flush=True)

    try:
        # Restart Flask
        log_print("[CLEANUP] 🚀 Starting Flask...", flush=True)
        flask_process = subprocess.Popen(
            ["python", "run.py"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        time.sleep(3)
        log_print("[CLEANUP] ✓ Flask restarted", flush=True)
    except Exception as e:
        log_print(f"[CLEANUP] ⚠️ Could not restart Flask: {e}", flush=True)


def start_rpc_metrics_api():
    """Start RPC Metrics API in background subprocess"""
    import subprocess

    try:
        # Check if API is already running
        try:
            requests.get("http://localhost:8001/health", timeout=2)
            log_print("[INIT] ✓ RPC Metrics API already running on port 8001", flush=True)
            return
        except:
            pass

        # Start API server
        log_print("[INIT] 🚀 Starting RPC Metrics API on port 8001...", flush=True)
        api_process = subprocess.Popen(
            ["python", "-m", "src.apis.rpc_metrics_api"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        time.sleep(2)

        # Verify it started
        try:
            requests.get("http://localhost:8001/health", timeout=2)
            log_print("[INIT] ✓ RPC Metrics API started successfully", flush=True)
        except:
            log_print("[INIT] ⚠️ RPC Metrics API may not have started properly", flush=True)
    except Exception as e:
        log_print(f"[INIT] ⚠️ Could not start RPC Metrics API: {e}", flush=True)


if __name__ == "__main__":
    # Start RPC Metrics API before listener
    start_rpc_metrics_api()

    # Start listener
    asyncio.run(main())
