"""
RPC Metrics Recorder - Production-grade credit accounting and metrics collection.

Tracks Helius credit usage by section, provider, and method.
Persists all RPC calls to database for cross-process metrics aggregation.
Maintains rolling counters and exposes metrics via HTTP endpoint.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from threading import RLock
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo
import json
import sqlite3
import os

from src.utils.db_locking import db_connect

# ============================================================================
# CREDIT SCHEDULE (from rpc_metrics_config.py)
# ============================================================================

try:
    from src.apis.rpc_metrics_config import CREDIT_SCHEDULE
except ImportError:
    # Fallback if config module not available
    CREDIT_SCHEDULE = {
    # --------
    # Standard RPC Methods (1 credit each)
    # --------
    "getHealth": 1,
    "getClusterNodes": 1,
    "getSystemProgram": 1,
    "getVersion": 1,
    "getSlot": 1,
    "getSlotLeader": 1,
    "getVoteAccounts": 1,
    "getBalance": 1,
    "getAccountInfo": 1,
    "getMultipleAccounts": 1,
    "simulateBundle": 1,
    "getPriorityFeeEstimate": 1,  # Priority Fee API
    "sendTransaction": 0,  # No cost for sending

    # --------
    # Historical/Archival Methods (10 credits each)
    # --------
    "getSignaturesForAddress": 10,
    "getTransaction": 10,
    "getBlock": 10,
    "getBlocks": 10,
    "getBlocksWithLimit": 10,
    "getBlockTime": 10,
    "getInflationReward": 10,
    "getSignatureStatuses": {
        "default": 1,                        # 1 credit without searchTransactionHistory
        "searchTransactionHistory": 10,      # 10 credits with searchTransactionHistory enabled
    },

    # --------
    # Advanced/Expensive Methods
    # --------
    "getProgramAccounts": 10,               # Standard: 10 credits
    "getProgramAccountsV2": 1,              # Paginated alternative: 1 credit
    "getTransactionsForAddress": 100,       # Helius-exclusive: 100 credits (Developer+ only)
    "getDAS": 10,                           # Digital Asset Standard: 10 credits per request

    # --------
    # Helius Enhanced Transactions API (REST pseudo-methods)
    # Used by funder_incoming_extractor.py and similar
    # Source: https://www.helius.dev/docs/billing/credits
    # --------
    "helius_enhanced_addresses_transactions": 100,  # Address feed endpoint: 100 credits per request
    "helius_enhanced_transactions": 10,             # Batch transactions endpoint (POST /v0/transactions): 10 credits per request
    "helius_enhanced_single_transaction": 10,       # Single transaction endpoint: 10 credits

    # --------
    # Streaming (handled separately via bytes)
    # 3 credits per 0.1 MB of uncompressed data
    # --------
    # "laserstream_bytes": Calculated as bytes * STREAMING_CREDITS_PER_BYTE
    # "enhanced_ws_bytes": Calculated as bytes * STREAMING_CREDITS_PER_BYTE
    }

# Streaming credits: 3 credits per 0.1MB = 3 credits per 102400 bytes
STREAMING_CREDITS_PER_BYTE = 3.0 / (0.1 * 1024 * 1024)

# UK timezone for daily reset
UK_TZ = ZoneInfo("Europe/London")


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class RequestRecord:
    """Single RPC request record"""
    timestamp: float
    section: str
    provider: str
    method: str
    mode: str
    status_code: int
    latency_ms: float
    retries: int
    source_file: str = "unknown"  # File/process making the call (e.g., pumpfun_curve_listener, creator_outgoing_extractor)
    bytes_in: int = 0
    bytes_out: int = 0
    credits: int = 0
    error: Optional[str] = None
    cache_action: str = "none"
    credits_saved: int = 0


@dataclass
class SectionStats:
    """Aggregated stats for a section"""
    requests: int = 0
    errors: int = 0
    rate_limits_429: int = 0
    latencies: deque = field(default_factory=lambda: deque(maxlen=1000))
    credits_total: int = 0
    credits_by_method: Dict[str, int] = field(default_factory=dict)
    credits_by_provider: Dict[str, int] = field(default_factory=dict)

    @property
    def avg_latency(self) -> float:
        if not self.latencies:
            return 0.0
        return sum(self.latencies) / len(self.latencies)

    @property
    def p95_latency(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[idx] if idx < len(sorted_lat) else sorted_lat[-1]


# ============================================================================
# DATABASE PERSISTENCE FOR CROSS-PROCESS METRICS AGGREGATION
# ============================================================================

DB_PATH = os.getenv("RPC_METRICS_DB", "flex_complete_database.db")

def _rpc_metrics_schema_ready() -> bool:
    required_tables = {"rpc_metrics", "rpc_metrics_state"}
    required_indexes = {
        "idx_rpc_metrics_timestamp",
        "idx_rpc_metrics_source_file",
        "idx_rpc_metrics_method",
        "idx_rpc_metrics_cache_action",
        "idx_rpc_metrics_optimization_layer",
    }
    required_columns = {
        "timestamp",
        "section",
        "provider",
        "method",
        "status_code",
        "latency_ms",
        "credits",
        "mode",
        "retries",
        "source_file",
        "error",
        "process_pid",
        "cache_action",
        "credits_saved",
        "optimization_layer",
        "recorded_at",
    }
    conn = db_connect(DB_PATH, timeout=5, read_only=True)
    try:
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'index')"
        ).fetchall()
        tables = {row[0] for row in rows if row[1] == "table"}
        indexes = {row[0] for row in rows if row[1] == "index"}
        if not (required_tables <= tables and required_indexes <= indexes):
            return False
        columns = {row[1] for row in conn.execute("PRAGMA table_info(rpc_metrics)").fetchall()}
        return required_columns <= columns
    finally:
        conn.close()

def _ensure_rpc_metrics_table():
    """Create rpc_metrics table if it doesn't exist, and migrate columns if needed"""
    try:
        if _rpc_metrics_schema_ready():
            return
    except Exception:
        pass
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rpc_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                section TEXT NOT NULL,
                provider TEXT NOT NULL,
                method TEXT NOT NULL,
                status_code INTEGER,
                latency_ms REAL,
                credits INTEGER,
                mode TEXT DEFAULT 'realtime',
                retries INTEGER DEFAULT 0,
                source_file TEXT DEFAULT 'unknown',
                error TEXT,
                process_pid INTEGER,
                cache_action TEXT DEFAULT 'none',
                credits_saved INTEGER DEFAULT 0,
                optimization_layer TEXT DEFAULT 'none',
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create state table for recorder persistence
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rpc_metrics_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migrate existing tables: add missing columns if they don't exist
        cursor = conn.execute("PRAGMA table_info(rpc_metrics)")
        existing_columns = {row[1] for row in cursor.fetchall()}

        if 'cache_action' not in existing_columns:
            try:
                conn.execute("ALTER TABLE rpc_metrics ADD COLUMN cache_action TEXT DEFAULT 'none'")
                print(f"[RPC_METRICS] Added cache_action column to rpc_metrics table", flush=True)
            except Exception as e:
                print(f"[RPC_METRICS] Failed to add cache_action column: {e}", flush=True)

        if 'credits_saved' not in existing_columns:
            try:
                conn.execute("ALTER TABLE rpc_metrics ADD COLUMN credits_saved INTEGER DEFAULT 0")
                print(f"[RPC_METRICS] Added credits_saved column to rpc_metrics table", flush=True)
            except Exception as e:
                print(f"[RPC_METRICS] Failed to add credits_saved column: {e}", flush=True)

        if 'optimization_layer' not in existing_columns:
            try:
                conn.execute("ALTER TABLE rpc_metrics ADD COLUMN optimization_layer TEXT DEFAULT 'none'")
                print(f"[RPC_METRICS] Added optimization_layer column to rpc_metrics table", flush=True)
            except Exception as e:
                print(f"[RPC_METRICS] Failed to add optimization_layer column: {e}", flush=True)

        # Create index for common queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rpc_metrics_timestamp
            ON rpc_metrics(timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rpc_metrics_source_file
            ON rpc_metrics(source_file, timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rpc_metrics_method
            ON rpc_metrics(method, timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rpc_metrics_cache_action
            ON rpc_metrics(cache_action, timestamp DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rpc_metrics_optimization_layer
            ON rpc_metrics(optimization_layer, timestamp DESC)
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WARNING] Could not ensure RPC metrics table: {e}", flush=True)

# ── BATCHED metric persistence ───────────────────────────────────────────────
# Previously every RPC call did its own connect→INSERT→commit→close = one write-lock
# acquisition PER CALL. With the cascade/listener/scheduler/price-worker all making RPC
# calls constantly, that was thousands of write-locks/minute hammering the live db — the
# dominant source of the lock storm (per DB_LOCK_STORM_DIAGNOSIS). Fix: buffer rows in
# memory and flush as ONE batched transaction every few seconds, turning thousands of
# lock acquisitions into a handful.
import queue as _queue_mod
import threading as _threading_mod

_metric_buffer: "_queue_mod.Queue" = _queue_mod.Queue(maxsize=20000)
_flusher_started = False
_flusher_lock = _threading_mod.Lock()
_FLUSH_INTERVAL_S = 5.0
_FLUSH_MAX_BATCH = 500


def _metric_flush_loop():
    import os as _os
    _db_enabled = _os.environ.get("LISTENER_RPC_METRICS_DB_ENABLED", "1") not in ("0", "false", "no", "off")
    if not _db_enabled:
        print("[RPC_METRICS] DB writes disabled via LISTENER_RPC_METRICS_DB_ENABLED=0 — draining buffer without writing", flush=True)
    while True:
        time.sleep(_FLUSH_INTERVAL_S)
        rows = []
        try:
            while len(rows) < _FLUSH_MAX_BATCH:
                rows.append(_metric_buffer.get_nowait())
        except _queue_mod.Empty:
            pass
        if not rows:
            continue
        if not _db_enabled:
            continue  # discard rows, keep buffer clear, no DB contention
        for _attempt in range(3):
            try:
                conn = sqlite3.connect(DB_PATH, timeout=30)
                try:
                    conn.execute("PRAGMA busy_timeout=15000")
                    conn.executemany(
                        """INSERT INTO rpc_metrics
                           (timestamp, section, provider, method, status_code, latency_ms, credits,
                            mode, retries, source_file, error, process_pid, cache_action,
                            credits_saved, optimization_layer)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
                    conn.commit()
                finally:
                    conn.close()
                break
            except Exception as e:
                if "locked" in str(e).lower() and _attempt < 2:
                    time.sleep(1.0); continue
                print(f"[RPC_METRICS] batch write failed ({len(rows)} rows): {e}", flush=True)
                break


def _ensure_flusher():
    global _flusher_started
    if _flusher_started:
        return
    with _flusher_lock:
        if _flusher_started:
            return
        _threading_mod.Thread(target=_metric_flush_loop, daemon=True,
                              name="rpc-metrics-flusher").start()
        _flusher_started = True


def _persist_rpc_metric(
    timestamp: float,
    section: str,
    provider: str,
    method: str,
    status_code: int,
    latency_ms: float,
    credits: int,
    mode: str = "realtime",
    retries: int = 0,
    source_file: str = "unknown",
    error: Optional[str] = None,
    cache_action: str = "none",
    credits_saved: int = 0,
    optimization_layer: str = "none",
):
    """Enqueue an RPC metric for the background batch writer (NON-BLOCKING — no per-call DB
    write). Drops silently if the buffer is full (metrics are telemetry, never block an RPC)."""
    _ensure_flusher()
    try:
        import os as os_module
        _metric_buffer.put_nowait(
            (timestamp, section, provider, method, status_code, latency_ms, credits,
             mode, retries, source_file, error, os_module.getpid(), cache_action,
             credits_saved, optimization_layer))
    except _queue_mod.Full:
        pass

def _set_state(key: str, value: str) -> None:
    """Persist a recorder state value to database"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("""
            INSERT INTO rpc_metrics_state(key, value, updated_at)
            VALUES(?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=CURRENT_TIMESTAMP
        """, (key, value))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[RPC_METRICS] Failed to persist state {key}: {e}", flush=True)



def _get_earliest_metric_timestamp() -> float:
    """Get the earliest recorded metric timestamp from database"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.execute("SELECT MIN(timestamp) FROM rpc_metrics")
        row = cursor.fetchone()
        conn.close()
        return float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        return 0.0

def _get_state(key: str, default: Optional[str] = None) -> Optional[str]:
    """Retrieve a recorder state value from database"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.execute("""
            SELECT value
            FROM rpc_metrics_state
            WHERE key = ?
        """, (key,))
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default

def _try_claim_reset_day(day_key: str) -> bool:
    """Atomically claim the UK reset day so only one process performs reset work."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        cursor = conn.execute("""
            INSERT INTO rpc_metrics_state(key, value, updated_at)
            VALUES('rpc_metrics_last_reset_day', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=CURRENT_TIMESTAMP
            WHERE rpc_metrics_state.value != excluded.value
        """, (day_key,))
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed
    except Exception:
        return False

# ============================================================================
# RPC METRICS RECORDER
# ============================================================================

class RPCMetricsRecorder:
    """Thread-safe metrics recorder for RPC requests"""

    def __init__(self, max_history: int = 10000, plan_monthly_credits: int = 0):
        """
        Initialize recorder.

        Args:
            max_history: Keep last N request records for diagnostics
            plan_monthly_credits: Total monthly credit budget (0 = unlimited)
        """
        self._lock = RLock()
        self._max_history = max_history
        self._plan_monthly_credits = plan_monthly_credits

        # History buffer (for diagnostics)
        self._history: deque = deque(maxlen=max_history)

        # Per-section stats
        self._section_stats: Dict[str, SectionStats] = defaultdict(SectionStats)

        # Note: In-memory caches for potential future optimization
        # Currently all reporting derives from _history and database
        # These are maintained for backward compatibility and reset operations
        self._source_file_stats = {}  # Not actively updated; get_source_file_stats() rebuilds from _history
        self._method_stats = {}  # Not actively updated; get_top_methods() rebuilds from _history

        # Global tracking
        self._start_time = time.time()
        self._total_credits = 0
        self._total_requests = 0
        self._total_errors = 0
        self._total_429s = 0

        # Daily reset tracking (UK timezone)
        self._daily_reset_time = datetime.now(UK_TZ)
        self._daily_credits = 0
        self._daily_requests = 0
        self._daily_errors = 0
        self._daily_429s = 0

        # Ensure database table exists for persistent cross-process metrics
        _ensure_rpc_metrics_table()

        # Comparison reset baseline - timestamp-based for DB-backed queries
        # Load persisted values first, then fallback to earliest DB metric
        persisted_reset_ts = _get_state("comparison_reset_timestamp")
        persisted_helius_baseline = _get_state("baseline_helius_credits_today")
        persisted_reset_time = _get_state("comparison_reset_time")

        # If no persisted reset timestamp, use earliest metric in DB (includes all history)
        # If no metrics exist yet, use 0.0 (will include future metrics as they arrive)
        self._comparison_reset_timestamp = (
            float(persisted_reset_ts)
            if persisted_reset_ts
            else _get_earliest_metric_timestamp()
        )

        self._baseline_helius_credits_today = (
            int(persisted_helius_baseline)
            if persisted_helius_baseline
            else 0
        )

        self._comparison_reset_time = (
            datetime.fromisoformat(persisted_reset_time)
            if persisted_reset_time
            else datetime.now(UK_TZ)
        )

    def _uk_now(self) -> datetime:
        """Get current time in UK timezone"""
        return datetime.now(UK_TZ)

    def _uk_day_key(self) -> str:
        """Get date string for today in UK timezone (YYYY-MM-DD format)"""
        return self._uk_now().date().isoformat()

    def _uk_midnight_timestamp(self) -> float:
        """Get Unix timestamp for today's UK midnight (start of day)"""
        uk_now = self._uk_now()
        uk_midnight = uk_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return uk_midnight.timestamp()

    def _maybe_auto_reset_at_uk_midnight(self) -> None:
        """
        Automatically reset metrics once per UK calendar day.
        Uses database state so multiple processes share the same reset.
        """

        current_day = self._uk_day_key()

        if not _try_claim_reset_day(current_day):
            return

        # First process that sees the new day performs reset
        self._baseline_helius_credits_today = self._get_actual_helius_usage()
        self._comparison_reset_time = self._uk_now()
        self._comparison_reset_timestamp = self._uk_midnight_timestamp()

        self._daily_reset_time = self._uk_now()
        self._daily_credits = 0
        self._daily_requests = 0
        self._daily_errors = 0
        self._daily_429s = 0

        self._history.clear()
        self._section_stats.clear()
        self._source_file_stats.clear()
        self._method_stats.clear()

        _set_state("baseline_helius_credits_today", str(self._baseline_helius_credits_today))
        _set_state("comparison_reset_time", self._comparison_reset_time.isoformat())
        _set_state("comparison_reset_timestamp", str(self._comparison_reset_timestamp))

    def _refresh_reset_state_from_db(self) -> None:
        """Refresh persisted reset/baseline state so all processes stay in sync."""
        persisted_reset_ts = _get_state("comparison_reset_timestamp")
        persisted_helius_baseline = _get_state("baseline_helius_credits_today")
        persisted_reset_time = _get_state("comparison_reset_time")

        new_reset_ts = None
        if persisted_reset_ts:
            try:
                new_reset_ts = float(persisted_reset_ts)
            except (ValueError, TypeError):
                new_reset_ts = None

        if persisted_helius_baseline:
            try:
                self._baseline_helius_credits_today = int(persisted_helius_baseline)
            except (ValueError, TypeError):
                pass

        if persisted_reset_time:
            try:
                self._comparison_reset_time = datetime.fromisoformat(persisted_reset_time).replace(tzinfo=UK_TZ)
            except (ValueError, TypeError):
                pass

        if new_reset_ts is not None and new_reset_ts > self._comparison_reset_timestamp:
            self._comparison_reset_timestamp = new_reset_ts

            # Another process performed a newer reset: clear local in-memory state
            self._history.clear()
            self._section_stats.clear()
            self._source_file_stats.clear()
            self._method_stats.clear()

            self._total_credits = 0
            self._total_requests = 0
            self._total_errors = 0
            self._total_429s = 0
            self._daily_credits = 0
            self._daily_requests = 0
            self._daily_errors = 0
            self._daily_429s = 0
            self._daily_reset_time = self._uk_now()
            self._start_time = time.time()

    def _get_actual_helius_usage(self) -> int:
        """Safely read current Helius credits_used from latest database snapshot"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.cursor()
            # Get latest Helius usage snapshot
            cursor.execute("""
                SELECT credits_used
                FROM helius_usage_snapshots
                ORDER BY timestamp DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            conn.close()
            return int(row[0]) if row else 0
        except Exception:
            # Fallback to config if database read fails
            try:
                from src.apis.rpc_metrics_config import PlanConfig
                return int(PlanConfig.CURRENT_USAGE.get("credits_used_today", 0))
            except Exception:
                return 0

    def record_request(
        self,
        section: str,
        provider: str,
        method: str,
        status_code: int,
        latency_ms: float,
        mode: str = "realtime",
        retries: int = 0,
        bytes_in: int = 0,
        bytes_out: int = 0,
        source_file: str = "unknown",
        error: Optional[str] = None,
        cache_action: str = "none",
        credits_saved: int = 0,
        optimization_layer: str = "none",
        credits_override: Optional[int] = None,
    ) -> int:
        """
        Record a single RPC request or cache optimization event.

        Args:
            section: Component section (listener, creator_funding, funder_incoming, etc.)
            provider: Provider (helius_rpc, helius_enhanced, public_rpc_fallback, etc.)
            method: RPC method (getTransaction) or pseudo-method (helius_enhanced_addresses_transactions)
            status_code: HTTP/RPC status code
            latency_ms: Request latency in milliseconds
            mode: realtime or background
            retries: Number of retries before success
            bytes_in: Request body bytes
            bytes_out: Response body bytes
            source_file: File/process making the call (e.g., pumpfun_curve_listener, creator_outgoing_extractor)
            error: Error message if failed
            cache_action: Cache action (skip, refresh, full_scan, or none)
            credits_saved: Credits saved by cache action
            optimization_layer: Optimization that caused the skip (tx_cache, wallet_cache, etc.)
            credits_override: Override computed credits (e.g., 0 for cache events). If None, computes from method.

        Returns:
            Credits consumed for this request (0 if cache event with override)
        """
        with self._lock:
            self._maybe_auto_reset_at_uk_midnight()
            
            # Compute credits (unless overridden)
            if credits_override is not None:
                credits = credits_override
            else:
                credits = self._compute_credits(method, status_code)
            
            ts = time.time()

            # Create record
            record = RequestRecord(
                timestamp=ts,
                section=section,
                provider=provider,
                method=method,
                mode=mode,
                status_code=status_code,
                latency_ms=latency_ms,
                retries=retries,
                source_file=source_file,
                bytes_in=bytes_in,
                bytes_out=bytes_out,
                credits=credits,
                error=error,
                cache_action=cache_action,
                credits_saved=credits_saved,
            )

            # Store in history
            self._history.append(record)

            # Update section stats
            section_stats = self._section_stats[section]
            section_stats.requests += 1
            section_stats.latencies.append(latency_ms)
            section_stats.credits_total += credits
            section_stats.credits_by_method[method] = section_stats.credits_by_method.get(method, 0) + credits
            section_stats.credits_by_provider[provider] = section_stats.credits_by_provider.get(provider, 0) + credits

            # Track errors and daily counters
            if status_code >= 400:
                section_stats.errors += 1
                self._total_errors += 1
                self._daily_errors += 1

            if status_code == 429:
                section_stats.rate_limits_429 += 1
                self._total_429s += 1
                self._daily_429s += 1

            # Update global totals
            self._total_requests += 1
            self._total_credits += credits
            self._daily_requests += 1
            self._daily_credits += credits

            # Debug logging before persistence
            print(
                f"[RPC_METRICS] record_request source_file={source_file} method={method} credits={credits} cache_action={cache_action} ts={ts}",
                flush=True
            )

            # Persist to database for cross-process aggregation (non-blocking)
            _persist_rpc_metric(
                ts, section, provider, method, status_code, latency_ms,
                credits, mode, retries, source_file, error, cache_action, credits_saved, optimization_layer
            )

            return credits

    def record_stream_bytes(
        self,
        section: str,
        provider: str,
        stream_name: str,
        bytes_count: int,
    ) -> int:
        """
        Record streaming data (LaserStream, WebSocket, etc.).

        Args:
            section: Component section
            provider: Provider
            stream_name: Stream identifier (laserstream, enhanced_ws, etc.)
            bytes_count: Bytes transferred

        Returns:
            Credits consumed
        """
        with self._lock:
            self._maybe_auto_reset_at_uk_midnight()
            
            # Compute credits from bytes (3 credits per 0.1MB)
            credits = int(bytes_count * STREAMING_CREDITS_PER_BYTE) + (1 if (bytes_count * STREAMING_CREDITS_PER_BYTE) % 1 > 0 else 0)
            ts = time.time()

            # Record as pseudo-request
            record = RequestRecord(
                timestamp=ts,
                section=section,
                provider=provider,
                method=f"{stream_name}_bytes",
                mode="streaming",
                status_code=200,
                latency_ms=0,
                retries=0,
                source_file="streaming",
                bytes_out=bytes_count,
                credits=credits,
            )

            self._history.append(record)

            # Update stats
            section_stats = self._section_stats[section]
            section_stats.requests += 1
            section_stats.credits_total += credits
            section_stats.credits_by_method[f"{stream_name}_bytes"] = section_stats.credits_by_method.get(f"{stream_name}_bytes", 0) + credits
            section_stats.credits_by_provider[provider] = section_stats.credits_by_provider.get(provider, 0) + credits

            self._total_requests += 1
            self._total_credits += credits
            self._daily_requests += 1
            self._daily_credits += credits

            # Persist to database for cross-process aggregation
            _persist_rpc_metric(
                ts, section, provider, f"{stream_name}_bytes", 200, 0.0,
                credits, mode="streaming", retries=0, source_file="streaming",
                error=None, cache_action="none", credits_saved=0
            )

            return credits

    def _get_cache_stats(self, hours: int = 24, timestamp_cutoff: Optional[float] = None) -> Dict[str, int]:
        """Calculate cache-related stats from history (optionally time-windowed)"""
        cache_skip_count = 0
        cache_refresh_count = 0
        cache_full_scan_count = 0
        credits_saved_total = 0

        # Try database first (accurate cross-process view), fall back to in-memory history
        try:
            import sqlite3
            conn = sqlite3.connect(DB_PATH, timeout=30)
            
            # Add time filter: use explicit timestamp_cutoff if provided, else use hours
            where_clause = ""
            params = []
            if timestamp_cutoff is not None:
                where_clause = "AND timestamp > ?"
                params = [timestamp_cutoff]
            elif hours > 0:
                cutoff_timestamp = time.time() - (hours * 3600)
                where_clause = "AND timestamp > ?"
                params = [cutoff_timestamp]
            
            cursor = conn.execute(f"""
                SELECT
                    SUM(CASE WHEN cache_action='skip' THEN 1 ELSE 0 END) as skip_count,
                    SUM(CASE WHEN cache_action='refresh' THEN 1 ELSE 0 END) as refresh_count,
                    SUM(CASE WHEN cache_action='full_scan' THEN 1 ELSE 0 END) as full_scan_count,
                    SUM(credits_saved) as total_saved
                FROM rpc_metrics
                WHERE cache_action != 'none' {where_clause}
            """, params)
            row = cursor.fetchone()
            conn.close()

            if row:
                cache_skip_count = row[0] or 0
                cache_refresh_count = row[1] or 0
                cache_full_scan_count = row[2] or 0
                credits_saved_total = row[3] or 0
        except Exception:
            # Fallback: use in-memory history if database unavailable
            if timestamp_cutoff is not None:
                cutoff_timestamp = timestamp_cutoff
            else:
                cutoff_timestamp = time.time() - (hours * 3600) if hours > 0 else 0
            
            for record in self._history:
                if hours > 0 and record.timestamp < cutoff_timestamp:
                    continue
                if hasattr(record, 'cache_action'):
                    if record.cache_action == 'skip':
                        cache_skip_count += 1
                        credits_saved_total += getattr(record, 'credits_saved', 0)
                    elif record.cache_action == 'refresh':
                        cache_refresh_count += 1
                        credits_saved_total += getattr(record, 'credits_saved', 0)
                    elif record.cache_action == 'full_scan':
                        cache_full_scan_count += 1

        return {
            "cache_skip_count": cache_skip_count,
            "cache_refresh_count": cache_refresh_count,
            "cache_full_scan_count": cache_full_scan_count,
            "credits_saved_total": credits_saved_total,
        }

    def _compute_credits(self, method: str, status_code: int) -> int:
        """
        Compute credits for a method based on schedule.

        Returns 0 for:
        - Failed requests (status >= 400)
        - Methods with "unknown" cost (user must verify with Helius)
        """
        # Failed requests may not consume credits (depends on plan)
        # Default: 400+ errors cost credits, but this is configurable
        if status_code >= 400:
            return 0  # Assume failed requests don't consume credits

        # Look up in schedule
        if method not in CREDIT_SCHEDULE:
            # Unknown method - return 0 and let user add it to CREDIT_SCHEDULE
            return 0

        schedule_entry = CREDIT_SCHEDULE[method]

        # If entry is "unknown" string, return 0 (user must configure)
        if schedule_entry == "unknown":
            return 0

        # If it's a dict (e.g., getSignatureStatuses with conditional), use default
        if isinstance(schedule_entry, dict):
            return schedule_entry.get("default", 0)

        # Try to convert to int
        try:
            return int(schedule_entry)
        except (ValueError, TypeError):
            return 0

    def _get_tracked_calls(self, hours: int = 24) -> int:
        """Get count of actual RPC calls (not cache events) from database for time window"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            # Use UK midnight when default 24 hours is requested
            if hours == 24:
                cutoff_timestamp = self._uk_midnight_timestamp()
            else:
                cutoff_timestamp = time.time() - (hours * 3600)
            cursor = conn.execute("""
                SELECT COUNT(*)
                FROM rpc_metrics
                WHERE timestamp > ?
                  AND credits > 0
            """, (cutoff_timestamp,))
            value = cursor.fetchone()[0] or 0
            conn.close()
            return value
        except Exception:
            # Fallback to in-memory history
            if hours == 24:
                cutoff_timestamp = self._uk_midnight_timestamp()
            else:
                cutoff_timestamp = time.time() - (hours * 3600)
            return sum(1 for record in self._history if record.timestamp > cutoff_timestamp and record.credits > 0)

    def _get_tracked_calls_today_uk(self) -> int:
        """Get count of actual RPC calls (not cache events) since UK midnight"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cutoff_timestamp = self._uk_midnight_timestamp()
            cursor = conn.execute("""
                SELECT COUNT(*)
                FROM rpc_metrics
                WHERE timestamp > ?
                  AND credits > 0
            """, (cutoff_timestamp,))
            value = cursor.fetchone()[0] or 0
            conn.close()
            return value
        except Exception:
            # Fallback to in-memory history
            cutoff_timestamp = self._uk_midnight_timestamp()
            return sum(1 for record in self._history if record.timestamp > cutoff_timestamp and record.credits > 0)

    def _get_credits_24h(self) -> int:
        """Get actual credits from database since UK midnight (for compatibility)"""
        return self._get_credits_today_uk()

    def _get_credits_today_uk(self) -> int:
        """Get actual credits from database since UK midnight"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cutoff_timestamp = self._uk_midnight_timestamp()
            cursor = conn.execute("""
                SELECT COALESCE(SUM(credits), 0)
                FROM rpc_metrics
                WHERE timestamp > ?
            """, (cutoff_timestamp,))
            value = cursor.fetchone()[0] or 0
            conn.close()
            return value
        except Exception:
            return 0

    def _get_coverage_pct_24h(self) -> float:
        """
        Get tracking coverage estimate for 24h (local tracked vs Helius actual).
        
        NOTE: This is an approximation. It compares:
        - Local tracked credits: DB SUM for last 24h (exact)
        - Helius credits: credits_used_today from config (may be "today" not full 24h)
        
        Only accurate if Helius config provides a true 24h metric.
        Otherwise use as a rough estimate of instrumentation coverage.
        """
        try:
            # Get Helius actual credits from config (labeled as "today" - may not be full 24h)
            try:
                from src.apis.rpc_metrics_config import PlanConfig
                helius_total = int(PlanConfig.CURRENT_USAGE.get("credits_used_today", 0))
            except Exception:
                helius_total = 0

            if helius_total <= 0:
                return 0.0

            # Get locally tracked credits for last 24 hours (exact)
            local_tracked = self._get_credits_24h()
            
            coverage = (local_tracked / helius_total) * 100 if helius_total > 0 else 0
            return coverage
        except Exception:
            return 0.0

    def _get_tracked_credits_since_reset(self) -> int:
        """Get tracked credits since comparison reset (DB-backed, cross-process)"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.execute("""
                SELECT COALESCE(SUM(credits), 0)
                FROM rpc_metrics
                WHERE timestamp > ?
            """, (self._comparison_reset_timestamp,))
            value = cursor.fetchone()[0] or 0
            conn.close()
            return int(value)
        except Exception:
            # Fallback to in-memory history
            return sum(
                record.credits
                for record in self._history
                if record.timestamp > self._comparison_reset_timestamp
            )

    def _get_credits_since_reset(self) -> int:
        """Get total credits consumed since manual reset."""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.execute(
                """
                SELECT COALESCE(SUM(credits), 0)
                FROM rpc_metrics
                WHERE timestamp > ?
                """,
                (self._comparison_reset_timestamp,),
            )
            value = cursor.fetchone()[0] or 0
            conn.close()
            return int(value)
        except Exception:
            return 0

    def _get_tracked_calls_since_reset(self) -> int:
        """Get number of RPC calls since manual reset."""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM rpc_metrics
                WHERE timestamp > ?
                AND credits > 0
                """,
                (self._comparison_reset_timestamp,),
            )
            value = cursor.fetchone()[0] or 0
            conn.close()
            return int(value)
        except Exception:
            return sum(
                1 for r in self._history
                if r.timestamp > self._comparison_reset_timestamp and r.credits > 0
            )

    def get_summary(self) -> Dict:
        """Get high-level summary of credit usage"""
        with self._lock:
            self._maybe_auto_reset_at_uk_midnight()
            self._refresh_reset_state_from_db()
            
            now = time.time()
            uptime_seconds = now - self._start_time
            uptime_minutes = uptime_seconds / 60.0

            # Get actual Helius usage from latest database snapshot (always current)
            try:
                conn = sqlite3.connect(DB_PATH, timeout=30)
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT credits_used, credits_remaining
                    FROM helius_usage_snapshots
                    ORDER BY timestamp DESC
                    LIMIT 1
                """)
                row = cursor.fetchone()
                conn.close()

                if row:
                    credits_used_today_raw = int(row[0])
                    credits_remaining_budget = int(row[1])
                    credits_total_budget = credits_used_today_raw + credits_remaining_budget
                    print(f"[RPC_METRICS] Helius usage from DB: used={credits_used_today_raw}, remaining={credits_remaining_budget}", flush=True)
                else:
                    raise Exception("No Helius snapshot found")
            except Exception as e:
                # Fallback if database not available
                credits_used_today_raw = self._daily_credits
                credits_total_budget = self._plan_monthly_credits
                credits_remaining_budget = max(0, credits_total_budget - credits_used_today_raw) if credits_total_budget > 0 else None
                print(f"[RPC_METRICS] Failed to read from DB, using fallback: {e}", flush=True)

            # Calculate Helius deltas since reset (Helius is global/process-wide)
            helius_credits_since_reset = max(
                0, credits_used_today_raw - self._baseline_helius_credits_today
            )
            
            # Calculate local deltas since reset using DB (cross-process, timestamp-based)
            local_credits_since_reset = self._get_tracked_credits_since_reset()
            
            # Calculate untracked usage (Helius billed but not instrumented)
            untracked_usage = max(0, helius_credits_since_reset - local_credits_since_reset)
            
            # Calculate coverage percentage (can exceed 100% if local tracking includes calls not billed by Helius)
            coverage_pct = (
                (local_credits_since_reset / helius_credits_since_reset) * 100
                if helius_credits_since_reset > 0 else 0
            )
            
            comparison_diff = helius_credits_since_reset - local_credits_since_reset

            # Calculate elapsed time since comparison reset for accurate estimates
            # Use timezone-aware comparison
            now_utc = datetime.now(UK_TZ)
            comparison_elapsed_seconds = max(
                (now_utc - self._comparison_reset_time).total_seconds(), 1
            )
            comparison_elapsed_hours = comparison_elapsed_seconds / 3600.0
            comparison_elapsed_minutes = comparison_elapsed_seconds / 60.0

            # Daily credit estimate (based on since-reset values and time)
            daily_estimate = (
                local_credits_since_reset / comparison_elapsed_hours * 24
                if comparison_elapsed_hours > 0 else 0
            )

            # Monthly estimate
            monthly_estimate = daily_estimate * 30

            # Burn rate (credits per minute, based on since-reset)
            burn_rate = local_credits_since_reset / max(comparison_elapsed_minutes, 1)

            # ========================================================================
            # DASHBOARD CARD VALUES (Session-based, reset by manual monitoring reset)
            # ========================================================================
            
            # Get metrics since manual monitoring reset (respects reset button)
            actual_credits_since_reset = self._get_credits_since_reset()
            tracked_calls_since_reset = self._get_tracked_calls_since_reset()
            cache_stats_since_reset = self._get_cache_stats(timestamp_cutoff=self._comparison_reset_timestamp)
            saved_credits_since_reset = cache_stats_since_reset["credits_saved_total"]
            estimated_without_opts_since_reset = actual_credits_since_reset + saved_credits_since_reset
            tracking_coverage_pct = (
                (actual_credits_since_reset / helius_credits_since_reset) * 100
                if helius_credits_since_reset > 0 else 0
            )

            result = {
                "timestamp": datetime.now().isoformat(),
                "uptime_minutes": round(uptime_minutes, 2),
                # Raw values (for debugging)
                "helius_credits_today_raw": credits_used_today_raw,
                "local_credits_today_raw": self._daily_credits,
                # Since-reset values (DB-backed for cross-process consistency)
                "helius_credits_since_reset": helius_credits_since_reset,
                "local_credits_since_reset": local_credits_since_reset,
                "untracked_usage": untracked_usage,
                "comparison_coverage_pct": round(coverage_pct, 2),
                "comparison_diff": comparison_diff,
                "comparison_reset_at": self._comparison_reset_time.isoformat(),
                "manual_monitoring_reset_at": _get_state("manual_monitoring_reset_at"),
                # UI-friendly aliases for comparison panel
                "helius_billed": helius_credits_since_reset,
                "tracked_locally": local_credits_since_reset,
                "coverage_pct": round(coverage_pct, 2),
                # Backward-compatible aliases (since-reset DB-backed values)
                "credits_today": helius_credits_since_reset,
                "credits_instrumented_today": local_credits_since_reset,
                "credits_total": local_credits_since_reset,  # Stable alias for total locally tracked since reset
                # ====================================================================
                # DASHBOARD CARD FIELDS (Session-based, reset by manual monitoring reset)
                # ====================================================================
                "actual_rpc_credits": actual_credits_since_reset,
                "tracked_calls": tracked_calls_since_reset,
                "tracking_coverage_pct": round(tracking_coverage_pct, 2),
                "saved_credits": saved_credits_since_reset,
                "estimated_without_opts": estimated_without_opts_since_reset,
                # Estimates and burn rate
                "credits_monthly_estimate": int(monthly_estimate),
                "credits_monthly_budget": credits_total_budget,
                "credits_monthly_remaining": credits_remaining_budget,
                "credits_burn_rate_per_minute": round(burn_rate, 2),
                # Request counts (in-memory, for since-reset comparison)
                "requests_total": self._total_requests,
                "errors_total": self._total_errors,
                "rate_limits_total": self._total_429s,
                "sections_active": len(self._section_stats),
                # Cache details (session-based)
                "cache_skip_count": cache_stats_since_reset["cache_skip_count"],
                "cache_refresh_count": cache_stats_since_reset["cache_refresh_count"],
                "cache_full_scan_count": cache_stats_since_reset["cache_full_scan_count"],
                "credits_saved_total": cache_stats_since_reset["credits_saved_total"],
            }
            return result

    def get_section_stats(self) -> Dict[str, Dict]:
        """Get detailed stats per section"""
        with self._lock:
            self._maybe_auto_reset_at_uk_midnight()
            self._refresh_reset_state_from_db()
            
            result = {}
            for section, stats in self._section_stats.items():
                # Top 5 methods by credits
                top_methods = sorted(
                    stats.credits_by_method.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:5]

                result[section] = {
                    "credits": stats.credits_total,
                    "requests": stats.requests,
                    "errors": stats.errors,
                    "rate_limits_429": stats.rate_limits_429,
                    "avg_latency_ms": round(stats.avg_latency, 2),
                    "p95_latency_ms": round(stats.p95_latency, 2),
                    "top_methods": [{"method": m, "credits": c} for m, c in top_methods],
                }

            return result

    def get_source_file_stats(self) -> Dict[str, Dict]:
        """Get detailed stats grouped by source file/process"""
        with self._lock:
            self._maybe_auto_reset_at_uk_midnight()
            self._refresh_reset_state_from_db()
            
            source_stats = {}
            
            # Aggregate by source file
            for record in self._history:
                source = record.source_file or "unknown"
                if source not in source_stats:
                    source_stats[source] = {
                        'requests': 0,
                        'credits': 0,
                        'errors': 0,
                        'rate_limits_429': 0,
                        'latencies': [],
                        'sections': {},  # Track which sections this source uses
                        'methods': {},   # Track which methods this source uses
                    }
                
                stats = source_stats[source]
                stats['requests'] += 1
                stats['credits'] += record.credits
                stats['latencies'].append(record.latency_ms)
                
                # Track sections and methods
                if record.section not in stats['sections']:
                    stats['sections'][record.section] = 0
                stats['sections'][record.section] += 1
                
                if record.method not in stats['methods']:
                    stats['methods'][record.method] = 0
                stats['methods'][record.method] += 1
                
                if record.status_code >= 400:
                    stats['errors'] += 1
                if record.status_code == 429:
                    stats['rate_limits_429'] += 1
            
            # Calculate percentiles and format for API
            result = {}
            for source, stats in source_stats.items():
                latencies = sorted(stats['latencies'])
                result[source] = {
                    'credits': stats['credits'],
                    'requests': stats['requests'],
                    'errors': stats['errors'],
                    'rate_limits_429': stats['rate_limits_429'],
                    'avg_latency_ms': round(sum(stats['latencies']) / len(stats['latencies']), 2) if stats['latencies'] else 0,
                    'p95_latency_ms': round(latencies[int(len(latencies) * 0.95)], 2) if latencies else 0,
                    'sections': dict(sorted(stats['sections'].items(), key=lambda x: x[1], reverse=True)),
                    'top_methods': [
                        {'method': m, 'calls': c}
                        for m, c in sorted(stats['methods'].items(), key=lambda x: x[1], reverse=True)[:5]
                    ],
                }
            
            return dict(sorted(result.items(), key=lambda x: x[1]['credits'], reverse=True))

    def get_top_methods(self, limit: int = 10, hours: int = 24) -> List[Dict]:
        """Get top methods by credits across all processes (DB-backed)"""
        with self._lock:
            self._maybe_auto_reset_at_uk_midnight()
            self._refresh_reset_state_from_db()

        try:
            # Try database first (cross-process, accurate)
            cutoff_timestamp = (
                self._comparison_reset_timestamp
                if hours == 24
                else time.time() - (hours * 3600)
            )
            conn = sqlite3.connect(DB_PATH, timeout=30)
            cursor = conn.execute("""
                SELECT
                    method,
                    COUNT(*) as requests,
                    COALESCE(SUM(credits), 0) as credits
                FROM rpc_metrics
                WHERE timestamp > ?
                  AND credits > 0
                GROUP BY method
                ORDER BY credits DESC, requests DESC
                LIMIT ?
            """, (cutoff_timestamp, limit))
            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    "method": method,
                    "credits": credits,
                    "requests": requests,
                }
                for method, requests, credits in rows
            ]
        except Exception:
            # Fallback to in-memory history
            with self._lock:
                method_credits = defaultdict(int)
                method_requests = defaultdict(int)

                cutoff_timestamp = self._comparison_reset_timestamp
                for record in self._history:
                    if record.timestamp <= cutoff_timestamp:
                        continue
                    if record.credits == 0:
                        continue
                    method_credits[record.method] += record.credits
                    method_requests[record.method] += 1

                sorted_methods = sorted(
                    method_credits.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:limit]

                return [
                    {
                        "method": method,
                        "credits": credits,
                        "requests": method_requests[method],
                    }
                    for method, credits in sorted_methods
                ]

    def get_alerts(self, burn_rate_threshold: float = 100.0) -> List[Dict]:
        """Check for alerts (budget, burn rate, etc.)"""
        alerts = []

        with self._lock:
            summary = self.get_summary()

            # High burn rate alert
            if summary["credits_burn_rate_per_minute"] > burn_rate_threshold:
                alerts.append({
                    "level": "warning",
                    "type": "high_burn_rate",
                    "message": f"Burn rate {summary['credits_burn_rate_per_minute']:.2f} credits/min exceeds threshold {burn_rate_threshold}",
                })

            # Budget depletion alert (use same budget source as get_summary())
            budget = summary.get("credits_monthly_budget") or 0
            remaining = summary.get("credits_monthly_remaining")
            if budget > 0 and remaining is not None:
                remaining_pct = (remaining / budget * 100)
                if remaining_pct < 20:
                    alerts.append({
                        "level": "warning" if remaining_pct > 5 else "critical",
                        "type": "budget_depletion",
                        "message": f"Only {remaining_pct:.1f}% of monthly budget remaining ({remaining} credits)",
                    })

            # High error rate
            error_rate = (self._total_errors / max(self._total_requests, 1)) * 100
            if error_rate > 5:
                alerts.append({
                    "level": "warning",
                    "type": "high_error_rate",
                    "message": f"Error rate {error_rate:.1f}% exceeds threshold 5%",
                })

        return alerts

    def reset_daily(self):
        """Reset daily counters (call once per day)"""
        with self._lock:
            self._daily_reset_time = self._uk_now()
            self._daily_credits = 0
            self._daily_requests = 0
            self._daily_errors = 0
            self._daily_429s = 0
            # Reset section stats by clearing and letting defaultdict recreate as SectionStats
            self._section_stats.clear()
            # Reset source file stats
            self._source_file_stats.clear()
            # Reset method stats
            self._method_stats.clear()

    def reset_comparison_baseline(self):
        """
        Reset comparison baselines so both Helius and local metrics
        start from 0 for reporting purposes.

        This allows accurate comparison of Helius-billed credits vs
        locally-instrumented credits from a known reset point.

        Implementation note: Uses timestamp-based DB query (WHERE timestamp > reset_ts).
        Minor clock skew between processes may cause tiny discrepancies at reset boundary.
        This is expected and acceptable.
        """
        with self._lock:
            self._baseline_helius_credits_today = self._get_actual_helius_usage()
            self._comparison_reset_time = self._uk_now()
            self._comparison_reset_timestamp = time.time()

            # Persist to database so all processes see the same reset point
            _set_state("baseline_helius_credits_today", str(self._baseline_helius_credits_today))
            _set_state("comparison_reset_time", self._comparison_reset_time.isoformat())
            _set_state("comparison_reset_timestamp", str(self._comparison_reset_timestamp))

    def reset_credits_today(self):
        """Reset all local/session metrics to 0 and snapshot Helius for comparison."""
        with self._lock:
            now_uk = self._uk_now()

            # Snapshot current Helius usage as baseline
            self._baseline_helius_credits_today = self._get_actual_helius_usage()

            # Reset local counters
            self._total_credits = 0
            self._total_requests = 0
            self._total_errors = 0
            self._total_429s = 0
            self._daily_credits = 0
            self._daily_requests = 0
            self._daily_errors = 0
            self._daily_429s = 0

            # Reset comparison baselines
            self._comparison_reset_time = now_uk
            self._comparison_reset_timestamp = time.time()
            self._daily_reset_time = now_uk
            self._start_time = time.time()

            # Clear in-memory history/stats
            self._history.clear()
            self._section_stats.clear()
            self._source_file_stats.clear()
            self._method_stats.clear()

            # Persist comparison reset so all processes see the same baseline
            _set_state("baseline_helius_credits_today", str(self._baseline_helius_credits_today))
            _set_state("comparison_reset_time", self._comparison_reset_time.isoformat())
            _set_state("comparison_reset_timestamp", str(self._comparison_reset_timestamp))
            _set_state("manual_monitoring_reset_at", now_uk.isoformat())  # Config may not be available

    def get_optimization_savings(self, hours: int = 24) -> Dict:
        """
        Calculate RPC credit savings from optimizations.

        Args:
            hours: Time window (default 24 hours)

        Returns:
            {
                "actual_credits": int,
                "saved_credits": int,
                "estimated_without_optimizations": int,
                "savings_pct": float,
                "by_optimization_layer": {...},
                "by_section": {...}
            }
        """
        with self._lock:
            self._maybe_auto_reset_at_uk_midnight()
            self._refresh_reset_state_from_db()
        
        try:
            import sqlite3
            from datetime import datetime, timedelta

            cutoff_timestamp = (
                self._comparison_reset_timestamp
                if hours == 24
                else time.time() - (hours * 3600)
            )

            conn = sqlite3.connect(DB_PATH, timeout=30)

            # Query 1: Actual credits consumed
            cursor = conn.execute("""
                SELECT SUM(credits)
                FROM rpc_metrics
                WHERE timestamp > ?
            """, (cutoff_timestamp,))
            actual_credits = cursor.fetchone()[0] or 0
            
            # Query 2: Credits saved by optimizations
            cursor = conn.execute("""
                SELECT SUM(credits_saved)
                FROM rpc_metrics
                WHERE timestamp > ? AND credits_saved > 0
            """, (cutoff_timestamp,))
            saved_credits = cursor.fetchone()[0] or 0
            
            # Query 3: Savings by optimization layer
            cursor = conn.execute("""
                SELECT optimization_layer, SUM(credits_saved), COUNT(*)
                FROM rpc_metrics
                WHERE timestamp > ? AND credits_saved > 0
                GROUP BY optimization_layer
                ORDER BY SUM(credits_saved) DESC
            """, (cutoff_timestamp,))
            by_optimization_layer = {}
            for layer, credits, count in cursor.fetchall():
                by_optimization_layer[layer] = {
                    "credits_saved": credits or 0,
                    "skip_count": count
                }
            
            # Query 4: Savings by section
            cursor = conn.execute("""
                SELECT section, SUM(credits_saved), COUNT(*)
                FROM rpc_metrics
                WHERE timestamp > ? AND credits_saved > 0
                GROUP BY section
                ORDER BY SUM(credits_saved) DESC
            """, (cutoff_timestamp,))
            by_section = {}
            for section, credits, count in cursor.fetchall():
                by_section[section] = {
                    "credits_saved": credits or 0,
                    "skip_count": count
                }
            
            conn.close()
            
            # Calculate percentages
            estimated_total = actual_credits + saved_credits
            savings_pct = (saved_credits / estimated_total * 100) if estimated_total > 0 else 0
            
            print(
                f"[RPC_METRICS] optimization_savings cutoff={cutoff_timestamp} actual={actual_credits} saved={saved_credits} layers={len(by_optimization_layer)}",
                flush=True
            )
            
            return {
                "actual_credits": actual_credits,
                "saved_credits": saved_credits,
                "estimated_without_optimizations": estimated_total,
                "savings_pct": round(savings_pct, 2),
                "by_optimization_layer": by_optimization_layer,
                "by_section": by_section,
                "window_hours": hours,
            }
        except Exception as e:
            print(f"[RPC_METRICS] Error calculating optimization savings: {e}", flush=True)
            return {
                "actual_credits": 0,
                "saved_credits": 0,
                "estimated_without_optimizations": 0,
                "savings_pct": 0,
                "by_optimization_layer": {},
                "by_section": {},
                "window_hours": hours,
                "error": str(e),
            }

    def get_component_breakdown(self, hours: int = 24) -> Dict:
        """
        Get RPC usage breakdown by component (source_file).
        
        This is the primary way to view component attribution, as source_file
        reliably identifies which process made each RPC call.
        
        Args:
            hours: Time window (default 24 hours)
            
        Returns:
            {
                "components": {
                    "funder_incoming_extractor": {
                        "credits": 2600,
                        "calls": 26,
                        "avg_credits_per_call": 100.0,
                        "top_methods": [...]
                    },
                    ...
                },
                "window_hours": 24,
                "total_credits": 917,
                "total_calls": 28
            }
        """
        with self._lock:
            self._maybe_auto_reset_at_uk_midnight()
            self._refresh_reset_state_from_db()
        
        try:
            import sqlite3

            cutoff_timestamp = (
                self._comparison_reset_timestamp
                if hours == 24
                else time.time() - (hours * 3600)
            )
            conn = sqlite3.connect(DB_PATH, timeout=30)

            # Query 1: Aggregate by source_file (component), only counting actual RPC calls
            cursor = conn.execute("""
                SELECT
                    source_file,
                    SUM(CASE WHEN credits > 0 THEN 1 ELSE 0 END) as calls,
                    COALESCE(SUM(credits), 0) as credits,
                    ROUND(AVG(CASE WHEN credits > 0 THEN credits ELSE NULL END), 2) as avg_credits
                FROM rpc_metrics
                WHERE timestamp > ?
                  AND source_file IS NOT NULL
                  AND source_file != ''
                GROUP BY source_file
                ORDER BY credits DESC
            """, (cutoff_timestamp,))
            
            components_data = cursor.fetchall()
            print(
                f"[RPC_METRICS] component_breakdown cutoff={cutoff_timestamp} rows={len(components_data)}",
                flush=True
            )
            components = {}
            total_credits = 0
            total_calls = 0
            
            # Query 2: Get top 5 methods per component (only actual RPC calls)
            for source_file, calls, credits, avg_credits in components_data:
                total_credits += credits
                total_calls += calls
                
                # Get top methods for this component
                cursor = conn.execute("""
                    SELECT 
                        method,
                        SUM(CASE WHEN credits > 0 THEN 1 ELSE 0 END) as method_calls,
                        COALESCE(SUM(credits), 0) as method_credits,
                        ROUND(AVG(CASE WHEN credits > 0 THEN credits ELSE NULL END), 2) as method_avg
                    FROM rpc_metrics
                    WHERE timestamp > ?
                      AND source_file = ?
                      AND credits > 0
                    GROUP BY method
                    ORDER BY method_credits DESC
                    LIMIT 5
                """, (cutoff_timestamp, source_file))
                
                top_methods = []
                for method, method_calls, method_credits, method_avg in cursor.fetchall():
                    top_methods.append({
                        "method": method,
                        "calls": method_calls,
                        "credits": method_credits,
                        "avg_credits_per_call": method_avg,
                    })
                
                components[source_file] = {
                    "credits": credits,
                    "calls": calls,
                    "avg_credits_per_call": avg_credits,
                    "top_methods": top_methods,
                }
            
            conn.close()
            
            return {
                "timestamp": datetime.now().isoformat(),
                "window_hours": hours,
                "total_credits": total_credits,
                "total_calls": total_calls,
                "components": components,
            }
        except Exception as e:
            import traceback
            print(f"[RPC_METRICS] Error calculating component breakdown: {e}", flush=True)
            traceback.print_exc()
            return {
                "timestamp": datetime.now().isoformat(),
                "window_hours": hours,
                "total_credits": 0,
                "total_calls": 0,
                "components": {},
                "error": str(e),
            }

    def export_json(self) -> str:
        """Export full metrics as JSON"""
        with self._lock:
            return json.dumps({
                "summary": self.get_summary(),
                "sections": self.get_section_stats(),
                "top_methods": self.get_top_methods(hours=24),
                "alerts": self.get_alerts(),
            }, indent=2)


# Global recorder instance
_recorder: Optional[RPCMetricsRecorder] = None


def initialize_recorder(plan_monthly_credits: int = 0) -> RPCMetricsRecorder:
    """Initialize global recorder instance"""
    global _recorder
    _recorder = RPCMetricsRecorder(plan_monthly_credits=plan_monthly_credits)
    return _recorder


def get_recorder() -> RPCMetricsRecorder:
    """Get global recorder instance"""
    global _recorder
    if _recorder is None:
        _recorder = RPCMetricsRecorder()
    return _recorder


def record_request(
    section: str,
    provider: str,
    method: str,
    status_code: int,
    latency_ms: float,
    mode: str = "realtime",
    retries: int = 0,
    bytes_in: int = 0,
    bytes_out: int = 0,
    source_file: str = "unknown",
    error: Optional[str] = None,
    cache_action: str = "none",
    credits_saved: int = 0,
    optimization_layer: str = "none",
    credits_override: Optional[int] = None,
) -> int:
    """Convenience function to record request with global instance"""
    return get_recorder().record_request(
        section=section,
        provider=provider,
        method=method,
        status_code=status_code,
        latency_ms=latency_ms,
        mode=mode,
        retries=retries,
        bytes_in=bytes_in,
        bytes_out=bytes_out,
        source_file=source_file,
        error=error,
        cache_action=cache_action,
        credits_saved=credits_saved,
        optimization_layer=optimization_layer,
        credits_override=credits_override,
    )


def record_stream_bytes(
    section: str,
    provider: str,
    stream_name: str,
    bytes_count: int,
) -> int:
    """Convenience function to record streaming bytes with global instance"""
    return get_recorder().record_stream_bytes(section, provider, stream_name, bytes_count)


def get_optimization_savings(hours: int = 24) -> Dict:
    """Convenience function to get optimization savings with global instance"""
    return get_recorder().get_optimization_savings(hours)


def get_component_breakdown(hours: int = 24) -> Dict:
    """Convenience function to get component breakdown with global instance"""
    return get_recorder().get_component_breakdown(hours)


def reset_comparison_baseline():
    """Convenience function to reset actual vs local comparison baseline"""
    get_recorder().reset_comparison_baseline()


def record_cache_event(
    section: str,
    provider: str,
    method: str,
    source_file: str,
    cache_action: str,
    credits_saved: int,
    optimization_layer: str,
) -> None:
    """
    Record a cache optimization event (cache hit, refresh, etc).
    
    Call this whenever a cache layer prevents an RPC call.
    
    Args:
        section: Component section (e.g., "funder_incoming", "creator_funding")
        provider: Provider (e.g., "helius_rpc", "public_rpc")
        method: RPC method that was skipped/refreshed
        source_file: File/process that triggered the cache event
        cache_action: Action type (skip, refresh, full_scan)
        credits_saved: Credits that would have been spent
        optimization_layer: Layer that triggered skip (tx_cache, wallet_cache, etc)
    
    Example:
        # Transaction was in cache, skip getTransaction RPC
        record_cache_event(
            section="funder_incoming",
            provider="helius_rpc",
            method="getTransaction",
            source_file="funder_incoming_extractor.py",
            cache_action="skip",
            credits_saved=10,
            optimization_layer="tx_cache",
        )
    """
    print(
        f"[RPC_METRICS] record_cache_event source_file={source_file} method={method} cache_action={cache_action} credits_saved={credits_saved} layer={optimization_layer}",
        flush=True
    )
    get_recorder().record_request(
        section=section,
        provider=provider,
        method=method,
        status_code=200,
        latency_ms=0.0,
        mode="realtime",
        retries=0,
        bytes_in=0,
        bytes_out=0,
        source_file=source_file,
        error=None,
        cache_action=cache_action,
        credits_saved=credits_saved,
        optimization_layer=optimization_layer,
        credits_override=0,  # Cache events consume 0 credits (they prevent RPC calls)
    )
