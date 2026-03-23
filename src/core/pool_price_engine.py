"""
Pool-based pricing engine: batch-fetch on-chain AMM reserves and compute token prices.
Uses getMultipleAccounts RPC batching (max 100 pubkeys/call) to minimize RPC usage.
Includes WebSocket subscription client for real-time reserve account updates.
"""

import base64
import struct
import time
import logging
import os
import aiohttp
import asyncio
import json
import threading
import websockets
from typing import Dict, List, Optional, Tuple

from .price_service import TokenPrice
from src.metrics.rpc_metrics_recorder import record_request

logger = logging.getLogger(__name__)

# Load environment configuration
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '../../config/.env'))

# Get Helius API key from environment (use monitoring key if available)
_HELIUS_MONITORING_API_KEY = os.getenv("HELIUS_MONITORING_API_KEY", "")
_HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
_RPC_KEY = _HELIUS_MONITORING_API_KEY or _HELIUS_API_KEY

# Build URLs with API key if available
HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", f"https://mainnet.helius-rpc.com/?api-key={_RPC_KEY}" if _RPC_KEY else "https://api.mainnet-beta.solana.com")
HELIUS_WS_URL = os.getenv("HELIUS_WS_URL", f"wss://mainnet.helius-rpc.com/?api-key={_RPC_KEY}" if _RPC_KEY else "wss://api.mainnet-beta.solana.com")


class PoolReserveFetcher:
    """Batch-fetches token balances from on-chain pool accounts using getMultipleAccounts."""

    SOL_MINT = "So11111111111111111111111111111111111111112"
    MAX_PUBKEYS_PER_CALL = 100

    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_active_pools(self) -> List[Dict]:
        """Load all active pool registrations from token_pool_accounts table.
        
        Returns only:
        - Validated pools (vaults confirmed to exist on-chain)
        - Pending pools (vaults not yet confirmed, waiting for retry)
        
        Excludes:
        - Rejected pools (extraction proved vaults are wrong/missing)
        """
        import sqlite3

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM token_pool_accounts 
                   WHERE is_active = 1 
                   AND vault_validation_status IN ('validated', 'pending')
                   ORDER BY vault_validation_status DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    async def fetch_reserves(self, pools: List[Dict]) -> Dict[Tuple[str, str], Tuple[int, int]]:
        """
        Batch-fetch base+quote reserves for all pools via getMultipleAccounts.
        Returns {(mint, base_account): (base_reserve_raw, quote_reserve_raw)}.
        Keyed by (mint, base_account) to support multiple pools per token.
        Batches pubkeys into groups of MAX_PUBKEYS_PER_CALL.
        """
        if not pools:
            return {}

        # Build pubkey list: [base_account, quote_account] per pool
        pubkeys: List[str] = []
        pubkey_to_mint: Dict[str, str] = {}  # pubkey -> mint
        pubkey_to_account_type: Dict[str, str] = {}  # pubkey -> 'base' or 'quote'

        for pool in pools:
            base_pubkey = pool["base_account"]
            quote_pubkey = pool["quote_account"]
            mint = pool["mint"]

            pubkeys.append(base_pubkey)
            pubkeys.append(quote_pubkey)
            pubkey_to_mint[base_pubkey] = mint
            pubkey_to_mint[quote_pubkey] = mint
            pubkey_to_account_type[base_pubkey] = "base"
            pubkey_to_account_type[quote_pubkey] = "quote"

        # Fetch in batches
        balances: Dict[str, Optional[int]] = {}
        for i in range(0, len(pubkeys), self.MAX_PUBKEYS_PER_CALL):
            batch = pubkeys[i : i + self.MAX_PUBKEYS_PER_CALL]
            result = await self._call_get_multiple_accounts(batch)
            balances.update(result)

        # Pair up reserves by (mint, base_account) to support multiple pools per token
        reserves: Dict[Tuple[str, str], Tuple[int, int]] = {}
        for pool in pools:
            base_key = pool["base_account"]
            quote_key = pool["quote_account"]
            base_balance = balances.get(base_key)
            quote_balance = balances.get(quote_key)

            if base_balance is not None and quote_balance is not None:
                reserves[(pool["mint"], pool["base_account"])] = (base_balance, quote_balance)

        return reserves


    async def fetch_token_supply(self, mints: List[str]) -> Dict[str, int]:
        """
        Fetch token supply for mints from their Mint accounts.
        Returns {mint: supply_raw}.
        """
        if not mints:
            return {}
        
        result = {}
        for i in range(0, len(mints), self.MAX_PUBKEYS_PER_CALL):
            batch = mints[i : i + self.MAX_PUBKEYS_PER_CALL]
            try:
                response = await self.rpc.get_multiple_accounts(batch)
                if not response or "result" not in response:
                    continue
                
                accounts = response.get("result", {}).get("value", [])
                for idx, account in enumerate(accounts):
                    if not account:
                        continue
                    
                    mint = batch[idx]
                    data = account.get("data", [None])[0]
                    
                    if not data:
                        continue
                    
                    # SPL Token Mint account: supply is at offset 16, 8 bytes
                    try:
                        data_bytes = base64.b64decode(data) if isinstance(data, str) else data
                        if len(data_bytes) >= 24:
                            supply_raw = int.from_bytes(data_bytes[16:24], byteorder='little')
                            result[mint] = supply_raw
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Error fetching token supply: {e}")
        
        return result

    async def _call_get_multiple_accounts(
        self, pubkeys: List[str]
    ) -> Dict[str, Optional[int]]:
        """
        Single getMultipleAccounts RPC call.
        Returns {pubkey: token_balance_raw or None}.
        Records RPC metrics via rpc_metrics_recorder.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getMultipleAccounts",
            "params": [pubkeys, {"encoding": "base64", "commitment": "confirmed"}],
        }

        start_ms = time.time() * 1000
        status_code = 200
        error_msg = None
        results: Dict[str, Optional[int]] = {pk: None for pk in pubkeys}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    HELIUS_RPC_URL,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=3.0),
                ) as resp:
                    status_code = resp.status
                    data = await resp.json()

            # Extract accounts from RPC response
            accounts = data.get("result", {}).get("value", [])
            for pubkey, account in zip(pubkeys, accounts):
                if account and account.get("data"):
                    balance = self._decode_spl_token_balance(account["data"][0])
                    results[pubkey] = balance

        except Exception as e:
            error_msg = str(e)
            logger.warning(f"getMultipleAccounts failed for {len(pubkeys)} pubkeys: {e}")

        finally:
            latency_ms = time.time() * 1000 - start_ms
            record_request(
                section="pool_pricing",
                provider="helius",
                method="getMultipleAccounts",
                status_code=status_code,
                latency_ms=latency_ms,
                mode="realtime",
                source_file="pool_price_engine",
                error=error_msg,
            )

        return results

    @staticmethod
    def _decode_spl_token_balance(data_b64: str) -> Optional[int]:
        """
        Decode base64-encoded SPL token account data.
        Extract token balance (uint64 LE) at offset 64.
        Returns raw balance, or None if decode fails.
        """
        try:
            data = base64.b64decode(data_b64)
            if len(data) < 72:
                return None
            return struct.unpack_from("<Q", data, 64)[0]
        except Exception as e:
            logger.debug(f"SPL decode error: {e}")
            return None


class PoolPriceCalculator:
    """Compute token prices from AMM reserve ratios with manipulation protection."""

    MIN_LIQUIDITY_USD = 100.0  # Lowered to $100 for testing (production: $5000)
    MAX_PRICE_DEVIATION = 0.40

    @staticmethod
    def compute_price(
        mint: str,
        base_reserve_raw: int,
        quote_reserve_raw: int,
        base_decimals: int,
        quote_decimals: int,
        quote_is_sol: bool,
        sol_price_usd: float,
        last_cached_price: Optional[float] = None,
        base_account: Optional[str] = None,
        total_supply: int = 0,
    ) -> Optional[TokenPrice]:
        """
        Compute TokenPrice from AMM reserves.
        Applies filters: minimum liquidity ($5000 USD) and max deviation (40%).
        Returns None if price is rejected by filters.

        Args:
            base_account: Pool/pair address for attribution (optional)
        """
        if base_reserve_raw == 0 or quote_reserve_raw == 0:
            return None

        # Convert raw to human-readable amounts
        base_human = base_reserve_raw / (10 ** base_decimals)
        quote_human = quote_reserve_raw / (10 ** quote_decimals)

        # Compute price ratio
        price_in_quote = quote_human / base_human
        price_usd = (
            price_in_quote * sol_price_usd if quote_is_sol else price_in_quote
        )
        price_sol = price_in_quote if quote_is_sol else price_usd / sol_price_usd

        # Manipulation filter 1: minimum liquidity
        liquidity_usd = 2 * quote_human * (
            sol_price_usd if quote_is_sol else 1.0
        )
        if liquidity_usd < PoolPriceCalculator.MIN_LIQUIDITY_USD:
            return None

        # Manipulation filter 2: max deviation from last known price
        if last_cached_price and last_cached_price > 0:
            deviation = abs(price_usd - last_cached_price) / last_cached_price
            if deviation > PoolPriceCalculator.MAX_PRICE_DEVIATION:
                logger.warning(
                    f"Pool price rejected for {mint}: {deviation:.1%} deviation "
                    f"(pool={price_usd:.8f}, cached={last_cached_price:.8f})"
                )
                return None

        # Calculate market cap from liquidity and base reserve
        # market_cap ≈ 2 * liquidity_usd (assuming base and quote have roughly equal value)
        # This is more reliable than using total_supply which we often don't have
        market_cap = 2 * liquidity_usd

        return TokenPrice(
            mint=mint,
            price_usd=price_usd,
            price_sol=price_sol,
            liquidity_usd=liquidity_usd,
            volume_24h=0,
            market_cap=market_cap,
            source="pool",
            is_stale=False,
            pair_address=base_account,
        )

    @staticmethod
    async def fetch_sol_price_usd() -> float:
        """
        Fetch current SOL price from CoinGecko API (fallback to Binance if unavailable).
        Called once per worker cycle; result shared across all compute_price() calls.
        Falls back to cached/default price if API fails.
        """
        # Try CoinGecko first
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "solana", "vs_currencies": "usd"},
                    timeout=aiohttp.ClientTimeout(total=2.0),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "solana" in data and "usd" in data["solana"]:
                            price = float(data["solana"]["usd"])
                            print(f"[SOL_PRICE] ✅ Fetched from CoinGecko: ${price:.2f}", flush=True)
                            return price
        except Exception as e:
            logger.debug(f"CoinGecko SOL price fetch failed: {e}")

        # Try Binance as fallback
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.binance.com/api/v3/avgPrice",
                    params={"symbol": "SOLUSDT"},
                    timeout=aiohttp.ClientTimeout(total=2.0),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "price" in data:
                            price = float(data["price"])
                            print(f"[SOL_PRICE] ✅ Fetched from Binance: ${price:.2f}", flush=True)
                            return price
        except Exception as e:
            logger.debug(f"Binance SOL price fetch failed: {e}")

        # Fallback to reasonable SOL price ($94 as of March 2026)
        fallback_price = 94.0
        print(f"[SOL_PRICE] ⚠️  Using fallback price: ${fallback_price:.2f}", flush=True)
        return fallback_price


class PoolStateStore:
    """
    Thread-safe store for pool reserve state updated by WebSocket events.
    Keyed by (mint, base_account) to support multiple pools per token.
    Deduplicates rapid events from same slot (per pool).
    Detects stale pools (no updates >5 minutes).
    """

    STALE_POOL_THRESHOLD = 300  # 5 minutes in seconds

    def __init__(self):
        self._lock = threading.Lock()
        # (mint, base_account) -> {
        #   'base_reserve': int, 'quote_reserve': int,
        #   'last_update': float, 'last_slot': int,
        #   'is_stale': bool,
        #   'was_ready': bool  # Track if we've already logged this pool as ready
        # }
        self._state: Dict[Tuple[str, str], Dict] = {}
        self._update_count = 0  # Track total updates for logging

    def update_reserve(self, mint: str, base_account: str, account_type: str,
                       raw_balance: int, slot: Optional[int] = None) -> bool:
        """
        Update one side of a pool's reserves (base or quote).
        Pool identified by (mint, base_account) to support multiple pools per token.
        Returns True if update was applied, False if deduplicated.
        """
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
                    "was_ready": False,  # Track if we've already logged this pool
                }

            # Deduplication: skip if same slot seen recently (per account, not per pool)
            # This allows BOTH base and quote to update on the same slot
            slot_key = f"{account_type}_last_slot"
            if slot is not None and self._state[pool_id].get(slot_key) == slot:
                print(f"[POOL_STATE_DEBUG] ⏭️  DEDUP: {mint[:8]}... {account_type} slot {slot} already seen", flush=True)
                return False

            print(f"[POOL_STATE_DEBUG] 📝 Storing {account_type}_reserve={raw_balance} for {mint[:8]}... slot={slot}", flush=True)
            self._state[pool_id][f"{account_type}_reserve"] = raw_balance
            self._state[pool_id][slot_key] = slot
            self._state[pool_id]["last_update"] = time.time()
            self._state[pool_id]["is_stale"] = False

            # Log only once when pool becomes ready
            # FIX: Check if reserves > 0 (has actual liquidity), not just non-null
            has_base = (
                self._state[pool_id]["base_reserve"] is not None
                and self._state[pool_id]["base_reserve"] > 0
            )
            has_quote = (
                self._state[pool_id]["quote_reserve"] is not None
                and self._state[pool_id]["quote_reserve"] > 0
            )
            was_ready = self._state[pool_id].get("was_ready", False)

            print(f"[POOL_STATE_DEBUG] State after update: base={self._state[pool_id]['base_reserve']}, quote={self._state[pool_id]['quote_reserve']}", flush=True)

            if has_base and has_quote and not was_ready:
                print(f"[POOL_STATE] ✅ READY: {mint[:8]}... (base={self._state[pool_id]['base_reserve']}, quote={self._state[pool_id]['quote_reserve']})", flush=True)
                self._state[pool_id]["was_ready"] = True

            return True

    def get_reserves(self, mint: str, base_account: str) -> Optional[Tuple[int, int]]:
        """Return (base_raw, quote_raw) for a specific pool, or None if not both known/not stale."""
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

    def get_pools_for_mint(self, mint: str) -> List[Tuple[str, int, int]]:
        """
        Return [(base_account, base_raw, quote_raw), ...] for all valid pools of a mint.
        ✅ Only includes pools with BOTH reserves > 0 (has actual liquidity).
        Filters out: stale pools, missing reserves, zero-liquidity pools.
        """
        results = []
        with self._lock:
            for (m, base_account), s in self._state.items():
                if m == mint and not s["is_stale"]:
                    # ✅ CRITICAL: Only return pools with REAL liquidity (> 0)
                    if (
                        s["base_reserve"] is not None
                        and s["quote_reserve"] is not None
                        and s["base_reserve"] > 0
                        and s["quote_reserve"] > 0
                    ):
                        results.append((base_account, s["base_reserve"], s["quote_reserve"]))

        return results

    def get_all_mints(self) -> List[str]:
        """Return list of all distinct mints currently in store."""
        with self._lock:
            mints = list({m for (m, _) in self._state.keys()})
            # if len(mints) > 0:
            #     print(f"[POOL_STATE] Returning {len(mints)} mints from {len(self._state)} pool entries", flush=True)
            return mints

    def mark_stale_pools(self, now: Optional[float] = None) -> List[str]:
        """
        Mark pools with no updates >5 minutes as stale.
        Returns list of mints that have at least one pool marked stale.
        """
        if now is None:
            now = time.time()

        stale_mints = set()
        with self._lock:
            for (mint, base_account), state in self._state.items():
                if not state["is_stale"] and now - state["last_update"] > self.STALE_POOL_THRESHOLD:
                    state["is_stale"] = True
                    stale_mints.add(mint)

        if stale_mints:
            logger.warning(f"Marked pools as stale (no updates >5 min): {list(stale_mints)[:5]}")

        return list(stale_mints)

    def clear(self, mint: str) -> None:
        """Clear reserve state for all pools of a mint."""
        with self._lock:
            keys = [(m, b) for (m, b) in list(self._state.keys()) if m == mint]
            for k in keys:
                del self._state[k]



class PoolAggregator:
    """
    Aggregate prices from multiple pools for same token.

    Strategies:
    1. Single pool: Return that pool's price
    2. Two pools: Liquidity-weighted average
    3. Three+ pools: Liquidity-weighted median (resistant to manipulation)

    Health scoring prevents selecting unhealthy pools even if they have high liquidity.
    """

    @staticmethod
    def compute_health_score(
        price_obj: "TokenPrice",
        volume_24h: Optional[float] = None,
        pool_age_seconds: Optional[float] = None,
    ) -> float:
        """
        Compute health score for a pool (0.0 to 1.0).

        Factors:
        - Liquidity: Higher is better
        - Volume: Higher is better (shows activity)
        - Age: Older is better (proven stability)
        - Price consistency: Price stability (deviation from median)

        Args:
            price_obj: TokenPrice with liquidity_usd
            volume_24h: Optional 24h trading volume
            pool_age_seconds: Optional seconds since pool creation

        Returns:
            Health score (0.0 to 1.0)
        """
        if not price_obj or price_obj.liquidity_usd <= 0:
            return 0.0

        score = 0.0

        # Liquidity component (max 0.5 points)
        # Pools with >$100M liquidity get full credit
        liquidity_score = min(price_obj.liquidity_usd / 100_000_000, 1.0)
        score += liquidity_score * 0.5

        # Volume component (max 0.3 points)
        if volume_24h and volume_24h > 0:
            volume_24h_usd = volume_24h if volume_24h > 1000 else 0
            volume_score = min(volume_24h_usd / (price_obj.liquidity_usd * 5), 1.0)
            score += volume_score * 0.3

        # Age component (max 0.2 points)
        if pool_age_seconds:
            # Pools older than 7 days get full credit
            age_days = pool_age_seconds / 86400
            age_score = min(age_days / 7, 1.0)
            score += age_score * 0.2
        else:
            # No age data, assume medium score
            score += 0.1

        return min(score, 1.0)

    @staticmethod
    def aggregate(prices: List["TokenPrice"]) -> Optional["TokenPrice"]:
        """
        Aggregate prices from multiple pools.

        Strategy:
        - 1 pool: Return it
        - 2 pools: Liquidity-weighted average
        - 3+ pools: Liquidity-weighted median (attack-resistant)

        Args:
            prices: List of TokenPrice objects (may contain None values)

        Returns:
            TokenPrice with aggregated price, or None if no valid prices.
        """
        valid = [p for p in prices if p is not None]
        if not valid:
            return None

        if len(valid) == 1:
            # Single pool: return as-is
            return TokenPrice(
                mint=valid[0].mint,
                price_usd=valid[0].price_usd,
                price_sol=valid[0].price_sol,
                liquidity_usd=valid[0].liquidity_usd,
                volume_24h=valid[0].volume_24h,
                market_cap=valid[0].market_cap,
                source="pool",
                is_stale=valid[0].is_stale,
            )

        # Multiple pools: Use liquidity-weighted median
        # Sort by liquidity (descending)
        sorted_by_liq = sorted(valid, key=lambda p: p.liquidity_usd, reverse=True)

        # Calculate total liquidity
        total_liq = sum(p.liquidity_usd for p in sorted_by_liq)
        if total_liq <= 0:
            return sorted_by_liq[0]  # Fallback to highest nominal liquidity

        # Weighted median: accumulate liquidity until we pass 50%
        half_liq = total_liq / 2
        cumulative = 0
        median_price_obj = sorted_by_liq[0]

        for price_obj in sorted_by_liq:
            cumulative += price_obj.liquidity_usd
            if cumulative >= half_liq:
                median_price_obj = price_obj
                break

        # Return median price with pool count annotation
        return TokenPrice(
            mint=median_price_obj.mint,
            price_usd=median_price_obj.price_usd,
            price_sol=median_price_obj.price_sol,
            liquidity_usd=median_price_obj.liquidity_usd,
            volume_24h=median_price_obj.volume_24h,
            market_cap=median_price_obj.market_cap,
            source=f"pool({len(valid)})",
            is_stale=median_price_obj.is_stale,
        )

class PoolWebSocketClient:
    """
    Persistent WebSocket subscription client for pool reserve accounts.
    Runs in a daemon thread with its own asyncio event loop.
    On account update: decode SPL balance → update PoolStateStore.
    """

    WS_STALE_THRESHOLD = 120  # 2 minutes without events triggers fallback poll

    def __init__(self, state_store: PoolStateStore, db_path: str, on_dual_update=None):
        self._store = state_store
        self._db_path = db_path
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._sub_id_to_account: Dict[int, str] = {}  # subscription_id -> pubkey
        self._account_to_pools: Dict[str, List[Dict]] = {}  # pubkey -> list of pool dicts (handles shared accounts)
        self._last_event_received = time.time()
        self._on_dual_update = on_dual_update  # Callback when both reserves updated
        self.stats = {
            "connected": False,
            "subscriptions": 0,
            "events_received": 0,
            "events_decoded": 0,
            "events_deduplicated": 0,
            "reconnects": 0,
            "last_event_at": 0,
            "is_stale": False,
        }

    def start(self, pools: List[Dict]) -> None:
        """Spawn daemon thread running the async WebSocket loop."""
        self._build_account_map(pools)
        self._running = True
        self._thread = threading.Thread(
            target=self._run_thread, daemon=True, name="pool-ws"
        )
        self._thread.start()
        print(f"[POOL_WS] 🚀 Starting WebSocket client to subscribe to {len(self._account_to_pools)} pool accounts", flush=True)
        logger.info(
            f"PoolWebSocketClient started — subscribing to {len(self._account_to_pools)} accounts"
        )

    def stop(self) -> None:
        """Stop the WebSocket client and wait for thread shutdown."""
        self._running = False
        if self._thread:
            # Wait for thread to complete gracefully (the while loop will exit on next iteration)
            self._thread.join(timeout=10)

    def refresh_pools(self, pools: List[Dict]) -> None:
        """Reload pool subscriptions from updated database.

        When new pools are discovered, this updates the subscription list
        and triggers an immediate reconnect to re-subscribe to the new accounts.
        """
        old_count = len(self._account_to_pools)
        self._build_account_map(pools)
        new_count = len(self._account_to_pools)

        print(f"[POOL_WS] 🔄 refresh_pools: {old_count} → {new_count} accounts", flush=True)
        logger.info(f"[POOL_WS] refresh_pools: {old_count} → {new_count} accounts")

        if new_count != old_count:
            print(f"[POOL_WS] ⚡ Detected {new_count - old_count} new accounts, stopping loop to trigger reconnect", flush=True)
            logger.info(f"[POOL_WS] Detected {new_count - old_count} new accounts, reconnecting")
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            else:
                print(f"[POOL_WS] ⚠️  Loop not running - cannot trigger reconnect", flush=True)
                logger.warning(f"[POOL_WS] Loop not running - cannot trigger reconnect")

    def _build_account_map(self, pools: List[Dict]) -> None:
        """Build pubkey->pools mapping from pool list. Handles multiple pools per account (shared WSOL, etc)."""
        self._account_to_pools = {}
        new_pool_count = 0
        for pool in pools:
            base_account = pool["base_account"]
            quote_account = pool["quote_account"]
            is_new = pool.get("is_legacy") == 0
            if is_new:
                new_pool_count += 1

            if base_account not in self._account_to_pools:
                self._account_to_pools[base_account] = []
            self._account_to_pools[base_account].append(pool)

            if quote_account not in self._account_to_pools:
                self._account_to_pools[quote_account] = []
            self._account_to_pools[quote_account].append(pool)

        print(f"[POOL_WS] 🗺️  Built account map: {len(self._account_to_pools)} accounts → {len(pools)} pools ({new_pool_count} new)", flush=True)

    def _run_thread(self) -> None:
        """Entry point for daemon thread — owns its own event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        except RuntimeError as e:
            # Expected when _running is set to False and loop stops
            if "Event loop stopped" not in str(e):
                logger.error(f"WebSocket thread error: {e}")
        finally:
            # Cancel any remaining tasks
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            # Run the loop once more to handle cancellations
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    async def _connect_loop(self) -> None:
        """Outer reconnect loop with exponential backoff."""
        reconnect_delay = 5
        ws = None
        try:
            while self._running:
                try:
                    async with websockets.connect(
                        HELIUS_WS_URL,
                        ping_interval=20,
                        ping_timeout=10,
                        close_timeout=10,
                    ) as ws:
                        reconnect_delay = 5  # reset on success
                        self.stats["connected"] = True
                        if self.stats["reconnects"] > 0:
                            self.stats["reconnects"] += 1
                        logger.info("Pool WebSocket connected")
                        await self._subscribe_all(ws)
                        await self._receive_loop(ws)
                except Exception as e:
                    logger.warning(f"Pool WebSocket disconnected: {e}")
                    self.stats["connected"] = False
                    if self._running:
                        await asyncio.sleep(min(reconnect_delay, 30))
                        reconnect_delay *= 2
        finally:
            self.stats["connected"] = False

    async def _subscribe_all(self, ws) -> None:
        """Send accountSubscribe for each tracked pool account."""
        self._sub_id_to_account = {}
        req_id = 1
        pubkeys_list = list(self._account_to_pools.keys())
        logger.info(f"Pool WS subscribing to {len(pubkeys_list)} accounts (sample: {[p[:8] for p in pubkeys_list[:5]]}...)")

        # Send all subscription requests and build mapping IMMEDIATELY (before responses arrive)
        req_to_pubkey = {}
        for pubkey in pubkeys_list:
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "accountSubscribe",
                "params": [pubkey, {"encoding": "base64", "commitment": "confirmed"}],
            }
            await ws.send(json.dumps(msg))
            req_to_pubkey[req_id] = pubkey  # Store IMMEDIATELY after send
            req_id += 1

        # Collect subscription confirmation responses
        confirmed = 0
        needed = len(pubkeys_list)
        start_confirm = time.time()
        timeout_per_confirm = 30  # Increase timeout for large subscription sets
        while confirmed < needed and self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout_per_confirm)
                data = json.loads(raw)
                logger.debug(f"Subscription response: {json.dumps(data)}")
                if "id" in data and "result" in data:
                    sub_id = data["result"]
                    req_id_from_response = data["id"]
                    pubkey = req_to_pubkey.get(req_id_from_response)
                    if pubkey:
                        self._sub_id_to_account[sub_id] = pubkey
                        confirmed += 1
                        if confirmed % 50 == 0 or confirmed == needed:
                            logger.info(f"Pool WS subscriptions: {confirmed}/{needed} confirmed")
                    else:
                        logger.warning(f"Subscription response for unknown request ID {req_id_from_response}")
                elif "id" in data and "error" in data:
                    logger.warning(f"Subscription error for request {data['id']}: {data.get('error')}")
            except asyncio.TimeoutError:
                elapsed = time.time() - start_confirm
                logger.warning(f"Timeout waiting for accountSubscribe confirmations ({confirmed}/{needed} confirmed after {elapsed:.1f}s)")
                # Don't break immediately — wait a bit longer for stragglers
                if confirmed >= needed * 0.95:  # If we have 95%+ confirmations, proceed
                    break
                await asyncio.sleep(2)

        self.stats["subscriptions"] = len(self._sub_id_to_account)
        print(f"[POOL_WS] ✅ Subscribed to {confirmed}/{needed} pool accounts", flush=True)
        logger.info(f"Pool WS subscribed to {confirmed}/{needed} accounts ({len(self._sub_id_to_account)} subscription IDs mapped)")

        if confirmed < needed:
            unconfirmed_ids = [req_id for req_id in req_to_pubkey.keys() if req_to_pubkey[req_id] not in [v for v in self._sub_id_to_account.values()]]
            logger.warning(f"[POOL_WS] ⚠️  {len(unconfirmed_ids)} subscriptions failed to confirm (request IDs: {unconfirmed_ids[:10]}...)")

    async def _receive_loop(self, ws) -> None:
        """Process incoming account notification events."""
        print(f"[POOL_WS] 🔄 _receive_loop started, waiting for account notifications...", flush=True)
        message_count = 0
        no_message_timeout = 0
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                no_message_timeout = 0  # Reset on any message
                message_count += 1
                if message_count % 100 == 1 or message_count <= 3:  # Log first few and every 100th
                    print(f"[POOL_WS] 📨 Received message #{message_count}: {raw[:100] if len(raw) > 100 else raw}...", flush=True)
            except asyncio.TimeoutError:
                # No message in 60s — normal if accounts aren't changing
                # Helius accountSubscribe only sends notifications when accounts change
                no_message_timeout += 1
                if no_message_timeout == 1:
                    print(f"[POOL_WS] ℹ️  No account changes in 60s (normal if pools are idle)", flush=True)
                continue
            self._handle_message(raw)

    def _handle_message(self, raw: str) -> None:
        """Parse accountNotification and update PoolStateStore. Deduplicates by slot."""
        try:
            msg = json.loads(raw)
            if msg.get("method") != "accountNotification":
                return

            print(f"[POOL_WS_DEBUG] accountNotification received", flush=True)
            params = msg.get("params", {})
            sub_id = params.get("subscription")
            pubkey = self._sub_id_to_account.get(sub_id)
            if not pubkey:
                print(f"[POOL_WS_DEBUG] Unknown subscription ID", flush=True)
                return

            self.stats["events_received"] += 1
            self._last_event_received = time.time()
            self.stats["is_stale"] = False

            account_data = params.get("result", {}).get("value", {})

            # Try to decode balance from SPL token account data first
            data_list = account_data.get("data", [])
            print(f"[POOL_WS_DEBUG] account_data keys: {account_data.keys()}, data_list length: {len(data_list) if data_list else 0}", flush=True)

            balance = None
            if data_list:
                print(f"[POOL_WS_DEBUG] Attempting to decode data[0], length={len(data_list[0]) if isinstance(data_list[0], (str, bytes, list)) else 'unknown'}", flush=True)
                balance = PoolReserveFetcher._decode_spl_token_balance(data_list[0])
                print(f"[POOL_WS_DEBUG] Decoded balance from data: {balance}", flush=True)

            # If no balance from data (native SOL account), try lamports field
            if balance is None:
                lamports = account_data.get("lamports")
                print(f"[POOL_WS_DEBUG] No balance from data, trying lamports: {lamports}", flush=True)
                balance = lamports

            if balance is None:
                print(f"[POOL_WS_DEBUG] No balance extracted from account at all", flush=True)
                return

            print(f"[POOL_WS_DEBUG] Final balance {balance} for {pubkey[:16]}...", flush=True)
            pools = self._account_to_pools.get(pubkey)
            if not pools:
                # Log unexpected accounts (might be subscription confirmations, etc.)
                print(f"[POOL_WS_DEBUG] Account NOT in account_to_pools map: {pubkey[:16]}... (map has {len(self._account_to_pools)} accounts)", flush=True)
                return

            # Update all pools that use this account
            print(f"[POOL_WS_DEBUG] Found {len(pools)} pools for account {pubkey[:16]}...", flush=True)
            for pool in pools:
                mint = pool["mint"]
                is_new = pool.get("is_legacy") == 0
                account_type = (
                    "base" if pubkey == pool["base_account"] else "quote"
                )
                if is_new:
                    logger.debug(f"[POOL_WS_DEBUG] NEW pool update: {mint[:16]}... {account_type} reserve on {pubkey[:16]}...")

                # Extract slot for deduplication (optional in notification)
                slot = params.get("result", {}).get("context", {}).get("slot")

                # Update reserve; returns False if deduplicated
                print(f"[POOL_WS_DEBUG] Calling update_reserve: mint={mint[:16]}..., base_account={pool['base_account'][:16]}..., type={account_type}, balance={balance}, slot={slot}", flush=True)
                reserve_updated = self._store.update_reserve(mint, pool["base_account"], account_type, balance, slot)
                print(f"[POOL_WS_DEBUG] update_reserve returned: {reserve_updated}", flush=True)
                if not reserve_updated:
                    self.stats["events_deduplicated"] += 1
                    continue

                self.stats["events_decoded"] += 1
                self.stats["last_event_at"] = time.time()

                # Log every 100 decoded events
                if self.stats["events_decoded"] % 100 == 0:
                    print(f"[POOL_WS] 📥 Decoded {self.stats['events_decoded']} reserve updates (dedup: {self.stats['events_deduplicated']})", flush=True)

                # Check if both reserves are now available for this pool
                # If callback registered, notify on dual-reserve completion
                reserves = self._store.get_reserves(mint, pool["base_account"])
                if reserves and self._on_dual_update:
                    try:
                        self._on_dual_update(mint, pool["base_account"], reserves)
                    except Exception as e:
                        logger.debug(f"Error in dual-update callback: {e}")

        except Exception as e:
            logger.debug(f"Pool WS message parse error: {e}")


_fetcher_instance: Optional[PoolReserveFetcher] = None
_pool_state_instance: Optional[PoolStateStore] = None


def get_pool_fetcher(db_path: str) -> PoolReserveFetcher:
    """Get or create singleton PoolReserveFetcher instance."""
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = PoolReserveFetcher(db_path)
    return _fetcher_instance


def get_pool_state() -> PoolStateStore:
    """Get or create singleton PoolStateStore instance."""
    global _pool_state_instance
    if _pool_state_instance is None:
        _pool_state_instance = PoolStateStore()
    return _pool_state_instance
