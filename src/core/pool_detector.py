"""
Universal Pool Discovery via Program Ownership Detection

Replaces fragile position-based pool extraction with reliable program-ownership
detection. Identifies AMM pool PDAs for PumpSwap, Raydium, Orca, Meteora.

Works by scanning transaction account keys for accounts owned by known AMM programs.
"""

import asyncio
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
import struct

logger = logging.getLogger(__name__)


@dataclass
class PoolInfo:
    """Discovered pool information ready for registration."""
    mint: str
    pool_address: str
    base_account: str
    quote_account: str
    base_token: str
    quote_token: str
    base_decimals: int
    quote_decimals: int
    pool_program: str


class AMMPrograms:
    """Known AMM program IDs."""

    # PumpSwap (uses Raydium AMM v4 underneath)
    PUMPSWAP = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

    # Raydium AMM v4
    RAYDIUM_AMM = "675kPX9MHTjS2zt1qrXjVnYYtYEyojNMjuSofEMQSdt"

    # Raydium Concentrated Liquidity Market Maker (CLMM)
    RAYDIUM_CLMM = "CAMMCzo5YL8w4VFF8EDCDqV1HqpW4GTonjfVNcNB5vp"

    # Orca Whirlpool
    ORCA_WHIRLPOOL = "whirLbMiicVdio4KfUbuVrCo6XcnWcj7v5KbQmxxF6J"

    # Meteora DLMM
    METEORA_DLMM = "Liq7fJg2yVHhbPPqqEDSVGMtPVaYYkSBPP8Y63QNhJS"

    # Solend (for future support)
    SOLEND = "So1endDq2YkqvdRWFLVm3BVqin6VPrxkkzpJ8UNqxWs"

    ALL = {PUMPSWAP, RAYDIUM_AMM, RAYDIUM_CLMM, ORCA_WHIRLPOOL, METEORA_DLMM, SOLEND}

    @classmethod
    def identify_program(cls, owner: str) -> Optional[str]:
        """Identify AMM program name from owner address."""
        program_map = {
            cls.PUMPSWAP: "pumpswap",
            cls.RAYDIUM_AMM: "raydium_amm",
            cls.RAYDIUM_CLMM: "raydium_clmm",
            cls.ORCA_WHIRLPOOL: "orca_whirlpool",
            cls.METEORA_DLMM: "meteora_dlmm",
            cls.SOLEND: "solend",
        }
        return program_map.get(owner)


class PoolDetector:
    """
    Detect AMM pool PDAs via program ownership from migration transactions.

    Algorithm:
    1. Extract accountKeys from migration TX
    2. Query getAccountInfo for each key
    3. Detect accounts owned by known AMM programs
    4. Return that account as the pool PDA
    """

    def __init__(self, rpc_url: str):
        """
        Args:
            rpc_url: RPC endpoint URL for account queries
        """
        self.rpc_url = rpc_url
        self.rpc_cache = {}  # Simple cache for account info

    async def detect_pool_from_tx(
        self,
        tx_data: Dict,
        token_mint: str
    ) -> Optional[str]:
        """
        Detect pool PDA from migration transaction via program ownership.

        Args:
            tx_data: Transaction data from getTransaction RPC call
            token_mint: Token mint address for context

        Returns:
            Pool account address (owned by AMM program) or None if not found
        """
        try:
            message = tx_data.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            if not account_keys:
                logger.warning(f"No account keys in transaction for {token_mint}")
                return None

            logger.info(f"[POOL_DETECT] Scanning {len(account_keys)} accounts for AMM ownership")

            # Scan each account for AMM program ownership
            for i, account_addr in enumerate(account_keys):
                try:
                    account_info = await self._get_account_info_cached(account_addr)

                    if not account_info or "owner" not in account_info:
                        continue

                    owner = account_info["owner"]

                    # Check if owner is a known AMM program
                    if owner in AMMPrograms.ALL:
                        program_name = AMMPrograms.identify_program(owner)
                        logger.info(
                            f"[POOL_DETECT] ✅ Found {program_name} pool at index {i}: {account_addr[:16]}..."
                        )
                        return account_addr

                except Exception as e:
                    logger.debug(f"[POOL_DETECT] Error checking account {i}: {e}")
                    continue

            logger.warning(f"[POOL_DETECT] No AMM-owned pool found in {len(account_keys)} accounts")
            return None

        except Exception as e:
            logger.error(f"[POOL_DETECT] Error detecting pool from TX: {e}")
            return None

    async def _get_account_info_cached(self, address: str) -> Optional[Dict]:
        """Fetch account info with caching."""
        if address in self.rpc_cache:
            return self.rpc_cache[address]

        try:
            import aiohttp

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "jsonParsed"}]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        if "result" in result and result["result"]:
                            account_info = result["result"]["value"]
                            self.rpc_cache[address] = account_info
                            return account_info
        except Exception as e:
            logger.debug(f"[POOL_DETECT] RPC error fetching {address[:16]}...: {e}")

        return None


class PoolParser:
    """Base class for AMM-specific pool parsers."""

    @staticmethod
    async def parse(
        pool_address: str,
        pool_data: bytes,
        token_mint: str,
        rpc_url: str
    ) -> Optional[PoolInfo]:
        """Parse pool account data and extract vault addresses."""
        raise NotImplementedError


class RaydiumAMMParser(PoolParser):
    """Parser for Raydium AMM v4 pools (also used by PumpSwap)."""

    # Raydium AMM pool account structure offsets
    # These are byte offsets in the account data
    OPEN_ORDERS_OFFSET = 200
    BASE_VAULT_OFFSET = 232
    QUOTE_VAULT_OFFSET = 264

    @staticmethod
    async def parse(
        pool_address: str,
        pool_data: bytes,
        token_mint: str,
        rpc_url: str
    ) -> Optional[PoolInfo]:
        """
        Parse Raydium AMM pool structure.

        Extracts vault addresses from fixed byte offsets in the pool state account.
        """
        try:
            if len(pool_data) < 296:
                logger.warning(f"Pool data too small for Raydium AMM: {len(pool_data)} bytes")
                return None

            # Extract vault addresses (32-byte Pubkey each)
            base_vault = _bytes_to_pubkey(pool_data[RaydiumAMMParser.BASE_VAULT_OFFSET:RaydiumAMMParser.BASE_VAULT_OFFSET + 32])
            quote_vault = _bytes_to_pubkey(pool_data[RaydiumAMMParser.QUOTE_VAULT_OFFSET:RaydiumAMMParser.QUOTE_VAULT_OFFSET + 32])

            if not base_vault or not quote_vault:
                logger.warning(f"Could not extract vault addresses from pool {pool_address}")
                return None

            logger.info(f"[RAYDIUM] Extracted vaults: base={base_vault[:16]}... quote={quote_vault[:16]}...")

            # Fetch vault data to determine token mints and decimals
            base_info = await _fetch_token_account_info(base_vault, rpc_url)
            quote_info = await _fetch_token_account_info(quote_vault, rpc_url)

            if not base_info or not quote_info:
                logger.warning(f"Could not fetch vault metadata for {pool_address}")
                return None

            return PoolInfo(
                mint=token_mint,
                pool_address=pool_address,
                base_account=base_vault,
                quote_account=quote_vault,
                base_token=base_info["mint"],
                quote_token=quote_info["mint"],
                base_decimals=base_info["decimals"],
                quote_decimals=quote_info["decimals"],
                pool_program="raydium_amm"
            )

        except Exception as e:
            logger.error(f"[RAYDIUM] Error parsing pool {pool_address}: {e}")
            return None


class OrcaWhirlpoolParser(PoolParser):
    """Parser for Orca Whirlpool pools."""

    # Orca Whirlpool pool account structure
    # Different layout than Raydium
    TOKEN_MINT_A_OFFSET = 104
    TOKEN_MINT_B_OFFSET = 136
    TOKEN_VAULT_A_OFFSET = 168
    TOKEN_VAULT_B_OFFSET = 200

    @staticmethod
    async def parse(
        pool_address: str,
        pool_data: bytes,
        token_mint: str,
        rpc_url: str
    ) -> Optional[PoolInfo]:
        """
        Parse Orca Whirlpool pool structure.

        Orca uses explicit mint and vault fields in the account structure.
        """
        try:
            if len(pool_data) < 232:
                logger.warning(f"Pool data too small for Orca Whirlpool: {len(pool_data)} bytes")
                return None

            # Extract token mints and vaults
            mint_a = _bytes_to_pubkey(pool_data[OrcaWhirlpoolParser.TOKEN_MINT_A_OFFSET:OrcaWhirlpoolParser.TOKEN_MINT_A_OFFSET + 32])
            mint_b = _bytes_to_pubkey(pool_data[OrcaWhirlpoolParser.TOKEN_MINT_B_OFFSET:OrcaWhirlpoolParser.TOKEN_MINT_B_OFFSET + 32])
            vault_a = _bytes_to_pubkey(pool_data[OrcaWhirlpoolParser.TOKEN_VAULT_A_OFFSET:OrcaWhirlpoolParser.TOKEN_VAULT_A_OFFSET + 32])
            vault_b = _bytes_to_pubkey(pool_data[OrcaWhirlpoolParser.TOKEN_VAULT_B_OFFSET:OrcaWhirlpoolParser.TOKEN_VAULT_B_OFFSET + 32])

            if not all([mint_a, mint_b, vault_a, vault_b]):
                logger.warning(f"Could not extract mint/vault from Orca pool {pool_address}")
                return None

            # Determine which is base and which is quote
            # If token_mint is mint_a, then base=mint_a, quote=mint_b
            base_token = mint_a if mint_a == token_mint else mint_b
            quote_token = mint_b if mint_a == token_mint else mint_a
            base_vault = vault_a if mint_a == token_mint else vault_b
            quote_vault = vault_b if mint_a == token_mint else vault_a

            # Fetch decimals
            base_info = await _fetch_token_account_info(base_vault, rpc_url)
            quote_info = await _fetch_token_account_info(quote_vault, rpc_url)

            if not base_info or not quote_info:
                logger.warning(f"Could not fetch vault metadata for Orca pool {pool_address}")
                return None

            return PoolInfo(
                mint=token_mint,
                pool_address=pool_address,
                base_account=base_vault,
                quote_account=quote_vault,
                base_token=base_token,
                quote_token=quote_token,
                base_decimals=base_info["decimals"],
                quote_decimals=quote_info["decimals"],
                pool_program="orca_whirlpool"
            )

        except Exception as e:
            logger.error(f"[ORCA] Error parsing pool {pool_address}: {e}")
            return None


class MeteoraParser(PoolParser):
    """Parser for Meteora DLMM pools."""

    # Meteora pool structure (similar to Raydium but different offsets)
    BIN_ARRAY_BITMAP_OFFSET = 8
    RESERVE_X_OFFSET = 168
    RESERVE_Y_OFFSET = 200

    @staticmethod
    async def parse(
        pool_address: str,
        pool_data: bytes,
        token_mint: str,
        rpc_url: str
    ) -> Optional[PoolInfo]:
        """Parse Meteora DLMM pool."""
        try:
            if len(pool_data) < 232:
                logger.warning(f"Pool data too small for Meteora: {len(pool_data)} bytes")
                return None

            # Meteora stores vault addresses
            reserve_x = _bytes_to_pubkey(pool_data[MeteoraParser.RESERVE_X_OFFSET:MeteoraParser.RESERVE_X_OFFSET + 32])
            reserve_y = _bytes_to_pubkey(pool_data[MeteoraParser.RESERVE_Y_OFFSET:MeteoraParser.RESERVE_Y_OFFSET + 32])

            if not reserve_x or not reserve_y:
                logger.warning(f"Could not extract reserves from Meteora pool {pool_address}")
                return None

            # Fetch vault metadata
            x_info = await _fetch_token_account_info(reserve_x, rpc_url)
            y_info = await _fetch_token_account_info(reserve_y, rpc_url)

            if not x_info or not y_info:
                logger.warning(f"Could not fetch vault metadata for Meteora pool {pool_address}")
                return None

            # Determine base/quote
            base_token = x_info["mint"] if x_info["mint"] == token_mint else y_info["mint"]
            quote_token = y_info["mint"] if x_info["mint"] == token_mint else x_info["mint"]
            base_vault = reserve_x if x_info["mint"] == token_mint else reserve_y
            quote_vault = reserve_y if x_info["mint"] == token_mint else reserve_x

            return PoolInfo(
                mint=token_mint,
                pool_address=pool_address,
                base_account=base_vault,
                quote_account=quote_vault,
                base_token=base_token,
                quote_token=quote_token,
                base_decimals=x_info["decimals"] if base_token == x_info["mint"] else y_info["decimals"],
                quote_decimals=y_info["decimals"] if base_token == x_info["mint"] else x_info["decimals"],
                pool_program="meteora_dlmm"
            )

        except Exception as e:
            logger.error(f"[METEORA] Error parsing pool {pool_address}: {e}")
            return None


class PoolParserDispatcher:
    """Route pool parsing to the appropriate parser based on owner program."""

    PARSERS = {
        AMMPrograms.PUMPSWAP: RaydiumAMMParser,
        AMMPrograms.RAYDIUM_AMM: RaydiumAMMParser,
        AMMPrograms.RAYDIUM_CLMM: RaydiumAMMParser,  # TODO: Use CLMM-specific parser
        AMMPrograms.ORCA_WHIRLPOOL: OrcaWhirlpoolParser,
        AMMPrograms.METEORA_DLMM: MeteoraParser,
    }

    @classmethod
    async def parse(
        cls,
        pool_address: str,
        owner_program: str,
        pool_data: bytes,
        token_mint: str,
        rpc_url: str
    ) -> Optional[PoolInfo]:
        """Dispatch to appropriate parser."""
        parser_class = cls.PARSERS.get(owner_program)

        if not parser_class:
            logger.warning(f"No parser for program {owner_program}")
            return None

        return await parser_class.parse(pool_address, pool_data, token_mint, rpc_url)


# Helper functions

def _bytes_to_pubkey(data: bytes) -> Optional[str]:
    """Convert 32-byte public key to base58 string."""
    if len(data) != 32:
        return None

    try:
        from solders.pubkey import Pubkey
        return str(Pubkey(data))
    except Exception:
        # Fallback: just encode as hex for debugging
        return None


async def _fetch_token_account_info(token_account: str, rpc_url: str) -> Optional[Dict]:
    """Fetch token account metadata (mint, decimals, balance)."""
    try:
        import aiohttp

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [token_account, {"encoding": "jsonParsed"}]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if "result" in result and result["result"]:
                        account = result["result"]["value"]
                        parsed = account.get("data", {}).get("parsed", {})
                        info = parsed.get("info", {})

                        return {
                            "mint": info.get("mint"),
                            "owner": info.get("owner"),
                            "decimals": None,  # Would need to fetch mint metadata
                            "balance": info.get("tokenAmount", {}).get("uiAmount", 0)
                        }
    except Exception as e:
        logger.debug(f"Error fetching token account {token_account[:16]}...: {e}")

    return None
