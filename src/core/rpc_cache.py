#!/usr/bin/env python3
"""
SQLite-backed RPC Response Cache for Phase 2 deployment.

Caches raw JSON-RPC responses to avoid re-fetching identical data within
validity windows (TTL). Reduces redundant API calls by 30-35%.

Follows Phase 1 CursorManager pattern exactly:
- Same SQLite WAL mode pragmas
- Same graceful error handling (all methods return None on exception)
- Same _get_conn() and _ensure_table() pattern
- Zero external dependencies

Usage:
    cache = RPCCache(db_path)

    # Check cache before RPC call
    key = RPCCache.make_key_get_transaction(signature)
    cached = cache.get(key)
    if cached:
        return cached  # Hit!

    # Make live RPC call
    result = await rpc.get_transaction(signature)

    # Store result
    cache.set(key, result, "getTransaction")

TTLs by method:
    getTransaction                          → 86400s (24h, immutable)
    getSignaturesForAddress (with before)   → 3600s  (1h, stable pages)
    getSignaturesForAddress (first page)    → 300s   (5min, new sigs)
    helius_enhanced_addresses_transactions  → 3600s  (1h, append-only)
    helius_enhanced_transactions_batch      → 86400s (24h, immutable)
"""

import sqlite3
import json
import time
import logging
import hashlib
from typing import Optional, Dict, List
from datetime import datetime
from src.utils.db_locking import db_connect

logger = logging.getLogger(__name__)


class RPCCache:
    """SQLite-backed cache for RPC responses."""

    # TTL values in seconds (by method)
    TTLS = {
        "getTransaction": 86400,                             # 24h
        "getSignaturesForAddress": 3600,                     # 1h (historical pages)
        "helius_enhanced_addresses_transactions": 3600,      # 1h
        "helius_enhanced_transactions_batch": 86400,         # 24h
    }

    def __init__(self, db_path: str):
        """Initialize cache with database path."""
        self.db_path = db_path
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        """Get database connection with WAL mode (matches CursorManager pattern)."""
        try:
            conn = db_connect(self.db_path, timeout=60)
            conn.execute("PRAGMA busy_timeout = 60000")
            return conn
        except Exception as e:
            logger.error(f"[RPC_CACHE] Failed to get connection: {e}")
            return None

    def _ensure_table(self) -> None:
        """Idempotent table creation (matches CursorManager pattern)."""
        conn = None
        try:
            conn = self._get_conn()
            if conn is None:
                return

            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rpc_response_cache (
                    cache_key        TEXT PRIMARY KEY,
                    response_json    TEXT NOT NULL,
                    method           TEXT NOT NULL,
                    cached_at        REAL NOT NULL,
                    ttl_seconds      INTEGER NOT NULL,
                    hit_count        INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            logger.error(f"[RPC_CACHE] Failed to ensure table: {e}")

    def get(self, cache_key: str) -> Optional[dict]:
        """
        Look up cached response by key.

        Returns parsed dict on hit, None on miss or expired.
        Expired entries are lazily deleted on miss.
        Never raises — returns None on any exception.
        """
        # X78.0 -- conn was not declared before the try (unlike set(), which
        # already does this correctly). If self._get_conn() itself somehow
        # raised before completing assignment, the except block's
        # `if conn is not None` would hit UnboundLocalError, masking the
        # real exception and skipping cleanup. Called on every RPC request
        # during extraction -- one of the highest-frequency call sites in
        # the whole pipeline.
        conn = None
        try:
            conn = self._get_conn()
            if conn is None:
                return None

            cursor = conn.cursor()
            cursor.execute(
                "SELECT response_json, cached_at, ttl_seconds FROM rpc_response_cache WHERE cache_key = ?",
                (cache_key,)
            )
            row = cursor.fetchone()

            if not row:
                conn.close()
                return None

            response_json, cached_at, ttl_seconds = row
            now = time.time()

            # Check if expired
            if now > cached_at + ttl_seconds:
                # Lazy expiry: delete expired entry and return miss
                conn.execute(
                    "DELETE FROM rpc_response_cache WHERE cache_key = ?",
                    (cache_key,)
                )
                conn.commit()
                conn.close()
                return None

            # Cache hit: increment hit count
            conn.execute(
                "UPDATE rpc_response_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
                (cache_key,)
            )
            conn.commit()
            conn.close()

            # Parse and return
            try:
                return json.loads(response_json)
            except json.JSONDecodeError as e:
                logger.error(f"[RPC_CACHE] Failed to parse cached JSON: {e}")
                return None

        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            logger.error(f"[RPC_CACHE] get() failed for {cache_key[:40]}: {e}")
            return None

    def set(self, cache_key: str, response: dict, method: str) -> None:
        """
        Store a response in cache.

        TTL is derived from method via TTLS dict.
        For getSignaturesForAddress, check if it's the first page (before=none)
        and use 5min TTL instead of 1h.

        Uses INSERT OR REPLACE for concurrent safety under WAL.
        Never raises — logs warning on any exception.
        """
        conn = None
        try:
            # Determine TTL: special case for first page of getSignaturesForAddress
            ttl_seconds = self.TTLS.get(method, 3600)  # Default 1h

            if method == "getSignaturesForAddress" and ":none:" in cache_key:
                # First page (no cursor) — refresh frequently
                ttl_seconds = 300  # 5 minutes

            conn = self._get_conn()
            if conn is None:
                return

            response_json = json.dumps(response)
            now = time.time()

            conn.execute(
                """INSERT OR REPLACE INTO rpc_response_cache
                   (cache_key, response_json, method, cached_at, ttl_seconds, hit_count)
                   VALUES (?, ?, ?, ?, ?, 0)""",
                (cache_key, response_json, method, now, ttl_seconds)
            )
            conn.commit()
            conn.close()

        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            logger.warning(f"[RPC_CACHE] set() failed for {cache_key[:40]}: {e}")

    def invalidate(self, cache_key: str) -> None:
        """Explicitly invalidate a cache entry."""
        conn = None
        try:
            conn = self._get_conn()
            if conn is None:
                return

            conn.execute(
                "DELETE FROM rpc_response_cache WHERE cache_key = ?",
                (cache_key,)
            )
            conn.commit()
            conn.close()

        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            logger.warning(f"[RPC_CACHE] invalidate() failed: {e}")

    def cleanup_expired(self) -> int:
        """
        Bulk delete all expired rows.
        Call periodically (e.g., hourly) to reclaim space.
        Returns count of deleted rows.
        """
        conn = None
        try:
            conn = self._get_conn()
            if conn is None:
                return 0

            now = time.time()
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM rpc_response_cache WHERE cached_at + ttl_seconds <= ?",
                (now,)
            )
            deleted = cursor.rowcount
            conn.commit()
            conn.close()

            if deleted > 0:
                logger.info(f"[RPC_CACHE] Cleaned up {deleted} expired entries")

            return deleted

        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            logger.warning(f"[RPC_CACHE] cleanup_expired() failed: {e}")
            return 0

    def get_stats(self) -> dict:
        """
        Return cache statistics for monitoring.

        Returns:
            {
                'total_entries': int,
                'total_hits': int,
                'hit_rate_percent': float,
                'entries_by_method': dict,
                'estimated_size_bytes': int
            }
        """
        conn = None
        try:
            conn = self._get_conn()
            if conn is None:
                return {'error': 'No database connection'}

            cursor = conn.cursor()

            # Total entries and hits
            cursor.execute(
                "SELECT COUNT(*), COALESCE(SUM(hit_count), 0) FROM rpc_response_cache"
            )
            total_entries, total_hits = cursor.fetchone() or (0, 0)

            # Entries by method
            cursor.execute(
                "SELECT method, COUNT(*) FROM rpc_response_cache GROUP BY method"
            )
            entries_by_method = {row[0]: row[1] for row in cursor.fetchall()}

            # Estimated size (rough: avg 2KB per entry)
            cursor.execute(
                "SELECT AVG(LENGTH(response_json)) FROM rpc_response_cache"
            )
            avg_response_size = cursor.fetchone()[0] or 0
            estimated_size = total_entries * (64 + avg_response_size)  # 64 bytes overhead

            conn.close()

            hit_rate = (total_hits / (total_entries + 1) * 100) if total_entries > 0 else 0.0

            return {
                'total_entries': total_entries,
                'total_hits': int(total_hits),
                'hit_rate_percent': hit_rate,
                'entries_by_method': entries_by_method,
                'estimated_size_bytes': estimated_size,
            }

        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            logger.error(f"[RPC_CACHE] get_stats() failed: {e}")
            return {'error': str(e)}

    # ===== Cache Key Builders (Static Methods) =====

    @staticmethod
    def make_key_get_transaction(signature: str) -> str:
        """Cache key for getTransaction RPC call (immutable, 24h TTL)."""
        return f"getTransaction:{signature}"

    @staticmethod
    def make_key_get_signatures(address: str, before: Optional[str], limit: int) -> str:
        """
        Cache key for getSignaturesForAddress RPC call (pagination page).

        Each page (identified by address + before_cursor + limit) has its own entry.
        First page (before=None) gets 5min TTL (for new sigs).
        Historical pages get 1h TTL (stable).
        """
        before_part = before if before else "none"
        return f"getSignaturesForAddress:{address}:{before_part}:{limit}"

    @staticmethod
    def make_key_helius_addr_txs(address: str, before: Optional[str], limit: int) -> str:
        """
        Cache key for Helius Enhanced API addresses/transactions call (1h TTL).
        Same pagination logic as getSignaturesForAddress.
        """
        before_part = before if before else "none"
        return f"helius_addr_txs:{address}:{before_part}:{limit}"

    @staticmethod
    def make_key_helius_batch(signatures: List[str]) -> str:
        """
        Cache key for Helius batch transaction details (24h TTL, immutable).

        Uses MD5 hash of sorted signatures to create a fixed-length key
        (batch size can be large, so we hash to avoid huge keys).
        """
        try:
            # Sort for determinism, hash for fixed length
            sig_str = ",".join(sorted(signatures))
            h = hashlib.md5(sig_str.encode()).hexdigest()[:16]
            return f"helius_batch_txs:{h}"
        except Exception as e:
            logger.error(f"[RPC_CACHE] make_key_helius_batch() failed: {e}")
            return f"helius_batch_txs:error"
