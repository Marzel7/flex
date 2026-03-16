"""
Automatic pool discovery and reserve account extraction.

When a token launches to Raydium/Orca, automatically:
1. Fetch the pool account on-chain
2. Extract base and quote reserve accounts
3. Register in token_pool_accounts
4. WebSocket subscribes automatically on next worker cycle

This enables real-time pricing from the moment a token launches.
"""

import logging
import sqlite3
import asyncio
import aiohttp
import json
from typing import Optional, Dict, Tuple
from base64 import b64decode

logger = logging.getLogger(__name__)

# Raydium program IDs
RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K"
RAYDIUM_CPMM_PROGRAM = "CPMMoo8L3F4rn9aUYn2QRiPK5VrKMjstm69edQaMQAC"

# Orca program ID
ORCA_WHIRLPOOL_PROGRAM = "whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco"

# PumpSwap program ID (uses Raydium AMM layout)
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

# PumpFun V1 program ID (uses Raydium AMM layout)
PUMPFUN_V1_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# SPL Token program
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPVwwQQftas5LLppuCQqn"

# SOL mint
SOL_MINT = "So11111111111111111111111111111111111111112"


class PoolDiscovery:
    """Extract pool reserve accounts from on-chain pool data."""

    def __init__(self, db_path: str, rpc_url: str):
        self.db_path = db_path
        self.rpc_url = rpc_url

    async def extract_pool_reserves(
        self, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """
        Extract base_account and quote_account from a pool address.

        Args:
            pool_address: The pool account address (Raydium or Orca)
            token_mint: The token mint address

        Returns:
            Dict with:
            {
                'base_account': str,
                'quote_account': str,
                'base_token': str,
                'quote_token': str,
                'base_decimals': int,
                'quote_decimals': int,
                'pool_program': str ('raydium_amm', 'raydium_cpmm', 'orca', etc)
            }
            Or None if extraction fails.
        """
        try:
            logger.info(f"🔍 Extracting reserves from pool: {pool_address}")

            # Fetch the pool account
            pool_data = await self._fetch_account(pool_address)
            if not pool_data:
                logger.warning(f"Could not fetch pool account: {pool_address}")
                return None

            # Log what we fetched
            owner = pool_data.get("owner")
            data_len = 0
            if pool_data.get("data"):
                data_field = pool_data.get("data")
                if isinstance(data_field, list) and len(data_field) > 0:
                    data_b64 = data_field[0]
                    decoded = b64decode(data_b64)
                    data_len = len(decoded)
                elif isinstance(data_field, str):
                    decoded = b64decode(data_field)
                    data_len = len(decoded)

            logger.info(f"  Owner: {owner}, Data: {data_len} bytes")

            # Try to extract based on pool program
            reserves = await self._extract_from_pool_data(
                pool_data, pool_address, token_mint
            )

            if reserves:
                logger.info(
                    f"✅ Extracted pool reserves from {pool_address[:16]}...: "
                    f"base={reserves['base_account'][:16]}... "
                    f"quote={reserves['quote_account'][:16]}..."
                )
                return reserves

            logger.warning(f"Could not extract reserves from pool: {pool_address}")
            return None

        except Exception as e:
            logger.error(f"Error extracting pool reserves: {e}")
            return None

    async def _fetch_account(self, address: str) -> Optional[Dict]:
        """Fetch account data from RPC."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "base64", "commitment": "finalized"}],
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    result = await resp.json()
                    if "result" in result and result["result"]:
                        return result["result"]["value"]
                    return None

        except Exception as e:
            logger.debug(f"Error fetching account {address}: {e}")
            return None

    async def _extract_from_pool_data(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """Extract reserves from pool data based on account owner."""
        owner = pool_data.get("owner")

        # Raydium AMM
        if owner == RAYDIUM_AMM_PROGRAM:
            return await self._extract_raydium_amm(pool_data, pool_address, token_mint)

        # Raydium CPMM
        if owner == RAYDIUM_CPMM_PROGRAM:
            return await self._extract_raydium_cpmm(
                pool_data, pool_address, token_mint
            )

        # Orca Whirlpool
        if owner == ORCA_WHIRLPOOL_PROGRAM:
            return await self._extract_orca_whirlpool(
                pool_data, pool_address, token_mint
            )

        # PumpSwap (uses Raydium AMM layout)
        if owner == PUMPSWAP_PROGRAM:
            return await self._extract_raydium_amm(pool_data, pool_address, token_mint)

        # PumpFun V1 (different structure, may use Raydium-like layout at different offsets)
        if owner == PUMPFUN_V1_PROGRAM:
            return await self._extract_pumpfun_v1(pool_data, pool_address, token_mint)

        logger.warning(f"Unknown pool program owner: {owner}")
        return None

    async def _extract_raydium_amm(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """
        Extract vault accounts from Raydium AMM pool with STRICT validation.

        10-stage validation pipeline:
        1. Verify candidate owner is PumpSwap/Raydium program
        2. Fetch raw account bytes
        3. Verify minimum pool state size (296 bytes)
        4. Extract vault pubkeys from offsets 232-296
        5. Verify vault accounts exist and are SPL token accounts
        6. Verify token account size = 165 bytes (hardened check)
        7. Extract token mints from vault accounts
        8. Verify one mint matches the launched token
        9. Determine base/quote pairing
        10. Register pool

        Validates that the candidate account is actually a pool state account
        before attempting to decode vaults at fixed offsets.
        """
        try:
            # ===== DIAGNOSTIC: Log pool candidate for detection verification =====
            logger.info(
                f"[POOL_DETECT] mint={token_mint[:20]}... candidate_pool={pool_address}"
            )

            # ===== STAGE 1: Owner validation =====
            owner = pool_data.get("owner")
            if owner not in {PUMPSWAP_PROGRAM, PUMPFUN_V1_PROGRAM, RAYDIUM_AMM_PROGRAM, RAYDIUM_CPMM_PROGRAM}:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ Invalid owner for candidate {pool_address[:16]}...: {owner}"
                )
                return None

            # ===== STAGE 2: Get decoded data =====
            data_field = pool_data.get("data")
            if not data_field:
                logger.warning(f"[POOL_EXTRACT] ❌ No data in pool account {pool_address[:16]}...")
                return None

            # RPC returns data as [base64_string, "base64"]
            if isinstance(data_field, list) and len(data_field) > 0:
                data = data_field[0]
            else:
                data = data_field

            if isinstance(data, str):
                decoded = b64decode(data)
            else:
                decoded = data

            # ===== STAGE 3: Size validation =====
            if len(decoded) < 296:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ Candidate {pool_address[:16]}... too small for Raydium layout: {len(decoded)} bytes"
                )
                return None

            # ===== STAGE 4: Extract vault addresses =====
            base_vault = self._bytes_to_pubkey(decoded[232:264])
            quote_vault = self._bytes_to_pubkey(decoded[264:296])

            if not base_vault or not quote_vault:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ Could not decode vault pubkeys from {pool_address[:16]}..."
                )
                return None

            logger.info(
                f"[POOL_EXTRACT] 📍 Candidate {pool_address[:16]}... extracted: "
                f"base={base_vault[:16]}... quote={quote_vault[:16]}..."
            )

            # ===== STAGE 5: Validate vaults are real SPL token accounts =====
            base_info = await self._fetch_account(base_vault)
            quote_info = await self._fetch_account(quote_vault)

            if not base_info or not quote_info:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ Could not fetch extracted vault accounts: "
                    f"base={base_vault[:16]}... quote={quote_vault[:16]}..."
                )
                return None

            # Verify both are owned by token program
            base_owner = base_info.get("owner")
            quote_owner = quote_info.get("owner")

            if base_owner != SPL_TOKEN_PROGRAM or quote_owner != SPL_TOKEN_PROGRAM:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ Rejected {pool_address[:16]}... - extracted vaults are NOT token accounts: "
                    f"base_owner={base_owner} quote_owner={quote_owner}"
                )
                return None

            # ===== STAGE 6: HARDENED - Validate SPL token account size =====
            # SPL token accounts have fixed layout of exactly 165 bytes
            base_vault_data = base_info.get("data")
            quote_vault_data = quote_info.get("data")

            if isinstance(base_vault_data, list) and len(base_vault_data) > 0:
                base_vault_data = b64decode(base_vault_data[0])
            elif isinstance(base_vault_data, str):
                base_vault_data = b64decode(base_vault_data)

            if isinstance(quote_vault_data, list) and len(quote_vault_data) > 0:
                quote_vault_data = b64decode(quote_vault_data[0])
            elif isinstance(quote_vault_data, str):
                quote_vault_data = b64decode(quote_vault_data)

            SPL_TOKEN_ACCOUNT_SIZE = 165

            if not isinstance(base_vault_data, bytes) or len(base_vault_data) != SPL_TOKEN_ACCOUNT_SIZE:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ Rejected {pool_address[:16]}... - base vault invalid size: "
                    f"got {len(base_vault_data) if isinstance(base_vault_data, bytes) else 'unknown'} bytes, "
                    f"expected {SPL_TOKEN_ACCOUNT_SIZE}"
                )
                return None

            if not isinstance(quote_vault_data, bytes) or len(quote_vault_data) != SPL_TOKEN_ACCOUNT_SIZE:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ Rejected {pool_address[:16]}... - quote vault invalid size: "
                    f"got {len(quote_vault_data) if isinstance(quote_vault_data, bytes) else 'unknown'} bytes, "
                    f"expected {SPL_TOKEN_ACCOUNT_SIZE}"
                )
                return None

            logger.info(
                f"[POOL_EXTRACT] ✅ Vaults validated as SPL token accounts (size={SPL_TOKEN_ACCOUNT_SIZE} bytes)"
            )

            # ===== STAGE 7: Extract vault token mints =====
            base_decimals = await self._get_token_decimals(base_vault)
            quote_decimals = await self._get_token_decimals(quote_vault)

            # SPL token account: mint is at offset 0-32
            base_mint = None
            quote_mint = None

            try:
                base_mint = str(self._bytes_to_pubkey(base_vault_data[0:32]))
            except Exception as e:
                logger.debug(f"[POOL_EXTRACT] Could not extract base mint: {e}")

            try:
                quote_mint = str(self._bytes_to_pubkey(quote_vault_data[0:32]))
            except Exception as e:
                logger.debug(f"[POOL_EXTRACT] Could not extract quote mint: {e}")

            # ===== STAGE 8: Verify one vault mint matches token_mint =====
            if base_mint != token_mint and quote_mint != token_mint:
                logger.warning(
                    f"[POOL_EXTRACT] ❌ Neither vault mint matches token_mint for {pool_address[:16]}...: "
                    f"token={token_mint} base_mint={base_mint} quote_mint={quote_mint}"
                )
                return None

            # ===== STAGE 9: Determine base/quote pairing =====
            if base_mint == token_mint:
                final_base_token = base_mint
                final_quote_token = quote_mint
                final_base_account = base_vault
                final_quote_account = quote_vault
                final_base_decimals = base_decimals or 6
                final_quote_decimals = quote_decimals or 9
            else:
                # Swap: quote is the token, base is SOL
                final_base_token = quote_mint
                final_quote_token = base_mint
                final_base_account = quote_vault
                final_quote_account = base_vault
                final_base_decimals = quote_decimals or 6
                final_quote_decimals = base_decimals or 9

            # ===== STAGE 10: Register pool =====
            logger.info(
                f"[POOL_EXTRACT] ✅ VALIDATED pool {pool_address[:16]}... "
                f"base_token={final_base_token[:20]}... "
                f"quote_token={final_quote_token[:20]}..."
            )

            # Determine program name for logging
            program_name = "raydium_amm"
            if owner == PUMPSWAP_PROGRAM:
                program_name = "pumpswap"
            elif owner == PUMPFUN_V1_PROGRAM:
                program_name = "pumpfun_v1"

            return {
                "base_account": final_base_account,
                "quote_account": final_quote_account,
                "base_token": final_base_token,
                "quote_token": final_quote_token,
                "base_decimals": final_base_decimals,
                "quote_decimals": final_quote_decimals,
                "pool_program": program_name,
            }

        except Exception as e:
            logger.debug(f"[POOL_EXTRACT] Error extracting Raydium AMM: {e}")
            return None

    async def _extract_raydium_cpmm(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """Extract reserves from Raydium CPMM pool."""
        # Similar structure to AMM but with different offsets
        try:
            data = pool_data.get("data", [None, None])[0]
            if not data or len(data) < 200:
                return None

            decoded = b64decode(data)

            # CPMM has similar structure
            base_account = self._bytes_to_pubkey(decoded[8:40])
            quote_account = self._bytes_to_pubkey(decoded[40:72])

            if not base_account or not quote_account:
                return None

            base_decimals = await self._get_token_decimals(base_account)
            quote_decimals = await self._get_token_decimals(quote_account)

            return {
                "base_account": base_account,
                "quote_account": quote_account,
                "base_token": token_mint,
                "quote_token": SOL_MINT,
                "base_decimals": base_decimals or 6,
                "quote_decimals": quote_decimals or 9,
                "pool_program": "raydium_cpmm",
            }

        except Exception as e:
            logger.debug(f"Error extracting Raydium CPMM: {e}")
            return None

    async def _extract_orca_whirlpool(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """Extract reserves from Orca Whirlpool pool."""
        try:
            data = pool_data.get("data", [None, None])[0]
            if not data or len(data) < 200:
                return None

            decoded = b64decode(data)

            # Orca structure differs; token accounts at different offsets
            # This is approximate - Orca has complex state structure
            base_account = self._bytes_to_pubkey(decoded[72:104])  # Example offset
            quote_account = self._bytes_to_pubkey(decoded[104:136])

            if not base_account or not quote_account:
                return None

            base_decimals = await self._get_token_decimals(base_account)
            quote_decimals = await self._get_token_decimals(quote_account)

            return {
                "base_account": base_account,
                "quote_account": quote_account,
                "base_token": token_mint,
                "quote_token": SOL_MINT,
                "base_decimals": base_decimals or 6,
                "quote_decimals": quote_decimals or 9,
                "pool_program": "orca_whirlpool",
            }

        except Exception as e:
            logger.debug(f"Error extracting Orca Whirlpool: {e}")
            return None

    async def _extract_pumpfun_v1(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """
        Extract reserves from PumpFun V1 pool.

        PumpFun V1 pools have a different structure than Raydium AMM.
        The vault addresses don't appear to be at the standard Raydium offsets.

        For now, we attempt Raydium extraction but fall back gracefully
        if it fails. This allows us to detect and register pools even if
        we don't fully understand the data structure.

        TODO: Reverse-engineer the actual PumpFun V1 pool structure and
        implement proper extraction.
        """
        try:
            logger.info(
                f"[POOL_EXTRACT] Attempting PumpFun V1 extraction for {pool_address[:16]}..."
            )

            # Try Raydium-like layout first
            result = await self._extract_raydium_amm(pool_data, pool_address, token_mint)

            if result:
                # Update program name to reflect it's PumpFun V1
                result["pool_program"] = "pumpfun_v1"
                return result

            # If Raydium extraction failed, try alternative offsets
            # (This is speculative - we may need to adjust based on actual structure)
            data = pool_data.get("data", [None, None])[0]
            if not data or len(data) < 200:
                logger.warning(
                    f"[POOL_EXTRACT] PumpFun V1 pool too small: {len(data) if data else 0} bytes"
                )
                return None

            decoded = b64decode(data)

            # Try different offset possibilities
            # Standard Raydium is 232-264, 264-296, but PumpFun might use different layout
            possible_offsets = [
                (232, 264, 296),  # Standard Raydium
                (8, 40, 72),      # Early in structure
                (64, 96, 128),    # Shifted offsets
            ]

            for base_start, base_end, quote_end in possible_offsets:
                if len(decoded) < quote_end:
                    continue

                base_vault = self._bytes_to_pubkey(decoded[base_start:base_end])
                quote_vault = self._bytes_to_pubkey(decoded[base_end:quote_end])

                if base_vault and quote_vault:
                    # Verify these are actual accounts
                    base_info = await self._fetch_account(base_vault)
                    quote_info = await self._fetch_account(quote_vault)

                    if base_info and quote_info:
                        logger.info(
                            f"[POOL_EXTRACT] Found valid vaults at offset {base_start}-{base_end}-{quote_end}"
                        )

                        base_decimals = await self._get_token_decimals(base_vault)
                        quote_decimals = await self._get_token_decimals(quote_vault)

                        return {
                            "base_account": base_vault,
                            "quote_account": quote_vault,
                            "base_token": token_mint,
                            "quote_token": SOL_MINT,
                            "base_decimals": base_decimals or 6,
                            "quote_decimals": quote_decimals or 9,
                            "pool_program": "pumpfun_v1",
                        }

            logger.warning(
                f"[POOL_EXTRACT] Could not extract PumpFun V1 pool structure"
            )
            return None

        except Exception as e:
            logger.debug(f"[POOL_EXTRACT] Error extracting PumpFun V1: {e}")
            return None

    async def _get_token_decimals(self, token_mint: str) -> Optional[int]:
        """Fetch token decimals from on-chain token metadata."""
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenSupply",
                "params": [token_mint],
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    result = await resp.json()
                    if "result" in result:
                        decimals = result["result"].get("decimals")
                        return decimals

            return None

        except Exception as e:
            logger.debug(f"Error fetching token decimals: {e}")
            return None

    @staticmethod
    def _bytes_to_pubkey(data: bytes) -> Optional[str]:
        """Convert 32-byte public key to base58 address."""
        try:
            from solders.pubkey import Pubkey

            if len(data) != 32:
                return None

            # Reject placeholder/null pubkeys (all zeros or all ones)
            if data == b'\x00' * 32 or data == b'\xff' * 32:
                return None

            return str(Pubkey(data))
        except Exception:
            return None

    async def register_pool_to_db(
        self, token_mint: str, reserves: Dict
    ) -> bool:
        """Register extracted pool in token_pool_accounts table."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT OR REPLACE INTO token_pool_accounts
                (mint, base_account, quote_account, base_token, quote_token,
                 base_decimals, quote_decimals, pool_program, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    token_mint,
                    reserves["base_account"],
                    reserves["quote_account"],
                    reserves["base_token"],
                    reserves["quote_token"],
                    reserves["base_decimals"],
                    reserves["quote_decimals"],
                    reserves["pool_program"],
                    1,  # is_active
                    int(__import__("time").time()),
                    int(__import__("time").time()),
                ),
            )

            conn.commit()
            conn.close()

            logger.info(
                f"✅ Registered pool for {token_mint} → "
                f"{reserves['base_account'][:16]}... / {reserves['quote_account'][:16]}..."
            )
            return True

        except Exception as e:
            logger.error(f"Error registering pool to database: {e}")
            return False

    async def discover_and_register_pool(
        self, pool_address: str, token_mint: str
    ) -> bool:
        """
        Discover pool reserves and register in database.

        Called when a token launches to automatically enable WebSocket pricing.
        """
        logger.info(f"🔍 Discovering pool reserves for {token_mint}")

        # Extract reserves from on-chain pool data
        reserves = await self.extract_pool_reserves(pool_address, token_mint)

        if not reserves:
            logger.warning(f"Failed to extract reserves from pool {pool_address}")
            return False

        # Register in database (enables WebSocket subscription on next worker cycle)
        success = await self.register_pool_to_db(token_mint, reserves)

        if success:
            logger.info(
                f"🚀 Pool auto-registered! WebSocket will subscribe on next worker cycle"
            )

        return success
