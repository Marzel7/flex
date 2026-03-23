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
import requests
import threading
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
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
    peak_market_cap: float = 0  # NEW: Historical maximum market cap
    peak_market_cap_at: Optional[int] = None  # NEW: Timestamp when peak reached
    
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
    
    def set(self, mint: str, price: TokenPrice, cache_type: str = 'hot') -> None:
        """Store price in cache (cache_type parameter for future multi-tier support)."""
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
            'pool_attempted': 0,
            'pool_success': 0,
            'pool_fail': 0,
            'stale_fallback': 0,
            'unavailable': 0,
        }
        # Shared Birdeye client: requests.Session per thread (thread-safe)
        self._birdeye_local = threading.local()
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='birdeye-')

        # Pool price cache: synchronous dict read, populated by worker each cycle
        self.pool_price_cache: Dict[str, 'TokenPrice'] = {}

        # Circuit breaker: track disabled sources and cooldown with exponential backoff
        self.circuit_breaker = {
            'dexscreener': {'disabled': False, 'disabled_at': 0, 'break_count': 0},
            'jupiter': {'disabled': False, 'disabled_at': 0, 'break_count': 0},
            'birdeye': {'disabled': False, 'disabled_at': 0, 'break_count': 0},
            'pool': {'disabled': False, 'disabled_at': 0, 'break_count': 0},
        }

        # EWMA latency per source (for adaptive ordering)
        self.source_latency_ewma = {
            'dexscreener': 0.0,
            'jupiter': 0.0,
            'birdeye': 0.0,
            'pool': 0.0,
        }

        # Source attempt history for rolling failure rate (last 50 attempts per source)
        self.source_attempts = {
            'dexscreener': [],
            'jupiter': [],
            'birdeye': [],
            'pool': [],
        }

        # Provider timeout budgets (seconds) — per-source limits
        self.provider_timeouts = {
            'dexscreener': 1.2,  # Reduce from 1.5 to 1.2
            'jupiter': 0.8,      # Reduce from 1.2 to 0.8
            'birdeye': 1.0,      # Keep at 1.0
            'pool': 0,           # Pool is synchronous dict read, no HTTP timeout
        }

        self._ensure_tables()

        # NEW: Load persisted circuit breaker state from database
        self._load_circuit_breaker_state()

        logger.info("TokenPriceService initialized")
    
    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _ensure_tables(self) -> None:
        """Create price snapshot and circuit breaker persistence tables."""
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

        # NEW: Circuit breaker persistence table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS circuit_breaker_state (
                source TEXT PRIMARY KEY,
                disabled INTEGER DEFAULT 0,
                disabled_at INTEGER DEFAULT 0,
                break_count INTEGER DEFAULT 0,
                last_break_at INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cb_disabled
            ON circuit_breaker_state(disabled, disabled_at)
        """)

        # Pool accounts registration table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS token_pool_accounts (
                mint              TEXT NOT NULL,
                base_account      TEXT NOT NULL,
                quote_account     TEXT NOT NULL,
                pool_program      TEXT NOT NULL DEFAULT 'raydium_amm',
                base_token        TEXT NOT NULL,
                quote_token       TEXT NOT NULL DEFAULT 'So11111111111111111111111111111111111111112',
                base_decimals     INTEGER NOT NULL DEFAULT 6,
                quote_decimals    INTEGER NOT NULL DEFAULT 9,
                last_reserve_fetch INTEGER DEFAULT 0,
                vault_validation_status TEXT NOT NULL DEFAULT 'pending' CHECK(vault_validation_status IN ('pending', 'validated', 'rejected')),
                vault_validation_error TEXT,
                vault_validation_attempts INTEGER DEFAULT 0,
                last_vault_validation_at INTEGER DEFAULT 0,
                discovery_method  TEXT DEFAULT 'unknown',
                is_active         BOOLEAN DEFAULT 1,
                created_at        INTEGER NOT NULL,
                updated_at        INTEGER NOT NULL,
                PRIMARY KEY (mint, base_account)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tpa_mint_active
            ON token_pool_accounts(mint, is_active)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_tpa_vault_status
            ON token_pool_accounts(vault_validation_status, last_vault_validation_at)
        """)

        # Migration: Add discovery_method column if missing
        try:
            cursor.execute("PRAGMA table_info(token_pool_accounts)")
            columns = {row[1] for row in cursor.fetchall()}
            if 'discovery_method' not in columns:
                cursor.execute("""
                    ALTER TABLE token_pool_accounts
                    ADD COLUMN discovery_method TEXT DEFAULT 'unknown'
                """)
                logger.info("Migrated: Added discovery_method column to token_pool_accounts")
        except Exception as e:
            logger.debug(f"Migration check for discovery_method: {e}")

        conn.commit()
        conn.close()
        logger.info("Database tables ensured (price snapshots + circuit breaker + pool accounts with vault tracking)")
    
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
    

    def _fetch_birdeye_sync(self, mint: str) -> Optional[TokenPrice]:
        """
        Fetch Birdeye price synchronously using shared requests.Session per thread.
        
        Called via run_in_executor from get_token_price() to avoid blocking the event loop.
        """
        try:
            # Get or create a shared session for this thread
            if not hasattr(self._birdeye_local, 'session'):
                self._birdeye_local.session = requests.Session()
                self._birdeye_local.session.headers.update({'accept': 'application/json'})
            
            resp = self._birdeye_local.session.get(
                f"{BirdeyeClient.BASE_URL}/token_price",
                params={'address': mint},
                timeout=1.0
            )
            
            if resp.status_code != 200:
                return None
            
            data = resp.json()
            price_data = data.get('data', {})
            
            if not price_data or not price_data.get('price'):
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
        
        except requests.Timeout:
            logger.debug(f"Birdeye timeout for {mint}")
            return None
        except Exception as e:
            logger.debug(f"Birdeye error for {mint}: {e}")
            return None

    def _get_exponential_cooldown(self, break_count: int) -> int:
        """
        Get cooldown in seconds based on break count (exponential backoff).

        1st break:  10 min (600s)
        2nd break:  30 min (1800s)
        3rd break:  2 hours (7200s)
        4th+ breaks: 4 hours (14400s)
        """
        BASE_COOLDOWN = 600  # 10 minutes
        MAX_EXPONENT = 3     # Cap at 4 hours

        if break_count <= 1:
            return BASE_COOLDOWN

        exponent = min(break_count - 1, MAX_EXPONENT)
        cooldown = BASE_COOLDOWN * (2 ** exponent)
        return int(cooldown)

    def _load_circuit_breaker_state(self) -> None:
        """Load persisted circuit breaker state from database."""
        try:
            conn = self._get_conn()
            rows = conn.execute(
                'SELECT source, disabled, disabled_at, break_count FROM circuit_breaker_state'
            ).fetchall()

            for source, disabled, disabled_at, break_count in rows:
                if source in self.circuit_breaker:
                    self.circuit_breaker[source] = {
                        'disabled': bool(disabled),
                        'disabled_at': disabled_at,
                        'break_count': break_count or 0,
                    }

                    if disabled:
                        elapsed = time.time() - disabled_at
                        logger.info(
                            f"Loaded circuit breaker state: {source} disabled "
                            f"(elapsed {elapsed:.0f}s, break #{break_count})"
                        )
                    else:
                        logger.info(f"Loaded circuit breaker state: {source} enabled")

            conn.close()
        except Exception as e:
            logger.error(f"Failed to load circuit breaker state: {e}")
            # Fall back to in-memory defaults (non-fatal)

    def _save_circuit_breaker_state(self, source: str) -> None:
        """Persist circuit breaker state to database."""
        try:
            conn = self._get_conn()
            cb = self.circuit_breaker.get(source, {})
            now = int(time.time())

            conn.execute('''
                INSERT OR REPLACE INTO circuit_breaker_state
                (source, disabled, disabled_at, break_count, last_break_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, COALESCE((
                    SELECT created_at FROM circuit_breaker_state WHERE source = ?
                ), ?), ?)
            ''', (
                source,
                int(cb.get('disabled', False)),
                cb.get('disabled_at', 0),
                cb.get('break_count', 0),
                int(time.time()) if cb.get('disabled') else 0,
                source,
                now,
                now
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to save circuit breaker state: {e}")
            # Non-fatal; state still in memory

    async def _fetch_with_timeout(self, coro, timeout_secs: float, source: str):
        """
        Execute a coroutine with strict timeout enforcement.
        Raises asyncio.TimeoutError if timeout exceeded.
        """
        try:
            return await asyncio.wait_for(coro, timeout=timeout_secs)
        except asyncio.TimeoutError:
            logger.debug(f"{source} exceeded timeout budget ({timeout_secs:.1f}s)")
            raise

    def _is_circuit_broken(self, source: str) -> bool:
        """Check if source is currently circuit broken with exponential cooldown."""
        cb = self.circuit_breaker.get(source, {})
        if not cb.get('disabled'):
            return False

        # Get cooldown based on break count (exponential backoff)
        break_count = cb.get('break_count', 0)
        cooldown_secs = self._get_exponential_cooldown(break_count)

        elapsed = time.time() - cb.get('disabled_at', 0)
        if elapsed > cooldown_secs:
            cb['disabled'] = False
            cooldown_min = cooldown_secs / 60
            logger.info(
                f"Circuit breaker for {source} reset after {cooldown_min:.0f}min cooldown "
                f"(break #{break_count})"
            )
            self._save_circuit_breaker_state(source)
            return False

        return True

    def get_rolling_window_stats(self) -> dict:
        """
        Get rolling window statistics for all sources.
        Used for monitoring and debugging source health.
        """
        stats = {}
        for source in self.source_attempts:
            attempts = self.source_attempts[source]
            if attempts:
                failures = sum(1 for _, s in attempts if not s)
                success_rate = (len(attempts) - failures) / len(attempts)
            else:
                success_rate = 0.0
            
            stats[source] = {
                'attempts_in_window': len(self.source_attempts[source]),
                'success_rate': round(success_rate, 3),
                'failure_rate': round(1 - success_rate, 3),
            }
        return stats

    def _update_source_stats(self, source: str, success: bool) -> None:
        """
        Track attempt success and update:
        1. Source attempt history (1-hour rolling window)
        2. Circuit breaker failure rate (>90% over 20+ attempts)
        3. Increment break_count and persist on circuit break
        """
        now = time.time()
        self.source_attempts[source].append((now, success))

        # Keep only last 1 hour of attempts (rolling window)
        cutoff = now - 3600  # 1 hour
        self.source_attempts[source] = [
            (ts, s) for ts, s in self.source_attempts[source]
            if ts > cutoff
        ]

        # Check if circuit should break (>90% failure over 20+ attempts)
        attempts = self.source_attempts[source]
        if len(attempts) >= 20:
            failures = sum(1 for _, s in attempts if not s)
            failure_rate = failures / len(attempts)

            if failure_rate > 0.9 and not self.circuit_breaker[source]['disabled']:
                self.circuit_breaker[source]['disabled'] = True
                self.circuit_breaker[source]['disabled_at'] = now
                self.circuit_breaker[source]['break_count'] = self.circuit_breaker[source].get('break_count', 0) + 1
                break_count = self.circuit_breaker[source]['break_count']
                cooldown_secs = self._get_exponential_cooldown(break_count)
                cooldown_min = cooldown_secs / 60
                logger.warning(
                    f"Circuit breaker triggered for {source}: "
                    f"{failure_rate:.1%} failure rate over {len(attempts)} attempts. "
                    f"Break #{break_count}, cooldown {cooldown_min:.0f} min"
                )
                self._save_circuit_breaker_state(source)

    def _get_source_rank(self, source: str) -> float:
        """
        Rank source by success rate and latency.

        Score = (success_rate × 0.7) + (1 - normalized_latency × 0.3)
        Range: 0.0 to 1.0 (higher is better)
        """
        attempts = self.source_attempts[source]
        if not attempts:
            return 0.5  # Default score for uninitialized sources

        successes = sum(1 for _, s in attempts if s)
        success_rate = successes / len(attempts)

        # Normalize latency to 0-1 (assume max 500ms = 1.0)
        latency_ms = self.source_latency_ewma[source]
        normalized_latency = min(latency_ms / 500.0, 1.0)

        score = (success_rate * 0.7) + ((1.0 - normalized_latency) * 0.3)
        return score

    def _get_sources_ordered(self) -> list:
        """
        Return list of sources ranked by success rate and latency.

        Pool is always first (if not circuit-broken) — fastest source (dict read, no HTTP).
        DexScreener is second (if not circuit-broken) — first fallback priority.
        Jupiter/Birdeye ranked by success rate + latency.
        Excludes circuit-broken sources.
        Always includes stale fallback (not in returned list).
        """
        active_sources = []

        # Pool: synchronous dict read, always first if available
        if not self._is_circuit_broken('pool'):
            active_sources.append(('pool', 1.0))

        # DexScreener: pinned as first fallback option
        if not self._is_circuit_broken('dexscreener'):
            active_sources.append(('dexscreener', 1.0))

        # Jupiter/Birdeye: ranked by success + latency
        for source in ['jupiter', 'birdeye']:
            if not self._is_circuit_broken(source):
                rank = self._get_source_rank(source)
                active_sources.append((source, rank))

        # Sort remaining sources (excluding pool & dexscreener) by rank descending
        if len(active_sources) > 2:
            # Keep pool and dexscreener pinned, sort the rest
            pinned = active_sources[:2]
            rest = active_sources[2:]
            rest.sort(key=lambda x: x[1], reverse=True)
            active_sources = pinned + rest

        return [source for source, _ in active_sources]

    def _update_latency_ewma(self, source: str, latency_ms: float) -> None:
        """Update EWMA latency for a source using 0.8 weight to previous."""
        EWMA_ALPHA = 0.8
        prev = self.source_latency_ewma[source]

        if prev == 0.0:
            # First measurement
            self.source_latency_ewma[source] = latency_ms
        else:
            self.source_latency_ewma[source] = (EWMA_ALPHA * prev) + ((1.0 - EWMA_ALPHA) * latency_ms)

    async def get_token_price(self, mint: str, cache_type: str = 'hot') -> TokenPrice:
        """
        Get token price with multi-source fallback and 3-second total budget.

        Sources ranked by success rate + latency (adaptive ordering).
        Each source has its own timeout budget, and overall budget is capped at 3 seconds.
        Circuit breaker disables failing sources.

        Total budget: 3 seconds across all sources.
        Per-provider budgets: dexscreener=1.2s, jupiter=0.8s, birdeye=1.0s (capped by remaining)
        """
        TOTAL_BUDGET_SECS = 3.0
        fetch_start = time.time()

        # Try in-memory cache (no budget check — free)
        cached = self.cache.get(mint, cache_type)
        if cached:
            return cached

        # Get sources ordered by current success rate + latency
        sources_ordered = self._get_sources_ordered()

        # Try each active source in ranked order
        for source in sources_ordered:
            elapsed = time.time() - fetch_start
            if elapsed >= TOTAL_BUDGET_SECS:
                break

            # Skip if circuit breaker is disabled for this source
            if self._is_circuit_broken(source):
                continue

            # Calculate remaining budget for this provider
            remaining_budget = TOTAL_BUDGET_SECS - elapsed
            provider_timeout = self.provider_timeouts.get(source, 1.0)
            # Use the minimum of provider timeout and remaining budget
            actual_timeout = min(provider_timeout, remaining_budget)

            if actual_timeout <= 0:
                break  # No time left for this provider

            if source == 'pool':
                # Pool: synchronous dict read, no HTTP, no budget consumed
                self.stats['pool_attempted'] += 1
                pool_price = self.pool_price_cache.get(mint)
                if pool_price:
                    self.stats['pool_success'] += 1
                    self._update_source_stats('pool', True)
                    self.cache.set(mint, pool_price)
                    return pool_price
                self.stats['pool_fail'] += 1
                self._update_source_stats('pool', False)

            elif source == 'dexscreener':
                self.stats['dexscreener_attempted'] += 1
                start = time.time()
                try:
                    # Override timeout in DexscreenerClient with actual_timeout
                    dex_price = await self._fetch_with_timeout(
                        DexscreenerClient.get_price(mint),
                        actual_timeout,
                        source
                    )
                    latency_ms = (time.time() - start) * 1000
                    self._update_latency_ewma('dexscreener', latency_ms)

                    if dex_price:
                        self.stats['dexscreener_success'] += 1
                        self._update_source_stats('dexscreener', True)
                        self.cache.set(mint, dex_price)
                        self._store_snapshot(dex_price)
                        return dex_price

                    self.stats['dexscreener_fail'] += 1
                    self._update_source_stats('dexscreener', False)
                except asyncio.TimeoutError:
                    self.stats['dexscreener_fail'] += 1
                    self._update_source_stats('dexscreener', False)
                    logger.debug(f"Dexscreener timeout ({actual_timeout:.1f}s) for {mint}")

            elif source == 'jupiter':
                self.stats['jupiter_attempted'] += 1
                start = time.time()
                try:
                    jup_price = await self._fetch_with_timeout(
                        JupiterClient.get_price(mint),
                        actual_timeout,
                        source
                    )
                    latency_ms = (time.time() - start) * 1000
                    self._update_latency_ewma('jupiter', latency_ms)

                    if jup_price:
                        self.stats['jupiter_success'] += 1
                        self._update_source_stats('jupiter', True)
                        self.cache.set(mint, jup_price)
                        self._store_snapshot(jup_price)
                        return jup_price

                    self.stats['jupiter_fail'] += 1
                    self._update_source_stats('jupiter', False)
                except asyncio.TimeoutError:
                    self.stats['jupiter_fail'] += 1
                    self._update_source_stats('jupiter', False)
                    logger.debug(f"Jupiter timeout ({actual_timeout:.1f}s) for {mint}")

            elif source == 'birdeye':
                self.stats['birdeye_attempted'] += 1
                start = time.time()
                try:
                    loop = asyncio.get_event_loop()
                    birdeye_price = await self._fetch_with_timeout(
                        loop.run_in_executor(
                            self._executor,
                            self._fetch_birdeye_sync,
                            mint
                        ),
                        actual_timeout,
                        source
                    )
                    latency_ms = (time.time() - start) * 1000
                    self._update_latency_ewma('birdeye', latency_ms)

                    if birdeye_price:
                        self.stats['birdeye_success'] += 1
                        self._update_source_stats('birdeye', True)
                        self.cache.set(mint, birdeye_price)
                        self._store_snapshot(birdeye_price)
                        return birdeye_price

                    self.stats['birdeye_fail'] += 1
                    self._update_source_stats('birdeye', False)
                except asyncio.TimeoutError:
                    self.stats['birdeye_fail'] += 1
                    self._update_source_stats('birdeye', False)
                    logger.debug(f"Birdeye timeout ({actual_timeout:.1f}s) for {mint}")

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
