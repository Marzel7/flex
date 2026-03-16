"""
Post-migration pool discovery fallback.

When the actual pool state account is not present in the migration transaction,
this module searches for it in three ways:

1. Recent transaction scan: Look for pool in subsequent transactions after migration
2. Token vault state: Use getTokenLargestAccounts to find vault accounts
3. Pool authority resolution: Resolve vault owner to find the pool state account

All paths feed results through the same hardened parser/extraction validation.
"""

import logging
import asyncio
import aiohttp
from typing import Optional, Dict, List
from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)

class PostMigrationPoolDiscovery:
    """Discover pools created or visible after migration transaction."""

    def __init__(self, rpc_url: str, pool_detector=None):
        self.rpc_url = rpc_url
        # Create detector if not provided
        if pool_detector is None:
            from src.core.pool_detector import PoolDetector
            import os
            debug_mode = os.getenv("POOL_DETECTOR_DEBUG", "true").lower() == "true"
            pool_detector = PoolDetector(rpc_url, debug=debug_mode)
        self.pool_detector = pool_detector
        self.processed_sigs = {}  # {mint: set(signatures)} to avoid duplicate processing  # {mint: set(signatures)} to avoid duplicate processing

    async def discover_pool_post_migration(
        self,
        mint: str,
        original_migration_sig: str,
        delays: List[int] = None
    ) -> Optional[str]:
        """
        Attempt to find pool after migration via multiple strategies.

        Args:
            mint: Token mint address
            original_migration_sig: Original migration transaction signature
            delays: List of delays (seconds) between retry attempts

        Returns:
            Pool address if found, None otherwise
        """
        if delays is None:
            delays = [10, 30, 60]

        self.processed_sigs.setdefault(mint, set())
        self.processed_sigs[mint].add(original_migration_sig)

        for attempt, delay in enumerate(delays, 1):
            await asyncio.sleep(delay)

            logger.info(
                f"[POOL_DISCOVERY] mint={mint[:20]}... attempt={attempt}/{len(delays)} "
                f"(delay={delay}s)"
            )

            # ===== Strategy 1: Recent transaction scan =====
            pool = await self._discover_via_recent_transactions(mint, original_migration_sig)
            if pool:
                logger.info(
                    f"[POOL_DISCOVERY] ✅ Found pool via recent_tx: {pool[:16]}..."
                )
                return pool

            # ===== Strategy 2: Vault state fallback =====
            pool = await self._discover_via_token_vault_state(mint)
            if pool:
                logger.info(
                    f"[POOL_DISCOVERY] ✅ Found pool via vault_fallback: {pool[:16]}..."
                )
                return pool

        logger.warning(
            f"[POOL_DISCOVERY] ❌ All post-migration strategies exhausted for {mint[:20]}..."
        )
        return None

    async def _discover_via_recent_transactions(
        self,
        mint: str,
        original_sig: str
    ) -> Optional[str]:
        """
        Scan recent transactions for pool discovery.

        Fetches signatures after the migration and attempts detection on each.
        """
        try:
            logger.debug(f"[POOL_DISCOVERY] Scanning recent transactions for {mint[:20]}...")

            # Get recent signatures involving the mint
            signatures = await self._get_recent_related_signatures(mint)

            if not signatures:
                logger.debug("[POOL_DISCOVERY] No recent signatures found")
                return None

            logger.debug(f"[POOL_DISCOVERY] Found {len(signatures)} recent signatures")

            for sig in signatures:
                # Skip already-processed signatures
                if sig in self.processed_sigs.get(mint, set()):
                    continue

                self.processed_sigs[mint].add(sig)

                # Fetch transaction
                tx_data = await self._fetch_transaction(sig)
                if not tx_data:
                    logger.debug(f"[POOL_DISCOVERY] Could not fetch {sig[:16]}...")
                    continue

                # Try detection on this transaction
                pool = await self.pool_detector.detect_pool_from_tx(tx_data, mint)
                if pool:
                    logger.info(
                        f"[POOL_DISCOVERY] ✅ Pool found in transaction {sig[:16]}..."
                    )
                    return pool

                logger.debug(f"[POOL_DISCOVERY] No pool in {sig[:16]}...")

        except Exception as e:
            logger.warning(f"[POOL_DISCOVERY] Error scanning recent transactions: {e}")

        return None

    async def _discover_via_token_vault_state(self, mint: str) -> Optional[str]:
        """
        Discover pool using token vault state (getTokenLargestAccounts).

        Uses the largest token accounts as vault candidates and resolves pool authority.
        """
        try:
            logger.debug(f"[POOL_DISCOVERY] Attempting vault state discovery for {mint[:20]}...")

            # Get largest token accounts for this mint
            largest_accounts = await self._get_token_largest_accounts(mint)

            if not largest_accounts:
                logger.debug("[POOL_DISCOVERY] No token accounts found")
                return None

            logger.debug(f"[POOL_DISCOVERY] Found {len(largest_accounts)} token accounts")

            # Try to resolve pool from vault accounts
            for vault_account in largest_accounts:
                vault_info = await self._fetch_account_info(vault_account)
                if not vault_info:
                    continue

                # SPL token account owner is at offset 32-64
                # The owner is typically the pool PDA
                owner_bytes = vault_info.get("data", [None])[0]
                if isinstance(owner_bytes, str):
                    from base64 import b64decode
                    owner_bytes = b64decode(owner_bytes)

                if owner_bytes and len(owner_bytes) >= 64:
                    try:
                        pool_candidate = str(Pubkey(owner_bytes[32:64]))
                        logger.debug(f"[POOL_DISCOVERY] Trying pool candidate {pool_candidate[:16]}...")

                        # Validate through parser
                        pool_data = await self._fetch_account_info(pool_candidate)
                        if pool_data:
                            # Use detector's parser to validate
                            result = self.pool_detector.parser_dispatcher.for_program(
                                pool_data.get("owner")
                            ).try_parse(pool_data.get("data", [None])[0])

                            if result:
                                logger.info(
                                    f"[POOL_DISCOVERY] ✅ Vault owner validates as pool: {pool_candidate[:16]}..."
                                )
                                return pool_candidate

                    except Exception as e:
                        logger.debug(f"[POOL_DISCOVERY] Could not resolve vault owner: {e}")

        except Exception as e:
            logger.warning(f"[POOL_DISCOVERY] Error in vault state discovery: {e}")

        return None

    async def _get_recent_related_signatures(self, mint: str) -> List[str]:
        """
        Get recent signatures related to the mint and PumpSwap/Raydium programs.

        This would use getSignaturesForAddress on the mint with filters.
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [
                    mint,
                    {"limit": 20, "commitment": "finalized"}
                ]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    result = await resp.json()
                    if "result" in result and result["result"]:
                        return [sig["signature"] for sig in result["result"]]

        except Exception as e:
            logger.debug(f"[POOL_DISCOVERY] Error getting signatures: {e}")

        return []

    async def _get_token_largest_accounts(self, mint: str) -> List[str]:
        """
        Get the largest token accounts for a mint.

        Returns account addresses of the largest token account holders.
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [mint]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    result = await resp.json()
                    if "result" in result and result["result"]:
                        return [
                            acct["address"]
                            for acct in result["result"]["value"][:3]  # Top 3 accounts
                        ]

        except Exception as e:
            logger.debug(f"[POOL_DISCOVERY] Error getting token largest accounts: {e}")

        return []

    async def _fetch_transaction(self, signature: str) -> Optional[Dict]:
        """Fetch transaction data from RPC."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [signature, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    result = await resp.json()
                    if "result" in result and result["result"]:
                        return result["result"]

        except Exception as e:
            logger.debug(f"[POOL_DISCOVERY] Error fetching transaction {signature[:16]}...: {e}")

        return None

    async def _fetch_account_info(self, address: str) -> Optional[Dict]:
        """Fetch account info from RPC."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "base64", "commitment": "finalized"}]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    result = await resp.json()
                    if "result" in result and result["result"]:
                        return result["result"]["value"]

        except Exception as e:
            logger.debug(f"[POOL_DISCOVERY] Error fetching account {address[:16]}...: {e}")

        return None
