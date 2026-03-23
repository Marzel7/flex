# Pool Pricing System Fixes
# Production-safe functions for correct bootstrap and lifecycle

import asyncio
import logging
import time
from typing import Dict, Tuple, Optional

logger = logging.getLogger(__name__)

# ============================================================================
# 1. BOOTSTRAP RESERVES AT STARTUP (Replace initialization)
# ============================================================================

async def bootstrap_pool_reserves(
    fetcher,
    pool_state,
    pools: list,
) -> Dict[Tuple[str, str], Tuple[int, int]]:
    """
    Fetch real reserves from RPC BEFORE starting WebSocket.

    Args:
        fetcher: PoolReserveFetcher instance
        pool_state: PoolStateStore singleton
        pools: List of pool dicts from database

    Returns:
        Dict of {(mint, base_account): (base_raw, quote_raw)}
    """
    if not pools:
        logger.info("[PRICE_BOOTSTRAP] No pools to bootstrap")
        return {}

    try:
        logger.info(f"[PRICE_BOOTSTRAP] Fetching reserves for {len(pools)} pools from RPC...")
        reserves_dict = await fetcher.fetch_reserves(pools)
        logger.info(f"[PRICE_BOOTSTRAP] ✅ Fetched {len(reserves_dict)} pool reserves")

        # Populate PoolStateStore with REAL reserves (not zeros)
        populated = 0
        zero_count = 0
        for pool in pools:
            mint = pool.get("mint")
            base_account = pool.get("base_account")
            quote_account = pool.get("quote_account")

            if not (mint and base_account and quote_account):
                continue

            (base_raw, quote_raw) = reserves_dict.get((mint, base_account), (0, 0))

            # Update with real values
            pool_state.update_reserve(mint, base_account, "base", base_raw)
            pool_state.update_reserve(mint, base_account, "quote", quote_raw)

            if base_raw > 0 and quote_raw > 0:
                logger.debug(f"[PRICE_BOOTSTRAP] {mint[:12]}... → base={base_raw}, quote={quote_raw}")
                populated += 1
            else:
                zero_count += 1

        logger.info(
            f"[PRICE_BOOTSTRAP] ✅ Populated {populated} pools with real reserves, "
            f"{zero_count} pools have zero liquidity (will be skipped)"
        )
        return reserves_dict

    except Exception as e:
        logger.error(f"[PRICE_BOOTSTRAP] ❌ RPC fetch failed: {e}", exc_info=True)
        # Graceful degradation: start with zeros (WebSocket will update)
        # But log loudly so we know initial state is incomplete
        return {}


# ============================================================================
# 2. FIX POOL READINESS CONDITION
# ============================================================================

def is_pool_ready(pool_state, mint: str, base_account: str) -> bool:
    """
    Check if pool has REAL usable reserves (not just non-null).

    Key fix: Only return True if both reserves > 0
    (not just "both exist")
    """
    reserves = pool_state.get_reserves(mint, base_account)
    if reserves is None:
        return False

    base_raw, quote_raw = reserves
    is_ready = base_raw > 0 and quote_raw > 0

    if not is_ready:
        logger.debug(
            f"[POOL_READY] {mint[:12]}... not ready: "
            f"base={base_raw}, quote={quote_raw}"
        )
    return is_ready


# ============================================================================
# 3. PERIODIC POOL RESYNC (Run as background task)
# ============================================================================

async def periodic_pool_resync(
    fetcher,
    pool_state,
    pools: list,
    interval_seconds: int = 180,
) -> None:
    """
    Periodically re-fetch reserves from RPC to repair any stale state.

    This guarantees that even if WebSocket is idle, pool reserves
    stay fresh and correct.

    Args:
        fetcher: PoolReserveFetcher instance
        pool_state: PoolStateStore singleton
        pools: List of pool dicts
        interval_seconds: Re-fetch interval (default 3 min)
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)

            logger.debug(f"[POOL_RESYNC] Running periodic resync ({len(pools)} pools)...")

            reserves_dict = await fetcher.fetch_reserves(pools)
            repaired_count = 0

            for pool in pools:
                mint = pool.get("mint")
                base_account = pool.get("base_account")

                if not (mint and base_account):
                    continue

                (base_raw, quote_raw) = reserves_dict.get((mint, base_account), (0, 0))

                # Check if state needs repair
                current = pool_state.get_reserves(mint, base_account)
                if current is None or current != (base_raw, quote_raw):
                    # Repair
                    pool_state.update_reserve(mint, base_account, "base", base_raw)
                    pool_state.update_reserve(mint, base_account, "quote", quote_raw)
                    repaired_count += 1
                    logger.debug(
                        f"[POOL_RESYNC] Repaired {mint[:12]}... → "
                        f"base={base_raw}, quote={quote_raw}"
                    )

            if repaired_count > 0:
                logger.info(f"[POOL_RESYNC] ✅ Repaired {repaired_count}/{len(pools)} pools")
            else:
                logger.debug(f"[POOL_RESYNC] ✅ All pools in sync")

        except Exception as e:
            logger.error(f"[POOL_RESYNC] ❌ Failed: {e}", exc_info=True)
            # Continue on error (don't crash background task)


# ============================================================================
# 4. CORRECT INITIALIZATION LIFECYCLE
# ============================================================================

async def initialize_price_system(
    price_worker,
    fetcher,
    pools: list,
) -> bool:
    """
    Correct startup sequence for price system.

    Order is critical:
    1. Load pools from database ✓
    2. Bootstrap reserves from RPC (this is the fix)
    3. Start WebSocket subscriptions
    4. Start price computation loop

    Args:
        price_worker: BackgroundPriceWorker instance
        fetcher: PoolReserveFetcher instance
        pools: List of pool dicts

    Returns:
        True if successful, False if critical failure
    """
    try:
        # Step 1: Bootstrap reserves (RPC)
        logger.info("[PRICE_SYSTEM_INIT] Step 1: Bootstrapping reserves from RPC...")
        await bootstrap_pool_reserves(fetcher, price_worker._pool_state, pools)

        # Step 2: Start WebSocket subscriptions
        logger.info("[PRICE_SYSTEM_INIT] Step 2: Starting WebSocket subscriptions...")
        if hasattr(price_worker, '_start_ws_client'):
            price_worker._start_ws_client()
            await asyncio.sleep(1)  # Give WebSocket time to connect

        # Step 3: Start price computation loop
        logger.info("[PRICE_SYSTEM_INIT] Step 3: Starting price computation loop...")
        if hasattr(price_worker, '_run_loop'):
            # Note: _run_loop is typically started in a thread, not awaited
            logger.info("[PRICE_SYSTEM_INIT] ✅ Price worker loop will start separately")

        # Step 4: Start periodic resync (background)
        logger.info("[PRICE_SYSTEM_INIT] Step 4: Starting periodic resync...")
        asyncio.create_task(periodic_pool_resync(fetcher, price_worker._pool_state, pools))

        logger.info("[PRICE_SYSTEM_INIT] ✅ Price system initialized successfully")
        return True

    except Exception as e:
        logger.error(f"[PRICE_SYSTEM_INIT] ❌ Failed to initialize: {e}", exc_info=True)
        return False


# ============================================================================
# 5. ENHANCED POOL STATE STORE (Updated methods)
# ============================================================================

class PoolStateStoreFixed:
    """
    Fixed version with correct readiness logic.

    Key changes:
    - Only mark READY if reserves > 0 (not just non-null)
    - Add debug logging for readiness transitions
    """

    def __init__(self):
        self._state: Dict[Tuple[str, str], dict] = {}
        self._lock = __import__('threading').Lock()

    def update_reserve(
        self,
        mint: str,
        base_account: str,
        account_type: str,
        raw_balance: int,
        slot: Optional[int] = None,
    ) -> bool:
        """Update reserve and check readiness (FIXED version)."""
        pool_id = (mint, base_account)

        with self._lock:
            if pool_id not in self._state:
                self._state[pool_id] = {
                    "base_reserve": None,
                    "quote_reserve": None,
                    "base_last_slot": None,
                    "quote_last_slot": None,
                    "last_update": 0,
                    "is_stale": False,
                    "was_ready": False,
                }

            # Deduplication
            slot_key = f"{account_type}_last_slot"
            if slot is not None and self._state[pool_id].get(slot_key) == slot:
                return False

            # Update reserve
            self._state[pool_id][f"{account_type}_reserve"] = raw_balance
            self._state[pool_id][slot_key] = slot
            self._state[pool_id]["last_update"] = time.time()
            self._state[pool_id]["is_stale"] = False

            # FIX: Check readiness with correct condition
            has_base = (
                self._state[pool_id]["base_reserve"] is not None
                and self._state[pool_id]["base_reserve"] > 0  # ← KEY FIX
            )
            has_quote = (
                self._state[pool_id]["quote_reserve"] is not None
                and self._state[pool_id]["quote_reserve"] > 0  # ← KEY FIX
            )
            was_ready = self._state[pool_id].get("was_ready", False)

            if has_base and has_quote and not was_ready:
                logger.info(
                    f"[POOL_STATE] ✅ READY: {mint[:8]}... "
                    f"(base={self._state[pool_id]['base_reserve']}, "
                    f"quote={self._state[pool_id]['quote_reserve']})"
                )
                self._state[pool_id]["was_ready"] = True
            elif not (has_base and has_quote) and was_ready:
                # Recovered state went back to zero (shouldn't happen)
                logger.warning(
                    f"[POOL_STATE] ⚠️  {mint[:8]}... reverted to zero liquidity"
                )
                self._state[pool_id]["was_ready"] = False

            return True

    def get_reserves(
        self, mint: str, base_account: str
    ) -> Optional[Tuple[int, int]]:
        """Get reserves if both available and not stale."""
        pool_id = (mint, base_account)
        with self._lock:
            s = self._state.get(pool_id)
            if (
                s
                and s["base_reserve"] is not None
                and s["quote_reserve"] is not None
                and not s["is_stale"]
            ):
                return (s["base_reserve"], s["quote_reserve"])
        return None


# ============================================================================
# 6. LOGGING FOR PERSISTENCE (Add to price_worker)
# ============================================================================

def log_price_persistence(mint: str, price_usd: float, source: str, base: int, quote: int):
    """
    Log when price is written to database.
    Use this in price_worker._on_price_fetched() or similar.
    """
    logger.info(
        f"[PRICE_PERSIST] mint={mint[:12]}... price=${price_usd:.6f} "
        f"source={source} reserves=(base={base},quote={quote})"
    )


# ============================================================================
# USAGE EXAMPLE (in price_worker.py)
# ============================================================================

"""
In BackgroundPriceWorker.start():

    async def start(self):
        from src.core.pool_price_engine import get_pool_fetcher, get_pool_state

        fetcher = get_pool_fetcher(self.db_path)
        pool_state = get_pool_state()

        # Load pools
        pools = fetcher.get_active_pools()

        # Initialize using correct lifecycle
        success = await initialize_price_system(self, fetcher, pools)
        if not success:
            logger.error("Failed to initialize price system")
            return

        # Rest of startup (threading, etc.)
"""
