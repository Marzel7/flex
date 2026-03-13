"""
Pool-based pricing engine: batch-fetch on-chain AMM reserves and compute token prices.
Uses getMultipleAccounts RPC batching (max 100 pubkeys/call) to minimize RPC usage.
"""

import base64
import struct
import time
import logging
import os
import aiohttp
from typing import Dict, List, Optional, Tuple

from .price_service import TokenPrice
from src.metrics.rpc_metrics_recorder import record_request

logger = logging.getLogger(__name__)

HELIUS_RPC_URL = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=")


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

    async def fetch_reserves(self, pools: List[Dict]) -> Dict[str, Tuple[int, int]]:
        """
        Batch-fetch base+quote reserves for all pools via getMultipleAccounts.
        Returns {mint: (base_reserve_raw, quote_reserve_raw)}.
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

        # Pair up reserves by mint
        reserves: Dict[str, Tuple[int, int]] = {}
        for pool in pools:
            base_key = pool["base_account"]
            quote_key = pool["quote_account"]
            base_balance = balances.get(base_key)
            quote_balance = balances.get(quote_key)

            if base_balance is not None and quote_balance is not None:
                reserves[pool["mint"]] = (base_balance, quote_balance)

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
                section="price",
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


_fetcher_instance: Optional[PoolReserveFetcher] = None


def get_pool_fetcher(db_path: str) -> PoolReserveFetcher:
    """Get or create singleton PoolReserveFetcher instance."""
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = PoolReserveFetcher(db_path)
    return _fetcher_instance
