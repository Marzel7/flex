"""
db_write_queue.py — In-memory write queue for the single-writer architecture.

Only db_writer.py may call BEGIN IMMEDIATE on the database.
All other code routes writes through enqueue_write().
"""

import queue
import threading
import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("db_write_queue")

@dataclass
class WriteItem:
    domain: str            # "migration" | "webhook" | "poller" | "scan" | "enrichment"
    label: str             # human label for dead-letter logging
    statements: list       # list of (sql, params) tuples
    idempotency_key: str   # "" = no dedup; else deduped within DEDUP_TTL seconds
    enqueued_at: float = field(default_factory=time.monotonic)


# One queue per domain — priority drain order: migration first
_queues: dict[str, queue.Queue] = {
    "migration":  queue.Queue(maxsize=500),
    "webhook":    queue.Queue(maxsize=2000),
    "poller":     queue.Queue(maxsize=500),
    "scan":       queue.Queue(maxsize=1000),
    "enrichment": queue.Queue(maxsize=500),
}
DRAIN_ORDER = ["migration", "webhook", "poller", "scan", "enrichment"]

# High-priority domains that trigger immediate writer wakeup
_URGENT_DOMAINS = frozenset({"migration"})

# Threading event — set when an urgent item arrives, wakes the writer immediately
migration_signal = threading.Event()

# Deduplication cache — idempotency_key → enqueued_at monotonic time
_dedup_cache: dict[str, float] = {}
_dedup_lock = threading.Lock()
_DEDUP_TTL = 60.0  # seconds


def _is_duplicate(key: str) -> bool:
    if not key:
        return False
    now = time.monotonic()
    with _dedup_lock:
        # Evict expired entries
        stale = [k for k, t in _dedup_cache.items() if now - t > _DEDUP_TTL]
        for k in stale:
            del _dedup_cache[k]
        if key in _dedup_cache:
            return True
        _dedup_cache[key] = now
        return False


def enqueue_write(item: WriteItem) -> bool:
    """
    Enqueue a write item. Returns True if accepted, False if dedup-rejected or queue full.
    Never raises.
    """
    if _is_duplicate(item.idempotency_key):
        return False
    q = _queues.get(item.domain, _queues["enrichment"])
    try:
        q.put_nowait(item)
        if item.domain in _URGENT_DOMAINS:
            migration_signal.set()
        return True
    except queue.Full:
        logger.warning(f"[WRITE_QUEUE] queue={item.domain} FULL — dropping label={item.label}")
        return False


def drain_batch(max_items: int = 300) -> list[WriteItem]:
    """
    Pull up to max_items from all queues in priority order. Non-blocking.
    """
    batch = []
    for domain in DRAIN_ORDER:
        q = _queues[domain]
        while len(batch) < max_items:
            try:
                batch.append(q.get_nowait())
            except queue.Empty:
                break
    return batch


def queue_depths() -> dict[str, int]:
    return {d: q.qsize() for d, q in _queues.items()}


def dedup_cache_size() -> int:
    with _dedup_lock:
        return len(_dedup_cache)
