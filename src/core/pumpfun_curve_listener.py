#!/usr/bin/env python3
"""
Pump.Fun → PumpSwap Migration Listener

Detects token migrations from Pump.Fun bonding curve to PumpSwap AMM via WebSocket.
When a migration is detected, runs post-migration analyzer to assess risk.
"""

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
import threading
import websockets
import aiohttp
import requests
from datetime import datetime
from typing import Set, Optional, List, Dict
from src.analysis.pump_fun_post_migration_analyzer import PostMigrationAnalyzer
from src.extractors.realtime_creator_funding_extractor import extract_funding_for_new_token
from src.extractors.funder_incoming_extractor import extract_for_creator as extract_funder_transfers
from src.analysis.clustering_task_queue import enqueue_clustering
from dotenv import load_dotenv

# === ANSI Color Codes ===
class Colors:
    DETECT = "\033[94m"      # Blue for POOL_DETECT
    DISCOVER = "\033[92m"    # Green for POOL_DISCOVER_FALLBACK
    RESET = "\033[0m"

# === Logging Helper ===
def log_print(*args, **kwargs):
    """Print with flush support across Python versions"""
    kwargs.pop('flush', None)  # Remove flush if present
    print(*args, **kwargs)
    sys.stdout.flush()

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

# Serializes ALL database writes across threads/processes to prevent lock contention
# Used by asyncio tasks (wrapped with self.db_lock THEN this), executor threads, and workers
DB_WRITE_LOCK = threading.RLock()

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
            conn = sqlite3.connect(db_path, timeout=5)
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
            conn = sqlite3.connect('flex_complete_database.db', timeout=60)
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
        conn = sqlite3.connect(DB_PATH, timeout=10)
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

# Use DB_PATH from environment or construct it relative to project root
DB_PATH = os.getenv("DB_PATH")
if not DB_PATH:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    DB_PATH = os.path.join(PROJECT_ROOT, 'database', 'flex_complete_database.db')


class PumpFunCurveListener:
    """Detects Pump.Fun → PumpSwap migrations via WebSocket and analyzes them"""

    def __init__(self):
        self.seen_mints: Set[str] = set()
        self.processing_migrations: Set[str] = set()
        self.completed_migrations: Set[str] = set()
        self.analyzed_tokens = {}
        self.db_lock = asyncio.Lock()
        self.websocket_connected = False
        self.websocket_msg_count = 0  # Track message receipt
        self.websocket_migration_count = 0  # Track migrations detected

        # === NEW: Transaction caching ===
        self.tx_cache = {}  # {signature: (tx_data, timestamp)}
        self.tx_cache_ttl_seconds = 1800  # 30 minutes TTL
        self.tx_inflight_locks = {}  # {signature: asyncio.Lock()} for singleflight
        self.tx_cache_pending_retries = {}  # {signature: retry_task} for delayed re-checks
        self.tx_cache_stats = {
            'hit': 0,
            'miss': 0,
            'wait': 0,
        }

        # === NEW: Deferred pool detection retries ===
        self.pool_detection_retries = {}  # {mint: (tx_data, signature, retry_count)}
        self.pool_detection_max_retries = 3
        self.pool_detection_retry_delay = 5  # seconds

        # === NEW: Token state tracking (pending → resolved) ===
        self.token_states = {}  # {mint: "pending" | "resolving" | "resolved"}
        self.token_discovery_times = {}  # {mint: {"detected": time, "resolved": time}}

        self._ensure_db()
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
        self.critical_window_tasks = {}  # {mint: critical_window_expiry_time}

        # RPC isolation: separate quotas for discovery vs background
        self.discovery_rpc_semaphore = asyncio.Semaphore(8)  # 8 concurrent discovery calls
        self.background_rpc_semaphore = asyncio.Semaphore(2)  # 2 concurrent background calls

        # Background job queue (deferred execution during critical window)
        self.background_job_queue = asyncio.Queue()
        self.background_jobs_processing = False
        asyncio.create_task(self._process_background_queue())

        # Telemetry for discovery attempts
        self.discovery_attempts = {}  # {mint: [attempt_1, attempt_2, ...]}

        # === Initialize price worker with WebSocket for pool price streaming ===
        try:
            from src.core.price_worker import get_price_worker
            self.price_worker = get_price_worker()
            self.price_worker.start()  # Start background thread + WebSocket
            log_print(f"[INIT] ✅ Price worker started with WebSocket pool subscriptions", flush=True)
        except Exception as e:
            log_print(f"[INIT] ⚠️  Price worker initialization failed: {e}", flush=True)
            self.price_worker = None

        log_print(f"[INIT] ✅ Phase 2 critical-path protection initialized", flush=True)
        log_print(f"[INIT]   • Discovery RPC: 8 concurrent slots", flush=True)
        log_print(f"[INIT]   • Background RPC: 2 concurrent slots", flush=True)
        log_print(f"[INIT]   • Critical window: {self.DISCOVERY_CRITICAL_WINDOW_SECONDS}s", flush=True)

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

                # If any critical windows are still active, skip processing
                # This ensures absolute deferral - no background work during critical window
                if self.critical_window_tasks:
                    # Windows still active - don't process jobs yet
                    await asyncio.sleep(0.5)
                    continue

                # All critical windows expired - process all queued jobs
                jobs_processed = 0
                try:
                    while not self.background_job_queue.empty():
                        job_item = self.background_job_queue.get_nowait()
                        mint = job_item.get('mint', '?')
                        try:
                            log_print(f"[BACKGROUND] 🚀 Executing queued job (mint={mint[:8]}...)", flush=True)
                            await job_item['coro']
                            jobs_processed += 1
                        except Exception as e:
                            logger.error(f"[BACKGROUND] ❌ Job failed (mint={mint[:8]}...): {e}")
                        self.background_job_queue.task_done()
                except asyncio.QueueEmpty:
                    pass

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
                result = await self._post_rpc_with_fallback(payload, timeout=timeout, priority="critical")
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
                result = await self._post_rpc_with_fallback(payload, timeout=timeout, priority="background")
                return result
            except Exception as e:
                logger.debug(f"Background RPC error ({method}): {e}")
                return None

    async def _write_resolution_telemetry(self, mint: str, resolve_source: str, pool_address: str = None, retry_count: int = 0):
        """Write token resolution telemetry to database."""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            now = int(time.time())
            detected_at = int(self.token_discovery_times.get(mint, {}).get("detected", now))
            resolved_at = int(self.token_discovery_times.get(mint, {}).get("resolved", now))
            resolve_seconds = resolved_at - detected_at if detected_at else 0
            
            cursor.execute("""
                INSERT OR REPLACE INTO token_resolution_telemetry
                (mint, detected_at, resolved_at, resolve_seconds, resolve_source, retry_count, pool_address, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (mint, detected_at, resolved_at, resolve_seconds, resolve_source, retry_count, pool_address, now, now))
            conn.commit()
            conn.close()
        except Exception as e:
            log_print(f"[TELEMETRY] ⚠️  Failed to write telemetry for {mint}: {e}", flush=True)

    async def _post_rpc_with_fallback(self, payload: dict, timeout: int = 10, priority: str = "critical") -> Optional[dict]:
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

                tx_data = await asyncio.wait_for(
                    self._post_rpc_with_fallback(payload),
                    timeout=timeout,
                )

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

    # --- Database ---
    def _ensure_db(self):
        conn = sqlite3.connect(DB_PATH)
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

    async def _store_analysis(self, mint: str, analysis: dict, signature: str = None, pool_address: str = None):
        """Store post-migration analysis results"""
        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
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
                    INSERT OR REPLACE INTO token_analysis (
                        mint, created_at, analyzed_at, events_parsed,
                        post_migration_mint_concentration, post_migration_unique_minters_ratio,
                        post_migration_sell_suppression_ratio, post_migration_mint_velocity_sec,
                        post_migration_buy_size_variance, post_migration_sell_volume_concentration,
                        post_migration_creator_activity_ratio,
                        rug_probability, risk_level, post_migration_coverage,
                        migration_tx, price_current, price_highest, pool_address, earliest_tx_creator, creator_is_blocked, network_risk, connected_malicious_count,
                        cluster_id, cluster_name, cluster_risk_multiplier, network_funder_address, network_name
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    cluster_id,  # Cluster ID if creator is in a cluster
                    cluster_name,  # Cluster name (NexusCerberus, etc.)
                    cluster_risk_multiplier,  # Risk multiplier for cluster
                    network_funder_address,  # Funder address from creator_funders
                    network_name  # Network name from atomic_network_names
                ))

                conn.commit()
                conn.close()
                pool_info = f"Pool: {pool_address[:16]}" if pool_address else "Pool: will discover at price-time"
                log_print(f"[DB] ✅ Stored analysis {mint} | {pool_info}", flush=True)
            except Exception as e:
                log_print(f"[DB] ❌ Failed to store analysis for {mint}: {e}", flush=True)

    def _token_exists_in_db(self, mint: str) -> bool:
        """Check if token exists in analysis table (previously analyzed)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM token_analysis WHERE mint = ?", (mint,))
            result = cursor.fetchone()
            conn.close()
            return bool(result)
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
        logs_text = ' '.join(logs)

        # Exclude swaps (Buy/Sell instructions)
        if 'Instruction: Buy' in logs_text or 'Instruction: Sell' in logs_text:
            return False

        # Filter out MigrateBondingCurveCreator - that's NOT a pool creation
        if 'MigrateBondingCurveCreator' in logs_text:
            return False

        # Must have Migrate instruction (Pump.Fun migration marker)
        if 'Instruction: Migrate' not in logs_text:
            return False

        # Check for pool initialization patterns
        if not any(pattern.lower() in logs_text.lower() for pattern in ['initialize', 'create_pool', 'InitializePool']):
            return False

        return True

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
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
                }

                data = await self._post_rpc_with_fallback(payload)

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

    async def _extract_pool_from_migration_tx(self, signature: str) -> Optional[str]:
        """
        Extract the PumpSwap pool address from a migration transaction.

        The pool is the account that is OWNED BY the PumpSwap program.

        Strategy:
        1. Fetch the transaction
        2. Look through all accounts in innerInstructions
        3. Find accounts that are used by the PumpSwap program
        4. Return the first writable PDA (index 0 of PumpSwap instruction accounts)

        Returns: The pool address (string) or None if extraction fails
        Uses RPC failover chain: Primary QuickNode -> Secondary QuickNode -> Helius -> Public.
        """
        max_retries = 3
        retry_delays = [1.0, 3.0, 5.0]

        for attempt in range(max_retries):
            try:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTransaction",
                    "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
                }

                data = await self._post_rpc_with_fallback(payload)

                if not data or "result" not in data or not data["result"]:
                    return None

                tx_data = data["result"]
                message = tx_data.get("transaction", {}).get("message", {})
                account_keys = message.get("accountKeys", [])
                
                if not account_keys:
                    return None
                
                meta = tx_data.get("meta", {})
                inner_instructions = meta.get("innerInstructions", [])
                
                PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
                
                # Find PumpSwap program index in accountKeys
                pumpswap_idx = -1
                for i, acc in enumerate(account_keys):
                    if acc == PUMPSWAP_PROGRAM:
                        pumpswap_idx = i
                        break
                
                if pumpswap_idx < 0:
                    return None
                
                # Search innerInstructions for PumpSwap calls using programIdIndex
                for ix_group in inner_instructions:
                    instructions = ix_group.get("instructions", [])
                    for ix in instructions:
                        program_id_idx = ix.get("programIdIndex")
                        
                        # Check if this instruction is calling PumpSwap
                        if program_id_idx == pumpswap_idx:
                            # This is a PumpSwap instruction
                            accounts = ix.get("accounts", [])
                            if accounts and len(accounts) > 0:
                                # The first account in a PumpSwap instruction is typically the pool
                                pool_idx = accounts[0]
                                if isinstance(pool_idx, int) and pool_idx < len(account_keys):
                                    pool_address = account_keys[pool_idx]
                                    log_print(f"[POOL] ✅ Extracted pool from PumpSwap instruction: {pool_address}", flush=True)
                                    return pool_address


                return None

            except Exception as e:
                if attempt < max_retries - 1:
                    log_print(f"[POOL] ⚠ Error extracting pool (attempt {attempt + 1}/{max_retries}): {e}", flush=True)
                    await asyncio.sleep(retry_delays[attempt])
                else:
                    log_print(f"[POOL_ERROR] Failed to extract pool address after {max_retries} attempts: {e}", flush=True)
                    return None

        return None

    async def _extract_pool_from_tx(self, tx_data: Dict) -> Optional[str]:
        """
        Extract PumpSwap pool address from transaction data (no RPC call needed).

        The pool is the account that is OWNED BY the PumpSwap program.

        Strategy:
        1. Look through all accounts in innerInstructions
        2. Find accounts used by the PumpSwap program
        3. Return the first writable PDA (index 0 of PumpSwap instruction accounts)
        """
        if not tx_data:
            return None

        message = tx_data.get("transaction", {}).get("message", {})
        account_keys = message.get("accountKeys", [])

        if not account_keys:
            return None

        meta = tx_data.get("meta", {})
        inner_instructions = meta.get("innerInstructions", [])

        PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

        # Find PumpSwap program index in accountKeys
        pumpswap_idx = -1
        for i, acc in enumerate(account_keys):
            if acc == PUMPSWAP_PROGRAM:
                pumpswap_idx = i
                break

        if pumpswap_idx < 0:
            return None

        # Search innerInstructions for PumpSwap calls
        for ix_group in inner_instructions:
            instructions = ix_group.get("instructions", [])
            for ix in instructions:
                program_id_idx = ix.get("programIdIndex")
                if program_id_idx == pumpswap_idx:
                    accounts = ix.get("accounts", [])
                    if accounts and len(accounts) > 0:
                        pool_idx = accounts[0]
                        if isinstance(pool_idx, int) and pool_idx < len(account_keys):
                            pool_address = account_keys[pool_idx]
                            log_print(f"[POOL] ✅ Extracted pool from cached tx: {pool_address}", flush=True)
                            return pool_address

        return None

    async def _get_pool_address(self, token_mint: str, signature: str) -> Optional[str]:
        """Get pool address from database only.
        
        Pool discovery happens in _process_migration_with_mint via:
        1. Migration TX scan (stage 1)
        2. Scheduled retry with program-account discovery (stage 2)
        
        This method only reads from DB - does not attempt discovery.
        """
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
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
        2. Try to query pool account balances (token and SOL)
        3. If fails, use DexScreener (more reliable)
        
        Returns: (price_usd, market_cap_usd, source) or None
        """
        try:
            # Get or extract pool address
            pool_address = await self._get_pool_address(token_mint, signature)
            
            if pool_address:
                # Try to get price from pool balances
                result = await self._get_price_from_pool_account(pool_address, token_mint)
                if result is not None:
                    price, market_cap = result
                    return (price, market_cap, "onchain")
            
            # Fall back to DexScreener (more reliable and always available)
            result = await self._fetch_dexscreener_price(token_mint)
            if result is not None:
                price, market_cap = result
                return (price, market_cap, "dexscreener")
            
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

        Returns: (price_usd, market_cap_usd) or None
        """
        try:
            # Query WSOL (wrapped SOL) token accounts owned by this pool
            # WSOL mint: So11111111111111111111111111111111111111112
            wsol_mint = "So11111111111111111111111111111111111111112"

            payload_wsol = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [pool_address, {"mint": wsol_mint}, {"encoding": "jsonParsed"}]
            }

            data = await self._post_rpc_with_fallback(payload_wsol)

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
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [pool_address, {"encoding": "jsonParsed"}]
                }

                data = await self._post_rpc_with_fallback(payload)
                if not data or "result" not in data or not data["result"]:
                    return None

                result_data = data["result"]
                if not isinstance(result_data, dict):
                    return None

                account_value = result_data.get("value", {})
                if not account_value or not isinstance(account_value, dict):
                    return None

                lamports = account_value.get("lamports", 0)
                sol_balance = lamports / 1e9

            if sol_balance == 0:
                return None

            # Query token accounts owned by this pool
            payload2 = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [pool_address, {"mint": token_mint}, {"encoding": "jsonParsed"}]
            }

            data2 = await self._post_rpc_with_fallback(payload2)
            if not data2 or "result" not in data2:
                return None

            result_data2 = data2["result"]
            if not isinstance(result_data2, dict) or "value" not in result_data2:
                return None

            accounts = result_data2["value"]
            if not accounts or not isinstance(accounts, list):
                # No token accounts for this mint in this pool - wrong pool address
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

            except (KeyError, ValueError, TypeError):
                return None

            if token_balance == 0 or sol_balance == 0:
                return None

            # Calculate price
            price_sol = sol_balance / token_balance
            sol_usd = await self._get_sol_price_usd()
            price_usd = price_sol * sol_usd
            total_supply = 1_000_000_000  # Pump.Fun tokens have 1B supply
            market_cap_usd = price_usd * total_supply

            return (price_usd, market_cap_usd)

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
        """Get current SOL price in USD"""
        try:
            SOL_MINT = "So11111111111111111111111111111111111111112"
            url = f"https://api.dexscreener.com/latest/dex/tokens/{SOL_MINT}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        if pairs and "priceUsd" in pairs[0]:
                            try:
                                return float(pairs[0]["priceUsd"])
                            except (ValueError, TypeError):
                                pass
            return 200.0  # Fallback
        except:
            return 200.0

    async def _fetch_dexscreener_price(self, token_mint: str) -> Optional[tuple]:
        """
        Fetch price and market cap from DexScreener API.
        
        Returns: (price_usd, market_cap_usd) or None
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
                    
                    return (price_usd, market_cap_usd)
                    
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
                    conn = sqlite3.connect(DB_PATH, timeout=60)
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
                            price, market_cap, source = result  # Unpack the source
                            await self._update_price_in_db(token_mint, price, market_cap, source)  # Pass source
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
            conn = sqlite3.connect(DB_PATH, timeout=60)
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
            conn = sqlite3.connect(DB_PATH, timeout=60)
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
                conn = sqlite3.connect(DB_PATH, timeout=60)
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

    async def _update_price_in_db(self, token_mint: str, current_price: float, current_market_cap: float, source: str = "onchain"):
        """
        Update live price, market cap, and price source in database.
        
        Also automatically detects and flags rug pulls:
        - If time to peak < 30 minutes AND peak market cap < $100k → flag as 'quick_peak_low_mc'
        
        Note: Prices and market caps are stored in USD for consistency with DexScreener.
        """
        async with self.db_lock:
            try:
                conn = sqlite3.connect(DB_PATH, timeout=60)
                cursor = conn.cursor()
                
                # Get previous values and creation time
                cursor.execute(
                    "SELECT price_current, price_highest, market_cap_current, market_cap_highest, market_cap_highest_at, price_source, created_at, rug_indicator FROM token_analysis WHERE mint = ?",
                    (token_mint,)
                )
                row = cursor.fetchone()

                price_highest = row[1] if row and row[1] else current_price
                market_cap_highest = row[3] if row and row[3] else current_market_cap
                market_cap_highest_at = row[4] if row else None
                created_at = row[6] if row else None
                current_rug_indicator = row[7] if row else None

                # Track if this is a new peak
                is_new_peak = False
                
                # Update highest if this is higher
                if current_price > price_highest:
                    price_highest = current_price
                if current_market_cap > market_cap_highest:
                    market_cap_highest = current_market_cap
                    market_cap_highest_at = datetime.now().isoformat(sep=' ')  # Store timestamp when peak is reached
                    is_new_peak = True

                # Auto-detect rug pulls based on timing
                rug_indicator = current_rug_indicator
                if is_new_peak and created_at and market_cap_highest is not None:
                    try:
                        # Parse created_at timestamp
                        if isinstance(created_at, str):
                            created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        else:
                            created_dt = created_at
                        
                        # Parse peak timestamp
                        if isinstance(market_cap_highest_at, str):
                            peak_dt = datetime.fromisoformat(market_cap_highest_at.replace('Z', '+00:00'))
                        else:
                            peak_dt = market_cap_highest_at
                        
                        # Calculate time to peak in minutes
                        time_to_peak_minutes = (peak_dt - created_dt).total_seconds() / 60
                        
                        # RUG DETECTION LOGIC:
                        # Peak in < 30 minutes AND peak market cap < $100k = classic rug pattern
                        if time_to_peak_minutes < 30 and market_cap_highest < 100000:
                            rug_indicator = 'quick_peak_low_mc'
                            log_print(f"[RUG] 🚨 DETECTED: {token_mint} | Time to peak: {time_to_peak_minutes:.1f} min | Peak MC: ${market_cap_highest:,.0f}", flush=True)

                            # Get creator and add to block list
                            cursor.execute("SELECT earliest_tx_creator FROM token_analysis WHERE mint = ?", (token_mint,))
                            creator_row = cursor.fetchone()
                            if creator_row and creator_row[0]:
                                # Call async method to add to blocklist (fire and forget)
                                asyncio.create_task(self._add_rug_creator_to_blocklist(token_mint, creator_row[0]))
                        elif time_to_peak_minutes < 30:
                            # Peaked fast but market cap was substantial - not a rug, just volatile
                            rug_indicator = None
                            log_print(f"[PEAK] ⚡ Fast peak but legit size: {token_mint} | Time: {time_to_peak_minutes:.1f} min | MC: ${market_cap_highest:,.0f}", flush=True)
                        else:
                            # Normal progression
                            rug_indicator = None
                            
                    except Exception as e:
                        log_print(f"[RUG_CHECK] ⚠ Could not analyze rug pattern for {token_mint}: {e}", flush=True)

                cursor.execute("""
                    UPDATE token_analysis
                    SET price_current = ?, price_highest = ?,
                        market_cap_current = ?, market_cap_highest = ?,
                        market_cap_highest_at = ?,
                        rug_indicator = ?,
                        price_source = ?, price_updated_at = datetime('now')
                    WHERE mint = ?
                """, (current_price, price_highest, current_market_cap, market_cap_highest, market_cap_highest_at, rug_indicator, source, token_mint))
                
                conn.commit()
                conn.close()
                
            except Exception as e:
                log_print(f"[DB_ERROR] Failed to update price for {token_mint}: {e}", flush=True)

    async def _create_minimal_token_entry(self, mint: str):
        """Create a minimal token entry in database immediately when migration is detected"""
        max_retries = 6
        base_delay = 0.25

        async with self.db_lock:
            for attempt in range(max_retries):
                try:
                    # CRITICAL: Acquire lock ONLY for the actual write, not the sleep
                    with DB_WRITE_LOCK:
                        conn = sqlite3.connect(DB_PATH, timeout=30)
                        conn.execute("PRAGMA journal_mode=WAL")
                        conn.execute("PRAGMA synchronous=NORMAL")
                        conn.execute("PRAGMA busy_timeout=30000")
                        cursor = conn.cursor()

                        now = time.time()
                        cursor.execute("""
                            INSERT OR REPLACE INTO token_analysis (
                                mint, created_at, analyzed_at,
                                rug_probability, risk_level, post_migration_coverage,
                                rug_indicator, events_parsed
                            ) VALUES (?, ?, ?, NULL, NULL, NULL, NULL, NULL)
                        """, (mint, now, now))

                        conn.commit()
                        conn.close()

                    log_print(f"[DB] ✅ Created minimal token entry for {mint}", flush=True)
                    return

                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                        wait = base_delay * (2 ** attempt)
                        log_print(f"[DB_RETRY] ⏳ Database locked (attempt {attempt+1}/{max_retries}), retrying in {wait:.2f}s...", flush=True)
                        # CRITICAL: Sleep OUTSIDE the lock
                        await asyncio.sleep(wait)
                        continue
                    log_print(f"[DB_ERROR] Failed to create minimal token entry: {e}", flush=True)
                    return
                except Exception as e:
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

        async with self.db_lock:
            for attempt in range(max_retries):
                try:
                    # CRITICAL: Acquire lock ONLY for the actual write, not the sleep
                    with DB_WRITE_LOCK:
                        conn = sqlite3.connect(DB_PATH, timeout=30)
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
                    log_print(f"[DB] ✅ Updated token entry with creator: {creator[:8]}... | Created: {created_at} | CREATE tx: {create_tx_signature[:20] if create_tx_signature else 'N/A'}...{cluster_info_str}", flush=True)
                    return

                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                        wait = base_delay * (2 ** attempt)
                        log_print(f"[DB_RETRY] ⏳ Database locked (attempt {attempt+1}/{max_retries}), retrying in {wait:.2f}s...", flush=True)
                        # CRITICAL: Sleep OUTSIDE the lock
                        await asyncio.sleep(wait)
                    else:
                        log_print(f"[DB_ERROR] Failed to update token entry with creator: {e}", flush=True)
                        return

    async def _process_migration_with_mint(self, signature: str, logs: list, mint: str, tx_data: Optional[Dict] = None):
        """Continue migration pipeline once mint is known."""
        if self._token_exists_in_db(mint):
            log_print(f"[MIGRATION] ⏭️  Token {mint} already analyzed - SKIPPED", flush=True)
            return

        self.seen_mints.add(mint)
        log_print(f"[EVENT] 🚀 MIGRATION DETECTED: {mint}", flush=True)
        log_print(f"[EVENT] Migration signature: {signature}", flush=True)

        # === PHASE 2: Start critical window for RPC isolation ===
        # Discovery RPC calls use 8 concurrent slots, background jobs use only 2
        self.start_critical_window(mint)

        # Create minimal token entry immediately (so token appears in UI right away)
        await self._create_minimal_token_entry(mint)

        # Store migration TX signature (needed for retry discovery and analytics)
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE token_analysis SET migration_tx = ? WHERE mint = ?",
                (signature, mint)
            )
            conn.commit()
            conn.close()
            log_print(f"[DB] ✅ Stored migration TX: {signature[:20]}...", flush=True)
        except Exception as e:
            log_print(f"[DB] ⚠️  Failed to store migration TX: {e}", flush=True)

        # === NEW: Track token state (pending initially) ===
        import time
        self.token_states[mint] = "pending"
        self.token_discovery_times[mint] = {"detected": time.time(), "resolved": None}
        log_print(f"[STATE] Token {mint[:16]}... → pending", flush=True)

        # === Write initial telemetry entry ===
        try:
            now = int(time.time())
            conn = sqlite3.connect(DB_PATH, timeout=10)
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

        # === Extract pool via FALLBACK-FIRST approach for PumpSwap ===
        # Strategy: Try TX parsing (fast, works 85%+) BEFORE RPC retries (slow, often fails for PumpSwap)
        pool_address = None
        pool_discovery_source = "none"
        tx_source = "miss"  # Track where TX came from: cached, rpc, or miss

        # STAGE 1: TX-based detection (PRIMARY - faster + more reliable for PumpSwap)
        # If tx_data was passed (from handle_migration's cache fetch), it came from cached
        if tx_data:
            tx_source = "cached"  # Indicates this came from handle_migration's _get_transaction_cached
            try:
                from src.core.post_migration_pool_discovery import PostMigrationPoolDiscovery
                from src.core.pool_detector import AMMPrograms
                
                log_print(
                    f"{Colors.DISCOVER}[POOL_DETECT] 🔍 PRIMARY: TX parsing (tx_source={tx_source}) for {mint[:16]}...{Colors.RESET}",
                    flush=True
                )
                
                discovery = PostMigrationPoolDiscovery(RPC_HTTP)
                
                # Try all pool candidates from migration TX
                pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(
                    mint=mint,
                    migration_sig=signature,
                    tx_data=tx_data
                )
                
                if pool_candidates:
                    for candidate in pool_candidates:
                        try:
                            account_info_payload = {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getAccountInfo",
                                "params": [candidate, {"encoding": "base64"}]
                            }
                            acct = await self._post_rpc_with_fallback(account_info_payload, timeout=5)
                            
                            if acct and "result" in acct and acct["result"]:
                                owner = acct["result"].get("value", {}).get("owner")
                                if owner in AMMPrograms.ALL:
                                    # Try to register this pool
                                    try:
                                        from src.core.pool_discovery import PoolDiscovery
                                        discovery_pipeline = PoolDiscovery(DB_PATH, RPC_HTTP)
                                        registered = await discovery_pipeline.discover_and_register_pool(
                                            candidate, mint
                                        )
                                        if registered:
                                            pool_address = candidate
                                            pool_discovery_source = "tx_parsing"
                                            log_print(
                                                f"{Colors.DETECT}[POOL_DETECT] ✅ Pool discovered via TX parsing: {candidate[:16]}...{Colors.RESET}",
                                                flush=True
                                            )
                                            # Write telemetry
                                            self.token_discovery_times[mint]["resolved"] = time.time()
                                            await self._write_resolution_telemetry(mint, "tx_parsing", candidate, 0)
                                            break
                                    except Exception as e:
                                        log_print(
                                            f"{Colors.DISCOVER}[POOL_DETECT] ⏭️  Registration failed for {candidate}: {e}{Colors.RESET}",
                                            flush=True
                                        )
                        except Exception as e:
                            log_print(
                                f"{Colors.DISCOVER}[POOL_DETECT] ⏭️  Error checking candidate {candidate}: {e}{Colors.RESET}",
                                flush=True
                            )
                
            except Exception as e:
                log_print(f"{Colors.DISCOVER}[POOL_DETECT] ⏭️  TX parsing failed: {e}{Colors.RESET}", flush=True)

        # STAGE 2: RPC-based vault discovery (FALLBACK when TX parsing fails)
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

        # ✅ Emit single source-of-truth result
        log_print(
            f"{Colors.DETECT}[POOL_DETECT] Final discovery result: source={pool_discovery_source} pool={pool_address if pool_address else 'None'}{Colors.RESET}",
            flush=True
        )

        # === SCHEDULE RETRY DISCOVERY IF NO POOL FOUND ===
        if pool_discovery_source == "none":
            # Transition to "resolving" state (will be resolved on retry)
            self.token_states[mint] = "resolving"
            log_print(
                f"{Colors.DETECT}[STATE] Token {mint[:16]}... → resolving (scheduling retries){Colors.RESET}",
                flush=True
            )
            log_print(
                f"{Colors.DETECT}[POOL_DETECT] Initial discovery failed, scheduling optimized retries...{Colors.RESET}",
                flush=True
            )
            # Mark for retry scheduling after creator extraction (will schedule with full context)
            schedule_retry_after_creator_extraction = True
        else:
            schedule_retry_after_creator_extraction = False

        # === AUTO-REGISTER POOL FOR WEBSOCKET PRICING ===
        # ✅ Validate pool owner before registration (belt-and-suspenders check)
        if pool_address:
            try:
                from src.core.pool_detector import AMMPrograms
                # Check pool owner is actually an AMM program
                account_info_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [pool_address, {"encoding": "base64"}]
                }
                acct = await self._post_rpc_with_fallback(account_info_payload, timeout=5)
                pool_is_valid = False
                if acct and "result" in acct and acct["result"]:
                    owner = acct["result"].get("value", {}).get("owner")
                    if owner in AMMPrograms.ALL:
                        pool_is_valid = True
                    else:
                        log_print(
                            f"{Colors.DETECT}[POOL_DETECT] ⚠️  Rejecting pool {pool_address}: owner {owner[:16] if owner else '???'}... is not AMM program{Colors.RESET}",
                            flush=True
                        )
                        pool_address = None  # Clear invalid pool

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
                        else:
                            log_print(f"[POOL] ⚠️  Could not auto-register pool reserves", flush=True)
                    except Exception as pool_err:
                        log_print(f"[POOL] ⚠️  Pool auto-registration error: {pool_err}", flush=True)
            except Exception as pool_err:
                log_print(f"{Colors.DETECT}[POOL_DETECT] ⚠️  Pool validation error: {pool_err}{Colors.RESET}", flush=True)

        # Trigger immediate price fetch (don't wait for background task)
        # This ensures market cap appears quickly in UI regardless of analysis settings
        try:
            result = await self._extract_price_from_transaction(signature, mint)
            if result is not None:
                price, market_cap, source = result
                await self._update_price_in_db(mint, price, market_cap, source)
                log_print(f"[PRICE] ✅ Initial price fetched: ${price:.2e} | Market Cap: ${market_cap:.2e} | Source: {source}", flush=True)
        except Exception as price_err:
            log_print(f"[PRICE] ⚠ Initial price fetch failed: {price_err}", flush=True)

        # Extract earliest creator and creation date (always, regardless of analysis toggles)
        # This ensures creator and date are always visible in the UI
        earliest_creator = None
        bonding_curve_pda = None
        created_at = None
        analyzer = None
        try:
            from src.analysis.pump_fun_post_migration_analyzer import PostMigrationAnalyzer
            analyzer = PostMigrationAnalyzer(mint, rpc_url=RPC_HTTP)
            provenance = await analyzer.get_creator_from_earliest_tx()
            earliest_creator = provenance.get('creator') if provenance else None

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

            log_print(
                f"{Colors.DISCOVER}[RETRY_SCHEDULE] Scheduling retries with context: bonding_curve={bonding_curve_for_retry[:16] if bonding_curve_for_retry else 'None'}... creator={creator_for_retry[:16] if creator_for_retry else 'None'}...{Colors.RESET}",
                flush=True
            )

            # Schedule retries at optimized delays (don't await - fire and forget)
            # Optimized schedule: denser early retries + extended late retries (0.5s intervals for first 8, then 3-5s intervals)
            # Pass tx_data, tx_source, and discovery context to maximize pool discovery success
            asyncio.create_task(self._retry_pool_discovery(
                mint,
                signature,
                delays=[0.5, 1, 1.5, 2, 3, 5, 8, 12, 18, 25, 35, 50],
                tx_source=tx_source,
                tx_data=tx_data,
                bonding_curve=bonding_curve_for_retry,
                creator=creator_for_retry,
                migration_timestamp=migration_timestamp_for_retry
            ))

        # Analyze token history (includes creator behavior from all token transactions) - MUST be deferred during critical window
        # This is a background task that consumes RPC quota and must wait until critical window expires
        if get_migration_setting('token_history_check', True):
            log_print(f"[SETTINGS] Token history ✅ ON - queueing post-migration analysis (deferred)", flush=True)
            # Queue for deferred execution after critical window expires (45s)
            await self.queue_background_job(self.analyze_post_migration(mint, signature, pool_address), mint=mint, priority=5)
        else:
            log_print(f"[SETTINGS] Token history ❌ OFF - skipping post-migration analysis", flush=True)

        log_print(f"[MIGRATION] ✅ CRITICAL PATH COMPLETE - Token {mint[:8]}... with creator {earliest_creator[:8] if earliest_creator else 'unknown'}... is now visible in UI", flush=True)

        # === PHASE 2: Queue background tasks for deferred execution ===
        # These jobs wait until critical window expires before running
        # This protects pool discovery RPC quota during the critical 45-second window
        if earliest_creator:
            create_tx_sig = analyzer._create_tx_signature if analyzer and hasattr(analyzer, '_create_tx_signature') else None

            async def background_funding_and_clustering():
                """Background: funding extraction, funder extraction, and clustering (deferred)"""
                log_print(f"[BACKGROUND] 🚀 Starting background funding and clustering tasks...", flush=True)

                # Extract creator funding
                try:
                    log_print(f"[FUNDING] ⏳ Starting creator funding extraction for {earliest_creator[:8]}...", flush=True)
                    await extract_funding_for_new_token(earliest_creator, created_at, create_tx_sig, mint)
                    log_print(f"[FUNDING] ✅ Creator funding extraction complete", flush=True)
                except Exception as e:
                    log_print(f"[FUNDING] ⚠️ Error in creator funding extraction: {e}", flush=True)

                # Extract funder transfers (respects auto_extract_funders toggle)
                try:
                    if get_migration_setting('auto_extract_funders', False):
                        log_print(f"[FUNDER_EXTRACTION] ⏳ Starting funder transfer extraction for {earliest_creator[:8]}...", flush=True)
                        await extract_funder_transfers_async(earliest_creator)
                        log_print(f"[FUNDER_EXTRACTION] ✅ Funder transfer extraction complete", flush=True)
                    else:
                        log_print(f"[FUNDER_EXTRACTION] ⏭️ Skipped (auto_extract_funders toggle is OFF)", flush=True)
                except Exception as e:
                    log_print(f"[FUNDER_EXTRACTION] ⚠️ Error in funder extraction: {e}", flush=True)

                # Queue clustering (now that funding is extracted)
                try:
                    log_print(f"[CLUSTERING] ⏳ Queueing network clustering task...", flush=True)
                    await enqueue_clustering(rebuild_super_clusters_from_funding, "super_clusters_rebuild")
                    log_print(f"[CLUSTERING] ✅ Clustering task enqueued for processing", flush=True)
                except Exception as e:
                    log_print(f"[CLUSTERING] ⚠️ Error queueing clustering: {e}", flush=True)

            # Queue background tasks for deferred execution (after critical window)
            # This allows pool discovery to complete before background RPC work starts
            critical_expiry = time.time() + self.DISCOVERY_CRITICAL_WINDOW_SECONDS
            log_print(f"[BACKGROUND] 📤 Queueing: funding + funder_extraction + clustering (will execute at T+45s, not before)", flush=True)
            log_print(f"[BACKGROUND] 🔒 DEFERRAL ABSOLUTE: no RPC work until critical_window expires at +{self.DISCOVERY_CRITICAL_WINDOW_SECONDS}s", flush=True)
            await self.queue_background_job(background_funding_and_clustering(), mint=mint, priority=10)
        else:
            log_print(f"[BACKGROUND] ⏭️ Skipping background tasks (no creator found)", flush=True)

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

                        # If cached parsing yielded candidates, use them
                        # Otherwise try follow-on discovery (Phase 3)
                        # Then fall back to RPC fetch (slower path)
                        if candidates_from_cached:
                            pool_candidates = candidates_from_cached
                            using_cached_payload = True
                        else:
                            # Phase 3: Try follow-on transaction discovery if:
                            # - cached TX present but zero candidates
                            # - we're past Tier 1 (attempts 1-3)
                            pool_candidates = []  # Initialize here to avoid uninitialized error
                            follow_on_max_txs = 0
                            if attempt >= 4:  # Tier 2+ (attempts 4+)
                                follow_on_max_txs = 10 if attempt < 7 else 20

                            follow_on_pool = None
                            follow_on_anchor = None
                            follow_on_txs_scanned = 0

                            log_print(
                                f"[FOLLOW_ON_CHECK] mint={mint[:16]}... "
                                f"follow_on_max_txs={follow_on_max_txs} "
                                f"tx_data={tx_data is not None} "
                                f"cached_count={cached_candidate_count}",
                                flush=True
                            )

                            if follow_on_max_txs > 0 and tx_data is not None and cached_candidate_count == 0:
                                # Use bonding_curve and creator passed from migration context
                                # These were extracted in _process_migration_with_mint
                                bonding_curve_for_follow_on = bonding_curve  # Use the parameter passed to _retry_pool_discovery
                                creator_for_follow_on = creator  # Use the parameter passed to _retry_pool_discovery

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
                                            f"{Colors.DISCOVER}[FOLLOW_ON_SUCCESS] Found pool {follow_on_pool[:16]}... via anchor={follow_on_anchor} at offset={follow_on_offset}{Colors.RESET}",
                                            flush=True
                                        )
                                        pool_candidates = [follow_on_pool]
                                        using_cached_payload = True  # Count as cached discovery
                                    else:
                                        log_print(
                                            f"{Colors.DISCOVER}[FOLLOW_ON_EXHAUSTED] Scanned {follow_on_txs_scanned} TXs, no valid pool found{Colors.RESET}",
                                            flush=True
                                        )
                                        # Fall through to RPC fallback
                                        pool_candidates = []
                                        using_cached_payload = tx_data is not None

                                except Exception as e:
                                    logger.error(f"[FOLLOW_ON_DISCOVERY] Error: {e}")
                                    pool_candidates = []
                                    using_cached_payload = tx_data is not None

                            # If no follow-on or follow-on failed, try RPC fetch
                            if not pool_candidates:
                                using_cached_payload = tx_data is not None
                                pool_candidates = await discovery.discover_pool_candidates_from_migration_tx(
                                    mint=mint,
                                    migration_sig=original_migration_sig,
                                    tx_data=tx_data  # Pass cached TX to avoid redundant fetch
                                )

                        candidates_tested = 0
                        rejection_reasons = []

                        if pool_candidates:
                            for candidate in pool_candidates:
                                candidates_tested += 1
                                discovery_metrics['total_candidates_tested'] += 1

                                try:
                                    # Check owner with discovery RPC quota
                                    account_info_payload = {
                                        "jsonrpc": "2.0",
                                        "id": 1,
                                        "method": "getAccountInfo",
                                        "params": [candidate, {"encoding": "base64"}]
                                    }
                                    acct = await self.call_discovery_rpc(
                                        "getAccountInfo",
                                        [candidate, {"encoding": "base64"}],
                                        timeout=5
                                    )

                                    if not acct or not acct.get("result"):
                                        rejection_reasons.append("tx_not_indexed")
                                        discovery_metrics['rejections']['tx_not_indexed'] = discovery_metrics['rejections'].get('tx_not_indexed', 0) + 1
                                        continue

                                    owner = acct["result"].get("value", {}).get("owner")
                                    if owner not in AMMPrograms.ALL:
                                        rejection_reasons.append("owner_mismatch")
                                        discovery_metrics['rejections']['owner_mismatch'] = discovery_metrics['rejections'].get('owner_mismatch', 0) + 1
                                        continue

                                    # Owner valid - try registration
                                    try:
                                        from src.core.pool_discovery import PoolDiscovery
                                        discovery_pipeline = PoolDiscovery(DB_PATH, RPC_HTTP)
                                        registered = await discovery_pipeline.discover_and_register_pool(
                                            candidate, mint
                                        )
                                        if registered:
                                            # SUCCESS!
                                            self.token_states[mint] = "resolved"
                                            self.token_discovery_times[mint]["resolved"] = time.time()
                                            elapsed = self.token_discovery_times[mint]["resolved"] - self.token_discovery_times[mint]["detected"]

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
                                            return

                                        else:
                                            rejection_reasons.append("registration_failed")
                                            discovery_metrics['rejections']['registration_failed'] = discovery_metrics['rejections'].get('registration_failed', 0) + 1

                                    except Exception as reg_err:
                                        rejection_reasons.append("registration_error")
                                        discovery_metrics['rejections']['registration_error'] = discovery_metrics['rejections'].get('registration_error', 0) + 1

                                except Exception as e:
                                    rejection_reasons.append("check_error")
                                    discovery_metrics['rejections']['check_error'] = discovery_metrics['rejections'].get('check_error', 0) + 1

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
                            self.token_states[mint] = "resolved"
                            self.token_discovery_times[mint]["resolved"] = time.time()
                            elapsed = self.token_discovery_times[mint]["resolved"] - self.token_discovery_times[mint]["detected"]

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
                        payload = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getTransaction",
                            "params": [
                                signature,
                                {
                                    "encoding": "jsonParsed",
                                    "commitment": "finalized",
                                    "maxSupportedTransactionVersion": 0,
                                },
                            ],
                        }

                        raw = await self._post_rpc_with_fallback(payload, timeout=20)
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

    async def listen_websocket(self):
        """Listen to PumpSwap program via WebSocket for live migration events"""
        # Check if token launch listening is enabled - keep checking periodically so toggle works at runtime
        while not get_migration_setting('listen_to_launches', True):
            log_print(f"[WEBSOCKET] ⏸ Token Launch listening is DISABLED - websocket idle (checking every 30s)", flush=True)
            await asyncio.sleep(30)
            continue

        log_print(f"\n[WEBSOCKET] Connecting to PumpSwap program...", flush=True)

        # Try Helius first, fall back to public Solana
        endpoints = [
            (HELIUS_RPC_WS, "Helius"),
            ("wss://api.mainnet-beta.solana.com/", "Public Solana")
        ]

        current_endpoint_idx = 0
        reconnect_delay = 5

        while True:
            try:
                endpoint, name = endpoints[current_endpoint_idx]
                # Improved WebSocket settings for stability
                async with websockets.connect(
                    endpoint,
                    ping_interval=20,      # Send ping every 20s
                    ping_timeout=5,        # Wait 5s for pong
                    close_timeout=10,      # Wait 10s for close frame
                    max_size=10 * 1024 * 1024  # 10MB max message size
                ) as ws:
                    self.websocket_connected = True
                    reconnect_delay = 5  # Reset delay on successful connection
                    log_print(f"[WEBSOCKET] ✓ Connected to PumpSwap program via {name}", flush=True)

                    # Record websocket connection (Helius charges for LaserStream connections)
                    record_request(
                        section='listener',
                        provider='helius_rpc',
                        method='logsSubscribe',  # WebSocket subscription method
                        status_code=200,
                        latency_ms=0,
                        source_file='pumpfun_curve_listener'
                    )

                    # Subscribe to PumpSwap program logs
                    subscribe_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [PUMPSWAP_PROGRAM]},
                            {"commitment": "confirmed"}
                        ]
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    log_print(f"[WEBSOCKET] Subscribed to PumpSwap migrations", flush=True)

                    # Wait for subscription confirmation before processing events
                    subscription_id = None
                    while subscription_id is None:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=10)
                            data = json.loads(msg)
                            
                            # Check for subscription response
                            if "result" in data:
                                subscription_id = data.get("result")
                                log_print(f"[WEBSOCKET] ✓ Subscription confirmed (ID: {subscription_id})\n", flush=True)
                                break
                        except asyncio.TimeoutError:
                            log_print(f"[WEBSOCKET] ⚠ No subscription confirmation after 10s", flush=True)
                            break
                    
                    # Now listen for actual migration events
                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=60)
                            data = json.loads(msg)

                            # Process only subscription result (actual events, not responses)
                            if 'params' in data and 'result' in data['params']:
                                self.websocket_msg_count += 1
                                result = data['params']['result']
                                value = result.get('value', {})
                                logs = value.get('logs', [])
                                signature = value.get('signature', '')
                                err = value.get('err')

                                # Skip failed transactions
                                if err or not signature:
                                    continue

                                # Check if this is a migration
                                if self._is_migration_transaction(logs):
                                    # Check if listening to launches is enabled
                                    listen_enabled = get_migration_setting('listen_to_launches', True)
                                    log_print(f"[WEBSOCKET] 🔍 Migration found. listen_to_launches={listen_enabled}", flush=True)

                                    if not listen_enabled:
                                        log_print(f"[WEBSOCKET] ⏸ Migration detected but launch listening disabled: {signature}", flush=True)
                                        continue

                                    self.websocket_migration_count += 1
                                    log_print(f"[WEBSOCKET] 🚨 Migration #{self.websocket_migration_count} detected: {signature}", flush=True)
                                    asyncio.create_task(self.handle_migration(signature, logs))

                        except asyncio.TimeoutError:
                            # Keepalive timeout - continue listening
                            continue
                        except json.JSONDecodeError:
                            # Invalid JSON, skip
                            continue
                        except Exception as e:
                            # Suppress keepalive ping timeout spam and close frame warnings
                            error_msg = str(e).lower()
                            if "keepalive" not in error_msg and "close frame" not in error_msg:
                                log_print(f"[WEBSOCKET] ⚠ Error processing message: {e}", flush=True)
                            # Reconnect on serious errors
                            if "close frame" in error_msg or "connection closed" in error_msg:
                                break
                            continue

            except Exception as e:
                self.websocket_connected = False
                error_str = str(e).lower()

                # Check for specific auth issues
                if "401" in str(e) or "unauthorized" in error_str:
                    log_print(f"[WEBSOCKET] ⚠ Auth error (401) - falling back to public RPC", flush=True)
                    current_endpoint_idx = 1  # Switch to public Solana
                    reconnect_delay = 5
                elif "connection" in error_str or "refused" in error_str:
                    log_print(f"[WEBSOCKET] ⚠ Connection refused, retrying in {reconnect_delay}s...", flush=True)
                elif "close frame" not in error_str:
                    # Don't log close frame messages as errors
                    log_print(f"[WEBSOCKET] ⚠ {name} connection error: {e}", flush=True)
                    log_print(f"[WEBSOCKET] Retrying in {reconnect_delay}s...", flush=True)

                await asyncio.sleep(reconnect_delay)
                # Exponential backoff with cap at 30s
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

        # Start WebSocket listener
        await self.listen_websocket()


async def main():
    # Ensure only one instance runs at a time
    import os
    import time
    from pathlib import Path
    
    lock_file = Path("/tmp/pumpfun_curve_listener.lock")
    
    # Kill any existing listener instances that might be hanging
    max_retries = 3
    for attempt in range(max_retries):
        result = os.system("pkill -9 -f 'python.*-m src.core.pumpfun_curve_listener' 2>/dev/null || true")
        time.sleep(0.5)
        # Check if there are still instances running
        check = os.popen("pgrep -f 'python.*-m src.core.pumpfun_curve_listener' | wc -l").read().strip()
        if check == "1":  # Only this instance
            break
        if attempt < max_retries - 1:
            time.sleep(0.5)
    
    listener = PumpFunCurveListener()
    await listener.listen()


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
