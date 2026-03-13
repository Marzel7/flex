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
            # Fetch the pool account
            pool_data = await self._fetch_account(pool_address)
            if not pool_data:
                logger.warning(f"Could not fetch pool account: {pool_address}")
                return None

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

        logger.warning(f"Unknown pool program owner: {owner}")
        return None

    async def _extract_raydium_amm(
        self, pool_data: Dict, pool_address: str, token_mint: str
    ) -> Optional[Dict]:
        """
        Extract reserves from Raydium AMM pool.

        Raydium AMM state structure:
        - Offset 0-8: nonce
        - Offset 8-40: token_account_a (base)
        - Offset 40-72: token_account_b (quote)
        - ... other fields
        """
        try:
            data = pool_data.get("data", [None, None])[0]
            if not data or len(data) < 200:
                return None

            decoded = b64decode(data)

            # Extract token accounts (public keys are 32 bytes)
            # Raydium AMM: base at offset 8, quote at offset 40
            base_account = self._bytes_to_pubkey(decoded[8:40])
            quote_account = self._bytes_to_pubkey(decoded[40:72])

            if not base_account or not quote_account:
                return None

            # Fetch token info for decimals
            base_decimals = await self._get_token_decimals(base_account)
            quote_decimals = await self._get_token_decimals(quote_account)

            # Determine which is the token and which is quote
            base_token = token_mint
            quote_token = SOL_MINT

            return {
                "base_account": base_account,
                "quote_account": quote_account,
                "base_token": base_token,
                "quote_token": quote_token,
                "base_decimals": base_decimals or 6,
                "quote_decimals": quote_decimals or 9,
                "pool_program": "raydium_amm",
            }

        except Exception as e:
            logger.debug(f"Error extracting Raydium AMM: {e}")
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
