"""
TokenHub — mint-filtered WebSocket pub/sub hub.

Clients subscribe to specific mints; only those clients receive
updates when a price is refreshed for that mint.

Thread-safe: publish() is called from the price worker thread.
"""

import json
import logging
import threading
from collections import defaultdict
from typing import Any, Dict, Set

logger = logging.getLogger(__name__)


class TokenHub:
    def __init__(self):
        # mint -> set of ws connections
        self._subs: Dict[str, Set] = defaultdict(set)
        # ws -> set of mints (for fast cleanup on disconnect)
        self._client_mints: Dict[Any, Set[str]] = defaultdict(set)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, ws, mints: list[str]) -> None:
        with self._lock:
            for mint in mints:
                self._subs[mint].add(ws)
                self._client_mints[ws].add(mint)

    def unsubscribe(self, ws, mints: list[str]) -> None:
        with self._lock:
            for mint in mints:
                self._subs[mint].discard(ws)
                self._client_mints[ws].discard(mint)
                if not self._subs[mint]:
                    del self._subs[mint]

    def remove_client(self, ws) -> None:
        """Remove all subscriptions for a disconnected client."""
        with self._lock:
            mints = list(self._client_mints.pop(ws, []))
            for mint in mints:
                self._subs[mint].discard(ws)
                if not self._subs[mint]:
                    del self._subs[mint]

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(self, mint: str, payload: dict) -> None:
        """Send payload to all clients subscribed to this mint."""
        with self._lock:
            targets = list(self._subs.get(mint, []))

        if not targets:
            return

        message = json.dumps(payload)
        dead = []
        for ws in targets:
            try:
                ws.send(message)
            except Exception:
                dead.append(ws)

        if dead:
            for ws in dead:
                self.remove_client(ws)

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._client_mints)


# Singleton
_hub = TokenHub()


def get_token_hub() -> TokenHub:
    return _hub
