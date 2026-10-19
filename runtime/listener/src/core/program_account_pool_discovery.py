"""
Program-account pool discovery fallback.

When pools don't appear in the migration transaction, search AMM program accounts directly.
Uses filtered getProgramAccounts queries to reduce candidate set, then validates each
candidate through the same hardened pipeline.
"""

import logging
import asyncio
import aiohttp
from typing import Optional, List, Dict, Tuple
from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)


class ProgramAccountPoolDiscovery:
    """Discover pools by querying AMM program accounts directly."""

    # Solana programs to search
    PUMPSWAP_PROGRAM = "PumpFun6WS79LYJSDhiBfk9YHgELDHSH4EvBiRVnqW"
    RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qrXrQVxwwp4W8gNzjX9oVhKt7Ck"

    # Pool state account sizes (used as RPC filter)
    RAYDIUM_POOL_SIZE = 696  # Raydium AMM v4 pool state size
    PUMPSWAP_POOL_SIZE_MIN = 296  # Minimum size for PumpSwap pools
    PUMPSWAP_POOL_SIZE_MAX = 500  # Maximum expected size

    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url

    async def discover_pool_via_program_accounts(
        self,
        mint: str,
        program_id: str = PUMPSWAP_PROGRAM,
        timeout: int = 30
    ) -> Optional[str]:
        """
        Search for pool by querying program accounts directly.

        Args:
            mint: Token mint address
            program_id: AMM program to search (default: PumpSwap)
            timeout: RPC timeout in seconds

        Returns:
            Pool address if found, None otherwise
        """
        try:
            logger.info(
                f"[POOL_DISCOVERY_PROGRAM] Searching {program_id[:16]}... "
                f"for pools of {mint[:20]}..."
            )

            # Build RPC request with size filter
            # This dramatically reduces candidates before validation
            pool_size = self.RAYDIUM_POOL_SIZE if program_id == self.RAYDIUM_AMM_PROGRAM \
                       else self.PUMPSWAP_POOL_SIZE_MIN

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getProgramAccounts",
                "params": [
                    program_id,
                    {
                        "encoding": "base64",
                        "dataSlice": None,  # Get full data
                        "filters": [
                            {
                                "dataSize": pool_size
                            }
                        ],
                        "commitment": "finalized"
                    }
                ]
            }

            logger.debug(
                f"[POOL_DISCOVERY_PROGRAM] RPC: getProgramAccounts "
                f"program={program_id[:16]}... dataSize={pool_size}"
            )

            # Query program accounts
            candidates = await self._fetch_program_accounts(payload, timeout)

            if not candidates:
                logger.debug(
                    f"[POOL_DISCOVERY_PROGRAM] No candidates found "
                    f"with size={pool_size} in {program_id[:16]}..."
                )
                return None

            logger.info(
                f"[POOL_DISCOVERY_PROGRAM] Found {len(candidates)} candidate pool accounts"
            )

            # Validate each candidate through hardened pipeline
            for idx, candidate in enumerate(candidates, 1):
                pubkey = candidate.get("pubkey")
                account = candidate.get("account", {})

                if not pubkey or not account:
                    continue

                logger.debug(
                    f"[POOL_DISCOVERY_PROGRAM] Validating candidate {idx}/{len(candidates)}: "
                    f"{pubkey[:16]}..."
                )

                # Validate candidate
                is_valid, reason = await self._validate_candidate_pool(
                    pool_address=pubkey,
                    account_data=account,
                    mint=mint
                )

                if is_valid:
                    logger.info(
                        f"[POOL_DISCOVERY_PROGRAM] ✅ Candidate {idx} validated as pool: "
                        f"{pubkey[:16]}..."
                    )
                    return pubkey
                else:
                    logger.debug(
                        f"[POOL_DISCOVERY_PROGRAM] ✗ Candidate {idx} rejected: {reason}"
                    )

            logger.warning(
                f"[POOL_DISCOVERY_PROGRAM] All {len(candidates)} candidates rejected"
            )
            return None

        except asyncio.TimeoutError:
            logger.warning(
                f"[POOL_DISCOVERY_PROGRAM] RPC timeout while querying "
                f"{program_id[:16]}... accounts"
            )
            return None

        except Exception as e:
            logger.warning(
                f"[POOL_DISCOVERY_PROGRAM] Error discovering pools: {e}"
            )
            return None

    async def discover_pool_multi_program(
        self,
        mint: str,
        programs: List[str] = None,
        timeout: int = 30
    ) -> Optional[str]:
        """
        Search multiple AMM programs in order.

        Tries PumpSwap first (faster), then Raydium if needed.

        Args:
            mint: Token mint address
            programs: List of program IDs to search (default: [PumpSwap, Raydium])
            timeout: RPC timeout per program

        Returns:
            Pool address if found, None otherwise
        """
        if programs is None:
            programs = [self.PUMPSWAP_PROGRAM, self.RAYDIUM_AMM_PROGRAM]

        logger.info(
            f"[POOL_DISCOVERY_PROGRAM] Starting multi-program search for {mint[:20]}..."
        )

        for program_id in programs:
            logger.info(f"[POOL_DISCOVERY_PROGRAM] Trying program {program_id[:16]}...")

            try:
                pool = await self.discover_pool_via_program_accounts(
                    mint=mint,
                    program_id=program_id,
                    timeout=timeout
                )

                if pool:
                    logger.info(
                        f"[POOL_DISCOVERY_PROGRAM] Found pool in {program_id[:16]}..."
                    )
                    return pool

            except Exception as e:
                logger.debug(
                    f"[POOL_DISCOVERY_PROGRAM] Error searching {program_id[:16]}...: {e}"
                )
                continue

        logger.warning(
            f"[POOL_DISCOVERY_PROGRAM] Pool not found in any program"
        )
        return None

    async def _validate_candidate_pool(
        self,
        pool_address: str,
        account_data: Dict,
        mint: str
    ) -> Tuple[bool, str]:
        """
        Validate a candidate pool account.

        Uses the same hardened validation as main extraction pipeline:
        1. Verify owner is AMM program
        2. Verify minimum data size
        3. Extract and validate vault addresses
        4. Fetch vault accounts and verify SPL token ownership
        5. Verify vault account sizes = 165 bytes
        6. Extract vault mint and match against launched token

        Args:
            pool_address: Pool account pubkey
            account_data: Account data from RPC ({"lamports", "owner", "data", ...})
            mint: Expected token mint for validation

        Returns:
            (is_valid, reason_if_invalid)
        """
        try:
            # Stage 1: Verify owner is AMM program
            owner = account_data.get("owner")
            from src.core.pool_detector import AMMPrograms

            if owner not in AMMPrograms.ALL:
                return False, f"owner={owner[:16] if owner else '???'}... not AMM program"

            # Stage 2: Verify data size
            data = account_data.get("data", [])
            if isinstance(data, list) and len(data) >= 2:
                # RPC returns [base64_string, "base64"]
                encoded_data = data[0] if isinstance(data[0], str) else data
                import base64
                try:
                    raw_data = base64.b64decode(encoded_data)
                except Exception:
                    return False, "could_not_decode_data"
            else:
                return False, "invalid_data_format"

            if len(raw_data) < 296:
                return False, f"size={len(raw_data)}<296"

            # Stage 3: Extract vault addresses
            try:
                base_vault_bytes = bytes(raw_data[232:264])
                quote_vault_bytes = bytes(raw_data[264:296])

                # Reject garbage patterns
                if base_vault_bytes == bytes(32):  # All zeros
                    return False, "base_vault_all_zeros"
                if quote_vault_bytes == bytes([0xFF] * 32):  # All ones
                    return False, "quote_vault_all_ones"

                base_vault = str(Pubkey(base_vault_bytes))
                quote_vault = str(Pubkey(quote_vault_bytes))

                # Reject system program
                if base_vault == "11111111111111111111111111111111":
                    return False, "base_vault_system_program"
                if quote_vault == "11111111111111111111111111111111":
                    return False, "quote_vault_system_program"

            except Exception as e:
                return False, f"could_not_extract_vaults: {str(e)}"

            # Stage 4 & 5 & 6: Verify vaults via RPC
            try:
                base_valid, base_mint = await self._verify_vault_account(
                    base_vault, mint
                )
                quote_valid, quote_mint = await self._verify_vault_account(
                    quote_vault, mint
                )

                if not base_valid and not quote_valid:
                    return False, "both_vaults_invalid"

                # At least one vault must match the token mint
                if base_valid and base_mint == mint:
                    logger.debug(
                        f"[POOL_DISCOVERY_PROGRAM] ✅ Base vault valid, matches token"
                    )
                    return True, ""

                if quote_valid and quote_mint == mint:
                    logger.debug(
                        f"[POOL_DISCOVERY_PROGRAM] ✅ Quote vault valid, matches token"
                    )
                    return True, ""

                return False, "no_vault_matches_token_mint"

            except Exception as e:
                return False, f"vault_verification_failed: {str(e)}"

        except Exception as e:
            return False, f"validation_error: {str(e)}"

    async def _verify_vault_account(
        self,
        vault_address: str,
        expected_mint: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify a vault account is valid SPL token account holding expected mint.

        Returns:
            (is_valid, vault_mint)
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [vault_address, {"encoding": "base64"}]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    result = await resp.json()

                    if "result" not in result or not result["result"]:
                        return False, None

                    account = result["result"].get("value")
                    if not account:
                        return False, None

                    # Verify owner is SPL token program
                    owner = account.get("owner")
                    SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPVwwQQftas5LLppuCQqn"
                    if owner != SPL_TOKEN_PROGRAM:
                        return False, None

                    # Verify size is 165 bytes (SPL token account)
                    data = account.get("data", [])
                    if isinstance(data, list) and len(data) >= 2:
                        encoded_data = data[0] if isinstance(data[0], str) else data
                        import base64
                        try:
                            raw_data = base64.b64decode(encoded_data)
                        except Exception:
                            return False, None
                    else:
                        return False, None

                    if len(raw_data) != 165:
                        return False, None

                    # Extract mint from SPL token account (offset 0-32)
                    try:
                        vault_mint = str(Pubkey(raw_data[0:32]))
                        return True, vault_mint
                    except Exception:
                        return False, None

        except Exception as e:
            logger.debug(
                f"[POOL_DISCOVERY_PROGRAM] Could not verify vault {vault_address[:16]}...: {e}"
            )
            return False, None

    async def _fetch_program_accounts(
        self,
        payload: Dict,
        timeout: int
    ) -> List[Dict]:
        """
        Fetch accounts owned by a program via RPC.

        Args:
            payload: RPC request payload
            timeout: Request timeout in seconds

        Returns:
            List of {pubkey, account} dicts
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    result = await resp.json()

                    if "result" not in result:
                        logger.debug(
                            f"[POOL_DISCOVERY_PROGRAM] RPC error: {result.get('error', 'unknown')}"
                        )
                        return []

                    return result.get("result", [])

        except asyncio.TimeoutError:
            raise
        except Exception as e:
            logger.warning(
                f"[POOL_DISCOVERY_PROGRAM] RPC request failed: {e}"
            )
            return []
