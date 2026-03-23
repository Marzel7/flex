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


import time

logger = logging.getLogger(__name__)


class TTLCache:
    """Simple TTL cache for account owners (optimization)."""

    def __init__(self, maxsize: int = 10000, ttl_seconds: int = 600):
        self.cache: Dict[str, Tuple[str, float]] = {}  # {pubkey: (owner, timestamp)}
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self.stats = {'hits': 0, 'misses': 0}

    def get(self, key: str) -> Optional[str]:
        """Get cached value if exists and not expired."""
        if key not in self.cache:
            self.stats['misses'] += 1
            return None

        owner, timestamp = self.cache[key]
        if time.time() - timestamp > self.ttl_seconds:
            del self.cache[key]
            self.stats['misses'] += 1
            return None

        self.stats['hits'] += 1
        return owner

    def set(self, key: str, value: str) -> None:
        """Set cache value (evict oldest if at capacity)."""
        if len(self.cache) >= self.maxsize:
            # Simple FIFO eviction
            oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k][1])
            del self.cache[oldest_key]

        self.cache[key] = (value, time.time())

    def stats_summary(self) -> str:
        """Return cache stats for logging."""
        total = self.stats['hits'] + self.stats['misses']
        hit_rate = (self.stats['hits'] / total * 100) if total > 0 else 0
        return f"hits={self.stats['hits']} misses={self.stats['misses']} hit_rate={hit_rate:.1f}% size={len(self.cache)}"


def _normalize_account_key(acc):
    """
    Normalize account key from various RPC provider formats (Phase 1).

    Handles:
    - Plain string: "Address123..."
    - Dict with pubkey: {"pubkey": "Address123...", ...}
    - Dict with address: {"address": "Address123...", ...}

    Returns:
        Normalized pubkey string or None
    """
    if isinstance(acc, str):
        return acc
    if isinstance(acc, dict):
        return acc.get("pubkey") or acc.get("address")
    return None


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

    # PumpFun V1 (uses Raydium AMM v4 layout)
    PUMPFUN_V1 = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

    # Raydium AMM v4
    RAYDIUM_AMM = "675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K"

    # Raydium Concentrated Liquidity Market Maker (CLMM)
    RAYDIUM_CLMM = "CAMMCzo5YL8w4VFF8EDCDqV1HqpW4GTonjfVNcNB5vp"

    # Orca Whirlpool
    ORCA_WHIRLPOOL = "whirLbMiicVdio4KfUqKKvsLrZtSqwNAUafgJMYco"

    # Meteora DLMM
    METEORA_DLMM = "Liq7fJg2yVHhbPPqqEDSVGMtPVaYYkSBPP8Y63QNhJS"

    # Solend (for future support)
    SOLEND = "So1endDq2YkqvdRWFLVm3BVqin6VPrxkkzpJ8UNqxWs"

    ALL = {PUMPSWAP, PUMPFUN_V1, RAYDIUM_AMM, RAYDIUM_CLMM, ORCA_WHIRLPOOL, METEORA_DLMM, SOLEND}

    @classmethod
    def identify_program(cls, owner: str) -> Optional[str]:
        """Identify AMM program name from owner address."""
        program_map = {
            cls.PUMPSWAP: "pumpswap",
            cls.PUMPFUN_V1: "pumpfun_v1",
            cls.RAYDIUM_AMM: "raydium_amm",
            cls.RAYDIUM_CLMM: "raydium_clmm",
            cls.ORCA_WHIRLPOOL: "orca_whirlpool",
            cls.METEORA_DLMM: "meteora_dlmm",
            cls.SOLEND: "solend",
        }
        return program_map.get(owner)


class AMMDataLengths:
    """Minimum data lengths for pool state accounts (Phase 4: Validation)."""
    RAYDIUM_AMM_MIN = 296  # Raydium AMM v4 pool state
    ORCA_WHIRLPOOL_MIN = 232  # Orca Whirlpool pool state
    METEORA_MIN = 232  # Meteora DLMM pool state
    PUMPSWAP_MIN = 296  # Uses Raydium layout

    EXPECTED = {
        AMMPrograms.RAYDIUM_AMM: RAYDIUM_AMM_MIN,
        AMMPrograms.PUMPSWAP: PUMPSWAP_MIN,
        AMMPrograms.ORCA_WHIRLPOOL: ORCA_WHIRLPOOL_MIN,
        AMMPrograms.METEORA_DLMM: METEORA_MIN,
    }


class PoolDetector:
    """
    Detect AMM pool PDAs via program ownership from migration transactions.

    Algorithm:
    1. Extract accountKeys from migration TX
    2. Query getAccountInfo for each key
    3. Detect accounts owned by known AMM programs
    4. Return that account as the pool PDA
    """

    def __init__(self, rpc_url: str, debug: bool = False):
        """
        Args:
            rpc_url: RPC endpoint URL for account queries
            debug: Enable verbose debug logging (Phase 6)
        """
        self.rpc_url = rpc_url
        self.rpc_cache = {}  # Simple cache for account info
        self.debug = debug
        self.owner_cache = TTLCache(maxsize=10000, ttl_seconds=600)  # Owner caching optimization  # Phase 6: Debug flag

    async def detect_pool_from_tx(
        self,
        tx_data: Dict,
        token_mint: str
    ) -> Optional[str]:
        """
        MINIMAL detector for debugging:
        - scan all tx accounts (+ loaded addresses + inner instruction accounts)
        - fetch owner
        - return first account owned by a known AMM program

        This intentionally skips:
        - size thresholds
        - parser validation
        - helper-PDA filtering

        Use this to prove whether valid pools are being seen at all.
        """
        try:
            message = tx_data.get("transaction", {}).get("message", {}) or {}
            meta = tx_data.get("meta", {}) or {}

            account_keys_raw = message.get("accountKeys", []) or []
            account_keys = [_normalize_account_key(a) for a in account_keys_raw]
            account_keys = [a for a in account_keys if a]

            loaded_addresses = meta.get("loadedAddresses", {}) or {}
            writable_accounts_raw = loaded_addresses.get("writable", []) or []
            writable_accounts = [_normalize_account_key(a) for a in writable_accounts_raw]
            writable_accounts = [a for a in writable_accounts if a]

            readonly_accounts_raw = loaded_addresses.get("readonly", []) or []
            readonly_accounts = [_normalize_account_key(a) for a in readonly_accounts_raw]
            readonly_accounts = [a for a in readonly_accounts if a]

            all_accounts = account_keys + writable_accounts + readonly_accounts

            # keep your existing helper if present
            try:
                inner_instruction_accounts = self._extract_inner_instruction_accounts(tx_data, all_accounts)
            except Exception:
                inner_instruction_accounts = []

            all_accounts_with_inner = all_accounts + inner_instruction_accounts

            logger.info(
                f"[POOL_DETECT_MINIMAL] token={token_mint[:16]}... "
                f"accounts={len(all_accounts_with_inner)}"
            )

            seen = set()
            for i, account_addr in enumerate(all_accounts_with_inner):
                if not account_addr or account_addr in seen:
                    continue
                seen.add(account_addr)

                try:
                    owner = await self._get_account_owner_cached(account_addr)
                except Exception as e:
                    logger.debug(
                        f"[POOL_DETECT_MINIMAL] idx={i} addr={account_addr[:16]}... owner_lookup_error={e}"
                    )
                    continue

                logger.info(
                    f"[POOL_DETECT_MINIMAL] idx={i} addr={account_addr[:16]}... "
                    f"owner={owner[:16] if owner else 'None'}..."
                )

                if owner in AMMPrograms.ALL:
                    program_name = AMMPrograms.identify_program(owner) or "unknown_amm"
                    logger.info(
                        f"[POOL_DETECT_MINIMAL] ✅ Returning first AMM-owned account: "
                        f"{account_addr[:16]}... program={program_name}"
                    )
                    return account_addr

            logger.warning(
                f"[POOL_DETECT_MINIMAL] No AMM-owned accounts found for {token_mint[:16]}..."
            )
            return None

        except Exception as e:
            logger.error(f"[POOL_DETECT_MINIMAL] Error detecting pool from TX: {e}")
            return None

    async def _discover_pool_via_vaults(self, token_mint: str) -> Optional[str]:
        """
        Improved fallback pool discovery via vault analysis.

        Flow:
        1. Get largest token accounts (candidate vaults)
        2. Validate they're not System Program owned (users)
        3. Parse as token accounts and extract authority
        4. Get authority owner and validate with parser
        5. Return only parser-validated pools

        Args:
            token_mint: Token mint to discover pool for

        Returns:
            Pool address or None if fallback discovery fails
        """
        try:
            logger.info(f"[POOL_DETECT_FALLBACK] Starting improved vault-based discovery")

            # Fetch largest token accounts
            import aiohttp
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenLargestAccounts",
                "params": [token_mint]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        logger.debug(f"[POOL_DETECT_FALLBACK] RPC call failed with status {resp.status}")
                        return None

                    result = await resp.json()
                    if "result" not in result or not result["result"]["value"]:
                        logger.debug(f"[POOL_DETECT_FALLBACK] No token accounts found for {token_mint}")
                        return None

                    accounts = result["result"]["value"]

            # Inspect each vault (top 5)
            for vault_account in accounts[:5]:
                vault_addr = vault_account["address"]

                try:
                    vault_info = await self._get_account_info_cached(vault_addr)
                    if not vault_info:
                        continue

                    vault_owner = vault_info.get("owner", "")

                    # FILTER: If vault owned by System Program, it's a user account, not a pool
                    if vault_owner == "11111111111111111111111111111111":
                        logger.debug(
                            f"[POOL_DETECT_FALLBACK] Vault {vault_addr[:16]}... "
                            f"owned by System Program (user account), skipping"
                        )
                        continue

                    # Try to parse vault as token account and extract authority
                    vault_data = vault_info.get("data", b"")
                    if len(vault_data) < 72:
                        logger.debug(
                            f"[POOL_DETECT_FALLBACK] Vault {vault_addr[:16]}... "
                            f"too small for token account ({len(vault_data)} bytes)"
                        )
                        continue

                    # Token account layout: bytes 32-64 = authority
                    try:
                        authority_bytes = vault_data[32:64]
                        authority = self._bytes_to_base58(authority_bytes)

                        if not authority:
                            continue

                        logger.debug(
                            f"[POOL_DETECT_FALLBACK] Vault {vault_addr[:16]}... "
                            f"authority={authority[:16]}..."
                        )

                        # Get authority account info
                        authority_info = await self._get_account_info_cached(authority)
                        if not authority_info:
                            continue

                        authority_owner = authority_info.get("owner", "")

                        # VALIDATE: Authority owner must be AMM program
                        if authority_owner not in AMMPrograms.ALL:
                            logger.debug(
                                f"[POOL_DETECT_FALLBACK] Authority {authority[:16]}... "
                                f"not owned by AMM program (owner={authority_owner[:16] if authority_owner else 'None'}...)"
                            )
                            continue

                        # VALIDATE: Parse authority data with appropriate parser
                        from src.core.pool_parser_dispatcher import PoolParserDispatcher

                        parser = PoolParserDispatcher.for_program(authority_owner)
                        if not parser:
                            logger.debug(
                                f"[POOL_DETECT_FALLBACK] No parser for authority owner {authority_owner[:16]}..."
                            )
                            continue

                        authority_data = authority_info.get("data", [])
                        pool_state = parser.try_parse(authority_data)

                        if pool_state:
                            logger.info(
                                f"[POOL_DETECT_FALLBACK] ✅ Pool found via vault authority: "
                                f"{authority[:16]}... (validated by parser)"
                            )
                            return authority

                        logger.debug(
                            f"[POOL_DETECT_FALLBACK] Parser rejected authority {authority[:16]}... "
                            f"(invalid structure)"
                        )

                    except Exception as e:
                        logger.debug(f"[POOL_DETECT_FALLBACK] Error parsing vault authority: {e}")
                        continue

                except Exception as e:
                    logger.debug(f"[POOL_DETECT_FALLBACK] Error processing vault {vault_addr[:16]}...: {e}")
                    continue

            logger.warning(f"[POOL_DETECT_FALLBACK] Failed to resolve pool via vaults")
            return None

        except Exception as e:
            logger.debug(f"[POOL_DETECT_FALLBACK] Error in vault discovery: {e}")
            return None

    async def _discover_pool_via_vaults_improved(self, token_mint: str) -> Optional[str]:
        """Alias for improved fallback discovery (used by updated detect_pool_from_tx)."""
        return await self._discover_pool_via_vaults(token_mint)

    def _extract_inner_instruction_accounts(self, tx_data: Dict, all_accounts: List[str]) -> List[str]:
        """
        Extract accounts referenced in inner instructions (optimization).

        Some pools are only referenced inside nested instructions:
        meta.innerInstructions[].instructions[].accounts

        These are indices into the full account list, so we need to resolve them.

        Args:
            tx_data: Transaction data from RPC
            all_accounts: Full list of all accounts (base + loaded addresses)

        Returns:
            List of additional account addresses from inner instructions
        """
        inner_instruction_accounts = set()

        try:
            meta = tx_data.get("meta", {})
            inner_instructions = meta.get("innerInstructions", []) or []

            for inner_group in inner_instructions:
                instructions = inner_group.get("instructions", []) or []

                for instruction in instructions:
                    accounts_indices = instruction.get("accounts", []) or []

                    # Convert indices to actual addresses
                    for idx in accounts_indices:
                        if isinstance(idx, int) and 0 <= idx < len(all_accounts):
                            inner_instruction_accounts.add(all_accounts[idx])

            if inner_instruction_accounts and self.debug:
                logger.debug(
                    f"[POOL_DETECT] Found {len(inner_instruction_accounts)} accounts "
                    f"in inner instructions"
                )

            return list(inner_instruction_accounts)

        except Exception as e:
            logger.debug(f"[POOL_DETECT] Error extracting inner instruction accounts: {e}")
            return []

    def _bytes_to_base58(self, data) -> Optional[str]:
        """
        Convert 32-byte pubkey to base58 string.

        Args:
            data: bytes or List[int] of length 32

        Returns:
            Base58-encoded address string or None if invalid
        """
        try:
            # Convert list to bytes if needed
            if isinstance(data, list):
                data = bytes(data)

            if not data or len(data) != 32:
                return None

            # Import base58 encoder
            try:
                import base58
            except ImportError:
                logger.debug("[POOL_DETECT] base58 module not available for authority parsing")
                return None

            # Encode to base58
            return base58.b58encode(data).decode('ascii')

        except Exception as e:
            logger.debug(f"[POOL_DETECT] Error converting bytes to base58: {e}")
            return None

    async def _get_account_owner_cached(self, address: str) -> Optional[str]:
        """
        Get account owner with TTL caching (optimization).
        
        This reduces RPC calls 80-90% by caching owners across detections.
        
        Args:
            address: Account pubkey
            
        Returns:
            Account owner pubkey or None if not found
        """
        # Check cache first
        cached_owner = self.owner_cache.get(address)
        if cached_owner is not None:
            return cached_owner

        try:
            # Fetch full account info
            account_info = await self._get_account_info_cached(address)
            if not account_info:
                return None

            owner = account_info.get("owner")
            if owner:
                # Cache the owner
                self.owner_cache.set(address, owner)

            return owner

        except Exception as e:
            logger.debug(f"[POOL_DETECT] Error getting owner for {address[:16]}...: {e}")
            return None

    async def _get_account_info_cached(self, address: str) -> Optional[Dict]:
        """Fetch account info with caching using raw account bytes."""
        if address in self.rpc_cache:
            return self.rpc_cache[address]

        try:
            import aiohttp
            import base64

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": "base64"}]
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(self.rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return None

                    result = await resp.json()
                    value = result.get("result", {}).get("value")
                    if not value:
                        return None

                    # Decode base64 data field
                    raw_data = b""
                    data_field = value.get("data")

                    if isinstance(data_field, list) and len(data_field) >= 1:
                        raw_data = base64.b64decode(data_field[0])

                    account_info = {
                        "owner": value.get("owner"),
                        "lamports": value.get("lamports"),
                        "executable": value.get("executable"),
                        "rentEpoch": value.get("rentEpoch"),
                        "data": raw_data,
                        "data_len": len(raw_data),
                    }

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
