"""
Token price fetch queue with rate limiting and concurrency control.

Replaces burst batch API calls with smooth, rate-limited requests.

Architecture:
1. Worker enqueues tokens to fetch
2. Queue manager limits concurrency (default 3 parallel)
3. Rate limiter delays between requests (default 200ms)
4. Fetch workers pull from queue synchronously
5. Results stored in cache and DB
"""

import sqlite3
import logging
import time
import threading
from typing import List, Dict, Optional, Callable
from dataclasses import dataclass, field
from queue import Queue, Empty

logger = logging.getLogger(__name__)


@dataclass
class FetchTask:
    """A single token to fetch."""
    mint: str
    priority: str  # HIGH, MEDIUM, LOW
    enqueued_at: float
    callback: Optional[Callable] = None  # Called with (mint, price) after fetch


class PriceFetchQueue:
    """
    Manages token price fetching with concurrency limits and rate limiting.

    Smooths API traffic: instead of fetching 20 tokens simultaneously (burst),
    fetches 3 at a time with 200ms delays between requests.

    Thread-safe implementation using standard Queue.
    """

    def __init__(self, max_concurrent: int = 3, request_delay_ms: int = 200):
        """
        Initialize queue.

        Args:
            max_concurrent: Max simultaneous fetches (default 3)
            request_delay_ms: Delay between requests in milliseconds (default 200ms)
        """
        self.max_concurrent = max_concurrent
        self.request_delay_ms = request_delay_ms / 1000.0  # Convert to seconds
        self.queue = Queue()
        self.active_requests = 0
        self.lock = threading.Lock()

        # EWMA latency tracking (0.8 weight to previous, 0.2 to new)
        self.latency_ewma = 0.0
        self.EWMA_ALPHA = 0.8

        self.stats = {
            'enqueued': 0,
            'processed': 0,
            'failed': 0,
            'queue_depth': 0,
            'last_request_at': 0,
            'avg_latency_ms': 0,
            'total_latency_ms': 0
        }
        self.running = False
        self.worker_thread = None

    def enqueue(self, task: FetchTask) -> None:
        """Add task to queue."""
        self.queue.put(task)
        with self.lock:
            self.stats['enqueued'] += 1
            self.stats['queue_depth'] = self.queue.qsize()
        logger.debug(f"Enqueued {task.mint} (priority {task.priority}, queue depth {self.queue.qsize()})")

    def enqueue_batch(self, tasks: List[FetchTask]) -> None:
        """Add multiple tasks to queue."""
        for task in tasks:
            self.enqueue(task)

    def start(self, fetch_fn: Callable) -> None:
        """Start the queue worker thread."""
        if self.running:
            logger.warning("Queue worker already running")
            return

        self.running = True
        self.fetch_fn = fetch_fn
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        logger.info(f"Price fetch queue started (max_concurrent={self.max_concurrent}, delay={self.request_delay_ms*1000}ms)")

    def stop(self) -> None:
        """Stop the queue worker thread."""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        logger.info("Price fetch queue stopped")

    def _worker_loop(self) -> None:
        """Main worker loop that processes queue."""
        while self.running:
            try:
                # Try to get a task with timeout
                try:
                    task = self.queue.get(timeout=1.0)
                except Empty:
                    # No task available; keep waiting
                    continue

                # Wait for a slot to become available (concurrency limit)
                while self.active_requests >= self.max_concurrent:
                    if not self.running:
                        self.queue.task_done()
                        return
                    time.sleep(0.1)

                # Apply rate limiting: delay between requests
                now = time.time()
                time_since_last = now - self.stats['last_request_at']
                if time_since_last < self.request_delay_ms and self.stats['last_request_at'] > 0:
                    sleep_time = self.request_delay_ms - time_since_last
                    if sleep_time > 0:
                        time.sleep(sleep_time)

                # Fetch price
                with self.lock:
                    self.active_requests += 1

                try:
                    start_time = time.time()
                    price = self.fetch_fn(task.mint)
                    latency_ms = (time.time() - start_time) * 1000

                    with self.lock:
                        # Update EWMA latency (smoother than arithmetic mean)
                        if self.latency_ewma == 0.0:
                            self.latency_ewma = latency_ms
                        else:
                            self.latency_ewma = (self.EWMA_ALPHA * self.latency_ewma) + \
                                              ((1.0 - self.EWMA_ALPHA) * latency_ms)

                        self.stats['processed'] += 1
                        self.stats['total_latency_ms'] += latency_ms
                        if self.stats['processed'] > 0:
                            self.stats['avg_latency_ms'] = (
                                self.stats['total_latency_ms'] / self.stats['processed']
                            )

                    # Call callback if provided
                    if task.callback:
                        task.callback(task.mint, price)

                    logger.debug(
                        f"Fetched {task.mint}: ${price.price_usd:.8f} "
                        f"(latency {latency_ms:.0f}ms, queue depth {self.queue.qsize()})"
                    )

                except Exception as e:
                    logger.error(f"Error fetching {task.mint}: {e}", exc_info=False)
                    with self.lock:
                        self.stats['failed'] += 1

                finally:
                    with self.lock:
                        self.active_requests -= 1
                        self.stats['last_request_at'] = time.time()
                        self.stats['queue_depth'] = self.queue.qsize()

                self.queue.task_done()

            except Exception as e:
                logger.error(f"Worker loop error: {e}", exc_info=True)
                time.sleep(1)

    def get_stats(self) -> Dict:
        """Return queue statistics with EWMA-based wait estimate."""
        with self.lock:
            avg_latency = self.stats['avg_latency_ms']
            depth = self.stats['queue_depth']
            request_delay = int(self.request_delay_ms * 1000)

            # Use EWMA latency for wait estimate (smoother, more responsive to spikes)
            latency_for_estimate = self.latency_ewma if self.latency_ewma > 0 else avg_latency
            queue_wait_estimate_ms = depth * (latency_for_estimate + self.request_delay_ms * 1000)

            return {
                'enqueued': self.stats['enqueued'],
                'processed': self.stats['processed'],
                'failed': self.stats['failed'],
                'queue_depth': depth,
                'active_requests': self.active_requests,
                'avg_latency_ms': round(avg_latency, 1),
                'ewma_latency_ms': round(self.latency_ewma, 1),
                'max_concurrent': self.max_concurrent,
                'request_delay_ms': request_delay,
                'queue_wait_estimate_ms': round(queue_wait_estimate_ms, 1),
            }

    def wait_until_empty(self, timeout_seconds: int = 30) -> bool:
        """
        Wait until queue is empty (all tasks processed).

        Args:
            timeout_seconds: Max time to wait

        Returns:
            True if queue became empty, False if timeout
        """
        start = time.time()
        while time.time() - start < timeout_seconds:
            if self.queue.empty() and self.active_requests == 0:
                return True
            time.sleep(0.1)
        return False


# Global queue instance
_price_queue: Optional[PriceFetchQueue] = None


def get_price_queue() -> PriceFetchQueue:
    """Get or create global price fetch queue."""
    global _price_queue
    if _price_queue is None:
        _price_queue = PriceFetchQueue(max_concurrent=3, request_delay_ms=200)
    return _price_queue


def start_price_queue_worker(fetch_fn: Callable) -> None:
    """Start the price fetch queue worker with the given fetch function."""
    queue = get_price_queue()
    queue.start(fetch_fn)


def stop_price_queue_worker() -> None:
    """Stop the price fetch queue worker."""
    global _price_queue
    if _price_queue:
        _price_queue.stop()
