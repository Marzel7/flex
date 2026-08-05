#!/usr/bin/env python3
"""
Real-time creator funding extractor.
Hooks into token migration events to extract creator funding immediately.

When a new token is detected as migrated:
  1. Get creator address from transaction
  2. Query all signatures BEFORE migration timestamp
  3. Extract SOL transfers TO creator (two types):
     - OUTGOING: Creator signed tx that moved SOL in (creator is fee payer)
     - INCOMING: Transfers where creator is recipient account (not signer)
  4. Save funder relationships to database
  5. Flag suspicious funding patterns

KEY DISTINCTION:
- FUNDING ACCOUNT: Fee payer who signed a transaction sending SOL
- RECIPIENT ACCOUNT: Account receiving SOL without necessarily signing
  (detected via balance change analysis or transaction parsing)
"""

import sqlite3
import asyncio
import aiohttp
import os
import time
from src.utils.db_locking import db_connect, managed_db_connect
from typing import Optional, Dict, List, Set, Iterable, Tuple
from datetime import datetime
from src.utils.db_locking import DB_WRITE_LOCK
from src.utils.infra_mapping import INFRASTRUCTURE_ACCOUNTS, CEX_ACCOUNTS
from src.utils.dust_addresses import DUST_ADDRESSES
from src.utils.domain_extraction import extract_from_helius_transaction_async
from src.utils.domain_mapping import register_domain, link_domain_to_address
from src.analysis.automatic_cex_detection import classify_addresses_from_funding
from src.analysis.post_launch_automation import run_post_launch_automation

# Import RPC metrics recorder for monitoring
try:
    from src.metrics.rpc_metrics_recorder import record_request, initialize_recorder
    initialize_recorder(plan_monthly_credits=50_000_000)
except ImportError:
    def record_request(*args, **kwargs):
        pass  # No-op if metrics recorder not available

_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "database",
    "flex_complete_database.db",
)
DB_PATH = os.environ.get(
    "DB_PATH",
    os.getenv("RPC_METRICS_DB", os.path.abspath(_DEFAULT_DB_PATH)),
)
# Initialize creator funding cache for Layer 6 optimization
try:
    from src.utils.creator_funding_graph_cache import CreatorFundingGraphCache
    CREATOR_CACHE = CreatorFundingGraphCache(DB_PATH)
except ImportError:
    CREATOR_CACHE = None
# FIX #6: Remove hardcoded API key fallback — fail safe instead
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "").strip()
HELIUS_MONITORING_API_KEY = os.getenv("HELIUS_MONITORING_API_KEY", "").strip()

# Use monitoring key if available, fall back to regular key
_RPC_KEY = HELIUS_MONITORING_API_KEY or HELIUS_API_KEY
USE_HELIUS = bool(_RPC_KEY)

# SNS Domain Resolver Configuration
SNS_API_BASE = "https://sns-api.bonfida.com"
SNS_PRIMARY_ENDPOINT = "/v2/user/fav-domains/"
DOMAIN_CACHE_TTL_SECS = 7 * 24 * 60 * 60  # 7 days local TTL

# Same RPC configuration as post_migration_analyzer for consistency
# RPC Configuration: Use Helius + Public Solana only (QuickNode removed)
RPC_URLS = []
if USE_HELIUS:
    RPC_URLS.append(f"https://mainnet.helius-rpc.com/?api-key={_RPC_KEY}")
RPC_URLS.append("https://api.mainnet-beta.solana.com")  # Public fallback

MAX_CONCURRENT_RPC = 8  # FIX #8: Bound RPC concurrency (was unused BATCH_SIZE = 10)
# FIX #2: Pagination limit (was hardcoded inline as 100)
MAX_PAGES = 8
MAX_RETRIES = 5
RPC_TIMEOUT = 30
FAST_FIRST_TX_PAGE_CAP = 3

# Pump.Fun program ID - used to filter out Pump.Fun token operations
PUMPFUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


class DomainResolver:
    """Resolve Solana domain names (SNS) for addresses with caching"""

    def __init__(self, db_path: str, session: aiohttp.ClientSession):
        self.db_path = db_path
        self.session = session
        self.mem: Dict[str, Tuple[Optional[str], int]] = {}  # address -> (domain_or_none, updated_at)
        self._ensure_table()

    def _ensure_table(self):
        """Create address_domains table if it doesn't exist"""
        conn = db_connect(self.db_path, timeout=60)
        cur = conn.cursor()
        
        # Domains cache table (for resolution state tracking)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS address_domains (
                address TEXT PRIMARY KEY,
                primary_domain TEXT,
                updated_at INTEGER
            )
        """)
        
        # Address tags table (persistent tags like INFRA and CEX)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS address_tags (
                address TEXT,
                tag_type TEXT,
                tag_value TEXT,
                source TEXT,
                first_seen_at INTEGER,
                PRIMARY KEY (address, tag_type, tag_value)
            )
        """)
        
        cur.execute("CREATE INDEX IF NOT EXISTS idx_address_tags_address ON address_tags(address)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_address_tags_type ON address_tags(tag_type)")
        
        conn.commit()
        conn.close()

    def _db_get(self, address: str) -> Optional[Tuple[Optional[str], int]]:
        """Get domain from database cache"""
        conn = db_connect(self.db_path, timeout=60)
        cur = conn.cursor()
        cur.execute("SELECT primary_domain, updated_at FROM address_domains WHERE address = ? LIMIT 1", (address,))
        row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return (row[0], row[1])

    def _db_set_many(self, rows: List[Tuple[str, Optional[str], int]]):
        """Save multiple domain lookups to database cache"""
        conn = db_connect(self.db_path, timeout=60)
        cur = conn.cursor()
        cur.executemany("""
            INSERT OR REPLACE INTO address_domains (address, primary_domain, updated_at)
            VALUES (?, ?, ?)
        """, rows)
        conn.commit()
        conn.close()

    def _is_fresh(self, updated_at: int) -> bool:
        """Check if cached entry is still fresh"""
        return (int(time.time()) - updated_at) < DOMAIN_CACHE_TTL_SECS

    def _save_address_tag(self, address: str, domain: str):
        """Save a discovered domain as a persistent address tag and register it"""
        if not domain:
            return

        try:
            conn = db_connect(self.db_path, timeout=60)
            cur = conn.cursor()

            # Save domain tag (tag_type='domain', tag_value=actual domain name)
            cur.execute("""
                INSERT OR REPLACE INTO address_tags
                (address, tag_type, tag_value, source, first_seen_at)
                VALUES (?, 'domain', ?, 'sns_resolver', ?)
            """, (address, domain, int(time.time())))

            conn.commit()
            conn.close()

            # Register domain in persistent mapping
            register_domain(domain, domain_type='owned',
                          metadata={'owner': address, 'source': 'sns_resolution'},
                          source='sns_resolver')

            # Link address to domain in mapping
            link_domain_to_address(domain, address)

        except Exception as e:
            pass  # Non-critical

    async def resolve_primary_domains(self, addresses: Iterable[str]) -> Dict[str, Optional[str]]:
        """
        Resolve primary SNS domains for addresses using Bonfida's improved endpoint.
        Returns {address: 'name.sol' or None}.
        Uses SNS primary domains endpoint with batching and caching.
        Saves discovered domains as persistent address tags.
        
        Endpoint: GET /v2/user/fav-domains/{pubkeys}
        - Returns primary/favorite domains (what most explorers display)
        - Includes subdomains
        - More reliable than old endpoint
        - Supports up to 20 addresses per request
        """
        now = int(time.time())
        addrs = [a for a in set(addresses) if isinstance(a, str) and len(a) > 20]

        if not addrs:
            return {}

        out: Dict[str, Optional[str]] = {}
        missing: List[str] = []

        # 1) Check memory cache
        for a in addrs:
            if a in self.mem and self._is_fresh(self.mem[a][1]):
                out[a] = self.mem[a][0]
            else:
                missing.append(a)

        # 2) Check database cache
        still_missing: List[str] = []
        for a in missing:
            row = self._db_get(a)
            if row and self._is_fresh(row[1]):
                domain, ts = row
                self.mem[a] = (domain, ts)
                out[a] = domain
                # Save cached domain as persistent tag if found
                if domain:
                    self._save_address_tag(a, domain)
            else:
                still_missing.append(a)

        # 3) Query SNS API in batches of 20 using new v2/user/fav-domains endpoint
        to_persist: List[Tuple[str, Optional[str], int]] = []
        for i in range(0, len(still_missing), 20):
            batch = still_missing[i:i+20]
            pubkeys = ",".join(batch)
            url = f"https://sns-api.bonfida.com/v2/user/fav-domains/{pubkeys}"

            try:
                start_time = time.time()
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    latency_ms = (time.time() - start_time) * 1000
                    record_request(
                        section="creator_funding",
                        provider="bonfida_sns",
                        method="sns_primary_domains",
                        status_code=resp.status,
                        latency_ms=latency_ms,
                        mode="realtime",
                        source_file="realtime_creator_funding_extractor",
                    )
                    if resp.status != 200:
                        # Mark as unknown but cache locally
                        for a in batch:
                            self.mem[a] = (None, now)
                            out[a] = None
                            to_persist.append((a, None, now))
                        continue

                    data = await resp.json()
                    # Response format: {pubkey: "domain"} (note: no .sol suffix in response)
                    # We need to add .sol back
                    for a in batch:
                        domain_name = data.get(a)
                        if isinstance(domain_name, str) and domain_name:
                            domain = f"{domain_name}.sol"  # Add .sol suffix
                        else:
                            domain = None
                        
                        self.mem[a] = (domain, now)
                        out[a] = domain
                        to_persist.append((a, domain, now))
                        
                        # Save domain as persistent tag if found
                        if domain:
                            self._save_address_tag(a, domain)

            except Exception as e:
                # On error, mark as unknown but cache short-term
                print(f"[DOMAIN_RESOLVER] ⚠ Error resolving batch: {e}", flush=True)
                for a in batch:
                    self.mem[a] = (None, now)
                    out[a] = None
                    to_persist.append((a, None, now))

            # Gentle throttle between batches
            await asyncio.sleep(0.05)

        if to_persist:
            self._db_set_many(to_persist)

        return out


class RealTimeCreatorFundingExtractor:
    """Extract creator funding in real-time when new tokens launch"""

    def __init__(self):
        self.processed_creators: Set[str] = set()
        self.session = None
        self.domain_resolver: Optional[DomainResolver] = None
        self.seen_bonding_curves: Set[str] = set()  # Cache bonding curves to skip trading noise
        self._rpc_sem = asyncio.Semaphore(MAX_CONCURRENT_RPC)
        # X76.3 -- the extractor itself now tracks every fire-and-forget
        # background task it spawns (CEX detection, BlockSec batching,
        # post-launch automation), not just creator_funding_worker's own
        # bolt-on _await_orphaned_tasks sweep. This makes ANY caller --
        # the worker, the listener, a future one-shot recovery tool --
        # able to supervise these tasks via wait_for_background_tasks(),
        # instead of only the worker (which diffs the global asyncio task
        # set as a heuristic and can't see tasks spawned by a DIFFERENT
        # extractor instance or caller).
        self._background_tasks: Set[asyncio.Task] = set()

    def _spawn_background_task(self, coro) -> asyncio.Task:
        """Create a tracked fire-and-forget task. Discards itself from the
        registry on completion (success, exception, or cancellation) so the
        set never grows unbounded across a long-lived process."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def wait_for_background_tasks(self, timeout: float = 20.0) -> None:
        """Bounded wait for every background task this extractor instance
        has ever spawned and not yet finished. Callers that need the "never
        let a write-capable child task outlive its parent" invariant (the
        tight-polling creator_funding_worker; any future one-shot recovery
        tool) should call this immediately after each extraction instead of
        (or in addition to) the worker's own all_tasks()-diffing sweep --
        this is exact (the extractor's own bookkeeping), where the diff
        approach is a heuristic over the whole event loop's task set.
        Never cancels stragglers past the timeout: a cancelled write mid-
        commit is worse than a slow one (see Phase 2 test coverage)."""
        pending = {t for t in self._background_tasks if not t.done()}
        if not pending:
            return
        done, still_pending = await asyncio.wait(pending, timeout=timeout)
        for t in done:
            exc = t.exception() if not t.cancelled() else None
            if exc:
                print(f"[REALTIME_FUNDING] background task raised (non-fatal, enrichment only): {exc}", flush=True)
        if still_pending:
            print(f"[REALTIME_FUNDING] {len(still_pending)} background task(s) still running "
                  f"after {timeout}s -- leaving them to finish on their own, not cancelling "
                  f"(would corrupt whatever they're mid-writing)", flush=True)
        # Phase 1: Initialize CursorManager for incremental extraction
        self.cursor_mgr = None
        try:
            from src.core.cursor_manager import CursorManager
            self.cursor_mgr = CursorManager(DB_PATH)
            print("✅ CursorManager initialized for Phase 1 deployment", flush=True)
        except Exception as e:
            print(f"⚠ CursorManager initialization failed: {e} (Phase 1 disabled)", flush=True)
        # Phase 2: Initialize RPCCache for response-level caching
        self.rpc_cache = None
        try:
            from src.core.rpc_cache import RPCCache
            self.rpc_cache = RPCCache(DB_PATH)
            print("✅ RPCCache initialized for Phase 2 deployment", flush=True)
        except Exception as e:
            print(f"⚠ RPCCache initialization failed: {e} (Phase 2 disabled)", flush=True)  # FIX #8: Bound RPC concurrency

    async def init_session(self):
        """Initialize aiohttp session and domain resolver"""
        if not self.session:
            self.session = aiohttp.ClientSession()
        if not self.domain_resolver:
            self.domain_resolver = DomainResolver(DB_PATH, self.session)

        # Initialize domain registry
        from src.utils.domain_mapping import init_domain_registry
        init_domain_registry()

        # Setup SQLite optimizations for performance
        self._setup_db_optimizations()

    def _setup_db_optimizations(self):
        """Configure SQLite for better performance (PRAGMA settings)"""
        try:
            conn = db_connect(DB_PATH, timeout=60)
            conn.execute("PRAGMA temp_store=MEMORY;")
            conn.execute("PRAGMA cache_size=-50000;")  # ~50MB cache
            conn.commit()
            conn.close()
            print("[PERF] SQLite optimizations applied", flush=True)
        except Exception as e:
            print(f"[PERF] Warning: Could not apply SQLite optimizations: {e}", flush=True)

    async def close_session(self):
        """Close aiohttp session"""
        if self.session:
            await self.session.close()

    async def _post_rpc(
        self,
        payload: dict,
        cache_action: str = "none",
        credits_saved: int = 0,
    ) -> Optional[dict]:
        """Post to RPC with failover chain + semaphore concurrency control - mirrors post_migration_analyzer approach"""
        async with self._rpc_sem:  # FIX #8: Bound concurrent RPC calls
            for attempt in range(MAX_RETRIES):
                # Try each RPC endpoint in the failover chain
                for rpc_url in RPC_URLS:
                    try:
                        # Record RPC request for metrics
                        start_time = time.time()
                        async with self.session.post(
                            rpc_url,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=RPC_TIMEOUT)
                        ) as resp:
                            latency_ms = (time.time() - start_time) * 1000

                            # HTTP-level errors
                            if resp.status != 200:
                                record_request(
                                    section="creator_funding",
                                    provider="helius_rpc",
                                    method=payload.get("method", "unknown"),
                                    status_code=resp.status,
                                    latency_ms=latency_ms,
                                    mode="realtime",
                                    retries=attempt,
                                    source_file="realtime_creator_funding_extractor",
                                    cache_action=cache_action,
                                    credits_saved=credits_saved,
                                    error=f"HTTP {resp.status}",
                                )
                                if resp.status == 429:
                                    # Rate limited - check for Retry-After header
                                    retry_after = resp.headers.get("Retry-After")
                                    retry_delay = None
                                    if retry_after:
                                        try:
                                            retry_delay = float(retry_after)
                                        except (ValueError, TypeError):
                                            retry_delay = None

                                    wait_time = retry_delay or (0.5 * (2 ** attempt))
                                    await asyncio.sleep(min(30.0, wait_time))  # Cap at 30s
                                    continue
                                elif resp.status >= 500:
                                    # Server error, try next RPC
                                    continue
                                else:
                                    # Client error, don't retry
                                    return None

                            latency_ms = (time.time() - start_time) * 1000
                            data = await resp.json()

                            # RPC-level errors
                            if "error" in data:
                                error_code = data["error"].get("code", -1)
                                # Record error for metrics
                                record_request(
                                    section="creator_funding",
                                    provider="helius_rpc",
                                    method=payload.get("method", "unknown"),
                                    status_code=200,  # HTTP level was OK
                                    latency_ms=latency_ms,
                                    mode="realtime",
                                    retries=attempt,
                                    source_file="realtime_creator_funding_extractor",
                                    cache_action=cache_action,
                                    credits_saved=credits_saved,
                                    error=f"RPC error {error_code}",
                                )
                                # Retryable RPC errors
                                if error_code in {-32008, -32000, -32003, -32009}:
                                    continue
                                else:
                                    return None

                            # Success
                            if "result" in data:
                                record_request(
                                    section="creator_funding",
                                    provider="helius_rpc",
                                    method=payload.get("method", "unknown"),
                                    status_code=resp.status,
                                    latency_ms=latency_ms,
                                    mode="realtime",
                                    retries=attempt,
                                    source_file="realtime_creator_funding_extractor",
                                    cache_action=cache_action,
                                    credits_saved=credits_saved,
                                )
                                return data

                    except asyncio.TimeoutError:
                        # Timeout on this RPC, try next
                        continue
                    except Exception as e:
                        # Other errors, try next RPC
                        continue

                # After trying all RPCs once, wait before next attempt
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(0.5 * (2 ** attempt))

        return None

    async def get_signatures_until_time(
        self, creator: str, until_timestamp: int, limit: int = 1000
    ) -> List[tuple]:
        """
        Get signatures UNTIL a specific timestamp (Unix seconds).
        Returns list of (signature, blockTime) tuples.
        (with Phase 2 cache for pagination pages)
        """
        signatures = []
        before = None

        while True:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    creator,
                    {
                        "limit": limit,
                        **({"before": before} if before else {})
                    }
                ]
            }

            # Phase 2: Check cache before RPC call (getSignaturesForAddress = 10 credits)
            cache_result = None
            sig_cache_key = None
            if self.rpc_cache is not None:
                sig_cache_key = self.rpc_cache.make_key_get_signatures(creator, before, limit)
                cache_result = self.rpc_cache.get(sig_cache_key)

            if cache_result is not None:
                # Cache hit
                result = cache_result
                record_request(
                    section="creator_funding",
                    provider="helius_rpc",
                    method="getSignaturesForAddress",
                    status_code=200,
                    latency_ms=0.0,
                    mode="realtime",
                    retries=0,
                    source_file="realtime_creator_funding_extractor",
                    cache_action="hit",
                    credits_saved=10,
                )
            else:
                # Cache miss: make live RPC call
                result = await self._post_rpc(payload, cache_action="miss", credits_saved=0)
                # Cache the result for future pagination requests
                if result and "result" in result and sig_cache_key and self.rpc_cache is not None:
                    self.rpc_cache.set(sig_cache_key, result, "getSignaturesForAddress")

            if not result or "result" not in result:
                break

            sigs = result.get("result", [])
            if not sigs:
                break

            for sig_info in sigs:
                sig = sig_info["signature"]
                block_time = sig_info.get("blockTime", 0)

                # API returns signatures newest-to-oldest
                # We want all signatures BEFORE the target time (for pre-migration funding)
                # Skip anything at or after the target time
                if block_time and block_time >= until_timestamp:
                    # Still in the post-migration period, skip
                    continue

                # This signature is before target time, include it
                signatures.append((sig, block_time))

            # If we got fewer than requested, we've reached the end
            if len(sigs) < limit:
                break

            before = sigs[-1]["signature"]
            await asyncio.sleep(0.05)

        return signatures

    async def get_transaction(self, signature: str) -> Optional[Dict]:
        """Get transaction with RPC failover (with Phase 2 cache)"""
        # Phase 2: Check cache first (getTransaction = 10 credits, 24h TTL, immutable data)
        if self.rpc_cache is not None:
            cache_key = self.rpc_cache.make_key_get_transaction(signature)
            cached = self.rpc_cache.get(cache_key)
            if cached is not None:
                # Cache hit: record metric
                record_request(
                    section="creator_funding",
                    provider="helius_rpc",
                    method="getTransaction",
                    status_code=200,
                    latency_ms=0.0,
                    mode="realtime",
                    retries=0,
                    source_file="realtime_creator_funding_extractor",
                    cache_action="hit",
                    credits_saved=10,
                )
                return cached

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
            ]
        }
        result = await self._post_rpc(payload, cache_action="miss", credits_saved=0)
        if result and "result" in result:
            tx = result.get("result")
            # RPC may return null for old/pruned transactions
            if tx is not None:
                # Phase 2: Cache the result for future requests
                if self.rpc_cache is not None:
                    cache_key = self.rpc_cache.make_key_get_transaction(signature)
                    self.rpc_cache.set(cache_key, tx, "getTransaction")
                return tx
        return None

    async def get_oldest_enhanced_transaction(self, address: str) -> Optional[Dict]:
        """Fetch the oldest known enhanced transaction for an address with a single cheap request."""
        if not USE_HELIUS or not self.session:
            return None

        query_url = (
            f"https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions"
            f"?api-key={_RPC_KEY}&limit=1&sort-order=asc&commitment=finalized"
        )
        try:
            start_time = time.time()
            async with self.session.get(
                query_url,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                latency_ms = (time.time() - start_time) * 1000
                record_request(
                    section="creator_funding",
                    provider="helius_rpc",
                    method="helius_enhanced_oldest_transaction",
                    status_code=resp.status,
                    latency_ms=latency_ms,
                    mode="realtime",
                    source_file="realtime_creator_funding_extractor",
                    cache_action="miss",
                    credits_saved=0,
                )
                if resp.status != 200:
                    print(f"[REALTIME_FUNDING]    ⚠ Oldest-tx probe HTTP {resp.status}", flush=True)
                    return None
                page = await resp.json()
                if isinstance(page, list) and page:
                    return page[0]
        except Exception as e:
            print(f"[REALTIME_FUNDING]    ⚠ Oldest-tx probe failed: {e}", flush=True)
        return None

    def extract_sol_transfers(self, tx: Dict, creator: str) -> List[Dict]:
        """Extract SOL transfers to/from creator"""
        transfers = []

        try:
            if not tx or "meta" not in tx:
                return transfers

            meta = tx.get("meta", {})
            pre_balances = meta.get("preBalances", [])
            post_balances = meta.get("postBalances", [])
            accounts = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])

            # Find creator account index
            creator_idx = None
            for idx, acc in enumerate(accounts):
                acc_str = acc.get("pubkey") if isinstance(acc, dict) else str(acc)
                if acc_str == creator:
                    creator_idx = idx
                    break

            if creator_idx is None:
                return transfers

            # Calculate balance change for creator
            if creator_idx < len(pre_balances) and creator_idx < len(post_balances):
                balance_change = post_balances[creator_idx] - pre_balances[creator_idx]

                # Only track meaningful amounts (> 1000 lamports = 0.000001 SOL)
                if abs(balance_change) > 1000:
                    amount_sol = abs(balance_change) / 1e9

                    # Determine direction
                    direction = "in" if balance_change > 0 else "out"

                    # FIX #3: Find best counterparty (account with opposite balance change)
                    # For multi-party transactions, identify the LARGEST opposite account (not smallest)
                    best_counterparty = None
                    best_match = 0  # FIX: was float('inf') — pick MAXIMUM magnitude

                    for idx2, acc2 in enumerate(accounts):
                        if idx2 == creator_idx or idx2 >= len(pre_balances) or idx2 >= len(post_balances):
                            continue

                        balance_change2 = post_balances[idx2] - pre_balances[idx2]

                        # Look for accounts with opposite direction
                        if direction == "in" and balance_change2 < 0:
                            # Best match is MOST negative (largest outflow = biggest sender) — FIX: > instead of <
                            if abs(balance_change2) > best_match:
                                best_match = abs(balance_change2)
                                best_counterparty = acc2.get("pubkey") if isinstance(acc2, dict) else str(acc2)
                        elif direction == "out" and balance_change2 > 0:
                            # Best match is MOST positive (largest inflow = primary recipient) — FIX: > instead of <
                            if balance_change2 > best_match:
                                best_match = balance_change2
                                best_counterparty = acc2.get("pubkey") if isinstance(acc2, dict) else str(acc2)

                    # Report the transfer with best counterparty found
                    # (if no counterparty found, use system/fee account as placeholder)
                    counterparty = best_counterparty or "SYSTEM"

                    transfers.append({
                        "direction": direction,
                        "counterparty": counterparty,
                        "amount_sol": amount_sol,
                    })

        except Exception as e:
            pass

        return transfers

    def _save_funder(self, creator: str, funder: str, amount_sol: float, funders_delta: Dict[str, dict]):
        """
        Accumulate funder in memory dict (sync, no DB ops, no domain resolution).
        CEX/INFRA classification deferred to flush phase.
        """
        if funder not in funders_delta:
            funders_delta[funder] = {"amount": 0, "is_cex": 0, "cex_exchange": None, "cex_type": None, "is_classified": 0}
        funders_delta[funder]["amount"] += amount_sol

    def _save_recipient(self, creator: str, recipient: str, amount_sol: float, recipients_delta: Dict[str, float]):
        """
        Accumulate recipient in memory dict (sync, no DB ops).
        Recipient classification deferred to flush phase.
        """
        if recipient not in recipients_delta:
            recipients_delta[recipient] = 0
        recipients_delta[recipient] += amount_sol

    async def _flush_page_batch(self, conn, creator: str, funders_delta: Dict[str, dict], recipients_delta: Dict[str, float], domain_addrs: Set[str], jito_events: List[tuple], transfer_index_rows: List[tuple] = None):
        """
        Flush accumulated page data to database in batch.
        - Insert all funders with CEX/INFRA classification
        - Insert all recipients
        - Single commit
        - Batch resolve domains
        - Insert Jito events
        - Write transfer_index rows (zero extra RPC)
        """
        from src.utils.infra_mapping import is_infrastructure_account, is_cex_account

        try:
            cursor = conn.cursor()

            # Process funders: check CEX/INFRA status and upsert
            for funder, funder_data in funders_delta.items():
                is_cex = 0
                cex_exchange = None
                cex_type = None
                is_classified = 0

                # Check CEX wallet database
                try:
                    cursor.execute("""
                        SELECT exchange_name, wallet_type
                        FROM cex_wallets
                        WHERE cex_address = ? AND is_active = 1
                        LIMIT 1
                    """, (funder,))
                    cex_row = cursor.fetchone()
                    if cex_row:
                        exchange, wallet_type = cex_row
                        cex_exchange = exchange
                        cex_type = wallet_type
                        is_cex = 1
                        is_classified = 1
                        print(f"[FUNDING] 🏛️ CEX FUNDER: {exchange} {wallet_type} → {creator[:16]}... ({funder_data['amount']:.2f} SOL)", flush=True)
                except Exception:
                    pass

                # Check infrastructure
                if not cex_exchange and is_infrastructure_account(funder):
                    is_classified = 1

                # Check CEX in mapping
                if not cex_exchange and is_cex_account(funder):
                    is_classified = 1

                fully_analyzed = 1 if (cex_exchange or is_classified) else 0

                cursor.execute("""
                    INSERT OR REPLACE INTO creator_funders
                    (creator_address, funder_address, amount_sol, first_detected_at, is_cex, cex_exchange, cex_type, is_classified, fully_analyzed)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?, ?, ?)
                """, (creator, funder, funder_data['amount'], is_cex, cex_exchange, cex_type, is_classified, fully_analyzed))

            # Process recipients: check INFRA/CEX and upsert
            for recipient, amount_sol in recipients_delta.items():
                is_infra = recipient in INFRASTRUCTURE_ACCOUNTS
                is_cex = recipient in CEX_ACCOUNTS

                recipient_type = None
                recipient_name = None
                if is_infra:
                    recipient_type = "INFRA"
                    recipient_name = INFRASTRUCTURE_ACCOUNTS[recipient].get("name", "")
                elif is_cex:
                    recipient_type = "CEX"
                    recipient_name = CEX_ACCOUNTS[recipient].get("name", "")

                cursor.execute("""
                    INSERT OR REPLACE INTO creator_receivers
                    (creator_address, receiver_address, amount_sol, receiver_type, receiver_name, first_detected_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (creator, recipient, amount_sol, recipient_type, recipient_name))

            # Single commit after all funders and recipients
            conn.commit()

            # Batch resolve domains (after DB commit, so resolver can cache reads)
            if domain_addrs and self.domain_resolver:
                try:
                    domains = await self.domain_resolver.resolve_primary_domains(list(domain_addrs))
                    for addr, domain in domains.items():
                        if domain:
                            print(f"[DOMAIN] 🌐 Resolved: {addr[:16]}... → {domain}", flush=True)
                except Exception as domain_err:
                    print(f"[DOMAIN] ⚠ Batch resolution error: {domain_err}", flush=True)

            # Insert Jito events in batch
            if jito_events:
                cursor.executemany("""
                    INSERT OR IGNORE INTO creator_service_history
                    (creator_address, tag, amount_sol, tx_signature, mint, network_fee_sol, tip_percentage, tx_type, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, jito_events)
                conn.commit()
                print(f"[JITO] 🪂 Batch inserted {len(jito_events)} Jito events", flush=True)

            # Write transfer_index rows collected from this page (zero extra RPC)
            if transfer_index_rows:
                try:
                    cursor.executemany("""
                        INSERT OR IGNORE INTO transfer_index
                        (signature, source, destination, amount_lamports, slot, block_time, indexed_at, is_valid, transfer_type)
                        VALUES (?, ?, ?, ?, 0, ?, ?, 1, 'standard')
                    """, transfer_index_rows)
                    conn.commit()
                except Exception as ti_err:
                    print(f"[TRANSFER_INDEX] ⚠ Insert error: {ti_err}", flush=True)

        except Exception as e:
            print(f"[FLUSH] ❌ Batch flush error: {e}", flush=True)
            import traceback
            traceback.print_exc()

    def _save_outgoing_transfer(self, creator: str, recipient: str, amount_sol: float, sig: str = None, block_time: int = None):
        """Save outgoing transfer from creator to recipient

        Checks against:
        1. CEX_ACCOUNTS mapping (immediate)
        2. cex_wallets table (manual + auto-detected)
        3. address_classification table (auto-detected with confidence)
        """
        try:
            from src.utils.infra_mapping import is_cex_account, CEX_ACCOUNTS

            # X76.3 -- managed_db_connect guarantees conn.close() (and therefore
            # TrackedConnection._release_write_lane(), which clears the
            # process-wide write lock, the cross-process file lease, and this
            # thread's _thread_write_lease reentrancy guard) on every exit path,
            # including an exception raised mid-write. Before this, close() was
            # only reached on the success path -- an exception anywhere in this
            # function (including inside the `with DB_WRITE_LOCK:` block) left
            # the lease held on whatever thread this call landed on, poisoning
            # every subsequent to_thread-dispatched write on that same pooled
            # thread with NestedDatabaseWriteError.
            with managed_db_connect(DB_PATH, timeout=60) as conn:
                cursor = conn.cursor()

                # Check if recipient is a known CEX wallet
                recipient_type = None
                exchange_name = None
                wallet_type = None
                is_cex = 0
                classification_confidence = None

                # Layer 1: Check CEX_ACCOUNTS mapping (immediate)
                if is_cex_account(recipient):
                    cex_info = CEX_ACCOUNTS.get(recipient, {})
                    exchange_name = cex_info.get("exchange", "Unknown")
                    wallet_type = cex_info.get("name", "Exchange Wallet")
                    is_cex = 1
                    recipient_type = f"cex_{exchange_name.lower()}"
                    print(f"[FUNDING] 💸 OUTGOING TO CEX: {creator[:16]}... → {exchange_name} {wallet_type} ({amount_sol:.2f} SOL)", flush=True)

                # Layer 2: Check cex_wallets table (manual + auto-detected)
                else:
                    try:
                        cursor.execute("""
                            SELECT exchange_name, wallet_type
                            FROM cex_wallets
                            WHERE cex_address = ? AND is_active = 1
                            LIMIT 1
                        """, (recipient,))
                        cex_row = cursor.fetchone()
                        if cex_row:
                            exchange_name, wallet_type = cex_row
                            is_cex = 1
                            recipient_type = f"cex_{exchange_name.lower()}"
                            print(f"[FUNDING] 💸 OUTGOING TO CEX: {creator[:16]}... → {exchange_name} {wallet_type} ({amount_sol:.2f} SOL)", flush=True)
                    except Exception as e:
                        pass

                # Layer 3: Check address_classification (auto-detected with confidence)
                if not is_cex:
                    try:
                        cursor.execute("""
                            SELECT classification, confidence_score, solscan_exchange_name
                            FROM address_classification
                            WHERE address = ?
                            LIMIT 1
                        """, (recipient,))
                        class_row = cursor.fetchone()
                        if class_row:
                            classification, confidence, solscan_exch = class_row
                            if classification == 'cex_confirmed':  # Only high confidence
                                exchange_name = solscan_exch or "Detected CEX"
                                wallet_type = "Auto-detected"
                                is_cex = 1
                                classification_confidence = confidence
                                recipient_type = f"cex_autodetected_{exchange_name.lower()}"
                                print(f"[FUNDING] 💸 OUTGOING TO CEX (AUTO-DETECTED): {creator[:16]}... → {exchange_name} (confidence: {confidence}) ({amount_sol:.2f} SOL)", flush=True)
                    except Exception as e:
                        pass

                # X73.2 -- see check_create_tx_for_jitotip's own comment: write
                # section only, serialized against concurrent callers. This
                # function is called once per transfer inside a loop in
                # extract_outgoing_transfers, so it's the highest-frequency write
                # path among the four gathered enrichment functions.
                with DB_WRITE_LOCK:
                    cursor.execute("""
                        INSERT OR REPLACE INTO creator_outgoing_transfers
                        (creator_address, recipient_address, amount_sol, transaction_signature, block_time,
                         recipient_type, is_cex, cex_exchange, cex_type, classification_confidence, first_detected_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (creator, recipient, amount_sol, sig, block_time, recipient_type, is_cex,
                          exchange_name, wallet_type, classification_confidence))

                    conn.commit()
        except Exception as e:
            print(f"[FUNDING] ⚠ Error saving outgoing transfer: {e}", flush=True)

    def get_creator_cex_outflows(self, creator: str) -> Dict:
        """Get all SOL transfers from creator to CEX addresses"""
        try:
            conn = db_connect(DB_PATH, timeout=15)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    recipient_address,
                    amount_sol,
                    cex_exchange,
                    cex_type,
                    classification_confidence,
                    transaction_signature,
                    first_detected_at
                FROM creator_outgoing_transfers
                WHERE creator_address = ? AND is_cex = 1
                ORDER BY amount_sol DESC
            """, (creator,))

            outflows = [dict(row) for row in cursor.fetchall()]
            conn.close()
            return outflows
        except Exception as e:
            print(f"[FUNDING] Error getting CEX outflows: {e}")
            return []

    async def extract_incoming_transfers(self, creator: str) -> Dict:
        """
        Search for incoming SOL transfers to creator by scanning recent transactions.
        This finds transfers where creator is a RECIPIENT (not signer).

        Alternative approach: We look at all recent transactions on-chain that mention
        the creator address and extract transfers TO the creator.
        """
        print(f"[REALTIME_FUNDING]    🔍 Searching for INCOMING transfers to creator...", flush=True)

        funders = {}
        max_attempts = 5
        attempt = 0

        # We'll need to search recent block transactions
        # This is a simplified version - in production, use indexed services
        try:
            # For now, return empty - we'd need to implement transaction scanning
            # This would require either:
            # 1. Scanning recent blocks manually
            # 2. Using a service like Helius that indexes transactions
            # 3. Using getSignaturesForAddress on all known funders (not scalable)
            return funders
        except Exception as e:
            print(f"[REALTIME_FUNDING]    ⚠ Error searching incoming: {e}", flush=True)
            return funders

    async def extract_outgoing_transfers(self, creator: str, after_timestamp: int, limit: int = 100) -> Dict:
        """
        Search for outgoing transfers FROM creator AFTER a specific timestamp (post-migration).
        Returns dict of recipient -> {amount: total_sol, count: tx_count}
        """
        print(f"[REALTIME_FUNDING]    🔍 Searching for OUTGOING transfers after migration...", flush=True)

        recipients = {}
        before = None
        max_sigs = 0

        try:
            # Get all signatures for the creator
            while max_sigs < limit:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [
                        creator,
                        {
                            "limit": 50,
                            **({"before": before} if before else {})
                        }
                    ]
                }

                result = await self._post_rpc(payload)
                if not result or "result" not in result:
                    break

                sigs = result.get("result", [])
                if not sigs:
                    break

                for sig_info in sigs:
                    sig = sig_info["signature"]
                    block_time = sig_info.get("blockTime", 0)

                    # We want signatures AFTER the migration time (post-migration)
                    if block_time and block_time <= after_timestamp:
                        # Before or at migration time, skip
                        continue

                    # This is post-migration, analyze it
                    tx = await self.get_transaction(sig)
                    if not tx:
                        continue

                    transfers = self.extract_sol_transfers(tx, creator)
                    for transfer in transfers:
                        if transfer["direction"] != "out":
                            continue

                        counterparty = transfer["counterparty"]
                        amount = transfer["amount_sol"]

                        if counterparty not in recipients:
                            recipients[counterparty] = {"amount": 0, "count": 0}

                        recipients[counterparty]["amount"] += amount
                        recipients[counterparty]["count"] += 1

                        # Save to database immediately. X73.2 -- dispatched via
                        # to_thread: _save_outgoing_transfer acquires
                        # DB_WRITE_LOCK (a synchronous threading.RLock)
                        # internally, and calling that directly from this
                        # coroutine's own thread (the event loop thread) would
                        # block the ENTIRE event loop for as long as another
                        # thread holds the lock -- including freezing
                        # asyncio.wait_for's own timeout mechanism in
                        # whatever awaited this extraction, which needs the
                        # loop running to fire. to_thread keeps the lock
                        # acquisition off the event loop thread entirely.
                        await asyncio.to_thread(
                            self._save_outgoing_transfer, creator, counterparty, amount, sig, block_time,
                        )

                    max_sigs += 1

                if len(sigs) < 50:
                    break

                before = sigs[-1]["signature"]
                await asyncio.sleep(0.1)  # Increased delay to reduce rate limiting

            return recipients

        except Exception as e:
            print(f"[REALTIME_FUNDING]    ⚠ Error searching outgoing: {e}", flush=True)
            return recipients

    def _mark_extraction_complete(self, creator: str, total_funders: int, total_recipients: int, total_inbound: float, total_outbound: float):
        """
        Mark extraction as complete for a creator by updating creator_state.
        
        This signals to the UI that extraction has finished and results are ready.
        Called after all funder/recipient data has been saved to the database.
        """
        try:
            # X76.3 -- managed_db_connect guarantees close() on every exit path.
            with managed_db_connect(DB_PATH, timeout=60) as conn:
                cursor = conn.cursor()

                # Update creator_state with final extraction status
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_state
                    (creator_pubkey, last_processed_at, total_signatures_processed, total_sol_in_lamports, total_sol_out_lamports, updated_at)
                    VALUES (?, CURRENT_TIMESTAMP, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    creator,
                    total_funders + total_recipients,  # count of transactions processed
                    int(total_inbound * 1_000_000_000),  # convert SOL to lamports
                    int(total_outbound * 1_000_000_000)   # convert SOL to lamports
                ))

                conn.commit()

            print(f"[EXTRACTION] ✅ COMPLETED for {creator[:16]}... | Funders: {total_funders}, Recipients: {total_recipients}", flush=True)

        except Exception as err:
            print(f"[EXTRACTION] ⚠ Could not mark extraction complete: {err}", flush=True)

    async def extract_for_creator(self, creator: str, migration_timestamp_str: str) -> Dict:
        """
        Extract funding activity for a creator using Helius Enhanced API.
        Uses same reliable approach as standalone tmp.py script:
        - Single page fetch (100 txs) instead of rapid pagination
        - Proper delays between requests
        - Filters pre-migration transfers only
        - Excludes token mints and bonding curves from both directions
        - Skips transactions with ANY Pump.Fun token transfers (bonding curves, migrations)
        
        This is slower than pagination but avoids 429 rate limit errors.
        """
        # Check if already processed in this session
        if creator in self.processed_creators:
            return {"status": "already_processed"}

        # Check creator funding cache (Layer 6 optimization)
        cache_action = "full_scan"
        credits_saved = 0
        if CREATOR_CACHE is not None:
            cached_result = CREATOR_CACHE.get_cached_funders(creator)
            if cached_result is not None:
                # Creator already cached, skip extraction
                cache_action = "skip"
                credits_saved = 150  # Saved full extraction cost
                print(f"[REALTIME_FUNDING] ✅ SKIP {creator[:16]}... (cached)", flush=True)
                return {
                    "status": "cached",
                    "creator": creator,
                    "funders": cached_result.get("funders", []),
                    "cache_action": cache_action,
                }

        # Mark as processed to prevent duplicate API calls in same session
        self.processed_creators.add(creator)

        # FIX #6: Fail safe if no Helius API key
        if not USE_HELIUS:
            print("[REALTIME_FUNDING] ⚠ No HELIUS_API_KEY set — skipping enriched extraction", flush=True)
            return {"creator": creator, "error": "no_helius_key", "status": "skipped"}

        # X76.3 -- extraction_conn is opened below and held open across the
        # entire multi-hundred-line paging loop (many awaits, many possible
        # exceptions/early returns). Before this fix, it was only closed on
        # ONE success path near the end of this function -- every early
        # `return` inside the try block (e.g. the pagination error handler
        # a few hundred lines down) left it open, and since it's a
        # TrackedConnection, a leaked-open connection that was ever used for
        # a write leaves the write lane/lease held on this thread until the
        # reaper eventually force-closes it (up to _MAX_TXN_CONNECTION_AGE_SECS
        # later). extraction_conn is declared here, before the try, so this
        # function's own finally can safely check `is not None` regardless of
        # which line raised.
        extraction_conn = None
        try:
            # Parse migration timestamp
            if "T" in migration_timestamp_str:
                migration_dt = datetime.fromisoformat(migration_timestamp_str.replace("Z", "+00:00"))
            else:
                migration_dt = datetime.fromisoformat(migration_timestamp_str)

            migration_timestamp = int(migration_dt.timestamp())

            # Calculate 1 month cutoff (30 days back from migration)
            one_month_cutoff = migration_timestamp - (30 * 24 * 60 * 60)

            print(f"[REALTIME_FUNDING] 🔍 Extracting creator funding for {creator[:16]}...", flush=True)
            print(f"[REALTIME_FUNDING]    Migration timestamp: {migration_timestamp_str}", flush=True)
            print(f"[REALTIME_FUNDING]    Will fetch up to 1 month of history", flush=True)

            # FIX #4: Open one shared connection for entire extraction run + ensure schema
            extraction_conn = db_connect(DB_PATH, timeout=90)
            extraction_cursor = extraction_conn.cursor()

            # Build exclusion set: token mints + bonding curves created by this creator
            exclude_set = set()

            # Get all tokens launched by this creator to exclude them
            extraction_cursor.execute("""
                SELECT mint, bonding_curve_pda, create_tx_signature
                FROM token_analysis
                WHERE earliest_tx_creator = ?
            """, (creator,))
            creator_tokens = extraction_cursor.fetchall()

            # Get CREATE tx signature(s) - if multiple tokens, just use the first one
            # (we mainly want to avoid double-counting Jito tips on the CREATE tx)
            create_tx_signature = None
            for row in creator_tokens:
                mint, bonding_curve, create_sig = row
                if create_sig and not create_tx_signature:
                    create_tx_signature = create_sig
                if mint:
                    exclude_set.add(mint)
                if bonding_curve:
                    exclude_set.add(bonding_curve)

            # Exclude only funders already identified for THIS SPECIFIC CREATOR
            # Don't exclude globally analyzed funders, as they may fund multiple creators
            extraction_cursor.execute("""
                SELECT DISTINCT funder_address
                FROM creator_funders
                WHERE creator_address = ? AND fully_analyzed = 1
            """, (creator,))
            fully_analyzed = extraction_cursor.fetchall()
            for (funder,) in fully_analyzed:
                exclude_set.add(funder)

            # Check if creator is already tagged with deBridge usage
            # If so, skip deBridge transaction detection in the loop
            extraction_cursor.execute("""
                SELECT 1 FROM creator_tags
                WHERE creator_address = ? AND tag = ?
            """, (creator, "uses_debridge"))
            creator_uses_debridge = extraction_cursor.fetchone() is not None

            if creator_uses_debridge:
                print(f"[REALTIME_FUNDING]    ℹ Creator already tagged as 'uses_debridge', skipping detection", flush=True)

            if exclude_set:
                print(f"[REALTIME_FUNDING]    Excluding {len(exclude_set)} addresses (creator's tokens & bonding curves)", flush=True)

            # Ensure tables exist
            try:
                extraction_cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_service_history (
                        creator_address TEXT,
                        tag TEXT,
                        amount_sol REAL,
                        tx_signature TEXT,
                        mint TEXT,
                        network_fee_sol REAL,
                        tip_percentage REAL,
                        tx_type TEXT,
                        created_at TEXT,
                        PRIMARY KEY (creator_address, tx_signature, tag)
                    )
                """)
                extraction_cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_receivers (
                        creator_address TEXT NOT NULL,
                        receiver_address TEXT NOT NULL,
                        amount_sol REAL,
                        receiver_type TEXT,
                        receiver_name TEXT,
                        first_detected_at TEXT,
                        PRIMARY KEY (creator_address, receiver_address)
                    )
                """)
                extraction_conn.commit()
            except Exception:
                pass  # Tables already exist

            # Use Helius Enhanced API - paginate through all transactions
            funders = {}
            recipients = {}
            filtered_dust = 0
            filtered_excluded = 0
            filtered_token_transfers = 0

            MIN_SOL = 0.001  # Filter dust

            print(f"[REALTIME_FUNDING]    Fetching all pre-migration transactions from Helius API...", flush=True)

            try:
                url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{creator}/transactions"

                page_num = 0
                before_signature = None
                total_fetched = 0
                found_pre_migration = False
                empty_inbound_pages = 0
                oldest_tx_seeded_funders = 0
                page_limit_for_scan = MAX_PAGES

                # Phase 1: Load cursor if available (enables incremental extraction)
                cursor_for_creator = None
                if self.cursor_mgr:
                    try:
                        cursor_for_creator = self.cursor_mgr.get_cursor(creator)
                        if cursor_for_creator and cursor_for_creator.last_signature:
                            before_signature = cursor_for_creator.last_signature
                            print(f"[REALTIME_FUNDING]    ✅ Loaded cursor: will fetch signatures after {before_signature[:20]}...", flush=True)
                        else:
                            print(f"[REALTIME_FUNDING]    ℹ No cursor found for {creator[:16]}... (first-time scan)", flush=True)
                    except Exception as e:
                        print(f"[REALTIME_FUNDING]    ⚠ Error loading cursor: {e} (falling back to full scan)", flush=True)

                # Cheap bootstrap: inspect the oldest known tx for first-time creators.
                # The first-ever tx often contains the creator's initial funding source.
                if before_signature is None:
                    oldest_tx = await self.get_oldest_enhanced_transaction(creator)
                    if oldest_tx:
                        oldest_sig = oldest_tx.get("signature", "")
                        oldest_ts = oldest_tx.get("timestamp", 0)
                        native = oldest_tx.get("nativeTransfers") or []
                        oldest_funders_delta: Dict[str, dict] = {}
                        oldest_recipients_delta: Dict[str, float] = {}
                        oldest_domain_addrs: Set[str] = {creator}
                        oldest_transfer_index_rows: List[tuple] = []
                        seeded_recipients = 0
                        for nt in native:
                            frm = nt.get("fromUserAccount")
                            to = nt.get("toUserAccount")
                            amt = nt.get("amount", 0)
                            if not isinstance(frm, str) or not isinstance(to, str):
                                continue
                            amount_sol = amt / 1_000_000_000
                            # Collect for transfer_index before dust filter
                            if oldest_sig and isinstance(amt, int) and amt > 0 and oldest_ts and oldest_ts > 0:
                                oldest_transfer_index_rows.append((oldest_sig, frm, to, amt, oldest_ts, time.time()))
                            if amount_sol < MIN_SOL:
                                continue
                            if to == creator and frm not in exclude_set and frm not in DUST_ADDRESSES:
                                if frm not in funders:
                                    oldest_tx_seeded_funders += 1
                                    funders[frm] = 0
                                funders[frm] += amount_sol
                                self._save_funder(creator, frm, amount_sol, oldest_funders_delta)
                                oldest_domain_addrs.add(frm)
                            elif frm == creator and to not in exclude_set:
                                if to not in recipients:
                                    seeded_recipients += 1
                                    recipients[to] = 0
                                recipients[to] += amount_sol
                                self._save_recipient(creator, to, amount_sol, oldest_recipients_delta)

                        if oldest_funders_delta or oldest_recipients_delta or oldest_transfer_index_rows:
                            await self._flush_page_batch(
                                extraction_conn,
                                creator,
                                oldest_funders_delta,
                                oldest_recipients_delta,
                                oldest_domain_addrs,
                                [],
                                oldest_transfer_index_rows,
                            )

                        if oldest_sig:
                            print(
                                f"[REALTIME_FUNDING]    [FIRST_TX] sig={oldest_sig[:20]} ts={oldest_ts} seeded_funders={oldest_tx_seeded_funders}",
                                flush=True,
                            )
                        if oldest_tx_seeded_funders > 0:
                            page_limit_for_scan = min(page_limit_for_scan, FAST_FIRST_TX_PAGE_CAP)
                            print(
                                f"[REALTIME_FUNDING]    [FIRST_TX] Initial funding found in oldest tx; limiting first-pass scan to {page_limit_for_scan} page(s)",
                                flush=True,
                            )

                while True:
                    page_num += 1

                    # Build URL with query parameters directly
                    # Note: Helius Enhanced API max limit is 100, not 1000
                    query_url = f"{url}?api-key={_RPC_KEY}&limit=100&sort-order=desc&commitment=finalized"
                    if before_signature:
                        query_url += f"&before={before_signature}"

                    try:
                        # Log the RPC call
                        print(f"[REALTIME_FUNDING]    [PAGE {page_num}] RPC CALL #{page_num}...", flush=True)

                        start_time = time.time()
                        async with self.session.get(
                                query_url,
                                timeout=aiohttp.ClientTimeout(total=30)
                            ) as resp:
                                latency_ms = (time.time() - start_time) * 1000

                                # Record RPC metric
                                record_request(
                                    section="creator_funding",
                                    provider="helius_rpc",
                                    method="helius_enhanced_addresses_transactions",
                                    status_code=resp.status,
                                    latency_ms=latency_ms,
                                    mode="realtime",
                                    source_file="realtime_creator_funding_extractor",
                                    cache_action=cache_action,
                                    credits_saved=credits_saved,
                                )

                                if resp.status == 429:
                                    print(f"[REALTIME_FUNDING]    ⚠ Rate limited (429) on page {page_num}", flush=True)
                                    break

                                if resp.status != 200:
                                    txt = await resp.text()
                                    print(f"[REALTIME_FUNDING]    ⚠ Helius HTTP {resp.status} on page {page_num}", flush=True)
                                    break

                                page = await resp.json()
                                if not isinstance(page, list) or len(page) == 0:
                                    print(f"[REALTIME_FUNDING]    [PAGE {page_num}] No more transactions", flush=True)
                                    break

                                print(f"[REALTIME_FUNDING]    [PAGE {page_num}] fetched={len(page)} txs", flush=True)
                                total_fetched += len(page)

                                # FIX #1: Initialize per-page state for batch accumulation
                                page_funders_delta: Dict[str, dict] = {}   # addr -> {amount, is_cex, cex_exchange, ...}
                                page_recipients_delta: Dict[str, float] = {}
                                page_domain_addrs: Set[str] = {creator}
                                page_jito_events: List[tuple] = []
                                page_transfer_index_rows: List[tuple] = []  # (sig, src, dst, lamports, block_time, indexed_at)

                                # Process transactions
                                page_has_pre_migration = False
                                earliest_tx_timestamp = None
                                page_funders_found = 0
                                page_dust_filtered = 0
                                page_excluded_filtered = 0
                                page_token_transfers_filtered = 0

                                for tx in page:
                                    tx_ts = tx.get("timestamp", 0)

                                    # Track earliest timestamp on this page
                                    if earliest_tx_timestamp is None or tx_ts < earliest_tx_timestamp:
                                        earliest_tx_timestamp = tx_ts

                                    # Extract domain names from transaction description
                                    # (both explicit .sol mentions and SNS domains for addresses in descriptions)
                                    try:
                                        domain_count, domains_found = await extract_from_helius_transaction_async(tx, creator, self.domain_resolver)
                                        if domain_count > 0:
                                            print(f"[DOMAIN] 📝 Found {domain_count} domain(s) in tx {tx.get('signature', '')[:16]}... {domains_found}", flush=True)
                                    except Exception as e:
                                        print(f"[DOMAIN] ⚠ Error during domain extraction: {e}", flush=True)

                                    # Extract service names from transaction description and tag creator
                                    try:
                                        from src.utils.solscan_address_tagger import extract_service_names_from_description, tag_creator_with_services
                                        tx_description = tx.get("description", "")
                                        services = extract_service_names_from_description(tx_description)
                                        if services:
                                            tags_added = tag_creator_with_services(creator, services)
                                            if tags_added > 0:
                                                print(f"[SERVICES] 🏷️ Tagged creator with {tags_added} service(s): {', '.join(sorted(services))}", flush=True)
                                    except Exception:
                                        pass  # Service tagging is non-critical

                                    # Check for Jito tips in this transaction (using existing Helius data)
                                    # Only save as "uses_jitotip_other" if NOT the CREATE tx for current token
                                    try:
                                        tx_sig = tx.get("signature", "")
                                        if tx_sig and tx_sig != create_tx_signature:  # Skip CREATE tx
                                            tx_accounts = tx.get("accountKeys", []) or []
                                            native_transfers = tx.get("nativeTransfers", []) or []
                                            fee = tx.get("fee", 0)
                                            network_fee_sol = fee / 1e9
                                            tx_description = tx.get("description", "Unknown")  # Get Helius transaction type

                                            # Check for Jito tips via native transfers to Jito accounts
                                            for jito_addr in [
                                                '96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5',  # Jitotip 1
                                                'HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe',  # Jitotip 2
                                                'Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY',  # Jitotip 3
                                                'ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49',  # Jitotip 4
                                                'DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh',  # Jitotip 5
                                                'ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt',  # Jitotip 6
                                                'DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL',  # Jitotip 7
                                                '3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT',  # Jitotip 8
                                            ]:
                                                for transfer in native_transfers:
                                                    if transfer.get("toUserAccount") == jito_addr:
                                                        jitotip_amount = transfer.get("amount", 0) / 1e9
                                                        if jitotip_amount > 0:
                                                            total_cost_sol = network_fee_sol + jitotip_amount
                                                            tip_percentage = (jitotip_amount / total_cost_sol * 100) if total_cost_sol > 0 else 0

                                                            # FIX #1: Accumulate Jito event for batch insert
                                                            page_jito_events.append((
                                                                creator, "uses_jitotip_other", jitotip_amount,
                                                                tx_sig, None, network_fee_sol, tip_percentage, tx_description
                                                            ))
                                                            print(f"[REALTIME_FUNDING]      ✅ Jito tip ({jitotip_amount:.6f} SOL, {tip_percentage:.1f}%) detected in {tx_description} tx {tx_sig[:20]}...", flush=True)
                                                        break
                                    except Exception:
                                        pass  # Jito scanning is non-critical

                                    # Capture ALL transfers regardless of pre/post migration
                                    # (we want all funding sources, not just pre-migration)
                                    page_has_pre_migration = True
                                    found_pre_migration = True

                                    # FIX #7: Check if tx has native SOL transfers first
                                    # If nativeTransfers exist, process them even if there are token ops
                                    native = tx.get("nativeTransfers") or []
                                    if not native:
                                        # No SOL transfers - safe to skip if token ops present
                                        tx_programs = tx.get("programs") or []
                                        if PUMPFUN_PROGRAM in tx_programs:
                                            token_transfers = tx.get("tokenTransfers") or []
                                            if token_transfers:
                                                filtered_token_transfers += 1
                                                page_token_transfers_filtered += 1
                                                continue

                                        # Also check cached token ops (even non-Pump.Fun)
                                        token_transfers = tx.get("tokenTransfers") or []
                                        if token_transfers:
                                            skip_tx_for_token_ops = False
                                            for tt in token_transfers:
                                                mint = tt.get("mint")
                                                if mint and mint in self.seen_bonding_curves:
                                                    skip_tx_for_token_ops = True
                                                    break
                                                elif mint:
                                                    self.seen_bonding_curves.add(mint)

                                            if skip_tx_for_token_ops:
                                                filtered_token_transfers += 1
                                                page_token_transfers_filtered += 1
                                                continue

                                    # Check if deBridge is a signer in this transaction (ONLY if not already detected)
                                    # For cross-chain transfers, deBridge initiates but creator may not be direct signer
                                    # Skip this check if creator is already known to use deBridge
                                    if not creator_uses_debridge:
                                        tx_accounts = tx.get("accountKeys", []) or []
                                        debridge_account = "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS"

                                        if debridge_account in tx_accounts:
                                            # This transaction involves deBridge
                                            # Count it as a transfer from deBridge to creator
                                            # Note: We'll estimate a reasonable amount based on context
                                            # or mark for manual review
                                            print(f"[REALTIME_FUNDING] 🌉 DEBRIDGE TRANSACTION: {tx.get('signature', '')[:16]}...", flush=True)

                                            # Mark creator for deBridge usage
                                            # X76.3 -- managed_db_connect: this opens its own
                                            # short-lived connection separate from extraction_conn
                                            # (deliberately, to avoid nesting a second write inside
                                            # extraction_conn's still-open transaction); guarantee
                                            # its close on every exit path.
                                            try:
                                                with managed_db_connect(DB_PATH, timeout=60) as conn:
                                                    cursor = conn.cursor()
                                                    cursor.execute("""
                                                        INSERT OR REPLACE INTO creator_tags
                                                        (creator_address, tag, description)
                                                        VALUES (?, ?, ?)
                                                    """, (creator, "uses_debridge", f"Creator uses deBridge for cross-chain transfers"))
                                                    conn.commit()
                                                print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_debridge'", flush=True)
                                                # Update flag so we don't check again in this extraction run
                                                creator_uses_debridge = True
                                            except Exception as tag_err:
                                                print(f"[REALTIME_FUNDING] ⚠ Could not tag deBridge: {tag_err}", flush=True)

                                    # Process nativeTransfers (already extracted by FIX #7)
                                    tx_sig = tx.get("signature", "")
                                    for nt in native:
                                        frm = nt.get("fromUserAccount")
                                        to = nt.get("toUserAccount")
                                        amt = nt.get("amount", 0)

                                        if not isinstance(frm, str) or not isinstance(to, str):
                                            continue

                                        amount_sol = amt / 1_000_000_000

                                        # Collect for transfer_index before dust filter (use lamport int directly)
                                        if tx_sig and isinstance(amt, int) and amt > 0 and tx_ts and tx_ts > 0:
                                            page_transfer_index_rows.append((tx_sig, frm, to, amt, tx_ts, time.time()))

                                        # Filter dust
                                        if amount_sol < MIN_SOL:
                                            filtered_dust += 1
                                            page_dust_filtered += 1
                                            continue

                                        # Inbound: someone sent creator SOL
                                        if to == creator and amount_sol > 0:
                                            # Skip dust addresses (known plumbing accounts)
                                            if frm in DUST_ADDRESSES:
                                                filtered_dust += 1
                                                page_dust_filtered += 1
                                                continue

                                            if frm in exclude_set:
                                                filtered_excluded += 1
                                                page_excluded_filtered += 1
                                                continue

                                            if frm not in funders:
                                                funders[frm] = 0
                                                page_funders_found += 1
                                            funders[frm] += amount_sol
                                            # FIX #1: Accumulate instead of saving immediately
                                            self._save_funder(creator, frm, amount_sol, page_funders_delta)
                                            page_domain_addrs.add(frm)

                                        # Outbound: creator sent SOL
                                        elif frm == creator and amount_sol > 0:
                                            # Filter dust on outbound too
                                            if amount_sol < MIN_SOL:
                                                filtered_dust += 1
                                                page_dust_filtered += 1
                                                continue

                                            if to in exclude_set:
                                                filtered_excluded += 1
                                                page_excluded_filtered += 1
                                                continue

                                            if to not in recipients:
                                                recipients[to] = 0
                                            recipients[to] += amount_sol
                                            # FIX #1: Accumulate instead of saving immediately
                                            self._save_recipient(creator, to, amount_sol, page_recipients_delta)

                                # FIX #1: Flush accumulated page data in batch
                                await self._flush_page_batch(extraction_conn, creator, page_funders_delta, page_recipients_delta, page_domain_addrs, page_jito_events, page_transfer_index_rows)

                                # Log page summary
                                if page_funders_found > 0 or page_dust_filtered > 0 or page_excluded_filtered > 0 or page_token_transfers_filtered > 0:
                                    details = []
                                    if page_funders_found > 0:
                                        details.append(f"✓ {page_funders_found} new funders")
                                    if page_dust_filtered > 0:
                                        details.append(f"🚫 {page_dust_filtered} dust")
                                    if page_excluded_filtered > 0:
                                        details.append(f"🔄 {page_excluded_filtered} excluded")
                                    if page_token_transfers_filtered > 0:
                                        details.append(f"🪙 {page_token_transfers_filtered} token ops")
                                    print(f"[REALTIME_FUNDING]    [PAGE {page_num}] " + " | ".join(details), flush=True)

                                    # OPTIMIZATION: Early stopping if no inbound funding found
                                    if page_funders_found == 0:
                                        empty_inbound_pages += 1
                                    else:
                                        empty_inbound_pages = 0

                                    # Stop if we've found enough funding or hit empty pages
                                    if empty_inbound_pages >= 5 and len(funders) >= 5:
                                        print(f"[REALTIME_FUNDING] ✅ EARLY STOP: {len(funders)} funders found + {empty_inbound_pages} empty pages", flush=True)
                                        break
                                    elif len(funders) >= 50:
                                        print(f"[REALTIME_FUNDING] ✅ EARLY STOP: {len(funders)} funders found (sufficient coverage)", flush=True)
                                        break

                                # Set up next page - continue if within 1-month cutoff AND under 100 pages
                                should_continue = False
                                if page:
                                    # Check if we've reached the 1-month cutoff
                                    if earliest_tx_timestamp and earliest_tx_timestamp < one_month_cutoff:
                                        print(f"[REALTIME_FUNDING]    [PAGE {page_num}] Reached 1-month cutoff", flush=True)
                                        break

                                    # FIX #2: Check if we've reached MAX_PAGES limit
                                    if page_num >= page_limit_for_scan:
                                        print(f"[REALTIME_FUNDING]    [PAGE {page_num}] Reached {page_limit_for_scan} page limit", flush=True)
                                        break

                                    # Continue if we found pre-migration txs
                                    if page_has_pre_migration:
                                        should_continue = True
                                    # OR if the earliest tx on this page is still after migration (means older txs exist)
                                    elif earliest_tx_timestamp and earliest_tx_timestamp > migration_timestamp:
                                        should_continue = True
                                        print(f"[REALTIME_FUNDING]    [PAGE {page_num}] All post-migration, but continuing to find older txs...", flush=True)

                                    if should_continue:
                                        before_signature = page[-1].get("signature")
                                        if before_signature:
                                            await asyncio.sleep(0.5)  # Rate limit delay
                                        else:
                                            print(f"[REALTIME_FUNDING]    No more signatures available", flush=True)
                                            break
                                    else:
                                        print(f"[REALTIME_FUNDING]    Pagination complete (reached end)", flush=True)
                                        break
                                else:
                                    break

                    except asyncio.TimeoutError:
                        print(f"[REALTIME_FUNDING]    ⚠ Timeout on page {page_num}", flush=True)
                        break
                    except Exception as e:
                        print(f"[REALTIME_FUNDING]    ⚠ Error on page {page_num}: {e}", flush=True)
                        break

                    print(f"[REALTIME_FUNDING]    Total transactions fetched: {total_fetched}", flush=True)

            except Exception as e:
                print(f"[REALTIME_FUNDING]    ⚠ Error: {e}", flush=True)
                return {"creator": creator, "error": str(e)}
            
            # Summary
            total_inbound = sum(funders.values())
            total_outbound = sum(recipients.values())
            
            print(f"[REALTIME_FUNDING]    ✓ Inbound: {len(funders)} funders ({total_inbound:.2f} SOL)", flush=True)
            print(f"[REALTIME_FUNDING]    ✓ Outbound: {len(recipients)} recipients ({total_outbound:.2f} SOL)", flush=True)
            
            if filtered_dust > 0:
                print(f"[REALTIME_FUNDING]    ℹ Filtered {filtered_dust} dust transfers", flush=True)
            if filtered_excluded > 0:
                print(f"[REALTIME_FUNDING]    ℹ Filtered {filtered_excluded} internal transfers (token/curve)", flush=True)
            if filtered_token_transfers > 0:
                print(f"[REALTIME_FUNDING]    ℹ Filtered {filtered_token_transfers} token operations (swaps, migrations)", flush=True)
            
            # Show top funders
            if funders:
                sorted_funders = sorted(funders.items(), key=lambda x: x[1], reverse=True)[:3]
                for i, (funder, amount) in enumerate(sorted_funders, 1):
                    print(f"[REALTIME_FUNDING]    Funder #{i}: {funder[:16]}... → {amount:.2f} SOL", flush=True)

            # Trigger automatic CEX detection asynchronously (non-blocking)
            # This will classify new funding addresses and potentially discover new CEX wallets
            # X76.3 -- tracked via _spawn_background_task so ANY caller can
            # supervise it with wait_for_background_tasks() (see that
            # method's docstring).
            if funders:
                self._spawn_background_task(self._run_automatic_cex_detection())

            # Trigger BlockSec AML batching (caches new addresses for batch submission)
            # Rate limited to 1 batch per 2.4 hours (10 calls/day = 24/10 hours between batches)
            self._spawn_background_task(self._try_blocksec_batch())

            # Close database connection after all processing
            extraction_conn.close()

            # ✅ MARK EXTRACTION AS COMPLETE — signals to UI that extraction is done
            self._mark_extraction_complete(creator, len(funders), len(recipients), total_inbound, total_outbound)

            # Cache creator funding results (Layer 6 optimization)
            if CREATOR_CACHE is not None and funders:
                try:
                    CREATOR_CACHE.store_funders(creator, {
                        "funders": list(funders.keys()),
                        "funder_count": len(funders),
                        "total_sol": total_inbound,
                        "timestamp": int(time.time()),
                    })
                    print(f"[REALTIME_FUNDING] ✅ Cached creator funding for {creator[:16]}...", flush=True)
                except Exception as cache_err:
                    print(f"[REALTIME_FUNDING] ⚠ Could not cache creator: {cache_err}", flush=True)

            # Phase 1: Update cursor for next extraction (enables incremental fetching)
            if self.cursor_mgr and total_fetched > 0:
                try:
                    # Get the most recent signature we fetched (first in the list)
                    # We'll update cursor to start from this signature next time
                    most_recent_sig = None

                    # The first transaction fetched is the most recent
                    # We need to track what signature to start from next time
                    # For now, we track total activity for scheduling
                    self.cursor_mgr.update_cursor(creator, "v1_migration_start", total_fetched)
                    print(f"[REALTIME_FUNDING] ✅ Updated cursor for {creator[:16]}... (fetched {total_fetched} txs)", flush=True)
                except Exception as e:
                    print(f"[REALTIME_FUNDING] ⚠ Error updating cursor: {e}", flush=True)

            # 🚀 TRIGGER POST-LAUNCH AUTOMATION — networks, clustering, coordinated funder detection, UI updates
            # X76.3 -- tracked via _spawn_background_task (see that method's docstring).
            if funders:
                # Run async without blocking extraction return
                self._spawn_background_task(run_post_launch_automation(
                    creator=creator,
                    mint=self._current_mint if hasattr(self, '_current_mint') else None,
                    total_funders=len(funders),
                    total_sol=total_inbound,
                    websocket_manager=None  # Will be passed from main app if available
                ))
                print(f"[REALTIME_FUNDING] 🚀 Post-launch automation triggered", flush=True)

            return {
                "creator": creator,
                "status": "success",
                "funding_sources": len(funders),
                "total_inbound": total_inbound,
                "outgoing_transfers": len(recipients),
                "total_outbound": total_outbound,
                "cache_action": cache_action,
                "credits_saved": credits_saved,
                "funders": {k: v for k, v in sorted(funders.items(), key=lambda x: x[1], reverse=True)[:10]} if funders else {}
            }

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error: {e}", flush=True)
            return {"creator": creator, "error": str(e)}
        finally:
            # X76.3 -- guaranteed close on every path (success, the inner
            # pagination-error early return, or this function's own outer
            # exception handler above) instead of only the one success path
            # that used to call extraction_conn.close() explicitly further up.
            # close() is a documented no-op if already closed, so this is safe
            # even on the success path where the explicit close still runs first.
            if extraction_conn is not None:
                try:
                    extraction_conn.close()
                except Exception:
                    pass

    async def _run_automatic_cex_detection(self):
        """
        Run automatic CEX detection on classified funding addresses.
        
        This is called after funding extraction completes to classify any new
        addresses found in funding relationships. If high-confidence CEX wallets
        are detected, they are automatically added to the cex_wallets table.
        
        Runs non-blocking to avoid delaying token processing.
        """
        try:
            result = await classify_addresses_from_funding(max_addresses=200)
            
            if result.get("error"):
                print(f"[AUTO-CEX] Error during classification: {result.get('error')}", flush=True)
                return
            
            classified = result.get("classified", 0)
            confirmed = result.get("confirmed", 0)
            likely = result.get("likely", 0)
            total = result.get("total_analyzed", 0)
            
            if classified > 0:
                print(f"[AUTO-CEX] Classification complete: {classified} classified, {confirmed} confirmed, {likely} likely (from {total} addresses)", flush=True)
        
        except Exception as e:
            print(f"[AUTO-CEX] Error: {e}", flush=True)

    async def _try_blocksec_batch(self):
        """
        Try to submit a batch to BlockSec AML API for address labeling.
        
        Addresses are cached for batching since we're limited to 10 calls/day.
        This method:
        1. Collects new funders/recipients that haven't been labeled yet
        2. Checks if enough time has passed since last batch (2.4 hours)
        3. Submits batch if ready, or queues for next scheduled batch
        
        Runs non-blocking to avoid delaying token processing.
        """
        try:
            from src.monitoring.blocksec_aml_batcher import BlockSecAMLBatcher, auto_batch_new_addresses
            
            # Just trigger the auto-batch function
            # It will check rate limits internally and only submit if ready
            result = await auto_batch_new_addresses()
            
            if result and result.get("success"):
                print(f"[BLOCKSEC] Batch submitted: {result['count']} addresses", flush=True)
            elif result and not result.get("success"):
                # Check if it's rate limited or an actual error
                if "Rate limited" in result.get("error", ""):
                    # This is normal - just log at debug level
                    batcher = BlockSecAMLBatcher()
                    stats = batcher.get_batch_stats()
                    if stats.get("next_batch_in_minutes"):
                        print(f"[BLOCKSEC] Rate limited. Next batch in {stats['next_batch_in_minutes']} minutes", flush=True)
                else:
                    print(f"[BLOCKSEC] Batch warning: {result.get('error')}", flush=True)
        
        except ImportError:
            # BlockSec module not available, skip silently
            pass
        except Exception as e:
            print(f"[BLOCKSEC] Error during batch attempt: {e}", flush=True)

    async def check_create_tx_for_jitotip(self, creator: str, create_tx_sig: str, mint: str = None):
        """Check if CREATE transaction uses Jitotip and tag creator if so"""
        if not create_tx_sig:
            return

        try:
            # X76.3 -- managed_db_connect (guaranteed close on every exit path,
            # including exceptions raised during the RPC calls below or mid-
            # write). Execution stays on the event-loop thread exactly as
            # before: X73.2A tried moving this to a to_thread dispatch and it
            # made NestedDatabaseWriteError WORSE, for reasons never fully
            # explained (see docs/audits/x73_2a_shared_extractor_concurrency.md)
            # -- so this fix deliberately changes nothing about where or how
            # the connection is used, only that it can no longer leak.
            with managed_db_connect(DB_PATH, timeout=60) as conn:
                cursor = conn.cursor()

                # Get list of Jitotip accounts from INFRASTRUCTURE_ACCOUNTS
                jitotip_accounts = [addr for addr in INFRASTRUCTURE_ACCOUNTS.keys() if "jito" in INFRASTRUCTURE_ACCOUNTS[addr].get("name", "").lower()]

                found_jitotip = False
                jitotip_amount = 0

                # Try Helius RPC first (more reliable), then fallback to public RPC
                rpc_urls = [
                    f"https://mainnet.helius-rpc.com/?api-key={_RPC_KEY}",  # Helius first
                    "https://api.mainnet-beta.solana.com"  # Public fallback
                ]

                for rpc_url in rpc_urls:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "getTransaction",
                        "params": [create_tx_sig, {
                            "encoding": "json",
                            "maxSupportedTransactionVersion": 0
                        }]
                    }

                    try:
                        async with self.session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                                if resp.status == 200:
                                    result = await resp.json()

                                    if "result" in result and result["result"]:
                                        tx = result["result"]

                                        # Get account keys
                                        message = tx.get("transaction", {}).get("message", {})
                                        accounts = message.get('accountKeys', [])

                                        # Check if any Jitotip account is in the transaction
                                        for jito in jitotip_accounts:
                                            if jito in accounts:
                                                # Found Jitotip, check balance changes
                                                jito_idx = accounts.index(jito)
                                                meta = tx.get("meta", {})
                                                post_balances = meta.get('postBalances', [])
                                                pre_balances = meta.get('preBalances', [])

                                                if jito_idx < len(post_balances) and jito_idx < len(pre_balances):
                                                    diff = post_balances[jito_idx] - pre_balances[jito_idx]
                                                    if diff > 0:  # Jitotip received SOL
                                                        found_jitotip = True
                                                        jitotip_amount = diff / 1e9

                                                        # Calculate total tx cost (network fee + jito tip)
                                                        network_fee_lamports = tx.get("meta", {}).get("fee", 0)
                                                        network_fee_sol = network_fee_lamports / 1e9
                                                        total_cost_sol = network_fee_sol + jitotip_amount

                                                        # Calculate tip as % of total cost
                                                        tip_percentage = (jitotip_amount / total_cost_sol * 100) if total_cost_sol > 0 else 0

                                                        rpc_name = "Helius" if "helius" in rpc_url else "Public RPC"
                                                        print(f"[REALTIME_FUNDING] 🎯 JITOTIP DETECTED (via {rpc_name}) in CREATE tx: {creator[:16]}... sent {jitotip_amount:.9f} SOL ({tip_percentage:.1f}% of {total_cost_sol:.6f} SOL total cost) to {INFRASTRUCTURE_ACCOUNTS[jito].get('name', 'Jitotip')}", flush=True)
                                                        break

                                        # If found, break out of RPC loop
                                        if found_jitotip:
                                            break
                    except Exception as rpc_err:
                        # Try next RPC on error
                        continue

                # If Jitotip found, tag the creator.
                # X73.2A -- reverted to the pre-X73.2 unlocked form. A to_thread
                # + DB_WRITE_LOCK version was tried and observed still raising
                # NestedDatabaseWriteError in sustained testing (SQLite connection
                # thread-affinity vs. to_thread's executor-pool dispatch is not a
                # fully understood interaction -- see
                # docs/audits/x73_2a_shared_extractor_concurrency.md). Restoring
                # known-good behaviour rather than shipping an unproven partial
                # fix. This write path can still occasionally race under
                # concurrent callers (the same limitation it always had); it is
                # not attribution-critical (Jitotip tagging is metadata
                # enrichment, not creator_funders population) and failures here
                # are already caught and logged, never raised to the caller.
                if found_jitotip:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS creator_tags (
                            creator_address TEXT NOT NULL,
                            tag TEXT NOT NULL,
                            description TEXT,
                            amount_sol REAL,
                            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY(creator_address, tag)
                        )
                    """)

                    # Create history table to track all tip amounts per transaction
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS creator_service_history (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            creator_address TEXT NOT NULL,
                            tag TEXT NOT NULL,
                            amount_sol REAL,
                            tx_signature TEXT,
                            mint TEXT,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            network_fee_sol REAL,
                            tip_percentage REAL,
                            UNIQUE(creator_address, tag, tx_signature)
                        )
                    """)

                    # 1. Save summary in creator_tags (for UI display - shows latest/highest)
                    cursor.execute("""
                        INSERT OR REPLACE INTO creator_tags
                        (creator_address, tag, description, amount_sol)
                        VALUES (?, ?, ?, ?)
                    """, (creator, "uses_jitotip", f"Creator uses Jitotip for MEV/fee tipping in CREATE transaction", jitotip_amount))

                    # 2. Save to history table (full audit trail of all tips)
                    try:
                        cursor.execute("""
                            INSERT OR IGNORE INTO creator_service_history
                            (creator_address, tag, amount_sol, tx_signature, mint, network_fee_sol, tip_percentage, tx_type)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (creator, "uses_jitotip", jitotip_amount, create_tx_sig, mint, network_fee_sol, tip_percentage, "Create"))
                    except Exception as hist_err:
                        pass  # Ignore duplicates

                    conn.commit()
                    print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_jitotip' - Tip amount: {jitotip_amount:.6f} SOL ({tip_percentage:.1f}% of tx cost)", flush=True)

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking CREATE tx for Jitotip: {e}", flush=True)

    async def check_transfers_for_meteora(self, creator: str):
        """Check if creator has inbound/outbound transfers to/from Meteora and tag if so"""
        try:
            conn = db_connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Meteora Pool Authority
            meteora_account = "HLnpSz9h2S4hiLQ43rnSD9XkcUThA7B8hQMKmDaiTLcC"

            found_meteora = False
            meteora_amount = 0
            meteora_direction = None
            meteora_source = None

            # Check inbound (Meteora sending to creator)
            cursor.execute("""
                SELECT SUM(amount_sol) FROM creator_funders
                WHERE creator_address = ? AND funder_address = ?
            """, (creator, meteora_account))
            
            inbound_result = cursor.fetchone()
            if inbound_result and inbound_result[0]:
                found_meteora = True
                meteora_amount = inbound_result[0]
                meteora_direction = "inbound"
                meteora_source = "direct_transfer"
                print(f"[REALTIME_FUNDING] 🎯 METEORA DETECTED (inbound): {creator[:16]}... received {meteora_amount:.6f} SOL from Meteora", flush=True)

            # Check outbound (creator sending to Meteora)
            if not found_meteora:
                cursor.execute("""
                    SELECT SUM(amount_sol) FROM creator_receivers
                    WHERE creator_address = ? AND receiver_address = ?
                """, (creator, meteora_account))
                
                outbound_result = cursor.fetchone()
                if outbound_result and outbound_result[0]:
                    found_meteora = True
                    meteora_amount = outbound_result[0]
                    meteora_direction = "outbound"
                    meteora_source = "direct_transfer"
                    print(f"[REALTIME_FUNDING] 🎯 METEORA DETECTED (outbound): {creator[:16]}... sent {meteora_amount:.6f} SOL to Meteora", flush=True)

            # If Meteora found, tag the creator
            if found_meteora:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_tags (
                        creator_address TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        description TEXT,
                        amount_sol REAL,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(creator_address, tag)
                    )
                """)

                # Create history table if not exists
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS creator_service_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        creator_address TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        amount_sol REAL,
                        tx_signature TEXT,
                        mint TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE(creator_address, tag, tx_signature)
                    )
                """)

                # Save summary in creator_tags
                cursor.execute("""
                    INSERT OR REPLACE INTO creator_tags
                    (creator_address, tag, description, amount_sol)
                    VALUES (?, ?, ?, ?)
                """, (creator, "uses_meteora", f"Creator uses Meteora for {meteora_direction} transfers via {meteora_source}", meteora_amount))

                # Save to history table (each Meteora interaction)
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO creator_service_history
                        (creator_address, tag, amount_sol, tx_signature)
                        VALUES (?, ?, ?, ?)
                    """, (creator, "uses_meteora", meteora_amount, None))
                except Exception as hist_err:
                    pass

                conn.commit()
                print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_meteora' - Amount: {meteora_amount:.6f} SOL", flush=True)

            conn.close()

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking transfers for Meteora: {e}", flush=True)

    async def check_for_meteora_program_interaction(self, creator: str):
        """Check if creator has interacted with Meteora program through transaction analysis
        
        This catches Meteora swaps/interactions that don't show as direct transfers.
        Since we don't have transaction signatures stored, we'd need to parse from extraction logs.
        For now, this method is a placeholder for future enhancement.
        """
        try:
            # NOTE: Full implementation would require storing transaction signatures
            # for all creator transfers and parsing them for Meteora program interactions.
            # This is noted for future enhancement when we store tx signatures in creator_receivers.
            pass
        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking for Meteora program interaction: {e}", flush=True)

    async def check_transfers_for_debridge(self, creator: str):
        """Check if creator has inbound or outbound transfers to/from deBridge and tag if so"""
        try:
            # X76.3 -- managed_db_connect, same reasoning as check_create_tx_for_jitotip.
            with managed_db_connect(DB_PATH, timeout=60) as conn:
                cursor = conn.cursor()

                # deBridge vault
                debridge_account = "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS"

                found_debridge = False
                debridge_amount = 0
                debridge_direction = None

                # Check inbound (deBridge sending to creator)
                cursor.execute("""
                    SELECT SUM(amount_sol) FROM creator_funders
                    WHERE creator_address = ? AND funder_address = ?
                """, (creator, debridge_account))

                inbound_result = cursor.fetchone()
                if inbound_result and inbound_result[0]:
                    found_debridge = True
                    debridge_amount = inbound_result[0]
                    debridge_direction = "inbound"
                    print(f"[REALTIME_FUNDING] 🎯 DEBRIDGE DETECTED (inbound): {creator[:16]}... received {debridge_amount:.6f} SOL from deBridge", flush=True)

                # Check outbound (creator sending to deBridge)
                if not found_debridge:
                    cursor.execute("""
                        SELECT SUM(amount_sol) FROM creator_receivers
                        WHERE creator_address = ? AND receiver_address = ?
                    """, (creator, debridge_account))

                    outbound_result = cursor.fetchone()
                    if outbound_result and outbound_result[0]:
                        found_debridge = True
                        debridge_amount = outbound_result[0]
                        debridge_direction = "outbound"
                        print(f"[REALTIME_FUNDING] 🎯 DEBRIDGE DETECTED (outbound): {creator[:16]}... sent {debridge_amount:.6f} SOL to deBridge", flush=True)

                # If deBridge found, tag the creator.
                # X73.2A -- reverted to the pre-X73.2 unlocked form (same
                # unproven-pattern reasoning as check_create_tx_for_jitotip; this
                # function was never actually exercised in testing, so there is
                # no evidence either way, but it shares the identical
                # connection-lifetime shape that failed there). See
                # docs/audits/x73_2a_shared_extractor_concurrency.md.
                if found_debridge:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS creator_tags (
                            creator_address TEXT PRIMARY KEY,
                            tag TEXT,
                            description TEXT,
                            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)

                    cursor.execute("""
                        INSERT OR REPLACE INTO creator_tags
                        (creator_address, tag, description, amount_sol)
                        VALUES (?, ?, ?, ?)
                    """, (creator, "uses_debridge", f"Creator uses deBridge for {debridge_direction} transfers", debridge_amount))

                    conn.commit()
                    print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_debridge'", flush=True)

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking transfers for deBridge: {e}", flush=True)

    async def check_transfers_for_axiom(self, creator: str):
        """Check if creator has interactions with Axiom and tag if so"""
        try:
            # X76.3 -- managed_db_connect, same reasoning as check_create_tx_for_jitotip.
            with managed_db_connect(DB_PATH, timeout=60) as conn:
                cursor = conn.cursor()

                # Axiom automation account
                axiom_account = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"

                found_axiom = False
                axiom_amount = 0
                axiom_direction = None

                # Check inbound (Axiom sending to creator)
                cursor.execute("""
                    SELECT SUM(amount_sol) FROM creator_funders
                    WHERE creator_address = ? AND funder_address = ?
                """, (creator, axiom_account))

                inbound_result = cursor.fetchone()
                if inbound_result and inbound_result[0]:
                    found_axiom = True
                    axiom_amount = inbound_result[0]
                    axiom_direction = "inbound"
                    print(f"[REALTIME_FUNDING] 📊 AXIOM DETECTED (inbound): {creator[:16]}... received {axiom_amount:.6f} SOL from Axiom", flush=True)

                # Check outbound (creator sending to Axiom)
                if not found_axiom:
                    cursor.execute("""
                        SELECT SUM(amount_sol) FROM creator_receivers
                        WHERE creator_address = ? AND receiver_address = ?
                    """, (creator, axiom_account))

                    outbound_result = cursor.fetchone()
                    if outbound_result and outbound_result[0]:
                        found_axiom = True
                        axiom_amount = outbound_result[0]
                        axiom_direction = "outbound"
                        print(f"[REALTIME_FUNDING] 📊 AXIOM DETECTED (outbound): {creator[:16]}... sent {axiom_amount:.6f} SOL to Axiom", flush=True)

                # If Axiom found, tag the creator.
                # X73.2A -- reverted to the pre-X73.2 unlocked form. Same
                # unproven-pattern reasoning as check_create_tx_for_jitotip. See
                # docs/audits/x73_2a_shared_extractor_concurrency.md.
                if found_axiom:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS creator_tags (
                            creator_address TEXT PRIMARY KEY,
                            tag TEXT,
                            description TEXT,
                            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)

                    cursor.execute("""
                        INSERT OR REPLACE INTO creator_tags
                        (creator_address, tag, description, amount_sol)
                        VALUES (?, ?, ?, ?)
                    """, (creator, "uses_axiom", f"Creator uses Axiom automation/oracle services ({axiom_direction} transfers)", axiom_amount))

                    conn.commit()
                    print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_axiom'", flush=True)

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking transfers for Axiom: {e}", flush=True)

    async def check_transactions_for_meteora_programs(self, creator: str):
        """
        Check if creator's transactions call Meteora DLMM program directly.
        This detects program-level interactions in inner instructions.
        
        Meteora programs to detect:
        - dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN (DLMM)
        """
        try:
            conn = db_connect(DB_PATH, timeout=60)
            cursor = conn.cursor()

            # Check if creator is already tagged with Meteora usage
            cursor.execute("""
                SELECT 1 FROM creator_tags
                WHERE creator_address = ? AND tag = ?
            """, (creator, "uses_meteora"))
            
            if cursor.fetchone() is not None:
                print(f"[REALTIME_FUNDING]    ℹ Creator already tagged as 'uses_meteora', skipping detection", flush=True)
                conn.close()
                return

            conn.close()

            # Use Helius to get transactions and check for Meteora program calls
            meteora_dlmm = "dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN"
            
            found_meteora = False
            meteora_tx_count = 0
            
            print(f"[REALTIME_FUNDING]    🔍 Checking for Meteora DLMM program calls...", flush=True)

            try:
                url = f"https://api-mainnet.helius-rpc.com/v0/addresses/{creator}/transactions"
                query_url = f"{url}?api-key={_RPC_KEY}&limit=50&sort-order=desc&commitment=finalized"

                # First get address transactions to find signatures
                async with self.session.get(query_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status == 200:
                            address_txs = await resp.json()
                            
                            if isinstance(address_txs, list):
                                # Now fetch full details for each transaction to check inner instructions
                                signatures_to_check = [tx.get('signature') for tx in address_txs[:20] if tx.get('signature')]
                                
                                if signatures_to_check:
                                    # Fetch full transaction details
                                    tx_url = f"https://api.helius.xyz/v0/transactions?api-key={_RPC_KEY}"
                                    tx_payload = {
                                        "transactions": signatures_to_check
                                    }
                                    
                                    async with self.session.post(tx_url, json=tx_payload, timeout=aiohttp.ClientTimeout(total=30)) as tx_resp:
                                        if tx_resp.status == 200:
                                            full_txs = await tx_resp.json()
                                            
                                            if isinstance(full_txs, list):
                                                for tx in full_txs:
                                                    instructions = tx.get("instructions", []) or []
                                                    
                                                    for instr in instructions:
                                                        # Check top-level program
                                                        program_id = instr.get("programId")
                                                        if program_id == meteora_dlmm:
                                                            found_meteora = True
                                                            meteora_tx_count += 1
                                                            print(f"[REALTIME_FUNDING] 🔄 METEORA DLMM CALL DETECTED (top-level): {tx.get('signature', '')[:16]}...", flush=True)
                                                            break
                                                        
                                                        # Check inner instructions
                                                        inner_instrs = instr.get("innerInstructions", []) or []
                                                        for inner_instr in inner_instrs:
                                                            inner_prog = inner_instr.get("programId")
                                                            if inner_prog == meteora_dlmm:
                                                                found_meteora = True
                                                                meteora_tx_count += 1
                                                                print(f"[REALTIME_FUNDING] 🔄 METEORA DLMM CALL DETECTED (inner): {tx.get('signature', '')[:16]}...", flush=True)
                                                                break
                                                        
                                                        if found_meteora:
                                                            break
                                                    
                                                    if found_meteora and meteora_tx_count >= 1:
                                                        # Found at least one Meteora interaction
                                                        break

            except Exception as e:
                print(f"[REALTIME_FUNDING]    ⚠ Error checking Helius for Meteora programs: {e}", flush=True)

            # If Meteora DLMM usage found, tag the creator
            if found_meteora:
                try:
                    conn = db_connect(DB_PATH, timeout=60)
                    cursor = conn.cursor()
                    
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS creator_tags (
                            creator_address TEXT,
                            tag TEXT,
                            description TEXT,
                            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (creator_address, tag)
                        )
                    """)

                    cursor.execute("""
                        INSERT OR IGNORE INTO creator_tags
                        (creator_address, tag, description)
                        VALUES (?, ?, ?)
                    """, (creator, "uses_meteora", f"Creator uses Meteora DLMM program ({meteora_tx_count} transaction(s))"))

                    conn.commit()
                    conn.close()
                    print(f"[REALTIME_FUNDING] ✅ Tagged creator as 'uses_meteora' (program-level detection)", flush=True)
                except Exception as e:
                    print(f"[REALTIME_FUNDING] ⚠ Error tagging Meteora: {e}", flush=True)

        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error checking transactions for Meteora programs: {e}", flush=True)

    async def process_new_token(self, creator: str, migration_timestamp_str: str):
        """
        Process a newly detected token.
        Call from main listener when migration is detected.
        """
        # Ensure session is initialized
        await self.init_session()

        # Extract funding in background (don't block main listener)
        try:
            result = await self.extract_for_creator(creator, migration_timestamp_str)
            return result
        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Unexpected error: {e}", flush=True)
            return {"error": str(e)}


# Global instance
_extractor = None


async def get_extractor() -> RealTimeCreatorFundingExtractor:
    """Get or create global extractor instance"""
    global _extractor
    if not _extractor:
        _extractor = RealTimeCreatorFundingExtractor()
        await _extractor.init_session()
    return _extractor


async def extract_funding_for_new_token(creator: str, migration_timestamp_str: str, create_tx_signature: str = None, mint: str = None):
    """
    Public function to extract funding when new token detected.

    Call from pumpfun_curve_listener.py in handle_migration():
        await extract_funding_for_new_token(creator, migration_time, create_tx_sig, mint)

    RPC metrics are automatically recorded for all RPC calls in this flow.
    """
    # Skip full extraction if we already have analyzed funding data for this creator.
    # Phase 2: check creator_profile.history_status first (creator-centric cache).
    # Phase 1 fallback: COUNT(creator_funders) if profile cache is not active.
    _skip = False
    _skip_reason = None
    _cached_funders = 0
    try:
        from src.creators.migration_bridge import should_skip_legacy_extraction
        from src.creators.repository import CreatorRepository
        from src.utils.db_locking import DB_WRITE_LOCK
        _repo = CreatorRepository(DB_PATH, DB_WRITE_LOCK)
        _profile = await _repo.get_creator_profile(creator)
        if await should_skip_legacy_extraction(_profile, _repo):
            _skip = True
            _skip_reason = "creator_profile_cache"
    except Exception as _e:
        print(f"[REALTIME_FUNDING] ⚠ Profile cache check failed: {_e}", flush=True)

    if not _skip:
        # Phase 1 fallback: legacy COUNT(*) check
        try:
            import os as _os
            _db_path = _os.getenv("DB_PATH") or _os.path.join(_os.path.dirname(__file__), "../../database/flex_complete_database.db")
            # X76.3 -- managed_db_connect guarantees close() on exception.
            with managed_db_connect(_db_path, timeout=15) as _conn:
                _row = _conn.execute(
                    "SELECT COUNT(*) FROM creator_funders WHERE creator_address = ?",
                    (creator,)
                ).fetchone()
            if _row and _row[0] > 0:
                _cached_funders = _row[0]
                _skip = True
                _skip_reason = "creator_funders_count"
        except Exception as _e:
            print(f"[REALTIME_FUNDING] ⚠ Count cache check failed: {_e}", flush=True)

    if _skip:
        print(f"[REALTIME_FUNDING] ⚡ Skip extraction creator={creator[:16]}... "
              f"reason={_skip_reason} funders={_cached_funders}", flush=True)
        try:
            from src.metrics.rpc_metrics_recorder import record_cache_event
            record_cache_event(
                section="creator_funding",
                provider="helius_rpc",
                method="getSignaturesForAddress+getTransaction",
                source_file="realtime_creator_funding_extractor.py",
                cache_action="skip",
                credits_saved=100,
                optimization_layer=_skip_reason,
            )
        except Exception:
            pass
        return {"skipped": True, "reason": _skip_reason, "cached_funders": _cached_funders}

    print(f"[REALTIME_FUNDING] 📊 Recording RPC metrics for creator funding extraction: {creator[:16]}...", flush=True)
    extractor = await get_extractor()

    # Main funding scan — must complete first (populates creator_funders)
    result = await extractor.process_new_token(creator, migration_timestamp_str)

    # All remaining checks are independent — run concurrently
    async def _jitotip():
        if create_tx_signature:
            await extractor.check_create_tx_for_jitotip(creator, create_tx_signature, mint)

    async def _outgoing():
        try:
            from datetime import datetime
            migration_dt = datetime.fromisoformat(migration_timestamp_str.replace('Z', '+00:00'))
            migration_timestamp = int(migration_dt.timestamp())
            await extractor.extract_outgoing_transfers(creator, migration_timestamp)
            print(f"[REALTIME_FUNDING] ✅ Extracted outgoing transfers for {creator[:16]}...", flush=True)
        except Exception as e:
            print(f"[REALTIME_FUNDING] ⚠ Error extracting outgoing transfers: {e}", flush=True)

    await asyncio.gather(
        _jitotip(),
        extractor.check_transfers_for_debridge(creator),
        extractor.check_transfers_for_axiom(creator),
        _outgoing(),
        return_exceptions=True,
    )

    # X76.3 -- supervise this extraction's own fire-and-forget background
    # tasks (CEX detection, BlockSec batching, post-launch automation)
    # HERE, at the extractor's own public entry point, rather than relying
    # solely on creator_funding_worker's bolt-on _await_orphaned_tasks
    # sweep. This makes the "don't outlive the parent extraction job"
    # invariant hold for every caller (the worker, the listener, a future
    # one-shot recovery tool) instead of only the one caller that happened
    # to add its own supervision. The worker's own sweep is left in place
    # too -- harmless double coverage, not a conflict, since both just
    # bounded-wait the same underlying tasks (a task already awaited/done
    # here is a no-op for asyncio.wait if the worker's sweep observes it
    # again).
    await extractor.wait_for_background_tasks()

    return result


if __name__ == "__main__":
    # Test with a known creator
    async def test():
        extractor = RealTimeCreatorFundingExtractor()
        await extractor.init_session()

        # Example: Extract for a specific creator
        creator = "cwPG1BF4GqAPDF8p"  # Replace with real creator
        timestamp = "2026-01-16T17:28:51"

        result = await extractor.extract_for_creator(creator, timestamp)
        print(f"\nResult: {result}")

        await extractor.close_session()

    asyncio.run(test())
