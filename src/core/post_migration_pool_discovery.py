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

    async def discover_pool_via_migration_transaction(
        self,
        mint: str,
        migration_sig: str
    ) -> Optional[str]:
        """
        Extract pool accounts directly from migration transaction.

        This is the most reliable discovery method because:
        1. Pool accounts are created/referenced IN the migration TX
        2. No RPC API limitations (uses standard getTransaction)
        3. Works even when RPC doesn't support filtered getProgramAccounts

        Strategy:
        1. Get migration TX
        2. Extract all account addresses
        3. Filter by known pool program owners, preferring larger accounts
           (larger = more likely to be actual pool state, not helper/config)
        4. Return pool address if found (validation happens in caller)

        Note: Returns the largest pool-sized account, as this is typically
        the actual pool state account. Helper/config accounts are usually
        much smaller.

        Args:
            mint: Token mint address
            migration_sig: Migration transaction signature

        Returns:
            Pool address if found, None otherwise
        """
        try:
            logger.info(
                f"[POOL_DISCOVERY_MIGRATION_TX] Extracting from {migration_sig[:20]}..."
            )

            # Fetch migration transaction
            tx_data = await self._fetch_transaction(migration_sig)
            if not tx_data:
                logger.warning("[POOL_DISCOVERY_MIGRATION_TX] Could not fetch transaction")
                return None

            # Extract all account addresses from the transaction
            try:
                message = tx_data.get("transaction", {}).get("message", {})
                accounts = message.get("accountKeys", [])
                meta = tx_data.get("meta", {})

                # Also include loaded addresses from versioned transactions
                loaded_addrs = meta.get("loadedAddresses", {})
                accounts = accounts + loaded_addrs.get("writable", []) + loaded_addrs.get("readonly", [])

            except (KeyError, TypeError):
                logger.warning("[POOL_DISCOVERY_MIGRATION_TX] Could not extract accounts")
                return None

            logger.debug(f"[POOL_DISCOVERY_MIGRATION_TX] Found {len(accounts)} accounts")

            # Known pool program owners (where pools live)
            POOL_PROGRAMS = {
                "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # PumpSwap
                "675kPX9MHTjS2zt1qrXrQVxwwp4W8gNzjX9oVhKt7Ck",  # Raydium
                "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # PumpFun V1
                "pmpA9A9n7CdrzJcm4E3rhZ4J8p9F3ZzK8Y9zCjR4Z5x",  # PumpFun V2
            }

            # System programs to skip
            SYSTEM_PROGRAMS = {
                "11111111111111111111111111111111",
                "ComputeBudget111111111111111111111111111111",
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                "So11111111111111111111111111111111111111112",
                "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
            }

            # Check each account's owner and size
            # Minimum size for Raydium AMM pool state: 296 bytes
            MIN_POOL_STATE_SIZE = 296

            # Collect all valid candidates, then return the largest (most likely to be real pool)
            candidates = []

            for account_addr in accounts:
                # Skip system programs
                if account_addr in SYSTEM_PROGRAMS:
                    continue

                # Get account info to check owner and size
                try:
                    account_info = await self._fetch_account_info(account_addr)
                    if not account_info:
                        continue

                    owner = account_info.get("owner")
                    if owner not in POOL_PROGRAMS:
                        continue

                    # Check account size (filter out helper accounts which are typically small)
                    data = account_info.get("data")
                    if isinstance(data, list) and len(data) > 0:
                        import base64
                        try:
                            decoded = base64.b64decode(data[0])
                            data_size = len(decoded)
                        except:
                            data_size = 0
                    else:
                        data_size = 0

                    # Reject accounts smaller than minimum pool state size
                    if data_size < MIN_POOL_STATE_SIZE:
                        logger.debug(
                            f"[POOL_DISCOVERY_MIGRATION_TX] Skipping {account_addr[:16]}... "
                            f"(size={data_size} < {MIN_POOL_STATE_SIZE})"
                        )
                        continue

                    candidates.append((account_addr, owner, data_size))

                except Exception as e:
                    logger.debug(f"[POOL_DISCOVERY_MIGRATION_TX] Error checking {account_addr[:16]}...: {e}")
                    continue

            if not candidates:
                logger.info("[POOL_DISCOVERY_MIGRATION_TX] No pool programs found in transaction")
                return None

            # Sort candidates by size (largest first - more likely to be real pool state)
            candidates_sorted = sorted(candidates, key=lambda x: x[2], reverse=True)

            # Return the largest pool account (most likely to be real pool state, not a config account)
            account_addr, owner, data_size = candidates_sorted[0]

            logger.info(
                f"[POOL_DISCOVERY_MIGRATION_TX] ✅ Found {len(candidates)} pool candidates, "
                f"returning largest: {account_addr[:20]}... "
                f"(owner={owner[:16]}... size={data_size} bytes)"
            )
            return account_addr

        except Exception as e:
            logger.warning(
                f"[POOL_DISCOVERY_MIGRATION_TX] Error: {e}"
            )
            return None

    async def parse_candidates_from_cached_tx(self, cached_tx: Dict) -> tuple:
        """
        Extract pool candidates ONLY from cached transaction payload.
        
        NO RPC calls. NO refetching. NO "not indexed yet" logic.
        
        Pure parsing: extract accounts from the cached TX object only.
        If TX is present but lacks accounts, returns empty list (not a failure).
        
        Args:
            cached_tx: Pre-fetched transaction data from handle_migration cache
            
        Returns:
            (candidates: List[str], parsed_successfully: bool, candidate_count: int)
            - candidates: List of pool addresses (may be empty if TX lacks accounts)
            - parsed_successfully: True if TX was parsed (even if no candidates)
            - candidate_count: Number of candidates found
        """
        try:
            logger.info(
                f"[CACHED_TX_PARSE] Parsing candidates from cached TX payload (no RPC)"
            )
            
            if not cached_tx:
                logger.warning("[CACHED_TX_PARSE] cached_tx is None or empty")
                return [], False, 0
            
            # Extract accounts from cached TX structure
            try:
                message = cached_tx.get("transaction", {}).get("message", {})
                accounts = message.get("accountKeys", [])
                meta = cached_tx.get("meta", {})
                
                # Also include loaded addresses from versioned transactions
                loaded_addrs = meta.get("loadedAddresses", {})
                accounts = accounts + loaded_addrs.get("writable", []) + loaded_addrs.get("readonly", [])
                
                # Convert accounts to strings
                accounts = [str(addr) if not isinstance(addr, str) else addr for addr in accounts]
                
            except (KeyError, TypeError) as e:
                logger.warning(f"[CACHED_TX_PARSE] Could not extract accounts from structure: {e}")
                return [], False, 0
            
            if not accounts:
                logger.info("[CACHED_TX_PARSE] No accounts found in cached TX")
                return [], True, 0
            
            logger.debug(f"[CACHED_TX_PARSE] Found {len(accounts)} accounts in cached TX")
            
            # Known pool program owners
            POOL_PROGRAMS = {
                "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # PumpSwap
                "675kPX9MHTjS2zt1qrXrQVxwwp4W8gNzjX9oVhKt7Ck",  # Raydium
                "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # PumpFun V1
                "pmpA9A9n7CdrzJcm4E3rhZ4J8p9F3ZzK8Y9zCjR4Z5x",  # PumpFun V2
            }
            
            # System programs to skip
            SYSTEM_PROGRAMS = {
                "11111111111111111111111111111111",
                "ComputeBudget111111111111111111111111111111",
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                "So11111111111111111111111111111111111111112",
                "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
            }
            
            # Skip accounts
            SKIP_ACCOUNTS = {
                "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf",
                "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw",
            }
            
            # Collect candidates by checking owner field from cached meta
            # NOTE: We can only identify candidates if their owner is in meta, or we have to skip
            # For cached-only parsing, we identify by message structure + metadata hints
            candidates = []
            meta_accounts = meta.get("accounts", [])  # Account info from meta if available
            
            for i, account_addr in enumerate(accounts):
                if not isinstance(account_addr, str):
                    account_addr = str(account_addr)
                
                if account_addr in SYSTEM_PROGRAMS or account_addr in SKIP_ACCOUNTS:
                    continue
                
                # For cached TX, we can check if this account appears in meta with owner info
                # Otherwise we mark it as a potential candidate (caller must validate via RPC)
                if i < len(meta_accounts):
                    meta_entry = meta_accounts[i]
                    owner = meta_entry.get("owner") if isinstance(meta_entry, dict) else None
                    if owner and owner in POOL_PROGRAMS:
                        candidates.append(account_addr)
                        logger.debug(f"[CACHED_TX_PARSE] Found pool candidate: {account_addr[:16]}... (owner={owner[:16]}...)")
                        continue
                
                # Fallback: if owner not in meta, can't determine from cached TX alone
                # Skip (caller should use RPC if needed)
            
            logger.info(
                f"[CACHED_TX_PARSE] Successfully parsed: found {len(candidates)} candidates from cached TX"
            )
            return candidates, True, len(candidates)
        
        except Exception as e:
            logger.warning(f"[CACHED_TX_PARSE] Parse error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return [], False, 0

    async def discover_pool_candidates_from_migration_tx(
        self,
        mint: str,
        migration_sig: str,
        tx_data: Optional[Dict] = None
    ) -> list:
        """
        Extract ALL pool candidates from migration transaction, sorted by likelihood.

        Returns a list of pool addresses sorted by size (largest first).
        Caller can try each candidate for extraction until one succeeds.

        Args:
            mint: Token mint address
            migration_sig: Migration transaction signature
            tx_data: Optional pre-fetched transaction data (avoids redundant RPC call)

        Returns:
            List of pool addresses, ordered by likelihood (largest first)
        """
        try:
            logger.info(
                f"[POOL_DISCOVERY_MIGRATION_TX_CANDIDATES] Extracting from {migration_sig[:20]}..."
            )

            # Use provided tx_data or fetch it
            if not tx_data:
                tx_data = await self._fetch_transaction(migration_sig)
            
            if not tx_data:
                logger.warning("[POOL_DISCOVERY_MIGRATION_TX_CANDIDATES] Could not fetch transaction")
                return []

            # Extract all account addresses from the transaction
            try:
                message = tx_data.get("transaction", {}).get("message", {})
                accounts = message.get("accountKeys", [])
                meta = tx_data.get("meta", {})

                # Also include loaded addresses from versioned transactions
                loaded_addrs = meta.get("loadedAddresses", {})
                accounts = accounts + loaded_addrs.get("writable", []) + loaded_addrs.get("readonly", [])

                # Convert accounts to strings (in case they're dicts or objects)
                accounts = [str(addr) if not isinstance(addr, str) else addr for addr in accounts]

            except (KeyError, TypeError) as e:
                logger.warning(f"[POOL_DISCOVERY_MIGRATION_TX_CANDIDATES] Could not extract accounts: {e}")
                return []

            logger.debug(f"[POOL_DISCOVERY_MIGRATION_TX_CANDIDATES] Found {len(accounts)} accounts")

            # Known pool program owners
            POOL_PROGRAMS = {
                "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # PumpSwap
                "675kPX9MHTjS2zt1qrXrQVxwwp4W8gNzjX9oVhKt7Ck",  # Raydium
                "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # PumpFun V1
                "pmpA9A9n7CdrzJcm4E3rhZ4J8p9F3ZzK8Y9zCjR4Z5x",  # PumpFun V2
            }

            # System programs to skip
            SYSTEM_PROGRAMS = {
                "11111111111111111111111111111111",
                "ComputeBudget111111111111111111111111111111",
                "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                "So11111111111111111111111111111111111111112",
                "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
            }

            # Accounts to skip (shared state/config, not token-specific pools)
            SKIP_ACCOUNTS = {
                "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf",  # Shared migration state (741 bytes)
                "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw",  # Shared program state (643 bytes, extracts non-existent vaults)
            }

            # Collect all valid candidates
            MIN_POOL_STATE_SIZE = 296
            candidates = []

            for account_addr in accounts:
                # Ensure account_addr is a string
                if not isinstance(account_addr, str):
                    account_addr = str(account_addr)
                
                if account_addr in SYSTEM_PROGRAMS or account_addr in SKIP_ACCOUNTS:
                    continue

                try:
                    account_info = await self._fetch_account_info(account_addr)
                    if not account_info:
                        continue

                    owner = account_info.get("owner")
                    if owner not in POOL_PROGRAMS:
                        continue

                    # Check account size
                    data = account_info.get("data")
                    if isinstance(data, list) and len(data) > 0:
                        import base64
                        try:
                            decoded = base64.b64decode(data[0])
                            data_size = len(decoded)
                        except:
                            data_size = 0
                    else:
                        data_size = 0

                    if data_size < MIN_POOL_STATE_SIZE:
                        continue

                    candidates.append((account_addr, owner, data_size))

                except Exception as e:
                    logger.debug(f"[POOL_DISCOVERY_MIGRATION_TX_CANDIDATES] Error checking {str(account_addr)[:16]}...: {e}")
                    continue

            if not candidates:
                logger.info("[POOL_DISCOVERY_MIGRATION_TX_CANDIDATES] No valid pool candidates found")
                return []

            # Sort by size (largest first)
            candidates_sorted = sorted(candidates, key=lambda x: x[2], reverse=True)
            result = [addr for addr, owner, size in candidates_sorted]

            logger.info(
                f"[POOL_DISCOVERY_MIGRATION_TX_CANDIDATES] Found {len(result)} candidates: "
                f"{', '.join(addr[:16] + '...' for addr in result)}"
            )
            return result

        except Exception as e:
            logger.warning(
                f"[POOL_DISCOVERY_MIGRATION_TX_CANDIDATES] Error: {e}"
            )
            import traceback
            logger.debug(traceback.format_exc())
            return []

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

    async def discover_pumpfun_v1_vault_pair(self, mint: str, pool_address: str) -> Optional[str]:
        """
        Discover vault pair for PumpFun V1 pool.
        
        PumpFun V1 pools don't store vault addresses in pool state.
        The "vault pair" for a Pump token is actually the PumpSwap pool account
        that holds the reserves after migration.
        
        Strategy: Query all accounts in recent transactions for this mint and find
        accounts owned by PumpSwap program that are roughly the right size (290-310 bytes).
        Falls back to searching by pool address if mint signatures aren't available.
        
        Returns the vault pair address (PumpSwap pool account), or None.
        """
        try:
            logger.info(f"[PUMPFUN_V1_VAULT_DISCOVERY] Discovering vault for {mint[:16]}...")
            
            PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
            found_candidates = {}
            
            # Strategy 1: Get signatures from mint
            signatures = await self._get_recent_related_signatures(mint)
            
            # Strategy 2: Also try pool address if provided
            if (not signatures or len(signatures) < 5) and pool_address:
                logger.info(f"[PUMPFUN_V1_VAULT_DISCOVERY] Fallback: querying pool address {pool_address[:16]}...")
                try:
                    payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [
                            pool_address,
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
                                pool_sigs = [sig["signature"] for sig in result["result"]]
                                signatures.extend(pool_sigs)
                except Exception as e:
                    logger.debug(f"[PUMPFUN_V1_VAULT_DISCOVERY] Error getting pool signatures: {e}")
            
            if not signatures:
                logger.warning("[PUMPFUN_V1_VAULT_DISCOVERY] No recent signatures found")
                return None
            
            logger.info(f"[PUMPFUN_V1_VAULT_DISCOVERY] Checking {len(signatures[:10])} recent signatures")
            
            # Check recent transactions for accounts owned by PumpSwap
            for sig in signatures[:10]:  # Check last 10 transactions
                try:
                    tx_data = await self._fetch_transaction(sig)
                    if not tx_data:
                        continue
                    
                    # Extract all accounts from transaction
                    try:
                        message = tx_data.get("transaction", {}).get("message", {})
                        accounts = message.get("accountKeys", [])
                        meta = tx_data.get("meta", {})
                        loaded_addrs = meta.get("loadedAddresses", {})
                        accounts = accounts + loaded_addrs.get("writable", []) + loaded_addrs.get("readonly", [])
                    except (KeyError, TypeError):
                        continue
                    
                    # Check each account
                    for account_addr in accounts:
                        if account_addr in found_candidates:
                            continue
                        
                        try:
                            account_info = await self._fetch_account_info(account_addr)
                            if not account_info:
                                continue
                            
                            owner = account_info.get("owner")
                            if owner != PUMPSWAP_PROGRAM:
                                continue
                            
                            # Check size (PumpSwap pools are typically 290-310 bytes)
                            data = account_info.get("data", [])
                            if isinstance(data, list) and len(data) > 0:
                                import base64
                                try:
                                    decoded = base64.b64decode(data[0])
                                    data_size = len(decoded)
                                except:
                                    data_size = 0
                            else:
                                data_size = 0
                            
                            if 290 <= data_size <= 310:
                                # This is a valid PumpSwap pool for this token
                                logger.debug(f"[PUMPFUN_V1_VAULT_DISCOVERY] Found candidate: {account_addr[:16]}... ({data_size} bytes)")
                                found_candidates[account_addr] = data_size
                        except Exception as e:
                            logger.debug(f"[PUMPFUN_V1_VAULT_DISCOVERY] Error checking {account_addr[:16]}...: {e}")
                            continue
                
                except Exception as e:
                    logger.debug(f"[PUMPFUN_V1_VAULT_DISCOVERY] Error processing tx {sig[:16]}...: {e}")
                    continue
            
            # Return the first valid candidate found (largest by size)
            if found_candidates:
                best_candidate = max(found_candidates.items(), key=lambda x: x[1])[0]
                logger.info(f"[PUMPFUN_V1_VAULT_DISCOVERY] ✅ Found vault pair: {best_candidate}")
                return best_candidate
            
            logger.warning("[PUMPFUN_V1_VAULT_DISCOVERY] No vault pair candidates found")
            return None
            
        except Exception as e:
            logger.error(f"[PUMPFUN_V1_VAULT_DISCOVERY] Error: {e}")
            return None

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
