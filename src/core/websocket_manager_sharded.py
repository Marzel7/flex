"""
WebSocket sharding manager for scaling to 1000+ pools.

Distributes pool subscriptions across multiple WS clients.
Each client supports ~400-500 subscriptions (Solana limit ~1000).
"""

import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class WebSocketManagerSharded:
    """
    Manages multiple WebSocket clients with automatic sharding.

    Architecture:
        WSClient1 → 400-500 accounts
        WSClient2 → 400-500 accounts
        WSClient3 → 400-500 accounts
    """

    MAX_SUBSCRIPTIONS_PER_CLIENT = 450

    def __init__(self, pool_state, db_path: str):
        self.pool_state = pool_state
        self.db_path = db_path
        self.clients: List = []
        self.stats = {
            'total_subscriptions': 0,
            'active_clients': 0,
            'reconnects': 0,
            'events_total': 0,
            'distribution': {}
        }

    def start(self, pools: List[Dict]) -> None:
        """Start WebSocket clients with sharding."""
        from src.core.pool_price_engine import PoolWebSocketClient

        # Calculate number of accounts and clients needed
        num_accounts = sum(2 for _ in pools)  # base + quote per pool
        clients_needed = max(1, (num_accounts + self.MAX_SUBSCRIPTIONS_PER_CLIENT - 1) // self.MAX_SUBSCRIPTIONS_PER_CLIENT)

        logger.info(
            f"[WS-MANAGER] Sharding: {len(pools)} pools → {num_accounts} accounts → {clients_needed} clients "
            f"(max {self.MAX_SUBSCRIPTIONS_PER_CLIENT}/client)"
        )

        # Distribute pools across clients
        pools_per_client = (len(pools) + clients_needed - 1) // clients_needed

        for client_id in range(clients_needed):
            start_idx = client_id * pools_per_client
            end_idx = min((client_id + 1) * pools_per_client, len(pools))
            client_pools = pools[start_idx:end_idx]

            if client_pools:
                client = PoolWebSocketClient(
                    self.pool_state,
                    self.db_path,
                    client_id=client_id
                )
                self.clients.append(client)

                num_accounts_for_client = len(client_pools) * 2
                logger.info(
                    f"[WS-{client_id}] Starting: {len(client_pools)} pools, {num_accounts_for_client} accounts"
                )

                client.start(client_pools)
                self.stats['distribution'][f'client_{client_id}'] = len(client_pools)

        self.stats['active_clients'] = len(self.clients)
        logger.info(f"[WS-MANAGER] Started {len(self.clients)} WebSocket clients")

    def refresh_pools(self, pools: List[Dict]) -> None:
        """Refresh pool subscriptions across all clients."""
        logger.info(f"[WS-MANAGER] Refreshing {len(pools)} pools across {len(self.clients)} clients")

        # Re-shard if needed
        new_clients_needed = max(1, (len(pools) * 2 + self.MAX_SUBSCRIPTIONS_PER_CLIENT - 1) // self.MAX_SUBSCRIPTIONS_PER_CLIENT)

        if new_clients_needed != len(self.clients):
            logger.info(f"[WS-MANAGER] Clients needed changed: {len(self.clients)} → {new_clients_needed}")
            self.stop()
            self.clients = []
            self.start(pools)
            return

        # Update subscriptions in existing clients
        pools_per_client = (len(pools) + len(self.clients) - 1) // len(self.clients)

        for client_id, client in enumerate(self.clients):
            start_idx = client_id * pools_per_client
            end_idx = min((client_id + 1) * pools_per_client, len(pools))
            client_pools = pools[start_idx:end_idx]

            if client_pools:
                try:
                    client.refresh_pools(client_pools)
                except Exception as e:
                    logger.error(f"[WS-{client_id}] Refresh error: {e}")

    def stop(self) -> None:
        """Stop all WebSocket clients."""
        logger.info(f"[WS-MANAGER] Stopping {len(self.clients)} clients")
        for client in self.clients:
            try:
                client.stop()
            except Exception as e:
                logger.warning(f"[WS-MANAGER] Stop error: {e}")
        self.clients.clear()

    def get_metrics(self) -> Dict:
        """Get metrics across all clients."""
        client_metrics = []

        for client in self.clients:
            try:
                client_metrics.append({
                    'client_id': client.client_id,
                    'subscriptions': len(client._account_to_pools),
                    'events_received': client.stats.get('events_received', 0),
                    'reconnects': client.stats.get('reconnects', 0),
                    'is_connected': client.stats.get('connected', False)
                })
            except Exception as e:
                logger.debug(f"Error getting metrics: {e}")

        return {
            'total_clients': len(self.clients),
            'total_subscriptions': sum(m['subscriptions'] for m in client_metrics),
            'total_events': sum(m['events_received'] for m in client_metrics),
            'clients': client_metrics
        }


def get_websocket_manager_sharded(pool_state, db_path: str) -> WebSocketManagerSharded:
    """Get or create WebSocket manager."""
    return WebSocketManagerSharded(pool_state, db_path)
