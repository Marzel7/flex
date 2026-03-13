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

HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=")
HELIUS_WS_URL = os.getenv("HELIUS_WS_URL", "wss://mainnet.helius-rpc.com/?api-key=")


class PoolReserveFetcher:
    """Batch-fetches token balances from on-chain pool accounts using getMultipleAccounts."""

    SOL_MINT = "So11111111111111111111111111111111111111112"
    MAX_PUBKEYS_PER_CALL = 100

    def __init__(self, db_path: str):
        self.db_path = db_path

    def get_active_pools(self) -> List[Dict]:
        """Load all active pool registrations from token_pool_accounts table."""
        import sqlite3

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM token_pool_accounts WHERE is_active = 1"
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

    MIN_LIQUIDITY_USD = 5_000.0
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
    ) -> Optional[TokenPrice]:
        """
        Compute TokenPrice from AMM reserves.
        Applies filters: minimum liquidity ($5000 USD) and max deviation (40%).
        Returns None if price is rejected by filters.
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

        return TokenPrice(
            mint=mint,
            price_usd=price_usd,
            price_sol=price_sol,
            liquidity_usd=liquidity_usd,
            volume_24h=0,
            market_cap=0,
            source="pool",
            is_stale=False,
        )

    @staticmethod
    async def fetch_sol_price_usd() -> float:
        """
        Fetch current SOL price from Jupiter API.
        Called once per worker cycle; result shared across all compute_price() calls.
        Returns 0.0 on failure (caller skips pool prices).
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://price.jup.ag/v4/price",
                    params={"ids": "SOL"},
                    timeout=aiohttp.ClientTimeout(total=2.0),
                ) as resp:
                    data = await resp.json()
                    return float(data["data"]["SOL"]["price"])
        except Exception as e:
            logger.warning(f"SOL price fetch failed: {e}")
            return 0.0


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
        #   'is_stale': bool
        # }
        self._state: Dict[Tuple[str, str], Dict] = {}

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
                    "last_update": 0,
                    "last_slot": None,
                    "is_stale": False,
                }

            # Deduplication: skip if same slot seen recently (per pool)
            if slot is not None and self._state[pool_id]["last_slot"] == slot:
                return False

            self._state[pool_id][f"{account_type}_reserve"] = raw_balance
            self._state[pool_id]["last_update"] = time.time()
            self._state[pool_id]["last_slot"] = slot
            self._state[pool_id]["is_stale"] = False
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
        Only includes pools with both reserves known and not stale.
        """
        results = []
        with self._lock:
            for (m, base_account), s in self._state.items():
                if m == mint and not s["is_stale"]:
                    if s["base_reserve"] is not None and s["quote_reserve"] is not None:
                        results.append((base_account, s["base_reserve"], s["quote_reserve"]))
        return results

    def get_all_mints(self) -> List[str]:
        """Return list of all distinct mints currently in store."""
        with self._lock:
            return list({m for (m, _) in self._state.keys()})

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
    Strategy: highest liquidity pool wins (already filtered by MIN_LIQUIDITY_USD in compute_price).
    Source annotated as "pool(N)" when N > 1 pools contributed.
    """

    @staticmethod
    def aggregate(prices: List["TokenPrice"]) -> Optional["TokenPrice"]:
        """
        Given a list of TokenPrice objects (one per pool for a mint),
        return the best price. Strategy: highest liquidity pool wins.
        
        Args:
            prices: List of TokenPrice objects (may contain None values)
            
        Returns:
            TokenPrice with aggregated price, or None if no valid prices.
        """
        valid = [p for p in prices if p is not None]
        if not valid:
            return None
        
        # Pick highest liquidity pool as most trusted price
        best = max(valid, key=lambda p: p.liquidity_usd)
        n = len(valid)
        
        # Annotate source with pool count
        return TokenPrice(
            mint=best.mint,
            price_usd=best.price_usd,
            price_sol=best.price_sol,
            liquidity_usd=best.liquidity_usd,
            volume_24h=best.volume_24h,
            market_cap=best.market_cap,
            source=f"pool({n})" if n > 1 else "pool",
            is_stale=best.is_stale,
        )

class PoolWebSocketClient:
    """
    Persistent WebSocket subscription client for pool reserve accounts.
    Runs in a daemon thread with its own asyncio event loop.
    On account update: decode SPL balance → update PoolStateStore.
    """

    WS_STALE_THRESHOLD = 120  # 2 minutes without events triggers fallback poll

    def __init__(self, state_store: PoolStateStore, db_path: str):
        self._store = state_store
        self._db_path = db_path
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._sub_id_to_account: Dict[int, str] = {}  # subscription_id -> pubkey
        self._account_to_pool: Dict[str, Dict] = {}  # pubkey -> pool dict
        self._last_event_received = time.time()
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
        logger.info(
            f"PoolWebSocketClient started — subscribing to {len(self._account_to_pool)} accounts"
        )

    def stop(self) -> None:
        """Stop the WebSocket client and wait for thread shutdown."""
        self._running = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)

    def _build_account_map(self, pools: List[Dict]) -> None:
        """Build pubkey->pool mapping from pool list."""
        self._account_to_pool = {}
        for pool in pools:
            self._account_to_pool[pool["base_account"]] = pool
            self._account_to_pool[pool["quote_account"]] = pool

    def _run_thread(self) -> None:
        """Entry point for daemon thread — owns its own event loop."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        finally:
            self._loop.close()

    async def _connect_loop(self) -> None:
        """Outer reconnect loop with exponential backoff."""
        reconnect_delay = 5
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
                logger.info(f"Pool WebSocket reconnecting in {reconnect_delay}s")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, 60)

    async def _subscribe_all(self, ws) -> None:
        """Send accountSubscribe for each tracked pool account."""
        self._sub_id_to_account = {}
        req_id = 1
        logger.info(f"Pool WS subscribing to {len(self._account_to_pool)} accounts")
        for pubkey in list(self._account_to_pool.keys()):
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "accountSubscribe",
                "params": [pubkey, {"encoding": "base64", "commitment": "confirmed"}],
            }
            await ws.send(json.dumps(msg))
            req_id += 1

        # Collect subscription confirmation responses
        confirmed = 0
        needed = len(self._account_to_pool)
        # Map req_id -> pubkey so we can match confirmations
        req_to_pubkey = {i + 1: pk for i, pk in enumerate(self._account_to_pool.keys())}
        while confirmed < needed and self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(raw)
                logger.debug(f"Subscription response: {json.dumps(data)}")
                if "id" in data and "result" in data:
                    sub_id = data["result"]
                    pubkey = req_to_pubkey.get(data["id"])
                    if pubkey:
                        self._sub_id_to_account[sub_id] = pubkey
                        confirmed += 1
                        logger.debug(f"Confirmed subscription {confirmed}/{needed}")
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for accountSubscribe confirmations ({confirmed}/{needed} confirmed so far)")
                break

        self.stats["subscriptions"] = len(self._sub_id_to_account)
        logger.info(f"Pool WS subscribed to {confirmed}/{needed} accounts")

    async def _receive_loop(self, ws) -> None:
        """Process incoming account notification events."""
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
            except asyncio.TimeoutError:
                # keepalive — no event in 60s is normal
                continue
            self._handle_message(raw)

    def _handle_message(self, raw: str) -> None:
        """Parse accountNotification and update PoolStateStore. Deduplicates by slot."""
        try:
            msg = json.loads(raw)
            if msg.get("method") != "accountNotification":
                return

            params = msg.get("params", {})
            sub_id = params.get("subscription")
            pubkey = self._sub_id_to_account.get(sub_id)
            if not pubkey:
                return

            self.stats["events_received"] += 1
            self._last_event_received = time.time()
            self.stats["is_stale"] = False

            account_data = params.get("result", {}).get("value", {})
            data_list = account_data.get("data", [])
            if not data_list:
                return

            balance = PoolReserveFetcher._decode_spl_token_balance(data_list[0])
            if balance is None:
                return

            pool = self._account_to_pool.get(pubkey)
            if not pool:
                return

            mint = pool["mint"]
            account_type = (
                "base" if pubkey == pool["base_account"] else "quote"
            )

            # Extract slot for deduplication (optional in notification)
            slot = params.get("result", {}).get("context", {}).get("slot")

            # Update reserve; returns False if deduplicated
            if not self._store.update_reserve(mint, pool["base_account"], account_type, balance, slot):
                self.stats["events_deduplicated"] += 1
                return

            self.stats["events_decoded"] += 1
            self.stats["last_event_at"] = time.time()

        except Exception as e:
            logger.debug(f"Pool WS message parse error: {e}")


_fetcher_instance: Optional[PoolReserveFetcher] = None


def get_pool_fetcher(db_path: str) -> PoolReserveFetcher:
    """Get or create singleton PoolReserveFetcher instance."""
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = PoolReserveFetcher(db_path)
    return _fetcher_instance
