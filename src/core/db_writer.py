"""
db_writer.py — SQL-batch client of the shared Database Write Service.

Feature code queues statements here; transaction ownership belongs to
database_write_service and the database path is a parameter.

Design:
- Priority-triggered flush: threading.Event wakes writer immediately for migration events
- Otherwise flushes every 500ms (webhook/poller cadence)
- executemany batching for homogeneous SQL statements
- Monotonic event_sequence counter assigned here — never by callers
- Shared transaction telemetry and rollback ownership
- Dead-letter logging on fatal errors
"""

import threading
import time
import logging
import os
from collections import defaultdict
from typing import Optional

from src.core.db_write_queue import drain_batch, WriteItem, queue_depths, migration_signal
from src.core.database_write_service import database_write_service

logger = logging.getLogger("db_writer")

# Configuration
_FLUSH_TIMEOUT_SEC = float(os.getenv("DB_WRITER_FLUSH_MS", "500")) / 1000.0
_MAX_BATCH         = int(os.getenv("DB_WRITER_MAX_BATCH", "300"))

# Monotonic event sequence — assigned ONLY by writer thread, never by callers
# Callers use -1 as sentinel in watchtower_events inserts
_event_sequence_counter = 0
_sequence_lock = threading.Lock()  # paranoia — only writer thread touches this

# Writer stats
_stats: dict = {
    "batches_committed": 0,
    "statements_written": 0,
    "lock_errors": 0,
    "dead_letters": 0,
    "last_flush_at": 0.0,
    "last_batch_size": 0,
    "avg_flush_ms": 0.0,
    "started_at": 0.0,
}
_stats_lock = threading.Lock()

_writer_thread: Optional[threading.Thread] = None


def _assign_event_sequences(statements: list) -> list:
    """
    Replace the -1 sentinel in watchtower_events inserts with the next
    monotonic sequence number. Called inside _commit_batch only.
    """
    global _event_sequence_counter
    result = []
    for sql, params in statements:
        if "watchtower_events" in sql and params and len(params) > 0 and params[0] == -1:
            with _sequence_lock:
                _event_sequence_counter += 1
                seq = _event_sequence_counter
            params = (seq,) + tuple(params[1:])
        result.append((sql, params))
    return result


def _commit_batch(db_path: str, items: list[WriteItem]) -> tuple[int, int]:
    """
    Commit all statements from items in a single transaction.
    Returns (statements_written, dead_letter_count).
    """
    if not items:
        return 0, 0

    all_statements = []
    for item in items:
        all_statements.extend(item.statements)

    # Assign monotonic sequence numbers to any event inserts
    all_statements = _assign_event_sequences(all_statements)

    t0 = time.monotonic()
    database = f"live:{os.path.realpath(db_path)}"

    def transaction(conn):
        groups: dict[str, list] = defaultdict(list)
        for sql, params in all_statements:
            groups[sql].append(params)
        for sql, param_list in groups.items():
            if len(param_list) == 1:
                conn.execute(sql, param_list[0])
            else:
                conn.executemany(sql, param_list)

    try:
        database_write_service.register_database(database, db_path)
        database_write_service.submit(database, "sql-batch", transaction)
        elapsed_ms = (time.monotonic() - t0) * 1000
        with _stats_lock:
            _stats["batches_committed"] += 1
            _stats["statements_written"] += len(all_statements)
            _stats["last_flush_at"] = time.time()
            _stats["last_batch_size"] = len(items)
            prev_avg = _stats["avg_flush_ms"]
            _stats["avg_flush_ms"] = round(0.9 * prev_avg + 0.1 * elapsed_ms, 1)
        return len(all_statements), 0
    except Exception as exc:
        logger.error(
            f"[DB_WRITER] transaction failed — dead-lettering {len(items)} items: {exc}",
            exc_info=True,
        )
        for item in items:
            logger.error(f"[DEAD_LETTER] domain={item.domain} label={item.label}")
        with _stats_lock:
            _stats["dead_letters"] += len(items)
            if "locked" in str(exc).lower():
                _stats["lock_errors"] += 1
        return 0, len(items)


def _writer_loop(db_path: str) -> None:
    """
    Priority-triggered flush loop feeding the shared transaction service.

    Wakes immediately when migration_signal is set (urgent items).
    Otherwise waits up to FLUSH_TIMEOUT_SEC before flushing whatever has accumulated.
    """
    logger.info(
        f"[DB_WRITER] single-writer thread started "
        f"flush_timeout={_FLUSH_TIMEOUT_SEC*1000:.0f}ms max_batch={_MAX_BATCH}"
    )

    while True:
        try:
            # Block until migration signal OR timeout
            triggered = migration_signal.wait(timeout=_FLUSH_TIMEOUT_SEC)
            migration_signal.clear()

            items = drain_batch(_MAX_BATCH)
            if items:
                _commit_batch(db_path, items)

            # Log queue depths periodically
            if triggered:
                depths = queue_depths()
                if any(v > 0 for v in depths.values()):
                    logger.debug(f"[DB_WRITER] queue depths after flush: {depths}")

        except Exception as e:
            logger.error(f"[DB_WRITER] loop error: {e}", exc_info=True)
            time.sleep(1.0)  # prevent tight error loop


def _watchdog_loop(db_path: str) -> None:
    """Restarts writer thread if it dies."""
    while True:
        time.sleep(30)
        global _writer_thread
        if not _writer_thread or not _writer_thread.is_alive():
            logger.critical("[DB_WRITER] writer thread died — restarting")
            _start_writer_thread(db_path)


def _start_writer_thread(db_path: str) -> None:
    global _writer_thread
    _writer_thread = threading.Thread(
        target=_writer_loop,
        args=(db_path,),
        daemon=True,
        name="db-single-writer",
    )
    _writer_thread.start()


def start_db_writer(db_path: str) -> None:
    """
    Start the single writer thread and its watchdog.
    Safe to call multiple times — idempotent.
    """
    global _writer_thread
    if _writer_thread and _writer_thread.is_alive():
        logger.debug("[DB_WRITER] already running")
        return

    with _stats_lock:
        _stats["started_at"] = time.time()

    _start_writer_thread(db_path)

    threading.Thread(
        target=_watchdog_loop,
        args=(db_path,),
        daemon=True,
        name="db-writer-watchdog",
    ).start()

    logger.info("[DB_WRITER] started with watchdog")


def get_writer_stats() -> dict:
    with _stats_lock:
        return {
            **_stats,
            "event_sequence_counter": _event_sequence_counter,
            "writer_alive": bool(_writer_thread and _writer_thread.is_alive()),
        }


def emit_event(
    event_type: str,
    wallet_address: str = None,
    related_wallet: str = None,
    token_mint: str = None,
    payload: dict = None,
    source: str = "rpc_scan",
    domain: str = "poller",
) -> None:
    """
    Emit a watchtower_events record. Sequence number is assigned by the writer thread.
    Events are never deduplicated — two identical events are two real events.
    """
    import json
    from src.core.db_write_queue import enqueue_write

    enqueue_write(WriteItem(
        domain=domain,
        label=f"event:{event_type}:{(wallet_address or '')[:20]}",
        statements=[(
            """INSERT INTO watchtower_events
               (event_sequence, event_type, wallet_address, related_wallet,
                token_mint, payload_json, source, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            # -1 is sentinel — writer thread replaces with monotonic sequence number
            (-1, event_type, wallet_address, related_wallet,
             token_mint, json.dumps(payload or {}), source, int(time.time()))
        )],
        idempotency_key="",  # events are always appended, never deduped
    ))
