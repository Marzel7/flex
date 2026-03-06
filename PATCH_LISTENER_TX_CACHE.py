"""
PATCH: Transaction Caching for PumpFunCurveListener

This patch adds:
1. TX cache with TTL (30 minutes) to avoid duplicate getTransaction RPC calls
2. Singleflight pattern for concurrent requests
3. Refactored extraction methods to use cached tx_data
4. Cache statistics tracking
5. Metrics exposed to UI

Expected savings: ~67% reduction in getTransaction calls (20 credits/migration)

Apply this patch by:
1. Adding cache initialization to __init__
2. Adding _get_transaction_cached() method
3. Refactoring _extract_mint_from_tx() and _extract_pool_from_tx()
4. Updating handle_migration() to use cached TX
5. Adding cache stats method
6. Adding new endpoint to main.py
"""

import time
import asyncio
from typing import Optional, Dict

# ============================================================================
# PATCH 1: Add to __init__ method
# ============================================================================
# Add these instance variables to __init__ (around line 315):

def __init__(self):
    # ... existing code ...
    self.seen_mints: Set[str] = set()
    self.detected_migrations: Set[str] = set()
    self.analyzed_tokens = {}
    self.db_lock = asyncio.Lock()
    self.websocket_connected = False
    self.websocket_msg_count = 0
    self.websocket_migration_count = 0

    # === NEW: Transaction caching ===
    self.tx_cache = {}  # {signature: (tx_data, timestamp)}
    self.tx_cache_ttl_seconds = 1800  # 30 minutes TTL
    self.tx_inflight_locks = {}  # {signature: asyncio.Lock()} for singleflight
    self.tx_cache_stats = {
        'hit': 0,
        'miss': 0,
        'wait': 0,
    }

    self._ensure_db()
    print(f"[INIT] ✅ TX Cache initialized (TTL: {self.tx_cache_ttl_seconds}s)", flush=True)
    # ... rest of existing code ...


# ============================================================================
# PATCH 2: Add _get_transaction_cached() method
# ============================================================================
# Add this method to PumpFunCurveListener class:

async def _get_transaction_cached(self, signature: str, timeout: int = 10) -> Optional[Dict]:
    """
    Fetch transaction with TTL cache + singleflight deduplication.

    Deduplicates concurrent requests for the same signature.
    Cache TTL is 30 minutes (1800 seconds).

    Returns tx_data dict from "result" field, or None if not found.

    Performance:
    - Cache hit: ~0.1ms (local lookup)
    - Cache miss: ~500ms (RPC call)
    - Wait: ~500ms (shared in-flight fetch)
    """
    current_time = time.time()

    # === Check cache hit ===
    if signature in self.tx_cache:
        cached_data, cached_time = self.tx_cache[signature]
        age = current_time - cached_time
        if age < self.tx_cache_ttl_seconds:
            self.tx_cache_stats['hit'] += 1
            print(f"[TX_CACHE] 💾 HIT: {signature[:16]}... (age: {age:.1f}s)", flush=True)
            return cached_data
        else:
            # Expired, remove from cache
            del self.tx_cache[signature]

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
            print(f"[TX_CACHE] ⏳ WAIT: {signature[:16]}... (shared fetch completed)", flush=True)
            return cached_data
        return None

    # === Cache miss: fetch it ===
    self.tx_cache_stats['miss'] += 1

    # Create lock for this signature (singleflight)
    lock = asyncio.Lock()
    self.tx_inflight_locks[signature] = lock
    await lock.acquire()

    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
        }

        print(f"[TX_CACHE] 🌐 MISS: fetching {signature[:16]}...", flush=True)
        tx_data = await asyncio.wait_for(
            self._post_rpc_with_fallback(payload),
            timeout=timeout
        )

        if tx_data and "result" in tx_data and tx_data["result"]:
            result = tx_data["result"]
            # Cache it
            self.tx_cache[signature] = (result, current_time)
            print(f"[TX_CACHE] 💾 CACHED: {signature[:16]}... ({len(str(result))} bytes)", flush=True)
            return result

        return None

    except asyncio.TimeoutError:
        print(f"[TX_CACHE] ⏱️  Timeout fetching {signature[:16]}...", flush=True)
        return None

    except Exception as e:
        print(f"[TX_CACHE] ⚠ Error fetching {signature[:16]}...: {e}", flush=True)
        return None

    finally:
        # Release lock for other waiters
        lock.release()
        del self.tx_inflight_locks[signature]


# ============================================================================
# PATCH 3: Add refactored extraction methods (no RPC calls)
# ============================================================================
# Replace or add these methods:

async def _extract_mint_from_tx(self, tx_data: Dict) -> Optional[str]:
    """
    Extract token mint from transaction data (no RPC call needed).

    Strategies:
    1. Try postTokenBalances first (most reliable)
    2. Fall back to accountKeys if postTokenBalances missing
    3. Filter out system programs

    Takes cached tx_data as input (no async calls).
    """
    if not tx_data:
        return None

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

    return None


async def _extract_pool_from_tx(self, tx_data: Dict) -> Optional[str]:
    """
    Extract PumpSwap pool address from transaction data (no RPC call needed).

    The pool is the account that is OWNED BY the PumpSwap program.

    Strategy:
    1. Look through all accounts in innerInstructions
    2. Find accounts used by the PumpSwap program
    3. Return the first writable PDA (index 0 of PumpSwap instruction accounts)

    Takes cached tx_data as input (no async calls).
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

            # Check if this instruction is calling PumpSwap
            if program_id_idx == pumpswap_idx:
                accounts = ix.get("accounts", [])
                if accounts and len(accounts) > 0:
                    # The first account in a PumpSwap instruction is typically the pool
                    pool_idx = accounts[0]
                    if isinstance(pool_idx, int) and pool_idx < len(account_keys):
                        pool_address = account_keys[pool_idx]
                        print(f"[POOL] ✅ Extracted pool from cached tx: {pool_address}", flush=True)
                        return pool_address

    return None


# ============================================================================
# PATCH 4: Add cache stats method
# ============================================================================
# Add this method to PumpFunCurveListener class:

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
        'rpc_calls_avoided': self.tx_cache_stats['hit'],  # Each hit = 1 RPC call avoided
        'credits_saved': self.tx_cache_stats['hit'] * 10,  # Each getTransaction = 10 credits
    }


# ============================================================================
# PATCH 5: Update handle_migration() to use cached TX
# ============================================================================
# Replace the handle_migration method (starting around line 1789):
# Key changes:
# 1. Call _get_transaction_cached() once at the top
# 2. Use _extract_mint_from_tx() instead of _fetch_mint_from_transaction()
# 3. Use _extract_pool_from_tx() instead of _extract_pool_from_migration_tx()
# 4. Extract blockTime from cached tx_data
# 5. Keep rest of logic the same

async def handle_migration(self, signature: str, logs: list):
    """
    Process detected migration.

    KEY OPTIMIZATION: Fetch TX once, reuse for mint, pool, blockTime.
    This reduces RPC calls from ~3 per migration to ~1.
    """
    try:
        # Skip if already processing this signature
        if signature in self.detected_migrations:
            return

        self.detected_migrations.add(signature)

        # === CRITICAL OPTIMIZATION: Cache TX fetch ===
        # Fetch TX once and reuse for mint, pool, blockTime extraction
        # This is the biggest single RPC savings opportunity
        tx_data = await self._get_transaction_cached(signature)

        if tx_data:
            # Extract from cached tx_data (no RPC calls!)
            mint = await self._extract_mint_from_tx(tx_data)
        else:
            # Fallback: None available
            mint = None

        if not mint:
            print(f"[MIGRATION] ⚠ Failed to extract mint from cached tx, trying logs fallback", flush=True)
            mint = self._extract_mint_from_logs(logs)

        if not mint:
            print(f"[MIGRATION] ⚠ Could not extract mint from {signature} - SKIPPED", flush=True)
            return

        # Skip if already analyzed
        if self._token_exists_in_db(mint):
            print(f"[MIGRATION] ⏭️  Token {mint} already analyzed - SKIPPED", flush=True)
            return

        self.seen_mints.add(mint)
        print(f"[EVENT] 🚀 MIGRATION DETECTED: {mint}", flush=True)
        print(f"[EVENT] Migration signature: {signature}", flush=True)

        # Create minimal token entry immediately
        await self._create_minimal_token_entry(mint)

        # === Extract pool from cached tx (no RPC call!) ===
        pool_address = None
        if tx_data:
            pool_address = await self._extract_pool_from_tx(tx_data)
            if pool_address:
                print(f"[EVENT] ✅ Pool extracted from cached tx: {pool_address}", flush=True)

        # === Extract blockTime from cached tx ===
        created_at = None
        if tx_data:
            block_time = tx_data.get("blockTime")
            if block_time:
                created_at = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                print(f"[CREATOR] 🕐 Using blockTime from cached tx: {created_at}", flush=True)

        # If we don't have created_at yet, try provenance flow (existing code)
        if not created_at:
            earliest_creator = None
            created_at = None
            analyzer = None
            try:
                from pump_fun_post_migration_analyzer import PostMigrationAnalyzer
                analyzer = PostMigrationAnalyzer(mint, rpc_url=RPC_HTTP)
                provenance = await analyzer.get_creator_from_earliest_tx()
                earliest_creator = provenance.get('creator') if provenance else None

                # Prefer on-chain blockTime from provenance over migration tx blockTime
                if provenance and provenance.get('blockTime'):
                    block_time = provenance.get('blockTime')
                    created_at = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                    print(f"[CREATOR] 🕐 Using on-chain time from earliest tx: {created_at}", flush=True)

                # Fallback: Get migration block time if provenance doesn't have blockTime
                if not created_at and signature:
                    try:
                        # Use cached tx if available (no RPC call!)
                        if tx_data:
                            block_time = tx_data.get("blockTime")
                            if block_time:
                                created_at = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                        else:
                            # Fallback to old RPC logic (shouldn't reach here if cache works)
                            payload = {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTransaction",
                                "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
                            }
                            tx_fallback = await self._post_rpc_with_fallback(payload, timeout=10)
                            if tx_fallback and tx_fallback.get("result"):
                                block_time = tx_fallback["result"].get("blockTime")
                                if block_time:
                                    created_at = datetime.utcfromtimestamp(block_time).isoformat() + "Z"
                    except Exception as ts_err:
                        pass

                created_at = created_at or (datetime.utcnow().isoformat() + "Z")

                # Rest of existing logic continues...
                if earliest_creator:
                    # ... existing provenance code ...
                    pass
            except Exception as creator_err:
                print(f"[CREATOR] ⚠ Could not extract creator: {creator_err}", flush=True)

        # Trigger immediate price fetch
        try:
            result = await self._extract_price_from_transaction(signature, mint)
            if result is not None:
                price, market_cap, source = result
                await self._update_price_in_db(mint, price, market_cap, source)
                print(f"[PRICE] ✅ Initial price fetched: ${price:.2e} | Market Cap: ${market_cap:.2e} | Source: {source}", flush=True)
        except Exception as price_err:
            print(f"[PRICE] ⚠ Initial price fetch failed: {price_err}", flush=True)

        # ... rest of handle_migration continues as before (analyze_post_migration, background tasks, etc.) ...

    except Exception as e:
        print(f"[MIGRATION] ⚠ Error handling migration: {e}", flush=True)
        import traceback
        traceback.print_exc()


# ============================================================================
# PATCH 6: Add to main.py (Flask app)
# ============================================================================
# Add this route to expose cache metrics:

@app.route('/api/listener/tx-cache-stats')
def listener_tx_cache_stats():
    """
    Expose listener transaction cache statistics to UI.

    Returns:
    {
        "tx_cache_hit": 123,
        "tx_cache_miss": 45,
        "tx_cache_wait": 12,
        "tx_cache_size": 50,
        "tx_cache_hit_rate_pct": 73.2,
        "rpc_calls_avoided": 123,
        "credits_saved": 1230
    }
    """
    try:
        # Get listener instance (you may need to refactor)
        # For now, return placeholder - you'll need to:
        # 1. Store listener as global in main.py, OR
        # 2. Pass cache stats to shared metrics system
        listener_instance = globals().get('listener')  # if available
        if listener_instance:
            stats = listener_instance.get_tx_cache_stats()
            return jsonify(stats)

        return jsonify({
            "tx_cache_hit": 0,
            "tx_cache_miss": 0,
            "tx_cache_wait": 0,
            "tx_cache_size": 0,
            "tx_cache_hit_rate_pct": 0.0,
            "rpc_calls_avoided": 0,
            "credits_saved": 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# INTEGRATION NOTES
# ============================================================================
"""
1. The _fetch_mint_from_transaction() method can be left as-is (legacy),
   but handle_migration() will use _extract_mint_from_tx() for cached data.

2. The _extract_pool_from_migration_tx() method can be left as-is (legacy),
   but handle_migration() will use _extract_pool_from_tx() for cached data.

3. Cache is thread-safe via asyncio.Lock() for singleflight.
   No additional locking needed since all access is in one event loop.

4. TTL of 30 minutes assumes migrations are spaced >30 min apart.
   If migrations for the same TX happen >30 min apart, cache expires
   (acceptable trade-off for memory efficiency).

5. Monitor logs for "[TX_CACHE]" messages:
   - "💾 HIT" = cache hit (RPC avoided)
   - "🌐 MISS" = cache miss (RPC call made)
   - "⏳ WAIT" = concurrent request sharing in-flight fetch

6. Expected improvement:
   - Before: 30 credits per migration (3 getTransaction @ 10 credits each)
   - After: 10 credits per migration (1 getTransaction @ 10 credits)
   - Savings: 67% reduction in getTransaction calls

7. Dashboard integration:
   - Add card showing "RPC Calls Avoided" and "Hit Rate"
   - Update every 5-10 seconds from /api/listener/tx-cache-stats
"""
