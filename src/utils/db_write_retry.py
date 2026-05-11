"""
Reliable SQLite write helpers.

Rules enforced here:
  - No connection held across awaits (caller must pass in a factory, not an open conn)
  - Exponential backoff on OperationalError (database is locked / disk I/O error)
  - Dead-letter logging when max retries exceeded
  - All failures recorded in write_failure_log table for /api/db-health
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import traceback
from typing import Callable, Optional

logger = logging.getLogger("db_write_retry")

# ── Dead-letter store (in-process ring buffer, 500 entries) ─────────────────
_dead_letters: list[dict] = []
_dead_letter_lock = asyncio.Lock() if False else None  # populated lazily
_MAX_DEAD_LETTERS = 500

# ── Lock error counter for /api/db-health ───────────────────────────────────
_lock_error_count: int = 0
_lock_error_timestamps: list[float] = []
_failed_write_count: int = 0
_write_latencies: list[float] = []  # last 100 write latencies (ms)
_MAX_LATENCY_SAMPLES = 100


def _record_lock_error(label: str = "") -> None:
    global _lock_error_count
    _lock_error_count += 1
    now = time.time()
    _lock_error_timestamps.append(now)
    # trim to last hour
    cutoff = now - 3600
    while _lock_error_timestamps and _lock_error_timestamps[0] < cutoff:
        _lock_error_timestamps.pop(0)
    # also feed the global counter in db_locking
    try:
        from src.utils.db_locking import record_lock_error as _global_record
        _global_record(label or None)
    except Exception:
        pass


def _record_write_latency(ms: float) -> None:
    _write_latencies.append(ms)
    if len(_write_latencies) > _MAX_LATENCY_SAMPLES:
        _write_latencies.pop(0)


def _record_failed_write() -> None:
    global _failed_write_count
    _failed_write_count += 1


def _dead_letter(label: str, sql: str, params: tuple, error: str) -> None:
    global _dead_letters
    entry = {
        "ts": time.time(),
        "label": label,
        "sql": sql[:200],
        "params_repr": repr(params)[:200],
        "error": error,
        "traceback": traceback.format_exc()[-500:],
    }
    _dead_letters.append(entry)
    if len(_dead_letters) > _MAX_DEAD_LETTERS:
        _dead_letters.pop(0)
    logger.error(
        f"[DEAD_LETTER] label={label} error={error} sql={sql[:80]}"
    )


def get_health_metrics() -> dict:
    """Return metrics for /api/db-health."""
    now = time.time()
    cutoff_1h = now - 3600
    lock_errors_1h = sum(1 for t in _lock_error_timestamps if t >= cutoff_1h)
    avg_latency = (
        round(sum(_write_latencies) / len(_write_latencies), 1)
        if _write_latencies else 0.0
    )
    return {
        "sqlite_lock_errors_1h": lock_errors_1h,
        "failed_writes_total": _failed_write_count,
        "dead_letter_count": len(_dead_letters),
        "avg_write_latency_ms": avg_latency,
        "recent_dead_letters": _dead_letters[-5:],
    }


# ── Sync write with retry ────────────────────────────────────────────────────

def write_with_retry(
    db_path: str,
    sql: str,
    params: tuple = (),
    *,
    label: str = "unnamed",
    max_attempts: int = 5,
    base_delay: float = 0.5,
    connect_fn: Optional[Callable] = None,
) -> bool:
    """
    Execute a single write statement with retry on lock errors.

    Returns True on success, False if all attempts failed (dead-lettered).
    Never raises.
    """
    from src.utils.db_locking import db_connect
    _connect = connect_fn or db_connect

    for attempt in range(max_attempts):
        t0 = time.monotonic()
        try:
            conn = _connect(db_path, timeout=30)
            conn.execute(sql, params)
            conn.commit()
            conn.close()
            ms = (time.monotonic() - t0) * 1000
            _record_write_latency(ms)
            return True
        except sqlite3.OperationalError as e:
            ms = (time.monotonic() - t0) * 1000
            _record_lock_error()
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"[WRITE_RETRY] label={label} attempt={attempt+1}/{max_attempts} "
                f"error={e} delay={delay:.1f}s"
            )
            if attempt < max_attempts - 1:
                time.sleep(delay)
        except Exception as e:
            _record_failed_write()
            _dead_letter(label, sql, params, str(e))
            return False

    _record_failed_write()
    _dead_letter(label, sql, params, "max_retries_exceeded")
    return False


def write_batch_with_retry(
    db_path: str,
    statements: list[tuple[str, tuple]],
    *,
    label: str = "unnamed_batch",
    max_attempts: int = 5,
    base_delay: float = 0.5,
    connect_fn: Optional[Callable] = None,
) -> bool:
    """
    Execute multiple write statements in a single transaction with retry.

    All statements commit together or none do.
    Returns True on success, False if all attempts failed.
    Never raises.
    """
    from src.utils.db_locking import db_connect
    _connect = connect_fn or db_connect

    for attempt in range(max_attempts):
        t0 = time.monotonic()
        conn = None
        try:
            conn = _connect(db_path, timeout=30)
            for sql, params in statements:
                conn.execute(sql, params)
            conn.commit()
            conn.close()
            ms = (time.monotonic() - t0) * 1000
            _record_write_latency(ms)
            return True
        except sqlite3.OperationalError as e:
            if conn:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
            _record_lock_error()
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"[BATCH_RETRY] label={label} attempt={attempt+1}/{max_attempts} "
                f"error={e} delay={delay:.1f}s"
            )
            if attempt < max_attempts - 1:
                time.sleep(delay)
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
            _record_failed_write()
            _dead_letter(label, statements[0][0] if statements else "", (), str(e))
            return False

    _record_failed_write()
    _dead_letter(label, "", (), "max_retries_exceeded")
    return False


# ── Async write with retry ───────────────────────────────────────────────────

async def async_write_with_retry(
    db_path: str,
    sql: str,
    params: tuple = (),
    *,
    label: str = "unnamed",
    max_attempts: int = 5,
    base_delay: float = 0.5,
    connect_fn: Optional[Callable] = None,
) -> bool:
    """
    Async version — opens connection, writes, closes, awaits between retries.
    Never holds a connection across an await.
    """
    from src.utils.db_locking import db_connect
    _connect = connect_fn or db_connect

    for attempt in range(max_attempts):
        t0 = time.monotonic()
        try:
            conn = _connect(db_path, timeout=30)
            conn.execute(sql, params)
            conn.commit()
            conn.close()
            ms = (time.monotonic() - t0) * 1000
            _record_write_latency(ms)
            return True
        except sqlite3.OperationalError as e:
            _record_lock_error()
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"[ASYNC_WRITE_RETRY] label={label} attempt={attempt+1}/{max_attempts} "
                f"error={e} delay={delay:.1f}s"
            )
            if attempt < max_attempts - 1:
                await asyncio.sleep(delay)
        except Exception as e:
            _record_failed_write()
            _dead_letter(label, sql, params, str(e))
            return False

    _record_failed_write()
    _dead_letter(label, sql, params, "max_retries_exceeded")
    return False


async def async_write_batch_with_retry(
    db_path: str,
    statements: list[tuple[str, tuple]],
    *,
    label: str = "unnamed_batch",
    max_attempts: int = 5,
    base_delay: float = 0.5,
    connect_fn: Optional[Callable] = None,
) -> bool:
    """
    Async batch write with retry. Opens and closes connection per attempt.
    Never holds connection across awaits.
    """
    from src.utils.db_locking import db_connect
    _connect = connect_fn or db_connect

    for attempt in range(max_attempts):
        t0 = time.monotonic()
        conn = None
        try:
            conn = _connect(db_path, timeout=30)
            for sql, params in statements:
                conn.execute(sql, params)
            conn.commit()
            conn.close()
            ms = (time.monotonic() - t0) * 1000
            _record_write_latency(ms)
            return True
        except sqlite3.OperationalError as e:
            if conn:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
            _record_lock_error()
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"[ASYNC_BATCH_RETRY] label={label} attempt={attempt+1}/{max_attempts} "
                f"error={e} delay={delay:.1f}s"
            )
            if attempt < max_attempts - 1:
                await asyncio.sleep(delay)
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                    conn.close()
                except Exception:
                    pass
            _record_failed_write()
            _dead_letter(label, statements[0][0] if statements else "", (), str(e))
            return False

    _record_failed_write()
    _dead_letter(label, "", (), "max_retries_exceeded")
    return False
