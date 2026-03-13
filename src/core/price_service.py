"""
FLEX Token Price Service

Fetches, caches, and normalizes token prices from multiple sources.

Architecture:
1. Dexscreener (primary - DEX pair prices)
2. Jupiter (secondary - implied prices from quotes)
3. Local cache (fallback - stale prices)
4. Mark as unavailable if all fail

Caching:
- Hot tokens: 5-15 seconds
- Organization page: 15-30 seconds
- Historical: 1-5 minutes
"""

import sqlite3
import logging
import time
import asyncio
import aiohttp
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import json

logger = logging.getLogger(__name__)


@dataclass
class TokenPrice:
    """Normalized token price response."""
    mint: str
    price_usd: float
    price_sol: float
    liquidity_usd: float
    volume_24h: float
    market_cap: float
    source: str  # 'dexscreener', 'jupiter', 'cached', 'unavailable'
    pair_address: Optional[str] = None
    timestamp: int = None
    is_stale: bool = False
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = int(time.time())


class PriceCache:
    """In-memory price cache with TTL support."""
    
    def __init__(self):
        self.cache: Dict[str, Tuple[TokenPrice, float]] = {}
        self.ttl_config = {
            'hot': 10,        # 10 seconds for dashboard
            'org': 30,        # 30 seconds for org pages
            'history': 300,   # 5 minutes for historical
            'snapshot': 30,   # 30 seconds snapshot buffer for dashboard reads
        }
    
    def get(self, mint: str, cache_type: str = 'hot') -> Optional[TokenPrice]:
        """Get price from cache if not expired."""
        if mint not in self.cache:
            return None
        
        price, cached_time = self.cache[mint]
        ttl = self.ttl_config.get(cache_type, 10)
        
        if time.time() - cached_time > ttl:
            del self.cache[mint]
            return None
        
        return price
    
    def set(self, mint: str, price: TokenPrice) -> None:
        """Store price in cache."""
        self.cache[mint] = (price, time.time())
    
    def clear(self, mint: Optional[str] = None) -> None:
        """Clear cache."""
        if mint:
            self.cache.pop(mint, None)
        else:
            self.cache.clear()


class DexscreenerClient:
    """Fetches prices from Dexscreener API."""
    
    BASE_URL = "https://api.dexscreener.com/latest/dex"
    
    @staticmethod
    async def get_price(mint: str) -> Optional[TokenPrice]:
        """Fetch token price from Dexscreener."""
        try:
            url = f"{DexscreenerClient.BASE_URL}/tokens/{mint}"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=1.5)) as resp:
                    if resp.status != 200:
                        logger.warning(f"Dexscreener {resp.status} for {mint}")
                        return None
                    
                    data = await resp.json()
                    
                    if not data.get('pairs') or len(data['pairs']) == 0:
                        logger.debug(f"No pairs found for {mint} on Dexscreener")
                        return None
                    
                    # Use first (best liquidity) pair
                    pair = data['pairs'][0]
                    
                    try:
                        price_usd = float(pair.get('priceUsd', 0) or 0)
                        if price_usd == 0:
                            return None
                        
                        # Estimate SOL price (assume 1 SOL = $180 for calculation)
                        sol_price = 180  # Could be fetched separately
                        price_sol = price_usd / sol_price if sol_price > 0 else 0
                        
                        return TokenPrice(
                            mint=mint,
                            price_usd=price_usd,
                            price_sol=price_sol,
                            liquidity_usd=float(pair.get('liquidity', {}).get('usd', 0) or 0),
                            volume_24h=float(pair.get('volume', {}).get('h24', 0) or 0),
                            market_cap=float(pair.get('marketCap', 0) or 0),
                            source='dexscreener',
                            pair_address=pair.get('pairAddress'),
                            timestamp=int(time.time()),
                            is_stale=False
                        )
                    except (ValueError, KeyError) as e:
                        logger.error(f"Error parsing Dexscreener response for {mint}: {e}")
                        return None
                        
        except asyncio.TimeoutError:
            logger.warning(f"Dexscreener timeout for {mint}")
            return None
        except Exception as e:
            logger.error(f"Dexscreener error for {mint}: {e}")
            return None
    
    @staticmethod
    async def get_prices(mints: List[str]) -> Dict[str, Optional[TokenPrice]]:
        """Fetch multiple prices in parallel."""
        tasks = [DexscreenerClient.get_price(mint) for mint in mints]
        results = await asyncio.gather(*tasks)
        return {mint: price for mint, price in zip(mints, results)}


class JupiterClient:
    """Fetches prices from Jupiter API (fallback)."""
    
    BASE_URL = "https://quote-api.jup.ag/v6"
    
    @staticmethod
    async def get_price(mint: str, sol_mint: str = "So11111111111111111111111111111111111111112") -> Optional[TokenPrice]:
        """Fetch price by getting quote from 1 token to SOL."""
        try:
            # Quote: 1 unit of token to SOL
            url = f"{JupiterClient.BASE_URL}/quote"
            params = {
                'inputMint': mint,
                'outputMint': sol_mint,
                'amount': 1_000_000_000,  # 1 unit (9 decimals)
                'slippageBps': 50
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=1.2)) as resp:
                    if resp.status != 200:
                        logger.debug(f"Jupiter {resp.status} for {mint}")
                        return None
                    
                    data = await resp.json()
                    
                    # Extract out amount (how much SOL for 1 token)
                    out_amount = float(data.get('outAmount', 0) or 0)
                    if out_amount == 0:
                        return None
                    
                    price_sol = out_amount / 1_000_000_000  # Convert from lamports
                    price_usd = price_sol * 180  # Estimate USD (1 SOL = $180)
                    
                    return TokenPrice(
                        mint=mint,
                        price_usd=price_usd,
                        price_sol=price_sol,
                        liquidity_usd=0,  # Jupiter doesn't provide this
                        volume_24h=0,     # Jupiter doesn't provide this
                        market_cap=0,     # Jupiter doesn't provide this
                        source='jupiter',
                        timestamp=int(time.time()),
                        is_stale=False
                    )
                    
        except asyncio.TimeoutError:
            logger.debug(f"Jupiter timeout for {mint}")
            return None
        except Exception as e:
            logger.debug(f"Jupiter error for {mint}: {e}")
            return None


class BirdeyeClient:
    """Fetches prices from Birdeye API (final fallback)."""

    BASE_URL = "https://public-api.birdeye.so/defi"

    @staticmethod
    async def get_price(mint: str) -> Optional[TokenPrice]:
        """Fetch token price from Birdeye API."""
        try:
            url = f"{BirdeyeClient.BASE_URL}/token_price"
            params = {'address': mint}

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=1.0)) as resp:
                    if resp.status == 404:
                        logger.debug(f"Birdeye 404 for {mint}")
                        return None
                    if resp.status == 429:
                        logger.debug(f"Birdeye rate limited for {mint}")
                        return None
                    if resp.status != 200:
                        logger.debug(f"Birdeye {resp.status} for {mint}")
                        return None

                    data = await resp.json()
                    price_data = data.get('data', {})

                    if not price_data or not price_data.get('price'):
                        logger.debug(f"Birdeye no price for {mint}")
                        return None

                    price_usd = float(price_data.get('price', 0))
                    if price_usd == 0:
                        return None

                    return TokenPrice(
                        mint=mint,
                        price_usd=price_usd,
                        price_sol=float(price_data.get('priceInSOL', 0)),
                        liquidity_usd=0,
                        volume_24h=0,
                        market_cap=0,
                        source='birdeye',
                        timestamp=int(time.time()),
                        is_stale=False
                    )

        except asyncio.TimeoutError:
            logger.debug(f"Birdeye timeout for {mint}")
            return None
        except Exception as e:
            logger.debug(f"Birdeye error for {mint}: {e}")
            return None


class TokenPriceService:
    """Main token price service with fallback logic."""
    
    def __init__(self, db_path: str = 'database/flex_complete_database.db'):
        self.db_path = db_path
        self.cache = PriceCache()
        self.stats = {
            'dexscreener_attempted': 0,
            'dexscreener_success': 0,
            'dexscreener_fail': 0,
            'jupiter_attempted': 0,
            'jupiter_success': 0,
            'jupiter_fail': 0,
            'birdeye_attempted': 0,
            'birdeye_success': 0,
            'birdeye_fail': 0,
            'stale_fallback': 0,
            'unavailable': 0,
        }
        self._ensure_tables()
        logger.info("TokenPriceService initialized")
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _ensure_tables(self) -> None:
        """Create price snapshot table if not exists."""
        conn = self._get_conn()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_price_snapshots (
                snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                mint            TEXT NOT NULL,
                price_usd       REAL NOT NULL,
                price_sol       REAL NOT NULL,
                liquidity_usd   REAL DEFAULT 0,
                volume_24h      REAL DEFAULT 0,
                market_cap      REAL DEFAULT 0,
                source          TEXT NOT NULL,
                pair_address    TEXT,
                captured_at     INTEGER NOT NULL,
                created_at      INTEGER NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tps_mint_time
            ON token_price_snapshots(mint, captured_at DESC)
        """)
        
        conn.commit()
        conn.close()
    
    def _get_cached_price(self, mint: str) -> Optional[TokenPrice]:
        """Get most recent price from database cache."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    mint, price_usd, price_sol, liquidity_usd, volume_24h, 
                    market_cap, source, pair_address, captured_at
                FROM token_price_snapshots
                WHERE mint = ?
                ORDER BY captured_at DESC
                LIMIT 1
            """, (mint,))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            # Check if older than 5 minutes
            age = int(time.time()) - row['captured_at']
            is_stale = age > 300
            
            return TokenPrice(
                mint=row['mint'],
                price_usd=row['price_usd'],
                price_sol=row['price_sol'],
                liquidity_usd=row['liquidity_usd'],
                volume_24h=row['volume_24h'],
                market_cap=row['market_cap'],
                source='cached',
                pair_address=row['pair_address'],
                timestamp=row['captured_at'],
                is_stale=is_stale
            )
        except Exception as e:
            logger.error(f"Error getting cached price for {mint}: {e}")
            return None
    
    def _store_snapshot(self, price: TokenPrice) -> None:
        """Store price snapshot in database."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO token_price_snapshots
                (mint, price_usd, price_sol, liquidity_usd, volume_24h, 
                 market_cap, source, pair_address, captured_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                price.mint,
                price.price_usd,
                price.price_sol,
                price.liquidity_usd,
                price.volume_24h,
                price.market_cap,
                price.source,
                price.pair_address,
                price.timestamp,
                int(time.time())
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error storing price snapshot for {price.mint}: {e}")
    
    async def get_token_price(self, mint: str, cache_type: str = 'hot') -> TokenPrice:
        """
        Get token price with multi-source fallback and 3-second budget.

        Priority:
        1. In-memory cache (hot)
        2. Dexscreener (1.5s timeout)
        3. Jupiter (1.2s timeout)
        4. Birdeye (1.0s timeout)
        5. Database cache (stale)
        6. Unavailable

        Total budget: 3 seconds across all sources.
        """
        TOTAL_BUDGET_SECS = 3.0
        fetch_start = time.time()

        # Try in-memory cache (no budget check — free)
        cached = self.cache.get(mint, cache_type)
        if cached:
            return cached

        # Try Dexscreener
        if time.time() - fetch_start < TOTAL_BUDGET_SECS:
            self.stats['dexscreener_attempted'] += 1
            dex_price = await DexscreenerClient.get_price(mint)
            if dex_price:
                self.stats['dexscreener_success'] += 1
                self.cache.set(mint, dex_price)
                self._store_snapshot(dex_price)
                return dex_price
            self.stats['dexscreener_fail'] += 1

        # Try Jupiter
        if time.time() - fetch_start < TOTAL_BUDGET_SECS:
            self.stats['jupiter_attempted'] += 1
            jup_price = await JupiterClient.get_price(mint)
            if jup_price:
                self.stats['jupiter_success'] += 1
                self.cache.set(mint, jup_price)
                self._store_snapshot(jup_price)
                return jup_price
            self.stats['jupiter_fail'] += 1

        # Try Birdeye (final fallback before stale cache)
        if time.time() - fetch_start < TOTAL_BUDGET_SECS:
            self.stats['birdeye_attempted'] += 1
            birdeye_price = await BirdeyeClient.get_price(mint)
            if birdeye_price:
                self.stats['birdeye_success'] += 1
                self.cache.set(mint, birdeye_price)
                self._store_snapshot(birdeye_price)
                return birdeye_price
            self.stats['birdeye_fail'] += 1

        # Try database cache (stale) — always attempt, no budget check
        db_price = self._get_cached_price(mint)
        if db_price:
            self.stats['stale_fallback'] += 1
            self.cache.set(mint, db_price)
            return db_price

        # Unavailable
        self.stats['unavailable'] += 1
        unavailable = TokenPrice(
            mint=mint,
            price_usd=0,
            price_sol=0,
            liquidity_usd=0,
            volume_24h=0,
            market_cap=0,
            source='unavailable',
            is_stale=True
        )
        self.cache.set(mint, unavailable)
        return unavailable
    
    async def get_token_prices(self, mints: List[str], cache_type: str = 'hot') -> Dict[str, TokenPrice]:
        """Fetch multiple token prices in parallel."""
        tasks = [self.get_token_price(mint, cache_type) for mint in mints]
        prices = await asyncio.gather(*tasks)
        return {mint: price for mint, price in zip(mints, prices)}
    
    def get_token_price_sync(self, mint: str, cache_type: str = 'hot') -> TokenPrice:
        """Synchronous wrapper for get_token_price."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.get_token_price(mint, cache_type))
        finally:
            loop.close()
    
    def get_token_prices_sync(self, mints: List[str], cache_type: str = 'hot') -> Dict[str, TokenPrice]:
        """Synchronous wrapper for get_token_prices."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.get_token_prices(mints, cache_type))
        finally:
            loop.close()
    
    def get_price_history(self, mint: str, hours: int = 24) -> List[Dict]:
        """Get historical price snapshots."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cutoff_time = int(time.time()) - (hours * 3600)
            
            cursor.execute("""
                SELECT 
                    price_usd, price_sol, liquidity_usd, volume_24h, 
                    market_cap, captured_at
                FROM token_price_snapshots
                WHERE mint = ? AND captured_at > ?
                ORDER BY captured_at ASC
            """, (mint, cutoff_time))
            
            rows = cursor.fetchall()
            conn.close()
            
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting price history for {mint}: {e}")
            return []
    
    def clear_old_snapshots(self, days: int = 30) -> int:
        """Clear old price snapshots."""
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            cutoff_time = int(time.time()) - (days * 86400)
            
            cursor.execute("""
                DELETE FROM token_price_snapshots
                WHERE created_at < ?
            """, (cutoff_time,))
            
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            
            logger.info(f"Cleared {deleted} old price snapshots")
            return deleted
        except Exception as e:
            logger.error(f"Error clearing old snapshots: {e}")
            return 0


# Singleton instance
_price_service: Optional[TokenPriceService] = None


def get_price_service(db_path: str = 'database/flex_complete_database.db') -> TokenPriceService:
    """Get or create singleton price service."""
    global _price_service
    if _price_service is None:
        _price_service = TokenPriceService(db_path)
    return _price_service
