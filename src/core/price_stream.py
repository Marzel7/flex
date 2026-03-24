"""
Real-time Price Stream via Server-Sent Events (SSE)

Implements a simple pub/sub system for broadcasting price updates
from the price worker to connected browser clients.

Architecture:
  Price Worker → broadcast(event) → All subscribed clients

Each client connects via GET /api/price-stream and receives
live price updates as they're computed.
"""

import asyncio
import threading
from typing import Set, Dict, Any, Optional
from queue import Queue  # Use thread-safe Queue instead of asyncio.Queue
import json
import logging

logger = logging.getLogger(__name__)


class PriceStream:
    """Thread-safe pub/sub for real-time price updates"""

    def __init__(self):
        self.subscribers: Set[Queue] = set()
        self._lock = threading.Lock()
        self._event_count = 0

    def subscribe(self) -> Queue:
        """
        Subscribe to price updates.

        Returns a thread-safe Queue that will receive price events.
        Client must call unsubscribe() to clean up.
        """
        queue = Queue(maxsize=100)  # Bounded queue to prevent memory issues
        with self._lock:
            self.subscribers.add(queue)
            count = len(self.subscribers)
            logger.info(f"[PRICE_STREAM] New subscriber. Total: {count}")
            print(f"[PRICE_STREAM_SUBSCRIBE] New subscriber connected. Total: {count}", flush=True)
        return queue

    def unsubscribe(self, queue: Queue) -> None:
        """Unsubscribe from price updates"""
        with self._lock:
            self.subscribers.discard(queue)
            logger.debug(f"[PRICE_STREAM] Subscriber removed. Total: {len(self.subscribers)}")

    async def broadcast(self, event: Dict[str, Any]) -> None:
        """
        Broadcast a price update event to all subscribers.

        Args:
            event: Dictionary with keys:
                - type: 'price_update'
                - mint: token mint address
                - price_usd: price in USD
                - price_sol: price in SOL (optional)
                - market_cap: market cap (optional)
                - liquidity_usd: liquidity in USD (optional)
                - source: 'pool' or 'dexscreener'
                - updated_at: unix timestamp
        """
        dead_queues = []

        with self._lock:
            subscriber_count = len(self.subscribers)
            subscribers = list(self.subscribers)  # Copy to avoid lock contention

        if not subscribers:
            return  # No one listening

        # Add metadata
        event["broadcast_id"] = self._event_count
        self._event_count += 1

        # Send to all subscribers
        for queue in subscribers:
            try:
                # Non-blocking put
                queue.put_nowait(event)
            except Exception as e:
                # Queue full or other error - subscriber not reading fast enough
                logger.warning(f"[PRICE_STREAM] Error sending to subscriber: {e}, removing")
                dead_queues.append(queue)

        # Clean up dead subscribers
        with self._lock:
            for queue in dead_queues:
                self.subscribers.discard(queue)

        if subscriber_count > 0:
            logger.debug(
                f"[PRICE_STREAM] Broadcast #{self._event_count}: {event.get('mint', '?')[:8]}... "
                f"price=${event.get('price_usd', '?')} to {subscriber_count} subscribers"
            )

    def get_subscriber_count(self) -> int:
        """Get current number of connected subscribers"""
        with self._lock:
            return len(self.subscribers)


# Global singleton instance
price_stream = PriceStream()


def get_price_stream() -> PriceStream:
    """Get the global price stream instance"""
    return price_stream
