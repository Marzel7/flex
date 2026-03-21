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

    async def emit_cached_tx_diagnostics(self, cached_tx: Dict) -> Dict:
        """
        Emit detailed diagnostic info for why cached TX may yield zero candidates.
        
        Returns structured diagnostic dict with reason code and detailed metrics.
        
        Returns:
            {
                'reason_code': str,  # no_amm_program_in_tx | meta_incomplete | inner_instructions_only | etc
                'accounts_count': int,
                'writable_count': int,
                'amm_program_present': bool,
                'meta_has_owners': bool,
                'meta_accounts_count': int,
                'inner_instructions_count': int,
                'largest_accounts': List[str],
                'diagnostic_detail': str,
            }
        """
        try:
            if not cached_tx:
                return {
                    'reason_code': 'no_cached_tx',
                    'accounts_count': 0,
                    'writable_count': 0,
                    'amm_program_present': False,
                    'meta_has_owners': False,
                    'meta_accounts_count': 0,
                    'inner_instructions_count': 0,
                    'largest_accounts': [],
                    'diagnostic_detail': 'Cached TX not provided',
                }

            message = cached_tx.get("transaction", {}).get("message", {})
            accounts = message.get("accountKeys", []) or []
            meta = cached_tx.get("meta", {})
            loaded_addrs = meta.get("loadedAddresses", {})

            # Count writable accounts
            num_required_signers = message.get("header", {}).get("numRequiredSigners", 0)
            num_readonly_signed = message.get("header", {}).get("numReadonlySignedAccounts", 0)
            writable_count = num_required_signers - num_readonly_signed

            # Check for AMM programs
            POOL_PROGRAMS = {
                "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",  # PumpSwap
                "675kPX9MHTjS2zt1qrXrQVxwwp4W8gNzjX9oVhKt7Ck",  # Raydium
                "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",  # PumpFun V1
                "pmpA9A9n7CdrzJcm4E3rhZ4J8p9F3ZzK8Y9zCjR4Z5x",  # PumpFun V2
            }

            amm_program_present = any(str(addr) in POOL_PROGRAMS for addr in accounts)

            # Check meta accounts
            meta_accounts = meta.get("accounts", [])
            meta_has_owners = any(
                isinstance(entry, dict) and entry.get("owner") for entry in meta_accounts
            )
            meta_accounts_with_owner = sum(
                1 for entry in meta_accounts if isinstance(entry, dict) and entry.get("owner")
            )

            # Check inner instructions
            inner_instructions = meta.get("innerInstructions", [])
            inner_instructions_count = len(inner_instructions)

            # Get largest accounts by data size
            largest_accounts = []
            if meta_accounts:
                acct_with_size = [
                    (str(accounts[i]) if i < len(accounts) else f"loaded_{i}", 
                     entry.get("lamports", 0) if isinstance(entry, dict) else 0)
                    for i, entry in enumerate(meta_accounts)
                ]
                largest_accounts = [addr for addr, _ in sorted(acct_with_size, key=lambda x: x[1], reverse=True)[:5]]

            # Determine reason code
            reason_code = "unknown"

            if not accounts:
                reason_code = "no_accounts_in_tx"
            elif not amm_program_present and not meta_has_owners:
                reason_code = "no_amm_program_in_tx"
            elif inner_instructions_count > 0 and not amm_program_present:
                reason_code = "inner_instructions_only"
            elif meta_accounts_with_owner == 0 and accounts:
                reason_code = "meta_incomplete"
            elif amm_program_present and not meta_has_owners:
                reason_code = "meta_owner_not_indexed"
            elif accounts and meta_accounts and meta_accounts_with_owner > 0:
                reason_code = "meta_has_owners_but_no_pool_matches"
            else:
                reason_code = "other_reason"

            diagnostic = {
                'reason_code': reason_code,
                'accounts_count': len(accounts),
                'writable_count': writable_count,
                'amm_program_present': amm_program_present,
                'meta_has_owners': meta_has_owners,
                'meta_accounts_count': meta_accounts_with_owner,
                'inner_instructions_count': inner_instructions_count,
                'largest_accounts': largest_accounts,
                'diagnostic_detail': f"reason={reason_code} accounts={len(accounts)} writable={writable_count} amm_present={amm_program_present} meta_owners={meta_accounts_with_owner} inner_ix={inner_instructions_count}",
            }

            logger.info(f"[CACHED_TX_PARSE_DIAGNOSTIC] {diagnostic['diagnostic_detail']}")
            return diagnostic

        except Exception as e:
            logger.warning(f"[CACHED_TX_PARSE_DIAGNOSTIC] Error: {e}")
            return {
                'reason_code': 'diagnostic_error',
                'accounts_count': 0,
                'writable_count': 0,
                'amm_program_present': False,
                'meta_has_owners': False,
                'meta_accounts_count': 0,
                'inner_instructions_count': 0,
                'largest_accounts': [],
                'diagnostic_detail': str(e),
            }

    async def parse_candidates_from_cached_tx(self, cached_tx: Dict) -> tuple:
        """
        Extract pool candidates ONLY from cached transaction payload.

        NO RPC calls. NO refetching. NO "not indexed yet" logic.

        Pure parsing: extract accounts from the cached TX object only.
        If TX is present but lacks accounts, returns empty list (not a failure).

        Args:
            cached_tx: Pre-fetched transaction data from handle_migration cache

        Returns:
            (candidates: List[str], parsed_successfully: bool, candidate_count: int, diagnostics: Dict)
            - candidates: List of pool addresses (may be empty if TX lacks accounts)
            - parsed_successfully: True if TX was parsed (even if no candidates)
            - candidate_count: Number of candidates found
            - diagnostics: Dict with reason_code when count==0, else empty dict
        """
        try:
            logger.info(
                f"[CACHED_TX_PARSE] Parsing candidates from cached TX payload (no RPC)"
            )

            if not cached_tx:
                logger.warning("[CACHED_TX_PARSE] cached_tx is None or empty")
                diag = await self.emit_cached_tx_diagnostics(cached_tx)
                return [], False, 0, diag

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
                diag = await self.emit_cached_tx_diagnostics(cached_tx)
                return [], False, 0, diag

            if not accounts:
                logger.info("[CACHED_TX_PARSE] No accounts found in cached TX")
                diag = await self.emit_cached_tx_diagnostics(cached_tx)
                return [], True, 0, diag
            
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

            # If zero candidates, emit diagnostics
            if len(candidates) == 0:
                diag = await self.emit_cached_tx_diagnostics(cached_tx)
                return candidates, True, len(candidates), diag
            else:
                return candidates, True, len(candidates), {}

        except Exception as e:
            logger.warning(f"[CACHED_TX_PARSE] Parse error: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            diag = await self.emit_cached_tx_diagnostics(cached_tx)
            return [], False, 0, diag

    async def discover_follow_on_pools(
        self,
        mint: str,
        migration_sig: str,
        bonding_curve: str = None,
        creator: str = None,
        token_mint: str = None,
        max_txs_per_anchor: int = 20,
        time_window_seconds: int = 30,
    ) -> tuple:
        """
        Discover pools from follow-on transactions after migration.

        Searches transactions related to migration context (bonding curve, creator, mint)
        within a bounded time window and RPC budget.

        Args:
            mint: Token mint address
            migration_sig: Original migration transaction signature
            bonding_curve: Bonding curve address (primary anchor)
            creator: Creator address (secondary anchor)
            token_mint: Token mint address (fallback anchor)
            max_txs_per_anchor: Max signatures to scan per anchor
            time_window_seconds: Search window after migration

        Returns:
            (pool_address: str, anchor_used: str, offset: int, txs_scanned: int)
            - pool_address: Found pool address or None
            - anchor_used: Which anchor worked (bonding_curve | creator | mint)
            - offset: Number of TXs after migration signature
            - txs_scanned: Total signatures examined
        """
        try:
            import aiohttp
            import time as time_module

            logger.info(
                f"[FOLLOW_ON_DISCOVERY] Starting search for {mint[:16]}... "
                f"(bonding_curve={bonding_curve[:16] if bonding_curve else 'N/A'}...)"
            )

            # Priority order for anchors
            anchors = []
            if bonding_curve:
                anchors.append(("bonding_curve", bonding_curve))
            if creator:
                anchors.append(("creator", creator))
            if token_mint:
                anchors.append(("mint", token_mint))

            total_txs_scanned = 0
            rpc_calls_made_total = 0
            max_rpc_calls_total = 15  # Total RPC budget for follow-on
            # Allocate budget per anchor so fallbacks (creator) get a fair chance
            num_anchors = len(anchors) if anchors else 1
            max_rpc_calls_per_anchor = max(1, max_rpc_calls_total // num_anchors)

            # Fetch migration blockTime for time-window filtering
            migration_blocktime = None
            try:
                sig_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignatureStatuses",
                    "params": [[migration_sig], {"searchTransactionHistory": True}],
                }
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.rpc_url,
                        json=sig_payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            result = await resp.json()
                            statuses = result.get("result", {}).get("value", [])
                            if statuses and statuses[0]:
                                migration_blocktime = statuses[0].get("blockTime")
                                rpc_calls_made_total += 1
                                logger.debug(
                                    f"[FOLLOW_ON_DISCOVERY] Migration blockTime: {migration_blocktime}"
                                )
            except Exception as e:
                logger.debug(f"[FOLLOW_ON_DISCOVERY] Failed to fetch migration blockTime: {e}")

            for anchor_name, anchor_addr in anchors:
                if rpc_calls_made_total >= max_rpc_calls_total:
                    logger.info(
                        f"[FOLLOW_ON_DISCOVERY] Total RPC budget exhausted for {mint[:16]}..."
                    )
                    break

                rpc_calls_for_this_anchor = 0  # Reset per anchor

                if not anchor_addr:
                    continue

                logger.debug(
                    f"[FOLLOW_ON_DISCOVERY] Scanning anchor={anchor_name} ({anchor_addr[:16]}...)"
                )

                try:
                    # Fetch signatures for this anchor
                    # CRITICAL: Do NOT use "before": migration_sig
                    # Pool creation happens AFTER migration, not before
                    # We need most recent signatures, which include post-migration TXs
                    sig_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [
                            anchor_addr,
                            {
                                "limit": max_txs_per_anchor,
                            },
                        ],
                    }

                    async with aiohttp.ClientSession() as session:
                        async with session.post(
                            self.rpc_url,
                            json=sig_payload,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status != 200:
                                logger.debug(
                                    f"[FOLLOW_ON_DISCOVERY] RPC failed for {anchor_name}: {resp.status}"
                                )
                                continue

                            sig_result = await resp.json()
                            rpc_calls_for_this_anchor += 1
                            rpc_calls_made_total += 1

                    signatures = sig_result.get("result", [])
                    if not signatures:
                        logger.debug(
                            f"[FOLLOW_ON_DISCOVERY] No signatures found for anchor={anchor_name}"
                        )
                        continue

                    logger.debug(
                        f"[FOLLOW_ON_DISCOVERY] Found {len(signatures)} signatures for {anchor_name}"
                    )

                    # Inspect each signature for pool creation
                    for sig_idx, sig_info in enumerate(signatures[:max_txs_per_anchor]):
                        if rpc_calls_for_this_anchor >= max_rpc_calls_per_anchor:
                            logger.debug(f"[FOLLOW_ON_DISCOVERY] RPC budget exhausted for anchor={anchor_name}")
                            break
                        if rpc_calls_made_total >= max_rpc_calls_total:
                            break

                        sig = sig_info.get("signature")
                        if not sig:
                            continue

                        total_txs_scanned += 1

                        # Skip if signature is the migration TX itself
                        if sig == migration_sig:
                            continue

                        # Check time window
                        block_time = sig_info.get("blockTime")
                        if migration_blocktime and block_time:
                            time_diff = block_time - migration_blocktime
                            # Skip if TX is before migration or outside window
                            if time_diff < 0 or time_diff > time_window_seconds:
                                continue

                        try:
                            # Fetch transaction
                            tx_payload = {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTransaction",
                                "params": [sig, {"encoding": "jsonParsed"}],
                            }

                            async with aiohttp.ClientSession() as session:
                                async with session.post(
                                    self.rpc_url,
                                    json=tx_payload,
                                    timeout=aiohttp.ClientTimeout(total=10),
                                ) as resp:
                                    if resp.status != 200:
                                        continue

                                    tx_result = await resp.json()
                                    rpc_calls_for_this_anchor += 1
                            rpc_calls_made_total += 1

                            tx_data = tx_result.get("result")
                            if not tx_data:
                                continue

                            # Extract and inspect candidates from this TX
                            candidates = await self._extract_pool_candidates_from_tx(
                                tx_data, anchor_name
                            )

                            for candidate in candidates:
                                logger.debug(
                                    f"[FOLLOW_ON_DISCOVERY] Found candidate {candidate[:16]}... "
                                    f"from anchor={anchor_name} at offset={sig_idx}"
                                )

                                # Validate candidate via RPC
                                if rpc_calls_for_this_anchor >= max_rpc_calls_per_anchor:
                                    break
                                if rpc_calls_made_total >= max_rpc_calls_total:
                                    break

                                try:
                                    acct_payload = {
                                        "jsonrpc": "2.0",
                                        "id": 1,
                                        "method": "getAccountInfo",
                                        "params": [candidate, {"encoding": "base64"}],
                                    }

                                    async with aiohttp.ClientSession() as session:
                                        async with session.post(
                                            self.rpc_url,
                                            json=acct_payload,
                                            timeout=aiohttp.ClientTimeout(total=10),
                                        ) as resp:
                                            if resp.status != 200:
                                                continue

                                            acct_result = await resp.json()
                                            rpc_calls_for_this_anchor += 1
                                            rpc_calls_made_total += 1

                                    acct = acct_result.get("result", {}).get("value")
                                    if not acct:
                                        continue

                                    owner = acct.get("owner")
                                    if owner and owner in {
                                        "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
                                        "675kPX9MHTjS2zt1qrXrQVxwwp4W8gNzjX9oVhKt7Ck",
                                        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
                                        "pmpA9A9n7CdrzJcm4E3rhZ4J8p9F3ZzK8Y9zCjR4Z5x",
                                    }:
                                        logger.info(
                                            f"[FOLLOW_ON_DISCOVERY] ✅ Found valid pool {candidate[:16]}... "
                                            f"via anchor={anchor_name} at offset={sig_idx}"
                                        )
                                        return candidate, anchor_name, sig_idx, total_txs_scanned

                                except Exception as e:
                                    logger.debug(
                                        f"[FOLLOW_ON_DISCOVERY] Error validating candidate: {e}"
                                    )
                                    continue

                        except Exception as e:
                            logger.debug(
                                f"[FOLLOW_ON_DISCOVERY] Error processing signature {sig[:16]}...: {e}"
                            )
                            continue

                except Exception as e:
                    logger.debug(
                        f"[FOLLOW_ON_DISCOVERY] Error scanning anchor {anchor_name}: {e}"
                    )
                    continue

            logger.info(
                f"[FOLLOW_ON_DISCOVERY] No pool found after scanning {total_txs_scanned} TXs "
                f"({rpc_calls_made} RPC calls)"
            )
            return None, None, None, total_txs_scanned

        except Exception as e:
            logger.warning(f"[FOLLOW_ON_DISCOVERY] Error: {e}")
            import traceback

            logger.debug(traceback.format_exc())
            return None, None, None, 0

    async def _extract_pool_candidates_from_tx(
        self, tx_data: Dict, anchor_name: str
    ) -> List[str]:
        """Extract potential pool candidates from a transaction."""
        try:
            candidates = []

            # Extract accounts from transaction
            message = tx_data.get("transaction", {}).get("message", {})
            accounts = message.get("accountKeys", []) or []

            # Include loaded addresses
            meta = tx_data.get("meta", {})
            loaded_addrs = meta.get("loadedAddresses", {})
            accounts = accounts + loaded_addrs.get("writable", []) + loaded_addrs.get("readonly", [])

            # Convert to strings
            accounts = [str(addr) if not isinstance(addr, str) else addr for addr in accounts]

            # Known pool programs
            POOL_PROGRAMS = {
                "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
                "675kPX9MHTjS2zt1qrXrQVxwwp4W8gNzjX9oVhKt7Ck",
                "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
                "pmpA9A9n7CdrzJcm4E3rhZ4J8p9F3ZzK8Y9zCjR4Z5x",
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

            # Look for accounts with pool program owner
            meta_accounts = meta.get("accounts", [])
            for i, account_addr in enumerate(accounts):
                if account_addr in SYSTEM_PROGRAMS:
                    continue

                # Check meta for owner info
                if i < len(meta_accounts):
                    meta_entry = meta_accounts[i]
                    if isinstance(meta_entry, dict):
                        owner = meta_entry.get("owner")
                        if owner in POOL_PROGRAMS:
                            candidates.append(account_addr)

            return candidates

        except Exception as e:
            logger.debug(f"[FOLLOW_ON_DISCOVERY] Error extracting candidates: {e}")
            return []

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
