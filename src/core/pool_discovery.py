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
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

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
                # Add pool_address to the returned dict
                reserves['pool_address'] = pool_address
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
        5. Verify vault accounts exist and are SPL token accounts (skip if not yet created)
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

            # If vaults don't exist yet (common for new launches), register with vault addresses anyway
            # The pool state is valid even if vaults aren't fully initialized
            if not base_info or not quote_info:
                logger.warning(
                    f"[POOL_EXTRACT] ⚠️  Vaults not yet created for {pool_address[:16]}...: "
                    f"base={base_vault[:16]}... quote={quote_vault[:16]}..."
                )
                
                # For recently launched tokens, use the vault addresses as-is
                # They will be populated later when the pool is fully initialized
                logger.info(
                    f"[POOL_EXTRACT] ✅ Using uninitialized vaults (will be ready soon): "
                    f"base={base_vault[:16]}... quote={quote_vault[:16]}..."
                )
                
                # Return with default decimals - will be corrected when vaults are created
                return {
                    "base_account": base_vault,
                    "quote_account": quote_vault,
                    "base_token": token_mint,
                    "quote_token": "So11111111111111111111111111111111111111112",  # SOL
                    "base_decimals": 6,
                    "quote_decimals": 9,
                    "pool_program": "pumpswap" if owner == PUMPSWAP_PROGRAM else "raydium_amm",
                }

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

        PumpFun V1 pools don't store vault addresses in pool state like Raydium does.
        Instead, vaults are created as separate accounts through PumpSwap.

        Workaround: Accept vault pair address as optional parameter or store it separately.
        For now, we mark pools as needing manual vault configuration.
        """
        try:
            logger.info(
                f"[POOL_EXTRACT] PumpFun V1 pools require special vault handling for {pool_address[:16]}..."
            )

            # Try Raydium-like layout first as fallback
            result = await self._extract_raydium_amm(pool_data, pool_address, token_mint)

            if result:
                result["pool_program"] = "pumpfun_v1"
                logger.info(f"[POOL_EXTRACT] PumpFun V1 extracted via Raydium-like structure")
                return result

            logger.warning(
                f"[POOL_EXTRACT] PumpFun V1 pool {pool_address[:16]}... requires vault pair address"
            )

            # Return partial data - vault information will need to be provided separately
            # This allows us to at least register the pool even without full vault data
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
        self, token_mint: str, reserves: Dict, discovery_method: str = "unknown"
    ) -> bool:
        """Register extracted pool in token_pool_accounts table."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Determine vault validation status
            # If vaults don't exist on-chain, mark as pending for later resolution
            vault_status = "pending"
            vault_error = None
            
            base_account = reserves.get("base_account")
            quote_account = reserves.get("quote_account")
            pool_program = reserves.get("pool_program", "raydium_amm")
            
            # Check if vaults actually exist and are valid
            try:
                base_info = await self._fetch_account(base_account)
                quote_info = await self._fetch_account(quote_account)
                
                if base_info and quote_info:
                    # Both vaults exist, validate based on pool program type
                    base_owner = base_info.get("owner")
                    quote_owner = quote_info.get("owner")
                    SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
                    PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
                    
                    # For Raydium/Orca: vaults must be SPL token accounts
                    if pool_program in ("raydium_amm", "raydium_cpmm", "orca"):
                        if base_owner == SPL_TOKEN_PROGRAM and quote_owner == SPL_TOKEN_PROGRAM:
                            vault_status = "validated"
                        else:
                            vault_status = "pending"
                            vault_error = "vaults exist but not SPL token accounts"
                    
                    # For PumpFun V1/PumpSwap: vaults ARE the PumpSwap pool accounts
                    elif pool_program in ("pumpfun_v1", "pumpswap"):
                        if base_owner == PUMPSWAP_PROGRAM and quote_owner == PUMPSWAP_PROGRAM:
                            vault_status = "validated"
                        else:
                            vault_status = "pending"
                            vault_error = "vaults exist but not PumpSwap pool accounts"
                    else:
                        vault_status = "pending"
                else:
                    vault_status = "pending"
                    vault_error = "vaults not yet created on-chain"
            except Exception as e:
                vault_status = "pending"
                vault_error = str(e)

            # Compute pool_score
            WSOL = "So11111111111111111111111111111111111111112"
            USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
            quote_token = reserves.get("quote_token", "")
            quote_pref = 1.0 if quote_token == WSOL else 0.5 if quote_token == USDC else 0.1
            validation_bonus = 0.3 if vault_status == "validated" else 0.0
            pool_score = quote_pref + validation_bonus

            cursor.execute(
                """
                INSERT OR REPLACE INTO token_pool_accounts
                (mint, base_account, quote_account, base_token, quote_token,
                 base_decimals, quote_decimals, pool_program, pool_address, is_active,
                 vault_validation_status, vault_validation_error, vault_validation_attempts,
                 last_vault_validation_at, discovery_method, pool_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    reserves.get("pool_address"),
                    1,  # is_active
                    vault_status,
                    vault_error,
                    1,  # vault_validation_attempts
                    int(__import__("time").time()),  # last_vault_validation_at
                    discovery_method,
                    pool_score,
                    int(__import__("time").time()),  # created_at
                    int(__import__("time").time()),  # updated_at
                ),
            )

            conn.commit()
            conn.close()

            status_str = "✅" if vault_status == "validated" else "⏳"
            logger.info(
                f"{status_str} Registered pool for {token_mint} → "
                f"{reserves['base_account'][:16]}... / {reserves['quote_account'][:16]}... "
                f"(vaults: {vault_status})"
            )
            if vault_error:
                logger.debug(f"   Vault error: {vault_error}")
            return True

        except Exception as e:
            logger.error(f"Error registering pool to database: {e}")
            return False

    async def retry_vault_validation(self, token_mint: str, pool_account: str) -> bool:
        """
        Retry vault validation for a pool marked as 'pending'.
        
        Called periodically to check if vaults have been created on-chain
        since initial registration. Updates vault_validation_status if vaults
        become available.
        
        Returns True if vaults are now validated, False otherwise.
        """
        try:
            import sqlite3
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Get the pool registration
            cursor.execute(
                """SELECT base_account, quote_account, pool_program, vault_validation_attempts
                   FROM token_pool_accounts 
                   WHERE mint = ? AND base_account = ?""",
                (token_mint, pool_account)
            )
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                logger.debug(f"[VAULT_RETRY] No pool found for {token_mint[:16]}... {pool_account[:16]}...")
                return False
            
            base_account, quote_account, pool_program, attempts = row
            
            # Try to validate vaults
            logger.info(
                f"[VAULT_RETRY] Attempt {attempts + 1}: Validating vaults for {token_mint[:16]}... "
                f"(pool: {pool_program})"
            )
            
            base_info = await self._fetch_account(base_account)
            quote_info = await self._fetch_account(quote_account)
            
            if not base_info or not quote_info:
                logger.debug(
                    f"[VAULT_RETRY] Vaults still not created for {token_mint[:16]}..."
                )
                # Update attempt count
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE token_pool_accounts 
                       SET vault_validation_attempts = vault_validation_attempts + 1,
                           last_vault_validation_at = ?
                       WHERE mint = ? AND base_account = ?""",
                    (int(__import__("time").time()), token_mint, pool_account)
                )
                conn.commit()
                conn.close()
                return False
            
            # Vaults exist, validate them
            base_owner = base_info.get("owner")
            quote_owner = quote_info.get("owner")
            SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
            
            if base_owner == SPL_TOKEN_PROGRAM and quote_owner == SPL_TOKEN_PROGRAM:
                # Vaults are valid!
                logger.info(
                    f"[VAULT_RETRY] ✅ Vaults validated for {token_mint[:16]}... after {attempts + 1} attempts"
                )
                
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE token_pool_accounts 
                       SET vault_validation_status = 'validated',
                           vault_validation_attempts = ?,
                           last_vault_validation_at = ?,
                           updated_at = ?
                       WHERE mint = ? AND base_account = ?""",
                    (attempts + 1, int(__import__("time").time()), 
                     int(__import__("time").time()), token_mint, pool_account)
                )
                conn.commit()
                conn.close()
                return True
            else:
                # Vaults exist but are wrong type
                logger.warning(
                    f"[VAULT_RETRY] ❌ Vaults exist but are not SPL token accounts for {token_mint[:16]}..."
                )
                
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute(
                    """UPDATE token_pool_accounts 
                       SET vault_validation_status = 'rejected',
                           vault_validation_error = 'vaults not SPL token accounts',
                           vault_validation_attempts = ?,
                           last_vault_validation_at = ?,
                           is_active = 0
                       WHERE mint = ? AND base_account = ?""",
                    (attempts + 1, int(__import__("time").time()), token_mint, pool_account)
                )
                conn.commit()
                conn.close()
                return False
        
        except Exception as e:
            logger.error(f"[VAULT_RETRY] Error: {e}")
            return False

    async def register_pumpfun_v1_pool(
        self, token_mint: str, pool_address: str, vault_pair: str
    ) -> bool:
        """
        Register a PumpFun V1 pool with a known vault pair address.

        Since PumpFun V1 pools don't have vaults in pool state, we accept
        the vault pair address directly and derive token vaults from it.

        Args:
            token_mint: The token being traded
            pool_address: The PumpFun V1 pool state account
            vault_pair: The vault pair account (owned by PumpSwap)

        Returns:
            True if registration successful, False otherwise
        """
        try:
            logger.info(
                f"[POOL_REGISTER] Registering PumpFun V1 pool: {pool_address[:16]}... "
                f"with vault pair {vault_pair[:16]}..."
            )

            # Fetch vault pair to understand its structure
            vault_data = await self._fetch_account(vault_pair)
            if not vault_data:
                logger.error(f"Could not fetch vault pair: {vault_pair}")
                return False

            # For now, use vault pair itself as both base and quote accounts
            # (This is a placeholder - real implementation should extract actual token vaults)
            reserves = {
                "base_account": vault_pair,
                "quote_account": vault_pair,
                "base_token": token_mint,
                "quote_token": SOL_MINT,
                "base_decimals": 6,
                "quote_decimals": 9,
                "pool_program": "pumpfun_v1",
            }

            return await self.register_pool_to_db(token_mint, reserves)

        except Exception as e:
            logger.error(f"Error registering PumpFun V1 pool: {e}")
            return False

    async def discover_and_register_pool(
        self, pool_address: str, token_mint: str, migration_sig: str = None
    ) -> bool:
        """
        Discover pool reserves and register in database with proper vault validation.

        Called when a token launches to automatically enable WebSocket pricing.
        
        Args:
            pool_address: Candidate pool address (optional if migration_sig provided)
            token_mint: The token mint address
            migration_sig: Migration transaction signature (optional, enables full discovery)
        
        Strategy:
        1. If migration_sig provided, use vault pair discovery which searches transaction history
           This finds the actual PumpSwap pool account
        2. Otherwise, try vault pair discovery with provided pool_address
        3. Fall back to standard extraction from pool_address
        4. Register with vault_validation_status = pending/validated
        """
        logger.info(f"🔍 Discovering pool reserves for {token_mint}")

        reserves = None
        vault_source = None

        # Strategy 1: Try PumpFun V1 vault pair discovery
        # This finds the actual PumpSwap pool account which IS the vault pair
        logger.info(
            f"[DISCOVERY_CHAIN] Step 1: Attempting PumpFun V1 vault pair discovery"
        )
        try:
            from src.core.post_migration_pool_discovery import PostMigrationPoolDiscovery
            vault_discovery = PostMigrationPoolDiscovery(self.rpc_url)
            vault_pair = await vault_discovery.discover_pumpfun_v1_vault_pair(
                mint=token_mint,
                pool_address=pool_address
            )

            if vault_pair:
                logger.info(
                    f"[DISCOVERY_CHAIN] ✅ Found vault pair: {vault_pair[:16]}... "
                    f"(attempting to extract actual vaults)"
                )

                # Extract actual base and quote vaults from the pool account
                extracted = await self.extract_pool_reserves(vault_pair, token_mint)

                if extracted and extracted.get("base_account") != extracted.get("quote_account"):
                    # Valid extraction with distinct vaults
                    reserves = extracted
                    vault_source = "pumpfun_v1_vault_extraction"
                    logger.info(
                        f"[DISCOVERY_CHAIN] ✅ Extracted vaults from vault pair: "
                        f"base={reserves['base_account'][:16]}... "
                        f"quote={reserves['quote_account'][:16]}..."
                    )
                else:
                    # Extraction failed or returned invalid (same address for both)
                    logger.info(
                        f"[DISCOVERY_CHAIN] ⏭️  Vault pair extraction invalid or failed, "
                        f"falling back to standard extraction"
                    )
            else:
                logger.info(
                    f"[DISCOVERY_CHAIN] ⏭️  No vault pair found, falling back to standard extraction"
                )

        except Exception as e:
            logger.debug(f"[DISCOVERY_CHAIN] Vault pair discovery failed: {e}")

        # Strategy 2: Try standard extraction from pool_address
        if not reserves:
            logger.info(
                f"[DISCOVERY_CHAIN] Step 2: Attempting standard pool extraction"
            )
            reserves = await self.extract_pool_reserves(pool_address, token_mint)
            
            if reserves:
                logger.info(
                    f"[DISCOVERY_CHAIN] ✅ Successfully extracted reserves from pool"
                )
                vault_source = "standard_extraction"

        # If no reserves found, stop here
        if not reserves:
            logger.warning(f"Failed to extract reserves from pool {pool_address}")
            return False

        # CRITICAL: Validate that base and quote vaults are different
        # If they're the same and this isn't a known PumpFun V1 vault pair, reject
        if reserves.get("base_account") == reserves.get("quote_account"):
            if vault_source != "pumpfun_v1_discovered":
                logger.warning(
                    f"[VALIDATION] ❌ Rejecting pool: base_account == quote_account (invalid pool state)"
                )
                return False

        logger.info(
            f"[DISCOVERY_CHAIN] ✅ Pool extraction successful from {vault_source}"
        )

        # Map vault_source to discovery_method for database
        discovery_method = vault_source or "unknown"

        # Register in database with vault validation status
        # Vaults will be marked as 'pending' or 'validated' based on whether they exist on-chain
        success = await self.register_pool_to_db(token_mint, reserves, discovery_method)

        if success:
            logger.info(
                f"🚀 Pool registered (WebSocket will subscribe when vaults are validated)"
            )

        return success

    async def register_pool_with_vaults(
        self, pool_address: str, token_mint: str, 
        base_account: str, quote_account: str,
        base_token: str, quote_token: str,
        base_decimals: int = 6, quote_decimals: int = 9,
        pool_program: str = "pumpswap"
    ) -> bool:
        """
        Register a pool when vault addresses are already known.
        
        Used for PumpFun V1 pools where vault discovery found the correct
        vault pair address but standard extraction won't work.
        
        Args:
            pool_address: The pool account address (for reference)
            token_mint: The token mint
            base_account: The base vault account (or PumpSwap pool itself)
            quote_account: The quote vault account (or same as base for PumpFun V1)
            base_token: The token mint for base
            quote_token: The token mint for quote (usually SOL)
            base_decimals: Decimals for base token
            quote_decimals: Decimals for quote token (9 for SOL)
            pool_program: Pool program name
        
        Returns True if registered successfully.
        """
        logger.info(
            f"[REGISTER_WITH_VAULTS] Registering {token_mint[:16]}... "
            f"with known vaults"
        )
        
        reserves = {
            "base_account": base_account,
            "quote_account": quote_account,
            "base_token": base_token,
            "quote_token": quote_token,
            "base_decimals": base_decimals,
            "quote_decimals": quote_decimals,
            "pool_program": pool_program,
        }
        
        # Register with explicit vault validation
        success = await self.register_pool_to_db(token_mint, reserves)
        
        if success:
            logger.info(
                f"✅ Pool registered with known vaults (vaults should be validated)"
            )
        
        return success
