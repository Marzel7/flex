"""
Correct market cap calculation using total supply.

Formula:
    market_cap = price_usd × total_supply

(NOT price × base_reserve — that's wrong!)

Caches supply per mint for performance.
"""

import logging
import sqlite3
import asyncio
from typing import Optional, Dict
from datetime import datetime

logger = logging.getLogger(__name__)


class MarketCapCalculator:
    """Calculate correct market cap using total supply."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._supply_cache: Dict[str, int] = {}

    async def get_token_supply(
        self,
        mint: str,
        rpc_client=None
    ) -> Optional[int]:
        """
        Get total token supply.

        Checks cache first, then database, then RPC.
        """
        # Check in-memory cache
        if mint in self._supply_cache:
            return self._supply_cache[mint]

        # Check database
        supply = await self._fetch_supply_from_db(mint)
        if supply:
            self._supply_cache[mint] = supply
            return supply

        # Fetch from RPC (first time only)
        if rpc_client:
            try:
                supply = await self._fetch_supply_from_rpc(mint, rpc_client)
                if supply:
                    self._supply_cache[mint] = supply
                    await self._store_supply_in_db(mint, supply)
                    return supply
            except Exception as e:
                logger.error(f"Failed to fetch supply for {mint}: {e}")

        return None

    def calculate_market_cap(
        self,
        price_usd: float,
        total_supply: int,
        decimals: int
    ) -> float:
        """
        Calculate market cap.

        Formula:
            market_cap = price_usd × (total_supply / 10^decimals)

        Args:
            price_usd: Token price in USD
            total_supply: Total token supply (raw units)
            decimals: Token decimals

        Returns:
            Market cap in USD
        """
        if total_supply <= 0 or decimals < 0:
            return 0.0

        circulating_supply = total_supply / (10 ** decimals)
        return price_usd * circulating_supply

    async def _fetch_supply_from_db(self, mint: str) -> Optional[int]:
        """Fetch supply from database cache."""
        def fetch():
            try:
                conn = sqlite3.connect(self.db_path, timeout=5)
                cursor = conn.cursor()
                row = cursor.execute(
                    "SELECT total_supply FROM token_supply_cache WHERE mint = ?",
                    (mint,)
                ).fetchone()
                conn.close()
                return row[0] if row else None
            except Exception as e:
                logger.debug(f"DB fetch supply error: {e}")
                return None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, fetch)

    async def _fetch_supply_from_rpc(
        self,
        mint: str,
        rpc_client
    ) -> Optional[int]:
        """Fetch total supply from RPC."""
        try:
            response = await rpc_client.get_token_supply(mint)
            if response and 'result' in response:
                return int(response['result']['value']['amount'])
        except Exception as e:
            logger.warning(f"RPC fetch supply error for {mint}: {e}")
        return None

    async def _store_supply_in_db(self, mint: str, supply: int) -> None:
        """Store supply in database cache."""
        def store():
            try:
                conn = sqlite3.connect(self.db_path, timeout=5)
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO token_supply_cache
                    (mint, total_supply, decimals, fetched_at)
                    VALUES (?, ?, 6, ?)
                """, (mint, supply, datetime.utcnow().isoformat()))
                conn.commit()
                conn.close()
                logger.debug(f"Stored supply for {mint}: {supply}")
            except Exception as e:
                logger.debug(f"DB store supply error: {e}")

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, store)


def get_market_cap_calculator(db_path: str) -> MarketCapCalculator:
    """Get market cap calculator."""
    return MarketCapCalculator(db_path)
