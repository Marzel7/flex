#!/usr/bin/env python3
"""
Creator Funding Graph Cache - 6th Optimization Layer

Purpose:
  Avoid re-scanning creator funding when the same creator launches multiple tokens.
  Caches creator → funder relationships with configurable TTL.

Architecture:
  Token detected → Check creator cache → Return cached funders if valid
  If cache miss/expired → Run creator extractor → Store results → Return

Performance:
  Lookup: O(1) - direct hash lookup + single index query
  Store: O(N) where N = funder count (typically 20-100)
  Cleanup: O(K) where K = expired entries (batch operation)

Expected Impact:
  • 30-50% reduction in creator funding scans (for multi-token creators)
  • Typical dataset: 200 tokens/day, 120 unique creators → 40% skip rate
  • Combined with other layers: 90-97% total Helius reduction

Database:
  Table: creator_funding_graph (creator_address, funder_address) → relationships
  Indexes: creator, last_seen (for TTL cleanup)
  Views: stats, top_creators, frequent_funders
"""

import sqlite3
import logging
import time
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


class CreatorFundingGraphCache:
    """
    Persistent cache for creator → funder relationships.

    Usage:
        cache = CreatorFundingGraphCache(db_path, ttl_hours=24)

        # Check cache
        funders = cache.get_cached_funders(creator)
        if funders:
            return funders  # Cache hit

        # Cache miss - run extraction
        funders = extract_creator_funders(creator)
        cache.store_funders(creator, funders)
        return funders
    """

    def __init__(self, db_path: str, ttl_hours: int = 24):
        """
        Initialize creator funding graph cache.

        Args:
            db_path: Path to SQLite database
            ttl_hours: Time-to-live for cached entries (default 24 hours)
        """
        self.db_path = db_path
        self.ttl_hours = ttl_hours
        self.ttl_seconds = ttl_hours * 3600

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with optimized pragmas."""
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def get_cached_funders(self, creator_address: str) -> Optional[Dict[str, Any]]:
        """
        Get cached funders for a creator if cache is still valid.

        Args:
            creator_address: Creator wallet address

        Returns:
            Dict with keys: {funder_address: {sol, tx_count}, ...}
            Or None if cache miss or expired

        Example:
            cached = cache.get_cached_funders('creator_123')
            if cached:
                for funder, data in cached.items():
                    print(f"Funder: {funder}, SOL: {data['sol']}, TXs: {data['tx_count']}")
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            # Check if creator has cached funders
            cur.execute(
                """
                SELECT funder_address, inbound_sol, inbound_tx_count, last_seen
                FROM creator_funding_graph
                WHERE creator_address = ?
                ORDER BY inbound_sol DESC
                """,
                (creator_address,),
            )
            rows = cur.fetchall()
            conn.close()

            if not rows:
                logger.debug(f"[CREATOR_CACHE] Miss: {creator_address[:16]}... (no entries)")
                return None

            # Check TTL: if any entry is stale, treat entire cache as expired
            now = time.time()
            for row in rows:
                funder, sol, tx_count, last_seen_str = row
                if last_seen_str:
                    try:
                        last_seen = datetime.fromisoformat(last_seen_str).timestamp()
                        age_seconds = now - last_seen
                        if age_seconds > self.ttl_seconds:
                            logger.info(
                                f"[CREATOR_CACHE] Expired: {creator_address[:16]}... "
                                f"(age={age_seconds/3600:.1f}h, TTL={self.ttl_hours}h)"
                            )
                            return None
                    except Exception:
                        pass

            # All entries are fresh - return cached data
            cached = {
                funder: {
                    "sol": sol,
                    "tx_count": tx_count,
                }
                for funder, sol, tx_count, _ in rows
            }
            logger.info(
                f"[CREATOR_CACHE] ✅ Hit: {creator_address[:16]}... "
                f"({len(cached)} funders cached)"
            )
            return cached

        except Exception as e:
            logger.warning(f"[CREATOR_CACHE] Lookup failed: {e}")
            return None

    def store_funders(
        self,
        creator_address: str,
        funders: Dict[str, Dict[str, Any]],
    ) -> bool:
        """
        Store creator → funder relationships in cache.

        Args:
            creator_address: Creator wallet address
            funders: Dict of {funder_address: {sol: float, tx_count: int, ...}}

        Returns:
            True if successful, False otherwise

        Example:
            funders = {
                'funder_123': {'sol': 1.5, 'tx_count': 3},
                'funder_456': {'sol': 0.8, 'tx_count': 2},
            }
            cache.store_funders('creator_123', funders)
        """
        if not funders:
            logger.debug(f"[CREATOR_CACHE] No funders to store for {creator_address[:16]}...")
            return True

        try:
            conn = self._get_conn()
            cur = conn.cursor()

            now = datetime.utcnow().isoformat()

            for funder_address, funder_data in funders.items():
                if not isinstance(funder_data, dict):
                    continue

                inbound_sol = funder_data.get("sol", 0.0)
                inbound_tx_count = funder_data.get("tx_count", 1)

                # Insert or update (replace on conflict)
                cur.execute(
                    """
                    INSERT OR REPLACE INTO creator_funding_graph
                    (creator_address, funder_address, first_seen, last_seen, inbound_sol, inbound_tx_count)
                    VALUES (?, ?, COALESCE(
                        (SELECT first_seen FROM creator_funding_graph
                         WHERE creator_address = ? AND funder_address = ?),
                        ?
                    ), ?, ?, ?)
                    """,
                    (
                        creator_address,
                        funder_address,
                        creator_address,
                        funder_address,
                        now,
                        now,
                        float(inbound_sol),
                        int(inbound_tx_count),
                    ),
                )

            conn.commit()
            conn.close()

            logger.debug(
                f"[CREATOR_CACHE] Stored: {creator_address[:16]}... "
                f"({len(funders)} funders)"
            )
            return True

        except Exception as e:
            logger.warning(f"[CREATOR_CACHE] Store failed: {e}")
            return False

    def get_stats(self, hours: int = 24) -> Dict[str, Any]:
        """
        Get cache statistics for a time window.

        Args:
            hours: Look back window (default 24)

        Returns:
            Dict with cache statistics

        Example:
            stats = cache.get_stats(hours=24)
            print(f"Creators cached: {stats['creators_cached']}")
            print(f"Total relationships: {stats['total_relationships']}")
            print(f"Cache hit rate: {stats['hit_rate']}%")
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            # Get stats from view
            cur.execute(
                """
                SELECT
                    creators_cached,
                    total_relationships,
                    total_inbound_sol,
                    total_transactions,
                    avg_inbound_sol,
                    avg_tx_count
                FROM v_creator_graph_stats_24h
                """
            )
            row = cur.fetchone()

            if not row:
                conn.close()
                return {
                    "creators_cached": 0,
                    "total_relationships": 0,
                    "total_inbound_sol": 0.0,
                    "total_transactions": 0,
                    "avg_inbound_sol": 0.0,
                    "avg_tx_count": 0.0,
                }

            (
                creators,
                relationships,
                total_sol,
                total_txs,
                avg_sol,
                avg_tx,
            ) = row

            # Calculate hit rate from metrics table (if available)
            hit_rate = 0.0
            try:
                cur.execute(
                    """
                    SELECT
                        ROUND(100.0 * SUM(creator_cache_hit) / NULLIF(COUNT(*), 0), 1)
                    FROM wallet_scan_metrics
                    WHERE created_at >= datetime('now', '-24 hours')
                    """
                )
                hit_row = cur.fetchone()
                if hit_row and hit_row[0]:
                    hit_rate = hit_row[0]
            except Exception:
                pass

            conn.close()

            return {
                "creators_cached": creators or 0,
                "total_relationships": relationships or 0,
                "total_inbound_sol": float(total_sol or 0.0),
                "total_transactions": total_txs or 0,
                "avg_inbound_sol": float(avg_sol or 0.0),
                "avg_tx_count": float(avg_tx or 0.0),
                "hit_rate": hit_rate,
            }

        except Exception as e:
            logger.warning(f"[CREATOR_CACHE] Stats query failed: {e}")
            return {}

    def get_top_creators(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get creators with most funders (most active/suspicious).

        Args:
            limit: Number of results (default 20)

        Returns:
            List of creator statistics
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            cur.execute(
                """
                SELECT creator_address, funder_count, total_inbound_sol, total_tx_count, last_updated
                FROM v_creator_graph_top_creators
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            conn.close()

            return [
                {
                    "creator": row[0],
                    "funder_count": row[1],
                    "total_sol": row[2],
                    "total_txs": row[3],
                    "last_updated": row[4],
                }
                for row in rows
            ]

        except Exception as e:
            logger.warning(f"[CREATOR_CACHE] Top creators query failed: {e}")
            return []

    def get_frequent_funders(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Get funders that appear across many creators (coordinated funding signal).

        Args:
            limit: Number of results (default 20)

        Returns:
            List of funder statistics
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            cur.execute(
                """
                SELECT funder_address, creator_count, total_inbound_sol, total_tx_count, last_updated
                FROM v_creator_graph_frequent_funders
                LIMIT ?
                """,
                (limit,),
            )
            rows = cur.fetchall()
            conn.close()

            return [
                {
                    "funder": row[0],
                    "creator_count": row[1],
                    "total_sol": row[2],
                    "total_txs": row[3],
                    "last_updated": row[4],
                }
                for row in rows
            ]

        except Exception as e:
            logger.warning(f"[CREATOR_CACHE] Frequent funders query failed: {e}")
            return []

    def cleanup_expired(self) -> int:
        """
        Remove expired cache entries (older than TTL).

        Returns:
            Number of entries deleted

        Example:
            deleted = cache.cleanup_expired()
            print(f"Cleaned up {deleted} expired entries")
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            cutoff_time = datetime.utcnow() - timedelta(hours=self.ttl_hours)
            cutoff_str = cutoff_time.isoformat()

            cur.execute(
                """
                DELETE FROM creator_funding_graph
                WHERE last_seen < ?
                """,
                (cutoff_str,),
            )

            deleted = cur.rowcount
            conn.commit()
            conn.close()

            if deleted > 0:
                logger.info(f"[CREATOR_CACHE] Cleanup: Deleted {deleted} expired entries")

            return deleted

        except Exception as e:
            logger.warning(f"[CREATOR_CACHE] Cleanup failed: {e}")
            return 0

    def estimate_credits_saved(self) -> Dict[str, Any]:
        """
        Estimate Helius credits saved by cache hits.

        Assumptions:
          • Creator scan: ~100-200 credits
          • Each cache hit avoids 1 scan
          • Average: 150 credits per hit

        Returns:
            Dict with savings estimate
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()

            cur.execute(
                """
                SELECT SUM(creator_cache_hit)
                FROM wallet_scan_metrics
                WHERE creator_cache_hit = 1
                """
            )
            row = cur.fetchone()
            conn.close()

            total_hits = row[0] if row and row[0] else 0
            credits_saved = total_hits * 150  # Average creator scan cost

            return {
                "total_cache_hits": total_hits,
                "estimated_credits_saved": credits_saved,
                "estimated_cost_saved_usd": credits_saved * 0.0001,  # ~$0.0001 per credit
            }

        except Exception as e:
            logger.warning(f"[CREATOR_CACHE] Savings estimate failed: {e}")
            return {
                "total_cache_hits": 0,
                "estimated_credits_saved": 0,
                "estimated_cost_saved_usd": 0.0,
            }


# Convenience functions for integration

_CACHE = None


def initialize_creator_cache(db_path: str, ttl_hours: int = 24) -> CreatorFundingGraphCache:
    """Initialize global creator cache instance."""
    global _CACHE
    _CACHE = CreatorFundingGraphCache(db_path, ttl_hours=ttl_hours)
    return _CACHE


def get_cached_creator_funders(creator_address: str) -> Optional[Dict[str, Any]]:
    """Get cached funders for a creator (uses global cache instance)."""
    if _CACHE is None:
        return None
    return _CACHE.get_cached_funders(creator_address)


def store_creator_funders(
    creator_address: str,
    funders: Dict[str, Dict[str, Any]],
) -> bool:
    """Store creator funders in cache (uses global cache instance)."""
    if _CACHE is None:
        return False
    return _CACHE.store_funders(creator_address, funders)
