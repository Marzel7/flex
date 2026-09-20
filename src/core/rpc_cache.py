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
from src.utils.db_locking import db_connect, bounded_write_wait

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

    def _table_exists_read_only(self) -> Optional[bool]:
        """Check table existence via a mode=ro connection -- never touches
        the write lane. Returns None (unknown) rather than False on any
        error, so callers can distinguish "confirmed absent" from
        "couldn't check" and fail toward the safe (write-attempting) path."""
        conn = None
        try:
            conn = db_connect(self.db_path, timeout=5, read_only=True)
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rpc_response_cache'"
            ).fetchone()
            conn.close()
            return row is not None
        except Exception:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            return None

    def _ensure_table(self) -> None:
        """
        Idempotent table creation (matches CursorManager pattern).

        WT_OPS write-lane fix: CREATE TABLE IF NOT EXISTS is itself
        classified as a write by _is_write_sql() and queues behind the
        cross-process write lane every time -- including every
        RPCCache(db_path) construction, even for callers that only ever
        intend to read (e.g. count_expired(), and the maintenance runner's
        preflight). Skip the write path entirely once a read-only check
        confirms the table already exists (the overwhelmingly common case
        after the very first run in a DB's lifetime). Falls through to the
        original write-attempting path if the read-only check is
        inconclusive (None) or confirms the table is genuinely missing.
        """
        if self._table_exists_read_only() is True:
            return

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

            # Cache hit: PURE READ. hit_count is UNUSED_LEGACY_METRIC --
            # confirmed zero current callers of get_stats() (its only
            # reader) -- so the prior UPDATE hit_count = hit_count + 1 here
            # was a write on every single cache hit for a value nothing
            # consumes. get() is documented above as "one of the
            # highest-frequency call sites in the whole pipeline," so this
            # was a real, avoidable contributor to wt_ops write-lane
            # contention. The hit_count column itself is retained
            # unchanged (schema, cleanup, TTL, set() all untouched) in case
            # a future consumer wants it -- it will simply stop
            # incrementing on read.
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

    # Maintenance-path acquisition budget: an order of magnitude below the
    # project's general-purpose 60s write-wait default. Maintenance is
    # optional housekeeping and must yield to production writers -- it must
    # never queue for minutes the way a critical-ingestion write legitimately
    # might. This bounds BOTH the cross-process lane wait (via
    # bounded_write_wait) AND the connection-level busy_timeout, and the
    # retry loop's total wall-clock is separately capped below so no
    # combination of attempts can silently multiply back up to minutes.
    MAINTENANCE_LANE_TIMEOUT_SECONDS = 3.0
    MAINTENANCE_TOTAL_BUDGET_SECONDS = 10.0

    def cleanup_expired_batch(self, batch_size: int, now: Optional[float] = None,
                               busy_retry_attempts: int = 3) -> int:
        """
        Bounded, production-safe expired-row cleanup.

        WT_OPS_BOUNDED_MAINTENANCE: cleanup_expired() above is a single
        unbounded bulk DELETE and must NOT be wired into any scheduled
        maintenance path against the live backlog -- rows in this table
        average ~692KB, so an unbounded DELETE against a multi-thousand-row
        backlog can generate hundreds of MB to GB of WAL in one transaction.
        This bounded variant selects and deletes at most `batch_size` expired
        keys in ONE small transaction, returns the exact count deleted, and
        is safe to call repeatedly (idempotent -- re-selects the current
        qualifying set each call, no offset/cursor state).

        Write-lane budget fix: the prior implementation opened a connection
        with a 60s SQLite busy_timeout and retried up to 5 times with linear
        backoff on EACH of its two phases (SELECT, then DELETE) -- a
        theoretical worst case of roughly 2 x 5 x 60s = 600s, which matches
        the >6 minute stall observed in production. This version uses a
        short (few-second) connection timeout, wraps the DELETE phase in
        bounded_write_wait() so the cross-process lane wait itself is
        capped independent of the process-wide 60s default used by other
        (non-maintenance) writers, and enforces a hard overall wall-clock
        budget across all retries combined -- once exceeded, this returns 0
        immediately rather than attempting another retry.

        Expired predicate is unchanged and authoritative:
            cached_at + ttl_seconds <= now

        Does not touch TTL values, get()/set() lookup semantics, or provider
        fallback behavior in any way. Does not change the process-wide write
        wait timeout used by production writers -- bounded_write_wait() is
        context-local to this call only.

        Returns 0 (never raises) on any connection failure, empty result,
        exhausted retry budget, or overall time-budget exhaustion --
        callers should treat 0 as "nothing deleted this call, safe to retry
        next invocation," not as an error signal by itself.
        """
        if batch_size <= 0:
            return 0

        cutoff = now if now is not None else time.time()
        call_start = time.monotonic()
        conn = None
        try:
            conn = db_connect(self.db_path, timeout=self.MAINTENANCE_LANE_TIMEOUT_SECONDS)
            conn.execute(f"PRAGMA busy_timeout = {int(self.MAINTENANCE_LANE_TIMEOUT_SECONDS * 1000)}")

            cursor = conn.cursor()

            attempt = 0
            while True:
                if time.monotonic() - call_start >= self.MAINTENANCE_TOTAL_BUDGET_SECONDS:
                    logger.info("[RPC_CACHE] cleanup_expired_batch() SELECT phase: "
                                "maintenance time budget exhausted, skipping this run")
                    conn.close()
                    return 0
                try:
                    # LIMIT bounds the number of rows returned, not the work
                    # SQLite may perform to find them.  On the production
                    # cache (large response_json overflow payloads and no
                    # expiry expression index), an eligibility scan can run
                    # far beyond this method's nominal wall-clock budget.
                    # Interrupt VM execution at the same absolute deadline so
                    # maintenance fails closed instead of pinning a one-shot
                    # process indefinitely.  This is connection-local and is
                    # cleared immediately after the SELECT.
                    query_deadline = call_start + self.MAINTENANCE_TOTAL_BUDGET_SECONDS
                    conn.set_progress_handler(
                        lambda: 1 if time.monotonic() >= query_deadline else 0,
                        1000,
                    )
                    with bounded_write_wait(self.MAINTENANCE_LANE_TIMEOUT_SECONDS):
                        try:
                            cursor.execute(
                                "SELECT cache_key FROM rpc_response_cache "
                                "WHERE cached_at + ttl_seconds <= ? LIMIT ?",
                                (cutoff, batch_size),
                            )
                            keys = [r[0] for r in cursor.fetchall()]
                        finally:
                            conn.set_progress_handler(None, 0)
                    break
                except sqlite3.OperationalError as e:
                    attempt += 1
                    if "interrupted" in str(e).lower():
                        logger.info(
                            "[RPC_CACHE] cleanup_expired_batch() SELECT phase: "
                            "query deadline reached, skipping this run"
                        )
                        conn.close()
                        return 0
                    if "locked" in str(e).lower() or "busy" in str(e).lower():
                        if attempt >= busy_retry_attempts:
                            logger.info(
                                f"[RPC_CACHE] cleanup_expired_batch() SELECT: write lane busy, "
                                f"skipping this run after {attempt} bounded attempts"
                            )
                            conn.close()
                            return 0
                        time.sleep(0.2 * attempt)
                        continue
                    raise

            if not keys:
                conn.close()
                return 0

            attempt = 0
            while True:
                if time.monotonic() - call_start >= self.MAINTENANCE_TOTAL_BUDGET_SECONDS:
                    logger.info("[RPC_CACHE] cleanup_expired_batch() DELETE phase: "
                                "maintenance time budget exhausted, skipping this run")
                    try:
                        conn.rollback()
                    except sqlite3.OperationalError:
                        pass
                    conn.close()
                    return 0
                try:
                    with bounded_write_wait(self.MAINTENANCE_LANE_TIMEOUT_SECONDS):
                        cursor.execute("BEGIN IMMEDIATE")
                        cursor.executemany(
                            "DELETE FROM rpc_response_cache WHERE cache_key = ? "
                            "AND cached_at + ttl_seconds <= ?",
                            [(k, cutoff) for k in keys],
                        )
                        deleted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else len(keys)
                        conn.commit()
                    conn.close()
                    return deleted
                except sqlite3.OperationalError as e:
                    try:
                        conn.rollback()
                    except sqlite3.OperationalError:
                        pass
                    attempt += 1
                    if "locked" in str(e).lower() or "busy" in str(e).lower():
                        if attempt >= busy_retry_attempts:
                            logger.info(
                                f"[RPC_CACHE] cleanup_expired_batch() DELETE: write lane busy, "
                                f"skipping this run after {attempt} bounded attempts "
                                f"(MAINTENANCE_SKIPPED_WRITE_LANE_BUSY)"
                            )
                            conn.close()
                            return 0
                        time.sleep(0.2 * attempt)
                        continue
                    logger.warning(f"[RPC_CACHE] cleanup_expired_batch() DELETE failed: {e}")
                    conn.close()
                    return 0

        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            logger.warning(f"[RPC_CACHE] cleanup_expired_batch() failed: {e}")
            return 0

    def count_expired(self, now: Optional[float] = None) -> int:
        """
        Read-only count of currently-expired rows. Never raises.

        WT_OPS write-lane fix: deliberately does NOT go through
        self._get_conn() -- that path (and RPCCache.__init__ -> _ensure_table())
        opens a normal read-write connection whose very first statement is a
        CREATE TABLE IF NOT EXISTS, which _is_write_sql() classifies as a
        write and therefore queues behind the cross-process write lane even
        though this method never mutates anything. A genuinely read-only
        `mode=ro` connection (db_connect(..., read_only=True)) never touches
        the write lane at all, matching this method's actual semantics.
        """
        cutoff = now if now is not None else time.time()
        conn = None
        try:
            conn = db_connect(self.db_path, timeout=10, read_only=True)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM rpc_response_cache WHERE cached_at + ttl_seconds <= ?",
                (cutoff,),
            )
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else 0
        except Exception as e:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            logger.warning(f"[RPC_CACHE] count_expired() failed: {e}")
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
